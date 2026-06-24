"""Tests for domain exception hierarchy."""

import pytest
from app.domain.exceptions import (
    VegaError, TradingError, InvalidStateTransition,
    RiskError, BudgetExceeded, KillSwitchActive,
    BrokerError, BrokerConnectionError,
    MarketDataError, ConfigError,
)


class TestExceptionHierarchy:
    def test_all_inherit_from_vega_error(self):
        for exc_cls in [TradingError, RiskError, BrokerError, MarketDataError, ConfigError]:
            assert issubclass(exc_cls, VegaError)

    def test_trading_subtypes(self):
        assert issubclass(InvalidStateTransition, TradingError)

    def test_risk_subtypes(self):
        for cls in [BudgetExceeded, KillSwitchActive]:
            assert issubclass(cls, RiskError)

    def test_broker_subtypes(self):
        assert issubclass(BrokerConnectionError, BrokerError)

    def test_invalid_state_transition_fields(self):
        e = InvalidStateTransition('open', 'pending')
        assert e.current == 'open'
        assert e.target == 'pending'
        assert 'open' in str(e)
        assert e.code == 'InvalidStateTransition'

    def test_vega_error_fields(self):
        e = VegaError('something broke', detail='extra info', code='CUSTOM')
        assert e.message == 'something broke'
        assert e.detail == 'extra info'
        assert e.code == 'CUSTOM'

    def test_catch_by_category(self):
        """Handlers can catch broad categories."""
        with pytest.raises(VegaError):
            raise BudgetExceeded('over budget')
        with pytest.raises(RiskError):
            raise KillSwitchActive('killed')
        with pytest.raises(TradingError):
            raise InvalidStateTransition('closed', 'open')
