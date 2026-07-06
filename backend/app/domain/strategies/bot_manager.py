"""
Crypto Bot Manager — multi-bot orchestration layer.

Like Freqtrade / OctoBot / 3Commas: each bot is an independent scanner instance
with its own name, symbols, strategy config, positions, and P&L tracking.

Bots are persisted in SQLite and auto-restored on startup.

Usage:
    mgr = get_bot_manager()
    bot_id = mgr.create_bot('BTC Scalper', symbols=['BTCUSDT'], config={...})
    mgr.start_bot(bot_id)
    mgr.stop_bot(bot_id)
    mgr.delete_bot(bot_id)
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from ...shared.logger import get_logger

logger = get_logger('bot_manager')


# ── Bot config dataclass ─────────────────────────────────────────────────────

@dataclass
class BotConfig:
    """Per-bot configuration overrides. Unset fields fall back to global settings."""
    scan_interval_sec: int = 300
    entry_tf: str = '5m'
    trend_tf: str = '1h'
    min_confidence: int = 60
    sl_pct: float = 0.10
    tp_pct: float = 0.0
    trail_pct: float = 0.02
    breakeven_pct: float = 0.02
    max_hold_hours: int = 48
    max_positions: int = 3
    position_size_usd: float = 1000.0
    daily_loss_limit_usd: float = 500.0
    trade_cooldown_sec: int = 600

    @classmethod
    def from_dict(cls, d: dict) -> BotConfig:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


# ── CryptoBot — wraps a CryptoScanner with per-bot config ───────────────────

class CryptoBot:
    """A single named bot instance backed by a CryptoScanner."""

    def __init__(self, bot_id: str, name: str, strategy: str,
                 symbols: list[str], config: BotConfig) -> None:
        self.bot_id = bot_id
        self.name = name
        self.strategy = strategy
        self.symbols = symbols
        self.config = config
        self._scanner = None  # lazy-init

    def _get_scanner(self):
        """Lazy-create a CryptoScanner with per-bot overrides."""
        if self._scanner is None:
            from .crypto_scanner import CryptoScanner
            self._scanner = CryptoScanner(bot_config=self)
        return self._scanner

    @property
    def is_running(self) -> bool:
        return self._scanner is not None and self._scanner.is_running()

    def start(self) -> None:
        sc = self._get_scanner()
        sc.start()
        logger.info('Bot [%s] "%s" started — %d symbols', self.bot_id[:8], self.name, len(self.symbols))

    def stop(self) -> None:
        if self._scanner:
            self._scanner.stop()
            logger.info('Bot [%s] "%s" stopped', self.bot_id[:8], self.name)

    def get_status(self) -> dict:
        sc = self._get_scanner()
        positions = sc.get_positions()
        return {
            'bot_id':         self.bot_id,
            'name':           self.name,
            'strategy':       self.strategy,
            'symbols':        self.symbols,
            'running':        sc.is_running(),
            'signal_count':   sc.signal_count(),
            'positions':      len(positions),
            'open_positions': positions,
            'pnl':            sc.get_pnl(),
            'config':         self.config.to_dict(),
        }

    def get_signals(self, limit: int = 30) -> list[dict]:
        return self._get_scanner().get_signals(limit)

    def get_positions(self) -> list[dict]:
        return self._get_scanner().get_positions()

    def get_pnl(self) -> dict:
        return self._get_scanner().get_pnl()

    def manual_close(self, symbol: str) -> dict | None:
        return self._get_scanner().manual_close(symbol)

    def reset_daily(self) -> None:
        if self._scanner:
            self._scanner.reset_daily()


# ── BotManager — manages all bot instances ───────────────────────────────────

class BotManager:
    """Orchestrates multiple CryptoBot instances. Persists to SQLite."""

    def __init__(self) -> None:
        self._bots: dict[str, CryptoBot] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load saved bots from DB on first access."""
        if self._loaded:
            return
        self._loaded = True
        try:
            from ...infrastructure.db import state_store
            for row in state_store.list_bots():
                bot_id = row['bot_id']
                symbols = [s.strip() for s in row['symbols'].split(',') if s.strip()]
                config_dict = json.loads(row.get('config_json', '{}'))
                config = BotConfig.from_dict(config_dict)
                bot = CryptoBot(
                    bot_id=bot_id,
                    name=row['name'],
                    strategy=row.get('strategy', 'trend_v4'),
                    symbols=symbols,
                    config=config,
                )
                self._bots[bot_id] = bot
                # Auto-start enabled bots
                if row.get('enabled', 1):
                    try:
                        bot.start()
                    except Exception as exc:
                        logger.warning('Failed to auto-start bot %s: %s', bot_id[:8], exc)
            logger.info('Loaded %d crypto bots from DB', len(self._bots))
        except Exception as exc:
            logger.warning('Could not load bots from DB: %s', exc)

    def create_bot(self, name: str, symbols: list[str],
                   strategy: str = 'trend_v4',
                   config: dict | None = None) -> str:
        """Create a new bot, persist it, return bot_id."""
        self._ensure_loaded()
        bot_id = str(uuid.uuid4())
        bot_config = BotConfig.from_dict(config or {})
        bot = CryptoBot(
            bot_id=bot_id,
            name=name,
            strategy=strategy,
            symbols=symbols,
            config=bot_config,
        )
        self._bots[bot_id] = bot

        # Persist
        try:
            from ...infrastructure.db import state_store
            state_store.save_bot(
                bot_id=bot_id,
                name=name,
                strategy=strategy,
                symbols=','.join(symbols),
                config_json=json.dumps(bot_config.to_dict()),
                enabled=True,
            )
        except Exception as exc:
            logger.error('Failed to persist bot: %s', exc)

        logger.info('Created bot [%s] "%s" — %d symbols', bot_id[:8], name, len(symbols))
        return bot_id

    def update_bot(self, bot_id: str, name: str | None = None,
                   symbols: list[str] | None = None,
                   config: dict | None = None) -> bool:
        """Update bot config. Must stop/start to apply runtime changes."""
        self._ensure_loaded()
        bot = self._bots.get(bot_id)
        if not bot:
            return False

        if name is not None:
            bot.name = name
        if symbols is not None:
            bot.symbols = symbols
        if config is not None:
            bot.config = BotConfig.from_dict({**bot.config.to_dict(), **config})

        # Re-persist
        try:
            from ...infrastructure.db import state_store
            state_store.save_bot(
                bot_id=bot_id,
                name=bot.name,
                strategy=bot.strategy,
                symbols=','.join(bot.symbols),
                config_json=json.dumps(bot.config.to_dict()),
                enabled=bot.is_running,
            )
        except Exception as exc:
            logger.error('Failed to update bot in DB: %s', exc)

        return True

    def delete_bot(self, bot_id: str) -> bool:
        """Stop and delete a bot."""
        self._ensure_loaded()
        bot = self._bots.pop(bot_id, None)
        if not bot:
            return False
        bot.stop()
        try:
            from ...infrastructure.db import state_store
            state_store.delete_bot(bot_id)
        except Exception as exc:
            logger.error('Failed to delete bot from DB: %s', exc)
        logger.info('Deleted bot [%s] "%s"', bot_id[:8], bot.name)
        return True

    def start_bot(self, bot_id: str) -> bool:
        """Start a specific bot."""
        self._ensure_loaded()
        bot = self._bots.get(bot_id)
        if not bot:
            return False
        bot.start()
        # Update enabled state
        self._persist_enabled(bot_id, True)
        return True

    def stop_bot(self, bot_id: str) -> bool:
        """Stop a specific bot."""
        self._ensure_loaded()
        bot = self._bots.get(bot_id)
        if not bot:
            return False
        bot.stop()
        self._persist_enabled(bot_id, False)
        return True

    def start_all(self) -> int:
        """Start all enabled bots. Returns count started."""
        self._ensure_loaded()
        count = 0
        for bot in self._bots.values():
            if not bot.is_running:
                bot.start()
                count += 1
        return count

    def stop_all(self) -> int:
        """Stop all running bots. Returns count stopped."""
        self._ensure_loaded()
        count = 0
        for bot in self._bots.values():
            if bot.is_running:
                bot.stop()
                count += 1
        return count

    def get_bot(self, bot_id: str) -> CryptoBot | None:
        self._ensure_loaded()
        return self._bots.get(bot_id)

    def list_bots(self) -> list[dict]:
        """Return status for all bots."""
        self._ensure_loaded()
        return [bot.get_status() for bot in self._bots.values()]

    def aggregate_pnl(self) -> dict:
        """Aggregate P&L across all bots."""
        self._ensure_loaded()
        total = {'realized': 0.0, 'unrealized': 0.0, 'net': 0.0, 'daily': 0.0,
                 'total_positions': 0, 'running_bots': 0, 'total_bots': len(self._bots)}
        for bot in self._bots.values():
            if bot.is_running:
                total['running_bots'] += 1
            pnl = bot.get_pnl()
            total['realized'] += pnl.get('realized', 0)
            total['unrealized'] += pnl.get('unrealized', 0)
            total['net'] += pnl.get('net', 0)
            total['daily'] += pnl.get('daily', 0)
            total['total_positions'] += pnl.get('positions', 0)
        for k in ('realized', 'unrealized', 'net', 'daily'):
            total[k] = round(total[k], 2)
        return total

    def _persist_enabled(self, bot_id: str, enabled: bool) -> None:
        bot = self._bots.get(bot_id)
        if not bot:
            return
        try:
            from ...infrastructure.db import state_store
            state_store.save_bot(
                bot_id=bot_id,
                name=bot.name,
                strategy=bot.strategy,
                symbols=','.join(bot.symbols),
                config_json=json.dumps(bot.config.to_dict()),
                enabled=enabled,
            )
        except Exception as exc:
            logger.error('Failed to update bot enabled state: %s', exc)


# ── Singleton ────────────────────────────────────────────────────────────────

_manager: BotManager | None = None


def get_bot_manager() -> BotManager:
    global _manager
    if _manager is None:
        _manager = BotManager()
    return _manager
