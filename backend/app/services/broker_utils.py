"""
Broker / market utilities shared by position_monitor, fo_scanner, market API.

Centralises:
  - IST-aware time helpers + NSE trading-day calendar
  - F&O instrument-master lookup (lot size, security_id, expiry, strike)
  - Indian options/futures fee model (STT, exchange, SEBI, GST, stamp, brokerage)

Everything assumes Indian market (NSE / BSE F&O) and IST tz.
"""
from __future__ import annotations

import os
from datetime import datetime, date, time as dt_time
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo('Asia/Kolkata')
except Exception:                              # pragma: no cover
    _IST = None

# ── Market hours (IST) ────────────────────────────────────────────────────────
MARKET_OPEN  = dt_time(9, 15)
MARKET_CLOSE = dt_time(15, 30)
SAFE_OPEN    = dt_time(9, 20)   # avoid first 5min volatility / opening auction tail
SAFE_CLOSE   = dt_time(15, 0)   # last entry before square-off ramp
FORCE_EXIT   = dt_time(15, 10)  # close every MIS leg before broker auto-square-off
THETA_EXIT   = dt_time(13, 0)   # on expiry day, exit options after this

# ── NSE holiday list (yyyy-mm-dd) ────────────────────────────────────────────
# Update yearly from https://www.nseindia.com/resources/exchange-communication-holidays
# Includes trading-holidays only (not settlement). Edit as NSE publishes.
NSE_HOLIDAYS_2025 = {
    '2025-02-26',  # Mahashivratri
    '2025-03-14',  # Holi
    '2025-03-31',  # Id-Ul-Fitr (Ramzan Id)
    '2025-04-10',  # Shri Mahavir Jayanti
    '2025-04-14',  # Dr. Baba Saheb Ambedkar Jayanti
    '2025-04-18',  # Good Friday
    '2025-05-01',  # Maharashtra Day
    '2025-08-15',  # Independence Day
    '2025-08-27',  # Ganesh Chaturthi
    '2025-10-02',  # Mahatma Gandhi Jayanti / Dussehra
    '2025-10-20',  # Diwali Laxmi Pujan (special muhurat session only)
    '2025-10-22',  # Diwali-Balipratipada
    '2025-11-05',  # Prakash Gurpurb Sri Guru Nanak Dev
    '2025-12-25',  # Christmas
}
NSE_HOLIDAYS_2026 = {
    '2026-01-26',  # Republic Day
    '2026-03-03',  # Mahashivratri
    '2026-03-25',  # Holi
    '2026-04-03',  # Good Friday
    '2026-04-14',  # Dr. Baba Saheb Ambedkar Jayanti
    '2026-05-01',  # Maharashtra Day
    '2026-08-15',  # Independence Day
    '2026-10-02',  # Gandhi Jayanti
    '2026-11-09',  # Diwali Balipratipada (placeholder)
    '2026-12-25',  # Christmas
}
NSE_HOLIDAYS: set[str] = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026


def now_ist() -> datetime:
    """Current datetime in IST (timezone-aware if zoneinfo available)."""
    if _IST:
        return datetime.now(_IST)
    return datetime.now()


def today_ist() -> date:
    return now_ist().date()


def is_trading_day(d: Optional[date] = None) -> bool:
    """True iff `d` is a weekday and not in the NSE holiday list."""
    d = d or today_ist()
    if d.weekday() >= 5:                          # Sat/Sun
        return False
    return d.isoformat() not in NSE_HOLIDAYS


def is_market_hours() -> bool:
    n = now_ist()
    return is_trading_day(n.date()) and (MARKET_OPEN <= n.time() <= MARKET_CLOSE)


def is_safe_hours() -> bool:
    """Window during which we are willing to OPEN new positions."""
    n = now_ist()
    return is_trading_day(n.date()) and (SAFE_OPEN <= n.time() <= SAFE_CLOSE)


def is_force_exit_time() -> bool:
    n = now_ist()
    return is_trading_day(n.date()) and n.time() >= FORCE_EXIT


def seconds_until_market_open() -> int:
    """Seconds from now until the next NSE market-open bell (09:15 IST).

    Returns 0 if the market is currently in hours. Skips weekends and NSE
    holidays — e.g. on Saturday this returns the seconds until Monday 09:15
    (or Tuesday 09:15 if Monday is a holiday). Used by the F&O scanner loop
    to compute wake-aligned sleep duration so the first cycle fires within
    a second of market open instead of up to 60s late.
    """
    from datetime import timedelta
    n = now_ist()
    if is_market_hours():
        return 0

    candidate = None
    today_open = n.replace(hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute,
                           second=0, microsecond=0)
    if n < today_open and is_trading_day(n.date()):
        candidate = today_open
    else:
        d = n.date()
        for _ in range(14):  # at most 2 weeks of holidays
            d = d + timedelta(days=1)
            if is_trading_day(d):
                if _IST:
                    candidate = datetime.combine(d, MARKET_OPEN, tzinfo=_IST)
                else:
                    candidate = datetime.combine(d, MARKET_OPEN)
                break
    if candidate is None:
        return 86400  # safety fallback: 1 day
    return max(0, int((candidate - n).total_seconds()))


