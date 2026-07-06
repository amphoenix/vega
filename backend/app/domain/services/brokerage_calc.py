"""
Brokerage calculator — segment-based, broker-agnostic fee model.

Pure domain service — no I/O, no framework imports.

Segments (each with its own exchange-mandated fee structure):
  swing / scalp  — NSE F&O (options): STT 0.1%, exchange 0.035%
  forex          — NSE CDS (currency futures): CTT 0.01%, exchange 0.0035%
  crypto         — Binance spot: 0.1%/side + 1% TDS + 30% income tax on gains
  crypto_fo      — Deribit options/perps: 0.03–0.05%/side + 1% TDS + 30% tax

Public API:
  segment_brokerage(segment, entry, exit, qty, **kw) → float
  segment_brokerage_breakdown(segment, entry, exit, qty, **kw) → BrokerageBreakdown

  # Legacy (backward compat) — uses F&O profile
  total_brokerage(entry, exit, qty) → float
  calc_brokerage(entry, exit, qty) → BrokerageBreakdown
"""

from __future__ import annotations

from dataclasses import dataclass

from ..value_objects.money import BrokerageBreakdown

# ── Fee profiles (exchange-mandated, same across all brokers) ────────────────

@dataclass(frozen=True)
class FeeProfile:
    """Fee rates for a market segment."""
    name: str
    flat_per_order: float     # ₹ per order (buy or sell)
    exchange_txn_pct: float   # % of total turnover
    stt_pct: float            # % of sell-side turnover
    sebi_pct: float           # % of total turnover
    stamp_pct: float          # % of buy-side turnover
    gst_pct: float            # % on (brokerage + exchange + SEBI)


# NSE F&O — options (swing, scalp)
FO = FeeProfile(
    name='NSE F&O',
    flat_per_order=20.0,
    exchange_txn_pct=0.0003503,  # ₹35.03/lakh
    stt_pct=0.001,               # STT 0.1% on sell side (Budget 2024)
    sebi_pct=0.000001,           # ₹10/crore
    stamp_pct=0.00003,           # 0.003% on buy side
    gst_pct=0.18,
)

# NSE CDS — currency futures (forex)
CDS = FeeProfile(
    name='NSE CDS',
    flat_per_order=20.0,
    exchange_txn_pct=0.0000350,  # ₹3.50/lakh (0.0035%)
    stt_pct=0.0001,              # CTT 0.01% on sell side
    sebi_pct=0.000001,           # ₹10/crore
    stamp_pct=0.00002,           # 0.002% on buy side
    gst_pct=0.18,
)

# Segment → profile mapping
_SEGMENT_PROFILES: dict[str, FeeProfile] = {
    'swing':  FO,
    'scalp':  FO,
    'fo':     FO,
    'forex':  CDS,
    'cds':    CDS,
}


# ── Indian regulatory profile calculator ─────────────────────────────────────

def _calc_indian_profile(
    entry_premium: float, exit_premium: float, qty: int, profile: FeeProfile,
) -> BrokerageBreakdown:
    """Round-trip brokerage for Indian exchange trades (F&O / CDS)."""
    buy_val  = entry_premium * qty
    sell_val = exit_premium * qty
    total    = buy_val + sell_val

    flat_brokerage = profile.flat_per_order * 2
    exchange_txn   = total    * profile.exchange_txn_pct
    stt            = sell_val * profile.stt_pct
    sebi           = total    * profile.sebi_pct
    stamp          = buy_val  * profile.stamp_pct
    gst            = (flat_brokerage + exchange_txn + sebi) * profile.gst_pct

    return BrokerageBreakdown(
        flat_brokerage=round(flat_brokerage, 2),
        exchange_txn=round(exchange_txn, 2),
        stt=round(stt, 2),
        sebi=round(sebi, 4),
        stamp=round(stamp, 4),
        gst=round(gst, 2),
    )


# ── Crypto spot (Binance) ────────────────────────────────────────────────────

def _calc_crypto(
    entry_premium: float, exit_premium: float, qty: float,
    *, gross_pnl: float = 0.0,
) -> float:
    """Crypto spot round-trip brokerage (Binance + Indian tax).

    Components:
      Exchange fee : 0.10% per side (Binance spot base rate, no BNB discount)
      Income tax   : 31.2% on gains only — 115BBH 30% + 4% health & education
                     cess; no loss offset. Surcharge omitted (income-dependent).

    1% TDS (194S) is intentionally NOT charged here: it is a *creditable* prepaid
    tax adjusted against the 30% liability at ITR filing (and refundable if it
    exceeds final tax), not a standalone cost. Adding it on top of income_tax
    would double-count the same tax.
    """
    entry_notional = entry_premium * qty
    exit_notional  = exit_premium * qty
    exchange_fee = round((entry_notional + exit_notional) * 0.001, 2)
    income_tax   = round(max(0, gross_pnl) * 0.312, 2)
    return round(exchange_fee + income_tax, 2)


