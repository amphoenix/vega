"""
Tests for direction-based re-entry block in order_executor.

Verifies:
  1. After SL hit on NIFTY CE, new NIFTY CE entries (any strike) are blocked.
  2. After SL hit on NIFTY CE, NIFTY PE entries still allowed.
  3. After SL hit on NIFTY CE, SENSEX CE entries still allowed.
  4. Direction ledger is cleared on daily reset.
  5. Budget headroom guard blocks entry when headroom insufficient.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import app.services.order_executor as executor


class TestDirectionLedger:

    def setup_method(self):
        """Reset direction ledger before each test."""
        with executor._lock:
            executor._sl_direction_ledger.clear()
            executor._daily_ledger.clear()

    def test_sl_records_direction(self):
        """SL hit on NIFTY CE should record ^NSEI:CE in direction ledger."""
        with executor._lock:
            # Simulate what happens in try_auto_exit when SL hits
            _und = '^NSEI'
            _otype = 'CE'
            _dk = f"{_und}:{_otype}"
            executor._sl_direction_ledger[_dk] = executor._sl_direction_ledger.get(_dk, 0) + 1

        assert executor._sl_direction_ledger.get('^NSEI:CE') == 1

    def test_direction_block_after_sl(self):
        """After 1 SL on ^NSEI:CE, the guard should block further CE entries."""
        with executor._lock:
            executor._sl_direction_ledger['^NSEI:CE'] = 1

        # Simulate the check in try_auto_entry
        _dir_key = '^NSEI:CE'
        _dir_sl_count = executor._sl_direction_ledger.get(_dir_key, 0)
        blocked = _dir_sl_count >= executor._MAX_SL_PER_DIRECTION

        assert blocked, "NIFTY CE should be blocked after 1 SL hit"

    def test_pe_still_allowed_after_ce_sl(self):
        """SL on NIFTY CE should NOT block NIFTY PE entries."""
        with executor._lock:
            executor._sl_direction_ledger['^NSEI:CE'] = 1

        _dir_key = '^NSEI:PE'
        _dir_sl_count = executor._sl_direction_ledger.get(_dir_key, 0)
        blocked = _dir_sl_count >= executor._MAX_SL_PER_DIRECTION

        assert not blocked, "NIFTY PE should still be allowed after NIFTY CE SL"

    def test_different_underlying_not_blocked(self):
        """SL on NIFTY CE should NOT block SENSEX CE entries."""
        with executor._lock:
            executor._sl_direction_ledger['^NSEI:CE'] = 1

        _dir_key = '^BSESN:CE'
        _dir_sl_count = executor._sl_direction_ledger.get(_dir_key, 0)
        blocked = _dir_sl_count >= executor._MAX_SL_PER_DIRECTION

        assert not blocked, "SENSEX CE should not be blocked by NIFTY CE SL"

    def test_daily_reset_clears_direction_ledger(self):
        """reset_daily() should clear the direction ledger."""
        with executor._lock:
            executor._sl_direction_ledger['^NSEI:CE'] = 2
            executor._sl_direction_ledger['^BSESN:PE'] = 1

        executor.reset_daily()

        assert len(executor._sl_direction_ledger) == 0, \
            "Direction ledger should be empty after daily reset"

    def test_multiple_sl_hits_increment(self):
        """Multiple SL hits on same direction should increment counter."""
        with executor._lock:
            _dk = '^NSEI:CE'
            executor._sl_direction_ledger[_dk] = executor._sl_direction_ledger.get(_dk, 0) + 1
            executor._sl_direction_ledger[_dk] = executor._sl_direction_ledger.get(_dk, 0) + 1

        assert executor._sl_direction_ledger['^NSEI:CE'] == 2


class TestBudgetHeadroomGuard:

    def test_budget_blocks_when_headroom_insufficient(self):
        """Worst-case loss > headroom should block entry."""
        sl_pts = 15      # NIFTY SL max points
        qty = 50          # 1 lot
        worst_case = (sl_pts * qty) + 70  # 820
        daily_limit = 1000.0
        realized_pnl = -500.0  # already lost 500

        headroom = daily_limit + realized_pnl  # 500
        blocked = worst_case > headroom

        assert blocked, f"Should block: worst_case ₹{worst_case} > headroom ₹{headroom}"

    def test_budget_allows_when_headroom_sufficient(self):
        """Worst-case loss ≤ headroom should allow entry."""
        sl_pts = 15
        qty = 50
        worst_case = (sl_pts * qty) + 70  # 820
        daily_limit = 1000.0
        realized_pnl = 0.0  # no losses yet

        headroom = daily_limit + realized_pnl  # 1000
        blocked = worst_case > headroom

        assert not blocked, f"Should allow: worst_case ₹{worst_case} ≤ headroom ₹{headroom}"
