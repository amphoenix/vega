"""
Tests for domain/services/strategy_config_store.py — versioned strategy configs.
"""


from app.domain.services.strategy_config_store import (
    StrategyConfigStore,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _base_params() -> dict:
    return {'min_confidence': 85, 'use_llm': True, 'ema_fast': 9, 'ema_slow': 21}


def _updated_params() -> dict:
    return {'min_confidence': 80, 'use_llm': True, 'ema_fast': 13, 'ema_slow': 34}


# ── Put & Get ────────────────────────────────────────────────────────────────

class TestPutAndGet:
    def test_put_first_version(self):
        store = StrategyConfigStore()
        v = store.put('swing_ai', _base_params())
        assert v.version == 1
        assert v.strategy_name == 'swing_ai'
        assert v.params == _base_params()
        assert v.parent_version == 0

    def test_put_increments_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        v2 = store.put('swing_ai', _updated_params())
        assert v2.version == 2
        assert v2.parent_version == 1

    def test_get_active(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        active = store.get_active('swing_ai')
        assert active is not None
        assert active.version == 2
        assert active.params == _updated_params()

    def test_get_active_none(self):
        store = StrategyConfigStore()
        assert store.get_active('nope') is None

    def test_get_active_params(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        assert store.get_active_params('swing_ai') == _base_params()

    def test_get_active_params_empty(self):
        store = StrategyConfigStore()
        assert store.get_active_params('nope') == {}

    def test_get_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        v1 = store.get_version('swing_ai', 1)
        assert v1 is not None
        assert v1.params == _base_params()

    def test_get_version_missing(self):
        store = StrategyConfigStore()
        assert store.get_version('swing_ai', 999) is None

    def test_author_and_reason(self):
        store = StrategyConfigStore()
        v = store.put('swing_ai', _base_params(), author='aman', reason='initial')
        assert v.author == 'aman'
        assert v.reason == 'initial'

    def test_created_at_timestamp(self):
        store = StrategyConfigStore()
        v = store.put('swing_ai', _base_params())
        assert v.created_at != ''
        assert '20' in v.created_at


# ── History ──────────────────────────────────────────────────────────────────

class TestHistory:
    def test_history_returns_all(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        h = store.history('swing_ai')
        assert len(h) == 2
        assert h[0].version == 1
        assert h[1].version == 2

    def test_history_empty(self):
        store = StrategyConfigStore()
        assert store.history('nope') == []

    def test_latest_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        assert store.latest_version('swing_ai') == 2

    def test_latest_version_zero(self):
        store = StrategyConfigStore()
        assert store.latest_version('nope') == 0


# ── Diff ─────────────────────────────────────────────────────────────────────

class TestDiff:
    def test_diff_changed_params(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        d = store.diff('swing_ai', 1, 2)
        assert d is not None
        assert d.has_changes
        assert 'min_confidence' in d.changed
        assert d.changed['min_confidence'] == (85, 80)
        assert 'ema_fast' in d.changed
        assert 'ema_slow' in d.changed

    def test_diff_added_key(self):
        store = StrategyConfigStore()
        store.put('swing_ai', {'a': 1})
        store.put('swing_ai', {'a': 1, 'b': 2})
        d = store.diff('swing_ai', 1, 2)
        assert 'b' in d.added
        assert d.added['b'] == 2

    def test_diff_removed_key(self):
        store = StrategyConfigStore()
        store.put('swing_ai', {'a': 1, 'b': 2})
        store.put('swing_ai', {'a': 1})
        d = store.diff('swing_ai', 1, 2)
        assert 'b' in d.removed
        assert d.removed['b'] == 2

    def test_diff_no_changes(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _base_params())
        d = store.diff('swing_ai', 1, 2)
        assert not d.has_changes

    def test_diff_missing_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        assert store.diff('swing_ai', 1, 999) is None

    def test_diff_to_dict(self):
        store = StrategyConfigStore()
        store.put('swing_ai', {'a': 1})
        store.put('swing_ai', {'a': 2, 'b': 3})
        d = store.diff('swing_ai', 1, 2)
        dd = d.to_dict()
        assert dd['from_version'] == 1
        assert dd['to_version'] == 2
        assert 'old' in dd['changed']['a']
        assert 'new' in dd['changed']['a']


# ── Rollback ─────────────────────────────────────────────────────────────────

class TestRollback:
    def test_rollback_creates_new_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        v3 = store.rollback('swing_ai', 1)
        assert v3 is not None
        assert v3.version == 3
        assert v3.params == _base_params()
        assert 'rollback to v1' in v3.reason

    def test_rollback_is_active(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        store.rollback('swing_ai', 1)
        active = store.get_active('swing_ai')
        assert active.params == _base_params()

    def test_rollback_preserves_history(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        store.rollback('swing_ai', 1)
        assert len(store.history('swing_ai')) == 3

    def test_rollback_missing_version(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        assert store.rollback('swing_ai', 999) is None

    def test_rollback_with_author(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        v3 = store.rollback('swing_ai', 1, author='aman')
        assert v3.author == 'aman'


# ── Multi-strategy ───────────────────────────────────────────────────────────

class TestMultiStrategy:
    def test_independent_versioning(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('nifty_scalp', {'sl_points': 8})
        assert store.latest_version('swing_ai') == 1
        assert store.latest_version('nifty_scalp') == 1

    def test_strategies_list(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('nifty_scalp', {'sl_points': 8})
        assert set(store.strategies()) == {'swing_ai', 'nifty_scalp'}


# ── Stats ────────────────────────────────────────────────────────────────────

class TestStats:
    def test_stats(self):
        store = StrategyConfigStore()
        store.put('swing_ai', _base_params())
        store.put('swing_ai', _updated_params())
        store.put('nifty_scalp', {'sl_points': 8})
        s = store.stats()
        assert s['strategies'] == 2
        assert s['total_versions'] == 3
        assert s['active']['swing_ai'] == 2

    def test_stats_empty(self):
        store = StrategyConfigStore()
        s = store.stats()
        assert s['strategies'] == 0
        assert s['total_versions'] == 0


# ── ConfigVersion.to_dict ────────────────────────────────────────────────────

class TestConfigVersionToDict:
    def test_to_dict(self):
        store = StrategyConfigStore()
        v = store.put('swing_ai', _base_params(), author='aman', reason='test')
        d = v.to_dict()
        assert d['strategy_name'] == 'swing_ai'
        assert d['version'] == 1
        assert d['author'] == 'aman'
        assert d['reason'] == 'test'
        assert d['params'] == _base_params()
