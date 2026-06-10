"""
Tests for scalp scanner adverse-exit logic (_check_adverse_exit).

Verifies:
  1. Whipsaw (7+ direction flips in 10 bars) triggers force-exit of open scalp positions.
  2. Fewer than 7 flips → no exit.
  3. Non-scalp positions are ignored.
  4. Already-fired positions are not exited again.
  5. Positions for a different underlying are ignored.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_candles(directions: list[int]) -> list[dict]:
    """Build fake 1-min candles from a list of directions.
    +1 = green candle (close > open), -1 = red candle (close < open).
    E.g. [1, -1, 1, -1, ...] = alternating = choppy."""
    candles = []
    base = 100.0
    for d in directions:
        o = base
        c = base + (0.5 * d)
        candles.append({'open': o, 'close': c, 'high': max(o, c), 'low': min(o, c)})
    return candles


def _make_position(ticker: str, track_id: str = 'pos-1', mode: str = 'scalp') -> dict:
    return {
        'id': track_id,
        'ticket': {
            'trade_mode': mode,
            'underlying': ticker,
            'trading_symbol': f'{ticker}-CE',
            'entry': {'expected_premium_inr': 180.0},
        },
        'qty': 65,
    }


def _choppy_candles(n: int = 10) -> list[dict]:
    """Generate n candles that alternate direction every bar → max whipsaw."""
    return _make_candles([(-1)**i for i in range(n)])


def _trending_candles(n: int = 10) -> list[dict]:
    """Generate n candles all going same direction → no whipsaw."""
    return _make_candles([1] * n)


# ── Tests ─────────────────────────────────────────────────────────────────────

@patch('app.api.indmoney._ind_ltp', return_value=175.0)
@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_whipsaw_triggers_exit(mock_list, mock_exit, mock_ltp):
    """7+ direction flips in 10 bars should force-exit the scalp position."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^NSEI')
    mock_list.return_value = [pos]

    candles = _choppy_candles(10)  # 9 flips (every bar alternates)
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_called_once()
    call_args = mock_exit.call_args
    assert call_args[0][0] == 'pos-1'         # track_id
    assert call_args[0][1] == 'thesis_flip'    # exit reason
    assert 'pos-1' in _adverse_exit_fired      # marked as fired
    _adverse_exit_fired.clear()


@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_no_exit_when_trending(mock_list, mock_exit):
    """Fewer than 7 flips → no exit should fire."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^NSEI')
    mock_list.return_value = [pos]

    candles = _trending_candles(10)  # 0 flips
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_not_called()
    assert len(_adverse_exit_fired) == 0


@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_ignores_swing_positions(mock_list, mock_exit):
    """Swing positions should not be exited by adverse check."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^NSEI', mode='swing')
    mock_list.return_value = [pos]

    candles = _choppy_candles(10)
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_not_called()


@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_no_double_exit(mock_list, mock_exit):
    """Position already in _adverse_exit_fired should not be exited again."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()
    _adverse_exit_fired.add('pos-1')  # pre-mark as fired

    pos = _make_position('^NSEI', track_id='pos-1')
    mock_list.return_value = [pos]

    candles = _choppy_candles(10)
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_not_called()
    _adverse_exit_fired.clear()


@patch('app.api.indmoney._ind_ltp', return_value=175.0)
@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_ignores_different_underlying(mock_list, mock_exit, mock_ltp):
    """Positions for a different underlying should be ignored."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^BSESN')  # SENSEX position
    mock_list.return_value = [pos]

    candles = _choppy_candles(10)
    _check_adverse_exit(candles, '^NSEI')  # checking NIFTY

    mock_exit.assert_not_called()
    _adverse_exit_fired.clear()


@patch('app.api.indmoney._ind_ltp', return_value=175.0)
@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_exactly_6_flips_no_exit(mock_list, mock_exit, mock_ltp):
    """6 flips (just below threshold) should NOT trigger exit."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^NSEI')
    mock_list.return_value = [pos]

    # 7 candles alternating = 6 flips (indices 0-6, flips at 1,2,3,4,5,6)
    candles = _make_candles([1, -1, 1, -1, 1, -1, 1])
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_not_called()
    _adverse_exit_fired.clear()


@patch('app.api.indmoney._ind_ltp', return_value=175.0)
@patch('app.services.order_executor.try_auto_exit')
@patch('app.services.tracked_positions.list_tracked')
def test_exactly_7_flips_triggers_exit(mock_list, mock_exit, mock_ltp):
    """Exactly 7 flips should trigger exit."""
    from app.services.scalp_scanner import _check_adverse_exit, _adverse_exit_fired
    _adverse_exit_fired.clear()

    pos = _make_position('^NSEI')
    mock_list.return_value = [pos]

    # 8 candles alternating = 7 flips
    candles = _make_candles([1, -1, 1, -1, 1, -1, 1, -1])
    _check_adverse_exit(candles, '^NSEI')

    mock_exit.assert_called_once()
    _adverse_exit_fired.clear()
