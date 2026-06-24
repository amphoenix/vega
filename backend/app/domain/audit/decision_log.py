"""
Decision Log — structured audit trail for every trade decision.

Answers: "Why did trade #184 happen?"

Every signal that reaches the supervisor gets a DecisionRecord,
regardless of whether it was approved or rejected.
Records are append-only and immutable.

Pure domain — no I/O, no framework imports.
Infrastructure layer handles SQLite persistence.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from ...shared.time import datetime, now_ist


class DecisionOutcome(str, Enum):
    APPROVED = 'approved'
    REJECTED = 'rejected'


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """Immutable audit record of a single trade decision.

    Captures the full context at the moment a signal was evaluated:
    regime, supervisor verdict, risk sizing, latency, and outcome.
    """

    # Identity
    decision_id: str                     # unique ID (e.g. uuid or sequential)
    timestamp: datetime = field(default_factory=now_ist)

    # Signal context
    symbol: str = ''
    underlying: str = ''
    direction: str = ''                  # CE/PE or LONG/SHORT
    trade_mode: str = ''                 # swing / scalp
    confidence: float = 0.0

    # Regime at decision time
    regime: str = ''                     # TRENDING_BULL, RANGING, etc.
    vix: float = 0.0

    # Supervisor verdict
    outcome: DecisionOutcome = DecisionOutcome.APPROVED
    supervisor_gate: str = ''            # which gate rejected (empty if approved)
    rejection_reason: str = ''           # human-readable reason

    # Risk sizing (only if approved)
    risk_qty: int = 0
    stop_loss: float = 0.0
    target_1: float = 0.0
    target_2: float = 0.0
    entry_price: float = 0.0

    # Performance
    latency_ms: float = 0.0             # signal → decision time

    # Outcome (filled later if trade was taken)
    trade_id: str = ''                   # set after trade is opened
    exit_reason: str = ''                # set after trade is closed
    realized_pnl: float = 0.0           # set after trade is closed

    # Extensible
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['timestamp'] = self.timestamp.isoformat()
        d['outcome'] = self.outcome.value
        return d

    @property
    def is_approved(self) -> bool:
        return self.outcome == DecisionOutcome.APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.outcome == DecisionOutcome.REJECTED


class DecisionLog:
    """Append-only, thread-safe in-memory decision log.

    The application layer persists records to SQLite via infrastructure.
    This class provides fast in-memory queries for the current session.
    """

    def __init__(self, max_records: int = 10_000) -> None:
        self._records: list[DecisionRecord] = []
        self._lock = threading.Lock()
        self._max = max_records

    # ── Mutations ─────────────────────────────────────────────────────────

    def append(self, record: DecisionRecord) -> None:
        """Add a decision record. Oldest records are evicted if over max."""
        with self._lock:
            self._records.append(record)
            if len(self._records) > self._max:
                self._records = self._records[-self._max:]

    def enrich_trade_result(
        self,
        decision_id: str,
        trade_id: str = '',
        exit_reason: str = '',
        realized_pnl: float = 0.0,
    ) -> bool:
        """Enrich an existing record with trade outcome (post-close).

        Since DecisionRecord is frozen, we replace it with an updated copy.
        Returns True if record was found and updated.
        """
        with self._lock:
            for i, rec in enumerate(self._records):
                if rec.decision_id == decision_id:
                    self._records[i] = DecisionRecord(
                        decision_id=rec.decision_id,
                        timestamp=rec.timestamp,
                        symbol=rec.symbol,
                        underlying=rec.underlying,
                        direction=rec.direction,
                        trade_mode=rec.trade_mode,
                        confidence=rec.confidence,
                        regime=rec.regime,
                        vix=rec.vix,
                        outcome=rec.outcome,
                        supervisor_gate=rec.supervisor_gate,
                        rejection_reason=rec.rejection_reason,
                        risk_qty=rec.risk_qty,
                        stop_loss=rec.stop_loss,
                        target_1=rec.target_1,
                        target_2=rec.target_2,
                        entry_price=rec.entry_price,
                        latency_ms=rec.latency_ms,
                        trade_id=trade_id or rec.trade_id,
                        exit_reason=exit_reason or rec.exit_reason,
                        realized_pnl=realized_pnl if realized_pnl else rec.realized_pnl,
                        metadata=rec.metadata,
                    )
                    return True
        return False

    # ── Queries ───────────────────────────────────────────────────────────

    def get(self, decision_id: str) -> DecisionRecord | None:
        with self._lock:
            for rec in reversed(self._records):
                if rec.decision_id == decision_id:
                    return rec
        return None

    def by_trade_id(self, trade_id: str) -> DecisionRecord | None:
        """Find the decision that led to a specific trade."""
        with self._lock:
            for rec in reversed(self._records):
                if rec.trade_id == trade_id:
                    return rec
        return None

    def recent(self, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            return list(reversed(self._records[-limit:]))

    def approved(self, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            return [r for r in reversed(self._records) if r.is_approved][:limit]

    def rejected(self, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            return [r for r in reversed(self._records) if r.is_rejected][:limit]

    def by_symbol(self, symbol: str, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            return [r for r in reversed(self._records) if r.symbol == symbol][:limit]

    def by_underlying(self, underlying: str, limit: int = 50) -> list[DecisionRecord]:
        with self._lock:
            return [r for r in reversed(self._records) if r.underlying == underlying][:limit]

    def rejection_summary(self) -> dict[str, int]:
        """Count rejections by gate — answers "what's blocking my trades?"."""
        with self._lock:
            counts: dict[str, int] = {}
            for rec in self._records:
                if rec.is_rejected and rec.supervisor_gate:
                    counts[rec.supervisor_gate] = counts.get(rec.supervisor_gate, 0) + 1
            return counts

    @property
    def total(self) -> int:
        with self._lock:
            return len(self._records)

    @property
    def approved_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records if r.is_approved)

    @property
    def rejected_count(self) -> int:
        with self._lock:
            return sum(1 for r in self._records if r.is_rejected)

    def to_list(self) -> list[dict]:
        with self._lock:
            return [r.to_dict() for r in self._records]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
