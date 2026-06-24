"""
Feature Store — caches pre-computed TA indicators per ticker × timeframe.

Eliminates redundant indicator recalculation across scan cycles. The engine
layer feeds candle data; strategies read cached features via get().

Architecture:
    Engine → FeatureStore.update(ticker, tf, candles) → computes all indicators
    Strategy → FeatureStore.get(ticker, tf) → reads cached dict

Uses shared.indicators for all computations (Supertrend, ADX, RSI, EMA, MACD,
ATR, VWAP, Bollinger) and shared.time for TTL expiry.

Pure domain — no I/O, no threads, no database. Thread-safe via dict copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ...shared.indicators import (
    CandleData,
    ema, rsi, atr, adx_full, supertrend, macd, vwap,
    bollinger_bands,
)
from ...shared.time import now_ist


# ── Feature record ───────────────────────────────────────────────────────────

@dataclass
class FeatureRecord:
    """Cached indicator snapshot for one ticker × timeframe."""
    ticker: str
    timeframe: str
    computed_at: str            # ISO timestamp (IST)
    price: float = 0.0
    supertrend_dir: int = 0    # 1=bullish, -1=bearish
    supertrend_val: float = 0.0
    adx: float = 0.0
    adx_plus_di: float = 0.0
    adx_minus_di: float = 0.0
    rsi: float = 50.0
    ema20: float = 0.0
    ema50: float = 0.0
    atr: float = 0.0
    macd_cross: str = 'NONE'
    macd_hist: float = 0.0
    vwap: float = 0.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict matching fo_scanner technicals format."""
        return {
            'supertrend_dir': self.supertrend_dir,
            'adx': self.adx,
            'adx_plus_di': self.adx_plus_di,
            'adx_minus_di': self.adx_minus_di,
            'rsi': self.rsi,
            'ema20': self.ema20,
            'ema50': self.ema50,
            'atr': self.atr,
            'macd_cross': self.macd_cross,
            'macd_hist': self.macd_hist,
            'vwap': self.vwap,
        }


# ── Feature Store ────────────────────────────────────────────────────────────

@dataclass
class FeatureStoreConfig:
    """Configuration for the feature store."""
    ttl_seconds: int = 300      # how long a cached record is valid
    default_timeframes: list[str] = field(
        default_factory=lambda: ['15m', '1h', '4h'],
    )


class FeatureStore:
    """In-memory cache of pre-computed TA indicators.

    Keyed by (ticker, timeframe). Each entry has a TTL; stale entries
    are returned with a `stale=True` flag but never block reads.
    """

    def __init__(self, config: FeatureStoreConfig | None = None) -> None:
        self._config = config or FeatureStoreConfig()
        self._cache: dict[str, FeatureRecord] = {}  # "TICKER:TF" → record

    @staticmethod
    def _key(ticker: str, tf: str) -> str:
        return f"{ticker}:{tf}"

    def update(
        self,
        ticker: str,
        timeframe: str,
        candles: list[dict[str, Any]],
        session_start: str = '',
    ) -> FeatureRecord:
        """Compute indicators from candle data and cache the result.

        Args:
            ticker: e.g. '^NSEI'
            timeframe: e.g. '15m', '1h', '4h'
            candles: list of candle dicts with open/high/low/close/volume/date
            session_start: for intraday VWAP reset (e.g. '2025-06-18 09:15')

        Returns:
            The freshly computed FeatureRecord.
        """
        cds = [CandleData.from_dict(c) for c in candles]
        closes = [c.close for c in cds]
        price = closes[-1] if closes else 0.0

        # Supertrend
        st = supertrend(cds) or {}
        st_dir = st.get('direction', 0)
        st_val = st.get('value', 0.0)

        # ADX + DI
        adx_data = adx_full(cds) or {}
        adx_val = adx_data.get('adx', 0.0)
        plus_di = adx_data.get('plus_di', 0.0)
        minus_di = adx_data.get('minus_di', 0.0)

        # RSI
        rsi_val = rsi(closes) or 50.0

        # EMA
        ema20_val = ema(closes, 20) or price
        ema50_val = ema(closes, 50) or price

        # ATR
        atr_val = atr(cds) or price * 0.01

        # MACD
        macd_data = macd(closes) or {}
        macd_cross = macd_data.get('cross', 'NONE')
        macd_hist = macd_data.get('histogram', 0.0)

        # VWAP
        vwap_val = vwap(cds, session_start) or 0.0

        # Bollinger Bands
        bb = bollinger_bands(closes) or (0.0, 0.0, 0.0)

        record = FeatureRecord(
            ticker=ticker,
            timeframe=timeframe,
            computed_at=now_ist().isoformat(),
            price=price,
            supertrend_dir=st_dir,
            supertrend_val=st_val,
            adx=adx_val,
            adx_plus_di=plus_di,
            adx_minus_di=minus_di,
            rsi=rsi_val,
            ema20=ema20_val,
            ema50=ema50_val,
            atr=atr_val,
            macd_cross=macd_cross,
            macd_hist=macd_hist,
            vwap=vwap_val,
            bb_upper=bb[0],
            bb_mid=bb[1],
            bb_lower=bb[2],
        )

        self._cache[self._key(ticker, timeframe)] = record
        return record

    def get(self, ticker: str, timeframe: str) -> Optional[FeatureRecord]:
        """Read cached features. Returns None if no entry exists."""
        return self._cache.get(self._key(ticker, timeframe))

    def get_dict(self, ticker: str, timeframe: str) -> dict[str, Any]:
        """Read cached features as a technicals dict (for strategy on_tick).

        Returns empty dict if no entry exists.
        """
        rec = self.get(ticker, timeframe)
        return rec.to_dict() if rec else {}

    def is_stale(self, ticker: str, timeframe: str) -> bool:
        """True if cached entry is older than TTL or doesn't exist."""
        rec = self.get(ticker, timeframe)
        if rec is None:
            return True
        age = (now_ist() - _parse_iso(rec.computed_at)).total_seconds()
        return age > self._config.ttl_seconds

    def get_all_timeframes(
        self, ticker: str, timeframes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get features across multiple timeframes for MTF confluence.

        Returns list of dicts matching fo_scanner's `timeframes` format.
        """
        tfs = timeframes or self._config.default_timeframes
        result = []
        for tf in tfs:
            rec = self.get(ticker, tf)
            if rec is not None:
                d = rec.to_dict()
                d['timeframe'] = tf
                result.append(d)
        return result

    def invalidate(self, ticker: str, timeframe: str = '') -> None:
        """Remove cached entries. If no timeframe, remove all for ticker."""
        if timeframe:
            self._cache.pop(self._key(ticker, timeframe), None)
        else:
            keys = [k for k in self._cache if k.startswith(f"{ticker}:")]
            for k in keys:
                del self._cache[k]

    def clear(self) -> None:
        """Remove all cached entries (daily reset)."""
        self._cache.clear()

    def stats(self) -> dict[str, Any]:
        """Cache statistics for monitoring."""
        stale = sum(1 for k in self._cache
                    if self.is_stale(*k.split(':', 1)))
        return {
            'total_entries': len(self._cache),
            'stale_entries': stale,
            'tickers': list({k.split(':')[0] for k in self._cache}),
        }


def _parse_iso(iso_str: str):
    """Parse ISO timestamp back to datetime (IST-aware)."""
    from ...shared.time import datetime, IST
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt
    except (ValueError, TypeError):
        return now_ist()
