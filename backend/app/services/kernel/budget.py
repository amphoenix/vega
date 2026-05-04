"""LLM token usage ledger — persisted to SQLite so per-day / per-month
spend reports survive backend restarts.

Schema (single table `llm_usage`):
    id INTEGER PRIMARY KEY AUTOINCREMENT
    timestamp_utc TEXT      -- ISO-8601 with 'Z' suffix
    timestamp_ist TEXT      -- 'YYYY-MM-DD' (date only, IST) — indexed for day rollups
    yyyymm_ist    TEXT      -- 'YYYY-MM' (IST month) — indexed for month rollups
    agent_id      TEXT
    model         TEXT
    prompt_tokens     INTEGER
    completion_tokens INTEGER
    total_tokens      INTEGER
    cost_usd          REAL

Two derived columns (timestamp_ist / yyyymm_ist) are stored at insert time
so day/month aggregations never have to do per-row tz math at query time.
"""
import os
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

_PRICING: Dict[str, Dict[str, float]] = {
    'claude-opus-4-6':   {'input': 15.00, 'output': 75.00},
    'claude-sonnet-4-6': {'input':  3.00, 'output': 15.00},
    'claude-haiku-4-5':  {'input':  0.80, 'output':  4.00},
    'gpt-4o':            {'input':  5.00, 'output': 15.00},
    'gpt-4o-mini':       {'input':  0.15, 'output':  0.60},
    'llama3-70b-8192':   {'input':  0.59, 'output':  0.79},
    'llama3-8b-8192':    {'input':  0.05, 'output':  0.08},
}

# IST = UTC+5:30 (no DST in India)
_IST_OFFSET = timedelta(hours=5, minutes=30)

# Path to SQLite file — backend/data/budget.db (sibling of fno cache)
_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    'data', 'budget.db',
)


def _cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    key = model.lower()
    pricing = _PRICING.get(key)
    if not pricing:
        for k, v in _PRICING.items():
            if k in key or key in k:
                pricing = v
                break
    if not pricing:
        return 0.0
    return (prompt_tokens * pricing['input'] + completion_tokens * pricing['output']) / 1_000_000


def _ist_now() -> datetime:
    return datetime.now(timezone.utc) + _IST_OFFSET


def _ist_date_str(dt_utc: Optional[datetime] = None) -> str:
    """'YYYY-MM-DD' for the IST calendar day containing the given UTC datetime."""
    dt_utc = dt_utc or datetime.now(timezone.utc)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return (dt_utc + _IST_OFFSET).strftime('%Y-%m-%d')


def _ist_month_str(dt_utc: Optional[datetime] = None) -> str:
    """'YYYY-MM' for the IST calendar month containing the given UTC datetime."""
    dt_utc = dt_utc or datetime.now(timezone.utc)
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return (dt_utc + _IST_OFFSET).strftime('%Y-%m')


@dataclass
class Entry:
    agent_id:          str
    model:             str
    prompt_tokens:     int
    completion_tokens: int
    total_tokens:      int
    cost_usd:          float
    timestamp:         str    # legacy field name; ISO-8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)


