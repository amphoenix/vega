"""
Strategy Plugin ABC — Freqtrade-inspired strategy architecture.

Every trading strategy (scalp, swing, AI-assisted, crypto, polymarket)
implements this interface. The engine loads strategies as plugins and
routes market data to the appropriate strategy based on market type.

Architecture:
    StrategyBase (ABC)
        ├── CryptoMomentumStrategy  (crypto via CCXT)
        ├── PolymarketResearchStrategy (prediction markets + LLM)
        └── ... (user-defined)

Inspired by:
  - Freqtrade: strategy lifecycle, populate_indicators/entry/exit
  - Hummingbot: strategy config, market-aware strategies

Pure domain — no I/O. Strategies receive data, emit signals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from ..entities.signal import Signal
from ..value_objects.instrument import Instrument
from ..value_objects.market import MarketType
from ..regime.regime_engine import Regime


class StrategyState(str, Enum):
    """Lifecycle state of a strategy."""
    IDLE     = 'idle'       # loaded but not running
    RUNNING  = 'running'    # actively processing data
    PAUSED   = 'paused'     # temporarily halted (cooldown, regime)
    STOPPED  = 'stopped'    # permanently stopped for the session


@dataclass
class StrategyConfig:
    """Configuration for a strategy instance."""
    name: str = ''
    market_type: MarketType = MarketType.INDIAN_FO
    instruments: list[str] = field(default_factory=list)  # symbols to trade
    trade_mode: str = 'swing'       # 'swing', 'scalp', 'research'
    max_positions: int = 2
    risk_per_trade_pct: float = 20.0
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)   # strategy-specific


@dataclass
class StrategyResult:
    """Output of a strategy tick — zero or more signals."""
    signals: list[Signal] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_signals(self) -> bool:
        return len(self.signals) > 0


class StrategyBase(ABC):
    """Abstract base for all trading strategies.

    Lifecycle:
        1. __init__(config)
        2. on_start()               — called once when engine starts
        3. on_tick(data)             — called on every market data update
        4. on_regime_change(regime)  — called when regime engine detects change
        5. on_trade_closed(result)   — feedback loop from trade outcomes
        6. on_stop()                 — called on shutdown

    Strategies must NOT:
      - Make I/O calls (no HTTP, no DB, no file)
      - Import infrastructure modules
      - Hold exchange connections

    Strategies CAN:
      - Compute indicators from provided data
      - Emit Signal objects
      - Track internal state (indicators, counters)
      - Use regime info for position sizing
    """

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.state = StrategyState.IDLE
        self._current_regime: Regime = Regime.RANGING

    @property
    def name(self) -> str:
        return self.config.name or self.__class__.__name__

    @property
    def market_type(self) -> MarketType:
        return self.config.market_type

    @property
    def regime(self) -> Regime:
        return self._current_regime

    # ── Lifecycle hooks ──────────────────────────────────────────────────

    def on_start(self) -> None:
        """Called once when the strategy engine starts. Override for init."""
        self.state = StrategyState.RUNNING

    def on_stop(self) -> None:
        """Called on shutdown. Override for cleanup."""
        self.state = StrategyState.STOPPED

    def on_regime_change(self, new_regime: Regime) -> None:
        """Called when regime engine detects a change.

        Default: update internal regime. Override to adapt behavior
        (e.g., pause in HIGH_VOLATILITY, increase size in TRENDING).
        """
        self._current_regime = new_regime

    def on_trade_opened(self, trade_info: dict[str, Any]) -> None:
        """Called when the engine confirms a trade entry.

        Override to update open position counts, ledger, etc.
        """
        pass

    def on_trade_closed(self, trade_result: dict[str, Any]) -> None:
        """Feedback loop — called after a trade closes.

        Override to adapt strategy based on outcomes
        (e.g., reduce aggression after consecutive losses).
        """
        pass

    # ── Core — must implement ─────────────────────────────────────────────

    @abstractmethod
    def on_tick(self, data: dict[str, Any]) -> StrategyResult:
        """Process a market data tick and optionally emit signals.

        Args:
            data: Market data dict. Contents vary by market:
                Indian F&O: {'spot': 18500, 'vix': 14.5, 'chain': {...}}
                Crypto: {'ohlcv': [...], 'orderbook': {...}, 'ticker': {...}}
                Polymarket: {'markets': [...], 'prices': {...}}

        Returns:
            StrategyResult with zero or more Signal objects.
        """
        ...

    @abstractmethod
    def get_instruments(self) -> list[Instrument]:
        """Return instruments this strategy wants to trade.

        Called by the engine to subscribe to market data feeds.
        """
        ...

    # ── Optional overrides ───────────────────────────────────────────────

    def should_trade(self) -> bool:
        """Pre-check before on_tick. Return False to skip processing."""
        return self.state == StrategyState.RUNNING and self.config.enabled

    def describe(self) -> dict[str, Any]:
        """Return strategy metadata for monitoring/UI."""
        return {
            'name': self.name,
            'state': self.state.value,
            'market': self.market_type.value,
            'regime': self._current_regime.value,
            'instruments': self.config.instruments,
            'trade_mode': self.config.trade_mode,
        }
