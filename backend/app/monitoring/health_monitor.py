"""
Health Monitor — unified system health tracking.

Tracks the health of every subsystem (broker, WebSocket, scanner, etc.)
and exposes a single system-wide health status + per-component breakdown.

Design:
    - Components register themselves with a name and heartbeat interval.
    - Each heartbeat updates the component's last-seen timestamp.
    - A component is DEGRADED if its last heartbeat exceeds 2x its interval.
    - A component is DOWN if its last heartbeat exceeds 5x its interval.
    - System health is the worst component health.

Pure domain — no I/O. Relies on callers to invoke heartbeat() periodically.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..shared.time import monotonic
from enum import Enum
from typing import Any


class HealthStatus(str, Enum):
    HEALTHY   = 'healthy'
    DEGRADED  = 'degraded'
    DOWN      = 'down'
    UNKNOWN   = 'unknown'


@dataclass
class ComponentHealth:
    """Health state of a single subsystem."""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_heartbeat: float = 0.0       # monotonic timestamp
    heartbeat_interval: float = 10.0  # expected seconds between heartbeats
    error_count: int = 0
    last_error: str = ''
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Derived ─────────────────────────────────────────────────────

    @property
    def seconds_since_heartbeat(self) -> float:
        if self.last_heartbeat == 0:
            return float('inf')
        return monotonic() - self.last_heartbeat

    def compute_status(self) -> HealthStatus:
        """Derive status from heartbeat freshness."""
        elapsed = self.seconds_since_heartbeat
        if elapsed == float('inf'):
            return HealthStatus.UNKNOWN
        if elapsed <= self.heartbeat_interval * 2:
            return HealthStatus.HEALTHY
        if elapsed <= self.heartbeat_interval * 5:
            return HealthStatus.DEGRADED
        return HealthStatus.DOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'status': self.status.value,
            'seconds_since_heartbeat': round(self.seconds_since_heartbeat, 1),
            'heartbeat_interval': self.heartbeat_interval,
            'error_count': self.error_count,
            'last_error': self.last_error,
            'metadata': self.metadata,
        }


@dataclass
class SystemHealth:
    """Aggregate health across all components."""
    status: HealthStatus
    components: list[ComponentHealth]
    uptime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            'status': self.status.value,
            'uptime_seconds': round(self.uptime_seconds, 1),
            'components': [c.to_dict() for c in self.components],
        }


# ── Health check function type ───────────────────────────────────────────────

HealthCheckFn = callable  # () -> (HealthStatus, str)


class HealthMonitor:
    """Central health aggregator — thread-safe.

    Usage:
        monitor = HealthMonitor()
        monitor.register('broker', interval=5.0)
        monitor.register('websocket', interval=2.0)

        # In each subsystem's loop:
        monitor.heartbeat('broker')
        monitor.heartbeat('websocket')

        # On error:
        monitor.record_error('broker', 'connection timeout')

        # Query:
        health = monitor.system_health()
    """

    def __init__(self) -> None:
        self._components: dict[str, ComponentHealth] = {}
        self._lock = threading.Lock()
        self._start_time = monotonic()
        self._health_checks: dict[str, HealthCheckFn] = {}

    # ── Registration ─────────────────────────────────────────────────

    def register(
        self,
        name: str,
        interval: float = 10.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a new component to monitor."""
        with self._lock:
            self._components[name] = ComponentHealth(
                name=name,
                heartbeat_interval=interval,
                metadata=metadata or {},
            )

    def register_check(self, name: str, check_fn: HealthCheckFn) -> None:
        """Register an active health check function for a component.

        The check function is called during system_health() to get
        real-time status instead of relying only on heartbeats.
        """
        with self._lock:
            self._health_checks[name] = check_fn

    def unregister(self, name: str) -> None:
        with self._lock:
            self._components.pop(name, None)
            self._health_checks.pop(name, None)

    # ── Heartbeat ────────────────────────────────────────────────────

    def heartbeat(self, name: str, metadata: dict[str, Any] | None = None) -> None:
        """Record a heartbeat from a component."""
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return
            comp.last_heartbeat = monotonic()
            comp.status = HealthStatus.HEALTHY
            if metadata:
                comp.metadata.update(metadata)

    def record_error(self, name: str, error: str) -> None:
        """Record an error from a component."""
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return
            comp.error_count += 1
            comp.last_error = error

    def clear_errors(self, name: str) -> None:
        """Reset error count for a component (e.g. after recovery)."""
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return
            comp.error_count = 0
            comp.last_error = ''

    # ── Queries ──────────────────────────────────────────────────────

    def component_health(self, name: str) -> ComponentHealth | None:
        with self._lock:
            comp = self._components.get(name)
            if comp is None:
                return None
            # Run active check if registered
            check_fn = self._health_checks.get(name)
            if check_fn:
                try:
                    status, msg = check_fn()
                    comp.status = status
                    if msg:
                        comp.metadata['check_message'] = msg
                except Exception as e:
                    comp.status = HealthStatus.DOWN
                    comp.last_error = str(e)
            else:
                comp.status = comp.compute_status()
            return comp

    def system_health(self) -> SystemHealth:
        """Compute aggregate system health."""
        with self._lock:
            components = list(self._components.values())

        # Run active checks and compute statuses
        for comp in components:
            check_fn = self._health_checks.get(comp.name)
            if check_fn:
                try:
                    status, msg = check_fn()
                    comp.status = status
                    if msg:
                        comp.metadata['check_message'] = msg
                except Exception as e:
                    comp.status = HealthStatus.DOWN
                    comp.last_error = str(e)
            else:
                comp.status = comp.compute_status()

        # Aggregate: worst status wins
        if not components:
            overall = HealthStatus.UNKNOWN
        elif any(c.status == HealthStatus.DOWN for c in components):
            overall = HealthStatus.DOWN
        elif any(c.status == HealthStatus.DEGRADED for c in components):
            overall = HealthStatus.DEGRADED
        elif any(c.status == HealthStatus.UNKNOWN for c in components):
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.HEALTHY

        return SystemHealth(
            status=overall,
            components=components,
            uptime_seconds=monotonic() - self._start_time,
        )

    @property
    def component_names(self) -> list[str]:
        with self._lock:
            return list(self._components.keys())

    @property
    def is_healthy(self) -> bool:
        return self.system_health().status == HealthStatus.HEALTHY

    def reset(self) -> None:
        """Clear all components (used in tests)."""
        with self._lock:
            self._components.clear()
            self._health_checks.clear()
            self._start_time = monotonic()
