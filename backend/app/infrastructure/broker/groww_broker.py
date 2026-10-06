"""
Groww Broker Adapter.

REST base: https://api.groww.in/v1
Instrument master (CSV): https://growwapi-assets.groww.in/instruments/instrument.csv
Docs: https://groww.in/trade-api/docs/curl

Auth: API key + secret → checksum (SHA256(api_secret + epoch_seconds)) →
POST /token/api/access → Bearer access token (expires daily ~6AM IST, so
refreshed lazily on 401 rather than on a fixed timer). Static ACCESS_TOKEN
env var is also supported for parity with how Dhan is run today (manual
daily token rotation), bypassing the checksum flow entirely.

NOTE: order_type / product literal values (MARKET/LIMIT/SL/SL_M,
MIS/CNC/NRML) and a few response field names below are taken from the
public docs, not exercised against a live account yet — verify in paper
mode before flipping active_broker to groww.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time as _time
from typing import Any

import requests as _requests

from ...shared.logger import get_logger
from ...shared.parse_expiry import parse_expiry
from ...shared.time import now_ist, today_ist
from .base import (
    BrokerAdapter,
    CandleData,
    HoldingInfo,
    InstrumentInfo,
    OrderResult,
    PositionInfo,
    QuoteResult,
)

logger = get_logger('groww_broker')

_BASE_URL = 'https://api.groww.in/v1'
_INSTRUMENTS_CSV_URL = 'https://growwapi-assets.groww.in/instruments/instrument.csv'

_ORDER_TYPE_MAP = {'MARKET': 'MARKET', 'LIMIT': 'LIMIT', 'SL': 'SL', 'SL-M': 'SL_M', 'SL_M': 'SL_M'}


class GrowwBroker(BrokerAdapter):
    """Groww broker — REST implementation (no WebSocket tick feed yet)."""

    def __init__(
        self, api_key: str = '', api_secret: str = '',
        access_token: str = '', product_type: str = 'MIS',
        **kwargs: Any,
    ) -> None:
        self._api_key = api_key or os.environ.get('GROWW_API_KEY', '')
        self._api_secret = api_secret or os.environ.get('GROWW_API_SECRET', '')
        self._static_token = access_token or os.environ.get('GROWW_ACCESS_TOKEN', '')
        self._default_product = product_type

        self._token = self._static_token
        self._token_lock = threading.Lock()

        self._stub = not bool(self._static_token or (self._api_key and self._api_secret))
        if self._stub:
            logger.warning('Groww credentials empty — running in stub mode')

        self._inst_master: list[dict] = []
        self._inst_lock = threading.Lock()
        self._fno_sym_index: dict[str, list[dict]] = {}

        self._order_segment: dict[str, str] = {}
        self._cash_cache: tuple[float, float] | None = None
        self._cash_lock = threading.Lock()

    @property
    def broker_name(self) -> str:
        return 'groww'

    # ── Auth ─────────────────────────────────────────────────────────────

    def _generate_token(self) -> str:
        """Checksum flow: SHA256(api_secret + epoch_seconds) → access token."""
        timestamp = str(int(_time.time()))
        checksum = hashlib.sha256(f'{self._api_secret}{timestamp}'.encode()).hexdigest()
        try:
            r = _requests.post(
                f'{_BASE_URL}/token/api/access',
                headers={'Authorization': f'Bearer {self._api_key}',
                         'Content-Type': 'application/json'},
                json={'key_type': 'approval', 'checksum': checksum, 'timestamp': timestamp},
                timeout=10,
            )
            if r.ok:
                token = (r.json() or {}).get('token') or (r.json() or {}).get('access_token', '')
                if token:
                    return token
            logger.error('Groww token generation failed: %s %s', r.status_code, r.text[:200])
        except Exception as e:
            logger.error('Groww token generation error: %s', e)
        return ''

    def _ensure_token(self) -> str:
        with self._token_lock:
            if self._token:
                return self._token
            if self._static_token:
                self._token = self._static_token
                return self._token
            self._token = self._generate_token()
            return self._token

    def _headers(self) -> dict[str, str]:
        return {
            'Authorization': f'Bearer {self._ensure_token()}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'X-API-VERSION': '1.0',
        }

    def _request(self, method: str, path: str, **kw: Any) -> _requests.Response | None:
        """Issue a request, regenerating the token once on 401 (static
        token can't self-refresh, so only retries when using key+secret)."""
        try:
            r = _requests.request(method, f'{_BASE_URL}{path}', headers=self._headers(), **kw)
            if r.status_code == 401 and not self._static_token:
                with self._token_lock:
                    self._token = self._generate_token()
                r = _requests.request(method, f'{_BASE_URL}{path}', headers=self._headers(), **kw)
            return r
        except Exception as e:
            logger.warning('Groww request error [%s %s]: %s', method, path, e)
            return None

    # ── Symbol / exchange helpers ────────────────────────────────────────

    def _groww_exchange(self, exchange: str) -> str:
        return 'BSE' if exchange.upper() in ('BSE', 'BFO') else 'NSE'

    def _segment(self, exchange: str) -> str:
        return 'FNO' if exchange.upper() in ('NFO', 'BFO') else 'CASH'

    # ── Core: place_order ────────────────────────────────────────────────

    def place_order(
        self, symbol: str, side: str, qty: int,
        order_type: str = 'MARKET', price: float = 0.0,
        exchange: str = 'NFO', security_id: str = '',
        trigger_price: float = 0.0, product_type: str = '',
        tag: str = '',
    ) -> OrderResult:
        if self._stub:
            return OrderResult(success=False, message='Groww stub — no credentials')

        segment = self._segment(exchange)
        payload: dict[str, Any] = {
            'trading_symbol': symbol,
            'quantity': qty,
            'price': price,
            'trigger_price': trigger_price,
            'validity': 'DAY',
            'exchange': self._groww_exchange(exchange),
            'segment': segment,
            'product': (product_type or self._default_product).upper(),
            'order_type': _ORDER_TYPE_MAP.get(order_type.upper(), order_type.upper()),
            'transaction_type': side.upper(),
        }
        if tag:
            payload['order_reference_id'] = tag

        r = self._request('POST', '/order/create', json=payload, timeout=10)
        if r is None:
            return OrderResult(success=False, message='request failed')
        try:
            resp = r.json()
        except Exception:
            resp = {}
        if r.ok and resp.get('status') == 'SUCCESS':
            order_id = str(resp.get('groww_order_id', ''))
            if order_id:
                self._order_segment[order_id] = segment
            self._invalidate_cash_cache()
            return OrderResult(
                success=True, order_id=order_id,
                status=str(resp.get('order_status', '')), raw=resp,
            )
        return OrderResult(success=False, message=resp.get('remark') or str(resp), raw=resp)

    # ── Orders (optional) ────────────────────────────────────────────────

    def modify_order(
        self, order_id: str, qty: int = 0, price: float = 0.0,
        order_type: str = '', trigger_price: float = 0.0,
    ) -> OrderResult:
        if self._stub:
            return OrderResult(success=False, message='Groww stub — no credentials')
        payload: dict[str, Any] = {
            'groww_order_id': order_id,
            'segment': self._order_segment.get(order_id, 'FNO'),
        }
        if qty:
            payload['quantity'] = qty
        if price:
            payload['price'] = price
        if trigger_price:
            payload['trigger_price'] = trigger_price
        if order_type:
            payload['order_type'] = _ORDER_TYPE_MAP.get(order_type.upper(), order_type.upper())

        r = self._request('POST', '/order/modify', json=payload, timeout=10)
        if r is None:
            return OrderResult(success=False, message='request failed')
        resp = r.json() if r.ok else {}
        success = r.ok and resp.get('status') == 'SUCCESS'
        return OrderResult(success=success, order_id=order_id,
                            status=str(resp.get('order_status', '')), raw=resp)

    def cancel_order(self, order_id: str) -> bool:
        if self._stub:
            return False
        payload = {'groww_order_id': order_id, 'segment': self._order_segment.get(order_id, 'FNO')}
        r = self._request('POST', '/order/cancel', json=payload, timeout=10)
        return bool(r and r.ok)

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        if self._stub:
            return {}
        segment = self._order_segment.get(order_id, 'FNO')
        r = self._request('GET', f'/order/status/{order_id}', params={'segment': segment}, timeout=5)
        if r and r.ok:
            return r.json() or {}
        return {}

    def get_order_list(self) -> list[dict[str, Any]]:
        if self._stub:
            return []
        out: list[dict[str, Any]] = []
        for segment in ('FNO', 'CASH'):
            r = self._request('GET', '/order/list', params={'segment': segment, 'page': 0, 'page_size': 50}, timeout=5)
            if r and r.ok:
                data = r.json() or {}
                out.extend(data.get('order_list') or [])
        return out

    # ── Core: get_ltp ────────────────────────────────────────────────────

    def get_ltp(self, symbol: str, exchange: str = 'NFO',
                security_id: str = '') -> float | None:
        if self._stub:
            return None
        segment = self._segment(exchange)
        exch_symbol = f'{self._groww_exchange(exchange)}_{symbol}'
        r = self._request('GET', '/live-data/ltp',
                           params={'segment': segment, 'exchange_symbols': exch_symbol}, timeout=5)
        if not r or not r.ok:
            return None
        try:
            data = r.json() or {}
            val = data.get(exch_symbol) or (data.get('ltp') if len(data) <= 2 else None)
            return float(val) if val is not None else None
        except Exception:
            return None

    # ── Core: get_quote ──────────────────────────────────────────────────

    def get_quote(self, symbol: str, exchange: str = 'NFO',
                  security_id: str = '') -> QuoteResult | None:
        if self._stub:
            return None
        r = self._request('GET', '/live-data/quote', params={
            'exchange': self._groww_exchange(exchange),
            'segment': self._segment(exchange),
            'trading_symbol': symbol,
        }, timeout=5)
        if not r or not r.ok:
            return None
        try:
            d = r.json() or {}
            ohlc = d.get('ohlc') or {}
            return QuoteResult(
                symbol=symbol,
                ltp=float(d.get('last_price') or 0),
                bid=float(d.get('bid_price') or 0),
                ask=float(d.get('offer_price') or 0),
                volume=int(float(d.get('volume') or 0)),
                open=float(ohlc.get('open') or 0),
                high=float(ohlc.get('high') or 0),
                low=float(ohlc.get('low') or 0),
                close=float(ohlc.get('close') or 0),
            )
        except Exception as e:
            logger.warning('Groww quote error %s: %s', symbol, e)
            return None

    # ── Core: get_positions ──────────────────────────────────────────────

    def get_positions(self) -> list[PositionInfo]:
        if self._stub:
            return []
        r = self._request('GET', '/positions/user', timeout=5)
        if not r or not r.ok:
            return []
        try:
            positions = ((r.json() or {}).get('payload') or {}).get('positions') or []
            return [
                PositionInfo(
                    symbol=p.get('trading_symbol', ''),
                    qty=int(p.get('quantity') or 0),
                    avg_price=float(p.get('net_price') or 0),
                    ltp=0.0,
                    pnl=float(p.get('realised_pnl') or 0),
                    realized_pnl=float(p.get('realised_pnl') or 0),
                    exchange=p.get('exchange', ''),
                    product_type=p.get('product', ''),
                )
                for p in positions
            ]
        except Exception as e:
            logger.warning('Groww positions error: %s', e)
            return []

    # ── Core: get_available_cash ─────────────────────────────────────────

    def get_available_cash(self) -> float:
        if self._stub:
            return 0.0
        now = _time.time()
        with self._cash_lock:
            if self._cash_cache and now - self._cash_cache[0] < 60:
                return self._cash_cache[1]
        r = self._request('GET', '/margins/detail/user', timeout=5)
        if not r or not r.ok:
            return 0.0
        try:
            cash = float((r.json() or {}).get('clear_cash') or 0)
            with self._cash_lock:
                self._cash_cache = (now, cash)
            return cash
        except Exception as e:
            logger.warning('Groww margin error: %s', e)
            return 0.0

    def _invalidate_cash_cache(self) -> None:
        with self._cash_lock:
            self._cash_cache = None

    # ── Holdings ──────────────────────────────────────────────────────────

    def get_holdings(self) -> list[HoldingInfo]:
        if self._stub:
            return []
        r = self._request('GET', '/holdings/user', timeout=5)
        if not r or not r.ok:
            return []
        try:
            holdings = ((r.json() or {}).get('payload') or {}).get('holdings') or []
            return [
                HoldingInfo(
                    symbol=h.get('trading_symbol', ''),
                    qty=int(h.get('quantity') or 0),
                    avg_price=float(h.get('average_price') or 0),
                    security_id=h.get('isin', ''),
                )
                for h in holdings
            ]
        except Exception as e:
            logger.warning('Groww holdings error: %s', e)
            return []

    # ── Candles (deprecated endpoint per Groww docs — works today) ────────

    def get_candles(
        self, symbol: str, interval: str = '5m', days: int = 5,
        exchange: str = 'NFO', security_id: str = '',
    ) -> list[CandleData]:
        if self._stub:
            return []
        interval_min = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '1d': 1440}.get(interval, 5)
        to_dt = now_ist()
        from_dt = to_dt - __import__('datetime').timedelta(days=days)
        r = self._request('GET', '/historical/candle/range', params={
            'exchange': self._groww_exchange(exchange),
            'segment': self._segment(exchange),
            'trading_symbol': symbol,
            'start_time': from_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'end_time': to_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'interval_in_minutes': interval_min,
        }, timeout=15)
        if not r or not r.ok:
            return []
        try:
            rows = ((r.json() or {}).get('payload') or {}).get('candles') or []
            candles = []
            for c in rows:
                if len(c) < 6:
                    continue
                candles.append(CandleData(
                    date=str(c[0]), open=float(c[1]), high=float(c[2]),
                    low=float(c[3]), close=float(c[4]), volume=int(c[5]),
                ))
            candles.sort(key=lambda x: x.date)
            return candles
        except Exception as e:
            logger.warning('Groww candles error %s: %s', symbol, e)
            return []

    # ── Instrument master ────────────────────────────────────────────────

    def load_instruments(self, source: str = 'fno') -> list:
        with self._inst_lock:
            if self._inst_master:
                return self._inst_master
        try:
            import csv
            import io
            r = _requests.get(_INSTRUMENTS_CSV_URL, timeout=20)
            if not r.ok:
                return []
            rows = list(csv.DictReader(io.StringIO(r.text)))
            with self._inst_lock:
                self._inst_master = rows
            logger.info('Loaded %d Groww instruments', len(rows))
            return rows
        except Exception as e:
            logger.warning('Groww instrument master load error: %s', e)
            return []

    def _build_fno_index(self) -> None:
        if self._fno_sym_index:
            return
        for inst in self.load_instruments():
            if (inst.get('segment') or '').upper() != 'FNO':
                continue
            underlying = (inst.get('underlying_symbol') or '').strip().upper()
            if underlying:
                self._fno_sym_index.setdefault(underlying, []).append(inst)

    def resolve_option_contract(
        self, underlying: str, option_type: str, strike: float,
    ) -> dict | None:
        self._build_fno_index()
        today = today_ist()
        candidates = []
        for inst in self._fno_sym_index.get(underlying.upper(), []):
            if (inst.get('instrument_type') or '').strip().upper() != option_type.upper():
                continue
            exp = parse_expiry(inst.get('expiry_date') or '')
            if not exp or exp < today:
                continue
            try:
                inst_strike = float(inst.get('strike_price') or 0)
            except Exception:
                continue
            candidates.append((exp, abs(inst_strike - strike), inst, inst_strike))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (x[0], x[1]))
        exp, _, best, inst_strike = candidates[0]
        symbol = best.get('trading_symbol', '')
        return {
            'trading_symbol': symbol,
            'display_symbol': symbol,
            'security_id': best.get('groww_symbol', ''),
            'expiry_date': best.get('expiry_date', ''),
            'strike': inst_strike,
            'ltp': self.get_ltp(symbol, exchange='NFO'),
        }

    def underlying_lot_size(self, underlying: str) -> int | None:
        self._build_fno_index()
        for inst in self._fno_sym_index.get(underlying.upper(), []):
            try:
                lot = int(float(inst.get('lot_size') or 0))
            except Exception:
                continue
            if lot > 0:
                return lot
        return None

    def search_instruments(
        self, query: str, exchange: str = '', instrument_type: str = '',
    ) -> list[InstrumentInfo]:
        q = query.upper()
        results = []
        for inst in self.load_instruments():
            sym = (inst.get('trading_symbol') or '').strip().upper()
            if q not in sym:
                continue
            results.append(InstrumentInfo(
                symbol=sym,
                security_id=(inst.get('groww_symbol') or '').strip(),
                exchange=(inst.get('exchange') or '').strip(),
                instrument_type=(inst.get('instrument_type') or '').strip(),
                lot_size=int(float(inst.get('lot_size') or 1)),
                tick_size=float(inst.get('tick_size') or 0.05),
                expiry=(inst.get('expiry_date') or '').strip(),
                strike=float(inst.get('strike_price') or 0),
                underlying=(inst.get('underlying_symbol') or '').strip(),
                name=sym,
            ))
            if len(results) >= 50:
                break
        return results
