"""
Versioned Strategy Config Store — tracks config changes with history & rollback.

Every strategy config update is stored as an immutable version snapshot.
Supports:
  - Version history per strategy
  - Diff between any two versions
  - Rollback to a prior version
  - Active config retrieval

Pure domain — no file I/O, no database. The infrastructure layer
(ConfigService) feeds YAML data in; this module manages versioning.

Uses shared.time for IST timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...shared.time import now_ist

# ── Domain models ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfigVersion:
    """Immutable snapshot of a strategy config at a point in time."""
    strategy_name: str
    version: int
    params: dict[str, Any]
    created_at: str                # ISO timestamp (IST)
    author: str = 'system'        # who made the change
    reason: str = ''               # why the change was made
    parent_version: int = 0        # 0 = first version (no parent)

    def to_dict(self) -> dict[str, Any]:
        return {
            'strategy_name': self.strategy_name,
            'version': self.version,
            'params': dict(self.params),
            'created_at': self.created_at,
            'author': self.author,
            'reason': self.reason,
            'parent_version': self.parent_version,
        }


@dataclass(frozen=True)
class ConfigDiff:
    """Diff between two config versions."""
    strategy_name: str
    from_version: int
    to_version: int
    added: dict[str, Any]       # keys present in `to` but not `from`
    removed: dict[str, Any]     # keys present in `from` but not `to`
    changed: dict[str, tuple[Any, Any]]  # key → (old_value, new_value)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            'strategy_name': self.strategy_name,
            'from_version': self.from_version,
            'to_version': self.to_version,
            'added': self.added,
            'removed': self.removed,
            'changed': {k: {'old': v[0], 'new': v[1]} for k, v in self.changed.items()},
        }


# ── Store ────────────────────────────────────────────────────────────────────

class StrategyConfigStore:
    """In-memory versioned config store for all strategies.

    Usage:
        store = StrategyConfigStore()
        v1 = store.put('swing_ai', {'min_confidence': 85, 'use_llm': True})
        v2 = store.put('swing_ai', {'min_confidence': 80, 'use_llm': True},
                        author='aman', reason='lower confidence for more signals')
        diff = store.diff('swing_ai', 1, 2)
        store.rollback('swing_ai', 1)  # revert to v1
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[ConfigVersion]] = {}  # strategy → [versions]
        self._active: dict[str, int] = {}                    # strategy → active version number

    def put(
        self,
        strategy_name: str,
        params: dict[str, Any],
        author: str = 'system',
        reason: str = '',
    ) -> ConfigVersion:
        """Store a new version of strategy config. Returns the version."""
        history = self._versions.setdefault(strategy_name, [])
        version_num = len(history) + 1
        parent = history[-1].version if history else 0

        ver = ConfigVersion(
            strategy_name=strategy_name,
            version=version_num,
            params=dict(params),
            created_at=now_ist().isoformat(),
            author=author,
            reason=reason,
            parent_version=parent,
        )
        history.append(ver)
        self._active[strategy_name] = version_num
        return ver

    def get_active(self, strategy_name: str) -> ConfigVersion | None:
        """Get the currently active config version for a strategy."""
        active_num = self._active.get(strategy_name)
        if active_num is None:
            return None
        return self._get_version(strategy_name, active_num)

    def get_active_params(self, strategy_name: str) -> dict[str, Any]:
        """Get the active config params dict. Returns {} if none."""
        ver = self.get_active(strategy_name)
        return dict(ver.params) if ver else {}

    def get_version(self, strategy_name: str, version: int) -> ConfigVersion | None:
        """Get a specific version."""
        return self._get_version(strategy_name, version)

    def history(self, strategy_name: str) -> list[ConfigVersion]:
        """Get full version history for a strategy (oldest first)."""
        return list(self._versions.get(strategy_name, []))

    def latest_version(self, strategy_name: str) -> int:
        """Latest version number (0 if no versions exist)."""
        history = self._versions.get(strategy_name, [])
        return history[-1].version if history else 0

    def diff(
        self,
        strategy_name: str,
        from_version: int,
        to_version: int,
    ) -> ConfigDiff | None:
        """Compute diff between two versions. Returns None if either version missing."""
        v_from = self._get_version(strategy_name, from_version)
        v_to = self._get_version(strategy_name, to_version)
        if v_from is None or v_to is None:
            return None

        return _compute_diff(strategy_name, v_from, v_to)

    def rollback(
        self,
        strategy_name: str,
        target_version: int,
        author: str = 'system',
    ) -> ConfigVersion | None:
        """Rollback to a previous version by creating a new version with its params.

        This does NOT rewrite history — it creates a new version with the
        old params, maintaining a full audit trail.
        """
        target = self._get_version(strategy_name, target_version)
        if target is None:
            return None

        return self.put(
            strategy_name,
            target.params,
            author=author,
            reason=f'rollback to v{target_version}',
        )

    def strategies(self) -> list[str]:
        """List all strategy names with at least one version."""
        return list(self._versions.keys())

    def stats(self) -> dict[str, Any]:
        """Store statistics for monitoring."""
        return {
            'strategies': len(self._versions),
            'total_versions': sum(len(v) for v in self._versions.values()),
            'active': dict(self._active),
        }

    # ── Internal ─────────────────────────────────────────────────────────

    def _get_version(self, strategy_name: str, version: int) -> ConfigVersion | None:
        history = self._versions.get(strategy_name, [])
        for v in history:
            if v.version == version:
                return v
        return None


# ── Diff helper ──────────────────────────────────────────────────────────────

def _compute_diff(
    strategy_name: str,
    v_from: ConfigVersion,
    v_to: ConfigVersion,
) -> ConfigDiff:
    """Compute what changed between two config versions."""
    old, new = v_from.params, v_to.params
    all_keys = set(old) | set(new)

    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    changed: dict[str, tuple[Any, Any]] = {}

    for k in all_keys:
        in_old = k in old
        in_new = k in new
        if in_new and not in_old:
            added[k] = new[k]
        elif in_old and not in_new:
            removed[k] = old[k]
        elif old[k] != new[k]:
            changed[k] = (old[k], new[k])

    return ConfigDiff(
        strategy_name=strategy_name,
        from_version=v_from.version,
        to_version=v_to.version,
        added=added,
        removed=removed,
        changed=changed,
    )
