"""Entries must be priced from the live market, never from a stale cached ticket."""

import pytest

from app.engines import fo_scanner as fo
from app.engines import order_executor as oe

SYM = 'NIFTY-NOV2026-23250-CE'


def _ticket(entry=100.0):
    return {
        'trading_symbol': SYM, 'display_symbol': 'NIFTY 3 NOV 23250 CE', 'underlying': 'NIFTY',
        'option_type': 'CE', 'security_id': '123', 'lot_size': 65,
        'entry': {'expected_premium_inr': entry, 'window_ist': 'NOW'},
        'exit': {'stop_loss_inr': entry - 15, 'target_1_inr': entry * 1.3, 'target_2_inr': entry * 1.6},
    }


class TestPureHelpers:
    def test_drift_pct(self):
        assert oe.ticket_price_drift_pct(100, 110) == pytest.approx(10.0)
        assert oe.ticket_price_drift_pct(100, 90) == pytest.approx(10.0)
        assert oe.ticket_price_drift_pct(355.95, 468.45) == pytest.approx(31.6, abs=0.1)   # the fake-profit case
        assert oe.ticket_price_drift_pct(0, 50) == 0.0

    def test_rebase_moves_entry_sl_and_targets(self):
        t = _ticket(100.0)
        oe.rebase_ticket_to_price(t, 120.0)
        assert t['entry']['expected_premium_inr'] == 120.0
        assert t['exit']['stop_loss_inr'] == 105.0                       # price - 15 pts (NIFTY)
        assert t['exit']['target_1_inr'] == pytest.approx(156.0)         # scaled by 1.2
        assert t['exit']['target_2_inr'] == pytest.approx(192.0)

    def test_rebase_sensex_uses_wider_sl(self):
        t = _ticket(300.0)
        t['underlying'] = 'SENSEX'
        oe.rebase_ticket_to_price(t, 300.0)
        assert t['exit']['stop_loss_inr'] == 250.0                       # 50 pts

    def test_rebase_never_negative_sl(self):
        t = _ticket(10.0)
        oe.rebase_ticket_to_price(t, 10.0)
        assert t['exit']['stop_loss_inr'] == 0.05

    def test_live_premium_handles_failures(self, monkeypatch):
        class Broken:
            def get_ltp(self, *a, **k): raise RuntimeError('down')
        monkeypatch.setattr(oe, 'get_broker', lambda: Broken())
        assert oe._live_premium(_ticket(), SYM) is None

        class Zero:
            def get_ltp(self, *a, **k): return 0
        monkeypatch.setattr(oe, 'get_broker', lambda: Zero())
        assert oe._live_premium(_ticket(), SYM) is None

        class Ok:
            def get_ltp(self, *a, **k): return '57.5'
        monkeypatch.setattr(oe, 'get_broker', lambda: Ok())
        assert oe._live_premium(_ticket(), SYM) == 57.5

    def test_paper_detection(self, monkeypatch):
        monkeypatch.setattr(oe._settings, 'trading_mode', 'live')
        monkeypatch.setattr(oe._settings, 'live_trading_enabled', True)
        monkeypatch.setattr(oe._settings, 'fo_mode', 'live')
        assert oe._is_paper_entry() is False
        monkeypatch.setattr(oe._settings, 'fo_mode', 'paper')
        assert oe._is_paper_entry() is True


class FakeBroker:
    def __init__(self, option_ltp, spot=23000.0):
        self.option_ltp, self.spot = option_ltp, spot

    def get_ltp(self, symbol, **kw):
        return self.option_ltp if symbol == SYM else self.spot

    def get_positions(self): return []
    def get_available_cash(self): return 100_000.0


class Boom(RuntimeError):
    pass


