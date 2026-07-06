"""
Tests for domain/services/feature_store.py — FeatureStore + MTF aggregation.
"""


from app.domain.services.feature_store import (
    FeatureRecord,
    FeatureStore,
    FeatureStoreConfig,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _candles(n: int = 60, base: float = 22000.0, trend: float = 5.0) -> list[dict]:
    """Generate n synthetic candle dicts with a mild uptrend."""
    result = []
    for i in range(n):
        c = base + i * trend
        result.append({
            'open': c - 10, 'high': c + 30, 'low': c - 30,
            'close': c, 'volume': 100_000 + i * 1000,
            'date': f'2025-06-18 09:{15 + i // 4}',
        })
    return result


def _down_candles(n: int = 60, base: float = 22000.0) -> list[dict]:
    """Generate n synthetic candle dicts with a downtrend."""
    result = []
    for i in range(n):
        c = base - i * 5.0
        result.append({
            'open': c + 10, 'high': c + 30, 'low': c - 30,
            'close': c, 'volume': 100_000 + i * 1000,
            'date': f'2025-06-18 09:{15 + i // 4}',
        })
    return result


# ── FeatureStore CRUD ────────────────────────────────────────────────────────

class TestFeatureStoreCRUD:
    def test_update_and_get(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles())
        assert rec.ticker == '^NSEI'
        assert rec.timeframe == '15m'
        assert rec.price > 0

        fetched = fs.get('^NSEI', '15m')
        assert fetched is not None
        assert fetched.ticker == '^NSEI'

    def test_get_nonexistent_returns_none(self):
        fs = FeatureStore()
        assert fs.get('^NSEI', '15m') is None

    def test_get_dict_returns_technicals(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles())
        d = fs.get_dict('^NSEI', '15m')
        assert 'supertrend_dir' in d
        assert 'adx' in d
        assert 'rsi' in d
        assert 'ema20' in d
        assert 'vwap' in d

    def test_get_dict_nonexistent_returns_empty(self):
        fs = FeatureStore()
        assert fs.get_dict('^NSEI', '15m') == {}

    def test_invalidate_single(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles())
        fs.update('^NSEI', '1h', _candles())
        fs.invalidate('^NSEI', '15m')
        assert fs.get('^NSEI', '15m') is None
        assert fs.get('^NSEI', '1h') is not None

    def test_invalidate_all_timeframes(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles())
        fs.update('^NSEI', '1h', _candles())
        fs.invalidate('^NSEI')
        assert fs.get('^NSEI', '15m') is None
        assert fs.get('^NSEI', '1h') is None

    def test_clear(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles())
        fs.update('^BSESN', '15m', _candles())
        fs.clear()
        assert fs.get('^NSEI', '15m') is None
        assert fs.get('^BSESN', '15m') is None

    def test_update_overwrites(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles(60, base=21000))
        r1 = fs.get('^NSEI', '15m')
        fs.update('^NSEI', '15m', _candles(60, base=23000))
        r2 = fs.get('^NSEI', '15m')
        assert r2.price > r1.price


# ── Indicator computation ────────────────────────────────────────────────────

class TestIndicatorComputation:
    def test_supertrend_direction(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.supertrend_dir in (1, -1)

    def test_adx_computed(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.adx >= 0

    def test_rsi_in_range(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert 0 <= rec.rsi <= 100

    def test_ema_values(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.ema20 > 0
        assert rec.ema50 > 0

    def test_macd_cross_valid(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.macd_cross in ('BULLISH', 'BEARISH', 'NONE')

    def test_vwap_computed(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.vwap > 0

    def test_bollinger_bands(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.bb_upper >= rec.bb_mid >= rec.bb_lower

    def test_computed_at_timestamp(self):
        fs = FeatureStore()
        rec = fs.update('^NSEI', '15m', _candles(60))
        assert rec.computed_at != ''
        assert '2' in rec.computed_at  # starts with year


# ── Multi-Timeframe ──────────────────────────────────────────────────────────

class TestMultiTimeframe:
    def test_get_all_timeframes(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles(60))
        fs.update('^NSEI', '1h', _candles(60))
        fs.update('^NSEI', '4h', _candles(60))

        tfs = fs.get_all_timeframes('^NSEI')
        assert len(tfs) == 3
        assert all('timeframe' in tf for tf in tfs)
        assert all('supertrend_dir' in tf for tf in tfs)

    def test_get_all_timeframes_partial(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles(60))
        # only 15m cached, 1h and 4h missing
        tfs = fs.get_all_timeframes('^NSEI')
        assert len(tfs) == 1
        assert tfs[0]['timeframe'] == '15m'

    def test_get_all_timeframes_custom_list(self):
        fs = FeatureStore()
        fs.update('^NSEI', '5m', _candles(60))
        fs.update('^NSEI', '30m', _candles(60))

        tfs = fs.get_all_timeframes('^NSEI', ['5m', '30m'])
        assert len(tfs) == 2

    def test_get_all_timeframes_empty(self):
        fs = FeatureStore()
        tfs = fs.get_all_timeframes('^NSEI')
        assert tfs == []


# ── TTL / Staleness ──────────────────────────────────────────────────────────

class TestStaleness:
    def test_fresh_entry_not_stale(self):
        fs = FeatureStore(FeatureStoreConfig(ttl_seconds=600))
        fs.update('^NSEI', '15m', _candles(60))
        assert not fs.is_stale('^NSEI', '15m')

    def test_nonexistent_is_stale(self):
        fs = FeatureStore()
        assert fs.is_stale('^NSEI', '15m')

    def test_expired_entry_is_stale(self):
        fs = FeatureStore(FeatureStoreConfig(ttl_seconds=0))
        fs.update('^NSEI', '15m', _candles(60))
        # ttl=0 means immediately stale
        assert fs.is_stale('^NSEI', '15m')


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats_basic(self):
        fs = FeatureStore()
        fs.update('^NSEI', '15m', _candles(60))
        fs.update('^BSESN', '15m', _candles(60))
        s = fs.stats()
        assert s['total_entries'] == 2
        assert set(s['tickers']) == {'^NSEI', '^BSESN'}

    def test_stats_empty(self):
        fs = FeatureStore()
        s = fs.stats()
        assert s['total_entries'] == 0


# ── FeatureRecord.to_dict ────────────────────────────────────────────────────

class TestFeatureRecordToDict:
    def test_to_dict_keys(self):
        rec = FeatureRecord(
            ticker='^NSEI', timeframe='15m', computed_at='2025-06-18T10:00:00',
            supertrend_dir=1, adx=30, rsi=62, ema20=22000, ema50=21900,
        )
        d = rec.to_dict()
        expected_keys = {
            'supertrend_dir', 'adx', 'adx_plus_di', 'adx_minus_di',
            'rsi', 'ema20', 'ema50', 'atr', 'macd_cross', 'macd_hist', 'vwap',
        }
        assert set(d.keys()) == expected_keys
