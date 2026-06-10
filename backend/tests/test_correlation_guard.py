"""
Tests for cross-index correlation guard in fo_scanner._technical_cio.

Verifies:
  1. SENSEX signal is SKIPPED when NIFTY direction disagrees.
  2. SENSEX signal PASSES when NIFTY direction agrees.
  3. NIFTY is never blocked by correlation (it has no reference ticker).
  4. When NIFTY hasn't been scanned yet, SENSEX still passes (no reference data).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock
import app.services.fo_scanner as scanner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ta(st_dir: int, adx: float = 25.0, price: float = 23500.0) -> dict:
    """Build a minimal technicals dict that passes _technical_cio pre-filter."""
    return {
        'supertrend_dir': st_dir,
        'adx': adx,
        'adx_plus_di': 20.0 if st_dir == 1 else 10.0,
        'adx_minus_di': 10.0 if st_dir == 1 else 20.0,
        'rsi': 60.0 if st_dir == 1 else 40.0,
        'ema20': price,
        'ema50': price * 0.99 if st_dir == 1 else price * 1.01,
        'atr': price * 0.01,
        'macd_cross': 'BULLISH' if st_dir == 1 else 'BEARISH',
        'macd_hist': 0.5 if st_dir == 1 else -0.5,
        'cpr_type': 'narrow',
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCorrelationGuard:

    def setup_method(self):
        """Reset state before each test."""
        scanner._latest_st_dir.clear()

    def test_sensex_blocked_when_nifty_disagrees(self):
        """SENSEX BEAR should be skipped when NIFTY is BULL."""
        # First: register NIFTY as bullish
        scanner._latest_st_dir['^NSEI'] = 1  # BULL

        # Now run SENSEX as bearish
        ta = _make_ta(st_dir=-1, price=75000.0)  # BEAR
        result = scanner._technical_cio('^BSESN', {
            'price': 75000.0, 'technicals': ta, 'company_name': 'SENSEX'
        })

        assert result is None, "SENSEX BEAR should be blocked when NIFTY is BULL"

    def test_sensex_passes_when_nifty_agrees(self):
        """SENSEX BULL should pass when NIFTY is also BULL."""
        scanner._latest_st_dir['^NSEI'] = 1  # BULL

        ta = _make_ta(st_dir=1, price=75000.0)  # BULL
        result = scanner._technical_cio('^BSESN', {
            'price': 75000.0, 'technicals': ta, 'company_name': 'SENSEX'
        })

        assert result is not None, "SENSEX BULL should pass when NIFTY is BULL"
        assert result['instrument_type'] == 'CE'

    def test_nifty_never_blocked(self):
        """NIFTY has no reference ticker — should always pass correlation check."""
        # Even if SENSEX is opposite
        scanner._latest_st_dir['^BSESN'] = -1  # BEAR

        ta = _make_ta(st_dir=1)  # BULL
        result = scanner._technical_cio('^NSEI', {
            'price': 23500.0, 'technicals': ta, 'company_name': 'NIFTY 50'
        })

        assert result is not None, "NIFTY should never be blocked by correlation guard"
        assert result['instrument_type'] == 'CE'

    def test_sensex_passes_when_nifty_not_scanned(self):
        """If NIFTY hasn't been scanned yet, SENSEX should still pass."""
        # _latest_st_dir is empty — no reference data
        ta = _make_ta(st_dir=-1, price=75000.0)  # BEAR
        result = scanner._technical_cio('^BSESN', {
            'price': 75000.0, 'technicals': ta, 'company_name': 'SENSEX'
        })

        assert result is not None, "SENSEX should pass when NIFTY not yet scanned"
        assert result['instrument_type'] == 'PE'

    def test_both_bear_passes(self):
        """Both indices BEAR → SENSEX PE should pass."""
        scanner._latest_st_dir['^NSEI'] = -1  # BEAR

        ta = _make_ta(st_dir=-1, price=75000.0)  # BEAR
        result = scanner._technical_cio('^BSESN', {
            'price': 75000.0, 'technicals': ta, 'company_name': 'SENSEX'
        })

        assert result is not None
        assert result['instrument_type'] == 'PE'

    def test_nifty_direction_stored(self):
        """After scanning NIFTY, its direction should be in _latest_st_dir."""
        ta = _make_ta(st_dir=1)
        scanner._technical_cio('^NSEI', {
            'price': 23500.0, 'technicals': ta, 'company_name': 'NIFTY 50'
        })

        assert scanner._latest_st_dir.get('^NSEI') == 1