@pytest.fixture
def entry_env(monkeypatch):
    """try_auto_entry with every external dependency faked; the order call raises so nothing is written."""
    placed, events = [], []

    def fake_buy(*a, **k):
        placed.append((a, k))
        raise Boom('order would be placed here')

    monkeypatch.setattr(oe, 'auto_trading_enabled', lambda: True)
    monkeypatch.setattr(oe, '_daily_kill_switch', False)
    monkeypatch.setattr(oe, 'min_confidence', lambda: 1)
    monkeypatch.setattr(oe, '_check_day_rollover', lambda: None)
    monkeypatch.setattr(oe, '_swing_pnl_from_db', lambda: 0.0)
    monkeypatch.setattr(oe, '_blocked_symbols', set())
    monkeypatch.setattr(oe, '_symbol_claims', {})
    monkeypatch.setattr(oe, '_inflight', set())
    monkeypatch.setattr(oe, '_scalp_inflight', set())
    monkeypatch.setattr(oe, '_sl_direction_ledger', {})
    monkeypatch.setattr('app.infrastructure.db.tracked_positions.list_tracked', lambda: [])
    monkeypatch.setattr(oe, '_place_fo_buy', fake_buy)
    monkeypatch.setattr(oe, '_order_broadcast', events.append)
    monkeypatch.setattr(oe._settings, 'trading_mode', 'paper')
    return placed, events


def _signal(ticket):
    return {'ticker': '^NSEI', 'confidence': 99, 'ticket': ticket, 'spot': 23000.0}


class TestEntryGate:
    def test_stale_ticket_is_rejected_and_nothing_is_ordered(self, entry_env, monkeypatch):
        placed, events = entry_env
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=131.6))    # +31.6% vs ticket 100
        assert oe.try_auto_entry(_signal(_ticket(100.0))) is None
        assert placed == []
        assert events[-1]['status'] == 'STALE_TICKET'

    def test_stale_ticket_is_dropped_from_scanner_cache(self, entry_env, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=140.0))
        monkeypatch.setattr(fo, '_ticket_cache', {'^NSEI:CE': _ticket(100.0)})
        oe.try_auto_entry(_signal(_ticket(100.0)))
        assert fo._ticket_cache == {}

    def test_fresh_ticket_is_filled_at_live_price(self, entry_env, monkeypatch):
        placed, _ = entry_env
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=52.0))     # +4%: within 10%
        ticket = _ticket(50.0)
        with pytest.raises(Boom):
            oe.try_auto_entry(_signal(ticket))
        assert len(placed) == 1                                      # the order was attempted
        assert ticket['entry']['expected_premium_inr'] == 52.0       # priced from live, not the ticket
        assert ticket['exit']['stop_loss_inr'] == 37.0               # 52 - 15

    def test_paper_entry_skipped_when_no_live_price(self, entry_env, monkeypatch):
        placed, _ = entry_env
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=None))
        assert oe.try_auto_entry(_signal(_ticket(100.0))) is None
        assert placed == []

    def test_live_mode_without_live_price_proceeds_as_before(self, entry_env, monkeypatch):
        placed, _ = entry_env
        monkeypatch.setattr(oe._settings, 'trading_mode', 'live')
        monkeypatch.setattr(oe._settings, 'live_trading_enabled', True)
        monkeypatch.setattr(oe._settings, 'fo_mode', 'live')
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=None))
        ticket = _ticket(50.0)
        ticket['ltp'] = 50.0
        with pytest.raises(Boom):
            oe.try_auto_entry(_signal(ticket))
        assert len(placed) == 1


class TestScannerTicketCache:
    def test_ticket_expires_after_ttl(self, monkeypatch):
        clock = {'t': 1000.0}
        monkeypatch.setattr(fo, 'monotonic', lambda: clock['t'])
        monkeypatch.setattr(fo, '_ticket_cache', {})
        monkeypatch.setattr(fo, '_ticket_cached_at', {})
        ttl = fo._fo_cfg().fo_ticket_ttl_sec
        t = _ticket()
        fo._cache_ticket('^BSESN:CE', t)
        clock['t'] += ttl - 1
        assert fo._cached_ticket('^BSESN:CE') is t                    # still fresh
        clock['t'] += 2
        assert fo._cached_ticket('^BSESN:CE') is None                 # expired and dropped
        assert '^BSESN:CE' not in fo._ticket_cache

    def test_missing_key(self):
        assert fo._cached_ticket('NOPE:CE') is None


