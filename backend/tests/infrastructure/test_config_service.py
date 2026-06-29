"""
Tests for Config Service — runtime configuration manager.
"""

import pytest
from app.infrastructure.config.config_service import (
    ConfigService, ConfigSnapshot, _is_secret_key, _mask,
)


class _FakeSettings:
    """Plain settings object (no MagicMock surprises)."""
    def validate_llm(self):
        return []


def _mock_settings(**overrides):
    """Create a fake Settings object with sensible defaults."""
    defaults = dict(
        daily_loss_limit_inr=2500.0,
        max_trades_per_day=10,
        max_consecutive_losses=3,
        cooldown_minutes=30,
        max_same_underlying_trades=2,
        max_open_trades=5,
        allow_outside_safe_hours=False,
        fo_max_lots_per_trade=1,
        swing_capital_inr=100000.0,
        llm_provider='openai',
        llm_api_key='sk-test-key-1234567890',
        llm_api_key_2='',
        llm_api_key_3='',
        v2_port=47293,
        debug=False,
    )
    defaults.update(overrides)
    s = _FakeSettings()
    for k, v in defaults.items():
        setattr(s, k, v)
    s.model_fields = defaults.keys()
    return s


class TestCoreAccess:
    def test_get_from_settings(self):
        svc = ConfigService(_mock_settings())
        assert svc.get('daily_loss_limit_inr') == 2500.0

    def test_get_missing_returns_default(self):
        svc = ConfigService(_mock_settings())
        assert svc.get('nonexistent', 42) == 42

    def test_get_int(self):
        svc = ConfigService(_mock_settings())
        assert svc.get_int('max_trades_per_day') == 10

    def test_get_float(self):
        svc = ConfigService(_mock_settings())
        assert svc.get_float('daily_loss_limit_inr') == 2500.0

    def test_get_bool(self):
        svc = ConfigService(_mock_settings())
        assert svc.get_bool('debug') is False

    def test_get_str(self):
        svc = ConfigService(_mock_settings())
        assert svc.get_str('llm_provider') == 'openai'


class TestOverrides:
    def test_override_takes_precedence(self):
        svc = ConfigService(_mock_settings())
        assert svc.get('max_trades_per_day') == 10
        svc.override('max_trades_per_day', 5)
        assert svc.get('max_trades_per_day') == 5

    def test_clear_override_reverts(self):
        svc = ConfigService(_mock_settings())
        svc.override('max_trades_per_day', 5)
        svc.clear_override('max_trades_per_day')
        assert svc.get('max_trades_per_day') == 10

    def test_clear_all_overrides(self):
        svc = ConfigService(_mock_settings())
        svc.override('max_trades_per_day', 5)
        svc.override('daily_loss_limit_inr', 1000.0)
        svc.clear_all_overrides()
        assert svc.overrides == {}
        assert svc.get('max_trades_per_day') == 10

    def test_overrides_property(self):
        svc = ConfigService(_mock_settings())
        svc.override('debug', True)
        assert svc.overrides == {'debug': True}


class TestDomainConfigBuilders:
    def test_build_supervisor_config(self):
        svc = ConfigService(_mock_settings())
        sc = svc.build_supervisor_config()
        assert sc.daily_loss_limit == 2500.0
        assert sc.max_trades_per_day == 10
        assert sc.max_consecutive_losses == 3

    def test_supervisor_config_respects_overrides(self):
        svc = ConfigService(_mock_settings())
        svc.override('daily_loss_limit_inr', 1000.0)
        sc = svc.build_supervisor_config()
        assert sc.daily_loss_limit == 1000.0

    def test_build_kill_switch_config(self):
        svc = ConfigService(_mock_settings())
        kc = svc.build_kill_switch_config()
        assert kc.daily_loss_soft == 2000.0
        assert kc.daily_loss_hard == 4000.0

    def test_kill_switch_config_with_override(self):
        svc = ConfigService(_mock_settings())
        svc.override('ks_daily_loss_soft', 500.0)
        kc = svc.build_kill_switch_config()
        assert kc.daily_loss_soft == 500.0


class TestValidation:
    def test_valid_config(self):
        svc = ConfigService(_mock_settings())
        errors = svc.validate()
        assert errors == []

    def test_invalid_lots(self):
        svc = ConfigService(_mock_settings(fo_max_lots_per_trade=0))
        errors = svc.validate()
        assert any('fo_max_lots_per_trade' in e for e in errors)

    def test_invalid_capital(self):
        svc = ConfigService(_mock_settings(swing_capital_inr=-100))
        errors = svc.validate()
        assert any('swing_capital_inr' in e for e in errors)


class TestExport:
    def test_export_masks_secrets(self):
        svc = ConfigService(_mock_settings())
        d = svc.export()
        assert '***' in d['llm_api_key']  # masked
        assert d['max_trades_per_day'] == 10  # plain value

    def test_export_empty_secret(self):
        svc = ConfigService(_mock_settings(llm_api_key=''))
        d = svc.export()
        assert d['llm_api_key'] == ''


class TestSnapshots:
    def test_initial_snapshot(self):
        svc = ConfigService(_mock_settings())
        assert len(svc.snapshots) == 1
        assert svc.snapshots[0].source == 'env'

    def test_override_creates_snapshot(self):
        svc = ConfigService(_mock_settings())
        svc.override('debug', True)
        assert len(svc.snapshots) == 2
        assert svc.snapshots[1].source == 'override'


class TestHelpers:
    def test_is_secret_key(self):
        assert _is_secret_key('llm_api_key')
        assert _is_secret_key('aws_secret_access_key')
        assert _is_secret_key('indmoney_access_token')
        assert not _is_secret_key('max_trades_per_day')
        assert not _is_secret_key('debug')

    def test_mask_short(self):
        assert _mask('abc') == '***'

    def test_mask_long(self):
        m = _mask('sk-1234567890abcdef')
        assert m.startswith('sk-1')
        assert '***' in m
        assert m.endswith('cdef')
