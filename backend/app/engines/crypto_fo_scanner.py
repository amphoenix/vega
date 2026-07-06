"""
Crypto Derivatives Scanner — Options (gamma scalp) + Perpetual Futures on Deribit.

Architecture inspired by tfrmma/gamma-scalper (MIT):
  - Short gamma: sell ATM straddles, delta-hedge via BTC-PERPETUAL
  - IV > RV edge (~6.5% mean premium, 72% win rate on 730-day BTC backtest)
  - Funding carry: perp funding is positive 76% of the time (~5.3% annualized)
  - Kill switches: RV spike, drawdown, adverse funding, margin util

Two sub-strategies in one scanner:
  1. OPTIONS — Sell ATM straddles/strangles on Deribit, delta-hedge with perpetuals
  2. PERPS   — Momentum + funding carry on BTC/ETH perpetual futures

Uses CCXT async adapter for Deribit connectivity (already wired in ccxt_adapter.py).
All config in config.py (crypto_fo_* and crypto_perp_* fields).
"""
from __future__ import annotations

import asyncio
import json
import math
import threading
import uuid

from ..config import settings
from ..shared.logger import get_logger
from ..shared.time import clock

logger = get_logger('crypto_fo_scanner')

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_SIGNALS = 100

# Annualization factor: crypto = 365 * 24 = 8760 hours/year
_ANN_FACTOR = 8760.0


# ══════════════════════════════════════════════════════════════════════════════
# Volatility Estimators
# ══════════════════════════════════════════════════════════════════════════════

def yang_zhang_rv(candles: list[dict], window: int = 24) -> float | None:
    """Yang-Zhang (2000) realized volatility estimator.

    5-8x more efficient than close-to-close on same bar count.
    Expects hourly candles with open/high/low/close keys.
    Returns annualized vol as a fraction (e.g. 0.65 = 65%).
    """
    if len(candles) < window + 1:
        return None

    n = window
    subset = candles[-(n + 1):]

    # Log returns
    log_oc = []  # open-to-close
    log_co = []  # close-to-open (overnight)
    log_rs = []  # Rogers-Satchell

    for i in range(1, len(subset)):
        c_prev = subset[i - 1]['close']
        o = subset[i]['open']
        h = subset[i]['high']
        l = subset[i]['low']
        c = subset[i]['close']

        if c_prev <= 0 or o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue

        log_co.append(math.log(o / c_prev))
        log_oc.append(math.log(c / o))
        # Rogers-Satchell: log(h/c)*log(h/o) + log(l/c)*log(l/o)
        log_rs.append(
            math.log(h / c) * math.log(h / o) + math.log(l / c) * math.log(l / o)
        )

    if len(log_co) < 2:
        return None

    n_obs = len(log_co)

    # Overnight variance
    mean_co = sum(log_co) / n_obs
    var_co = sum((x - mean_co) ** 2 for x in log_co) / (n_obs - 1)

    # Open-to-close variance
    mean_oc = sum(log_oc) / n_obs
    var_oc = sum((x - mean_oc) ** 2 for x in log_oc) / (n_obs - 1)

    # Rogers-Satchell variance
    var_rs = sum(log_rs) / n_obs

    # Yang-Zhang combination
    k = 0.34 / (1.34 + (n_obs + 1) / (n_obs - 1))
    var_yz = var_co + k * var_oc + (1 - k) * var_rs

    if var_yz <= 0:
        return None

    # Annualize (hourly bars → annual)
    hourly_vol = math.sqrt(var_yz)
    annual_vol = hourly_vol * math.sqrt(_ANN_FACTOR)
    return annual_vol