class BudgetStore:
    """Thread-safe SQLite-backed token usage ledger.

    Methods:
      record(...)                     — append one LLM call
      summary(period='all')           — totals + by_model/by_agent + last 20 calls
                                        period ∈ {'session','today','month','all'}
      summary_all_periods()           — convenient bundle for the UI
      reset()                         — wipe everything
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._session_started_utc = datetime.now(timezone.utc).isoformat() + 'Z'
        self._init_db()

    # ── DB lifecycle ──────────────────────────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
        c = sqlite3.connect(_DB_PATH, timeout=10)
        c.row_factory = sqlite3.Row
        # Concurrent reader/writer-friendly journaling
        c.execute('PRAGMA journal_mode = WAL')
        return c

    def _init_db(self) -> None:
        with self._lock, self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS llm_usage (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc     TEXT NOT NULL,
                    timestamp_ist     TEXT NOT NULL,
                    yyyymm_ist        TEXT NOT NULL,
                    agent_id          TEXT,
                    model             TEXT,
                    prompt_tokens     INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    total_tokens      INTEGER DEFAULT 0,
                    cost_usd          REAL    DEFAULT 0
                )
            """)
            c.execute('CREATE INDEX IF NOT EXISTS idx_ts_ist ON llm_usage(timestamp_ist)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_yyyymm ON llm_usage(yyyymm_ist)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_ts_utc ON llm_usage(timestamp_utc)')

    # ── Public API ────────────────────────────────────────────────────────────
    def record(self, agent_id: str, model: str,
               prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        total = prompt_tokens + completion_tokens
        cost  = _cost_usd(model, prompt_tokens, completion_tokens)
        now_utc = datetime.now(timezone.utc)
        ts_utc  = now_utc.isoformat().replace('+00:00', 'Z')
        ts_ist  = _ist_date_str(now_utc)
        ym_ist  = _ist_month_str(now_utc)
        with self._lock, self._conn() as c:
            c.execute(
                """INSERT INTO llm_usage
                   (timestamp_utc, timestamp_ist, yyyymm_ist, agent_id, model,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts_utc, ts_ist, ym_ist, agent_id or '', model or '',
                 int(prompt_tokens), int(completion_tokens), int(total),
                 round(cost, 6)),
            )

    def _aggregate(self, where_sql: str, params: tuple) -> dict:
        """Run aggregate queries against a WHERE clause and shape the result."""
        with self._lock, self._conn() as c:
            tot = c.execute(f"""
                SELECT
                  COUNT(*)                       AS total_calls,
                  COALESCE(SUM(prompt_tokens),0)     AS total_prompt_tokens,
                  COALESCE(SUM(completion_tokens),0) AS total_completion_tokens,
                  COALESCE(SUM(total_tokens),0)      AS total_tokens,
                  COALESCE(SUM(cost_usd),0)          AS total_cost_usd
                FROM llm_usage WHERE {where_sql}
            """, params).fetchone()

            by_model_rows = c.execute(f"""
                SELECT model AS k,
                       COUNT(*) AS calls,
                       COALESCE(SUM(total_tokens),0) AS tokens,
                       COALESCE(SUM(cost_usd),0)     AS cost_usd
                FROM llm_usage WHERE {where_sql}
                GROUP BY model
            """, params).fetchall()

            by_agent_rows = c.execute(f"""
                SELECT agent_id AS k,
                       COUNT(*) AS calls,
                       COALESCE(SUM(total_tokens),0) AS tokens,
                       COALESCE(SUM(cost_usd),0)     AS cost_usd
                FROM llm_usage WHERE {where_sql}
                GROUP BY agent_id
            """, params).fetchall()

            recent_rows = c.execute(f"""
                SELECT timestamp_utc, agent_id, model,
                       prompt_tokens, completion_tokens, total_tokens, cost_usd
                FROM llm_usage WHERE {where_sql}
                ORDER BY id DESC LIMIT 20
            """, params).fetchall()

        return {
            'total_calls':             int(tot['total_calls'] or 0),
            'total_prompt_tokens':     int(tot['total_prompt_tokens'] or 0),
            'total_completion_tokens': int(tot['total_completion_tokens'] or 0),
            'total_tokens':            int(tot['total_tokens'] or 0),
            'total_cost_usd':          round(float(tot['total_cost_usd'] or 0), 4),
            'by_model': {
                r['k'] or 'unknown': {
                    'calls': r['calls'], 'tokens': r['tokens'],
                    'cost_usd': round(r['cost_usd'], 6),
                } for r in by_model_rows
            },
            'by_agent': {
                r['k'] or 'unknown': {
                    'calls': r['calls'], 'tokens': r['tokens'],
                    'cost_usd': round(r['cost_usd'], 6),
                } for r in by_agent_rows
            },
            'recent_entries': [
                {
                    'agent_id': r['agent_id'],
                    'model':    r['model'],
                    'prompt_tokens':     r['prompt_tokens'],
                    'completion_tokens': r['completion_tokens'],
                    'total_tokens':      r['total_tokens'],
                    'cost_usd':          round(r['cost_usd'], 6),
                    'timestamp':         r['timestamp_utc'],
                } for r in recent_rows
            ],
        }

    def summary(self, period: str = 'all') -> dict:
        """Summary for a named period.

        period ∈ {'session','today','month','all'}
          session — since this backend process started
          today   — current IST calendar day
          month   — current IST calendar month (month-to-date)
          all     — entire ledger (default)
        """
        period = (period or 'all').lower()
        if period == 'today':
            return self._aggregate('timestamp_ist = ?', (_ist_date_str(),))
        if period == 'month':
            return self._aggregate('yyyymm_ist = ?', (_ist_month_str(),))
        if period == 'session':
            return self._aggregate('timestamp_utc >= ?', (self._session_started_utc,))
        return self._aggregate('1=1', ())

    def summary_all_periods(self) -> dict:
        """One-shot bundle used by the UI to render Today / Month / Session
        / All-time tiles in a single round-trip.

        Returns:
          {
            'today':   {...full summary...},
            'month':   {...},
            'session': {...},
            'all':     {...},
            # back-compat top-level fields = `today` (most useful default)
            'total_calls': N,  'total_cost_usd': X, ... ,
            'by_model': {...}, 'by_agent': {...}, 'recent_entries': [...],
          }
        """
        today   = self.summary('today')
        month   = self.summary('month')
        session = self.summary('session')
        all_    = self.summary('all')
        out = {
            'today':   today,
            'month':   month,
            'session': session,
            'all':     all_,
        }
        # Back-compat: existing UI reads top-level keys → default to "today".
        out.update(today)
        return out

    def reset(self) -> None:
        with self._lock, self._conn() as c:
            c.execute('DELETE FROM llm_usage')
            c.execute('DELETE FROM sqlite_sequence WHERE name = "llm_usage"')


budget = BudgetStore()
