"""
Multi-market abstractions — tests for Instrument, MarketType, Currency,
ExchangeAdapter, Strategy plugin, and multi-market Signal/Trade.
"""

from datetime import date

import pytest

from app.domain.entities.signal import Signal
from app.domain.entities.trade import Trade, TradeState
from app.domain.regime.regime_engine import Regime
from app.domain.strategies.base import (
    StrategyBase,
    StrategyConfig,
    StrategyResult,
    StrategyState,
)
from app.domain.value_objects.instrument import (
    crypto_instrument,
    indian_fo_instrument,
    polymarket_instrument,
)
from app.domain.value_objects.market import AssetClass, Currency, ExchangeId, MarketType
from app.domain.value_objects.money import Money
from app.domain.value_objects.option_leg import OptionLeg, OptionType
from app.infrastructure.exchange.base import Balance, ExchangePosition, Order, Ticker

# ═══════════════════════════════════════════════════════════════════════
# MarketType + Currency
# ═══════════════════════════════════════════════════════════════════════

class TestMarketType:
    def test_all_markets_exist(self):
        assert MarketType.INDIAN_FO.value == 'indian_fo'
        assert MarketType.CRYPTO.value == 'crypto'
        assert MarketType.POLYMARKET.value == 'polymarket'

    def test_no_us_equity(self):
        """Architecture reflects reality — we don't trade US markets."""
        assert not hasattr(MarketType, 'US_EQUITY')


class TestAssetClass:
    def test_all_asset_classes(self):
        assert AssetClass.INDIAN_OPTION.value == 'indian_option'
        assert AssetClass.CRYPTO_PERP.value == 'crypto_perp'
        assert AssetClass.CRYPTO_OPTION.value == 'crypto_option'
        assert AssetClass.PREDICTION_MARKET.value == 'prediction_market'

    def test_infer_from_market(self):
        assert AssetClass.for_market(MarketType.INDIAN_FO) == AssetClass.INDIAN_OPTION
        assert AssetClass.for_market(MarketType.CRYPTO) == AssetClass.CRYPTO_PERP
        assert AssetClass.for_market(MarketType.CRYPTO, is_option=True) == AssetClass.CRYPTO_OPTION
        assert AssetClass.for_market(MarketType.POLYMARKET) == AssetClass.PREDICTION_MARKET


class TestCurrency:
    def test_default_for_market(self):
        assert Currency.for_market(MarketType.INDIAN_FO) == Currency.INR
        assert Currency.for_market(MarketType.CRYPTO) == Currency.USDT
        assert Currency.for_market(MarketType.POLYMARKET) == Currency.USDC

    def test_all_currencies(self):
        for c in [Currency.INR, Currency.USDC, Currency.USDT,
                  Currency.BTC, Currency.ETH]:
            assert isinstance(c.value, str)

    def test_no_usd(self):
        """USD removed — not a traded currency."""
        assert not hasattr(Currency, 'USD')


class TestExchangeId:
    def test_key_exchanges(self):
        assert ExchangeId.INDMONEY.value == 'indmoney'
        assert ExchangeId.BINANCE.value == 'binance'
        assert ExchangeId.POLYMARKET.value == 'polymarket'
        assert ExchangeId.DHAN.value == 'dhan'


# ═══════════════════════════════════════════════════════════════════════
# Instrument
# ═══════════════════════════════════════════════════════════════════════

