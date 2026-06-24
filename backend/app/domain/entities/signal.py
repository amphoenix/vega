"""
Signal — a scanner-produced trading signal with confidence and direction.

Immutable once created. Carries everything needed for entry decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...shared.time import datetime, now_ist
from ..value_objects.market import MarketType


@dataclass(frozen=True, slots=True)
class Signal:
    """A directional trading signal from a scanner.

    Works across markets:
      - Indian F&O: direction='CE'/'PE', underlying='NIFTY'
      - Crypto: direction='LONG'/'SHORT', underlying='BTC/USDT'
      - Polymarket: direction='YES'/'NO', underlying=condition_id
    """

    underlying: str             # NIFTY, BTC/USDT, condition_id
    direction: str              # CE/PE, LONG/SHORT, YES/NO
    confidence: float           # 0-100
    trade_mode: str             # swing, scalp, research
    spot: float                 # current price at signal time

    market_type: MarketType = MarketType.INDIAN_FO
    symbol: str = ''            # resolved trading symbol (if known)
    strike: int = 0             # F&O only
    expiry: str = ''            # F&O only
    rationale: str = ''         # human/LLM-readable reason
    strategy_name: str = ''     # which strategy emitted this
    timestamp: datetime = field(default_factory=now_ist)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_bullish(self) -> bool:
        return self.direction.upper() in ('CE', 'LONG', 'YES')

    @property
    def is_bearish(self) -> bool:
        return self.direction.upper() in ('PE', 'SHORT', 'NO')

    @property
    def is_strong(self) -> bool:
        return self.confidence >= 85.0

    @property
    def is_crypto(self) -> bool:
        return self.market_type == MarketType.CRYPTO

    @property
    def is_prediction(self) -> bool:
        return self.market_type == MarketType.POLYMARKET
