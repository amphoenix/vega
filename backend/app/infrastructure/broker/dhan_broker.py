"""
Dhan Broker Adapter — real integration via dhanhq SDK.

Uses the official DhanHQ Python SDK (pip install dhanhq).
Docs: https://dhanhq.co/docs/v2/
SDK:  https://github.com/dhan-oss/dhan-py

Supports:
  - Order placement (MARKET, LIMIT, SL, SLM)
  - Order cancellation
  - Order status
  - Positions
  - Holdings
  - Available margin
  - LTP / Quote

Gracefully falls back to stub mode if dhanhq is not installed.
"""

from __future__ import annotations

import json
import queue as _queue
import threading
from typing import Any, Callable, Optional

from ...shared.logger import get_logger
from ...shared.time import datetime, timedelta, fmt_candle_date, now_ist, clock, sleep, monotonic
from .base import (
    BrokerAdapter, OrderResult, QuoteResult, PositionInfo,
    HoldingInfo, CandleData, InstrumentInfo,
)

logger = get_logger('dhan_broker')

# ── SDK import ────────────────────────────────────────────────────────────────

try:
    from dhanhq import DhanContext, dhanhq as DhanHQ
    _HAS_DHAN = True
except ImportError:
    DhanContext = None  # type: ignore
    DhanHQ = None  # type: ignore
    _HAS_DHAN = False
    logger.info('dhanhq not installed — DhanBroker runs in stub mode (pip install dhanhq)')


# ── Dhan SDK v2 constants ─────────────────────────────────────────────────────
# These MUST match the actual dhanhq package values. Verified against v2.2.0.

# Product types
INTRADAY = 'INTRADAY'
CNC = 'CNC'
MARGIN = 'MARGIN'

# Order types (SDK uses STOP_LOSS / STOP_LOSS_MARKET, not SL / SLM)
MARKET = 'MARKET'
LIMIT = 'LIMIT'
SL = 'STOP_LOSS'
SLM = 'STOP_LOSS_MARKET'

# Exchange segments (SDK class attrs: DhanHQ.NSE, DhanHQ.BSE, DhanHQ.FNO, etc.)
NSE = 'NSE_EQ'
BSE = 'BSE_EQ'
NFO = 'NSE_FNO'
BFO = 'BSE_FNO'
MCX = 'MCX_COMM'
CUR = 'NSE_CURRENCY'
IDX = 'IDX_I'           # Dhan index segment for NIFTY / SENSEX / VIX etc.

# Map our exchange strings to Dhan's segment constants
# Interval mapping (our format → Dhan format)
_INTERVAL_MAP: dict[str, str] = {
    '1m': '1', '5m': '5', '15m': '15', '25m': '25',
    '30m': '30', '1h': '60', '1d': 'D',
}

_EXCHANGE_MAP: dict[str, str] = {
    'NSE': NSE,
    'BSE': BSE,
    'NFO': NFO,
    'BFO': BFO,
    'MCX': MCX,
    'CUR': CUR,
    'IDX': IDX,
}

# Index ticker → (Dhan security_id, segment)
# Verified from official Dhan CSV: https://images.dhan.co/api-data/api-scrip-master.csv
# All indices live in the IDX_I segment (column SEM_SEGMENT='I').
_INDEX_SEC: dict[str, tuple[int, str]] = {
    '^NSEI':      (13, IDX),    # NIFTY 50
    'NIFTY':      (13, IDX),
    '^NSEBANK':   (25, IDX),    # BANK NIFTY
    'BANKNIFTY':  (25, IDX),
    '^CNXFIN':    (27, IDX),    # FIN NIFTY
    'FINNIFTY':   (27, IDX),
    '^NSEMDCP50': (442, IDX),   # MIDCAP NIFTY
    'MIDCPNIFTY': (442, IDX),
    '^BSESN':     (51, IDX),    # SENSEX
    'SENSEX':     (51, IDX),
    '^INDIAVIX':  (21, IDX),    # INDIA VIX      (NSE,I,21)
    'INDIAVIX':   (21, IDX),
}


