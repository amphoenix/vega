"""
Brokerage calculator — broker-agnostic F&O fee model.

Pure domain service — no I/O, no framework imports.

Supports multiple Indian brokers via fee profiles. Each profile specifies:
  flat_per_order  ₹ per order (× 2 for round-trip)
  exchange_txn    % of total turnover
  stt             % of sell-side turnover (Budget 2024: 0.1%)
  sebi            % of total turnover (₹10/crore = 0.0001%)
  stamp           % of buy-side turnover
  gst             % on (brokerage + exchange_txn + SEBI)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..value_objects.money import BrokerageBreakdown


# ── Fee profiles per broker ──────────────────────────────────────────────────

@dataclass(frozen=True)
class FeeProfile:
    """Broker-specific fee rates for F&O."""
    name: str
    flat_per_order: float     # ₹ per order (buy or sell)
    exchange_txn_pct: float   # % of total turnover
    stt_pct: float            # % of sell-side (options) or total (futures)
    sebi_pct: float           # % of total turnover
    stamp_pct: float          # % of buy-side turnover
    gst_pct: float            # % on (brokerage + exchange + SEBI)


INDMONEY = FeeProfile(
    name='INDmoney/INDstocks',
    flat_per_order=20.0,
    exchange_txn_pct=0.0003503,
    stt_pct=0.001,
    sebi_pct=0.000001,
    stamp_pct=0.00003,
    gst_pct=0.18,
)

DHAN = FeeProfile(
    name='Dhan',
    flat_per_order=20.0,
    exchange_txn_pct=0.0003503,
    stt_pct=0.001,
    sebi_pct=0.000001,
    stamp_pct=0.00003,
    gst_pct=0.18,
)

ZERODHA = FeeProfile(
    name='Zerodha',
    flat_per_order=20.0,
    exchange_txn_pct=0.0003503,
    stt_pct=0.001,
    sebi_pct=0.000001,
    stamp_pct=0.00003,
    gst_pct=0.18,
)

GROWW = FeeProfile(
    name='Groww',
    flat_per_order=20.0,
    exchange_txn_pct=0.0003503,
    stt_pct=0.001,
    sebi_pct=0.000001,
    stamp_pct=0.00003,
    gst_pct=0.18,
)

DHAN_CDS = FeeProfile(
    name='Dhan CDS',
    flat_per_order=20.0,
    exchange_txn_pct=0.0000350,  # NSE CDS futures: ₹3.50/lakh (0.0035%)
    stt_pct=0.0001,              # CTT (not STT) — 0.01% on sell side
    sebi_pct=0.000001,           # ₹10/crore
    stamp_pct=0.00002,           # stamp duty on buy: 0.002%
    gst_pct=0.18,
)

_PROFILES: dict[str, FeeProfile] = {
    'indmoney': INDMONEY,  'indstocks': INDMONEY,
    'dhan': DHAN,
    'zerodha': ZERODHA,    'kite': ZERODHA,
    'groww': GROWW,
    'dhan_cds': DHAN_CDS,
}

# Default — used when no broker specified
_default_profile: FeeProfile = INDMONEY


def set_default_profile(broker_name: str) -> None:
    """Set the default fee profile by broker name."""
    global _default_profile
    key = broker_name.lower().strip()
    if key in _PROFILES:
        _default_profile = _PROFILES[key]


def get_profile(broker_name: Optional[str] = None) -> FeeProfile:
    """Get fee profile by name, or the default."""
    if broker_name:
        return _PROFILES.get(broker_name.lower().strip(), _default_profile)
    return _default_profile


# ── Calculator ───────────────────────────────────────────────────────────────

def calc_brokerage(
    entry_premium: float,
    exit_premium: float,
    qty: int,
    profile: Optional[FeeProfile] = None,
) -> BrokerageBreakdown:
    """Full round-trip brokerage for one F&O trade (buy + sell)."""
    p = profile or _default_profile

    buy_val  = entry_premium * qty
    sell_val = exit_premium * qty
    total    = buy_val + sell_val

    flat_brokerage = p.flat_per_order * 2
    exchange_txn   = total    * p.exchange_txn_pct
    stt            = sell_val * p.stt_pct
    sebi           = total    * p.sebi_pct
    stamp          = buy_val  * p.stamp_pct
    gst            = (flat_brokerage + exchange_txn + sebi) * p.gst_pct

    return BrokerageBreakdown(
        flat_brokerage=round(flat_brokerage, 2),
        exchange_txn=round(exchange_txn, 2),
        stt=round(stt, 2),
        sebi=round(sebi, 4),
        stamp=round(stamp, 4),
        gst=round(gst, 2),
    )


def total_brokerage(
    entry_premium: float,
    exit_premium: float,
    qty: int,
    profile: Optional[FeeProfile] = None,
) -> float:
    """Shorthand — returns just the total brokerage amount."""
    return calc_brokerage(entry_premium, exit_premium, qty, profile).total