class TestIndianFOInstrument:
    def test_factory(self):
        inst = indian_fo_instrument(
            trading_symbol='NIFTY26JUN18500CE',
            underlying='NIFTY', strike=18500,
            expiry=date(2026, 6, 25),
            option_type='CE', lot_size=75,
        )
        assert inst.market == MarketType.INDIAN_FO
        assert inst.exchange == ExchangeId.INDMONEY
        assert inst.is_option
        assert not inst.is_crypto
        assert not inst.is_prediction
        assert inst.settle_currency == Currency.INR
        assert inst.asset_class == AssetClass.INDIAN_OPTION
        assert inst.underlying == 'NIFTY'
        assert inst.lot_size == 75

    def test_display_name(self):
        inst = indian_fo_instrument(
            trading_symbol='NIFTY26JUN18500CE',
            underlying='NIFTY', strike=18500,
            expiry=date(2026, 6, 25),
            option_type='CE', lot_size=75,
        )
        assert inst.display_name == 'NIFTY26JUN18500CE'

    def test_str(self):
        inst = indian_fo_instrument(
            trading_symbol='NIFTY26JUN18500CE',
            underlying='NIFTY', strike=18500,
            expiry=date(2026, 6, 25),
            option_type='CE', lot_size=75,
        )
        assert str(inst) == 'indmoney:NIFTY26JUN18500CE'


class TestCryptoInstrument:
    def test_spot_factory(self):
        inst = crypto_instrument('BTC/USDT', exchange=ExchangeId.BINANCE)
        assert inst.market == MarketType.CRYPTO
        assert inst.exchange == ExchangeId.BINANCE
        assert inst.base_currency == 'BTC'
        assert inst.quote_currency == 'USDT'
        assert inst.is_crypto
        assert not inst.is_option
        assert not inst.is_perpetual
        assert inst.settle_currency == Currency.USDT
        assert inst.asset_class == AssetClass.CRYPTO_PERP

    def test_perp_factory(self):
        inst = crypto_instrument('ETH/USDT:USDT', is_perpetual=True)
        assert inst.is_perpetual
        assert inst.base_currency == 'ETH'
        assert inst.quote_currency == 'USDT'

    def test_fees(self):
        inst = crypto_instrument('SOL/USDT', maker_fee=0.0002, taker_fee=0.0004)
        assert inst.maker_fee == 0.0002
        assert inst.taker_fee == 0.0004

    def test_display_name(self):
        inst = crypto_instrument('BTC/USDT')
        assert inst.display_name == 'BTC/USDT'


class TestPolymarketInstrument:
    def test_factory(self):
        inst = polymarket_instrument(
            condition_id='0xabc123',
            question='Will Trump win 2028?',
            outcome='YES',
        )
        assert inst.market == MarketType.POLYMARKET
        assert inst.exchange == ExchangeId.POLYMARKET
        assert inst.is_prediction
        assert inst.condition_id == '0xabc123'
        assert inst.outcome == 'YES'
        assert inst.settle_currency == Currency.USDC
        assert inst.asset_class == AssetClass.PREDICTION_MARKET

    def test_display_name_truncates(self):
        inst = polymarket_instrument(
            condition_id='0xabc123',
            question='Will the Federal Reserve cut interest rates in July 2026?',
            outcome='NO',
        )
        assert '[NO]' in inst.display_name
        assert len(inst.display_name) <= 60

    def test_no_question_falls_back_to_symbol(self):
        inst = polymarket_instrument(condition_id='0xabc123', question='')
        assert inst.display_name == '0xabc123'


# ═══════════════════════════════════════════════════════════════════════
# Money — multi-currency
# ═══════════════════════════════════════════════════════════════════════

class TestMoneyCurrency:
    def test_default_is_inr(self):
        m = Money(100.0)
        assert m.currency == 'INR'
        assert '₹' in str(m)

    def test_usdt_money(self):
        m = Money(50.0, 'USDT')
        assert m.currency == 'USDT'
        assert '$' in str(m)

    def test_same_currency_arithmetic(self):
        a = Money(100.0, 'USDT')
        b = Money(50.0, 'USDT')
        c = a + b
        assert c.amount == 150.0
        assert c.currency == 'USDT'

    def test_mixed_currency_raises(self):
        a = Money(100.0, 'INR')
        b = Money(50.0, 'USDT')
        with pytest.raises(ValueError, match='Cannot mix'):
            a + b

    def test_conversion(self):
        inr = Money(83000.0, 'INR')
        usdt = inr.to_currency('USDT', 1 / 83.0)
        assert usdt.currency == 'USDT'
        assert abs(usdt.amount - 1000.0) < 1.0

    def test_zero_with_currency(self):
        z = Money.zero('USDC')
        assert z.amount == 0.0
        assert z.currency == 'USDC'

    def test_negation_preserves_currency(self):
        m = Money(100.0, 'BTC')
        assert (-m).currency == 'BTC'
        assert (-m).amount == -100.0

    def test_multiply_preserves_currency(self):
        m = Money(10.0, 'ETH')
        assert (m * 3).currency == 'ETH'
        assert (3 * m).amount == 30.0


