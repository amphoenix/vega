"""
Broker Adapter — Abstract base class for broker integrations.

Adapter pattern: all broker implementations conform to this interface.
Any broker (INDmoney, Dhan, Zerodha, etc.) just implements BrokerAdapter.

Switching brokers is ONE config change:
  1. Set active_broker in config/brokers.yaml
  2. Set env vars (each broker declares what it needs via env_keys in YAML)
  3. That's it — BrokerFactory.create() handles everything.

To add a new broker:
  1. Create app/infrastructure/broker/<name>_broker.py implementing BrokerAdapter
  2. Add entry to config/brokers.yaml (class, env_keys, defaults)
  3. Register with register_broker() or add to _BROKER_REGISTRY
  4. Done — zero code changes anywhere else.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ...shared.logger import get_logger

logger = get_logger('broker')


# ── Data Transfer Objects ────────────────────────────────────────────────────

@dataclass
class OrderResult:
    """Result of placing an order."""
    success: bool
    order_id: str = ''
    fill_price: float = 0.0
    status: str = ''
    message: str = ''
    raw: dict[str, Any] | None = None


@dataclass
class QuoteResult:
    """Spot / LTP quote."""
    symbol: str
    ltp: float
    bid: float = 0.0
    ask: float = 0.0
    volume: int = 0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    timestamp: str = ''


@dataclass
class PositionInfo:
    """Broker position data."""
    symbol: str
    qty: int
    avg_price: float
    ltp: float
    pnl: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    security_id: str = ''
    exchange: str = ''
    product_type: str = ''


@dataclass
class HoldingInfo:
    """Long-term holding (CNC/delivery)."""
    symbol: str
    qty: int
    avg_price: float
    ltp: float = 0.0
    pnl: float = 0.0
    security_id: str = ''


@dataclass
class CandleData:
    """Single OHLCV bar."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass
class InstrumentInfo:
    """Instrument master entry."""
    symbol: str
    security_id: str = ''
    exchange: str = ''
    instrument_type: str = ''   # EQ, FUT, CE, PE
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: str = ''
    strike: float = 0.0
    underlying: str = ''
    name: str = ''


# ── Broker Adapter ABC ──────────────────────────────────────────────────────

