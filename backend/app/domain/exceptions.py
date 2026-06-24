"""
Domain exceptions — structured error hierarchy.

Every module raises one of these. Handlers catch by category.
Never raise bare Exception / ValueError from domain code.

Hierarchy:
    VegaError (base)
    ├── TradingError (trade lifecycle)
    │   ├── InvalidStateTransition
    │   ├── OrderRejected
    │   └── InsufficientCapital
    ├── RiskError (risk gates)
    │   ├── BudgetExceeded
    │   ├── KillSwitchActive
    │   ├── ExposureLimitHit
    │   └── CooldownActive
    ├── BrokerError (broker infra)
    │   ├── BrokerConnectionError
    │   └── BrokerOrderError
    ├── MarketDataError (data feeds)
    └── ConfigError (bad config / env)
"""

from __future__ import annotations


class VegaError(Exception):
    """Base for all Vega errors."""

    def __init__(self, message: str, detail: str = '', code: str = ''):
        self.message = message
        self.detail = detail
        self.code = code or self.__class__.__name__
        super().__init__(message)


# ── Trading ──────────────────────────────────────────────────────────────────

class TradingError(VegaError):
    """Trade lifecycle errors."""
    pass


class InvalidStateTransition(TradingError):
    """FSM transition not allowed."""

    def __init__(self, current: str, target: str):
        super().__init__(
            message=f'Invalid transition: {current} → {target}',
            detail=f'from={current} to={target}',
        )
        self.current = current
        self.target = target


class OrderRejected(TradingError):
    """Broker rejected the order."""
    pass


class InsufficientCapital(TradingError):
    """Not enough capital to place the trade."""
    pass


# ── Risk ─────────────────────────────────────────────────────────────────────

class RiskError(VegaError):
    """Risk gate blocked the action."""
    pass


class BudgetExceeded(RiskError):
    """Worst-case loss exceeds remaining daily budget."""
    pass


class KillSwitchActive(RiskError):
    """Daily loss limit hit — all trading halted."""
    pass


class ExposureLimitHit(RiskError):
    """Directional exposure limit reached."""
    pass


class CooldownActive(RiskError):
    """Instrument on cooldown after SL hit."""
    pass


# ── Infrastructure ───────────────────────────────────────────────────────────

class BrokerError(VegaError):
    """Broker integration errors."""
    pass


class BrokerConnectionError(BrokerError):
    """Cannot connect to broker API."""
    pass


class BrokerOrderError(BrokerError):
    """Broker returned an error for an order operation."""
    pass


class MarketDataError(VegaError):
    """Market data feed errors."""
    pass


class ConfigError(VegaError):
    """Invalid configuration or missing environment variables."""
    pass
