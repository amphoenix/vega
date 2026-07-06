"""
Tests for Decision Log — audit trail for trade decisions.
"""

import pytest

from app.domain.audit.decision_log import (
    DecisionLog,
    DecisionOutcome,
    DecisionRecord,
)


def _rec(decision_id: str = 'D1', symbol: str = 'NIFTY25000CE',
         outcome: DecisionOutcome = DecisionOutcome.APPROVED,
         gate: str = '', reason: str = '',
         trade_id: str = '', pnl: float = 0.0) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        symbol=symbol,
        underlying='NIFTY',
        direction='CE',
        trade_mode='swing',
        confidence=0.8,
        regime='TRENDING_BULL',
        vix=14.5,
        outcome=outcome,
        supervisor_gate=gate,
        rejection_reason=reason,
        risk_qty=50,
        stop_loss=85.0,
        target_1=130.0,
        target_2=160.0,
        entry_price=100.0,
        latency_ms=12.5,
        trade_id=trade_id,
        realized_pnl=pnl,
    )


class TestDecisionRecord:
    def test_immutable(self):
        r = _rec()
        with pytest.raises(AttributeError):
            r.symbol = 'changed'

    def test_is_approved(self):
        assert _rec().is_approved
        assert not _rec().is_rejected

    def test_is_rejected(self):
        r = _rec(outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH', reason='Daily loss limit')
        assert r.is_rejected
        assert not r.is_approved
        assert r.supervisor_gate == 'KILL_SWITCH'

    def test_to_dict(self):
        d = _rec().to_dict()
        assert d['decision_id'] == 'D1'
        assert d['outcome'] == 'approved'
        assert isinstance(d['timestamp'], str)  # ISO format

    def test_default_metadata(self):
        r = _rec()
        assert r.metadata == {}


class TestDecisionLog:
    def test_append_and_get(self):
        log = DecisionLog()
        log.append(_rec())
        assert log.total == 1
        assert log.get('D1') is not None

    def test_get_missing(self):
        log = DecisionLog()
        assert log.get('NOPE') is None

    def test_recent(self):
        log = DecisionLog()
        for i in range(5):
            log.append(_rec(f'D{i}'))
        recent = log.recent(3)
        assert len(recent) == 3
        assert recent[0].decision_id == 'D4'  # most recent first

    def test_approved_filter(self):
        log = DecisionLog()
        log.append(_rec('D1'))
        log.append(_rec('D2', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH'))
        log.append(_rec('D3'))
        assert log.approved_count == 2
        assert log.rejected_count == 1
        assert len(log.approved()) == 2
        assert len(log.rejected()) == 1

    def test_by_symbol(self):
        log = DecisionLog()
        log.append(_rec('D1', symbol='NIFTY25000CE'))
        log.append(_rec('D2', symbol='SENSEX70000CE'))
        log.append(_rec('D3', symbol='NIFTY25000CE'))
        assert len(log.by_symbol('NIFTY25000CE')) == 2

    def test_by_underlying(self):
        log = DecisionLog()
        log.append(_rec('D1'))  # underlying='NIFTY'
        assert len(log.by_underlying('NIFTY')) == 1
        assert len(log.by_underlying('SENSEX')) == 0

    def test_rejection_summary(self):
        log = DecisionLog()
        log.append(_rec('D1', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH'))
        log.append(_rec('D2', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH'))
        log.append(_rec('D3', outcome=DecisionOutcome.REJECTED, gate='MAX_TRADES'))
        log.append(_rec('D4'))  # approved — not counted
        summary = log.rejection_summary()
        assert summary == {'KILL_SWITCH': 2, 'MAX_TRADES': 1}

    def test_eviction_at_max(self):
        log = DecisionLog(max_records=5)
        for i in range(10):
            log.append(_rec(f'D{i}'))
        assert log.total == 5
        assert log.get('D0') is None   # evicted
        assert log.get('D9') is not None

    def test_enrich_trade_result(self):
        log = DecisionLog()
        log.append(_rec('D1'))
        ok = log.enrich_trade_result('D1', trade_id='T1', exit_reason='past_t1', realized_pnl=500.0)
        assert ok
        r = log.get('D1')
        assert r.trade_id == 'T1'
        assert r.exit_reason == 'past_t1'
        assert r.realized_pnl == 500.0
        # Original fields preserved
        assert r.symbol == 'NIFTY25000CE'
        assert r.confidence == 0.8

    def test_enrich_missing_returns_false(self):
        log = DecisionLog()
        assert not log.enrich_trade_result('NOPE', trade_id='T1')

    def test_by_trade_id(self):
        log = DecisionLog()
        log.append(_rec('D1'))
        log.enrich_trade_result('D1', trade_id='T1')
        r = log.by_trade_id('T1')
        assert r is not None
        assert r.decision_id == 'D1'

    def test_clear(self):
        log = DecisionLog()
        log.append(_rec())
        log.clear()
        assert log.total == 0

    def test_to_list(self):
        log = DecisionLog()
        log.append(_rec())
        lst = log.to_list()
        assert len(lst) == 1
        assert lst[0]['decision_id'] == 'D1'
