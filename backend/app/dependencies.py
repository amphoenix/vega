"""
Dependency Injection wiring — FastAPI Depends() hooks.

Provides singletons for event bus, scheduler, broker, and exchange adapters.
No Service Locator anti-pattern — these are explicit, typed dependencies.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from .application.event_bus import EventBus
from .config import Settings, settings
from .domain.safety.kill_switch import KillSwitchEngine, KillSwitchConfig
from .domain.supervisor.trade_supervisor import TradeSupervisor, SupervisorConfig
from .shared.scheduler import Scheduler

if TYPE_CHECKING:
    from .infrastructure.broker.base import BrokerAdapter
    from .infrastructure.exchange.ccxt_adapter import CCXTAdapter
    from .infrastructure.exchange.polymarket_adapter import PolymarketAdapter


# ── Singletons ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_event_bus() -> EventBus:
    return EventBus()


@lru_cache(maxsize=1)
def get_scheduler() -> Scheduler:
    return Scheduler()


@lru_cache(maxsize=1)
def get_kill_switch() -> KillSwitchEngine:
    limit = settings.daily_loss_limit_inr
    return KillSwitchEngine(KillSwitchConfig(
        daily_loss_soft=limit,
        daily_loss_hard=limit * 2,
    ))


@lru_cache(maxsize=1)
def get_supervisor() -> TradeSupervisor:
    ks = get_kill_switch()
    return TradeSupervisor(
        config=SupervisorConfig(daily_loss_limit=settings.daily_loss_limit_inr),
        kill_switch=ks,
    )


def get_settings() -> Settings:
    return settings


# ── Broker / Exchange adapters ────────────────────────────────────────────────

_broker_lock = threading.Lock()
_broker_instance: 'BrokerAdapter | None' = None

def get_broker() -> 'BrokerAdapter':
    """Active broker adapter — config-driven via config/brokers.yaml.

    Thread-safe singleton (lru_cache isn't thread-safe and caused
    multiple broker instances loading 100K+ instruments each).
    """
    global _broker_instance
    if _broker_instance is not None:
        return _broker_instance
    with _broker_lock:
        if _broker_instance is not None:
            return _broker_instance
        from .infrastructure.broker.base import BrokerFactory
        _broker_instance = BrokerFactory.create()
        return _broker_instance


@lru_cache(maxsize=1)
def get_crypto_exchange() -> 'CCXTAdapter':
    """Crypto exchange via CCXT. paper_mode=True → real ticker, simulated fills."""
    from .infrastructure.exchange.ccxt_adapter import CCXTAdapter, CCXTConfig
    paper = settings.crypto_mode == 'paper'
    config = CCXTConfig(
        exchange_name=settings.crypto_exchange,
        api_key=settings.crypto_api_key,
        secret=settings.crypto_api_secret,
        password=settings.crypto_passphrase,
        sandbox=False,
        paper_mode=paper,
        paper_capital=settings.crypto_capital_usd,
    )
    return CCXTAdapter(config)


@lru_cache(maxsize=1)
def get_polymarket_exchange() -> 'PolymarketAdapter':
    """Polymarket prediction market. paper_mode=True → real prices, simulated fills."""
    from .infrastructure.exchange.polymarket_adapter import PolymarketAdapter, PolymarketConfig
    paper = settings.polymarket_mode == 'paper'
    config = PolymarketConfig(
        private_key=settings.polymarket_private_key,
        wallet=settings.polymarket_wallet,
        api_key=settings.polymarket_api_key,
        api_secret=settings.polymarket_api_secret,
        api_passphrase=settings.polymarket_api_passphrase,
        paper_mode=paper,
        paper_capital=settings.polymarket_capital_usd,
    )
    return PolymarketAdapter(config)
