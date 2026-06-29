"""
Config Service — runtime configuration layer.

Wraps Pydantic Settings with:
  1. Runtime overrides (change config without restart)
  2. YAML config files (config/*.yaml) for structured config
  3. Snapshot export (for audit trail / debugging)
  4. Domain-specific config builders (supervisor, risk, kill switch, brokers)
  5. Validation on load

Priority (highest wins):  runtime overrides > .env > YAML defaults
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...shared.logger import get_logger
from ...shared.time import datetime, now_ist

logger = get_logger('config_service')

# ── YAML loader ────────────────────────────────────────────────────────────────

_YAML_CACHE: dict[str, dict] = {}

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _find_config_dir() -> Path:
    """Locate the config/ directory relative to project root."""
    # backend_v2/app/infrastructure/config/config_service.py → backend_v2/config/
    here = Path(__file__).resolve()
    project_root = here.parent.parent.parent.parent  # backend_v2/
    return project_root / 'config'


def load_yaml(name: str) -> dict[str, Any]:
    """Load a YAML config file by name (without extension).

    Caches on first load. Returns {} if yaml not installed or file missing.
    """
    if not _HAS_YAML:
        return {}

    if name in _YAML_CACHE:
        return _YAML_CACHE[name]

    config_dir = _find_config_dir()
    path = config_dir / f'{name}.yaml'
    if not path.exists():
        path = config_dir / f'{name}.yml'
    if not path.exists():
        logger.debug('YAML config not found: %s', name)
        return {}

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    _YAML_CACHE[name] = data
    logger.info('Loaded YAML config: %s (%d keys)', path.name, len(data))
    return data


def reload_yaml(name: str) -> dict[str, Any]:
    """Force-reload a YAML config file."""
    _YAML_CACHE.pop(name, None)
    return load_yaml(name)


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Immutable snapshot of config state at a point in time."""
    timestamp: datetime
    source: str                          # 'env', 'override', 'reload'
    values: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            'timestamp': self.timestamp.isoformat(),
            'source': self.source,
            'values': self.values,
        }