def close_to_close_rv(closes: list[float], window: int = 24) -> float | None:
    """Simple close-to-close realized volatility (fallback when OHLC unavailable)."""
    if len(closes) < window + 1:
        return None
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(len(closes) - window, len(closes))
        if closes[i - 1] > 0
    ]
    if len(log_returns) < 2:
        return None
    mean_r = sum(log_returns) / len(log_returns)
    var = sum((r - mean_r) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(var) * math.sqrt(_ANN_FACTOR)


# ══════════════════════════════════════════════════════════════════════════════
# Deribit Client Wrapper (sync, uses CCXT under the hood)
# ══════════════════════════════════════════════════════════════════════════════

class DeribitClient:
    """CCXT Deribit wrapper — REST for orders/data, WebSocket for live prices.

    Uses ccxt (sync REST) for most operations and ccxt.pro (async WS) for
    real-time ticker streaming.  The WS loop runs in a dedicated thread so
    the scan loop always sees fresh prices without extra REST calls.
    """

    def __init__(self) -> None:
        self._exchange = None
        self._ws_exchange = None          # ccxt.pro async exchange
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._ws_running = False
        self._connected = False
        self._markets: dict = {}
        # Live ticker cache fed by WS
        self._live_tickers: dict[str, dict] = {}

    def connect(self) -> bool:
        """Initialize CCXT Deribit exchange and load markets."""
        try:
            import ccxt
            cfg = settings

            params = {
                'enableRateLimit': True,
                'timeout': 30000,
            }
            if cfg.deribit_client_id and cfg.deribit_client_secret:
                params['apiKey'] = cfg.deribit_client_id
                params['secret'] = cfg.deribit_client_secret

            self._exchange = ccxt.deribit(params)

            if cfg.deribit_testnet:
                self._exchange.set_sandbox_mode(True)
                logger.info('Deribit: using TESTNET')

            self._exchange.load_markets()
            self._markets = self._exchange.markets
            self._connected = True
            logger.info('Deribit connected: %d markets loaded (testnet=%s)',
                        len(self._markets), cfg.deribit_testnet)
            return True
        except Exception as e:
            logger.error('Deribit connect failed: %s', e)
            return False

    # ── WebSocket price streamer ──────────────────────────────────────────────

    def start_ws(self, symbols: list[str]) -> None:
        """Start WebSocket ticker stream for given symbols in a background thread."""
        if self._ws_running:
            return
        try:
            import ccxt.pro as ccxtpro
        except ImportError:
            logger.info('ccxt.pro not available — falling back to REST polling')
            return

        self._ws_running = True
        self._ws_thread = threading.Thread(
            target=self._ws_loop_runner, args=(symbols,),
            daemon=True, name='deribit-ws')
        self._ws_thread.start()
        logger.info('Deribit WS streamer started for %s', symbols)

    def _ws_loop_runner(self, symbols: list[str]) -> None:
        """Run the async WS event loop in a dedicated thread."""
        import asyncio as _aio
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        self._ws_loop = loop
        try:
            loop.run_until_complete(self._ws_stream(symbols))
        except Exception as e:
            logger.error('Deribit WS loop exited: %s', e)
        finally:
            self._ws_running = False

    async def _ws_stream(self, symbols: list[str]) -> None:
        """Subscribe to watch_ticker for each symbol, update live cache."""
        import ccxt.pro as ccxtpro

        cfg = settings
        params = {'enableRateLimit': True}
        if cfg.deribit_client_id and cfg.deribit_client_secret:
            params['apiKey'] = cfg.deribit_client_id
            params['secret'] = cfg.deribit_client_secret

        ws = ccxtpro.deribit(params)
        if cfg.deribit_testnet:
            ws.set_sandbox_mode(True)
        self._ws_exchange = ws

        try:
            while self._ws_running:
                for sym in symbols:
                    try:
                        ticker = await ws.watch_ticker(sym)
                        self._live_tickers[sym] = {
                            'last': float(ticker.get('last', 0)),
                            'bid': float(ticker.get('bid', 0)),
                            'ask': float(ticker.get('ask', 0)),
                            'ts': int(clock() * 1000),
                        }
                    except Exception as e:
                        logger.debug('WS watch_ticker(%s): %s', sym, e)
        finally:
            await ws.close()

    def stop_ws(self) -> None:
        """Stop WebSocket streamer."""
        self._ws_running = False
        if self._ws_loop:
            self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)

    def get_live_price(self, symbol: str) -> float | None:
        """Get latest price from WS cache, or None if unavailable."""
        t = self._live_tickers.get(symbol)
        if t and clock() * 1000 - t.get('ts', 0) < 30_000:  # stale after 30s
            return t['last']
        return None

    @property
    def is_connected(self) -> bool:
        return self._connected

    def fetch_ticker(self, symbol: str) -> dict:
        if not self._exchange:
            return {}
        try:
            return self._exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error('fetch_ticker(%s): %s', symbol, e)
            return {}

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1h',
                    limit: int = 100) -> list[dict]:
        """Fetch OHLCV candles, returned as list of dicts."""
        if not self._exchange:
            return []
        try:
            raw = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            return [
                {'ts': c[0], 'open': c[1], 'high': c[2],
                 'low': c[3], 'close': c[4], 'volume': c[5]}
                for c in raw
            ]
        except Exception as e:
            logger.error('fetch_ohlcv(%s): %s', symbol, e)
            return []

    def fetch_funding_rate(self, symbol: str) -> dict:
        if not self._exchange:
            return {}
        try:
            return self._exchange.fetch_funding_rate(symbol)
        except Exception as e:
            logger.debug('fetch_funding_rate(%s): %s', symbol, e)
            return {}

    def fetch_option_markets(self, base: str = 'BTC') -> list[dict]:
        """Return all option markets for a given base asset."""
        return [
            m for m in self._markets.values()
            if m.get('type') == 'option'
            and base.upper() in str(m.get('base', ''))
        ]

    def fetch_greeks(self, symbol: str) -> dict:
        """Fetch greeks + IV from Deribit for an option symbol."""
        if not self._exchange:
            return {}
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            info = ticker.get('info', {})
            return {
                'delta': float(info.get('greeks', {}).get('delta', 0) or info.get('delta', 0)),
                'gamma': float(info.get('greeks', {}).get('gamma', 0) or info.get('gamma', 0)),
                'theta': float(info.get('greeks', {}).get('theta', 0) or info.get('theta', 0)),
                'vega': float(info.get('greeks', {}).get('vega', 0) or info.get('vega', 0)),
                'iv': float(info.get('mark_iv', 0) or info.get('iv', 0)) / 100.0,
                'mark_price': float(ticker.get('last', 0)),
                'bid': float(ticker.get('bid', 0)),
                'ask': float(ticker.get('ask', 0)),
            }
        except Exception as e:
            logger.error('fetch_greeks(%s): %s', symbol, e)
            return {}

    def get_balance(self) -> dict:
        """Fetch account balance (requires API keys)."""
        if not self._exchange:
            return {}
        try:
            bal = self._exchange.fetch_balance()
            return {
                'BTC': float(bal.get('total', {}).get('BTC', 0)),
                'ETH': float(bal.get('total', {}).get('ETH', 0)),
                'USDC': float(bal.get('total', {}).get('USDC', 0)),
                'equity': float(bal.get('info', {}).get('equity', 0)),
            }
        except Exception as e:
            logger.error('get_balance: %s', e)
            return {}

    def place_order(self, symbol: str, side: str, amount: float,
                    order_type: str = 'market', price: float = 0,
                    params: dict = None) -> dict:
        """Place order on Deribit."""
        if not self._exchange:
            return {'error': 'not connected'}
        try:
            return self._exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side.lower(),
                amount=amount,
                price=price if order_type == 'limit' else None,
                params=params or {},
            )
        except Exception as e:
            logger.error('place_order(%s %s %.4f): %s', side, symbol, amount, e)
            return {'error': str(e)}

    def get_positions(self) -> list[dict]:
        """Fetch all open positions."""
        if not self._exchange:
            return []
        try:
            return self._exchange.fetch_positions()
        except Exception as e:
            logger.error('get_positions: %s', e)
            return []


# ══════════════════════════════════════════════════════════════════════════════
# Option Selector — find best ATM straddle/strangle for gamma scalp
# ══════════════════════════════════════════════════════════════════════════════