# ── Crypto F&O (Deribit) ─────────────────────────────────────────────────────

def _calc_crypto_fo_options(notional_usd: float, gross_pnl: float) -> float:
    """Deribit options (short gamma) brokerage.

    Components:
      Exchange fee : 0.03% of underlying notional × 2 legs
      Income tax   : 31.2% on gains (115BBH 30% + 4% cess), no loss offset

    1% TDS omitted — creditable prepaid tax, not a standalone cost (see _calc_crypto).
    """
    exchange_fee = round(notional_usd * 0.0003 * 2, 2)
    income_tax   = round(max(0, gross_pnl) * 0.312, 2)
    return round(exchange_fee + income_tax, 2)


def _calc_crypto_fo_perps(
    entry_premium: float, exit_premium: float, qty: float,
    *, gross_pnl: float = 0.0,
) -> float:
    """Deribit perpetuals brokerage.

    Components:
      Exchange fee : 0.05% taker per side
      Income tax   : 31.2% on gains (115BBH 30% + 4% cess), no loss offset

    1% TDS omitted — creditable prepaid tax, not a standalone cost (see _calc_crypto).
    """
    entry_notional = entry_premium * qty
    exit_notional  = exit_premium * qty
    exchange_fee = round((entry_notional + exit_notional) * 0.0005, 2)
    income_tax   = round(max(0, gross_pnl) * 0.312, 2)
    return round(exchange_fee + income_tax, 2)


# ══════════════════════════════════════════════════════════════════════════════
# Public API — segment-based
# ══════════════════════════════════════════════════════════════════════════════

def segment_brokerage(
    segment: str,
    entry_premium: float,
    exit_premium: float,
    qty: int | float,
    *,
    gross_pnl: float = 0.0,
    notional_usd: float = 0.0,
    sub_type: str = '',
) -> float:
    """Single entry point — returns total brokerage for any segment.

    Args:
        segment:       'swing', 'scalp', 'forex', 'crypto', 'crypto_fo'
        entry_premium: entry price
        exit_premium:  exit price
        qty:           quantity
        gross_pnl:     pre-computed gross P&L (needed for crypto income tax)
        notional_usd:  underlying notional in USD (crypto_fo options only)
        sub_type:      'options' or 'perps' (crypto_fo only, default='perps')
    """
    seg = segment.lower().strip()

    if seg == 'crypto':
        return _calc_crypto(entry_premium, exit_premium, qty, gross_pnl=gross_pnl)

    if seg == 'crypto_fo':
        if sub_type == 'options' and notional_usd > 0:
            return _calc_crypto_fo_options(notional_usd, gross_pnl)
        return _calc_crypto_fo_perps(entry_premium, exit_premium, qty, gross_pnl=gross_pnl)

    # Indian exchange segments (swing, scalp, forex)
    profile = _SEGMENT_PROFILES.get(seg, FO)
    return _calc_indian_profile(entry_premium, exit_premium, int(qty), profile).total


def segment_brokerage_breakdown(
    segment: str,
    entry_premium: float,
    exit_premium: float,
    qty: int,
) -> BrokerageBreakdown:
    """Detailed breakdown — only for Indian exchange segments."""
    profile = _SEGMENT_PROFILES.get(segment.lower().strip(), FO)
    return _calc_indian_profile(entry_premium, exit_premium, qty, profile)


# ══════════════════════════════════════════════════════════════════════════════
# Legacy API — backward compatible (uses F&O profile)
# ══════════════════════════════════════════════════════════════════════════════

# Keep old broker-name-based profiles for any legacy callers
_PROFILES: dict[str, FeeProfile] = {
    'indmoney': FO, 'indstocks': FO, 'dhan': FO,
    'zerodha': FO, 'kite': FO, 'groww': FO,
}
_default_profile: FeeProfile = FO


def set_default_profile(broker_name: str) -> None:
    global _default_profile
    key = broker_name.lower().strip()
    if key in _PROFILES:
        _default_profile = _PROFILES[key]


def get_profile(broker_name: str | None = None) -> FeeProfile:
    if broker_name:
        return _PROFILES.get(broker_name.lower().strip(), _default_profile)
    return _default_profile


def calc_brokerage(
    entry_premium: float, exit_premium: float, qty: int,
    profile: FeeProfile | None = None,
) -> BrokerageBreakdown:
    """Legacy: round-trip brokerage using F&O profile."""
    return _calc_indian_profile(entry_premium, exit_premium, qty, profile or _default_profile)


def total_brokerage(
    entry_premium: float, exit_premium: float, qty: int,
    profile: FeeProfile | None = None,
) -> float:
    """Legacy: shorthand total."""
    return calc_brokerage(entry_premium, exit_premium, qty, profile).total