class ConfigService:
    """Runtime configuration manager.

    Thread-safe. Provides typed access to config, runtime overrides,
    and domain-specific config builders.
    """

    def __init__(self, settings: Any = None) -> None:
        if settings is None:
            from ...config import settings as _default_settings
            settings = _default_settings
        self._settings = settings
        self._overrides: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._snapshots: list[ConfigSnapshot] = []
        self._take_snapshot('env')

    # ── Core access ───────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value. Runtime overrides take precedence."""
        with self._lock:
            if key in self._overrides:
                return self._overrides[key]
        return getattr(self._settings, key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        v = self.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ('1', 'true', 'yes')
        return bool(v)

    def get_str(self, key: str, default: str = '') -> str:
        return str(self.get(key, default))

    # ── Runtime overrides ─────────────────────────────────────────────────

    def override(self, key: str, value: Any) -> None:
        """Set a runtime override (survives until restart or clear)."""
        with self._lock:
            old = self._overrides.get(key)
            self._overrides[key] = value
        logger.info('Config override: %s = %s (was %s)', key, value, old)
        self._take_snapshot('override')

    def clear_override(self, key: str) -> None:
        """Remove a runtime override, reverting to .env value."""
        with self._lock:
            self._overrides.pop(key, None)

    def clear_all_overrides(self) -> None:
        with self._lock:
            self._overrides.clear()

    @property
    def overrides(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._overrides)

    # ── Domain config builders ────────────────────────────────────────────

    def build_supervisor_config(self):
        """Build a SupervisorConfig from current settings + overrides."""
        from ...domain.supervisor.trade_supervisor import SupervisorConfig
        return SupervisorConfig(
            daily_loss_limit=self.get_float('daily_loss_limit_inr', 2500.0),
            max_trades_per_day=self.get_int('max_trades_per_day', 10),
            max_consecutive_losses=self.get_int('max_consecutive_losses', 3),
            cooldown_minutes=self.get_int('cooldown_minutes', 30),
            max_same_underlying_trades=self.get_int('max_same_underlying_trades', 2),
            max_open_trades=self.get_int('max_open_trades', 5),
            allow_outside_safe_hours=self.get_bool('allow_outside_safe_hours', False),
        )

    def build_kill_switch_config(self):
        """Build a KillSwitchConfig from current settings + overrides."""
        from ...domain.safety.kill_switch import KillSwitchConfig
        return KillSwitchConfig(
            daily_loss_soft=self.get_float('ks_daily_loss_soft', 2000.0),
            daily_loss_hard=self.get_float('ks_daily_loss_hard', 4000.0),
            drawdown_soft=self.get_float('ks_drawdown_soft', 1500.0),
            drawdown_hard=self.get_float('ks_drawdown_hard', 3000.0),
            loss_streak_soft=self.get_int('ks_loss_streak_soft', 3),
            loss_streak_hard=self.get_int('ks_loss_streak_hard', 5),
            max_errors_per_window=self.get_int('ks_max_errors', 5),
            error_window_minutes=self.get_int('ks_error_window_min', 10),
        )

    # ── YAML config access ───────────────────────────────────────────────────

    def get_yaml(self, name: str) -> dict[str, Any]:
        """Load a YAML config file by name (cached)."""
        return load_yaml(name)

    def reload_yaml_config(self, name: str) -> dict[str, Any]:
        """Force-reload a YAML config file."""
        return reload_yaml(name)

    # ── Broker config (from YAML) ────────────────────────────────────────────

    def get_active_broker_name(self) -> str:
        """Which broker is active. Env override > YAML > 'paper'."""
        env_val = self.get('active_broker', '')
        if env_val:
            return str(env_val)
        brokers_yaml = load_yaml('brokers')
        return brokers_yaml.get('active_broker', 'paper')

    def get_broker_config(self, broker_name: str = '') -> dict[str, Any]:
        """Get config dict for a specific broker from YAML."""
        name = broker_name or self.get_active_broker_name()
        brokers_yaml = load_yaml('brokers')
        return brokers_yaml.get('brokers', {}).get(name, {})

    # ── Strategy config (from YAML) ──────────────────────────────────────────

    def get_strategy_config(self, strategy_name: str) -> dict[str, Any]:
        """Get config dict for a specific strategy from YAML."""
        strats = load_yaml('strategies')
        return strats.get('strategies', {}).get(strategy_name, {})

    def get_all_strategies(self) -> dict[str, dict[str, Any]]:
        """Get all strategy configs."""
        strats = load_yaml('strategies')
        return strats.get('strategies', {})

    def get_enabled_strategies(self) -> dict[str, dict[str, Any]]:
        """Get only enabled strategies."""
        return {k: v for k, v in self.get_all_strategies().items() if v.get('enabled', False)}

    # ── Versioned strategy config ──────────────────────────────────────────

    def get_strategy_config_store(self):
        """Lazy-init and return the versioned StrategyConfigStore."""
        if not hasattr(self, '_strategy_config_store'):
            from ...domain.services.strategy_config_store import StrategyConfigStore
            self._strategy_config_store = StrategyConfigStore()
            # Seed with current YAML configs
            for name, cfg in self.get_all_strategies().items():
                params = dict(cfg.get('params', {}))
                params.update({k: v for k, v in cfg.items() if k != 'params'})
                self._strategy_config_store.put(name, params, reason='initial load from YAML')
        return self._strategy_config_store

    def update_strategy_params(
        self, strategy_name: str, params: dict, author: str = 'system', reason: str = '',
    ):
        """Update strategy params with version tracking."""
        store = self.get_strategy_config_store()
        return store.put(strategy_name, params, author=author, reason=reason)

    def rollback_strategy(self, strategy_name: str, version: int, author: str = 'system'):
        """Rollback strategy config to a prior version."""
        store = self.get_strategy_config_store()
        return store.rollback(strategy_name, version, author=author)

    def strategy_config_history(self, strategy_name: str) -> list:
        """Get version history for a strategy."""
        store = self.get_strategy_config_store()
        return store.history(strategy_name)

    def strategy_config_diff(self, strategy_name: str, from_v: int, to_v: int):
        """Diff two versions of a strategy config."""
        store = self.get_strategy_config_store()
        return store.diff(strategy_name, from_v, to_v)

    # ── A/B test config (from YAML) ──────────────────────────────────────────

    def get_ab_tests(self) -> dict[str, dict[str, Any]]:
        """Get all A/B test configs."""
        strats = load_yaml('strategies')
        return strats.get('ab_tests', {})

    def get_enabled_ab_tests(self) -> dict[str, dict[str, Any]]:
        """Get only enabled A/B tests."""
        return {k: v for k, v in self.get_ab_tests().items() if v.get('enabled', False)}

    # ── Exchange config (from YAML) ──────────────────────────────────────────

    def get_active_crypto_exchange(self) -> str:
        """Which crypto exchange is active."""
        env_val = self.get('active_crypto_exchange', '')
        if env_val:
            return str(env_val)
        ex = load_yaml('exchanges')
        return ex.get('crypto', {}).get('active_exchange', 'binance')

    def get_exchange_config(self, exchange_name: str = '') -> dict[str, Any]:
        """Get config dict for a specific crypto exchange."""
        name = exchange_name or self.get_active_crypto_exchange()
        ex = load_yaml('exchanges')
        return ex.get('crypto', {}).get('exchanges', {}).get(name, {})

    # ── Risk config (from YAML) ──────────────────────────────────────────────

    def get_risk_config(self, section: str = '') -> dict[str, Any]:
        """Get risk config. Optionally specify section: supervisor, kill_switch, etc."""
        risk = load_yaml('risk')
        if section:
            return risk.get(section, {})
        return risk

    # ── Validation ────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Validate the current configuration. Returns list of errors."""
        errors: list[str] = []

        if self.get_float('daily_loss_limit_inr') <= 0:
            errors.append('daily_loss_limit_inr must be positive')

        if self.get_int('fo_max_lots_per_trade') < 1:
            errors.append('fo_max_lots_per_trade must be >= 1')

        if self.get_float('swing_capital_inr') <= 0:
            errors.append('swing_capital_inr must be positive')

        errors.extend(self._settings.validate_llm())

        return errors

    # ── Snapshot / export ─────────────────────────────────────────────────

    def _take_snapshot(self, source: str) -> None:
        """Record current state for audit trail."""
        with self._lock:
            snap = ConfigSnapshot(
                timestamp=now_ist(),
                source=source,
                values=self._safe_export(),
            )
            self._snapshots.append(snap)
            if len(self._snapshots) > 100:
                self._snapshots = self._snapshots[-100:]

    def _safe_export(self) -> dict[str, Any]:
        """Export config values, masking secrets."""
        d = {}
        for key in self._settings.model_fields:
            val = self.get(key)
            if _is_secret_key(key):
                d[key] = _mask(str(val)) if val else ''
            else:
                d[key] = val
        return d

    def export(self) -> dict[str, Any]:
        """Export current config with secrets masked."""
        with self._lock:
            return self._safe_export()

    @property
    def snapshots(self) -> list[ConfigSnapshot]:
        with self._lock:
            return list(self._snapshots)

    @property
    def settings(self):
        return self._settings


# ── Helpers ───────────────────────────────────────────────────────────────────

_SECRET_PATTERNS = {'key', 'secret', 'token', 'password', 'credential'}


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return any(p in k for p in _SECRET_PATTERNS)


def _mask(value: str) -> str:
    if len(value) <= 8:
        return '***'
    return value[:4] + '***' + value[-4:]