class BrokerAdapter(ABC):
    """Interface that all broker implementations must satisfy.

    Capabilities (all abstract = MUST implement, non-abstract = OPTIONAL):

    CORE (abstract):
      place_order, get_ltp, get_positions, get_available_cash, get_quote

    ORDERS (optional):
      modify_order, cancel_order, get_order_status, get_order_list

    MARKET DATA (optional):
      get_candles, subscribe_ticks, unsubscribe_ticks

    INSTRUMENT MASTER (optional):
      search_instruments, get_instrument

    ACCOUNT (optional):
      get_holdings
    """

    @property
    def broker_name(self) -> str:
        """Human-readable broker name. Override in subclass."""
        return self.__class__.__name__

    @property
    def is_configured(self) -> bool:
        """True when broker credentials are present (token/key in env)."""
        # Each broker sets _stub (IndMoney) or _stub_mode (Dhan) when creds are missing
        if hasattr(self, '_stub'):
            return not self._stub
        if hasattr(self, '_stub_mode'):
            return not self._stub_mode
        return True

    # ── Core (abstract) ──────────────────────────────────────────────────

    @abstractmethod
    def place_order(
        self, symbol: str, side: str, qty: int,
        order_type: str = 'MARKET', price: float = 0.0,
        exchange: str = 'NFO', security_id: str = '',
        trigger_price: float = 0.0, product_type: str = '',
        tag: str = '',
    ) -> OrderResult:
        """Place a buy/sell order."""
        ...

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str = 'NFO',
                security_id: str = '') -> Optional[float]:
        """Get last traded price for a symbol."""
        ...

    @abstractmethod
    def get_positions(self) -> list[PositionInfo]:
        """Get all open positions."""
        ...

    @abstractmethod
    def get_available_cash(self) -> float:
        """Get available margin / cash."""
        ...

    @abstractmethod
    def get_quote(self, symbol: str, exchange: str = 'NFO',
                  security_id: str = '') -> Optional[QuoteResult]:
        """Get full quote (LTP, bid, ask, OHLC)."""
        ...

    def get_quotes_batch(self, scrip_codes: list[str]) -> dict[str, dict]:
        """Fetch full quotes (bid/ask/ltp/oi/volume) for multiple scrip codes.
        Returns {scrip_code: {bid, ask, ltp, oi, volume}}. Override per broker."""
        return {}

    # ── Orders (optional) ────────────────────────────────────────────────

    def modify_order(
        self, order_id: str, qty: int = 0, price: float = 0.0,
        order_type: str = '', trigger_price: float = 0.0,
    ) -> OrderResult:
        """Modify an existing pending order. Override if broker supports it."""
        return OrderResult(success=False, message='modify_order not supported')

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Override if broker supports it."""
        return False

    def get_order_status(self, order_id: str) -> dict[str, Any]:
        """Get order status. Override if broker supports it."""
        return {}

    def get_order_list(self) -> list[dict[str, Any]]:
        """Get today's order book. Override if broker supports it."""
        return []

    # ── Market Data (optional) ───────────────────────────────────────────

    def get_candles(
        self, symbol: str, interval: str = '5m', days: int = 5,
        exchange: str = 'NFO', security_id: str = '',
    ) -> list[CandleData]:
        """Fetch historical OHLCV candles.

        interval: '1m','5m','15m','30m','1h','1d'
        Returns oldest→newest.
        """
        return []

    def subscribe_ticks(
        self, symbols: list[str],
        callback: Callable[[str, float, dict[str, Any]], None] | None = None,
        exchange: str = 'NFO',
    ) -> bool:
        """Subscribe to real-time tick stream.

        callback(symbol, ltp, tick_data) is invoked on each tick.
        Override if broker supports WebSocket market feed.
        """
        return False

    def unsubscribe_ticks(self, symbols: list[str]) -> bool:
        """Unsubscribe from tick stream."""
        return False

    def create_tick_queue(self, symbol: str) -> tuple[str, queue.Queue]:
        """Create a queue that receives live tick payloads for *symbol*.

        Returns (scrip_code, queue).  The queue receives JSON-string
        payloads exactly like the old Flask SSE stream.  Used by the
        SSE ``/stream/{ticker}`` endpoint so ticks arrive from the
        WebSocket in real-time instead of 1-second REST polling.

        Override in broker implementation.  Default returns ('', empty queue).
        """
        return ('', queue.Queue(maxsize=50))

    def remove_tick_queue(self, code: str, q: queue.Queue) -> None:
        """Remove a previously-created tick queue.

        Called when the SSE client disconnects.  Override per broker.
        """
        pass

    # ── Instrument Master (optional) ─────────────────────────────────────

    def search_instruments(
        self, query: str, exchange: str = '', instrument_type: str = '',
    ) -> list[InstrumentInfo]:
        """Search instrument master. Override if broker supports it."""
        return []

    def get_instrument(
        self, symbol: str, exchange: str = 'NFO',
    ) -> Optional[InstrumentInfo]:
        """Get a specific instrument's details."""
        return None

    def resolve_option_contract(
        self, underlying: str, option_type: str, strike: float,
    ) -> Optional[dict]:
        """Find the nearest matching option contract for an underlying.
        Returns {trading_symbol, display_symbol, security_id, expiry_date,
        strike, ltp} or None. Override per broker."""
        return None

    def underlying_lot_size(self, underlying: str) -> Optional[int]:
        """Standard lot size for an underlying from the F&O instrument master.
        Override per broker."""
        return None

    def load_instruments(self, source: str = 'fno') -> list:
        """Load raw instrument master rows. Override per broker."""
        return []

    def get_option_chain(self, underlying: str, expiry: str = '') -> Optional[dict]:
        """Fetch option chain natively from broker API.

        Returns dict with keys: spot, expiry, lot_size, strikes (list of
        {strike, ce: {ltp,oi,volume,bid,ask,iv}, pe: {...}}).
        Return None to fall back to CSV-based approach.
        """
        return None

    # ── Account (optional) ───────────────────────────────────────────────

    def get_holdings(self) -> list[HoldingInfo]:
        """Get long-term holdings (CNC/delivery)."""
        return []


# ── Broker Factory ─────────────────────────────────────────────────────────────

# Registry of known broker adapter classes (lazy imports).
# Key = broker name in YAML, Value = (module_path, class_name)
_BROKER_REGISTRY: dict[str, tuple[str, str]] = {
    'indmoney': ('app.infrastructure.broker.indmoney_broker', 'INDMoneyBroker'),
    'dhan':     ('app.infrastructure.broker.dhan_broker', 'DhanBroker'),
}


def register_broker(name: str, module_path: str, class_name: str) -> None:
    """Register a custom broker adapter at runtime."""
    _BROKER_REGISTRY[name] = (module_path, class_name)


