"""
IST time helpers, NSE market hours, and holiday calendar.

Pure functions — no I/O, no framework imports.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from time import monotonic, monotonic_ns, sleep, time as clock  # re-export
from zoneinfo import ZoneInfo

IST = ZoneInfo('Asia/Kolkata')

# ── Market hours (IST) ──────────────────────────────────────────────────────
MARKET_OPEN  = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)
SAFE_OPEN    = dt_time(9, 20)    # avoid first 5min volatility
SAFE_CLOSE   = dt_time(15, 0)    # last entry before square-off ramp
FORCE_EXIT   = dt_time(15, 10)   # close every MIS leg
THETA_EXIT   = dt_time(13, 0)    # on expiry day, exit options after this

# ── NSE CDS (Currency Derivative Segment) hours ──────────────────────────────
CDS_OPEN       = dt_time(9, 0)
CDS_CLOSE      = dt_time(17, 0)
CDS_SAFE_OPEN  = dt_time(9, 5)     # avoid first 5min
CDS_SAFE_CLOSE = dt_time(16, 30)   # last entry before square-off
CDS_FORCE_EXIT = dt_time(16, 45)   # close every MIS leg

# ── NSE holiday list ────────────────────────────────────────────────────────
# Update yearly from https://www.nseindia.com/resources/exchange-communication-holidays
NSE_HOLIDAYS_2025: set[str] = {
    '2025-02-26', '2025-03-14', '2025-03-31', '2025-04-10',
    '2025-04-14', '2025-04-18', '2025-05-01', '2025-08-15',
    '2025-08-27', '2025-10-02', '2025-10-20', '2025-10-22',
    '2025-11-05', '2025-12-25',
}
NSE_HOLIDAYS_2026: set[str] = {
    '2026-01-26', '2026-03-03', '2026-03-25', '2026-04-03',
    '2026-04-14', '2026-05-01', '2026-08-15', '2026-10-02',
    '2026-11-09', '2026-12-25',
}
NSE_HOLIDAYS: set[str] = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026


# ── Core helpers ────────────────────────────────────────────────────────────

def now_ist() -> datetime:
    """Current datetime in IST (timezone-aware)."""
    return datetime.now(IST)


def now_utc() -> datetime:
    """Current datetime in UTC (timezone-aware). Use for internal timestamps."""
    return datetime.now(IST).astimezone(ZoneInfo('UTC'))


def today_ist() -> date:
    """Today's date in IST."""
    return now_ist().date()


def today_ist_str() -> str:
    """Today's date as 'YYYY-MM-DD' in IST."""
    return today_ist().isoformat()


def is_trading_day(d: date | None = None) -> bool:
    """True iff d is a weekday and not in the NSE holiday list."""
    d = d or today_ist()
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in NSE_HOLIDAYS


def is_market_hours() -> bool:
    """True if within NSE market hours."""
    n = now_ist()
    return is_trading_day(n.date()) and (MARKET_OPEN <= n.time() <= MARKET_CLOSE)


def is_safe_hours() -> bool:
    """Window during which we are willing to OPEN new positions."""
    n = now_ist()
    return is_trading_day(n.date()) and (SAFE_OPEN <= n.time() <= SAFE_CLOSE)


def is_force_exit_time() -> bool:
    """True when we should force-exit all MIS positions."""
    n = now_ist()
    return is_trading_day(n.date()) and n.time() >= FORCE_EXIT


def is_cds_hours() -> bool:
    """True if within NSE CDS (currency derivatives) hours."""
    n = now_ist()
    return is_trading_day(n.date()) and (CDS_OPEN <= n.time() <= CDS_CLOSE)


def is_cds_safe_hours() -> bool:
    """Window during which we are willing to OPEN currency positions."""
    n = now_ist()
    return is_trading_day(n.date()) and (CDS_SAFE_OPEN <= n.time() <= CDS_SAFE_CLOSE)


def is_cds_force_exit_time() -> bool:
    """True when we should force-exit all CDS MIS positions."""
    n = now_ist()
    return is_trading_day(n.date()) and n.time() >= CDS_FORCE_EXIT


def seconds_until_cds_open() -> int:
    """Seconds from now until CDS opens (09:00 IST). 0 if already in hours."""
    n = now_ist()
    if is_cds_hours():
        return 0
    today_open = n.replace(hour=CDS_OPEN.hour, minute=CDS_OPEN.minute,
                           second=0, microsecond=0)
    if n < today_open and is_trading_day(n.date()):
        return max(0, int((today_open - n).total_seconds()))
    d = n.date()
    for _ in range(14):
        d += timedelta(days=1)
        if is_trading_day(d):
            candidate = datetime.combine(d, CDS_OPEN, tzinfo=IST)
            return max(0, int((candidate - n).total_seconds()))
    return 86400


def seconds_until_market_open() -> int:
    """Seconds from now until the next NSE market-open bell (09:15 IST).

    Returns 0 if market is currently in hours. Skips weekends and holidays.
    """
    n = now_ist()
    if is_market_hours():
        return 0

    today_open = n.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
                           second=0, microsecond=0)
    if n < today_open and is_trading_day(n.date()):
        return max(0, int((today_open - n).total_seconds()))

    d = n.date()
    for _ in range(14):
        d += timedelta(days=1)
        if is_trading_day(d):
            candidate = datetime.combine(d, MARKET_OPEN, tzinfo=IST)
            return max(0, int((candidate - n).total_seconds()))

    return 86400  # safety fallback


# ── Date formatting for frontend ───────────────────────────────────────────

def fmt_candle_date(d: str | datetime, intraday: bool = True) -> str:
    """Normalise a candle date to the format the frontend expects.

    Intraday → 'YYYY-MM-DD HH:MM'   (lightweight-charts treats as IST)
    Daily    → 'YYYY-MM-DD'

    Handles timezone-aware strings ('+05:30', 'Z'), pandas Timestamps,
    and plain datetime objects.
    """
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            d = d.astimezone(IST)
        return d.strftime('%Y-%m-%d %H:%M') if intraday else d.strftime('%Y-%m-%d')

    s = str(d)
    # Strip timezone offset: '+05:30', '+0530', 'Z'
    s = s.split('+')[0].split('Z')[0].strip()
    if intraday:
        return s[:16]   # 'YYYY-MM-DD HH:MM'
    return s[:10]       # 'YYYY-MM-DD'


# ── Index ticker helpers ───────────────────────────────────────────────

_INDEX_MAP = {
    'NSEI': 'NIFTY', 'NSEBANK': 'BANKNIFTY', 'CNXFIN': 'FINNIFTY',
    'NSEMDCP50': 'MIDCPNIFTY', 'BSESN': 'SENSEX',
}


def nse_base(ticker: str) -> str:
    """Strip .NS/.BO/^ and map index tickers to NSE base names.

    Example: '^NSEI' → 'NIFTY', 'RELIANCE.NS' → 'RELIANCE'
    """
    t = ticker.upper().replace('.NS', '').replace('.BO', '').lstrip('^')
    return _INDEX_MAP.get(t, t)
