"""
INDmoney/INDstocks Broker Adapter.

REST base:      https://api.indstocks.com
WebSocket feed: wss://ws-prices.indstocks.com/api/v1/ws/prices
Order updates:  wss://ws-order-updates.indstocks.com/api/v1/ws/trades

Ported from v1's backend/app/api/indmoney.py — all broker-specific
logic is encapsulated here. Nothing leaks outside BrokerAdapter interface.
"""

from __future__ import annotations

import csv
import io
import json
import os
import queue as _queue
import threading
from collections.abc import Callable
from typing import Any

import requests as _requests

from ...shared import time as _shared_time
from ...shared.logger import get_logger
from ...shared.parse_expiry import parse_expiry, parse_expiry_datetime
from ...shared.time import clock, datetime, fmt_candle_date, now_ist, sleep, timedelta
from .base import (
    BrokerAdapter,
    CandleData,
    HoldingInfo,
    InstrumentInfo,
    OrderResult,
    PositionInfo,
    QuoteResult,
)

logger = get_logger('indmoney_broker')

# ── API URLs ──────────────────────────────────────────────────────────────────
_BASE_URL     = 'https://api.indstocks.com'
_WS_PRICE_URL = 'wss://ws-prices.indstocks.com/api/v1/ws/prices'
_WS_ORDER_URL = 'wss://ws-order-updates.indstocks.com/api/v1/ws/trades'

# Max age (seconds) a WS tick-cache entry is trusted before falling back to
# REST — IndStocks WS can silently stop pushing for a given instrument
# (observed on index codes) while reporting the socket as still "connected".
_TICK_TTL_SEC = 5.0

# ── Interval mapping ─────────────────────────────────────────────────────────
_IV_MAP = {
    '1m': '1minute', '3m': '3minute', '5m': '5minute',
    '10m': '10minute', '15m': '15minute', '30m': '30minute',
    '1h': '60minute', '2h': '120minute', '4h': '240minute',
    '1d': '1day', '1w': '1week', '1M': '1month',
}

# ── Index ticker map ─────────────────────────────────────────────────────────
_INDEX_MAP = {
    '^NSEI': 'NIFTY', 'NIFTY': 'NIFTY', '^NSEBANK': 'BANKNIFTY',
    'BANKNIFTY': 'BANKNIFTY', '^CNXFIN': 'FINNIFTY',
    '^NSEMDCP50': 'MIDCPNIFTY',
}


