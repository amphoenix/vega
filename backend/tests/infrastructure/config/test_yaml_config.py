"""Tests for YAML config loading and ConfigService YAML integration."""

import pytest

from app.infrastructure.config.config_service import _YAML_CACHE, ConfigService, load_yaml, reload_yaml


class TestYAMLLoader:
    def setup_method(self):
        _YAML_CACHE.clear()

    def test_load_brokers_yaml(self):
        data = load_yaml('brokers')
        assert 'active_broker' in data
        assert 'brokers' in data
        assert 'dhan' in data['brokers']
        assert 'indmoney' in data['brokers']

    def test_load_strategies_yaml(self):
        data = load_yaml('strategies')
        assert 'strategies' in data
        assert 'swing_ai' in data['strategies']
        assert 'nifty_scalp' in data['strategies']
        assert 'ab_tests' in data

    def test_load_exchanges_yaml(self):
        data = load_yaml('exchanges')
        assert 'crypto' in data
        assert 'active_exchange' in data['crypto']
        assert 'binance' in data['crypto']['exchanges']

    def test_load_risk_yaml(self):
        data = load_yaml('risk')
        assert 'supervisor' in data
        assert 'kill_switch' in data
        assert 'position_sizing' in data

    def test_caching(self):
        data1 = load_yaml('brokers')
        data2 = load_yaml('brokers')
        assert data1 is data2  # same object = cached

    def test_reload(self):
        data1 = load_yaml('brokers')
        data2 = reload_yaml('brokers')
        assert data1 is not data2  # different object = reloaded

    def test_missing_file(self):
        data = load_yaml('nonexistent_config_file')
        assert data == {}


class TestConfigServiceYAML:
    @pytest.fixture
    def svc(self):
        _YAML_CACHE.clear()
        return ConfigService()

    def test_get_active_broker(self, svc):
        name = svc.get_active_broker_name()
        assert name == 'dhan'  # default from YAML

    def test_get_broker_config(self, svc):
        config = svc.get_broker_config('dhan')
        # New YAML format: product_type lives under 'defaults'
        defaults = config.get('defaults', {})
        assert defaults.get('product_type') == 'INTRADAY'

    def test_get_strategy_config(self, svc):
        config = svc.get_strategy_config('swing_ai')
        assert config.get('market') == 'indian_fo'
        assert config.get('enabled') is True

    def test_get_enabled_strategies(self, svc):
        enabled = svc.get_enabled_strategies()
        assert 'swing_ai' in enabled
        assert 'nifty_scalp' in enabled

    def test_get_ab_tests(self, svc):
        tests = svc.get_ab_tests()
        assert 'swing_ema_test' in tests

    def test_get_enabled_ab_tests(self, svc):
        enabled = svc.get_enabled_ab_tests()
        # swing_ema_test is disabled by default in YAML
        assert 'swing_ema_test' not in enabled

    def test_get_active_crypto_exchange(self, svc):
        ex = svc.get_active_crypto_exchange()
        assert ex == 'binance'

    def test_get_exchange_config(self, svc):
        config = svc.get_exchange_config('deribit')
        assert config.get('default_type') == 'option'

    def test_get_risk_config(self, svc):
        risk = svc.get_risk_config()
        assert 'supervisor' in risk

    def test_get_risk_section(self, svc):
        supervisor = svc.get_risk_config('supervisor')
        assert supervisor.get('daily_loss_limit_inr') == 2500.0