# ── F&O instrument master lookup ─────────────────────────────────────────────
# Tolerant column-name resolver — different broker masters use different keys.
_FIELD_ALIASES = {
    'trading_symbol': ('TRADING_SYMBOL', 'tradingsymbol', 'tradingSymbol',
                       'symbol', 'SYMBOL', 'TRD_SYMBOL', 'name'),
    'security_id':    ('SECURITY_ID', 'security_id', 'securityId',
                       'isin', 'ISIN', 'token', 'TOKEN', 'instrument_token',
                       'INSTRUMENT_TOKEN'),
    'exchange':       ('EXCH', 'exchange', 'EXCHANGE', 'segment', 'SEGMENT',
                       'EXCH_SEG'),
    'lot_size':       ('LOT_SIZE', 'lot_size', 'lotSize', 'MARKET_LOT',
                       'market_lot', 'LOTSIZE', 'LOT_UNITS', 'lot_units'),
    'strike':         ('STRIKE_PRICE', 'strike_price', 'strikePrice',
                       'STRIKE', 'strike'),
    'expiry':         ('EXPIRY_DATE', 'expiry_date', 'expiryDate',
                       'EXPIRY', 'expiry', 'EXPIRY_DT'),
    'option_type':    ('OPTION_TYPE', 'option_type', 'optionType',
                       'OPT_TYPE', 'opt_type', 'instrument_type',
                       'INSTRUMENT_TYPE'),
}


def _field(inst: dict, key: str, default=''):
    """Case-insensitive, alias-tolerant getter for instrument-master rows."""
    for alias in _FIELD_ALIASES.get(key, ()):
        if alias in inst and inst[alias] not in (None, ''):
            return inst[alias]
    # Last-ditch case-insensitive scan
    lower = {k.lower(): v for k, v in inst.items()}
    for alias in _FIELD_ALIASES.get(key, ()):
        v = lower.get(alias.lower())
        if v not in (None, ''):
            return v
    return default


def _parse_expiry(s) -> Optional[date]:
    """
    Accept many expiry formats seen across Indian broker masters:
      ISO 'YYYY-MM-DD', '2026-04-30T00:00:00'
      Indian DMY 'DD-MM-YYYY', 'DD/MM/YYYY'
      NSE-style 'DD-Mon-YYYY', 'DDMonYY' / 'DDMonYYYY'
      epoch seconds / millis (int or numeric string)
    """
    if s is None or s == '':
        return None
    # epoch (seconds or millis)
    if isinstance(s, (int, float)):
        try:
            ts = float(s)
            if ts > 1e12: ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            return None
    s = str(s).strip()
    if s.isdigit() and len(s) >= 10:
        try:
            ts = float(s)
            if ts > 1e12: ts /= 1000.0
            return datetime.utcfromtimestamp(ts).date()
        except Exception:
            pass
    # ISO datetime — strip time portion
    if 'T' in s:
        s = s.split('T', 1)[0]
    if ' ' in s and ':' in s:
        s = s.split(' ', 1)[0]
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y', '%d-%m-%Y', '%d/%m/%Y',
                '%m/%d/%Y', '%d%b%y', '%d%b%Y', '%Y/%m/%d', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(s.upper(), fmt).date()
        except Exception:
            continue
    return None


def fno_meta(trading_symbol: str) -> Optional[dict]:
    """
    Look up an F&O contract in the IndStocks instrument master.

    Returns:
      {
        'security_id':    str,
        'exchange':       'NFO' | 'BFO',
        'lot_size':       int,
        'expiry':         date | None,
        'strike':         float,
        'option_type':    'CE' | 'PE' | 'XX',  # XX for futures
        'trading_symbol': str,
      }
    or None if not found.
    """
    try:
        from ..api.indmoney import _load_instruments
    except Exception:
        return None

    sym = (trading_symbol or '').strip().upper()
    if not sym:
        return None
    for inst in _load_instruments('fno'):
        t_sym = str(_field(inst, 'trading_symbol', '')).strip().upper()
        if t_sym != sym:
            continue
        # We're already iterating the F&O master so every row IS a derivative.
        # The IndStocks F&O CSV uses EXCH='NSE'/'BSE' (not 'NFO'/'BFO') so we
        # synthesise the F&O exchange code from the underlying exchange.
        t_exch = str(_field(inst, 'exchange', '')).strip().upper()
        if t_exch.startswith('B'):
            t_exch = 'BFO'
        else:
            t_exch = 'NFO'   # default — covers 'NSE', 'NFO', 'NSE_FO', etc.
        try:    lot = int(float(_field(inst, 'lot_size', 0) or 0))
        except: lot = 0
        try:    strike = float(_field(inst, 'strike', 0) or 0)
        except: strike = 0.0
        return {
            'security_id':    str(_field(inst, 'security_id', '')).strip(),
            'exchange':       t_exch,
            'lot_size':       lot,
            'expiry':         _parse_expiry(_field(inst, 'expiry', '')),
            'strike':         strike,
            'option_type':    str(_field(inst, 'option_type', 'XX')).strip().upper(),
            'trading_symbol': t_sym,
        }
    return None