# ── Paper orders behave like real market orders ───────────────────────────────

class Quote:
    def __init__(self, ltp=0.0, bid=0.0, ask=0.0):
        self.ltp, self.bid, self.ask = ltp, bid, ask


class QuoteBroker:
    def __init__(self, quote):
        self.quote = quote

    def get_quote(self, symbol, exchange='NFO', security_id=''):
        if isinstance(self.quote, Exception):
            raise self.quote
        return self.quote


class TestPaperFillPrice:
    def test_buy_at_ask_sell_at_bid(self, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(Quote(ltp=86.05, bid=85.90, ask=86.05)))
        assert oe._paper_fill_price('BUY', SYM, 'NFO') == 86.05
        assert oe._paper_fill_price('SELL', SYM, 'NFO') == 85.90

    def test_falls_back_to_last_price_without_bid_ask(self, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(Quote(ltp=50.0)))
        assert oe._paper_fill_price('BUY', SYM, 'NFO') == 50.0
        assert oe._paper_fill_price('SELL', SYM, 'NFO') == 50.0

    def test_ignores_crazy_side_far_from_last(self, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(Quote(ltp=50.0, bid=5.0, ask=90.0)))
        assert oe._paper_fill_price('BUY', SYM, 'NFO') == 50.0
        assert oe._paper_fill_price('SELL', SYM, 'NFO') == 50.0

    def test_no_price_at_all(self, monkeypatch):
        for q in (None, Quote(), RuntimeError('down')):
            monkeypatch.setattr(oe, 'get_broker', lambda q=q: QuoteBroker(q))
            assert oe._paper_fill_price('BUY', SYM, 'NFO') is None

    def test_avg_price_only_from_paper_orders(self):
        assert oe._paper_avg_price({'data': {'order_id': 'PAPER', 'avg_price': 12.5}}) == 12.5
        assert oe._paper_avg_price({'data': {'order_id': 'PAPER'}}) is None
        assert oe._paper_avg_price({'data': {'order_id': 'A1', 'avg_price': 12.5}}) is None
        assert oe._paper_avg_price(None) is None


class TestPaperOrder:
    @pytest.fixture
    def paper(self, monkeypatch):
        monkeypatch.setattr(oe._settings, 'trading_mode', 'paper')
        monkeypatch.setattr(oe, '_order_broadcast', lambda e: None)
        monkeypatch.setattr(oe._mkt, 'is_market_hours', lambda: True)
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(Quote(ltp=86.05, bid=85.90, ask=86.05)))

    def test_buy_fills_at_ask(self, paper):
        r = oe._place_fo_order('BUY', SYM, 65)
        assert r['success'] and r['data']['order_id'] == 'PAPER' and r['data']['avg_price'] == 86.05

    def test_sell_fills_at_bid(self, paper):
        assert oe._place_fo_order('SELL', SYM, 65)['data']['avg_price'] == 85.90

    def test_rejected_when_market_closed(self, paper, monkeypatch):
        monkeypatch.setattr(oe._mkt, 'is_market_hours', lambda: False)
        r = oe._place_fo_order('BUY', SYM, 65)
        assert r['success'] is False and r['data']['status'] == 'REJECTED'

    def test_currency_orders_are_not_blocked_by_nse_fo_hours(self, paper, monkeypatch):
        monkeypatch.setattr(oe._mkt, 'is_market_hours', lambda: False)
        assert oe._place_fo_order('BUY', 'USDINR-X', 1, exchange='CUR')['success'] is True

    def test_buy_without_price_is_rejected(self, paper, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(None))
        assert oe._place_fo_order('BUY', SYM, 65)['success'] is False

    def test_sell_without_price_still_succeeds_so_positions_can_always_close(self, paper, monkeypatch):
        monkeypatch.setattr(oe, 'get_broker', lambda: QuoteBroker(None))
        r = oe._place_fo_order('SELL', SYM, 65)
        assert r['success'] is True and 'avg_price' not in r['data']


