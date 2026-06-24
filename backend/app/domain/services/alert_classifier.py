"""
Alert Classifier — classify a position's alert status from current premium.

Pure domain service — no I/O.

Zones (for a long options position where lower premium = worse):
  SL_HIT       premium <= stop_loss
  NEAR_SL      premium within 20% of SL distance from entry
  SAFE         between NEAR_SL and NEAR_T1
  NEAR_T1      premium within 90% of T1 distance from entry
  PAST_T1      premium >= target_1
  NEAR_T2      premium within 90% of T2 distance from entry
  PAST_T2      premium >= target_2
"""

from __future__ import annotations

from ..entities.position import Position, AlertStatus


def classify_alert(pos: Position, premium: float) -> AlertStatus:
    """Classify the alert status of a position given current premium.

    For LONG positions (options): premium going UP = good.
        SL is below entry, T1/T2 are above entry.
    For SHORT positions (forex sell): premium going DOWN = good.
        SL is above entry, T1/T2 are below entry.
    """
    is_short = getattr(pos, 'direction', '') == 'SHORT'

    if is_short:
        return _classify_short(pos, premium)
    return _classify_long(pos, premium)


def _classify_long(pos: Position, premium: float) -> AlertStatus:
    """LONG: SL below entry, targets above."""
    if premium <= pos.stop_loss:
        return AlertStatus.SL_HIT

    if premium >= pos.target_2:
        return AlertStatus.PAST_T2

    if premium >= pos.target_1:
        t2_distance = pos.target_2 - pos.target_1
        if t2_distance > 0 and (premium - pos.target_1) / t2_distance >= 0.9:
            return AlertStatus.NEAR_T2
        return AlertStatus.PAST_T1

    t1_distance = pos.target_1 - pos.entry_price
    if t1_distance > 0 and (premium - pos.entry_price) / t1_distance >= 0.9:
        return AlertStatus.NEAR_T1

    sl_distance = pos.entry_price - pos.stop_loss
    if sl_distance > 0 and (pos.entry_price - premium) / sl_distance >= 0.8:
        return AlertStatus.NEAR_SL

    return AlertStatus.SAFE


def _classify_short(pos: Position, premium: float) -> AlertStatus:
    """SHORT: SL above entry, targets below."""
    if premium >= pos.stop_loss:
        return AlertStatus.SL_HIT

    if premium <= pos.target_2:
        return AlertStatus.PAST_T2

    if premium <= pos.target_1:
        t2_distance = pos.target_1 - pos.target_2
        if t2_distance > 0 and (pos.target_1 - premium) / t2_distance >= 0.9:
            return AlertStatus.NEAR_T2
        return AlertStatus.PAST_T1

    t1_distance = pos.entry_price - pos.target_1
    if t1_distance > 0 and (pos.entry_price - premium) / t1_distance >= 0.9:
        return AlertStatus.NEAR_T1

    sl_distance = pos.stop_loss - pos.entry_price
    if sl_distance > 0 and (premium - pos.entry_price) / sl_distance >= 0.8:
        return AlertStatus.NEAR_SL

    return AlertStatus.SAFE