def underlying_lot_size(underlying: str) -> Optional[int]:
    """
    Find the standard lot size for an underlying (e.g. 'NIFTY', 'RELIANCE')
    by inspecting the nearest-expiry future in the F&O master.
    Returns None if not found.
    """
    try:
        from ..api.indmoney import _load_instruments
    except Exception:
        return None

    base = (underlying or '').upper().replace('.NS', '').replace('.BO', '').lstrip('^')
    base = {'NSEI': 'NIFTY', 'NSEBANK': 'BANKNIFTY', 'CNXFIN': 'FINNIFTY'}.get(base, base)

    today = today_ist()
    best_lot, best_exp = None, None
    for inst in _load_instruments('fno'):
        t_sym = str(_field(inst, 'trading_symbol', '')).strip().upper()
        if not t_sym.startswith(base):
            continue
        opt = str(_field(inst, 'option_type', 'XX')).strip().upper()
        try:
            lot = int(float(_field(inst, 'lot_size', 0) or 0))
        except Exception:
            continue
        if lot <= 0:
            continue
        exp = _parse_expiry(_field(inst, 'expiry', ''))
        if not exp or exp < today:
            continue
        if best_exp is None or exp < best_exp:
            best_lot, best_exp = lot, exp
        if opt == 'XX' and (best_exp is None or exp <= best_exp):
            return lot
    return best_lot


# ── Indian F&O fee model (intraday MIS, NSE) ─────────────────────────────────
# Sources: SEBI/NSE/exchange circulars (verify yearly).
#   STT (options sell-side): 0.1 % of premium  (raised from 0.0625 %, Oct-2024)
#   STT (futures sell-side): 0.02 % of turnover
#   Exchange txn (options): 0.03503 % of premium
#   Exchange txn (futures): 0.0019 % of turnover
#   SEBI fee: ₹10 / crore on both sides
#   GST: 18 % on (brokerage + exchange + SEBI fees)
#   Stamp duty (buy-side only): options 0.003 %, futures 0.002 %
#   Brokerage: ₹20 flat per executed order (Zerodha/Dhan typical) — IndMoney similar
def compute_fees(
    side: str,                    # 'BUY' | 'SELL'
    instrument_type: str,         # 'CE'|'PE'|'FUT'|'EQ'
    price: float,                 # premium (options) or price (fut/eq)
    qty_units: int,               # total units = lots × lot_size (or shares for EQ)
    brokerage_per_order: float = 20.0,
) -> dict:
    """
    Returns a breakdown {brokerage, stt, exchange, sebi, gst, stamp, total}.
    Equity intraday handled too but main use is F&O.
    """
    side = side.upper()
    itype = instrument_type.upper()
    turnover = float(price) * int(qty_units)

    if itype in ('CE', 'PE'):
        stt      = 0.001    * turnover if side == 'SELL' else 0.0
        exch     = 0.0003503 * turnover
        stamp    = 0.00003   * turnover if side == 'BUY' else 0.0
    elif itype == 'FUT':
        stt      = 0.0002 * turnover if side == 'SELL' else 0.0
        exch     = 0.0000019 * turnover
        stamp    = 0.00002 * turnover if side == 'BUY' else 0.0
    else:    # EQ intraday MIS
        stt      = 0.00025 * turnover if side == 'SELL' else 0.0
        exch     = 0.0000297 * turnover
        stamp    = 0.00003 * turnover if side == 'BUY' else 0.0

    sebi  = (10.0 / 1e7) * turnover
    brok  = float(brokerage_per_order)
    gst   = 0.18 * (brok + exch + sebi)
    total = round(brok + stt + exch + sebi + gst + stamp, 2)
    return {
        'brokerage': round(brok, 2),
        'stt':       round(stt, 2),
        'exchange':  round(exch, 2),
        'sebi':      round(sebi, 2),
        'gst':       round(gst, 2),
        'stamp':     round(stamp, 2),
        'total':     total,
    }


# ── Mode flag ────────────────────────────────────────────────────────────────
def is_live_mode() -> bool:
    """LIVE TRADING ONLY. Paper mode is permanently disabled."""
    return True
