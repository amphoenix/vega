"""Tests for A/B testing framework."""

import pytest

from app.domain.analytics.ab_testing import ABTest, VariantStats


class TestVariantStats:
    def test_empty(self):
        v = VariantStats(label='A')
        assert v.trade_count == 0
        assert v.win_rate == 0.0
        assert v.avg_pnl == 0.0
        assert v.max_drawdown == 0.0
        assert v.sharpe == 0.0

    def test_with_trades(self):
        v = VariantStats(label='A')
        v.trades = [
            {'pnl': 100}, {'pnl': -50}, {'pnl': 200}, {'pnl': -30},
        ]
        assert v.trade_count == 4
        assert v.wins == 2
        assert v.losses == 2
        assert v.win_rate == 50.0
        assert v.total_pnl == 220.0
        assert v.avg_pnl == 55.0

    def test_max_drawdown(self):
        v = VariantStats(label='A')
        v.trades = [
            {'pnl': 100}, {'pnl': -200}, {'pnl': 50},
        ]
        # cumulative: 100, -100, -50 → peak 100, dd at -100 = 200
        assert v.max_drawdown == 200.0

    def test_sharpe(self):
        v = VariantStats(label='A')
        v.trades = [{'pnl': 10}, {'pnl': 10}, {'pnl': 10}]
        # all same → std = 0 → sharpe = 0
        assert v.sharpe == 0.0

    def test_to_dict(self):
        v = VariantStats(label='Test')
        v.trades = [{'pnl': 100}]
        d = v.to_dict()
        assert d['label'] == 'Test'
        assert d['trade_count'] == 1
        assert d['win_rate'] == 100.0


class TestABTest:
    @pytest.fixture
    def ab(self):
        return ABTest(
            name='test_ab',
            description='Test A/B',
            strategy_name='swing_ai',
            traffic_split=50,
            min_trades=3,
            variant_a=VariantStats(label='Control'),
            variant_b=VariantStats(label='Experiment'),
            variant_a_params={'ema_fast': 9},
            variant_b_params={'ema_fast': 13},
        )

    def test_start_stop(self, ab):
        ab.start()
        assert ab.enabled
        assert ab.started_at is not None
        ab.stop()
        assert not ab.enabled

    def test_assign_variant_distribution(self, ab):
        results = {'A': 0, 'B': 0}
        for _ in range(1000):
            v = ab.assign_variant()
            results[v] += 1
        # With 50/50 split, both should be roughly equal (within 10%)
        assert results['A'] > 350
        assert results['B'] > 350

    def test_get_params(self, ab):
        assert ab.get_params('A') == {'ema_fast': 9}
        assert ab.get_params('B') == {'ema_fast': 13}

    def test_record_trade(self, ab):
        ab.record_trade('A', {'pnl': 100, 'symbol': 'NIFTY'})
        ab.record_trade('B', {'pnl': -50, 'symbol': 'NIFTY'})
        assert ab.variant_a.trade_count == 1
        assert ab.variant_b.trade_count == 1

    def test_has_enough_data(self, ab):
        assert not ab.has_enough_data
        for i in range(3):
            ab.record_trade('A', {'pnl': 10 * i})
            ab.record_trade('B', {'pnl': 5 * i})
        assert ab.has_enough_data

    def test_get_winner_a_wins(self, ab):
        # A gets better trades
        for _ in range(5):
            ab.record_trade('A', {'pnl': 100})
            ab.record_trade('B', {'pnl': -50})
        assert ab.get_winner() == 'A'

    def test_get_winner_b_wins(self, ab):
        for _ in range(5):
            ab.record_trade('A', {'pnl': -50})
            ab.record_trade('B', {'pnl': 100})
        assert ab.get_winner() == 'B'

    def test_get_winner_inconclusive(self, ab):
        # Not enough data
        ab.record_trade('A', {'pnl': 100})
        assert ab.get_winner() is None

    def test_get_report(self, ab):
        for _ in range(3):
            ab.record_trade('A', {'pnl': 100})
            ab.record_trade('B', {'pnl': 50})
        report = ab.get_report()
        assert report['name'] == 'test_ab'
        assert report['variant_a']['trade_count'] == 3
        assert report['variant_b']['trade_count'] == 3
        assert report['winner'] == 'A'
        assert report['winner_label'] == 'Control'

    def test_reset(self, ab):
        ab.record_trade('A', {'pnl': 100})
        ab.reset()
        assert ab.variant_a.trade_count == 0
        assert ab.variant_b.trade_count == 0

    def test_from_config(self):
        config = {
            'description': 'Test EMA variants',
            'strategy': 'swing_ai',
            'enabled': True,
            'traffic_split': 60,
            'min_trades': 10,
            'metrics': ['win_rate', 'avg_pnl'],
            'variant_a': {'label': 'Fast', 'params': {'ema': 9}},
            'variant_b': {'label': 'Slow', 'params': {'ema': 21}},
        }
        ab = ABTest.from_config('ema_test', config)
        assert ab.name == 'ema_test'
        assert ab.enabled is True
        assert ab.traffic_split == 60
        assert ab.variant_a.label == 'Fast'
        assert ab.variant_b.label == 'Slow'
        assert ab.variant_a_params == {'ema': 9}
