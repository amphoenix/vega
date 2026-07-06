"""
Multi-format expiry date parser.

Handles every date format seen across Indian broker instrument masters:
  ISO 'YYYY-MM-DD', '2026-04-30T00:00:00'
  IndStocks MM/DD/YYYY (American style)
  Indian DMY 'DD-MM-YYYY', 'DD/MM/YYYY'
  NSE-style 'DD-Mon-YYYY', 'DDMonYY' / 'DDMonYYYY', 'DD Mon YYYY'
  Epoch seconds / millis (int or numeric string)

Pure function — no I/O, no framework imports.
"""

from __future__ import annotations

from .time import date, datetime


def parse_expiry(s) -> date | None:
    """Parse many expiry formats → date or None."""
    if s is None or s == '':
        return None

    # epoch (seconds or millis)
    if isinstance(s, (int, float)):
        try:
            ts = float(s)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            return None

    s = str(s).strip()

    # Numeric string — epoch
    if s.isdigit() and len(s) >= 10:
        try:
            ts = float(s)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            pass

    # ISO datetime — strip time portion
    if 'T' in s:
        s = s.split('T', 1)[0]
    if ' ' in s and ':' in s:
        s = s.split(' ', 1)[0]

    # NOTE: '%m/%d/%Y' must come BEFORE '%d/%m/%Y'. IndStocks ships dates as
    # MM/DD/YYYY (American). With Indian DD/MM/YYYY first, '05/12/2026' would
    # silently parse as Dec 5 instead of May 12, dropping all weekly NIFTY
    # contracts that fall in the first 12 days of any month.
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d %b %Y', '%d %B %Y',
                '%d-%m-%Y', '%m/%d/%Y', '%d/%m/%Y',
                '%d%b%y', '%d%b%Y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s.upper(), fmt).date()
        except Exception:
            continue
    return None


def parse_expiry_datetime(s) -> datetime | None:
    """Parse IndStocks MM/DD/YYYY HH:MM format → datetime or None."""
    if not s:
        return None
    s = str(s).strip()
    try:
        return datetime.strptime(s, '%m/%d/%Y %H:%M')
    except Exception:
        try:
            return datetime.strptime(s[:10], '%m/%d/%Y')
        except Exception:
            return None
