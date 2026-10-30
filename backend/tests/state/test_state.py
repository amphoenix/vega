"""Server-owned state: settings, watchlist, signal log."""

from app.state import db, settings, signals, watchlist


class TestSettings:
    def test_default_when_missing(self):
        assert settings.get('nope', 42) == 42
        assert settings.get_bool('flag', True) is True

    def test_missing_key_returns_default_every_time(self):
        assert settings.get('nope', 1) == 1
        assert settings.get('nope', 2) == 2          # second miss must not leak a cached sentinel
        settings.set('nope', 3)
        assert settings.get('nope', 9) == 3

    def test_roundtrip_types(self):
        settings.set('flag', False)
        settings.set('cfg', {'a': 1, 'b': [1, 2]})
        assert settings.get_bool('flag', True) is False
        assert settings.get('cfg') == {'a': 1, 'b': [1, 2]}

    def test_survives_restart(self):
        settings.set('scalp_auto_trade', False)
        settings.clear_cache()          # new process: memory empty, file remains
        assert settings.get_bool('scalp_auto_trade', True) is False

    def test_overwrite(self):
        settings.set('k', 1)
        settings.set('k', 2)
        assert settings.get('k') == 2


class TestWatchlist:
    def test_empty_by_default(self):
        assert watchlist.get_watchlist() == []

    def test_set_get_keeps_order_and_fields(self):
        items = [{'symbol': 'tcs', 'name': 'TCS'}, {'symbol': 'INFY', 'exchange': 'NSE'}]
        stored = watchlist.set_watchlist(items)
        assert [i['symbol'] for i in stored] == ['TCS', 'INFY']
        assert watchlist.get_watchlist() == stored
        assert watchlist.get_watchlist()[0]['name'] == 'TCS'

    def test_dedupes_and_drops_blank(self):
        stored = watchlist.set_watchlist([{'symbol': 'a'}, {'symbol': ' A '}, {'symbol': ''}, {}])
        assert [i['symbol'] for i in stored] == ['A']

    def test_replace_removes_old(self):
        watchlist.set_watchlist([{'symbol': 'A'}, {'symbol': 'B'}])
        watchlist.set_watchlist([{'symbol': 'C'}])
        assert [i['symbol'] for i in watchlist.get_watchlist()] == ['C']

    def test_capped(self):
        many = [{'symbol': f'S{i}'} for i in range(watchlist.MAX_ITEMS + 20)]
        assert len(watchlist.set_watchlist(many)) == watchlist.MAX_ITEMS


class TestSignalLog:
    def test_record_and_history_newest_first(self):
        signals.record('swing', {'ticker': '^NSEI', 'verdict': 'BUY', 'confidence': 72, 'action': 'BUY NOW'})
        signals.record('scalp', {'ticker': '^BSESN', 'direction': 'SELL', 'confidence': 77})
        hist = signals.history()
        assert [h['segment'] for h in hist] == ['scalp', 'swing']
        assert hist[1]['ticker'] == '^NSEI' and hist[1]['confidence'] == 72

    def test_filter_by_segment_and_since_id(self):
        signals.record('swing', {'ticker': 'A'})
        signals.record('scalp', {'ticker': 'B'})
        signals.record('swing', {'ticker': 'C'})
        assert [h['ticker'] for h in signals.history(segment='swing')] == ['C', 'A']
        first_id = signals.history(segment='swing')[-1]['id']
        assert [h['ticker'] for h in signals.history(since_id=first_id)] == ['C', 'B']

    def test_limit_is_clamped(self):
        for i in range(5):
            signals.record('swing', {'ticker': str(i)})
        assert len(signals.history(limit=2)) == 2
        assert len(signals.history(limit=10_000)) == 5

    def test_payload_keys_cannot_overwrite_log_fields(self):
        signals.record('swing', {'ticker': 'X', 'id': 999, 'segment': 'evil', 'ts': 'x'})
        h = signals.history()[0]
        assert h['segment'] == 'swing' and h['id'] != 999

    def test_never_raises(self):
        class Bad:
            def __repr__(self): raise RuntimeError('boom')
        signals.record('swing', {'ticker': 'X', 'obj': Bad()})   # must not raise

    def test_prunes_old_rows(self, monkeypatch):
        monkeypatch.setattr(signals, 'KEEP_LAST', 3)
        monkeypatch.setattr(signals, '_PRUNE_EVERY', 5)
        for i in range(10):
            signals.record('swing', {'ticker': str(i)})
        ids = [h['id'] for h in signals.history(limit=500)]
        assert max(ids) == 10 and len(ids) < 10


def test_state_uses_its_own_database_file(tmp_path):
    settings.set('k', 1)
    assert (tmp_path / 'state.db').exists()
    assert db._path.endswith('state.db')
