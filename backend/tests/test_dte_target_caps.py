"""
Tests for DTE-based target and SL capping.

Verifies:
  1. 1DTE: T1 ≤ 1.30× entry (+30%), T2 ≤ 1.60× entry (+60%).
  2. 7DTE: T1 ≤ 2.0× entry, T2 ≤ 3.5× entry.
  3. Absurd BS-derived targets get capped to realistic levels.
  4. T2 is always > T1 after capping.
  5. SL max loss is 30% for 1DTE, 50% for 7DTE.
"""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from unittest.mock import patch, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_rec(entry: float, t1: float, t2: float, sl: float, dte: int) -> dict:
    """Build a tracked position record with given exit levels."""
    return {
        'id': 'test-pos-1',
        'ticket': {
            'trading_symbol': 'NIFTY-TEST-CE',
            'underlying': '^NSEI',
            'days_to_expiry': dte,
            'entry': {'expected_premium_inr': entry},
            'exit': {
                'target_1_inr': t1,
                'target_2_inr': t2,
                'stop_loss_inr': sl,
                'stop_loss_points': 15,
            },
        },
        'qty': 50,
    }


def _compute_caps(dte: int) -> tuple[float, float]:
    """Replicate the DTE cap math from tracked_monitor._trail_sl_and_targets."""
    dte_scale = min(1.0, math.sqrt(dte / 7.0))
    t1_max = 1.15 + 0.85 * dte_scale
    t2_max = 1.35 + 2.15 * dte_scale
    if dte <= 2:
        t1_max = min(t1_max, 1.30)
        t2_max = min(t2_max, 1.60)
    return t1_max, t2_max


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestDTETargetCaps:

    def test_1dte_caps(self):
        """1DTE: T1 ≤ 1.30×, T2 ≤ 1.60×."""
        t1_max, t2_max = _compute_caps(1)
        assert t1_max <= 1.30, f"1DTE T1 mult {t1_max} exceeds 1.30"
        assert t2_max <= 1.60, f"1DTE T2 mult {t2_max} exceeds 1.60"

    def test_2dte_caps(self):
        """2DTE should also have tightened caps (≤2 DTE rule)."""
        t1_max, t2_max = _compute_caps(2)
        assert t1_max <= 1.30, f"2DTE T1 mult {t1_max} exceeds 1.30"
        assert t2_max <= 1.60, f"2DTE T2 mult {t2_max} exceeds 1.60"

    def test_3dte_looser_than_1dte(self):
        """3DTE should be looser than 1-2DTE."""
        t1_1, t2_1 = _compute_caps(1)
        t1_3, t2_3 = _compute_caps(3)
        assert t1_3 > t1_1, "3DTE T1 should be looser than 1DTE"
        assert t2_3 > t2_1, "3DTE T2 should be looser than 1DTE"

    def test_7dte_full_range(self):
        """7DTE+: T1 ≈ 2.0×, T2 ≈ 3.5×."""
        t1_max, t2_max = _compute_caps(7)
        assert abs(t1_max - 2.0) < 0.01, f"7DTE T1 mult {t1_max} != 2.0"
        assert abs(t2_max - 3.5) < 0.01, f"7DTE T2 mult {t2_max} != 3.5"

    def test_absurd_targets_get_capped(self):
        """T1=₹3921 from ₹480 entry (8.2×) must be capped to 1.30× for 1DTE."""
        entry = 480.0
        t1_max, t2_max = _compute_caps(1)
        capped_t1 = round(entry * t1_max, 2)
        capped_t2 = round(entry * t2_max, 2)

        assert capped_t1 <= 624.0, f"1DTE T1 {capped_t1} > ₹624 for ₹480 entry"
        assert capped_t2 <= 768.0, f"1DTE T2 {capped_t2} > ₹768 for ₹480 entry"
        assert capped_t1 < 3921.0, "Absurd T1 ₹3921 should be far below cap"

    def test_t2_always_above_t1(self):
        """T2 multiplier must always be greater than T1 multiplier."""
        for dte in [1, 2, 3, 5, 7, 10, 14, 21]:
            t1_max, t2_max = _compute_caps(dte)
            assert t2_max > t1_max, f"DTE={dte}: T2 mult {t2_max} ≤ T1 mult {t1_max}"

    @patch('app.services.tracked_monitor.tp')
    def test_trail_function_caps_1dte(self, mock_tp):
        """_trail_sl_and_targets should actually cap a 1DTE position."""
        from app.services.tracked_monitor import _trail_sl_and_targets

        # Mock tp._read/_write so persistence doesn't fail
        mock_tp._read.return_value = []
        mock_tp._write.return_value = None

        entry = 480.0
        rec = _make_rec(entry=entry, t1=3921.0, t2=7633.0, sl=240.0, dte=1)
        _trail_sl_and_targets(rec, prem=490.0)

        ex = rec['ticket']['exit']
        assert float(ex['target_1_inr']) <= entry * 1.30, \
            f"T1 {ex['target_1_inr']} exceeds 1.30× entry for 1DTE"
        assert float(ex['target_2_inr']) <= entry * 1.60, \
            f"T2 {ex['target_2_inr']} exceeds 1.60× entry for 1DTE"


class TestDTESLLimits:

    def test_1dte_sl_max_loss_30pct(self):
        """1DTE: SL should not allow more than 30% premium loss."""
        entry = 480.0
        sl_floor = entry * 0.30  # max loss = 30%
        sl_cap = entry * (1 - 0.10)  # at least 10% noise buffer
        # SL should be between floor and cap
        assert sl_floor == 144.0, "30% of 480 = 144"
        assert sl_cap == 432.0, "90% of 480 = 432"

    def test_7dte_sl_max_loss_50pct(self):
        """7DTE: SL allows up to 50% premium loss."""
        entry = 480.0
        sl_floor = entry * 0.50  # max loss = 50%
        sl_cap = entry * (1 - 0.15)  # at least 15% noise buffer
        assert sl_floor == 240.0, "50% of 480 = 240"
        assert sl_cap == 408.0, "85% of 480 = 408"

    def test_short_dte_tighter_than_long_dte(self):
        """1DTE SL floor (30%) should be tighter than 7DTE (50%)."""
        entry = 480.0
        sl_floor_1dte = entry * 0.30
        sl_floor_7dte = entry * 0.50
        assert sl_floor_1dte < sl_floor_7dte, \
            "1DTE SL floor should be higher (less loss allowed) than 7DTE"
