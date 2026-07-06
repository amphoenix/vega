"""
Tests for monitoring/health_monitor.py — HealthMonitor.
"""

import time

from app.monitoring.health_monitor import (
    ComponentHealth,
    HealthMonitor,
    HealthStatus,
)


class TestComponentHealth:
    def test_initial_unknown(self):
        c = ComponentHealth(name='broker')
        assert c.compute_status() == HealthStatus.UNKNOWN

    def test_healthy_after_heartbeat(self):
        c = ComponentHealth(name='broker', heartbeat_interval=10.0)
        c.last_heartbeat = time.monotonic()
        assert c.compute_status() == HealthStatus.HEALTHY

    def test_degraded_after_2x_interval(self):
        c = ComponentHealth(name='broker', heartbeat_interval=0.01)
        c.last_heartbeat = time.monotonic() - 0.03  # 3x interval
        assert c.compute_status() == HealthStatus.DEGRADED

    def test_down_after_5x_interval(self):
        c = ComponentHealth(name='broker', heartbeat_interval=0.01)
        c.last_heartbeat = time.monotonic() - 0.06  # 6x interval
        assert c.compute_status() == HealthStatus.DOWN

    def test_to_dict(self):
        c = ComponentHealth(name='ws', heartbeat_interval=5.0)
        d = c.to_dict()
        assert d['name'] == 'ws'
        assert 'status' in d
        assert 'error_count' in d


class TestHealthMonitor:
    def test_register_and_names(self):
        m = HealthMonitor()
        m.register('broker', interval=5.0)
        m.register('websocket', interval=2.0)
        assert set(m.component_names) == {'broker', 'websocket'}

    def test_heartbeat_sets_healthy(self):
        m = HealthMonitor()
        m.register('broker', interval=10.0)
        m.heartbeat('broker')
        c = m.component_health('broker')
        assert c.status == HealthStatus.HEALTHY

    def test_heartbeat_nonexistent_ignored(self):
        m = HealthMonitor()
        m.heartbeat('ghost')  # should not raise

    def test_record_error(self):
        m = HealthMonitor()
        m.register('broker')
        m.record_error('broker', 'timeout')
        c = m.component_health('broker')
        assert c.error_count == 1
        assert c.last_error == 'timeout'

    def test_clear_errors(self):
        m = HealthMonitor()
        m.register('broker')
        m.record_error('broker', 'timeout')
        m.clear_errors('broker')
        c = m.component_health('broker')
        assert c.error_count == 0
        assert c.last_error == ''

    def test_system_health_all_healthy(self):
        m = HealthMonitor()
        m.register('a', interval=10.0)
        m.register('b', interval=10.0)
        m.heartbeat('a')
        m.heartbeat('b')
        h = m.system_health()
        assert h.status == HealthStatus.HEALTHY

    def test_system_health_one_down(self):
        m = HealthMonitor()
        m.register('a', interval=0.001)
        m.register('b', interval=10.0)
        m.heartbeat('a')  # will become DOWN quickly
        m.heartbeat('b')
        time.sleep(0.01)  # let a expire
        h = m.system_health()
        assert h.status in (HealthStatus.DOWN, HealthStatus.DEGRADED)

    def test_system_health_empty(self):
        m = HealthMonitor()
        h = m.system_health()
        assert h.status == HealthStatus.UNKNOWN

    def test_system_health_unknown_degrades(self):
        m = HealthMonitor()
        m.register('a', interval=10.0)
        # No heartbeat → UNKNOWN
        h = m.system_health()
        assert h.status == HealthStatus.DEGRADED

    def test_unregister(self):
        m = HealthMonitor()
        m.register('x')
        m.unregister('x')
        assert 'x' not in m.component_names

    def test_is_healthy(self):
        m = HealthMonitor()
        m.register('a', interval=10.0)
        m.heartbeat('a')
        assert m.is_healthy

    def test_uptime(self):
        m = HealthMonitor()
        time.sleep(0.01)
        h = m.system_health()
        assert h.uptime_seconds >= 0.01

    def test_system_health_to_dict(self):
        m = HealthMonitor()
        m.register('a', interval=10.0)
        m.heartbeat('a')
        d = m.system_health().to_dict()
        assert d['status'] == 'healthy'
        assert len(d['components']) == 1

    def test_register_check(self):
        m = HealthMonitor()
        m.register('custom')
        m.register_check('custom', lambda: (HealthStatus.HEALTHY, 'all good'))
        c = m.component_health('custom')
        assert c.status == HealthStatus.HEALTHY
        assert c.metadata.get('check_message') == 'all good'

    def test_register_check_error(self):
        m = HealthMonitor()
        m.register('bad')
        m.register_check('bad', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
        c = m.component_health('bad')
        assert c.status == HealthStatus.DOWN

    def test_heartbeat_metadata(self):
        m = HealthMonitor()
        m.register('ws')
        m.heartbeat('ws', metadata={'reconnects': 3})
        c = m.component_health('ws')
        assert c.metadata['reconnects'] == 3

    def test_reset(self):
        m = HealthMonitor()
        m.register('a')
        m.reset()
        assert m.component_names == []