class DhanBroker(BrokerAdapter):
    """Dhan broker integration via official dhanhq SDK.

    Usage:
        broker = DhanBroker(
            client_id='1000000001',
            access_token='your-jwt-token',
        )
        result = broker.place_order('NIFTY 18JUN 24000 CE', 'BUY', 50, exchange='NFO')
    """

    def __init__(
        self,
        client_id: str = '',
        access_token: str = '',
        product_type: str = INTRADAY,
        paper_mode: bool = False,
        paper_capital: float = 100_000.0,
        **_kwargs: Any,
    ) -> None:
        self._client_id = client_id
        self._access_token = access_token
        self._default_product = product_type
        self._paper_mode = paper_mode
        self._paper_capital = paper_capital
        self._client: Any = None
        self._stub_mode = not _HAS_DHAN or not client_id or not access_token

        # WebSocket tick infrastructure
        self._ws_feed: Any = None
        self._ws_thread: Optional[threading.Thread] = None
        self._ws_lock = threading.Lock()
        self._sub_lock = threading.Lock()
        self._subscribers: dict[int, set[_queue.Queue]] = {}  # sec_id → {queues}
        self._sec_exchange: dict[int, int] = {}  # sec_id → exchange_int for WS
        self._sec_symbol: dict[int, str] = {}  # sec_id → original symbol for SSE payload
        self._tick_callbacks: dict[int, set] = {}  # sec_id → {callback functions}
        self._callback_lock = threading.Lock()
        self._symbol_sec_cache: dict[str, tuple[int, str]] = {}  # trading_symbol → (sec_id, segment)
        # Deferred WS start — batch instruments from multiple callers on boot
        self._ws_pending: set[tuple[int, str]] = set()  # (ws_exchange, sec_id_str)
        self._ws_start_timer: Optional[threading.Timer] = None
        self._ws_retry_count: int = 0
        self._WS_BATCH_DELAY: float = 5.0   # seconds to wait for more subscriptions on boot
        self._WS_MAX_RETRIES: int = 5

        if self._stub_mode:
            if _HAS_DHAN and (not client_id or not access_token):
                logger.warning('Dhan credentials missing — stub mode')
            return

        # Initialize real SDK client (v2: DhanContext → DhanHQ)
        ctx = DhanContext(client_id, access_token)
        self._client = DhanHQ(ctx)
        self._dhan_ctx = ctx  # keep for MarketFeed init
        mode = 'paper' if paper_mode else 'live'
        logger.info('DhanBroker connected (client_id=%s, mode=%s)', client_id[:6] + '***', mode)

    @property
    def broker_name(self) -> str:
        return 'dhan'

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str = 'MARKET',
        price: float = 0.0,
        exchange: str = 'NFO',
        security_id: str = '',
        trigger_price: float = 0.0,
        product_type: str = '',
        tag: str = '',
    ) -> OrderResult:
        if self._paper_mode:
            import uuid
            fill = self.get_ltp(symbol, exchange, security_id) or price or 0.0
            cost = fill * qty
            self._paper_capital -= cost
            logger.info('Dhan PAPER fill: %s %s x%d @ %.2f (capital=%.2f)',
                        side, symbol, qty, fill, self._paper_capital)
            return OrderResult(
                success=True,
                order_id=f'PAPER-{uuid.uuid4().hex[:8].upper()}',
                fill_price=fill,
                status='FILLED',
                message=f'Paper fill @ {fill}',
            )

        if self._stub_mode:
            return OrderResult(success=False, status='REJECTED', message='Dhan stub mode')

        try:
            segment = _EXCHANGE_MAP.get(exchange.upper(), NFO)
            product = product_type or self._default_product
            txn_type = 'BUY' if side.upper() == 'BUY' else 'SELL'

            # Map order types
            dhan_order_type = {
                'MARKET': MARKET,
                'LIMIT': LIMIT,
                'SL': SL,
                'SLM': SLM,
                'SL-M': SLM,
            }.get(order_type.upper(), MARKET)

            resp = self._client.place_order(
                security_id=security_id or symbol,
                exchange_segment=segment,
                transaction_type=txn_type,
                quantity=qty,
                order_type=dhan_order_type,
                product_type=product,
                price=price if dhan_order_type in (LIMIT, SL) else 0,
                trigger_price=trigger_price if dhan_order_type in (SL, SLM) else 0,
                tag=tag,
            )

            # Dhan SDK sometimes returns a string instead of dict
            if isinstance(resp, str):
                import json as _json
                try:
                    resp = _json.loads(resp)
                except (ValueError, TypeError):
                    logger.error('Dhan place_order returned non-dict: %s', resp[:200])
                    return OrderResult(success=False, status='ERROR',
                                       message=f'Unexpected response: {resp[:200]}')

            order_id = (resp.get('data') or {}).get('orderId', '')
            status = resp.get('status', '')
            remarks = resp.get('remarks', resp.get('data', ''))
            if status != 'success':
                logger.error('Dhan order REJECTED: %s %s x%d → %s | remarks=%s | raw=%s',
                             side, symbol, qty, status, remarks, resp)
            else:
                logger.info('Dhan order placed: %s %s x%d → %s (id=%s)',
                            side, symbol, qty, status, order_id)

            return OrderResult(
                success=status == 'success',
                order_id=str(order_id),
                status=status.upper(),
                raw=resp,
            )
        except Exception as e:
            logger.error('Dhan place_order failed: %s', e)
            return OrderResult(success=False, status='ERROR', message=str(e))

    def modify_order(
        self, order_id: str, qty: int = 0, price: float = 0.0,
        order_type: str = '', trigger_price: float = 0.0,
    ) -> OrderResult:
        if self._stub_mode:
            return OrderResult(success=False, message='Dhan stub mode')
        try:
            # SDK v2: modify_order(order_id, order_type, leg_name, quantity,
            #                      price, trigger_price, disclosed_quantity, validity)
            resp = self._client.modify_order(
                order_id=order_id,
                order_type=order_type or MARKET,
                leg_name='ENTRY_LEG',
                quantity=qty,
                price=price,
                trigger_price=trigger_price,
                disclosed_quantity=0,
                validity='DAY',
            )
            status = resp.get('status', '')
            return OrderResult(
                success=status == 'success',
                order_id=order_id,
                status=status.upper(),
                raw=resp,
            )
        except Exception as e:
            logger.error('Dhan modify_order failed: %s', e)
            return OrderResult(success=False, status='ERROR', message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        if self._stub_mode:
            return False
        try:
            resp = self._client.cancel_order(order_id=order_id)
            return resp.get('status') == 'success'
        except Exception as e:
            logger.error('Dhan cancel_order failed: %s', e)
            return False

    @staticmethod
    def _normalise_order(o: dict) -> dict:
        """Add snake_case aliases so the frontend can read Dhan orders."""
        o['order_id'] = o.get('orderId', o.get('order_id', ''))
        o['trading_symbol'] = o.get('tradingSymbol', o.get('trading_symbol', ''))
        o['transaction_type'] = o.get('transactionType', o.get('transaction_type', ''))
        o['order_status'] = o.get('orderStatus', o.get('order_status', ''))
        o['order_type'] = o.get('orderType', o.get('order_type', ''))
        o['qty'] = o.get('quantity', o.get('qty', 0))
        o['avg_price'] = o.get('averageTradedPrice', o.get('avg_price', o.get('price', 0)))
        o['order_timestamp'] = o.get('createTime', o.get('order_timestamp', ''))
        o['exchange_timestamp'] = o.get('exchangeTime', o.get('exchange_timestamp', ''))
        o['status'] = o['order_status']
        return o

    def get_order_list(self) -> list[dict[str, Any]]:
        if self._stub_mode or self._paper_mode:
            return []
        try:
            resp = self._client.get_order_list()
            orders = resp.get('data', [])
            if isinstance(orders, list):
                return [self._normalise_order(o) for o in orders]
            return []
        except Exception as e:
            logger.error('Dhan get_order_list failed: %s', e)
            return []

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        if self._stub_mode:
            return {}
        try:
            resp = self._client.get_order_by_id(order_id=order_id)
            data = resp.get('data', {})
            if data:
                return self._normalise_order(data)
            return {}
        except Exception as e:
            logger.error('Dhan get_order_status failed: %s', e)
            return {}

    # ── Market Data ───────────────────────────────────────────────────────────

    def _resolve_sec(self, symbol: str, exchange: str, security_id: str) -> tuple:
        """Resolve symbol → (security_id_int, segment_str)."""
        idx_info = _INDEX_SEC.get(symbol.upper())
        if idx_info and not security_id:
            return idx_info  # (sec_id, segment)
        # Fast path: check in-memory cache from prior subscribe_ticks
        cached = self._symbol_sec_cache.get(symbol.upper())
        if cached and not security_id:
            return cached
        segment = _EXCHANGE_MAP.get(exchange.upper(), NFO)
        sec = security_id or symbol
        try:
            sec_int = int(sec)
        except (ValueError, TypeError):
            # symbol is a trading symbol string — look up security ID from master
            sec_int = self._lookup_security_id(symbol, exchange)
            if sec_int is None:
                sec_int = sec  # last resort — SDK will likely reject this
        return (sec_int, segment)

    def _lookup_security_id(self, symbol: str, exchange: str = '') -> int | None:
        """Resolve a trading symbol to its numeric Dhan security ID via instrument master."""
        sym_upper = symbol.upper().strip()
        for inst in self._load_instruments():
            if str(inst.get('SEM_TRADING_SYMBOL', '')).strip().upper() == sym_upper:
                exch = str(inst.get('SEM_EXM_EXCH_ID', '')).strip().upper()
                if exchange and exchange.upper() not in exch:
                    continue
                try:
                    return int(inst['SEM_SMST_SECURITY_ID'])
                except (ValueError, TypeError, KeyError):
                    continue
        return None

    def _quote_raw(self, symbol: str, exchange: str, security_id: str) -> dict:
        """Internal: call SDK v2 quote_data and extract the single-security dict."""
        sec_int, segment = self._resolve_sec(symbol, exchange, security_id)
        resp = self._client.quote_data({segment: [sec_int]})
        return self._extract_market_data(resp)

    @staticmethod
    def _extract_market_data(resp: dict) -> dict:
        """Extract first security data from v2 response.

        v2 nests: {"data": {"NSE_EQ": {"11536": {"last_price": 4520}}}}
        """
        data = resp.get('data', {})
        if not isinstance(data, dict):
            return {}
        for seg_val in data.values():
            if isinstance(seg_val, dict):
                # Could be {"11536": {"last_price": ...}} or directly {"last_price": ...}
                for inner in seg_val.values():
                    if isinstance(inner, dict) and 'last_price' in inner:
                        return inner
                # Fallback: seg_val itself has last_price
                if 'last_price' in seg_val:
                    return seg_val
        return {}

    def _index_ltp(self, symbol: str) -> Optional[float]:
        """Get index LTP from last intraday candle (marketfeed doesn't support IDX_I)."""
        try:
            candles = self.get_candles(symbol, interval='1m', days=1)
            if candles:
                return float(candles[-1].close)
        except Exception as e:
            logger.debug('_index_ltp candle fallback failed for %s: %s', symbol, e)
        return None

    def get_ltp(
        self, symbol: str, exchange: str = 'NFO', security_id: str = '',
    ) -> Optional[float]:
        if self._stub_mode:
            return None
        try:
            sec_int, segment = self._resolve_sec(symbol, exchange, security_id)
            # Dhan marketfeed endpoints don't support IDX_I — use last candle
            if segment == IDX:
                return self._index_ltp(symbol)
            resp = self._client.ticker_data({segment: [sec_int]})
            data = self._extract_market_data(resp)
            ltp = float(data.get('last_price', data.get('LTP', 0)))
            if ltp > 0:
                return ltp
            # Fall back to quote_data
            data = self._quote_raw(symbol, exchange, security_id)
            return float(data.get('last_price', data.get('LTP', 0))) or None
        except Exception as e:
            logger.error('Dhan get_ltp failed: %s', e)
            return None

    def get_quote(
        self, symbol: str, exchange: str = 'NFO', security_id: str = '',
    ) -> Optional[QuoteResult]:
        if self._stub_mode:
            return None
        try:
            data = self._quote_raw(symbol, exchange, security_id)
            return QuoteResult(
                symbol=symbol,
                ltp=float(data.get('last_price', data.get('LTP', 0))),
                bid=float(data.get('depth', {}).get('buy', [{}])[0].get('price', data.get('bestBidPrice', 0))),
                ask=float(data.get('depth', {}).get('sell', [{}])[0].get('price', data.get('bestAskPrice', 0))),
                volume=int(data.get('volume', 0)),
                open=float(data.get('ohlc', {}).get('open', data.get('open', 0))),
                high=float(data.get('ohlc', {}).get('high', data.get('high', 0))),
                low=float(data.get('ohlc', {}).get('low', data.get('low', 0))),
                close=float(data.get('ohlc', {}).get('close', data.get('close', 0))),
            )
        except Exception as e:
            logger.error('Dhan get_quote failed: %s', e)
            return None

    # ── Account ───────────────────────────────────────────────────────────────

    def get_positions(self) -> list[PositionInfo]:
        if self._stub_mode:
            return []
        try:
            resp = self._client.get_positions()
            positions = []
            for p in resp.get('data', []):
                _realized = float(p.get('realizedProfit', 0))
                _unrealized = float(p.get('unrealizedProfit', 0))
                positions.append(PositionInfo(
                    symbol=p.get('tradingSymbol', ''),
                    qty=int(p.get('netQty', 0)),
                    avg_price=float(p.get('averagePrice', 0)),
                    ltp=float(p.get('ltp', 0)),
                    pnl=_realized + _unrealized,
                    realized_pnl=_realized,
                    unrealized_pnl=_unrealized,
                    security_id=str(p.get('securityId', '')),
                    exchange=p.get('exchangeSegment', ''),
                    product_type=p.get('productType', ''),
                ))
            return positions
        except Exception as e:
            logger.error('Dhan get_positions failed: %s', e)
            return []

    def get_available_cash(self) -> float:
        if self._paper_mode:
            return self._paper_capital
        if self._stub_mode:
            return 0.0
        try:
            resp = self._client.get_fund_limits()
            data = resp.get('data', {})
            return float(data.get('availabelBalance', data.get('availableBalance', 0)))
        except Exception as e:
            logger.error('Dhan get_available_cash failed: %s', e)
            return 0.0

    def get_holdings(self) -> list[HoldingInfo]:
        if self._stub_mode:
            return []
        try:
            resp = self._client.get_holdings()
            holdings = []
            for h in resp.get('data', []):
                holdings.append(HoldingInfo(
                    symbol=h.get('tradingSymbol', ''),
                    qty=int(h.get('totalQty', h.get('quantity', 0))),
                    avg_price=float(h.get('avgCostPrice', 0)),
                    ltp=float(h.get('ltp', 0)),
                    pnl=float(h.get('unrealizedProfit', 0)),
                    security_id=str(h.get('securityId', '')),
                ))
            return holdings
        except Exception as e:
            logger.error('Dhan get_holdings failed: %s', e)
            return []

    # ── Instrument Master ─────────────────────────────────────────────────

    _instruments_cache: list[dict] | None = None
    _instruments_lock = __import__('threading').Lock()

    def _load_instruments(self) -> list[dict]:
        """Download Dhan compact security list once, cache in memory."""
        if DhanBroker._instruments_cache is not None:
            return DhanBroker._instruments_cache
        with DhanBroker._instruments_lock:
            if DhanBroker._instruments_cache is not None:
                return DhanBroker._instruments_cache
            try:
                import tempfile, os
                # fetch_security_list saves a CSV file — use tempdir
                old_cwd = os.getcwd()
                tmpdir = tempfile.mkdtemp()
                os.chdir(tmpdir)
                df = self._client.fetch_security_list('compact')
                os.chdir(old_cwd)
                rows = df.to_dict('records') if hasattr(df, 'to_dict') else []
                DhanBroker._instruments_cache = rows
                logger.info('Dhan instrument master loaded: %d instruments', len(rows))
                return rows
            except Exception as e:
                logger.error('Dhan _load_instruments failed: %s', e)
                return []

    def search_instruments(
        self, query: str, exchange: str = '', instrument_type: str = '',
    ) -> list[InstrumentInfo]:
        if self._stub_mode:
            return []
        try:
            q = query.upper().strip()
            instruments = self._load_instruments()
            # Buckets: 0=exact sym, 1=prefix sym, 2=contains sym/custom
            buckets: list[list[InstrumentInfo]] = [[], [], []]
            for inst in instruments:
                sym = str(inst.get('SEM_TRADING_SYMBOL', '')).strip().upper()
                custom = str(inst.get('SEM_CUSTOM_SYMBOL', '')).strip().upper()
                # Match priority
                if sym == q:
                    bucket = 0
                elif sym.startswith(q):
                    bucket = 1
                elif q in sym or q in custom:
                    bucket = 2
                else:
                    continue
                seg = str(inst.get('SEM_SEGMENT', '')).strip()
                exch = str(inst.get('SEM_EXM_EXCH_ID', '')).strip()
                if exchange and exchange.upper() not in exch.upper() and exchange.upper() not in seg.upper():
                    continue
                opt_type = str(inst.get('SEM_OPTION_TYPE', '')).strip()
                inst_name = str(inst.get('SEM_INSTRUMENT_NAME', '')).strip()
                if instrument_type:
                    it = instrument_type.upper()
                    if it not in opt_type.upper() and it not in inst_name.upper():
                        continue
                info = InstrumentInfo(
                    symbol=sym,
                    security_id=str(inst.get('SEM_SMST_SECURITY_ID', '')),
                    exchange=exch,
                    instrument_type=inst_name or opt_type,
                    lot_size=int(float(inst.get('SEM_LOT_UNITS', 1) or 1)),
                    tick_size=float(inst.get('SEM_TICK_SIZE', 0.05) or 0.05),
                    expiry=str(inst.get('SEM_EXPIRY_DATE', '')).strip(),
                    strike=float(inst.get('SEM_STRIKE_PRICE', 0) or 0),
                    underlying=str(inst.get('SM_SYMBOL_NAME', '')).strip(),
                    name=str(inst.get('SEM_CUSTOM_SYMBOL', '') or sym).strip(),
                )
                buckets[bucket].append(info)
            # Within each bucket, sort: equity/index first, then F&O
            _EQ_TYPES = {'EQUITY', 'INDEX', 'ETF', ''}
            def _sort_key(r: InstrumentInfo) -> tuple:
                is_eq = 0 if r.instrument_type.upper() in _EQ_TYPES else 1
                is_nse = 0 if r.exchange.upper() in ('NSE', 'NSE_EQ') else 1
                return (is_eq, is_nse)
            results: list[InstrumentInfo] = []
            for b in buckets:
                b.sort(key=_sort_key)
                results.extend(b)
                if len(results) >= 50:
                    break
            return results[:50]
        except Exception as e:
            logger.error('Dhan search_instruments failed: %s', e)
            return []

    # ── Instrument Master (public adapter) ──────────────────────────────────

    # Map Dhan CSV column names → standard names used by option_planner / fo_scanner
    _SEM_FIELD_MAP: dict[str, str] = {
        'SEM_TRADING_SYMBOL':    'TRADING_SYMBOL',
        'SEM_CUSTOM_SYMBOL':     'CUSTOM_SYMBOL',
        'SEM_OPTION_TYPE':       'OPTION_TYPE',
        'SEM_STRIKE_PRICE':      'STRIKE_PRICE',
        'SEM_EXPIRY_DATE':       'EXPIRY_DATE',
        'SEM_LOT_UNITS':         'LOT_SIZE',
        'SEM_SMST_SECURITY_ID':  'SECURITY_ID',
        'SEM_INSTRUMENT_NAME':   'INSTRUMENT_NAME',
        'SEM_EXM_EXCH_ID':      'EXCH',
        'SEM_SEGMENT':           'SEGMENT',
        'SEM_TICK_SIZE':         'TICK_SIZE',
        'SM_SYMBOL_NAME':        'SYMBOL_NAME',
    }

    @classmethod
    def _normalise_inst_row(cls, raw: dict) -> dict:
        """Copy a Dhan CSV row adding standard field names alongside SEM_ originals."""
        out = dict(raw)
        for sem_key, std_key in cls._SEM_FIELD_MAP.items():
            if sem_key in out and std_key not in out:
                out[std_key] = out[sem_key]
        # Add convenience aliases expected by option_planner._field()
        if 'DISPLAY_SYMBOL' not in out:
            out['DISPLAY_SYMBOL'] = out.get('CUSTOM_SYMBOL', '')
        # Map Dhan exchange IDs to NFO/BFO that option_planner expects
        exch = str(out.get('EXCH', '')).strip().upper()
        if exch == 'NSE' and str(out.get('INSTRUMENT_NAME', '')).startswith(('OPT', 'FUT')):
            out['EXCH'] = 'NFO'
        elif exch == 'BSE' and str(out.get('INSTRUMENT_NAME', '')).startswith(('OPT', 'FUT')):
            out['EXCH'] = 'BFO'
        return out

    def load_instruments(self, source: str = 'fno') -> list:
        """Load F&O instrument master rows from cached Dhan CSV, normalised to standard field names."""
        all_rows = self._load_instruments()
        if source.lower() in ('fno', 'fo'):
            rows = [r for r in all_rows
                    if str(r.get('SEM_INSTRUMENT_NAME', '')).strip().upper()
                    in ('OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK', 'OPTCUR',
                        'FUTCUR', 'OPTCOM', 'FUTCOM')]
            return [self._normalise_inst_row(r) for r in rows]
        if source.lower() in ('equity', 'eq', 'cash'):
            rows = [r for r in all_rows
                    if str(r.get('SEM_INSTRUMENT_NAME', '')).strip().upper()
                    in ('EQUITY', 'INDEX', '')]
            return [self._normalise_inst_row(r) for r in rows]
        return [self._normalise_inst_row(r) for r in all_rows]

    # ── Resolve Option Contract ──────────────────────────────────────────

    def resolve_option_contract(
        self, underlying: str, option_type: str, strike: float,
    ) -> Optional[dict]:
        """Find nearest matching option contract for an underlying on Dhan."""
        from ...shared.time import today_ist
        base = underlying.upper().replace('.NS', '').replace('.BO', '').lstrip('^')
        base = {'NSEI': 'NIFTY', 'NSEBANK': 'BANKNIFTY',
                'CNXFIN': 'FINNIFTY', 'BSESN': 'SENSEX'}.get(base, base)
        today = today_ist()
        rows = self.load_instruments('fno')
        candidates = []
        for inst in rows:
            sym = str(inst.get('TRADING_SYMBOL', '')).strip().upper()
            if not sym.startswith(base):
                continue
            opt = str(inst.get('OPTION_TYPE', '')).strip().upper()
            if opt != option_type.upper():
                continue
            exch = str(inst.get('EXCH', '')).strip().upper()
            if exch not in ('NFO', 'BFO', 'NSE', 'BSE'):
                continue
            exp_str = str(inst.get('EXPIRY_DATE', '')).strip()
            exp = self._parse_expiry_date(exp_str)
            if exp is None or exp < today:
                continue
            try:
                inst_strike = float(inst.get('STRIKE_PRICE') or 0)
            except (ValueError, TypeError):
                continue
            candidates.append({
                'symbol': sym,
                'sec_id': str(inst.get('SECURITY_ID', '')).strip(),
                'expiry': exp,
                'expiry_s': exp_str,
                'strike': inst_strike,
                'display': str(inst.get('DISPLAY_SYMBOL') or inst.get('CUSTOM_SYMBOL') or '').strip(),
                'lot_size': int(float(inst.get('LOT_SIZE') or inst.get('LOT_UNITS') or 0)),
            })
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x['expiry'], abs(x['strike'] - strike)))
        best = candidates[0]
        ltp = self.get_ltp(best['symbol'], exchange='NFO',
                           security_id=best['sec_id'])
        return {
            'trading_symbol': best['symbol'],
            'display_symbol': best.get('display') or best['symbol'],
            'security_id': best['sec_id'],
            'expiry_date': best['expiry_s'],
            'strike': best['strike'],
            'ltp': ltp,
            'lot_size': best.get('lot_size', 0),
        }

    @staticmethod
    def _parse_expiry_date(s: str):
        """Parse expiry date string to date object. Returns None on failure."""
        if not s:
            return None
        from datetime import date as _date
        s = s.strip()
        if 'T' in s:
            s = s.split('T', 1)[0]
        if ' ' in s and ':' in s:
            s = s.split(' ', 1)[0]
        for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y',
                    '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d'):
            try:
                return datetime.strptime(s.upper(), fmt).date()
            except Exception:
                continue
        return None

    # ── Batch Quotes ─────────────────────────────────────────────────────

    # Segment mapping for Dhan quote API: our code → Dhan SDK segment string
    _QUOTE_SEG_MAP: dict[str, str] = {
        'NFO': 'NSE_FNO', 'NSE': 'NSE_EQ', 'BSE': 'BSE_EQ',
        'BFO': 'BSE_FNO', 'MCX': 'MCX_COMM', 'CUR': 'NSE_CURRENCY',
    }

    def get_quotes_batch(self, scrip_codes: list[str]) -> dict[str, dict]:
        """Fetch OI/volume/bid-ask for a list of 'EXCHANGE_SECURITYID' codes."""
        if self._stub_mode or not scrip_codes:
            return {}
        # Group by Dhan segment
        seg_groups: dict[str, list[int]] = {}
        code_map: dict[str, str] = {}  # dhan_seg → original codes mapping
        for code in scrip_codes:
            parts = code.split('_', 1)
            if len(parts) != 2:
                continue
            exch, sid = parts[0].strip().upper(), parts[1].strip()
            dhan_seg = self._QUOTE_SEG_MAP.get(exch, f'{exch}_EQ')
            try:
                sid_int = int(sid)
            except ValueError:
                continue
            seg_groups.setdefault(dhan_seg, []).append(sid_int)
            code_map[f'{dhan_seg}_{sid_int}'] = code

        out: dict[str, dict] = {}
        try:
            # Dhan SDK accepts: {"NSE_FNO": [id1, id2], "NSE_EQ": [id3]}
            resp = self._client.quote_data(seg_groups)
            data = resp.get('data', {})
            if not isinstance(data, dict):
                return out
            for dhan_seg, entries in data.items():
                if not isinstance(entries, dict):
                    continue
                for sid_str, d in entries.items():
                    if not isinstance(d, dict):
                        continue
                    orig_code = code_map.get(f'{dhan_seg}_{sid_str}', '')
                    if not orig_code:
                        # Try with matching exchange
                        for c in scrip_codes:
                            if c.endswith(f'_{sid_str}'):
                                orig_code = c
                                break
                    if not orig_code:
                        continue
                    depth = d.get('depth', {})
                    bid = ask = 0.0
                    if isinstance(depth, dict):
                        buy_list = depth.get('buy', [])
                        sell_list = depth.get('sell', [])
                        if buy_list and isinstance(buy_list, list):
                            bid = float(buy_list[0].get('price', 0) or 0)
                        if sell_list and isinstance(sell_list, list):
                            ask = float(sell_list[0].get('price', 0) or 0)
                    out[orig_code] = {
                        'bid':    bid,
                        'ask':    ask,
                        'ltp':    float(d.get('LTP', d.get('last_price', 0)) or 0),
                        'oi':     int(float(d.get('OI', d.get('oi', 0)) or 0)),
                        'volume': int(float(d.get('volume', d.get('day_volume', 0)) or 0)),
                    }
        except Exception as e:
            logger.warning('Dhan get_quotes_batch failed: %s', e)
        return out

    # ── OHLCV ────────────────────────────────────────────────────────────────

    # Candle cache to avoid Dhan rate limits (DH-904)
    _candle_cache: dict = {}
    _candle_cache_lock = threading.Lock()
    _api_throttle_lock = threading.Lock()
    _last_api_call: float = 0.0
    _API_MIN_INTERVAL: float = 0.1  # min seconds between Dhan API calls

    def get_candles(
        self, symbol: str, interval: str = '5m', days: int = 5,
        exchange: str = 'NFO', security_id: str = '',
    ) -> list[CandleData]:
        if self._stub_mode:
            return []

        # Rate-limit guard: return cached result if fresh enough
        _cache_key = f'{symbol}|{interval}|{days}|{exchange}|{security_id}'
        _ttl = 60.0 if interval in ('1m', '5m') else 120.0
        with DhanBroker._candle_cache_lock:
            entry = DhanBroker._candle_cache.get(_cache_key)
            if entry:
                age = clock() - entry[0]
                if age < _ttl:
                    return entry[1]

        # Throttle: ensure min interval between Dhan API calls
        with DhanBroker._api_throttle_lock:
            elapsed = clock() - DhanBroker._last_api_call
            if elapsed < DhanBroker._API_MIN_INTERVAL:
                sleep(DhanBroker._API_MIN_INTERVAL - elapsed)
            DhanBroker._last_api_call = clock()

        # Retry with backoff on DH-904 rate limit
        for _attempt in range(3):
            try:
                resp = self._fetch_candle_data(symbol, interval, days, exchange, security_id)
                remarks = resp.get('remarks') or {}
                if isinstance(remarks, dict) and remarks.get('error_code') == 'DH-904':
                    if _attempt < 2:
                        backoff = 1.0 * (2 ** _attempt)
                        logger.debug('Dhan DH-904 for %s — retry %d in %.1fs', symbol, _attempt + 1, backoff)
                        sleep(backoff)
                        continue
                break
            except Exception as e:
                logger.error('Dhan get_candles failed: %s', e)
                return []

        candles = self._parse_candle_response(resp, symbol, interval)
        with DhanBroker._candle_cache_lock:
            DhanBroker._candle_cache[_cache_key] = (clock(), candles)
        return candles

    def _fetch_candle_data(
        self, symbol: str, interval: str, days: int,
        exchange: str, security_id: str,
    ) -> dict:
        to_date = now_ist()
        from_date = to_date - timedelta(days=days)
        dhan_interval = _INTERVAL_MAP.get(interval, '5')

        idx_info = _INDEX_SEC.get(symbol.upper())
        if idx_info and not security_id:
            sec_id, segment = idx_info
            inst_type = 'INDEX'
        else:
            segment = _EXCHANGE_MAP.get(exchange.upper(), NFO)
            sec_id = security_id or symbol
            if exchange.upper() == 'CUR':
                inst_type = 'FUTCUR'
            elif exchange.upper() in ('NFO', 'BFO'):
                inst_type = 'OPTIDX'
            else:
                inst_type = 'EQUITY'

        try:
            sec_id = int(sec_id)
        except (ValueError, TypeError):
            pass

        if dhan_interval == 'D':
            return self._client.historical_daily_data(
                security_id=sec_id,
                exchange_segment=segment,
                instrument_type=inst_type,
                from_date=from_date.strftime('%Y-%m-%d'),
                to_date=to_date.strftime('%Y-%m-%d'),
            )
        return self._client.intraday_minute_data(
            security_id=sec_id,
            exchange_segment=segment,
            instrument_type=inst_type,
            from_date=from_date.strftime('%Y-%m-%d'),
            to_date=to_date.strftime('%Y-%m-%d'),
            interval=int(dhan_interval),
        )

    def _parse_candle_response(self, resp: dict, symbol: str, interval: str) -> list[CandleData]:
        candles: list[CandleData] = []
        data = resp.get('data', {})

        if not data or (isinstance(data, dict) and 'open' not in data and not isinstance(data, list)):
            remarks = resp.get('remarks') or {}
            if isinstance(remarks, dict) and remarks.get('error_code') == 'DH-904':
                logger.debug('Dhan rate-limited for %s/%s — returning empty', symbol, interval)
            else:
                logger.warning('Dhan get_candles empty: sym=%s interval=%s status=%s remarks=%s',
                               symbol, interval, resp.get('status'), remarks)
            return candles

        def _ts(v) -> str:
            try:
                f = float(v)
                if f > 1_000_000_000:
                    return datetime.fromtimestamp(f).strftime('%Y-%m-%d %H:%M')
            except (ValueError, TypeError, OSError):
                pass
            return str(v)

        if isinstance(data, dict) and 'open' in data:
            timestamps = data.get('timestamp', data.get('start_Time', []))
            opens = data.get('open', [])
            highs = data.get('high', [])
            lows = data.get('low', [])
            closes = data.get('close', [])
            volumes = data.get('volume', [])
            for i in range(len(opens)):
                candles.append(CandleData(
                    date=fmt_candle_date(_ts(timestamps[i])) if i < len(timestamps) else '',
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=int(volumes[i]) if i < len(volumes) else 0,
                ))
        elif isinstance(data, list):
            for row in data:
                candles.append(CandleData(
                    date=fmt_candle_date(_ts(row.get('timestamp', row.get('start_Time', '')))),
                    open=float(row.get('open', 0)),
                    high=float(row.get('high', 0)),
                    low=float(row.get('low', 0)),
                    close=float(row.get('close', 0)),
                    volume=int(row.get('volume', 0)),
                ))
        return candles

    # ── WebSocket Live Market Feed ────────────────────────────────────────────

    # Mapping from REST segment string → MarketFeed integer constant
    _SEG_TO_WS_INT: dict[str, int] = {
        IDX: 0,       # IDX_I → 0
        NSE: 1,       # NSE_EQ → 1
        NFO: 2,       # NSE_FNO → 2
        CUR: 3,       # NSE_CURRENCY → 3
        BSE: 4,       # BSE_EQ → 4
        MCX: 5,       # MCX_COMM → 5
        BFO: 8,       # BSE_FNO → 8
    }

    def create_tick_queue(self, symbol: str) -> tuple[str, _queue.Queue]:
        """Subscribe to live ticks via Dhan WebSocket and return (key, queue)."""
        sec_int, segment = self._resolve_sec(symbol, '', '')
        ws_exchange = self._SEG_TO_WS_INT.get(segment, 1)
        q: _queue.Queue = _queue.Queue(maxsize=500)
        key = f'{segment}:{sec_int}'

        with self._sub_lock:
            if sec_int not in self._subscribers:
                self._subscribers[sec_int] = set()
            self._subscribers[sec_int].add(q)
            self._sec_exchange[sec_int] = ws_exchange
            self._sec_symbol[sec_int] = symbol  # remember original ticker

        self._ensure_ws(sec_int, ws_exchange)
        return (key, q)

    def remove_tick_queue(self, key: str, q: _queue.Queue) -> None:
        """Detach a queue from the WS feed."""
        try:
            _, sec_str = key.split(':', 1)
            sec_int = int(sec_str)
        except (ValueError, AttributeError):
            return
        with self._sub_lock:
            qs = self._subscribers.get(sec_int)
            if qs:
                qs.discard(q)
                if not qs:
                    del self._subscribers[sec_int]
                    ws_exchange = self._sec_exchange.pop(sec_int, 1)
                    self._ws_unsubscribe(sec_int, ws_exchange)

    def subscribe_ticks(
        self, symbols: list[str],
        callback: Callable[[str, float, dict[str, Any]], None] | None = None,
        exchange: str = 'NFO',
        security_id: str = '',
    ) -> bool:
        """Subscribe to live ticks via Dhan WS and invoke callback(symbol, ltp, tick_dict)."""
        if self._stub_mode:
            return False
        for sym in symbols:
            sec_int, segment = self._resolve_sec(sym, exchange, security_id)
            if not sec_int or not isinstance(sec_int, int):
                logger.warning('subscribe_ticks: cannot resolve %s to numeric id', sym)
                continue
            # Cache symbol → sec_id for future lookups (SSE streams, etc.)
            self._symbol_sec_cache[sym.upper()] = (sec_int, segment)
            ws_exchange = self._SEG_TO_WS_INT.get(segment, 1)
            with self._sub_lock:
                if sec_int not in self._subscribers:
                    self._subscribers[sec_int] = set()
                self._sec_exchange[sec_int] = ws_exchange
                self._sec_symbol[sec_int] = sym
            if callback:
                with self._callback_lock:
                    self._tick_callbacks.setdefault(sec_int, set()).add(callback)
            self._ensure_ws(sec_int, ws_exchange)
        return True

    def unsubscribe_ticks(self, symbols: list[str]) -> bool:
        """Unsubscribe from tick stream and remove callbacks."""
        for sym in symbols:
            sec_int, segment = self._resolve_sec(sym, '', '')
            if not sec_int:
                continue
            with self._callback_lock:
                self._tick_callbacks.pop(sec_int, None)
            ws_exchange = self._sec_exchange.get(sec_int, 1)
            with self._sub_lock:
                qs = self._subscribers.get(sec_int)
                if not qs:
                    self._ws_unsubscribe(sec_int, ws_exchange)
        return True

    def _ensure_ws(self, sec_int: int, ws_exchange: int) -> None:
        """Start or subscribe instrument on the shared WS feed.

        On boot, multiple scanners call this within milliseconds.  Instead of
        creating a new MarketFeed for each call (which triggers Dhan 429 rate-
        limit), we collect pending instruments and start ONE connection after a
        short delay (_WS_BATCH_DELAY seconds).
        """
        with self._ws_lock:
            if self._ws_feed and self._ws_thread and self._ws_thread.is_alive():
                # WS already running — just subscribe the new instrument
                try:
                    self._ws_feed.subscribe_symbols([(ws_exchange, str(sec_int))])
                    logger.info('Dhan WS: subscribed %s on exchange %s', sec_int, ws_exchange)
                except Exception as e:
                    logger.warning('Dhan WS subscribe failed: %s', e)
                return

            # WS not running — add to pending batch and schedule deferred start
            self._ws_pending.add((ws_exchange, str(sec_int)))
            if self._ws_start_timer is None:
                delay = self._WS_BATCH_DELAY * (2 ** self._ws_retry_count)
                delay = min(delay, 30.0)  # cap at 30s
                self._ws_start_timer = threading.Timer(delay, self._ws_batch_start)
                self._ws_start_timer.daemon = True
                self._ws_start_timer.start()
                logger.info('Dhan WS: deferred start in %.1fs (%d pending)',
                            delay, len(self._ws_pending))

    def _ws_batch_start(self) -> None:
        """Start WS with all collected instruments after the batch delay."""
        with self._ws_lock:
            self._ws_start_timer = None
            # If WS came alive in the meantime (another thread), just subscribe pending
            if self._ws_feed and self._ws_thread and self._ws_thread.is_alive():
                pending = list(self._ws_pending)
                self._ws_pending.clear()
                for inst in pending:
                    try:
                        self._ws_feed.subscribe_symbols([inst])
                    except Exception:
                        pass
                return

        # Collect ALL instruments: pending + subscribers + callbacks
        all_instruments: list[tuple[int, str]] = []
        seen: set[str] = set()
        with self._ws_lock:
            for inst in self._ws_pending:
                key = f'{inst[0]}:{inst[1]}'
                if key not in seen:
                    all_instruments.append(inst)
                    seen.add(key)
            self._ws_pending.clear()

        with self._sub_lock:
            for sid in self._subscribers:
                ex = self._sec_exchange.get(sid, 1)
                key = f'{ex}:{sid}'
                if key not in seen:
                    all_instruments.append((ex, str(sid)))
                    seen.add(key)
        with self._callback_lock:
            for sid in self._tick_callbacks:
                ex = self._sec_exchange.get(sid, 1)
                key = f'{ex}:{sid}'
                if key not in seen:
                    all_instruments.append((ex, str(sid)))
                    seen.add(key)

        if not all_instruments:
            return

        try:
            from dhanhq import marketfeed as _mf
            feed = _mf.MarketFeed(
                self._dhan_ctx,
                instruments=all_instruments,
                version='v2',
                on_ticks=self._ws_on_message,
                on_connect=self._ws_on_connect,
                on_close=lambda f: logger.warning('Dhan MarketFeed WS closed'),
                on_error=self._ws_on_error,
            )
            with self._ws_lock:
                self._ws_feed = feed
            # Run in our own thread to suppress SDK tracebacks on 429
            def _ws_run():
                try:
                    feed.run()
                except Exception:
                    pass  # already handled by on_error callback
            t = threading.Thread(target=_ws_run, daemon=True)
            t.start()
            with self._ws_lock:
                self._ws_thread = t
            logger.info('Dhan MarketFeed WS starting with %d instruments', len(all_instruments))
        except Exception as e:
            logger.error('Failed to start Dhan MarketFeed: %s', e)
            self._ws_schedule_retry(all_instruments)

    def _ws_on_connect(self, _feed: Any) -> None:
        """Called when WS actually connects successfully."""
        with self._ws_lock:
            self._ws_connect_time = monotonic()
            self._ws_retry_count = 0
        logger.info('Dhan MarketFeed WS connected')

    def _ws_on_error(self, _feed: Any, error: Any) -> None:
        """Handle WS errors — schedule retry with backoff on 429 / no-close-frame."""
        error_str = str(error)
        logger.error('Dhan MarketFeed WS error: %s', error_str)

        is_retriable = '429' in error_str or 'no close frame' in error_str
        if not is_retriable:
            return

        # Only reset retry count if connection stayed alive > 5 seconds
        with self._ws_lock:
            connect_age = monotonic() - getattr(self, '_ws_connect_time', 0)
            if connect_age > 5:
                self._ws_retry_count = 0
            self._ws_feed = None
            self._ws_thread = None

        # Re-queue all known instruments
        all_instruments = []
        with self._sub_lock:
            for sid in self._subscribers:
                ex = self._sec_exchange.get(sid, 1)
                all_instruments.append((ex, str(sid)))
        with self._callback_lock:
            for sid in self._tick_callbacks:
                ex = self._sec_exchange.get(sid, 1)
                all_instruments.append((ex, str(sid)))
        if all_instruments:
            self._ws_schedule_retry(all_instruments)

    def _ws_schedule_retry(self, instruments: list) -> None:
        """Re-queue instruments and schedule a retry with exponential backoff."""
        with self._ws_lock:
            for inst in instruments:
                self._ws_pending.add(inst if isinstance(inst, tuple) and len(inst) == 2
                                     else (1, str(inst)))
            self._ws_retry_count = min(self._ws_retry_count + 1, self._WS_MAX_RETRIES)
            if self._ws_start_timer is None and self._ws_retry_count <= self._WS_MAX_RETRIES:
                delay = self._WS_BATCH_DELAY * (2 ** self._ws_retry_count)
                delay = min(delay, 60.0)
                self._ws_start_timer = threading.Timer(delay, self._ws_batch_start)
                self._ws_start_timer.daemon = True
                self._ws_start_timer.start()
                logger.info('Dhan WS: retry %d in %.1fs (%d instruments)',
                            self._ws_retry_count, delay, len(self._ws_pending))

    def _ws_on_message(self, _feed: Any, data: dict) -> None:
        """Dispatch incoming WS tick to subscriber queues."""
        if not isinstance(data, dict):
            return
        sec_id = data.get('security_id')
        ltp_raw = data.get('LTP', data.get('last_price'))
        if sec_id is None or ltp_raw is None:
            return
        try:
            sec_int = int(sec_id)
            ltp = float(ltp_raw)
        except (ValueError, TypeError):
            return
        if ltp <= 0:
            return

        # Use original symbol if available, else fall back to sec_id
        ticker = self._sec_symbol.get(sec_int, str(sec_int))
        payload = json.dumps({
            'price': ltp,
            'ticker': ticker,
            'timestamp': now_ist().isoformat(),
        })
        with self._sub_lock:
            qs = self._subscribers.get(sec_int, set())
            for q in list(qs):
                try:
                    q.put_nowait(payload)
                except _queue.Full:
                    try:
                        q.get_nowait()
                        q.put_nowait(payload)
                    except Exception:
                        pass

        # Invoke registered tick callbacks (scalp scanner, etc.)
        tick_dict = {'price': ltp, 'ltp': ltp, 'ticker': ticker, 'security_id': sec_int}
        with self._callback_lock:
            cbs = list(self._tick_callbacks.get(sec_int, set()))
        if not cbs and sec_int in (6598, 6601, 6572, 6600):
            logger.debug('WS tick for CDS sec_id=%s but no callback registered (keys=%s)',
                         sec_int, list(self._tick_callbacks.keys())[:10])
        for cb in cbs:
            try:
                cb(ticker, ltp, tick_dict)
            except Exception as e:
                logger.warning('tick callback error for %s: %s', ticker, e)

    def _ws_unsubscribe(self, sec_int: int, ws_exchange: int) -> None:
        """Unsubscribe a single instrument from the WS feed."""
        with self._ws_lock:
            feed = self._ws_feed
        if feed:
            try:
                feed.unsubscribe_symbols([(ws_exchange, str(sec_int))])
                logger.info('Dhan WS: unsubscribed %d', sec_int)
            except Exception as e:
                logger.warning('Dhan WS unsubscribe failed: %s', e)
