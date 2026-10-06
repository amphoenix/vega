"""
State Store — SQLite-backed key/value persistence for runtime state.

Reuses the same data/pnl.db as v1 for seamless cutover.
Provides: get/set trading state, P&L recording and queries.
"""

from __future__ import annotations

import os
import sqlite3
import threading

from ...shared.time import now_ist, today_ist_str

_DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data'))
_DB_PATH = os.path.join(_DB_DIR, 'pnl.db')
_lock = threading.Lock()
_local = threading.local()


def _conn() -> sqlite3.Connection:
    """Return a persistent per-thread connection (cached in threading.local)."""
    c = getattr(_local, 'conn', None)
    if c is None:
        os.makedirs(_DB_DIR, exist_ok=True)
        c = sqlite3.connect(_DB_PATH, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        _local.conn = c
    return c


# Market segments — used in pnl_trades.market_type
MARKET_FO     = 'fo'
MARKET_CRYPTO = 'crypto'
MARKET_POLY   = 'poly'
MARKET_FOREX  = 'forex'
_ALL_MARKETS  = {MARKET_FO, MARKET_CRYPTO, MARKET_POLY, MARKET_FOREX}


def init_db() -> None:
    """Create tables if they don't exist."""
    os.makedirs(_DB_DIR, exist_ok=True)
    with _conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS pnl_trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                date         TEXT    NOT NULL,
                mode         TEXT    NOT NULL,
                market_type  TEXT    NOT NULL DEFAULT 'fo',
                symbol       TEXT    NOT NULL,
                underlying   TEXT    NOT NULL DEFAULT '',
                direction    TEXT    NOT NULL DEFAULT '',
                strike_price REAL    NOT NULL DEFAULT 0,
                entry_prem   REAL    NOT NULL,
                exit_prem    REAL    NOT NULL,
                qty          INTEGER NOT NULL,
                lot_size     INTEGER NOT NULL DEFAULT 1,
                gross_pnl    REAL    NOT NULL,
                brokerage    REAL    NOT NULL,
                net_pnl      REAL    NOT NULL,
                exit_reason  TEXT    NOT NULL DEFAULT '',
                currency     TEXT    NOT NULL DEFAULT 'INR',
                entry_time   TEXT    NOT NULL DEFAULT '',
                exit_time    TEXT    NOT NULL DEFAULT ''
            )
        ''')
        # ── Migrate existing DBs: add new columns if missing ─────────────
        _migrate_add_columns(c)
        c.execute('CREATE INDEX IF NOT EXISTS idx_pnl_date ON pnl_trades(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pnl_mode ON pnl_trades(mode)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pnl_market ON pnl_trades(market_type)')
        c.execute('''
            CREATE TABLE IF NOT EXISTS trading_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id     TEXT    NOT NULL UNIQUE,
                timestamp       TEXT    NOT NULL,
                date            TEXT    NOT NULL,
                symbol          TEXT    NOT NULL DEFAULT '',
                underlying      TEXT    NOT NULL DEFAULT '',
                direction       TEXT    NOT NULL DEFAULT '',
                trade_mode      TEXT    NOT NULL DEFAULT '',
                confidence      REAL    NOT NULL DEFAULT 0,
                regime          TEXT    NOT NULL DEFAULT '',
                vix             REAL    NOT NULL DEFAULT 0,
                outcome         TEXT    NOT NULL DEFAULT 'approved',
                supervisor_gate TEXT    NOT NULL DEFAULT '',
                rejection_reason TEXT   NOT NULL DEFAULT '',
                risk_qty        INTEGER NOT NULL DEFAULT 0,
                stop_loss       REAL    NOT NULL DEFAULT 0,
                target_1        REAL    NOT NULL DEFAULT 0,
                target_2        REAL    NOT NULL DEFAULT 0,
                entry_price     REAL    NOT NULL DEFAULT 0,
                latency_ms      REAL    NOT NULL DEFAULT 0,
                trade_id        TEXT    NOT NULL DEFAULT '',
                exit_reason     TEXT    NOT NULL DEFAULT '',
                realized_pnl    REAL    NOT NULL DEFAULT 0,
                metadata_json   TEXT    NOT NULL DEFAULT '{}'
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_audit_date ON audit_log(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_audit_outcome ON audit_log(outcome)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_audit_trade ON audit_log(trade_id)')
        c.execute('''
            CREATE TABLE IF NOT EXISTS crypto_bots (
                bot_id      TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                strategy    TEXT NOT NULL DEFAULT 'trend_v4',
                symbols     TEXT NOT NULL DEFAULT '',
                config_json TEXT NOT NULL DEFAULT '{}',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        ''')
        c.commit()


def _migrate_add_columns(c: sqlite3.Connection) -> None:
    """Add columns introduced after initial release (idempotent)."""
    existing = {row[1] for row in c.execute('PRAGMA table_info(pnl_trades)').fetchall()}
    migrations = [
        ('market_type',  "TEXT NOT NULL DEFAULT 'fo'"),
        ('direction',    "TEXT NOT NULL DEFAULT ''"),
        ('strike_price', 'REAL NOT NULL DEFAULT 0'),
        ('currency',     "TEXT NOT NULL DEFAULT 'INR'"),
        ('entry_time',   "TEXT NOT NULL DEFAULT ''"),
        ('exit_time',    "TEXT NOT NULL DEFAULT ''"),
        ('order_id',      "TEXT NOT NULL DEFAULT ''"),
        ('exit_order_id', "TEXT NOT NULL DEFAULT ''"),
        ('display_symbol', "TEXT NOT NULL DEFAULT ''"),
    ]
    for col, typedef in migrations:
        if col not in existing:
            c.execute(f'ALTER TABLE pnl_trades ADD COLUMN {col} {typedef}')
    c.commit()

    # One-time fix: crypto trades recorded with qty=1 have wrong P&L.
    # Correct qty = position_size(1000) / entry_prem.
    # Recalculate gross_pnl, brokerage, net_pnl.
    _migrate_fix_crypto_qty(c)


def _migrate_fix_crypto_qty(c: sqlite3.Connection) -> None:
    """Fix crypto trades that were incorrectly recorded with qty=1."""
    try:
        bad = c.execute(
            "SELECT id, entry_prem, exit_prem FROM pnl_trades "
            "WHERE market_type = 'crypto' AND qty = 1 AND entry_prem > 1",
        ).fetchall()
        if not bad:
            return
        for row in bad:
            tid, entry, exit_ = row['id'], row['entry_prem'], row['exit_prem']
            if entry <= 0:
                continue
            real_qty = round(1000.0 / entry, 6)
            gross = round((exit_ - entry) * real_qty, 2)
            brk = round((entry * real_qty + exit_ * real_qty) * 0.001, 2)
            net = round(gross - brk, 2)
            c.execute(
                "UPDATE pnl_trades SET qty = ?, gross_pnl = ?, brokerage = ?, net_pnl = ? WHERE id = ?",
                (real_qty, gross, brk, net, tid),
            )
        c.commit()
    except Exception:
        pass


# ── Trading state KV ─────────────────────────────────────────────────────────

def get_state(key: str, default: str = '') -> str:
    try:
        with _conn() as c:
            row = c.execute('SELECT value FROM trading_state WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default
    except Exception:
        return default


def set_state(key: str, value: str) -> None:
    with _lock, _conn() as c:
        c.execute('INSERT OR REPLACE INTO trading_state (key, value) VALUES (?, ?)', (key, value))
        c.commit()


# ── P&L recording ────────────────────────────────────────────────────────────

def record_trade(
    mode: str, symbol: str, underlying: str,
    entry_prem: float, exit_prem: float, qty: int,
    lot_size: int, brokerage: float, exit_reason: str = '',
    *,
    market_type: str = 'fo',
    direction: str = '',
    strike_price: float = 0.0,
    currency: str = 'INR',
    entry_time: str = '',
    exit_time: str = '',
    order_id: str = '',
    exit_order_id: str = '',
    display_symbol: str = '',
    gross_pnl_override: float | None = None,
) -> dict:
    gross_pnl = round(gross_pnl_override, 2) if gross_pnl_override is not None else round((exit_prem - entry_prem) * qty, 2)
    net_pnl = round(gross_pnl - brokerage, 2)
    now = now_ist()

    with _lock, _conn() as c:
        c.execute(
            '''INSERT INTO pnl_trades
                       (timestamp, date, mode, market_type, symbol, underlying,
                        direction, strike_price, entry_prem, exit_prem, qty, lot_size,
                        gross_pnl, brokerage, net_pnl, exit_reason,
                        currency, entry_time, exit_time, order_id, exit_order_id, display_symbol)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (now.isoformat(), now.strftime('%Y-%m-%d'), mode, market_type,
             symbol, underlying, direction, strike_price,
             entry_prem, exit_prem, qty, lot_size,
             gross_pnl, brokerage, net_pnl, exit_reason,
             currency, entry_time, exit_time, order_id, exit_order_id, display_symbol),
        )
        c.commit()

    return {'gross_pnl': gross_pnl, 'brokerage': brokerage, 'net_pnl': net_pnl}


# ── Queries ──────────────────────────────────────────────────────────────────

def today_net_by_mode(market_type: str | None = None) -> dict[str, float]:
    date_str = today_ist_str()
    query = 'SELECT mode, SUM(net_pnl) AS net FROM pnl_trades WHERE date = ?'
    params: list = [date_str]
    if market_type:
        query += ' AND market_type = ?'
        params.append(market_type)
    query += ' GROUP BY mode'
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    result = {'swing': 0.0, 'scalp': 0.0, 'forex': 0.0, 'btst': 0.0}
    for r in rows:
        if r['mode'] in result:
            result[r['mode']] = round(r['net'] or 0.0, 2)
    return result


def daily_summary(date_str: str | None = None, market_type: str | None = None) -> dict:
    date_str = date_str or today_ist_str()
    z = lambda: {'trades': 0, 'gross': 0.0, 'brokerage': 0.0, 'net': 0.0}
    try:
        query = '''SELECT mode, COUNT(*) AS trades, SUM(gross_pnl) AS gross,
                          SUM(brokerage) AS brokerage, SUM(net_pnl) AS net
                   FROM pnl_trades WHERE date = ?'''
        params: list = [date_str]
        if market_type:
            query += ' AND market_type = ?'
            params.append(market_type)
        query += ' GROUP BY mode'
        with _conn() as c:
            rows = c.execute(query, params).fetchall()
    except Exception:
        return {'date': date_str, 'swing': z(), 'scalp': z(), 'forex': z(), 'total': z()}

    result: dict = {'date': date_str, 'swing': z(), 'scalp': z(), 'forex': z()}
    for r in rows:
        m = r['mode']
        if m in result:
            result[m] = {
                'trades': r['trades'],
                'gross': round(r['gross'] or 0, 2),
                'brokerage': round(r['brokerage'] or 0, 2),
                'net': round(r['net'] or 0, 2),
            }
    s, sc, fx = result['swing'], result['scalp'], result['forex']
    result['total'] = {
        'trades': s['trades'] + sc['trades'] + fx['trades'],
        'gross': round(s['gross'] + sc['gross'] + fx['gross'], 2),
        'brokerage': round(s['brokerage'] + sc['brokerage'] + fx['brokerage'], 2),
        'net': round(s['net'] + sc['net'] + fx['net'], 2),
    }
    return result


def daily_summary_by_segment(date_str: str | None = None) -> dict:
    """Per-tab P&L summary: swing, scalp (both F&O), crypto, poly, forex."""
    date_str = date_str or today_ist_str()
    z = lambda: {'trades': 0, 'gross': 0.0, 'brokerage': 0.0, 'net': 0.0}
    tabs = ('swing', 'scalp', 'crypto', 'crypto_fo', 'poly', 'forex')
    try:
        with _conn() as c:
            rows = c.execute(
                '''SELECT market_type, mode, COUNT(*) AS trades,
                          SUM(gross_pnl) AS gross,
                          SUM(brokerage) AS brokerage, SUM(net_pnl) AS net
                   FROM pnl_trades WHERE date = ?
                   GROUP BY market_type, mode''',
                (date_str,),
            ).fetchall()
    except Exception:
        result = {'date': date_str, 'total': z()}
        for t in tabs:
            result[t] = z()
        return result

    _SEGMENT_CURRENCY = {
        'swing': 'INR', 'scalp': 'INR', 'forex': 'INR',
        'crypto': 'USD', 'crypto_fo': 'USD', 'poly': 'USD',
    }

    result: dict = {'date': date_str}
    for t in tabs:
        result[t] = {**z(), 'currency': _SEGMENT_CURRENCY.get(t, 'INR')}
    for r in rows:
        mt   = r['market_type'] or 'fo'
        mode = r['mode'] or ''
        row_data = {
            'trades':    r['trades'],
            'gross':     round(r['gross'] or 0, 2),
            'brokerage': round(r['brokerage'] or 0, 2),
            'net':       round(r['net'] or 0, 2),
        }
        # Route by mode if mode is a known tab (handles forex with legacy market_type='fo')
        if mode in ('forex', 'crypto', 'crypto_fo', 'poly'):
            mt = mode  # override — mode is authoritative for these segments
        if mt == 'fo' and mode in ('swing', 'scalp'):
            result[mode] = {**row_data, 'currency': 'INR'}
        elif mt in ('crypto', 'crypto_fo', 'poly', 'forex'):
            prev = result[mt]
            result[mt] = {
                'trades':    prev['trades'] + row_data['trades'],
                'gross':     round(prev['gross'] + row_data['gross'], 2),
                'brokerage': round(prev['brokerage'] + row_data['brokerage'], 2),
                'net':       round(prev['net'] + row_data['net'], 2),
                'currency':  _SEGMENT_CURRENCY.get(mt, 'USD'),
            }

    # Totals split by currency for correct display
    inr_net, usd_net = 0.0, 0.0
    total_t, total_g, total_b, total_n = 0, 0.0, 0.0, 0.0
    for t in tabs:
        total_t += result[t]['trades']
        total_g += result[t]['gross']
        total_b += result[t]['brokerage']
        total_n += result[t]['net']
        if _SEGMENT_CURRENCY.get(t) == 'INR':
            inr_net += result[t]['net']
        else:
            usd_net += result[t]['net']
    result['total'] = {
        'trades': total_t,
        'gross': round(total_g, 2),
        'brokerage': round(total_b, 2),
        'net': round(total_n, 2),
        'inr_net': round(inr_net, 2),
        'usd_net': round(usd_net, 2),
    }
    return result


def recent_trades(limit: int = 50, mode: str | None = None,
                  market_type: str | None = None) -> list[dict]:
    query = 'SELECT * FROM pnl_trades'
    clauses: list[str] = []
    params: list = []
    if mode:
        clauses.append('mode = ?')
        params.append(mode)
    if market_type:
        clauses.append('market_type = ?')
        params.append(market_type)
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


# ── Audit log ────────────────────────────────────────────────────────────────

def persist_decision(record) -> None:
    """Persist a DecisionRecord to the audit_log table.

    Accepts a DecisionRecord (from domain/audit/decision_log.py) or any
    object with the same attributes.
    """
    import json
    with _lock, _conn() as c:
        c.execute(
            '''INSERT OR REPLACE INTO audit_log
                       (decision_id, timestamp, date, symbol, underlying,
                        direction, trade_mode, confidence, regime, vix,
                        outcome, supervisor_gate, rejection_reason,
                        risk_qty, stop_loss, target_1, target_2, entry_price,
                        latency_ms, trade_id, exit_reason, realized_pnl,
                        metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                record.decision_id,
                record.timestamp.isoformat(),
                record.timestamp.strftime('%Y-%m-%d'),
                record.symbol,
                record.underlying,
                record.direction,
                record.trade_mode,
                record.confidence,
                record.regime,
                record.vix,
                record.outcome.value if hasattr(record.outcome, 'value') else record.outcome,
                record.supervisor_gate,
                record.rejection_reason,
                record.risk_qty,
                record.stop_loss,
                record.target_1,
                record.target_2,
                record.entry_price,
                record.latency_ms,
                record.trade_id,
                record.exit_reason,
                record.realized_pnl,
                json.dumps(record.metadata) if record.metadata else '{}',
            ),
        )
        c.commit()


def recent_decisions(limit: int = 50, outcome: str | None = None) -> list[dict]:
    """Query recent audit records, optionally filtered by outcome."""
    query = 'SELECT * FROM audit_log'
    params: list = []
    if outcome:
        query += ' WHERE outcome = ?'
        params.append(outcome)
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def decisions_by_date(date_str: str | None = None) -> dict:
    """Summary of decisions for a given date."""
    date_str = date_str or today_ist_str()
    with _conn() as c:
        rows = c.execute(
            '''SELECT outcome, COUNT(*) AS cnt
               FROM audit_log WHERE date = ? GROUP BY outcome''',
            (date_str,),
        ).fetchall()
    result = {'date': date_str, 'approved': 0, 'rejected': 0, 'total': 0}
    for r in rows:
        result[r['outcome']] = r['cnt']
    result['total'] = result['approved'] + result['rejected']
    return result


def rejection_breakdown(date_str: str | None = None) -> list[dict]:
    """Count rejections by gate for a given date."""
    date_str = date_str or today_ist_str()
    with _conn() as c:
        rows = c.execute(
            '''SELECT supervisor_gate, COUNT(*) AS cnt
               FROM audit_log
               WHERE date = ? AND outcome = 'rejected'
               GROUP BY supervisor_gate ORDER BY cnt DESC''',
            (date_str,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Crypto Bot persistence ───────────────────────────────────────────────────

def save_bot(bot_id: str, name: str, strategy: str, symbols: str,
             config_json: str, enabled: bool = True) -> None:
    """Upsert a crypto bot config."""
    now = today_ist_str()
    with _lock, _conn() as c:
        c.execute('''
                INSERT INTO crypto_bots (bot_id, name, strategy, symbols, config_json, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    name=excluded.name, strategy=excluded.strategy, symbols=excluded.symbols,
                    config_json=excluded.config_json, enabled=excluded.enabled, updated_at=excluded.updated_at
            ''', (bot_id, name, strategy, symbols, config_json, int(enabled), now, now))


def list_bots() -> list[dict]:
    """Return all saved crypto bots."""
    with _conn() as c:
        rows = c.execute('SELECT * FROM crypto_bots ORDER BY created_at').fetchall()
    return [dict(r) for r in rows]


def get_bot(bot_id: str) -> dict | None:
    """Return a single bot config or None."""
    with _conn() as c:
        row = c.execute('SELECT * FROM crypto_bots WHERE bot_id = ?', (bot_id,)).fetchone()
    return dict(row) if row else None


def delete_bot(bot_id: str) -> bool:
    """Delete a bot config. Returns True if deleted."""
    with _lock, _conn() as c:
        cur = c.execute('DELETE FROM crypto_bots WHERE bot_id = ?', (bot_id,))
    return cur.rowcount > 0
