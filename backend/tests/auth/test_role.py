"""VEGA_ROLE: only a server runs the engines."""

import pytest
from fastapi.testclient import TestClient


def test_default_role_is_server():
    from app.config import Settings
    assert Settings(_env_file=None).vega_role == 'server'


def test_consumer_role_is_accepted_and_junk_rejected():
    from app.config import Settings
    assert Settings(_env_file=None, vega_role='consumer').vega_role == 'consumer'
    with pytest.raises(ValueError):
        Settings(_env_file=None, vega_role='both')


def test_production_defaults_are_the_safe_ones():
    from app.config import Settings
    s = Settings(_env_file=None)
    assert s.vega_auth_disabled is False
    assert s.vega_api_key == ''
    assert s.vega_rate_limit_per_min > 0 and s.vega_auth_max_failures <= 10 and s.vega_auth_lockout_sec >= 300


def test_consumer_starts_no_engines(monkeypatch):
    """With VEGA_ROLE=consumer the lifespan must return before any scanner or scheduler starts."""
    import app.engines.fo_scanner as fo
    import app.engines.scalp_scanner as sc
    import app.main as m
    started = []
    monkeypatch.setattr(m.settings, 'vega_role', 'consumer')
    monkeypatch.setattr(m, 'get_scheduler', lambda: type('S', (), {'start': lambda s: started.append('scheduler'),
                                                                    'daily': lambda *a, **k: None,
                                                                    'stop': lambda s: None})())
    monkeypatch.setattr(fo, 'start', lambda: started.append('fo'))
    monkeypatch.setattr(sc, 'start', lambda: started.append('scalp'))
    with TestClient(m.app):
        pass
    assert started == []