# ═══════════════════════════════════════════════════════════════════════
# Trade — multi-market
# ═══════════════════════════════════════════════════════════════════════

class TestTradeMultiMarket:
    def test_fo_trade_backward_compat(self):
        """Existing F&O trades still work with option_leg."""
        leg = OptionLeg(
            trading_symbol='NIFTY26JUN18500CE', strike=18500,
            expiry=date(2026, 6, 25), option_type=OptionType.CE, lot_size=75,
        )
        trade = Trade(trade_id='FO-1', option_leg=leg, direction='CE',
                      trade_mode='swing', qty=75)
        assert trade.market_type == MarketType.INDIAN_FO
        assert trade.symbol == 'NIFTY26JUN18500CE'

    def test_crypto_trade(self):
        inst = crypto_instrument('BTC/USDT')
        trade = Trade(trade_id='CRYPTO-1', instrument=inst, direction='LONG',
                      trade_mode='swing', qty=0.05)
        assert trade.market_type == MarketType.CRYPTO
        assert trade.symbol == 'BTC/USDT'
        trade.fill(67500.0, 'BIN-ORDER-123')
        assert trade.state == TradeState.OPEN

    def test_polymarket_trade(self):
        inst = polymarket_instrument('0xabc', 'Will BTC hit 100k?', 'YES')
        trade = Trade(trade_id='POLY-1', instrument=inst, direction='YES',
                      trade_mode='research', qty=100)
        assert trade.market_type == MarketType.POLYMARKET
        trade.fill(0.65, 'POLY-ORDER-1')
        assert trade.state == TradeState.OPEN
        trade.close(0.85, reason='TARGET_HIT')
        assert trade.state == TradeState.CLOSED
        assert trade.realized_gross_pnl > 0  # bought YES at 0.65, sold at 0.85

    def test_trade_fsm_works_for_all_markets(self):
        """FSM transitions are market-agnostic."""
        inst = crypto_instrument('SOL/USDT')
        trade = Trade(trade_id='C-2', instrument=inst, direction='SHORT', qty=10)
        assert trade.state == TradeState.PENDING
        trade.fill(150.0)
        assert trade.state == TradeState.OPEN
        trade.close(140.0, reason='TP')
        assert trade.state == TradeState.CLOSED

    def test_symbol_fallback(self):
        trade = Trade(trade_id='BARE-1')
        assert trade.symbol == 'BARE-1'  # no instrument or leg → falls back to trade_id


# ═══════════════════════════════════════════════════════════════════════
# Signal — multi-market
# ═══════════════════════════════════════════════════════════════════════

class TestSignalMultiMarket:
    def test_fo_signal_backward_compat(self):
        sig = Signal(underlying='NIFTY', direction='CE', confidence=80,
                     trade_mode='swing', spot=18500)
        assert sig.is_bullish
        assert not sig.is_bearish
        assert sig.market_type == MarketType.INDIAN_FO

    def test_crypto_signal(self):
        sig = Signal(underlying='BTC/USDT', direction='LONG', confidence=75,
                     trade_mode='swing', spot=67000,
                     market_type=MarketType.CRYPTO, strategy_name='crypto_momentum')
        assert sig.is_bullish
        assert sig.is_crypto
        assert sig.strategy_name == 'crypto_momentum'

    def test_polymarket_signal(self):
        sig = Signal(underlying='0xabc', direction='YES', confidence=90,
                     trade_mode='research', spot=0.55,
                     market_type=MarketType.POLYMARKET, strategy_name='poly_research')
        assert sig.is_bullish
        assert sig.is_prediction

    def test_bearish_directions(self):
        for d in ['PE', 'SHORT', 'NO']:
            sig = Signal(underlying='X', direction=d, confidence=50,
                         trade_mode='swing', spot=100)
            assert sig.is_bearish
            assert not sig.is_bullish