class TestReentryCooldown:
    @pytest.fixture(autouse=True)
    def _clean(self, monkeypatch):
        monkeypatch.setattr(oe, '_last_exit_at', {})
        self.now = {'t': 5000.0}
        monkeypatch.setattr(oe, 'clock', lambda: self.now['t'])

    def test_no_cooldown_before_any_exit(self):
        assert oe._reentry_cooldown_left(_ticket()) == 0.0

    def test_cooldown_after_exit_then_expires(self):
        oe._mark_exit(_ticket(), 'swing')
        total = float(oe._settings.fo_reentry_cooldown_sec)
        assert oe._reentry_cooldown_left(_ticket()) == pytest.approx(total)
        self.now['t'] += total - 1
        assert oe._reentry_cooldown_left(_ticket()) == pytest.approx(1.0)
        self.now['t'] += 2
        assert oe._reentry_cooldown_left(_ticket()) == 0.0

    def test_other_direction_and_scalp_are_not_affected(self):
        oe._mark_exit(_ticket(), 'swing')
        pe = _ticket()
        pe['option_type'] = 'PE'
        assert oe._reentry_cooldown_left(pe) == 0.0
        assert oe._reentry_cooldown_left(_ticket(), 'scalp') == 0.0

    def test_entry_is_blocked_during_cooldown(self, entry_env, monkeypatch):
        placed, _ = entry_env
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=50.0))
        oe._mark_exit(_ticket(50.0), 'swing')
        assert oe.try_auto_entry(_signal(_ticket(50.0))) is None
        assert placed == []                                          # no order, no matter how good the signal

    def test_entry_allowed_again_after_cooldown(self, entry_env, monkeypatch):
        placed, _ = entry_env
        monkeypatch.setattr(oe, 'get_broker', lambda: FakeBroker(option_ltp=50.0))
        oe._mark_exit(_ticket(50.0), 'swing')
        self.now['t'] += float(oe._settings.fo_reentry_cooldown_sec) + 1
        with pytest.raises(Boom):
            oe.try_auto_entry(_signal(_ticket(50.0)))
        assert len(placed) == 1


class TestScalpUsesSameAffordabilityRuleInPaperAndLive:
    def test_paper_uses_configured_scalp_capital(self, monkeypatch):
        monkeypatch.setattr(oe._settings, 'trading_mode', 'paper')
        monkeypatch.setattr(oe._settings, 'scalp_capital_inr', 6500.0)

        class NoBroker:
            def get_available_cash(self): raise AssertionError('paper must not read broker cash')
        monkeypatch.setattr(oe, 'get_broker', lambda: NoBroker())
        assert oe._scalp_available_cash() == 6500.0

    def test_live_uses_broker_cash(self, monkeypatch):
        monkeypatch.setattr(oe._settings, 'trading_mode', 'live')
        monkeypatch.setattr(oe._settings, 'live_trading_enabled', True)
        monkeypatch.setattr(oe._settings, 'scalp_mode', 'live')

        class Broker:
            def get_available_cash(self): return 12345.0
        monkeypatch.setattr(oe, 'get_broker', lambda: Broker())
        assert oe._scalp_available_cash() == 12345.0

    def test_live_with_broker_error_means_no_cash_so_entry_is_blocked(self, monkeypatch):
        monkeypatch.setattr(oe._settings, 'trading_mode', 'live')
        monkeypatch.setattr(oe._settings, 'live_trading_enabled', True)
        monkeypatch.setattr(oe._settings, 'scalp_mode', 'live')

        class Broken:
            def get_available_cash(self): raise RuntimeError('down')
        monkeypatch.setattr(oe, 'get_broker', lambda: Broken())
        assert oe._scalp_available_cash() == 0.0
