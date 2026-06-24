"""
Integration tests for audit log persistence (SQLite).
"""

import os
import tempfile
import pytest
from unittest.mock import patch

from app.domain.audit.decision_log import DecisionRecord, DecisionOutcome
from app.infrastructure.db import state_store


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    """Redirect state_store to a temp directory for each test."""
    db_dir = str(tmp_path)
    db_path = os.path.join(db_dir, 'pnl.db')
    # Clear per-thread cached connection so new path takes effect
    state_store._local.conn = None
    with patch.object(state_store, '_DB_DIR', db_dir), \
         patch.object(state_store, '_DB_PATH', db_path):
        state_store.init_db()
        yield
    # Clean up cached connection after test
    conn = getattr(state_store._local, 'conn', None)
    if conn:
        conn.close()
    state_store._local.conn = None


def _rec(decision_id: str = 'D1',
         outcome: DecisionOutcome = DecisionOutcome.APPROVED,
         gate: str = '') -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        symbol='NIFTY25000CE',
        underlying='NIFTY',
        direction='CE',
        trade_mode='swing',
        confidence=0.85,
        regime='TRENDING_BULL',
        vix=14.0,
        outcome=outcome,
        supervisor_gate=gate,
        rejection_reason='test reason' if gate else '',
        risk_qty=50,
        stop_loss=85.0,
        target_1=130.0,
        target_2=160.0,
        entry_price=100.0,
        latency_ms=10.0,
    )


class TestAuditPersistence:
    def test_persist_and_query(self):
        state_store.persist_decision(_rec())
        rows = state_store.recent_decisions(limit=10)
        assert len(rows) == 1
        assert rows[0]['decision_id'] == 'D1'
        assert rows[0]['outcome'] == 'approved'
        assert rows[0]['confidence'] == 0.85

    def test_persist_rejected(self):
        state_store.persist_decision(
            _rec('D2', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH')
        )
        rows = state_store.recent_decisions(outcome='rejected')
        assert len(rows) == 1
        assert rows[0]['supervisor_gate'] == 'KILL_SWITCH'

    def test_upsert_on_duplicate(self):
        state_store.persist_decision(_rec('D1'))
        # Re-persist same decision_id with updated trade_id
        r = DecisionRecord(
            decision_id='D1',
            symbol='NIFTY25000CE',
            underlying='NIFTY',
            direction='CE',
            trade_mode='swing',
            trade_id='T1',
            realized_pnl=500.0,
        )
        state_store.persist_decision(r)
        rows = state_store.recent_decisions()
        assert len(rows) == 1
        assert rows[0]['trade_id'] == 'T1'

    def test_decisions_by_date(self):
        state_store.persist_decision(_rec('D1'))
        state_store.persist_decision(
            _rec('D2', outcome=DecisionOutcome.REJECTED, gate='MAX_TRADES')
        )
        summary = state_store.decisions_by_date()
        assert summary['approved'] == 1
        assert summary['rejected'] == 1
        assert summary['total'] == 2

    def test_rejection_breakdown(self):
        state_store.persist_decision(
            _rec('D1', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH')
        )
        state_store.persist_decision(
            _rec('D2', outcome=DecisionOutcome.REJECTED, gate='KILL_SWITCH')
        )
        state_store.persist_decision(
            _rec('D3', outcome=DecisionOutcome.REJECTED, gate='MAX_TRADES')
        )
        breakdown = state_store.rejection_breakdown()
        gates = {r['supervisor_gate']: r['cnt'] for r in breakdown}
        assert gates['KILL_SWITCH'] == 2
        assert gates['MAX_TRADES'] == 1

    def test_recent_decisions_limit(self):
        for i in range(10):
            state_store.persist_decision(_rec(f'D{i}'))
        rows = state_store.recent_decisions(limit=3)
        assert len(rows) == 3

    def test_empty_db(self):
        rows = state_store.recent_decisions()
        assert rows == []
        summary = state_store.decisions_by_date()
        assert summary['total'] == 0