class INDMoneyBroker(BrokerAdapter):
    """INDmoney/INDstocks broker — full REST + WebSocket implementation.

    All IndStocks API specifics (endpoints, payload shapes, scrip code
    formats, instrument master CSV) are encapsulated here.
    """

    def __init__(self, access_token: str = '', **kwargs: Any) -> None:
        self._token = access_token or os.environ.get('INDMONEY_ACCESS_TOKEN', '')
        self._stub = not bool(self._token)
        if self._stub:
            logger.warning('INDmoney access token empty — running in stub mode')

        # Instrument master cache (populated lazily)
        self._inst_master: dict[str, list[dict]] = {}
        self._inst_lock = threading.Lock()
        self._fno_sym_index: dict[str, list[dict]] = {}

        # Tick cache + WebSocket state
        self._tick_cache: dict[str, dict] = {}
        self._tick_lock = threading.Lock()
        self._tick_callbacks: dict[str, set] = {}
        self._callback_lock = threading.Lock()
        self._ws_instance = None
        self._ws_thread: threading.Thread | None = None
        self._ws_lock = threading.Lock()
        self._subscribers: dict[str, set] = {}
        self._sub_lock = threading.Lock()
        self._ws_msg_count = 0

        # Cash cache (60s TTL)
        self._cash_cache: tuple[float, float] | None = None
        self._cash_lock = threading.Lock()

        # Scrip code cache: "SBIN.NS" → "NSE_2885"
        self._scrip_cache: dict[str, str] = {}
        self._scrip_lock = threading.Lock()

        # Candle 404 cache — skip scrips that don't support /historical
        self._candles_404: dict[str, float] = {}
        self._candles_404_lock = threading.Lock()

    @property
    def broker_name(self) -> str:
        return 'indmoney'

    # ── Auth helpers ──────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {'Authorization': self._token, 'Content-Type': 'application/json'}

    def _connected(self) -> bool:
        return bool(self._token)

    # ── Instrument Master ─────────────────────────────────────────────────

    def _load_instruments(self, source: str = 'equity') -> list[dict]:
        """Fetch instrument master CSV from IndStocks, cached per-source."""
        with self._inst_lock:
            if source in self._inst_master:
                return self._inst_master[source]
        try:
            r = _requests.get(f'{_BASE_URL}/market/instruments',
                              headers=self._headers(),
                              params={'source': source}, timeout=15)
            if not r.ok:
                logger.warning(f'Instrument master {source}: HTTP {r.status_code}')
                return []
            reader = csv.DictReader(io.StringIO(r.text))
            rows = list(reader)
            with self._inst_lock:
                self._inst_master[source] = rows
            logger.info(f'Loaded {len(rows)} instruments (source={source})')
            return rows
        except Exception as e:
            logger.warning(f'Instrument master load error ({source}): {e}')
            return []

    def _build_fno_index(self) -> None:
        """Build O(1) lookup by TRADING_SYMBOL and CUSTOM_SYMBOL."""
        if self._fno_sym_index:
            return
        rows = self._load_instruments('fno')
        for inst in rows:
            for key_field in ('TRADING_SYMBOL', 'tradingsymbol',
                              'CUSTOM_SYMBOL', 'custom_symbol'):
                val = (inst.get(key_field) or '').strip().upper()
                if val:
                    self._fno_sym_index.setdefault(val, []).append(inst)

    def _resolve_fo_instrument(self, symbol: str) -> dict | None:
        """Resolve F&O trading symbol → instrument master row (nearest expiry)."""
        self._build_fno_index()
        sym = symbol.upper()
        now = now_ist()
        matches = self._fno_sym_index.get(sym)
        if not matches:
            return None
        candidates = []
        for inst in matches:
            exp_dt = parse_expiry_datetime(inst.get('EXPIRY_DATE') or '')
            if exp_dt and exp_dt >= now:
                candidates.append((exp_dt, inst))
        if not candidates:
            # Fallback: most recent even if past (settlement day)
            fallback = []
            for inst in matches:
                exp_dt = parse_expiry_datetime(inst.get('EXPIRY_DATE') or '') or datetime.min
                fallback.append((exp_dt, inst))
            fallback.sort(key=lambda x: x[0], reverse=True)
            return fallback[0][1]
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _fo_scrip_code(self, inst: dict) -> str:
        """Return NFO_<sec_id> or BFO_<sec_id> for F&O instrument row."""
        sec_id = (inst.get('SECURITY_ID') or '').strip()
        seg = (inst.get('SEGMENT') or '').strip().upper()
        exch = (inst.get('EXCH') or '').strip().upper()
        if exch.startswith('B') or seg == 'BFO':
            return f'BFO_{sec_id}'
        return f'NFO_{sec_id}'

    # ── Scrip code resolution ─────────────────────────────────────────────

    def _norm(self, ticker: str) -> str:
        t = ticker.upper()
        if t in _INDEX_MAP:
            return _INDEX_MAP[t]
        return t.replace('.NS', '').replace('.BO', '')

    def _exchange_for(self, ticker: str) -> str:
        return 'BSE' if ticker.upper().endswith('.BO') else 'NSE'

    # Hardcoded index scrip codes (not in equity instrument master)
    _INDEX_CODES: dict[str, str] = {
        '^NSEI':      'NSE_40000001',   # NIFTY 50
        '^NSEBANK':   'NSE_40000002',   # BANK NIFTY
        '^CNXFIN':    'NSE_40000003',   # FIN NIFTY
        '^NSEMDCP50': 'NSE_40000005',   # MIDCAP NIFTY
        '^BSESN':     'BSE_40000006',   # SENSEX
        'NIFTY':      'NSE_40000001',
        'BANKNIFTY':  'NSE_40000002',
        'FINNIFTY':   'NSE_40000003',
        'MIDCPNIFTY': 'NSE_40000005',
        'SENSEX':     'BSE_40000006',
        '^INDIAVIX':  'NSE_40000007',   # INDIA VIX
        'INDIAVIX':   'NSE_40000007',
    }

    def _scrip_code(self, ticker: str, source: str = 'equity') -> str | None:
        """Convert Yahoo-style ticker → IndStocks scrip code (NSE_3045)."""
        key = f'{ticker}|{source}'
        with self._scrip_lock:
            if key in self._scrip_cache:
                return self._scrip_cache[key]

        # Check hardcoded index codes first
        idx_code = self._INDEX_CODES.get(ticker.upper())
        if idx_code:
            with self._scrip_lock:
                self._scrip_cache[key] = idx_code
            return idx_code

        norm = self._norm(ticker)
        exch = self._exchange_for(ticker)

        instruments = self._load_instruments(source)
        for inst in instruments:
            t_sym = (inst.get('TRADING_SYMBOL') or inst.get('tradingsymbol') or '').strip().upper()
            t_exch = (inst.get('EXCH') or inst.get('exchange') or '').strip().upper()
            sec_id = (inst.get('SECURITY_ID') or inst.get('security_id') or '').strip()

            if not t_sym or not sec_id:
                continue
            if t_sym == norm and (t_exch == exch or not t_exch):
                code = f'{t_exch or exch}_{sec_id}'
                with self._scrip_lock:
                    self._scrip_cache[key] = code
                return code

        # Try FNO master
        if source == 'equity':
            return self._scrip_code(ticker, 'fno')
        return None

    def _security_id(self, ticker: str, source: str = 'equity') -> str | None:
        code = self._scrip_code(ticker, source)
        return code.split('_', 1)[1] if code else None

    # ── Core: place_order ─────────────────────────────────────────────────

    def place_order(
        self, symbol: str, side: str, qty: int,
        order_type: str = 'MARKET', price: float = 0.0,
        exchange: str = 'NFO', security_id: str = '',
        trigger_price: float = 0.0, product_type: str = '',
        tag: str = '',
    ) -> OrderResult:
        if self._stub:
            return OrderResult(success=False, message='INDmoney stub — no token')

        # Determine segment + resolve security_id
        is_fno = exchange.upper() in ('NFO', 'BFO', 'NSE_FNO', 'BSE_FNO')
        if is_fno:
            inst = self._resolve_fo_instrument(symbol)
            if not inst:
                return OrderResult(success=False,
                                   message=f'F&O instrument not found: {symbol}')
            sec_id = security_id or (inst.get('SECURITY_ID') or '').strip()
            seg = (inst.get('EXCH') or '').strip().upper()
            api_exchange = 'BSE' if seg.startswith('B') else 'NSE'
            segment = 'DERIVATIVE'
        else:
            sec_id = security_id or self._security_id(symbol) or ''
            if not sec_id:
                return OrderResult(success=False,
                                   message=f'Instrument not found: {symbol}')
            api_exchange = self._exchange_for(symbol)
            segment = 'EQUITY'

        # Product mapping
        _product_map = {
            'NRML': 'MARGIN', 'MIS': 'INTRADAY', 'CNC': 'CNC',
            'MARGIN': 'MARGIN', 'INTRADAY': 'INTRADAY',
        }
        api_product = _product_map.get(
            (product_type or 'MARGIN' if is_fno else 'CNC').upper(), 'MARGIN')

        algo_id = '9999999999999999' if api_exchange == 'BSE' else '99999'

        payload: dict[str, Any] = {
            'txn_type': side.upper(),
            'exchange': api_exchange,
            'segment': segment,
            'product': api_product,
            'order_type': order_type.upper(),
            'validity': 'DAY',
            'security_id': sec_id,
            'qty': qty,
            'is_amo': False,
            'algo_id': algo_id,
        }
        if order_type.upper() == 'LIMIT' and price:
            payload['limit_price'] = price
        if order_type.upper() in ('SL', 'SL-M') and trigger_price:
            payload['trigger_price'] = trigger_price

        logger.info(f'[order] {side} {symbol} qty={qty} type={order_type} '
                     f'product={api_product} sec_id={sec_id}')
        try:
            r = _requests.post(f'{_BASE_URL}/order', headers=self._headers(),
                               json=payload, timeout=10)
            resp = r.json()
            if r.ok:
                data = resp.get('data', {})
                self._invalidate_cash_cache()
                return OrderResult(
                    success=True,
                    order_id=str(data.get('order_id', '')),
                    status=str(data.get('status', 'PLACED')),
                    raw=resp,
                )
            return OrderResult(success=False,
                               message=resp.get('message') or str(resp),
                               raw=resp)
        except Exception as e:
            logger.error(f'Order placement error: {e}')
            return OrderResult(success=False, message=str(e))

    # ── Core: cancel_order ────────────────────────────────────────────────

    def cancel_order(self, order_id: str) -> bool:
        if self._stub:
            return False
        try:
            r = _requests.post(f'{_BASE_URL}/order/cancel',
                               headers=self._headers(),
                               json={'order_id': order_id, 'segment': 'DERIVATIVE'},
                               timeout=10)
            return r.ok
        except Exception as e:
            logger.error(f'Cancel order error: {e}')
            return False

    # ── Core: get_order_status ────────────────────────────────────────────

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        if self._stub:
            return {}
        try:
            orders = self.get_order_list()
            for o in orders:
                if str(o.get('order_id', '')) == str(order_id):
                    return o
            return {}
        except Exception:
            return {}

    def get_order_list(self) -> list[dict[str, Any]]:
        if self._stub:
            return []
        try:
            r = _requests.get(f'{_BASE_URL}/order-book',
                              headers=self._headers(), timeout=5)
            if r.ok:
                data = r.json().get('data')
                return data if isinstance(data, list) else []
            return []
        except Exception:
            return []

    # ── Core: get_ltp ─────────────────────────────────────────────────────

    def get_ltp(self, symbol: str, exchange: str = 'NFO',
                security_id: str = '') -> float | None:
        if self._stub:
            return None

        # Check tick cache first — only trust it while fresh; IndStocks WS can
        # go quiet for a given instrument without the socket itself dropping.
        code = self._scrip_code(symbol)
        if code:
            with self._tick_lock:
                cached = self._tick_cache.get(code)
            if cached and clock() - cached.get('ts', 0) < _TICK_TTL_SEC:
                p = cached.get('ltp') or cached.get('last_price')
                if p:
                    return float(p)

        # F&O option LTP
        if code and (code.startswith('NFO_') or code.startswith('BFO_')):
            p = self._option_ltp(symbol)
            if p:
                return float(p)

        # REST fallback
        scrip = code or f'{self._exchange_for(symbol)}_{self._norm(symbol)}'
        try:
            r = _requests.get(f'{_BASE_URL}/market/quotes/ltp',
                              headers=self._headers(),
                              params={'scrip-codes': scrip}, timeout=2)
            if r.ok:
                data = r.json().get('data') or {}
                entry = data.get(scrip) if isinstance(data, dict) else None
                if isinstance(entry, dict):
                    p = entry.get('live_price') or entry.get('ltp') or entry.get('last_price')
                    if p:
                        return float(p)
                elif isinstance(data, list) and data:
                    p = (data[0] or {}).get('ltp') or (data[0] or {}).get('last_price')
                    if p:
                        return float(p)
        except Exception:
            pass

        # yfinance last-resort fallback — covers symbols IndStocks lists but
        # can't quote (notably ^BSESN / SENSEX which 400s on /market/quotes/ltp
        # despite appearing in index master).  15-min delayed but better than null.
        try:
            import yfinance as _yf
            info = _yf.Ticker(symbol).fast_info
            for k in ('last_price', 'lastPrice', 'regularMarketPrice'):
                v = info.get(k) if hasattr(info, 'get') else getattr(info, k, None)
                if v:
                    return float(v)
        except Exception:
            pass
        return None

    def _option_ltp(self, symbol: str) -> float | None:
        """Get LTP for F&O contract via resolved instrument."""
        inst = self._resolve_fo_instrument(symbol)
        if not inst:
            return None
        code = self._fo_scrip_code(inst)
        with self._tick_lock:
            cached = self._tick_cache.get(code)
        if cached and clock() - cached.get('ts', 0) < _TICK_TTL_SEC:
            p = cached.get('ltp') or cached.get('last_price') or cached.get('live_price')
            if p:
                return float(p)
        try:
            r = _requests.get(f'{_BASE_URL}/market/quotes/ltp',
                              headers=self._headers(),
                              params={'scrip-codes': code}, timeout=4)
            if r.ok:
                data = r.json().get('data') or {}
                entry = data.get(code) if isinstance(data, dict) else None
                if isinstance(entry, dict):
                    p = (entry.get('live_price') or entry.get('ltp')
                         or entry.get('last_price'))
                    if p:
                        return float(p)
        except Exception:
            pass
        return None

    # ── Core: get_quote ───────────────────────────────────────────────────

    def get_quote(self, symbol: str, exchange: str = 'NFO',
                  security_id: str = '') -> QuoteResult | None:
        if self._stub:
            return None

        is_fno = exchange.upper() in ('NFO', 'BFO', 'NSE_FNO')
        if is_fno:
            return self._option_quote(symbol)

        code = self._scrip_code(symbol) or f'{self._exchange_for(symbol)}_{self._norm(symbol)}'
        try:
            r = _requests.get(f'{_BASE_URL}/market/quotes/full',
                              headers=self._headers(),
                              params={'scrip-codes': code}, timeout=5)
            if not r.ok:
                return None
            raw = r.json().get('data') or {}
            if isinstance(raw, dict):
                data = raw.get(code) or (next(iter(raw.values()), {}) if raw else {})
            elif isinstance(raw, list):
                data = raw[0] if raw else {}
            else:
                data = {}
            return QuoteResult(
                symbol=symbol,
                ltp=float(data.get('live_price') or data.get('ltp') or 0),
                open=float(data.get('open') or 0),
                high=float(data.get('high') or 0),
                low=float(data.get('low') or 0),
                close=float(data.get('close') or data.get('prev_close') or 0),
                volume=int(float(data.get('volume') or 0)),
            )
        except Exception as e:
            logger.warning(f'Quote error {symbol}: {e}')
            return None

    def _option_quote(self, symbol: str) -> QuoteResult | None:
        """Full quote for F&O contract with bid/ask from market depth."""
        inst = self._resolve_fo_instrument(symbol.upper())
        if not inst:
            return None
        code = self._fo_scrip_code(inst)
        try:
            r = _requests.get(f'{_BASE_URL}/market/quotes/full',
                              headers=self._headers(),
                              params={'scrip-codes': code}, timeout=4)
            if not r.ok:
                return None
            data_raw = r.json().get('data') or {}
            d = data_raw.get(code) if isinstance(data_raw, dict) else None
            if not isinstance(d, dict):
                return None

            # Parse market depth for bid/ask
            md = d.get('market_depth') or {}
            md_in = md.get(code) if isinstance(md, dict) else None
            depth = (md_in or {}).get('depth') if isinstance(md_in, dict) else None
            bid = ask = 0.0
            if isinstance(depth, list) and depth:
                top = depth[0] or {}
                try:
                    bid = float((top.get('buy') or {}).get('price') or 0)
                    ask = float((top.get('sell') or {}).get('price') or 0)
                except Exception:
                    pass
            elif isinstance(depth, dict):
                bids = depth.get('buy') or depth.get('bids') or []
                asks = depth.get('sell') or depth.get('asks') or []
                try:
                    bid = float(((bids[0] or {}) if bids else {}).get('price') or 0)
                    ask = float(((asks[0] or {}) if asks else {}).get('price') or 0)
                except Exception:
                    pass

            ltp = float(d.get('live_price') or d.get('ltp') or d.get('last_price') or 0)
            return QuoteResult(
                symbol=symbol, ltp=ltp, bid=bid, ask=ask,
                volume=int(float(d.get('volume') or d.get('day_volume') or 0)),
                open=float(d.get('open') or 0),
                high=float(d.get('high') or 0),
                low=float(d.get('low') or 0),
                close=float(d.get('close') or d.get('prev_close') or 0),
            )
        except Exception:
            return None

    # ── Batch quotes (option chain etc.) ──────────────────────────────────

    def get_quotes_batch(self, scrip_codes: list[str]) -> dict[str, dict]:
        """Fetch full quotes for multiple F&O scrip codes in one shot."""
        if self._stub or not scrip_codes:
            return {}
        out: dict[str, dict] = {}
        CHUNK = 20
        for i in range(0, len(scrip_codes), CHUNK):
            chunk = scrip_codes[i:i + CHUNK]
            try:
                r = _requests.get(f'{_BASE_URL}/market/quotes/full',
                                  headers=self._headers(),
                                  params={'scrip-codes': ','.join(chunk)}, timeout=6)
                if not r.ok:
                    continue
                data = r.json().get('data') or {}
                if not isinstance(data, dict):
                    continue
                for code in chunk:
                    d = data.get(code)
                    if not isinstance(d, dict):
                        continue
                    md = d.get('market_depth') or {}
                    md_in = md.get(code) if isinstance(md, dict) else None
                    depth = (md_in or {}).get('depth') if isinstance(md_in, dict) else None
                    bid = ask = 0.0
                    if isinstance(depth, list) and depth:
                        top = depth[0] or {}
                        try:
                            bid = float((top.get('buy') or {}).get('price') or 0)
                            ask = float((top.get('sell') or {}).get('price') or 0)
                        except Exception:
                            pass
                    ltp = float(d.get('live_price') or d.get('ltp') or d.get('last_price') or 0)
                    out[code] = {
                        'bid':    bid,
                        'ask':    ask,
                        'ltp':    ltp,
                        'oi':     int(float(d.get('oi') or d.get('open_interest') or 0)),
                        'volume': int(float(d.get('volume') or d.get('day_volume') or 0)),
                    }
            except Exception as e:
                logger.warning('Batch quote error: %s', e)
        return out

    # ── Instrument Master (public adapter methods) ─────────────────────────

    def load_instruments(self, source: str = 'fno') -> list:
        return self._load_instruments(source)

    def underlying_lot_size(self, underlying: str) -> int | None:
        base = self._norm(underlying)
        today = _shared_time.today_ist()
        rows = self._load_instruments('fno')
        best_lot, best_exp = None, None
        for inst in rows:
            t_sym = (inst.get('TRADING_SYMBOL') or '').strip().upper()
            if not t_sym.startswith(base):
                continue
            try:
                lot = int(float(inst.get('LOT_SIZE') or inst.get('lot_size') or 0))
            except Exception:
                continue
            if lot <= 0:
                continue
            exp = parse_expiry(inst.get('EXPIRY_DATE') or inst.get('expiry_date') or '')
            if not exp or exp < today:
                continue
            opt = (inst.get('OPTION_TYPE') or '').strip().upper()
            if best_exp is None or exp < best_exp:
                best_lot, best_exp = lot, exp
            if opt == 'XX' and (best_exp is None or exp <= best_exp):
                return lot
        return best_lot

    def resolve_option_contract(
        self, underlying: str, option_type: str, strike: float,
    ) -> dict | None:
        base = self._norm(underlying)
        today = _shared_time.today_ist()
        rows = self._load_instruments('fno')
        candidates = []
        for inst in rows:
            sym      = (inst.get('TRADING_SYMBOL') or '').strip().upper()
            opt_type = (inst.get('OPTION_TYPE') or '').strip().upper()
            exch     = (inst.get('EXCH') or '').strip().upper()
            if not sym.startswith(base) or opt_type != option_type.upper():
                continue
            if exch not in ('NFO', 'BFO', 'NSE', 'BSE'):
                continue
            exp = parse_expiry(inst.get('EXPIRY_DATE') or '')
            if exp is None or exp < today:
                continue
            try:
                inst_strike = float(inst.get('STRIKE_PRICE') or 0)
            except Exception:
                continue
            candidates.append({
                'symbol': sym, 'sec_id': (inst.get('SECURITY_ID') or '').strip(),
                'expiry': exp, 'expiry_s': (inst.get('EXPIRY_DATE') or '').strip(),
                'strike': inst_strike,
                'display': (inst.get('CUSTOM_SYMBOL') or '').strip(),
            })
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x['expiry'], abs(x['strike'] - strike)))
        best = candidates[0]
        ltp = self.get_ltp(best['symbol'], exchange='NFO', security_id=best['sec_id'])
        return {
            'trading_symbol': best['symbol'],
            'display_symbol': best.get('display') or best['symbol'],
            'security_id': best['sec_id'],
            'expiry_date': best['expiry_s'],
            'strike': best['strike'],
            'ltp': ltp,
        }

    # ── Core: get_positions ───────────────────────────────────────────────

    def get_positions(self) -> list[PositionInfo]:
        if self._stub:
            return []
        try:
            r = _requests.get(f'{_BASE_URL}/portfolio/positions',
                              headers=self._headers(), timeout=5,
                              params={'segment': 'derivative', 'product': 'margin'})
            if not r.ok:
                return []
            data = r.json().get('data')
            if not isinstance(data, list):
                return []
            positions = []
            for p in data:
                positions.append(PositionInfo(
                    symbol=p.get('tradingSymbol') or p.get('trading_symbol') or '',
                    qty=int(p.get('netQty') or p.get('net_qty') or 0),
                    avg_price=float(p.get('averagePrice') or p.get('avg_price') or 0),
                    ltp=float(p.get('ltp') or p.get('last_price') or 0),
                    pnl=float(p.get('unrealizedProfit') or p.get('pnl') or 0),
                    security_id=str(p.get('securityId') or p.get('security_id') or ''),
                    exchange=p.get('exchange') or '',
                    product_type=p.get('product') or p.get('productType') or '',
                ))
            return positions
        except Exception as e:
            logger.warning(f'Positions error: {e}')
            return []

    # ── Core: get_available_cash ──────────────────────────────────────────

    def get_available_cash(self) -> float:
        if self._stub:
            return 0.0
        now = clock()
        with self._cash_lock:
            if self._cash_cache and now - self._cash_cache[0] < 60:
                return self._cash_cache[1]
        try:
            r = _requests.get(f'{_BASE_URL}/funds',
                              headers=self._headers(), timeout=5)
            if not r.ok:
                return 0.0
            data = r.json().get('data') or {}
            avl = data.get('detailed_avl_balance') or {}
            cash = avl.get('option_buy')
            if cash is None or float(cash) == 0:
                sod = float(data.get('sod_balance', 0) or 0)
                added = float(data.get('funds_added', 0) or 0)
                drawn = float(data.get('funds_withdrawn', 0) or 0)
                cash = sod + added - drawn
            cash = float(cash)
            with self._cash_lock:
                self._cash_cache = (now, cash)
            return cash
        except Exception as e:
            logger.warning(f'Funds error: {e}')
            return 0.0

    def _invalidate_cash_cache(self) -> None:
        with self._cash_lock:
            self._cash_cache = None

    # ── Holdings ──────────────────────────────────────────────────────────

    def get_holdings(self) -> list[HoldingInfo]:
        if self._stub:
            return []
        try:
            r = _requests.get(f'{_BASE_URL}/portfolio/holdings',
                              headers=self._headers(), timeout=5)
            if not r.ok:
                return []
            data = r.json().get('data')
            if not isinstance(data, list):
                return []
            return [
                HoldingInfo(
                    symbol=h.get('tradingSymbol') or h.get('trading_symbol') or '',
                    qty=int(h.get('totalQty') or h.get('quantity') or 0),
                    avg_price=float(h.get('avgCostPrice') or h.get('avg_price') or 0),
                    ltp=float(h.get('ltp') or h.get('last_price') or 0),
                    pnl=float(h.get('unrealizedProfit') or h.get('pnl') or 0),
                    security_id=str(h.get('securityId') or h.get('security_id') or ''),
                )
                for h in data
            ]
        except Exception as e:
            logger.warning(f'Holdings error: {e}')
            return []

    # ── Candles ───────────────────────────────────────────────────────────

    def get_candles(
        self, symbol: str, interval: str = '5m', days: int = 5,
        exchange: str = 'NFO', security_id: str = '',
    ) -> list[CandleData]:
        if self._stub:
            return []
        code = self._scrip_code(symbol)
        if not code:
            return []

        # Skip if recently learned this code is unsupported
        with self._candles_404_lock:
            cooldown = self._candles_404.get(f'{code}|{interval}', 0)
        if cooldown and clock() < cooldown:
            return []

        to_dt = now_ist()
        from_dt = to_dt - timedelta(days=days)
        from_ts = int(from_dt.timestamp() * 1000)
        to_ts = int(to_dt.timestamp() * 1000)

        ind_interval = _IV_MAP.get(interval, interval)

        try:
            r = _requests.get(
                f'{_BASE_URL}/market/historical/{ind_interval}',
                headers=self._headers(),
                params={
                    'scrip-codes': code,
                    'start_time': from_ts,
                    'end_time': to_ts,
                },
                timeout=15,
            )
            if not r.ok:
                if r.status_code == 400 and 'Invalid scrip' in r.text:
                    with self._candles_404_lock:
                        self._candles_404[f'{code}|{interval}'] = clock() + 3600
                else:
                    logger.warning(f'Candles {symbol} {interval}: {r.status_code}')
                return []

            raw_data = r.json().get('data') or {}
            raw: list = []
            if isinstance(raw_data, dict):
                for v in raw_data.values():
                    if isinstance(v, dict) and 'candles' in v:
                        raw = v['candles']
                        break
                    elif isinstance(v, list):
                        raw = v
                        break
            elif isinstance(raw_data, list):
                raw = raw_data

            candles = []
            for c in raw:
                if isinstance(c, list) and len(c) >= 6:
                    ts_s = c[0]
                    o, h, l, cl, vol = float(c[1]), float(c[2]), float(c[3]), float(c[4]), int(c[5])
                elif isinstance(c, dict):
                    ts_s = c.get('ts') or c.get('timestamp') or c.get('t') or 0
                    o = float(c.get('o', c.get('open', 0)))
                    h = float(c.get('h', c.get('high', 0)))
                    l = float(c.get('l', c.get('low', 0)))
                    cl = float(c.get('c', c.get('close', 0)))
                    vol = int(c.get('v', c.get('volume', 0)))
                else:
                    continue
                dt_str = fmt_candle_date(datetime.fromtimestamp(ts_s), intraday=True)
                candles.append(CandleData(date=dt_str, open=o, high=h, low=l,
                                          close=cl, volume=vol))

            # Ensure oldest→newest (some endpoints return newest-first)
            candles.sort(key=lambda x: x.date)
            return candles
        except Exception as e:
            logger.warning(f'Candles error {symbol}: {e}')
            return []

    # ── Tick stream (WebSocket) ───────────────────────────────────────────

    def subscribe_ticks(
        self, symbols: list[str],
        callback: Callable[[str, float, dict[str, Any]], None] | None = None,
        exchange: str = 'NFO',
    ) -> bool:
        if self._stub:
            return False

        codes = []
        for sym in symbols:
            code = self._scrip_code(sym)
            if code:
                codes.append(code)
                if callback:
                    with self._callback_lock:
                        self._tick_callbacks.setdefault(code, set()).add(callback)

        if not codes:
            return False

        # Start WebSocket if not running
        self._ensure_ws()

        # Subscribe
        self._ws_subscribe(codes)
        return True

    def unsubscribe_ticks(self, symbols: list[str]) -> bool:
        codes = []
        for sym in symbols:
            code = self._scrip_code(sym)
            if code:
                codes.append(code)
                with self._callback_lock:
                    self._tick_callbacks.pop(code, None)
        if codes:
            self._ws_unsubscribe(codes)
        return True

    def create_tick_queue(
        self, symbol: str, exchange: str = '', security_id: str = '',
    ) -> tuple[str, _queue.Queue]:
        """Create a queue fed by WebSocket ticks for *symbol*.

        Returns (scrip_code, queue).  Mirrors old backend's SSE stream
        pattern: queue gets JSON payloads pushed from _ws_on_message.

        exchange/security_id accepted for interface parity with DhanBroker
        but not yet used — same limitation as get_ltp above (_scrip_code
        does its own lookup). Fine for now: option live-tick resolution
        is only exercised on the active broker (Dhan).
        """
        code = self._scrip_code(symbol)
        if not code:
            return ('', _queue.Queue(maxsize=50))
        q: _queue.Queue = _queue.Queue(maxsize=50)
        with self._sub_lock:
            if code not in self._subscribers:
                self._subscribers[code] = set()
            self._subscribers[code].add(q)
        self._ensure_ws()
        self._ws_subscribe([code])
        return (code, q)

    def remove_tick_queue(self, code: str, q: _queue.Queue) -> None:
        """Detach an SSE queue.  Unsubscribe WS if no queues/callbacks left."""
        with self._sub_lock:
            qs = self._subscribers.get(code)
            if qs:
                qs.discard(q)
                if not qs:
                    del self._subscribers[code]
                    # Only unsubscribe WS if no callbacks either
                    with self._callback_lock:
                        has_cb = bool(self._tick_callbacks.get(code))
                    if not has_cb:
                        self._ws_unsubscribe([code])

    def _ensure_ws(self) -> None:
        """Start WS thread if not already running."""
        with self._ws_lock:
            if self._ws_thread and self._ws_thread.is_alive():
                return
        t = threading.Thread(target=self._ws_run, daemon=True, name='ind-ws')
        t.start()
        with self._ws_lock:
            self._ws_thread = t

    def _ws_run(self) -> None:
        """WebSocket event loop with reconnection."""
        try:
            import websocket
        except ImportError:
            logger.error('websocket-client not installed — WS ticks unavailable')
            return

        while True:
            try:
                ws = websocket.WebSocketApp(
                    _WS_PRICE_URL,
                    header={'Authorization': self._token},
                    on_message=self._ws_on_message,
                    on_open=self._ws_on_open,
                    on_close=lambda ws, c, r: logger.warning(f'WS closed: {c} {r}'),
                    on_error=lambda ws, e: logger.error(f'WS error: {e}'),
                )
                with self._ws_lock:
                    self._ws_instance = ws
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                logger.error(f'WS run error: {e}')
            sleep(5)  # Reconnect delay

    def _ws_on_open(self, ws) -> None:
        logger.info('INDmoney WebSocket connected')
        with self._sub_lock:
            codes = list(self._subscribers.keys())
        if codes:
            self._ws_subscribe(codes)

    def _ws_on_message(self, ws, message: str) -> None:
        try:
            self._ws_msg_count += 1
            # IndStocks double-encodes: WS frame is a JSON string whose
            # value is itself a JSON object string.  Decode twice.
            parsed = json.loads(message)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)

            # Normalise to list of ticks
            if isinstance(parsed, list):
                raw_ticks = parsed
            elif isinstance(parsed, dict):
                raw_ticks = [parsed]
            else:
                return

            for t in raw_ticks:
                # IndStocks format: {"instrument":"3045","data":{"ltp":1012.7}}
                # instrument = bare security_id (no exchange prefix)
                sec_id = str(t.get('instrument') or t.get('scripCode')
                             or t.get('scrip_code') or t.get('code') or '')
                if not sec_id:
                    continue

                # Price is nested under "data" key OR top-level
                d = t.get('data') or {}
                ltp = (d.get('ltp') or d.get('price') or d.get('last_price')
                       or t.get('ltp') or t.get('price') or t.get('last_price')
                       or t.get('live_price'))
                if not ltp:
                    continue

                # Resolve bare security_id → "NSE_3045" scrip code
                code = None
                with self._sub_lock:
                    for k in self._subscribers:
                        if k.endswith(f'_{sec_id}'):
                            code = k
                            break
                if not code:
                    for k in list(self._tick_cache.keys()):
                        if k.endswith(f'_{sec_id}'):
                            code = k
                            break
                if not code:
                    with self._callback_lock:
                        for k in self._tick_callbacks:
                            if k.endswith(f'_{sec_id}'):
                                code = k
                                break
                if not code:
                    code = f'NSE_{sec_id}'

                tick = {
                    'scrip_code': code,
                    'ltp':    float(ltp),
                    'open':   d.get('open') or d.get('o'),
                    'high':   d.get('high') or d.get('h'),
                    'low':    d.get('low') or d.get('l'),
                    'close':  d.get('close') or d.get('c') or d.get('prev_close'),
                    'volume': d.get('volume') or d.get('v'),
                    'change': d.get('net_change') or d.get('change'),
                    'change_pct': d.get('change_percent'),
                    'ts':     clock(),
                }

                with self._tick_lock:
                    self._tick_cache[code] = tick

                # Push to SSE subscriber queues (same pattern as old backend)
                payload = json.dumps({
                    'price': float(ltp), 'ticker': code,
                    'timestamp': now_ist().isoformat(),
                })
                with self._sub_lock:
                    queues = self._subscribers.get(code)
                    if queues:
                        dead = set()
                        for q in list(queues):
                            try:
                                q.put_nowait(payload)
                            except _queue.Full:
                                dead.add(q)
                        if dead:
                            queues -= dead

                # Fire callbacks (scanner tick handlers)
                with self._callback_lock:
                    cbs = list(self._tick_callbacks.get(code, set()))
                for cb in cbs:
                    try:
                        cb(code, float(ltp), tick)
                    except Exception:
                        pass
        except Exception:
            pass

    def _to_ws_format(self, code: str) -> str:
        """Translate REST-format scrip code → IndStocks WebSocket format.

        Equities:  NSE_3045    → NSE:3045
        Indices:   NSE_40000001 → NIDX:40000001  (IDs ≥ 40000000)
                   BSE_40000006 → BIDX:40000006
        F&O:       NFO_12345   → NFO:12345

        IndStocks reserves IDs ≥ 40000000 for indices and uses a different
        WS segment prefix (NIDX / BIDX) — see api-docs.indstocks.com/Websockets.
        """
        if ':' in code:
            return code
        if '_' not in code:
            return code
        seg, sid = code.split('_', 1)
        seg = seg.upper()
        is_index = sid.isdigit() and int(sid) >= 40000000
        if seg == 'NSE':
            return f"{'NIDX' if is_index else 'NSE'}:{sid}"
        if seg == 'BSE':
            return f"{'BIDX' if is_index else 'BSE'}:{sid}"
        return f'{seg}:{sid}'

    def _ws_subscribe(self, codes: list[str]) -> None:
        with self._ws_lock:
            ws = self._ws_instance
        if not ws:
            return
        ws_codes = [self._to_ws_format(c) for c in codes]
        try:
            # Official shape (api-docs.indstocks.com/Websockets/): action +
            # mode + instruments. Previously sent 'scrip_codes' with no
            # 'mode' — wrong key, so the server had nothing valid to act on.
            ws.send(json.dumps({'action': 'subscribe', 'mode': 'ltp', 'instruments': ws_codes}))
            with self._sub_lock:
                for c in codes:
                    self._subscribers.setdefault(c, set())
        except Exception as e:
            logger.warning(f'WS subscribe failed: {e}')

    def _ws_unsubscribe(self, codes: list[str]) -> None:
        with self._ws_lock:
            ws = self._ws_instance
        if not ws:
            return
        ws_codes = [self._to_ws_format(c) for c in codes]
        try:
            ws.send(json.dumps({'action': 'unsubscribe', 'mode': 'ltp', 'instruments': ws_codes}))
            with self._sub_lock:
                for c in codes:
                    self._subscribers.pop(c, None)
        except Exception:
            pass

    # ── Instrument search ─────────────────────────────────────────────────

    def search_instruments(
        self, query: str, exchange: str = '', instrument_type: str = '',
    ) -> list[InstrumentInfo]:
        if self._stub:
            return []
        q = query.upper()
        source = 'fno' if instrument_type.upper() in ('CE', 'PE', 'FUT', 'FUTIDX', 'OPTSTK', 'OPTIDX') else 'equity'
        instruments = self._load_instruments(source)
        results = []
        for inst in instruments:
            sym = (inst.get('TRADING_SYMBOL') or inst.get('tradingsymbol') or '').strip().upper()
            if q not in sym and q not in (inst.get('CUSTOM_SYMBOL') or '').upper():
                continue
            results.append(InstrumentInfo(
                symbol=sym,
                security_id=(inst.get('SECURITY_ID') or '').strip(),
                exchange=(inst.get('EXCH') or '').strip(),
                instrument_type=(inst.get('INSTRUMENT_TYPE') or inst.get('OPTION_TYPE') or '').strip(),
                lot_size=int(float(inst.get('LOT_UNITS') or inst.get('LOT_SIZE') or 1)),
                tick_size=float(inst.get('TICK_SIZE') or 0.05),
                expiry=(inst.get('EXPIRY_DATE') or '').strip(),
                strike=float(inst.get('STRIKE_PRICE') or 0),
                underlying=(inst.get('SYMBOL_NAME') or '').strip(),
                name=(inst.get('CUSTOM_SYMBOL') or sym).strip(),
            ))
            if len(results) >= 50:
                break
        return results

    def get_instrument(
        self, symbol: str, exchange: str = 'NFO',
    ) -> InstrumentInfo | None:
        if self._stub:
            return None
        inst = self._resolve_fo_instrument(symbol)
        if not inst:
            return None
        return InstrumentInfo(
            symbol=(inst.get('TRADING_SYMBOL') or '').strip(),
            security_id=(inst.get('SECURITY_ID') or '').strip(),
            exchange=(inst.get('EXCH') or '').strip(),
            instrument_type=(inst.get('OPTION_TYPE') or inst.get('INSTRUMENT_TYPE') or '').strip(),
            lot_size=int(float(inst.get('LOT_UNITS') or inst.get('LOT_SIZE') or 1)),
            tick_size=float(inst.get('TICK_SIZE') or 0.05),
            expiry=(inst.get('EXPIRY_DATE') or '').strip(),
            strike=float(inst.get('STRIKE_PRICE') or 0),
            underlying=(inst.get('SYMBOL_NAME') or '').strip(),
            name=(inst.get('CUSTOM_SYMBOL') or inst.get('TRADING_SYMBOL') or '').strip(),
        )
