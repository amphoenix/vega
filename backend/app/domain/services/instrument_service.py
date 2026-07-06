"""
Instrument Service — broker-agnostic F&O instrument resolution.

Responsibilities:
  1. Resolve option contracts by underlying + direction + expiry preference
  2. Look up lot sizes, strike selection, nearest expiry
  3. Delegate to BrokerAdapter for actual instrument master data
  4. Cache-friendly — no repeated broker calls for same query within a session

Pure domain service — uses BrokerAdapter via dependency injection.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

from ...shared.logger import get_logger
from ...shared.time import date, datetime, now_ist, timedelta

logger = get_logger('instrument_service')


# ── Protocols ─────────────────────────────────────────────────────────────────

class InstrumentProvider(Protocol):
    """What the instrument service needs from the broker adapter."""

    def search_instruments(
        self, query: str, exchange: str = '', instrument_type: str = '',
    ) -> list: ...

    def get_instrument(self, symbol: str, exchange: str = 'NFO') -> Any: ...

    def get_ltp(self, symbol: str, exchange: str = 'NFO',
                security_id: str = '') -> float | None: ...


# ── Data objects ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResolvedContract:
    """A fully resolved F&O option contract ready for trading."""
    symbol: str
    security_id: str
    exchange: str
    underlying: str
    direction: str          # CE or PE
    strike: float
    expiry: str             # YYYY-MM-DD
    lot_size: int
    ltp: float              # current premium
    display_name: str = ''


# ── Lot sizes (NSE standard) ─────────────────────────────────────────────────

_LOT_SIZES = {
    'NIFTY': 75,
    'BANKNIFTY': 30,
    'FINNIFTY': 40,
    'MIDCPNIFTY': 50,
    'SENSEX': 20,
    'BANKEX': 30,
}


def get_lot_size(underlying: str) -> int:
    """Get standard lot size for an underlying index."""
    key = underlying.upper().replace('^NSEI', 'NIFTY').replace('^BSESN', 'SENSEX')
    key = key.replace('NIFTY 50', 'NIFTY').replace('NIFTY50', 'NIFTY')
    return _LOT_SIZES.get(key, 50)


def normalize_underlying(symbol: str) -> str:
    """Normalize underlying symbol to canonical form."""
    mapping = {
        '^NSEI': 'NIFTY',
        'NIFTY 50': 'NIFTY',
        'NIFTY50': 'NIFTY',
        '^BSESN': 'SENSEX',
    }
    return mapping.get(symbol.upper(), symbol.upper())


# ── Service ───────────────────────────────────────────────────────────────────

class InstrumentService:
    """Broker-agnostic instrument resolution for F&O trading.

    Usage:
        svc = InstrumentService(broker_adapter)
        contract = svc.resolve_option_contract('NIFTY', 'CE', atm_offset=0)
    """

    def __init__(self, provider: InstrumentProvider) -> None:
        self._provider = provider
        self._cache: dict[str, ResolvedContract] = {}
        self._lock = threading.Lock()

    def resolve_option_contract(
        self,
        underlying: str,
        direction: str,        # CE or PE
        spot_price: float = 0.0,
        atm_offset: int = 0,   # 0 = ATM, +1 = 1 strike OTM, -1 = 1 strike ITM
        min_dte: int = 0,
        max_dte: int = 35,
        strike_gap: float = 0.0,  # auto-detect if 0
    ) -> ResolvedContract | None:
        """Resolve the best option contract for entry.

        Steps:
          1. Normalize underlying
          2. Get spot price if not provided
          3. Calculate ATM strike
          4. Search for matching instruments
          5. Filter by expiry window (min_dte..max_dte)
          6. Pick nearest expiry
          7. Return fully resolved contract with LTP
        """
        ul = normalize_underlying(underlying)
        d = direction.upper()

        # Get spot price from broker if not provided
        if spot_price <= 0:
            spot_price = self._get_spot(ul) or 0.0
            if spot_price <= 0:
                logger.warning('Cannot resolve contract: no spot price for %s', ul)
                return None

        # Determine strike gap
        if strike_gap <= 0:
            strike_gap = _default_strike_gap(ul)

        # Calculate ATM strike
        atm_strike = round(spot_price / strike_gap) * strike_gap

        # Apply offset
        if d == 'CE':
            target_strike = atm_strike + (atm_offset * strike_gap)
        else:
            target_strike = atm_strike - (atm_offset * strike_gap)

        # Search for matching instruments
        query = f'{ul}'
        instruments = self._provider.search_instruments(
            query=query, exchange='NFO', instrument_type=d,
        )

        if not instruments:
            logger.warning('No %s instruments found for %s', d, ul)
            return None

        # Filter by strike and expiry
        today = now_ist().date()
        min_expiry = today + timedelta(days=min_dte)
        max_expiry = today + timedelta(days=max_dte)

        candidates = []
        for inst in instruments:
            # Check strike matches
            inst_strike = getattr(inst, 'strike', 0.0)
            if abs(inst_strike - target_strike) > 0.01:
                continue

            # Check instrument type matches direction
            inst_type = getattr(inst, 'instrument_type', '')
            if inst_type.upper() != d:
                continue

            # Check expiry window
            expiry_str = getattr(inst, 'expiry', '')
            if not expiry_str:
                continue
            try:
                exp_date = _parse_expiry_date(expiry_str)
            except Exception:
                continue
            if exp_date < min_expiry or exp_date > max_expiry:
                continue

            candidates.append((inst, exp_date))

        if not candidates:
            logger.warning('No %s %s contracts at strike %.0f within %d-%d DTE',
                          ul, d, target_strike, min_dte, max_dte)
            return None

        # Sort by expiry (nearest first)
        candidates.sort(key=lambda x: x[1])
        best_inst, best_expiry = candidates[0]

        # Get LTP
        symbol = getattr(best_inst, 'symbol', '')
        security_id = getattr(best_inst, 'security_id', '')
        ltp = self._provider.get_ltp(symbol, 'NFO', security_id) or 0.0

        lot_size = getattr(best_inst, 'lot_size', 0) or get_lot_size(ul)

        contract = ResolvedContract(
            symbol=symbol,
            security_id=security_id,
            exchange='NFO',
            underlying=ul,
            direction=d,
            strike=getattr(best_inst, 'strike', target_strike),
            expiry=best_expiry.isoformat(),
            lot_size=lot_size,
            ltp=ltp,
            display_name=getattr(best_inst, 'name', symbol),
        )

        # Cache it
        cache_key = f'{ul}:{d}:{target_strike}:{best_expiry}'
        with self._lock:
            self._cache[cache_key] = contract

        logger.info('Resolved: %s %s %.0f exp=%s lot=%d ltp=%.2f',
                    ul, d, contract.strike, contract.expiry, lot_size, ltp)
        return contract

    def get_lot_size(self, underlying: str) -> int:
        """Get lot size for an underlying."""
        return get_lot_size(underlying)

    def clear_cache(self) -> None:
        """Clear instrument cache (e.g. at day start)."""
        with self._lock:
            self._cache.clear()

    def _get_spot(self, underlying: str) -> float | None:
        """Get spot price for an underlying index."""
        # Try common spot symbols
        spot_symbols = {
            'NIFTY': ['NIFTY 50', 'Nifty 50', '^NSEI'],
            'SENSEX': ['SENSEX', 'S&P BSE SENSEX', '^BSESN'],
            'BANKNIFTY': ['NIFTY BANK', 'Nifty Bank'],
            'FINNIFTY': ['NIFTY FIN SERVICE', 'Nifty Fin Service'],
        }
        symbols = spot_symbols.get(underlying, [underlying])
        for sym in symbols:
            ltp = self._provider.get_ltp(sym, 'NSE', '')
            if ltp and ltp > 0:
                return ltp
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_strike_gap(underlying: str) -> float:
    """Default strike price gap for an underlying."""
    gaps = {
        'NIFTY': 50.0,
        'BANKNIFTY': 100.0,
        'FINNIFTY': 50.0,
        'MIDCPNIFTY': 25.0,
        'SENSEX': 100.0,
        'BANKEX': 100.0,
    }
    return gaps.get(underlying, 50.0)


def _parse_expiry_date(expiry_str: str) -> date:
    """Parse expiry string to date object."""
    # Try ISO format first (YYYY-MM-DD)
    try:
        return date.fromisoformat(expiry_str[:10])
    except (ValueError, IndexError):
        pass

    # Try MM/DD/YYYY
    try:
        return datetime.strptime(expiry_str, '%m/%d/%Y').date()
    except ValueError:
        pass

    # Try DD-MM-YYYY
    try:
        return datetime.strptime(expiry_str, '%d-%m-%Y').date()
    except ValueError:
        pass

    # Try epoch timestamp
    try:
        ts = float(expiry_str)
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts).date()
    except (ValueError, TypeError, OSError):
        pass

    raise ValueError(f'Cannot parse expiry: {expiry_str}')