def select_options(client: DeribitClient, asset: str, spot: float,
                   cfg=None) -> dict | None:
    """Select ATM straddle for gamma scalp.

    Returns dict with call/put symbols, strike, DTE, greeks, or None.
    """
    if cfg is None:
        cfg = settings

    target_dte = cfg.crypto_fo_target_dte
    min_dte = cfg.crypto_fo_min_dte
    max_dte = cfg.crypto_fo_max_dte

    options = client.fetch_option_markets(asset)
    if not options:
        logger.warning('No %s options found on Deribit', asset)
        return None

    # Filter by DTE
    import datetime
    now = datetime.datetime.utcnow()
    candidates = []
    for m in options:
        expiry_str = m.get('expiry')
        if not expiry_str:
            continue
        try:
            exp = datetime.datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
            dte = (exp - now.replace(tzinfo=exp.tzinfo)).total_seconds() / 86400
        except Exception:
            continue
        if min_dte <= dte <= max_dte:
            candidates.append({**m, '_dte': dte})

    if not candidates:
        logger.info('No %s options in DTE range [%d, %d]', asset, min_dte, max_dte)
        return None

    # Find closest to target DTE
    by_dte = sorted(candidates, key=lambda x: abs(x['_dte'] - target_dte))

    # Group by expiry, pick best DTE group
    best_expiry = by_dte[0].get('expiry')
    expiry_group = [c for c in candidates if c.get('expiry') == best_expiry]

    # Find ATM strike (closest to spot)
    strikes = set()
    for c in expiry_group:
        strike = c.get('strike')
        if strike:
            strikes.add(float(strike))

    if not strikes:
        return None

    atm_strike = min(strikes, key=lambda s: abs(s - spot))

    # Find call + put at ATM strike
    call_sym = None
    put_sym = None
    for c in expiry_group:
        if float(c.get('strike', 0)) == atm_strike:
            info = c.get('info', {})
            opt_type = c.get('optionType', '') or info.get('option_type', '')
            if opt_type == 'call':
                call_sym = c['symbol']
            elif opt_type == 'put':
                put_sym = c['symbol']

    if not call_sym or not put_sym:
        logger.info('Could not find ATM C+P at strike %.0f for %s', atm_strike, asset)
        return None

    dte = by_dte[0]['_dte']
    logger.info('Selected %s ATM straddle: strike=%.0f  DTE=%.1f  C=%s  P=%s',
                asset, atm_strike, dte, call_sym, put_sym)

    return {
        'asset': asset,
        'strike': atm_strike,
        'dte': round(dte, 1),
        'expiry': best_expiry,
        'call_symbol': call_sym,
        'put_symbol': put_sym,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Funding Regime Classifier
# ══════════════════════════════════════════════════════════════════════════════

def classify_funding_regime(funding_rate: float) -> tuple[str, float]:
    """Classify funding regime and return (regime, size_multiplier).

    Based on gamma-scalper research:
    - Bull (>5% ann): full size, funding income
    - Neutral (0-5% ann): reduced size
    - Bear (<0% ann): minimal size, funding is a cost
    """
    cfg = settings
    # Annualize: 8h funding rate → annual
    ann_rate = funding_rate * 3 * 365  # 3 funding periods/day * 365 days

    if ann_rate > 0.05:
        return 'bull', cfg.crypto_fo_funding_bull_mult
    elif ann_rate > 0:
        return 'neutral', cfg.crypto_fo_funding_neutral_mult
    else:
        return 'bear', cfg.crypto_fo_funding_bear_mult


# ══════════════════════════════════════════════════════════════════════════════
# Kill Switch Engine
# ══════════════════════════════════════════════════════════════════════════════

class KillSwitch:
    """Risk engine with multiple kill-switch triggers."""

    def __init__(self) -> None:
        self._halted = False
        self._halt_reason = ''
        self._session_pnl = 0.0
        self._pnl_24h = 0.0
        self._pnl_history: list[tuple[float, float]] = []  # (timestamp, pnl_delta)

    @property
    def is_halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def record_pnl(self, delta: float) -> None:
        """Record a P&L change for velocity tracking."""
        now = clock()
        self._session_pnl += delta
        self._pnl_24h += delta
        self._pnl_history.append((now, delta))
        # Trim to last 24h
        cutoff = now - 86400
        self._pnl_history = [(t, p) for t, p in self._pnl_history if t > cutoff]

    def check(self, rv_1h: float = 0, rv_24h: float = 0,
              margin_util: float = 0) -> bool:
        """Check all kill switch conditions. Returns True if halted."""
        cfg = settings

        # 1. Daily loss limit
        if self._session_pnl < -cfg.crypto_fo_daily_loss_limit_usd:
            self._halt('daily_loss', f'Session P&L ${self._session_pnl:.0f} < -${cfg.crypto_fo_daily_loss_limit_usd:.0f}')
            return True

        # 2. Intraday drawdown
        if self._session_pnl < -cfg.crypto_fo_max_drawdown_usd:
            self._halt('drawdown', f'Drawdown ${self._session_pnl:.0f}')
            return True

        # 3. 24h drawdown
        if self._pnl_24h < -cfg.crypto_fo_max_drawdown_24h_usd:
            self._halt('drawdown_24h', f'24h drawdown ${self._pnl_24h:.0f}')
            return True

        # 4. RV spike (1h vol / 24h vol > threshold)
        if rv_24h > 0 and rv_1h / rv_24h > cfg.crypto_fo_rv_spike_halt:
            self._halt('rv_spike', f'RV ratio {rv_1h/rv_24h:.1f}x > {cfg.crypto_fo_rv_spike_halt}x')
            return True

        # 5. Margin utilization
        if margin_util > cfg.crypto_fo_margin_util_halt:
            self._halt('margin', f'Margin util {margin_util:.0%} > {cfg.crypto_fo_margin_util_halt:.0%}')
            return True

        # 6. Loss velocity: > $500/h
        now = clock()
        h1_pnl = sum(p for t, p in self._pnl_history if t > now - 3600)
        if h1_pnl < -500:
            self._halt('loss_velocity', f'1h loss ${h1_pnl:.0f}')
            return True

        return False

    def _halt(self, reason: str, detail: str) -> None:
        self._halted = True
        self._halt_reason = f'{reason}: {detail}'
        logger.warning('KILL SWITCH: %s', self._halt_reason)

    def reset(self) -> None:
        self._halted = False
        self._halt_reason = ''

    def reset_daily(self) -> None:
        self._session_pnl = 0.0
        self._halted = False
        self._halt_reason = ''


# ══════════════════════════════════════════════════════════════════════════════
# CryptoFOScanner — main scanner class
# ══════════════════════════════════════════════════════════════════════════════

class CryptoFOScanner:
    """Crypto derivatives scanner: options (gamma scalp) + perpetual futures.

    Lifecycle:
        scanner = CryptoFOScanner()
        scanner.start()  # spawns background thread
        ...
        scanner.stop()
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._cycle_count = 0

        # Deribit client
        self._client = DeribitClient()

        # Risk engine
        self._kill_switch = KillSwitch()

        # Options positions: { asset: { call_symbol, put_symbol, strike, ... } }
        self._option_positions: dict[str, dict] = {}

        # Perp positions: { asset: { symbol, side, entry, qty, ... } }
        self._perp_positions: dict[str, dict] = {}

        # P&L tracking
        self._realized_pnl = 0.0
        self._unrealized_pnl = 0.0

        # Signals log
        self._signals: list[dict] = []

        # Market state cache
        self._spot_prices: dict[str, float] = {}
        self._rv_cache: dict[str, dict] = {}  # { asset: { rv_1h, rv_24h } }
        self._iv_cache: dict[str, float] = {}
        self._funding_cache: dict[str, dict] = {}

        # Restore P&L from DB
        self._restore_state()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _restore_state(self) -> None:
        """Restore positions and P&L from DB."""
        try:
            from ..infrastructure.db import state_store
            # Restore positions
            raw = state_store.get_state('crypto_fo_positions')
            if raw:
                data = json.loads(raw)
                self._option_positions = data.get('options', {})
                self._perp_positions = data.get('perps', {})
                if self._option_positions or self._perp_positions:
                    logger.info('Restored crypto F&O positions: %d options, %d perps',
                                len(self._option_positions), len(self._perp_positions))

            # Restore realized P&L from trades DB
            segment = state_store.daily_summary_by_segment().get('crypto_fo', {})
            self._realized_pnl = segment.get('net', 0.0)
            if self._realized_pnl:
                logger.info('Restored crypto F&O realized P&L: $%.2f', self._realized_pnl)
        except Exception as e:
            logger.warning('Failed to restore crypto F&O state: %s', e)

    def _save_positions(self) -> None:
        """Persist positions to DB."""
        try:
            from ..infrastructure.db import state_store
            data = json.dumps({
                'options': self._option_positions,
                'perps': self._perp_positions,
            })
            state_store.set_state('crypto_fo_positions', data)
        except Exception as e:
            logger.warning('Failed to save crypto F&O positions: %s', e)

    def _record_trade(self, symbol: str, side: str, qty: float,
                      entry: float, exit_price: float, pnl: float,
                      brokerage: float, sub_mode: str = 'crypto_fo') -> None:
        """Record a completed trade to the P&L DB."""
        try:
            from ..infrastructure.db import state_store
            state_store.record_trade(
                mode='crypto_fo',
                symbol=symbol,
                underlying=symbol.split('-')[0],
                entry_prem=entry,
                exit_prem=exit_price,
                qty=1,
                lot_size=1,
                brokerage=brokerage,
                exit_reason=sub_mode,
                market_type='crypto_fo',
                direction=side,
                currency='USD',
                gross_pnl_override=pnl,
            )
        except Exception as e:
            logger.error('Failed to record crypto F&O trade: %s', e)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        if not settings.crypto_fo_enabled:
            logger.info('Crypto F&O scanner disabled in config')
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='crypto-fo-scanner')
        self._thread.start()

        # Start WS price streamer for real-time tickers
        assets = [a.strip() for a in settings.crypto_fo_assets.split(',') if a.strip()]
        ws_symbols = [f'{a}/USD:{a}' for a in assets]
        self._client.start_ws(ws_symbols)

        logger.info('Crypto F&O scanner started')

    def stop(self) -> None:
        self._stop_event.set()
        self._running = False
        self._client.stop_ws()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info('Crypto F&O scanner stopped')

    def get_state(self) -> dict:
        """Return scanner state for API."""
        with self._lock:
            return {
                'running': self._running,
                'connected': self._client.is_connected,
                'ws_streaming': self._client._ws_running,
                'mode': settings.crypto_fo_mode,
                'testnet': settings.deribit_testnet,
                'cycle_count': self._cycle_count,
                'kill_switch': {
                    'halted': self._kill_switch.is_halted,
                    'reason': self._kill_switch.halt_reason,
                },
                'option_positions': len(self._option_positions),
                'perp_positions': len(self._perp_positions),
                'spot_prices': dict(self._spot_prices),
                'rv_cache': dict(self._rv_cache),
                'iv_cache': dict(self._iv_cache),
                'funding_cache': dict(self._funding_cache),
            }

    def get_pnl(self) -> dict:
        """Return P&L summary."""
        # Read authoritative realized from DB
        realized = self._realized_pnl
        try:
            from ..infrastructure.db import state_store
            segment = state_store.daily_summary_by_segment().get('crypto_fo', {})
            realized = segment.get('net', 0.0)
        except Exception:
            pass

        with self._lock:
            unrealized = self._unrealized_pnl

        return {
            'realized': round(realized, 2),
            'unrealized': round(unrealized, 2),
            'net': round(realized + unrealized, 2),
            'option_positions': len(self._option_positions),
            'perp_positions': len(self._perp_positions),
        }

    def get_positions(self) -> dict:
        """Return current positions."""
        with self._lock:
            return {
                'options': list(self._option_positions.values()),
                'perps': list(self._perp_positions.values()),
            }

    def get_signals(self) -> list[dict]:
        return list(self._signals[-_MAX_SIGNALS:])

    # ── Main Loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        """Background scan loop."""
        # Connect to Deribit
        if not self._client.connect():
            logger.error('Cannot start crypto F&O scanner — Deribit connection failed')
            self._running = False
            return

        logger.info('Crypto F&O scanner loop started — options + perps')

        while not self._stop_event.is_set():
            try:
                self._cycle_count += 1
                self._scan_cycle()
            except Exception as e:
                logger.error('Crypto F&O scan cycle error: %s', e, exc_info=True)

            # Sleep between cycles
            interval = settings.crypto_perp_scan_interval_sec
            self._stop_event.wait(timeout=interval)

    def _scan_cycle(self) -> None:
        """One full scan cycle: update market state, run options + perps strategies."""
        cfg = settings

        # 1. Update spot prices for all assets
        assets = [a.strip() for a in cfg.crypto_fo_assets.split(',') if a.strip()]
        for asset in assets:
            self._update_spot(asset)

        # 2. Update realized vol (hourly candles)
        for asset in assets:
            self._update_rv(asset)

        # 3. Check kill switches
        for asset in assets:
            rv = self._rv_cache.get(asset, {})
            if self._kill_switch.check(
                rv_1h=rv.get('rv_1h', 0),
                rv_24h=rv.get('rv_24h', 0),
            ):
                # Flatten everything
                self._flatten_all('kill_switch')
                return

        # 4. Options strategy (gamma scalp)
        if cfg.crypto_fo_auto_trade:
            for asset in assets:
                self._options_cycle(asset)

        # 5. Perps strategy (momentum + funding)
        if cfg.crypto_perp_enabled:
            perp_assets = [a.strip() for a in cfg.crypto_perp_assets.split(',') if a.strip()]
            for asset in perp_assets:
                self._perps_cycle(asset)

        # 6. Monitor existing positions (SL/TP/roll)
        self._monitor_options()
        self._monitor_perps()

        # 7. Update unrealized P&L
        self._update_unrealized()

    # ── Market Data ───────────────────────────────────────────────────────────

    def _update_spot(self, asset: str) -> None:
        """Get price from WS cache (sub-second), fall back to REST fetch."""
        symbol = f'{asset}/USD:{asset}'
        ws_price = self._client.get_live_price(symbol)
        if ws_price and ws_price > 0:
            self._spot_prices[asset] = ws_price
            return
        # Fallback: REST (slower, rate-limited)
        ticker = self._client.fetch_ticker(symbol)
        if ticker:
            self._spot_prices[asset] = float(ticker.get('last', 0))

    def _update_rv(self, asset: str) -> None:
        """Fetch hourly candles and compute RV at 1h and 24h windows."""
        symbol = f'{asset}/USD:{asset}'
        candles = self._client.fetch_ohlcv(symbol, '1h', limit=200)
        if not candles:
            return

        rv_24h = yang_zhang_rv(candles, window=24)
        rv_1h = yang_zhang_rv(candles, window=1) if len(candles) > 2 else None

        # Fallback to C2C if YZ fails
        if rv_24h is None:
            closes = [c['close'] for c in candles]
            rv_24h = close_to_close_rv(closes, 24)
        if rv_1h is None and len(candles) > 2:
            closes = [c['close'] for c in candles]
            rv_1h = close_to_close_rv(closes, 1)

        self._rv_cache[asset] = {
            'rv_1h': rv_1h or 0,
            'rv_24h': rv_24h or 0,
        }

        if self._cycle_count <= 1:
            logger.info('%s RV: 1h=%.1f%%  24h=%.1f%%',
                        asset, (rv_1h or 0) * 100, (rv_24h or 0) * 100)

    # ── Options Strategy (Gamma Scalp) ────────────────────────────────────────

    def _options_cycle(self, asset: str) -> None:
        """Evaluate whether to enter/exit options position for given asset."""
        cfg = settings
        spot = self._spot_prices.get(asset, 0)
        if spot <= 0:
            return

        # Already have a position? Skip entry, just monitor.
        if asset in self._option_positions:
            return

        # Check vol premium: IV - RV
        rv = self._rv_cache.get(asset, {}).get('rv_24h', 0)
        if rv <= 0:
            return

        # Need to fetch ATM IV
        selection = select_options(self._client, asset, spot, cfg)
        if not selection:
            return

        # Fetch greeks for the call to get IV
        greeks = self._client.fetch_greeks(selection['call_symbol'])
        iv = greeks.get('iv', 0)
        self._iv_cache[asset] = iv

        vol_premium = iv - rv

        # Funding regime for sizing
        funding_data = self._client.fetch_funding_rate(f'{asset}/USD:{asset}')
        funding_rate = float(funding_data.get('fundingRate', 0) or 0)
        self._funding_cache[asset] = {
            'rate': funding_rate,
            'ann': funding_rate * 3 * 365,
        }
        regime, size_mult = classify_funding_regime(funding_rate)

        signal = {
            'id': str(uuid.uuid4()),
            'ts': int(clock() * 1000),
            'asset': asset,
            'type': 'options',
            'spot': spot,
            'iv': round(iv * 100, 1),
            'rv': round(rv * 100, 1),
            'vol_premium': round(vol_premium * 100, 1),
            'funding_regime': regime,
            'size_mult': size_mult,
            'strike': selection['strike'],
            'dte': selection['dte'],
        }

        # Entry decision
        if vol_premium >= cfg.crypto_fo_vol_entry_threshold:
            signal['action'] = 'SELL_STRADDLE'
            signal['reason'] = f'IV-RV={vol_premium*100:.1f}% > {cfg.crypto_fo_vol_entry_threshold*100:.0f}% threshold'
            self._signals.append(signal)

            _master_paper = (cfg.trading_mode == 'paper') or (not cfg.live_trading_enabled)
            if _master_paper or cfg.crypto_fo_mode == 'paper':
                self._paper_open_straddle(asset, selection, spot, greeks, size_mult)
            else:
                self._live_open_straddle(asset, selection, spot, greeks, size_mult)
        else:
            signal['action'] = 'WAIT'
            signal['reason'] = f'IV-RV={vol_premium*100:.1f}% < {cfg.crypto_fo_vol_entry_threshold*100:.0f}%'
            self._signals.append(signal)

    def _paper_open_straddle(self, asset: str, selection: dict, spot: float,
                             greeks: dict, size_mult: float) -> None:
        """Paper-trade: open short straddle."""
        cfg = settings
        notional = cfg.crypto_fo_base_notional_usd * size_mult
        # BTC options are priced in BTC on Deribit
        qty_btc = notional / spot if spot > 0 else 0
        qty = round(qty_btc, 4)

        with self._lock:
            self._option_positions[asset] = {
                'asset': asset,
                'side': 'SHORT',
                'structure': 'straddle',
                'call_symbol': selection['call_symbol'],
                'put_symbol': selection['put_symbol'],
                'strike': selection['strike'],
                'dte': selection['dte'],
                'expiry': selection['expiry'],
                'entry_spot': spot,
                'entry_iv': greeks.get('iv', 0),
                'entry_delta': greeks.get('delta', 0),
                'qty': qty,
                'notional_usd': round(notional, 2),
                'entry_time': int(clock() * 1000),
                'pnl': 0.0,
                'mode': 'paper',
                'net_delta': 0.0,  # hedged
                'hedge_qty': 0.0,
            }
        self._save_positions()
        logger.info('OPEN %s short straddle: strike=%.0f qty=%.4f notional=$%.0f DTE=%.1f [paper]',
                     asset, selection['strike'], qty, notional, selection['dte'])

    def _live_open_straddle(self, asset: str, selection: dict, spot: float,
                            greeks: dict, size_mult: float) -> None:
        """Live: sell call + sell put on Deribit."""
        cfg = settings
        notional = cfg.crypto_fo_base_notional_usd * size_mult
        qty_btc = notional / spot if spot > 0 else 0
        qty = round(qty_btc, 4)

        # Sell call
        call_result = self._client.place_order(
            selection['call_symbol'], 'sell', qty)
        # Sell put
        put_result = self._client.place_order(
            selection['put_symbol'], 'sell', qty)

        if call_result.get('error') or put_result.get('error'):
            logger.error('Failed to open straddle: call=%s put=%s',
                         call_result, put_result)
            return

        with self._lock:
            self._option_positions[asset] = {
                'asset': asset,
                'side': 'SHORT',
                'structure': 'straddle',
                'call_symbol': selection['call_symbol'],
                'put_symbol': selection['put_symbol'],
                'strike': selection['strike'],
                'dte': selection['dte'],
                'expiry': selection['expiry'],
                'entry_spot': spot,
                'entry_iv': greeks.get('iv', 0),
                'qty': qty,
                'notional_usd': round(notional, 2),
                'entry_time': int(clock() * 1000),
                'pnl': 0.0,
                'mode': 'live',
                'call_order': call_result,
                'put_order': put_result,
                'net_delta': 0.0,
                'hedge_qty': 0.0,
            }
        self._save_positions()
        logger.info('OPEN %s short straddle: strike=%.0f qty=%.4f [LIVE]',
                     asset, selection['strike'], qty)

    # ── Delta Hedging ─────────────────────────────────────────────────────────

    def _hedge_delta(self, asset: str) -> None:
        """Delta-hedge an options position using perpetual futures."""
        pos = self._option_positions.get(asset)
        if not pos:
            return

        cfg = settings
        # Fetch current greeks for both legs
        call_greeks = self._client.fetch_greeks(pos['call_symbol'])
        put_greeks = self._client.fetch_greeks(pos['put_symbol'])

        call_delta = call_greeks.get('delta', 0)
        put_delta = put_greeks.get('delta', 0)

        # Short straddle: our delta is -(call_delta + put_delta) * qty
        # (we sold, so negate)
        option_delta = -(call_delta + put_delta) * pos['qty']
        hedge_delta = pos.get('hedge_qty', 0)
        net_delta = option_delta + hedge_delta

        pos['net_delta'] = round(net_delta, 6)

        # Check if hedge needed
        if abs(net_delta) < cfg.crypto_fo_delta_threshold:
            return

        # Hedge: buy/sell perp to neutralize delta
        hedge_needed = -net_delta  # positive = buy perp, negative = sell perp
        symbol = f'{asset}/USD:{asset}'

        _master_paper = (cfg.trading_mode == 'paper') or (not cfg.live_trading_enabled)
        if _master_paper or cfg.crypto_fo_mode == 'paper':
            logger.info('HEDGE %s delta: net=%.4f → trade %.4f perp [paper]',
                        asset, net_delta, hedge_needed)
            pos['hedge_qty'] = round(pos.get('hedge_qty', 0) + hedge_needed, 6)
            pos['net_delta'] = 0.0
        else:
            side = 'buy' if hedge_needed > 0 else 'sell'
            result = self._client.place_order(
                symbol, side, abs(hedge_needed))
            if not result.get('error'):
                pos['hedge_qty'] = round(pos.get('hedge_qty', 0) + hedge_needed, 6)
                pos['net_delta'] = 0.0
                logger.info('HEDGE %s: %s %.4f perp', asset, side, abs(hedge_needed))

        self._save_positions()

    # ── Options Monitoring ────────────────────────────────────────────────────

    def _monitor_options(self) -> None:
        """Check exit conditions for options: roll, emergency, expiry."""
        cfg = settings
        to_close = []

        for asset, pos in list(self._option_positions.items()):
            spot = self._spot_prices.get(asset, 0)
            rv = self._rv_cache.get(asset, {}).get('rv_24h', 0)
            iv = self._iv_cache.get(asset, 0)
            vol_premium = iv - rv if iv and rv else 0

            # 1. Emergency exit: IV - RV dropped below emergency threshold
            if vol_premium < cfg.crypto_fo_vol_emergency_exit:
                to_close.append((asset, f'emergency: IV-RV={vol_premium*100:.1f}%'))
                continue

            # 2. Roll: DTE below threshold
            if pos['dte'] <= cfg.crypto_fo_roll_dte:
                # Close and re-enter
                to_close.append((asset, f'roll: DTE={pos["dte"]:.1f}'))
                continue

            # 3. Exit threshold: IV - RV below exit threshold
            if vol_premium < cfg.crypto_fo_vol_exit_threshold:
                to_close.append((asset, f'exit: IV-RV={vol_premium*100:.1f}%'))
                continue

            # 4. Delta hedge
            self._hedge_delta(asset)

        for asset, reason in to_close:
            self._close_options(asset, reason)

    def _close_options(self, asset: str, reason: str) -> None:
        """Close options position for asset."""
        with self._lock:
            pos = self._option_positions.pop(asset, None)
        if not pos:
            return

        spot = self._spot_prices.get(asset, 0)
        pnl = pos.get('pnl', 0)

        from ..domain.services.brokerage_calc import segment_brokerage
        entry_notional = pos.get('notional_usd', 0)
        brokerage = segment_brokerage('crypto_fo', 0, 0, 0,
                                      gross_pnl=pnl, notional_usd=entry_notional,
                                      sub_type='options')
        net_pnl = round(pnl - brokerage, 2)

        self._realized_pnl += net_pnl
        self._kill_switch.record_pnl(net_pnl)

        self._record_trade(
            symbol=f'{asset}-STRADDLE',
            side='SHORT',
            qty=pos.get('qty', 0),
            entry=pos.get('entry_spot', 0),
            exit_price=spot,
            pnl=pnl,
            brokerage=brokerage,
            sub_mode='options',
        )

        self._save_positions()
        logger.info('CLOSE %s straddle: gross=$%.2f brokerage=$%.2f net=$%.2f reason=%s',
                     asset, pnl, brokerage, net_pnl, reason)

    # ── Perpetuals Strategy (Momentum + Funding) ──────────────────────────────

    def _perps_cycle(self, asset: str) -> None:
        """Evaluate perpetual futures entry for given asset."""
        cfg = settings
        spot = self._spot_prices.get(asset, 0)
        if spot <= 0:
            return

        # Already have a perp position for this asset?
        if asset in self._perp_positions:
            return

        # Max positions check
        if len(self._perp_positions) >= cfg.crypto_perp_max_positions:
            return

        # Fetch candles for momentum analysis
        symbol = f'{asset}/USD:{asset}'
        candles = self._client.fetch_ohlcv(symbol, '1h', limit=50)
        if len(candles) < 30:
            return

        closes = [c['close'] for c in candles]

        # Multi-indicator momentum (matching proven Freqtrade + gamma-scalper patterns)
        from ..shared.indicators import (
            CandleData,
            adx,
            atr_from_dicts,
            ema_series,
            macd,
            roc,
            rsi,
        )

        ema_fast = ema_series(closes, 9)
        ema_slow = ema_series(closes, 21)
        rsi_val = rsi(closes, 14)
        macd_data = macd(closes)
        roc_val = roc(closes, 6)  # 6-bar momentum

        candle_dicts = [{'high': c['high'], 'low': c['low'], 'close': c['close']} for c in candles]
        candle_objs = [CandleData.from_dict(cd) for cd in candle_dicts]
        atr_val = atr_from_dicts(candle_dicts, 14)
        adx_val = adx(candle_objs, 14)

        if not ema_fast or not ema_slow or rsi_val is None or not macd_data or not atr_val:
            return

        # ATR trend strength filter: skip if ATR is too low (choppy market)
        atr_pct = (atr_val / spot * 100) if spot > 0 else 0
        if atr_pct < 0.3:
            return  # < 0.3% ATR = too quiet, no edge

        # Scoring (max 100)
        confidence = 0
        bias = 'neutral'

        # 1. EMA crossover (25 pts) — core trend signal
        if ema_fast[-1] > ema_slow[-1]:
            confidence += 25
            bias = 'long'
        elif ema_fast[-1] < ema_slow[-1]:
            confidence += 25
            bias = 'short'

        # 2. RSI zone confirmation (15 pts) — avoid overbought/oversold entries
        if (bias == 'long' and 40 < rsi_val < 70) or (bias == 'short' and 30 < rsi_val < 60):
            confidence += 15

        # 3. MACD histogram alignment (15 pts) — momentum confirmation
        if (bias == 'long' and macd_data['histogram'] > 0) or (bias == 'short' and macd_data['histogram'] < 0):
            confidence += 15

        # 4. Rate of Change (ROC) momentum (10 pts)
        if roc_val is not None:
            if (bias == 'long' and roc_val > 0.5) or (bias == 'short' and roc_val < -0.5):
                confidence += 10

        # 5. ADX trend strength (10 pts) — only trade when trend is real
        if adx_val is not None and adx_val > 20:
            confidence += 10
        elif adx_val is not None and adx_val < 15:
            confidence -= 10  # penalize choppy markets

        # 6. Funding regime alignment (15 pts)
        funding_data = self._client.fetch_funding_rate(symbol)
        funding_rate = float(funding_data.get('fundingRate', 0) or 0)
        regime, size_mult = classify_funding_regime(funding_rate)

        if bias == 'long' and regime == 'bull':
            confidence += 15   # long + positive funding = carry income
        elif bias == 'short' and regime == 'bear':
            confidence += 10
        elif bias == 'long' and regime == 'bear':
            confidence -= 5    # penalize against-funding trades

        # 7. Volume confirmation (10 pts) — last bar volume vs avg
        volumes = [c.get('volume', 0) for c in candles if c.get('volume')]
        if len(volumes) > 5:
            avg_vol = sum(volumes[-20:]) / min(len(volumes[-20:]), 20) if volumes else 1
            last_vol = volumes[-1] if volumes else 0
            if avg_vol > 0 and last_vol > avg_vol * 1.2:
                confidence += 10  # above-average volume confirms move

        signal = {
            'id': str(uuid.uuid4()),
            'ts': int(clock() * 1000),
            'asset': asset,
            'type': 'perp',
            'spot': spot,
            'bias': bias,
            'confidence': confidence,
            'rsi': rsi_val,
            'macd_hist': round(macd_data['histogram'], 2),
            'roc': round(roc_val, 2) if roc_val else None,
            'adx': round(adx_val, 1) if adx_val else None,
            'atr_pct': round(atr_pct, 2),
            'funding_regime': regime,
            'atr': round(atr_val, 2),
        }

        if confidence >= cfg.crypto_perp_min_confidence and bias != 'neutral':
            signal['action'] = f'OPEN_{bias.upper()}'
            signal['reason'] = f'conf={confidence} EMA+RSI+MACD+ROC+ADX+funding'
            self._signals.append(signal)
            self._open_perp(asset, bias, spot, atr_val, size_mult)
        else:
            signal['action'] = 'WAIT'
            signal['reason'] = f'conf={confidence} < {cfg.crypto_perp_min_confidence}'
            self._signals.append(signal)

    def _open_perp(self, asset: str, side: str, price: float,
                   atr_val: float, size_mult: float) -> None:
        """Open perpetual futures position."""
        cfg = settings
        notional = cfg.crypto_perp_position_size_usd * size_mult
        qty = round(notional / price, 6) if price > 0 else 0
        symbol = f'{asset}/USD:{asset}'

        sl_dist = price * cfg.crypto_perp_sl_pct / 100
        tp_dist = price * cfg.crypto_perp_tp_pct / 100

        if side == 'long':
            sl = round(price - sl_dist, 2)
            tp = round(price + tp_dist, 2)
        else:
            sl = round(price + sl_dist, 2)
            tp = round(price - tp_dist, 2)

        _master_paper = (cfg.trading_mode == 'paper') or (not cfg.live_trading_enabled)
        if _master_paper or cfg.crypto_fo_mode == 'paper':
            logger.info('OPEN %s %s perp @ $%.0f  qty=%.6f  SL=$%.0f  TP=$%.0f [paper]',
                        asset, side.upper(), price, qty, sl, tp)
        else:
            result = self._client.place_order(symbol, 'buy' if side == 'long' else 'sell', qty)
            if result.get('error'):
                logger.error('Failed to open %s perp: %s', asset, result)
                return
            logger.info('OPEN %s %s perp @ $%.0f  qty=%.6f [LIVE]',
                        asset, side.upper(), price, qty)

        with self._lock:
            self._perp_positions[asset] = {
                'asset': asset,
                'symbol': symbol,
                'side': side.upper(),
                'entry_price': price,
                'current_price': price,
                'qty': qty,
                'notional_usd': round(notional, 2),
                'sl': sl,
                'tp': tp,
                'entry_time': int(clock() * 1000),
                'pnl': 0.0,
                'mode': cfg.crypto_fo_mode,
            }
        self._save_positions()

    def _monitor_perps(self) -> None:
        """Check SL/TP/timeout for perp positions."""
        cfg = settings
        to_close = []

        for asset, pos in list(self._perp_positions.items()):
            spot = self._spot_prices.get(asset, 0)
            if spot <= 0:
                continue

            pos['current_price'] = spot
            side = pos['side']

            # P&L
            if side == 'LONG':
                pnl = (spot - pos['entry_price']) * pos['qty']
            else:
                pnl = (pos['entry_price'] - spot) * pos['qty']
            pos['pnl'] = round(pnl, 2)

            # SL check
            if (side == 'LONG' and spot <= pos['sl']) or (side == 'SHORT' and spot >= pos['sl']):
                to_close.append((asset, 'sl_hit'))

            # TP check
            if (side == 'LONG' and spot >= pos['tp']) or (side == 'SHORT' and spot <= pos['tp']):
                to_close.append((asset, 'tp_hit'))

            # Max hold timeout
            hold_ms = int(clock() * 1000) - pos.get('entry_time', 0)
            hold_hours = hold_ms / 3_600_000
            if hold_hours > cfg.crypto_perp_max_hold_hours:
                to_close.append((asset, f'timeout_{hold_hours:.0f}h'))

        for asset, reason in to_close:
            self._close_perp(asset, reason)

    def _close_perp(self, asset: str, reason: str) -> None:
        """Close perpetual position."""
        with self._lock:
            pos = self._perp_positions.pop(asset, None)
        if not pos:
            return

        cfg = settings
        spot = self._spot_prices.get(asset, pos.get('current_price', 0))
        pnl = pos.get('pnl', 0)

        from ..domain.services.brokerage_calc import segment_brokerage
        brokerage = segment_brokerage('crypto_fo', pos['entry_price'], spot, pos['qty'],
                                      gross_pnl=pnl, sub_type='perps')
        net_pnl = round(pnl - brokerage, 2)

        _master_live = (cfg.trading_mode == 'live') and cfg.live_trading_enabled
        if _master_live and cfg.crypto_fo_mode != 'paper':
            side = 'sell' if pos['side'] == 'LONG' else 'buy'
            self._client.place_order(pos['symbol'], side, pos['qty'],
                                     params={'reduceOnly': True})

        self._realized_pnl += net_pnl
        self._kill_switch.record_pnl(net_pnl)

        self._record_trade(
            symbol=f'{asset}-PERP',
            side=pos['side'],
            qty=pos['qty'],
            entry=pos['entry_price'],
            exit_price=spot,
            pnl=pnl,
            brokerage=brokerage,
            sub_mode='perps',
        )

        self._save_positions()
        logger.info('CLOSE %s %s perp @ $%.0f  entry=$%.0f  gross=$%.2f brokerage=$%.2f net=$%.2f reason=%s',
                     asset, pos['side'], spot, pos['entry_price'], pnl, brokerage, net_pnl, reason)

    # ── Flatten All ───────────────────────────────────────────────────────────

    def _flatten_all(self, reason: str) -> None:
        """Emergency flatten all positions."""
        logger.warning('FLATTEN ALL: %s', reason)
        for asset in list(self._option_positions.keys()):
            self._close_options(asset, f'flatten:{reason}')
        for asset in list(self._perp_positions.keys()):
            self._close_perp(asset, f'flatten:{reason}')

    # ── Unrealized P&L ────────────────────────────────────────────────────────

    def _update_unrealized(self) -> None:
        """Sum up unrealized P&L from all open positions."""
        total = 0.0
        # Perps
        for pos in self._perp_positions.values():
            total += pos.get('pnl', 0)
        # Options (simplified: premium decay approximation)
        for pos in self._option_positions.values():
            total += pos.get('pnl', 0)
        with self._lock:
            self._unrealized_pnl = round(total, 2)


# ══════════════════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════════════════

_scanner: CryptoFOScanner | None = None
_scanner_lock = threading.Lock()


def get_crypto_fo_scanner() -> CryptoFOScanner:
    global _scanner
    if _scanner is None:
        with _scanner_lock:
            if _scanner is None:
                _scanner = CryptoFOScanner()
    return _scanner