# ═══════════════════════════════════════════════════════════════════════
# Strategy Plugin ABC
# ═══════════════════════════════════════════════════════════════════════

class _DummyStrategy(StrategyBase):
    """Minimal concrete strategy for testing the ABC."""

    def on_tick(self, data):
        if data.get('signal'):
            return StrategyResult(signals=[
                Signal(underlying='TEST', direction='LONG',
                       confidence=80, trade_mode='swing', spot=100,
                       market_type=MarketType.CRYPTO),
            ])
        return StrategyResult()

    def get_instruments(self):
        return [crypto_instrument('BTC/USDT')]


class TestStrategyBase:
    def test_lifecycle(self):
        cfg = StrategyConfig(name='test_strat', market_type=MarketType.CRYPTO)
        s = _DummyStrategy(cfg)
        assert s.state == StrategyState.IDLE
        assert s.name == 'test_strat'

        s.on_start()
        assert s.state == StrategyState.RUNNING
        assert s.should_trade()

        s.on_stop()
        assert s.state == StrategyState.STOPPED
        assert not s.should_trade()

    def test_tick_no_signal(self):
        s = _DummyStrategy(StrategyConfig())
        result = s.on_tick({})
        assert not result.has_signals

    def test_tick_with_signal(self):
        s = _DummyStrategy(StrategyConfig())
        result = s.on_tick({'signal': True})
        assert result.has_signals
        assert result.signals[0].is_bullish

    def test_regime_change(self):
        s = _DummyStrategy(StrategyConfig())
        assert s.regime == Regime.RANGING
        s.on_regime_change(Regime.HIGH_VOLATILITY)
        assert s.regime == Regime.HIGH_VOLATILITY

    def test_describe(self):
        cfg = StrategyConfig(
            name='my_crypto', market_type=MarketType.CRYPTO,
            instruments=['BTC/USDT', 'ETH/USDT'],
        )
        s = _DummyStrategy(cfg)
        d = s.describe()
        assert d['name'] == 'my_crypto'
        assert d['market'] == 'crypto'
        assert d['trade_mode'] == 'swing'

    def test_get_instruments(self):
        s = _DummyStrategy(StrategyConfig())
        instruments = s.get_instruments()
        assert len(instruments) == 1
        assert instruments[0].symbol == 'BTC/USDT'

    def test_disabled_strategy_skips(self):
        cfg = StrategyConfig(enabled=False)
        s = _DummyStrategy(cfg)
        s.on_start()
        assert not s.should_trade()


# ═══════════════════════════════════════════════════════════════════════
# Exchange data classes
# ═══════════════════════════════════════════════════════════════════════

class TestExchangeDataClasses:
    def test_order(self):
        o = Order(order_id='X1', side='BUY', qty=1.5, status='FILLED',
                  fill_price=67000)
        assert o.is_filled

    def test_order_not_filled(self):
        o = Order(order_id='X2', status='PENDING')
        assert not o.is_filled

    def test_ticker_mid(self):
        t = Ticker(symbol='BTC/USDT', bid=66900, ask=67100)
        assert t.mid == 67000.0
        assert t.spread == 200.0

    def test_ticker_no_book(self):
        t = Ticker(symbol='BTC/USDT', last=67000)
        assert t.mid == 67000.0
        assert t.spread == 0.0

    def test_balance(self):
        b = Balance(currency='USDT', free=5000, used=1000, total=6000)
        assert b.free == 5000

    def test_position(self):
        p = ExchangePosition(symbol='BTC/USDT', side='LONG', qty=0.5,
                             avg_entry=65000, unrealized_pnl=1000)
        assert p.unrealized_pnl == 1000
