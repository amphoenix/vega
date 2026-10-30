"""ATM IV estimate must find the contract through the broker's own instrument list (broker-agnostic)."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from app.domain.services import greeks as gk
from app.domain.services import option_planner as op
from app.shared.time import today_ist

BASE = 'SENSEX'
SPOT = 74500.0
ATM = 74500
TRUE_IV = 0.22


def _expiry():
    return today_ist() + timedelta(days=7)


def _inst(symbol: str, expiry, strike=ATM, opt='CE'):
    return {
        'TRADING_SYMBOL': symbol, 'OPTION_TYPE': opt, 'EXPIRY_DATE': expiry.isoformat(),
        'STRIKE_PRICE': str(strike), 'LOT_SIZE': '20', 'EXCH': 'BSE', 'SECURITY_ID': '1134477',
    }


def _broker(symbol: str, expiry, ltp_for: dict):
    broker = MagicMock()
    broker.load_instruments.return_value = [_inst(symbol, expiry)]
    broker.get_ltp.side_effect = lambda sym, **kw: ltp_for.get(sym)
    return broker


def test_iv_comes_from_market_price_of_the_brokers_own_contract_name():
    exp = _expiry()
    name = 'SENSEX-XX-2026-74500-CE'   # deliberately NOT the Dhan-style {base}{YYMON}{strike}{CE}
    dte = (exp - today_ist()).days
    premium = gk.bs_price(SPOT, ATM, dte / 365.0, gk.RISK_FREE_RATE, TRUE_IV, 'CE')
    broker = _broker(name, exp, {name: premium})
    with patch.object(op, '_get_broker', return_value=broker):
        iv = op._atm_iv_estimate(BASE, SPOT, exp, 'CE')
    assert abs(iv - TRUE_IV) < 0.01
    asked = [c.args[0] for c in broker.get_ltp.call_args_list]
    assert asked == [name]


def test_falls_back_to_default_iv_when_contract_not_in_master():
    exp = _expiry()
    broker = MagicMock()
    broker.load_instruments.return_value = []
    with patch.object(op, '_get_broker', return_value=broker):
        iv = op._atm_iv_estimate(BASE, SPOT, exp, 'CE')
    assert iv == gk.default_iv(BASE)
    broker.get_ltp.assert_not_called()


def test_falls_back_to_default_iv_when_no_live_price():
    exp = _expiry()
    name = 'SENSEX-XX-2026-74500-CE'
    broker = _broker(name, exp, {})   # get_ltp -> None
    with patch.object(op, '_get_broker', return_value=broker):
        iv = op._atm_iv_estimate(BASE, SPOT, exp, 'CE')
    assert iv == gk.default_iv(BASE)