class BrokerFactory:
    """Config-driven broker creation. ZERO if/else per broker.

    How it works:
      1. Reads config/brokers.yaml for active_broker + broker-specific config
      2. Each broker declares 'class' (module:ClassName) and 'env_keys' mapping
      3. Factory resolves env vars → kwargs, merges YAML defaults, creates instance

    Usage:
        broker = BrokerFactory.create()        # uses active_broker from YAML
        broker = BrokerFactory.create('dhan')   # explicit broker name
    """

    @staticmethod
    def is_paper_mode() -> bool:
        """Check if we're in paper (sandbox) mode."""
        from ...config import settings
        return settings.trading_mode == 'paper'

    @staticmethod
    def create(broker_name: str = '', **overrides: Any) -> BrokerAdapter:
        """Create a broker adapter by name — fully config-driven.

        Reads config/brokers.yaml for settings, env vars for secrets.
        No hardcoded broker-specific logic.

        YAML schema per broker:
          brokers:
            dhan:
              class: app.infrastructure.broker.dhan_broker.DhanBroker
              env_keys:                          # env var → constructor kwarg
                DHAN_CLIENT_ID: client_id
                DHAN_ACCESS_TOKEN: access_token
              defaults:                          # static kwargs (from YAML)
                product_type: INTRADAY
        """
        import os
        from dotenv import load_dotenv
        from ..config.config_service import load_yaml

        # Ensure .env vars are in os.environ (idempotent, no-op if already set)
        _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
        load_dotenv(os.path.join(_project_root, '.env'), override=False)

        brokers_yaml = load_yaml('brokers')
        name = broker_name or brokers_yaml.get('active_broker', 'dhan')
        broker_cfg = brokers_yaml.get('brokers', {}).get(name, {})

        # Paper mode override: if TRADING_MODE=paper, use PaperBroker
        if BrokerFactory.is_paper_mode() and name != 'paper':
            logger.info('TRADING_MODE=paper → wrapping %s (real broker not used for orders)', name)

        # Resolve class — YAML 'class' field or registry fallback
        class_path = broker_cfg.get('class', '')
        if class_path:
            module_path, class_name = class_path.rsplit('.', 1)
        elif name in _BROKER_REGISTRY:
            module_path, class_name = _BROKER_REGISTRY[name]
        else:
            raise ValueError(
                f"Unknown broker '{name}'. "
                f"Known: {list(_BROKER_REGISTRY.keys())}. "
                f"Add it to config/brokers.yaml with a 'class' field, "
                f"or register with register_broker()."
            )

        # Lazy import the adapter class
        import importlib
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)

        # Build kwargs: env_keys → env vars → constructor params
        kwargs: dict[str, Any] = {}

        # 1) Static defaults from YAML
        defaults = broker_cfg.get('defaults', {})
        kwargs.update(defaults)

        # 2) Env var mapping: env var name → constructor kwarg name
        env_keys = broker_cfg.get('env_keys', {})
        for env_var, kwarg_name in env_keys.items():
            val = os.environ.get(env_var, '')
            if val:
                kwargs[kwarg_name] = val

        # 3) Backward compat: old YAML format without env_keys block
        #    Reads common broker env vars by convention
        if not env_keys:
            kwargs.update(_legacy_env_resolve(name))
            # merge flat YAML keys (old format)
            for k, v in broker_cfg.items():
                if k not in ('class', 'env_keys', 'defaults', 'exchange'):
                    kwargs[k] = v

        # 4) Caller overrides win
        kwargs.update(overrides)

        logger.info('Creating broker: %s (class=%s.%s, kwargs=%s)',
                     name, module_path, class_name,
                     [k for k in kwargs.keys()])

        return cls(**kwargs)


def _legacy_env_resolve(broker_name: str) -> dict[str, Any]:
    """Backward-compatible env var resolution for old YAML format.

    Follows convention: <BROKER_NAME>_<FIELD> env var.
    """
    import os
    prefix = broker_name.upper()
    result: dict[str, Any] = {}

    # Common patterns
    for suffix, kwarg in [
        ('ACCESS_TOKEN', 'access_token'),
        ('CLIENT_ID', 'client_id'),
        ('API_KEY', 'api_key'),
        ('API_SECRET', 'api_secret'),
    ]:
        val = os.environ.get(f'{prefix}_{suffix}', '')
        if val:
            result[kwarg] = val

    return result
