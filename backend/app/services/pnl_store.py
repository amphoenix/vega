"""
P&L persistence — SQLite-backed trade ledger.

Every closed trade (swing + scalp) is recorded with a full brokerage
breakdown so net P&L is always accurate.

Brokerage model (INDstocks F&O):
  ₹20 flat per order (buy + sell = ₹40)
  + NSE exchange charge  0.053%  of total premium turnover
  + STT                  0.0625% of sell-side premium turnover (options)
  + SEBI charges         ₹10 per ₹1 crore of turnover
  + Stamp duty           0.003%  of buy-side turnover
  + GST                  18%     on (brokerage + exchange charge)

Public API:
    record_trade(mode, symbol, underlying, entry_prem, exit_prem, qty, lot_size, exit_reason)
    daily_summary(date_str)   → {date, swing, scalp, total}  (each: trades/gross/brokerage/net)
    monthly_summary(year, month) → {year, month, swing, scalp, total, daily: {date: ...}}
    recent_trades(limit, mode) → list[dict]
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(_IST)
from typing import Optional

_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/pnl.db'))
_lock    = threading.Lock()


# ── DB init ───────────────────────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _init() -> None:
    os.makedirs(os.path.dirname(_DB_PATH), exist_ok=True)
    with _conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS pnl_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                date        TEXT    NOT NULL,
                mode        TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                underlying  TEXT    NOT NULL DEFAULT '',
                entry_prem  REAL    NOT NULL,
                exit_prem   REAL    NOT NULL,
                qty         INTEGER NOT NULL,
                lot_size    INTEGER NOT NULL DEFAULT 1,
                gross_pnl   REAL    NOT NULL,
                brokerage   REAL    NOT NULL,
                net_pnl     REAL    NOT NULL,
                exit_reason TEXT    NOT NULL DEFAULT ''
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pnl_date ON pnl_trades(date)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pnl_mode ON pnl_trades(mode)')
        c.execute('''
            CREATE TABLE IF NOT EXISTS trading_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        c.commit()


_init()


# ── Trading state (runtime-mutable config in SQLite, not .env) ────────────────

def get_trading_state(key: str, default: str = '') -> str:
    """Read a runtime state value from SQLite trading_state table."""
    try:
        with _conn() as c:
            row = c.execute('SELECT value FROM trading_state WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default
    except Exception:
        return default


def set_trading_state(key: str, value: str) -> None:
    """Write a runtime state value to SQLite trading_state table."""
    with _lock:
        with _conn() as c:
            c.execute('INSERT OR REPLACE INTO trading_state (key, value) VALUES (?, ?)',
                      (key, value))
            c.commit()


# ── Brokerage ─────────────────────────────────────────────────────────────────

def _calc_brokerage(entry_prem: float, exit_prem: float, qty: int) -> float:
    """Return total round-trip brokerage for one F&O trade (buy + sell).

    Rates as per INDmoney/INDstocks pricing (NSE options, Budget 2024 onwards):
      Flat brokerage   ₹20/order × 2 = ₹40
      Exchange txn     0.03503% of total premium turnover (buy + sell)
      STT              0.1% of sell-side premium (effective Oct 2024, Budget 2024)
      SEBI             ₹10 per crore (0.0001%) of total turnover
      Stamp duty       0.003% of buy-side turnover
      GST              18% on (brokerage + exchange txn + SEBI)
    """
    buy_val  = entry_prem * qty
    sell_val = exit_prem  * qty
    total    = buy_val + sell_val

    flat_brokerage = 40.0                          # ₹20 × 2 orders
    exchange_txn   = total    * 0.0003503          # 0.03503% of turnover (NSE options)
    stt            = sell_val * 0.001              # 0.1% sell side (Budget 2024, effective Oct 2024)
    sebi           = total    * 0.000001           # ₹10 per ₹1 crore
    stamp          = buy_val  * 0.00003            # 0.003% buy side only
    gst            = (flat_brokerage + exchange_txn + sebi) * 0.18

    return round(flat_brokerage + exchange_txn + stt + sebi + stamp + gst, 2)


# ── Write ─────────────────────────────────────────────────────────────────────

def record_trade(
    mode:        str,
    symbol:      str,
    underlying:  str,
    entry_prem:  float,
    exit_prem:   float,
    qty:         int,
    lot_size:    int  = 1,
    exit_reason: str  = '',
) -> dict:
    gross_pnl = round((exit_prem - entry_prem) * qty, 2)
    brokerage = _calc_brokerage(entry_prem, exit_prem, qty)
    net_pnl   = round(gross_pnl - brokerage, 2)
    now       = _now_ist()
    ts        = now.isoformat()
    date_str  = now.strftime('%Y-%m-%d')

    with _lock:
        with _conn() as c:
            c.execute(
                '''INSERT INTO pnl_trades
                       (timestamp, date, mode, symbol, underlying,
                        entry_prem, exit_prem, qty, lot_size,
                        gross_pnl, brokerage, net_pnl, exit_reason)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (ts, date_str, mode, symbol, underlying,
                 entry_prem, exit_prem, qty, lot_size,
                 gross_pnl, brokerage, net_pnl, exit_reason),
            )
            c.commit()

    return {'gross_pnl': gross_pnl, 'brokerage': brokerage, 'net_pnl': net_pnl}


# ── Read ──────────────────────────────────────────────────────────────────────

def _zero() -> dict:
    return {'trades': 0, 'gross': 0.0, 'brokerage': 0.0, 'net': 0.0}


def daily_summary(date_str: Optional[str] = None) -> dict:
    date_str = date_str or _now_ist().strftime('%Y-%m-%d')
    try:
        return _daily_summary_inner(date_str)
    except sqlite3.OperationalError:
        _init()  # table may have been deleted — recreate
        return _daily_summary_inner(date_str)


def _daily_summary_inner(date_str: str) -> dict:
    with _conn() as c:
        rows = c.execute(
            '''SELECT mode,
                      COUNT(*)       AS trades,
                      SUM(gross_pnl) AS gross,
                      SUM(brokerage) AS brokerage,
                      SUM(net_pnl)   AS net
               FROM pnl_trades
               WHERE date = ?
               GROUP BY mode''',
            (date_str,),
        ).fetchall()

    result: dict = {'date': date_str, 'swing': _zero(), 'scalp': _zero()}
    for r in rows:
        m = r['mode']
        if m in result:
            result[m] = {
                'trades':    r['trades'],
                'gross':     round(r['gross']     or 0, 2),
                'brokerage': round(r['brokerage'] or 0, 2),
                'net':       round(r['net']       or 0, 2),
            }

    s, sc = result['swing'], result['scalp']
    result['total'] = {
        'trades':    s['trades']    + sc['trades'],
        'gross':     round(s['gross']     + sc['gross'],     2),
        'brokerage': round(s['brokerage'] + sc['brokerage'], 2),
        'net':       round(s['net']       + sc['net'],       2),
    }
    return result


def monthly_summary(year: int, month: int) -> dict:
    prefix = f'{year:04d}-{month:02d}'
    with _conn() as c:
        rows = c.execute(
            '''SELECT date, mode,
                      COUNT(*)       AS trades,
                      SUM(gross_pnl) AS gross,
                      SUM(brokerage) AS brokerage,
                      SUM(net_pnl)   AS net
               FROM pnl_trades
               WHERE date LIKE ?
               GROUP BY date, mode
               ORDER BY date''',
            (f'{prefix}%',),
        ).fetchall()

    swing: dict = _zero()
    scalp: dict = _zero()
    daily: dict = {}

    for r in rows:
        d = r['date']
        m = r['mode']
        entry = {
            'trades':    r['trades'],
            'gross':     round(r['gross']     or 0, 2),
            'brokerage': round(r['brokerage'] or 0, 2),
            'net':       round(r['net']       or 0, 2),
        }
        bucket = swing if m == 'swing' else scalp
        bucket['trades']    += entry['trades']
        bucket['gross']      = round(bucket['gross']     + entry['gross'],     2)
        bucket['brokerage']  = round(bucket['brokerage'] + entry['brokerage'], 2)
        bucket['net']        = round(bucket['net']       + entry['net'],       2)

        daily.setdefault(d, {'swing': _zero(), 'scalp': _zero()})[m] = entry

    # add daily totals
    for d, v in daily.items():
        s, sc = v['swing'], v['scalp']
        v['total'] = {
            'trades':    s['trades']    + sc['trades'],
            'gross':     round(s['gross']     + sc['gross'],     2),
            'brokerage': round(s['brokerage'] + sc['brokerage'], 2),
            'net':       round(s['net']       + sc['net'],       2),
        }

    total = {
        'trades':    swing['trades']    + scalp['trades'],
        'gross':     round(swing['gross']     + scalp['gross'],     2),
        'brokerage': round(swing['brokerage'] + scalp['brokerage'], 2),
        'net':       round(swing['net']       + scalp['net'],       2),
    }
    return {
        'year': year, 'month': month,
        'swing': swing, 'scalp': scalp, 'total': total,
        'daily': daily,
    }


def today_gross_by_mode() -> dict:
    """Return {swing: gross_pnl_sum, scalp: gross_pnl_sum} for today."""
    date_str = _now_ist().strftime('%Y-%m-%d')
    with _conn() as c:
        rows = c.execute(
            'SELECT mode, SUM(gross_pnl) AS gross FROM pnl_trades WHERE date = ? GROUP BY mode',
            (date_str,),
        ).fetchall()
    result = {'swing': 0.0, 'scalp': 0.0}
    for r in rows:
        if r['mode'] in result:
            result[r['mode']] = round(r['gross'] or 0.0, 2)
    return result


def last_trade_date() -> Optional[str]:
    """Return the date string (YYYY-MM-DD) of the most recent trade, or None if no trades."""
    with _conn() as c:
        row = c.execute('SELECT MAX(date) AS d FROM pnl_trades').fetchone()
    return row['d'] if row and row['d'] else None


def today_net_by_mode() -> dict:
    """Return {swing: net_pnl_sum, scalp: net_pnl_sum} for today.
    Used to restore in-memory P&L (which tracks net after brokerage) across restarts."""
    date_str = _now_ist().strftime('%Y-%m-%d')
    with _conn() as c:
        rows = c.execute(
            'SELECT mode, SUM(net_pnl) AS net FROM pnl_trades WHERE date = ? GROUP BY mode',
            (date_str,),
        ).fetchall()
    result = {'swing': 0.0, 'scalp': 0.0}
    for r in rows:
        if r['mode'] in result:
            result[r['mode']] = round(r['net'] or 0.0, 2)
    return result


def recent_trades(limit: int = 50, mode: Optional[str] = None) -> list:
    query  = 'SELECT * FROM pnl_trades'
    params: list = []
    if mode:
        query += ' WHERE mode = ?'
        params.append(mode)
    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]
