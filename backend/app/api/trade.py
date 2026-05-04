"""
Trade execution, backtesting, and technical indicators.

Integrates:
- Backtrader  : signal backtesting with Sharpe, drawdown, win rate
- pandas-ta   : RSI, MACD, Bollinger Bands, EMA overlays for the chart
- OpenAlgo    : unified order execution across Indian brokers (Zerodha, Angel, Fyers…)
- NautilusTrader-style risk rules : position sizing, stop-loss, max daily loss guard
"""

import io
import json
import re
import time
from datetime import datetime, timedelta
from threading import Lock
from flask import request, jsonify

from . import trade_bp
from ..config import Config
from ..utils.logger import get_logger
from .market import _resolve_ticker

logger = get_logger('phoenixtrade.api.trade')

# ── In-memory cache (shared pattern with market.py) ──────────────────────────
_cache: dict = {}
_cache_lock = Lock()

def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry['expires']:
            return entry['data']
        return None

def _cache_set(key: str, data, ttl: int = 300):
    with _cache_lock:
        _cache[key] = {'data': data, 'expires': time.time() + ttl}

def _cache_key(*parts) -> str:
    return ':'.join(str(p) for p in parts)


# ── Helpers ───────────────────────────────────────────────────────────────────

_FNO_PAT = re.compile(r'^[A-Z]+-[A-Z]{3}\d{4}-(\d+-(CE|PE)|FUT)$')


def _fetch_ohlcv(ticker: str, days: int = 365, interval: str = '1d'):
    """Return a pandas DataFrame of OHLCV data.

    Option/futures contracts (e.g. ``BANKNIFTY-MAY2026-56300-PE``) come from
    IndMoney historical API since yfinance has no idea what they are.
    Everything else falls back to yfinance.
    """
    import pandas as pd

    if _FNO_PAT.match(ticker.upper()):
        from .indmoney import _ind_candles
        rows = _ind_candles(ticker, interval=interval, days=days)
        if not rows:
            return None
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        return df[['open', 'high', 'low', 'close', 'volume']]

    import yfinance as yf
    end   = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start.strftime('%Y-%m-%d'),
                     end=end.strftime('%Y-%m-%d'),
                     interval=interval, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df.index = pd.to_datetime(df.index)
    df = df.rename(columns=str.lower)
    return df


# ── Intraday Signal ──────────────────────────────────────────────────────────

@trade_bp.route('/intraday-signal/<ticker>', methods=['GET'])
def intraday_signal(ticker: str):
    """
    Fast intraday signal — no LLM, pure technical math on 1h bars.
    Returns BUY / SELL / HOLD with score, live price, and key reasons.
    Responds in < 1 second.
    """
    ticker = _resolve_ticker(ticker)
    try:
        import yfinance as yf
        import numpy as np

        # ── Live price ────────────────────────────────────────────────────────
        yf_ticker = yf.Ticker(ticker)
        try:
            info  = yf_ticker.fast_info
            live  = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
            price = float(live) if live else None
        except Exception:
            price = None

        # ── 1h OHLCV (last 30 days) ───────────────────────────────────────────
        df = _fetch_ohlcv(ticker, days=30, interval='1h')
        if df is None or len(df) < 20:
            return jsonify({"success": False, "error": f"Not enough intraday data for {ticker}"}), 404

        if price is None:
            price = float(df['close'].iloc[-1])

        c = df['close']
        h = df['high']
        l = df['low']
        v = df['volume']

        # ── RSI-14 ────────────────────────────────────────────────────────────
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])

        # ── EMA 9 / 21 ───────────────────────────────────────────────────────
        ema9  = float(c.ewm(span=9,  adjust=False).mean().iloc[-1])
        ema21 = float(c.ewm(span=21, adjust=False).mean().iloc[-1])
        ema9_prev  = float(c.ewm(span=9,  adjust=False).mean().iloc[-2])
        ema21_prev = float(c.ewm(span=21, adjust=False).mean().iloc[-2])
        ema_cross  = (ema9 > ema21)                          # True = bullish
        fresh_cross = (ema9 > ema21) != (ema9_prev > ema21_prev)  # just crossed

        # ── VWAP (rolling today approximation: last 6 1h bars = trading day) ─
        bars = min(len(df), 6)
        typical = (h.iloc[-bars:] + l.iloc[-bars:] + c.iloc[-bars:]) / 3
        vwap = float((typical * v.iloc[-bars:]).sum() / v.iloc[-bars:].sum()) if v.iloc[-bars:].sum() > 0 else price
        above_vwap = price > vwap

        # ── Volume spike ─────────────────────────────────────────────────────
        avg_vol   = float(v.iloc[-21:-1].mean()) if len(v) > 21 else float(v.mean())
        vol_ratio = float(v.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

        # ── ATR-14 (for SL/target) ────────────────────────────────────────────
        tr  = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
        atr = float(tr.rolling(14).mean().iloc[-1])

        # ── Score (0–100) ─────────────────────────────────────────────────────
        rsi_score  = max(0, min(100, (50 - rsi) * 2 + 50))      # low RSI → high score
        ema_score  = 80 if ema_cross else 20
        vwap_score = 70 if above_vwap else 30
        vol_score  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
        chg3h      = float((c.iloc[-1] - c.iloc[-4]) / c.iloc[-4] * 100) if len(c) >= 4 else 0
        mom_score  = min(100, max(0, chg3h * 10 + 50))

        score = round(
            rsi_score  * 0.25 +
            ema_score  * 0.25 +
            vwap_score * 0.20 +
            vol_score  * 0.15 +
            mom_score  * 0.15, 1)

        # ── Action ────────────────────────────────────────────────────────────
        if score >= 63:
            action = "BUY"
        elif score <= 37:
            action = "SELL"
        else:
            action = "HOLD"

        # ── Reasons ───────────────────────────────────────────────────────────
        reasons = []
        if rsi < 35:   reasons.append(f"RSI {rsi:.0f} — oversold")
        elif rsi > 65: reasons.append(f"RSI {rsi:.0f} — overbought")
        else:          reasons.append(f"RSI {rsi:.0f} — neutral")

        if fresh_cross:
            reasons.append(f"EMA9/21 {'bullish' if ema_cross else 'bearish'} crossover just triggered")
        elif ema_cross:
            reasons.append("EMA9 above EMA21 — uptrend intact")
        else:
            reasons.append("EMA9 below EMA21 — downtrend")

        reasons.append(f"Price {'above' if above_vwap else 'below'} VWAP ({vwap:.2f})")

        if vol_ratio >= 1.5:
            reasons.append(f"Volume spike {vol_ratio:.1f}x avg — strong conviction")
        elif vol_ratio <= 0.6:
            reasons.append(f"Low volume {vol_ratio:.1f}x avg — weak move")

        # ── Levels ────────────────────────────────────────────────────────────
        entry = round(price, 2)
        sl    = round(price - atr * 1.2, 2)
        t1    = round(price + atr * 1.5, 2)
        t2    = round(price + atr * 2.5, 2)

        return jsonify({
            "success": True,
            "data": {
                "ticker":      ticker,
                "action":      action,
                "score":       score,
                "price":       round(price, 2),
                "rsi":         round(rsi, 1),
                "ema_bull":    ema_cross,
                "fresh_cross": fresh_cross,
                "above_vwap":  above_vwap,
                "vwap":        round(vwap, 2),
                "vol_ratio":   round(vol_ratio, 2),
                "atr":         round(atr, 2),
                "entry":       entry,
                "sl":          sl,
                "t1":          t1,
                "t2":          t2,
                "reasons":     reasons,
                "interval":    "1h",
            }
        })

    except Exception as e:
        logger.error(f"Intraday signal failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Technical Indicators ──────────────────────────────────────────────────────

@trade_bp.route('/indicators/<ticker>', methods=['GET'])
def get_indicators(ticker: str):
    """
    Return RSI, MACD, Bollinger Bands, EMA-20/50, ATR for the chart overlays.

    Query params:
        interval   - 1d (default), 1h, 30m
        days       - lookback days (default 365)
    """
    ticker   = ticker.upper().strip()
    interval = request.args.get('interval', '1d')
    days     = int(request.args.get('days', 365))
    # yfinance caps intraday history at ~60 days; enforce it
    if interval in ('1h', '30m', '15m', '5m', '1m'):
        days = min(days, 59)

    try:
        import numpy as np

        df = _fetch_ohlcv(ticker, days=days, interval=interval)
        if df is None:
            return jsonify({"success": False, "error": f"No data for {ticker}"}), 404

        c = df['close']
        h = df['high']
        l = df['low']

        # RSI-14
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))

        # MACD (12,26,9)
        ema12  = c.ewm(span=12, adjust=False).mean()
        ema26  = c.ewm(span=26, adjust=False).mean()
        macd_l = ema12 - ema26
        macd_s = macd_l.ewm(span=9, adjust=False).mean()
        macd_h = macd_l - macd_s

        # Bollinger Bands (20, 2σ)
        sma20    = c.rolling(20).mean()
        std20    = c.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20

        # EMA 20 / 50
        ema20 = c.ewm(span=20, adjust=False).mean()
        ema50 = c.ewm(span=50, adjust=False).mean()

        # ATR-14
        tr = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
        atr = tr.rolling(14).mean()

        def safe(v):
            import math
            if v is None: return None
            try:
                f = float(v)
                return None if math.isnan(f) or math.isinf(f) else round(f, 4)
            except Exception:
                return None

        result = []
        for i, (ts, row) in enumerate(df.iterrows()):
            result.append({
                "date":       ts.strftime('%Y-%m-%d') if interval == '1d' else ts.isoformat(),
                "rsi":        safe(rsi.iloc[i]),
                "macd":       safe(macd_l.iloc[i]),
                "macd_signal":safe(macd_s.iloc[i]),
                "macd_hist":  safe(macd_h.iloc[i]),
                "bb_upper":   safe(bb_upper.iloc[i]),
                "bb_mid":     safe(sma20.iloc[i]),
                "bb_lower":   safe(bb_lower.iloc[i]),
                "ema20":      safe(ema20.iloc[i]),
                "ema50":      safe(ema50.iloc[i]),
                "atr":        safe(atr.iloc[i]),
                "close":      safe(row.get('close')),
            })

        # Current signal summary (last bar)
        last = result[-1] if result else {}
        signal = "NEUTRAL"
        if last.get('rsi') and last.get('ema20') and last.get('ema50'):
            rsi  = last['rsi']
            price = safe(df['close'].iloc[-1])
            ema20 = last['ema20']
            ema50 = last['ema50']
            if rsi < 35 and price and price > ema20:
                signal = "BUY"
            elif rsi > 65 and price and price < ema20:
                signal = "SELL"
            elif ema20 and ema50 and ema20 > ema50:
                signal = "BULLISH"
            elif ema20 and ema50 and ema20 < ema50:
                signal = "BEARISH"

        return jsonify({
            "success": True,
            "data": {
                "ticker":  ticker,
                "signal":  signal,
                "indicators": result,
                "latest": last,
            }
        })

    except Exception as e:
        logger.error(f"Indicators failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Backtesting ───────────────────────────────────────────────────────────────

@trade_bp.route('/backtest/<ticker>', methods=['GET'])
def backtest(ticker: str):
    """
    Backtest a FOMO+momentum strategy on historical data using Backtrader.

    Strategy rules (from OASIS simulation logic):
        BUY  when RSI < 40 AND close > EMA20 AND FOMO-proxy (vol ratio) > 1.2
        SELL when RSI > 65 OR close < EMA20

    Query params:
        days     - lookback period (default 365)
        cash     - starting capital in USD (default 10000)
    """
    ticker = _resolve_ticker(ticker)
    days   = int(request.args.get('days', 365))
    cash   = float(request.args.get('cash', 10000))

    try:
        import backtrader as bt
        import pandas as pd

        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 60:
            return jsonify({"success": False, "error": f"Not enough data for {ticker}"}), 404

        df = df[['open', 'high', 'low', 'close', 'volume']].copy()
        df.index = pd.to_datetime(df.index)
        df.index.name = 'datetime'

        # ── Strategy ─────────────────────────────────────────────────────
        class FOMOStrategy(bt.Strategy):
            params = dict(rsi_period=14, ema_period=20, rsi_buy=40, rsi_sell=65)

            def __init__(self):
                self.rsi  = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
                self.ema  = bt.indicators.EMA(self.data.close, period=self.p.ema_period)
                self.bb   = bt.indicators.BollingerBands(self.data.close, period=20)
                self.vol_sma = bt.indicators.SMA(self.data.volume, period=10)
                self.order = None
                self.trades_log = []

            def notify_order(self, order):
                if order.status in [order.Completed]:
                    side = 'BUY' if order.isbuy() else 'SELL'
                    self.trades_log.append({
                        "date":  self.data.datetime.date(0).isoformat(),
                        "side":  side,
                        "price": round(order.executed.price, 2),
                        "size":  order.executed.size,
                    })
                self.order = None

            def next(self):
                if self.order:
                    return
                vol_ratio = (self.data.volume[0] / self.vol_sma[0]) if self.vol_sma[0] else 1
                if not self.position:
                    if (self.rsi[0] < self.p.rsi_buy
                            and self.data.close[0] > self.ema[0]
                            and vol_ratio > 1.2):
                        size = int(self.broker.getcash() * 0.95 / self.data.close[0])
                        if size > 0:
                            self.order = self.buy(size=size)
                else:
                    if self.rsi[0] > self.p.rsi_sell or self.data.close[0] < self.ema[0]:
                        self.order = self.close()

        # ── Backtrader setup ─────────────────────────────────────────────
        class PandasOHLCV(bt.feeds.PandasData):
            params = (('datetime', None), ('open', 'open'), ('high', 'high'),
                      ('low', 'low'), ('close', 'close'), ('volume', 'volume'),
                      ('openinterest', None))

        cerebro = bt.Cerebro()
        cerebro.addstrategy(FOMOStrategy)
        cerebro.adddata(PandasOHLCV(dataname=df))
        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=0.001)  # 0.1% commission

        # Analyzers (NautilusTrader-style risk metrics)
        cerebro.addanalyzer(bt.analyzers.SharpeRatio,   _name='sharpe',   riskfreerate=0.05)
        cerebro.addanalyzer(bt.analyzers.DrawDown,       _name='drawdown')
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer,  _name='trades')
        cerebro.addanalyzer(bt.analyzers.Returns,        _name='returns')
        cerebro.addanalyzer(bt.analyzers.AnnualReturn,   _name='annual')

        results   = cerebro.run()
        strat     = results[0]
        final_val = cerebro.broker.getvalue()

        # ── Extract metrics ───────────────────────────────────────────────
        sharpe_a   = strat.analyzers.sharpe.get_analysis()
        dd_a       = strat.analyzers.drawdown.get_analysis()
        trades_a   = strat.analyzers.trades.get_analysis()
        returns_a  = strat.analyzers.returns.get_analysis()

        total_t    = trades_a.get('total', {}).get('total', 0)
        won_t      = trades_a.get('won',   {}).get('total', 0)
        win_rate   = round(won_t / total_t * 100, 1) if total_t else 0
        sharpe     = sharpe_a.get('sharperatio') or 0
        max_dd     = dd_a.get('max', {}).get('drawdown', 0)
        total_ret  = round((final_val - cash) / cash * 100, 2)

        # Position sizing recommendation (NautilusTrader-style: risk 1% per trade)
        last_close = float(df['close'].iloc[-1])
        atr_series = df['close'].rolling(14).std()
        atr_val    = float(atr_series.iloc[-1]) if len(atr_series) > 14 else last_close * 0.02
        stop_dist  = atr_val * 1.5
        risk_per_trade = cash * 0.01          # 1% of capital
        recommended_size = int(risk_per_trade / stop_dist) if stop_dist else 1

        return jsonify({
            "success": True,
            "data": {
                "ticker":      ticker,
                "days":        days,
                "start_cash":  cash,
                "final_value": round(final_val, 2),
                "total_return_pct": total_ret,
                "sharpe_ratio":     round(float(sharpe), 3) if sharpe else None,
                "max_drawdown_pct": round(float(max_dd), 2),
                "total_trades":     total_t,
                "win_rate_pct":     win_rate,
                "won_trades":       won_t,
                "lost_trades":      total_t - won_t,
                # NautilusTrader-style position sizing
                "risk_mgmt": {
                    "recommended_size":   recommended_size,
                    "stop_loss_distance": round(stop_dist, 2),
                    "entry_price":        round(last_close, 2),
                    "stop_price":         round(last_close - stop_dist, 2),
                    "take_profit_price":  round(last_close + stop_dist * 2, 2),  # 2:1 R/R
                    "risk_per_trade_usd": round(risk_per_trade, 2),
                },
                "recent_trades": strat.trades_log[-10:],
            }
        })

    except Exception as e:
        logger.error(f"Backtest failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@trade_bp.route('/vbt-backtest/<ticker>', methods=['GET'])
def vbt_backtest(ticker: str):
    """
    Multi-strategy backtest using vectorbt — tests 4 strategies simultaneously and
    returns comparison stats (Sharpe, return, win rate, max drawdown) for each.

    Strategies tested:
      1. RSI Bounce      — buy RSI<35, sell RSI>65
      2. EMA Crossover   — buy EMA9>EMA21, sell EMA9<EMA21
      3. EMA Crossover   — buy EMA21>EMA50, sell EMA21<EMA50
      4. Bollinger Bands — buy at lower band, sell at upper band

    Query params:
        days  — lookback in days (default 365)
        cash  — starting capital (default 100000)
    """
    ticker = _resolve_ticker(ticker)
    days   = int(request.args.get('days', 365))
    cash   = float(request.args.get('cash', 100_000))

    try:
        import vectorbt as vbt
        import numpy as np
        import pandas as pd

        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 60:
            return jsonify({"success": False, "error": f"Not enough data for {ticker}"}), 404

        price  = df['close'].astype(float)
        high   = df['high'].astype(float)
        low    = df['low'].astype(float)
        volume = df['volume'].astype(float)

        # ── Buy & Hold baseline ───────────────────────────────────────────────
        bh_return = round((float(price.iloc[-1]) - float(price.iloc[0])) / float(price.iloc[0]) * 100, 2)

        def _safe(val, default=0, decimals=2):
            try:
                v = float(val)
                if v != v or v == float('inf') or v == float('-inf'):
                    return default
                return round(v, decimals)
            except Exception:
                return default

        def _extract_stats(pf):
            s = pf.stats()
            return {
                'total_return_pct': _safe(s.get('Total Return [%]'),    0, 2),
                'sharpe':           _safe(s.get('Sharpe Ratio'),         0, 3),
                'max_drawdown_pct': _safe(s.get('Max Drawdown [%]'),     0, 2),
                'win_rate_pct':     _safe(s.get('Win Rate [%]'),         0, 1),
                'total_trades':     int(_safe(s.get('Total Trades'),      0, 0)),
                'final_value':      _safe(pf.final_value(),               cash, 2),
            }

        strategies = {}

        # ── 1. RSI Bounce ─────────────────────────────────────────────────────
        try:
            rsi = vbt.RSI.run(price, window=14).rsi
            pf  = vbt.Portfolio.from_signals(price, rsi.vbt.crossed_below(35),
                                             rsi.vbt.crossed_above(65), init_cash=cash, fees=0.001, freq='1D')
            strategies['RSI Bounce (14, 35/65)'] = _extract_stats(pf)
        except Exception as e:
            strategies['RSI Bounce (14, 35/65)'] = {'error': str(e)}

        # ── 2. EMA Crossover (9/21) ───────────────────────────────────────────
        try:
            e9  = vbt.MA.run(price, window=9,  ewm=True).ma
            e21 = vbt.MA.run(price, window=21, ewm=True).ma
            pf  = vbt.Portfolio.from_signals(price, e9.vbt.crossed_above(e21),
                                             e9.vbt.crossed_below(e21), init_cash=cash, fees=0.001, freq='1D')
            strategies['EMA Crossover (9/21)'] = _extract_stats(pf)
        except Exception as e:
            strategies['EMA Crossover (9/21)'] = {'error': str(e)}

        # ── 3. EMA Crossover (21/50) ──────────────────────────────────────────
        try:
            e21b = vbt.MA.run(price, window=21, ewm=True).ma
            e50  = vbt.MA.run(price, window=50, ewm=True).ma
            pf   = vbt.Portfolio.from_signals(price, e21b.vbt.crossed_above(e50),
                                              e21b.vbt.crossed_below(e50), init_cash=cash, fees=0.001, freq='1D')
            strategies['EMA Crossover (21/50)'] = _extract_stats(pf)
        except Exception as e:
            strategies['EMA Crossover (21/50)'] = {'error': str(e)}

        # ── 4. Bollinger Band Mean Reversion ──────────────────────────────────
        try:
            bb  = vbt.BBANDS.run(price, window=20, alpha=2)
            pf  = vbt.Portfolio.from_signals(price, price.vbt.crossed_above(bb.lower),
                                             price.vbt.crossed_above(bb.upper), init_cash=cash, fees=0.001, freq='1D')
            strategies['Bollinger Bands (20, 2σ)'] = _extract_stats(pf)
        except Exception as e:
            strategies['Bollinger Bands (20, 2σ)'] = {'error': str(e)}

        # ── Best strategy ─────────────────────────────────────────────────────
        valid = {k: v for k, v in strategies.items() if 'error' not in v}
        best  = max(valid, key=lambda k: valid[k]['sharpe']) if valid else None

        return jsonify({
            "success": True,
            "data": {
                "ticker":          ticker,
                "days":            days,
                "start_cash":      cash,
                "buy_and_hold_pct": bh_return,
                "strategies":      strategies,
                "best_strategy":   best,
                "note": "Sharpe > 1.0 = good, > 2.0 = excellent. Win rate > 50% with positive Sharpe = deploy-worthy."
            }
        })

    except Exception as e:
        logger.error(f"vbt backtest failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Portfolio Ranker ──────────────────────────────────────────────────────────

@trade_bp.route('/rank', methods=['POST'])
def rank_tickers():
    """
    Score and rank a list of tickers by combined signal strength.

    Multi-factor score (0-100):
        30% RSI momentum
        25% EMA trend alignment
        25% Volume / FOMO proxy
        20% 5-day price momentum

    Body: { "tickers": ["NVDA", "AAPL", "TSLA", ...] }
    """
    body    = request.get_json(silent=True) or {}
    tickers = [t.upper().strip() for t in body.get('tickers', [])][:20]

    if not tickers:
        return jsonify({"success": False, "error": "Provide tickers list"}), 400

    results = []
    for ticker in tickers:
        try:
            df = _fetch_ohlcv(ticker, days=60)
            if df is None or len(df) < 20:
                continue

            closes = df['close'].tolist()
            vols   = df['volume'].tolist()
            price  = closes[-1]

            c = df['close']
            delta = c.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, float('nan'))
            rsi_s = 100 - (100 / (1 + rs))
            rsi   = float(rsi_s.iloc[-1]) if not rsi_s.empty else 50

            ema20 = float(c.ewm(span=20, adjust=False).mean().iloc[-1])
            ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

            chg5d     = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
            avg_vol   = sum(vols[:-1]) / max(len(vols) - 1, 1)
            vol_ratio = vols[-1] / avg_vol if avg_vol else 1

            # Scores (all 0-100)
            rsi_score  = max(0, min(100, (50 - rsi) * 2 + 50))     # Low RSI → high score
            ema_score  = 100 if (price > ema20 > ema50) else (50 if price > ema20 else 0)
            vol_score  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
            mom_score  = min(100, max(0, chg5d * 5 + 50))

            composite  = round(rsi_score * 0.30 + ema_score * 0.25
                               + vol_score * 0.25 + mom_score * 0.20, 1)

            action = "STRONG BUY" if composite >= 70 else \
                     "BUY"        if composite >= 58 else \
                     "HOLD"       if composite >= 45 else \
                     "SELL"       if composite >= 30 else "STRONG SELL"

            results.append({
                "ticker":    ticker,
                "price":     round(float(price), 2),
                "score":     composite,
                "action":    action,
                "rsi":       round(rsi, 1),
                "vol_ratio": round(vol_ratio, 2),
                "chg5d":     round(chg5d, 2),
                "ema_trend": "BULL" if price > ema20 > ema50 else ("BEAR" if price < ema20 < ema50 else "MIXED"),
            })
        except Exception as e:
            logger.warning(f"Rank skip {ticker}: {e}")

    results.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({"success": True, "data": results})


# ── OpenAlgo Order Execution ──────────────────────────────────────────────────

def _get_openalgo_client():
    """Return an OpenAlgo client if configured, else None."""
    api_key = getattr(Config, 'OPENALGO_API_KEY', None) or ''
    host    = getattr(Config, 'OPENALGO_HOST', 'http://127.0.0.1:5000')
    if not api_key:
        return None
    try:
        from openalgo import api as OpenAlgoAPI
        return OpenAlgoAPI(api_key=api_key, host=host)
    except Exception as e:
        logger.warning(f"OpenAlgo client init failed: {e}")
        return None


@trade_bp.route('/execute', methods=['POST'])
def execute_order():
    """
    Place a real or paper trade via OpenAlgo → Zerodha/Angel/Fyers etc.

    Body:
    {
        "ticker":      "INFY",
        "action":      "BUY",         // BUY or SELL
        "lots":        1,             // (F&O) number of lots — preferred
        "quantity":    10,            // (EQ) units; or fallback for F&O if lots omitted
        "instrument":  "EQ",          // EQ | FUT | CE | PE
        "price_type":  "MARKET",      // MARKET or LIMIT
        "price":       0,             // for LIMIT orders
        "product":     "MIS",         // MIS (intraday) or CNC (delivery)
        "exchange":    "NSE",         // NSE | NFO | BSE | BFO
        "paper":       true           // if true, simulate without real order
    }

    NOTE: Indian F&O brokers expect quantity in *units* (lots × lot_size),
          not in lots. If `lots` is provided for F&O, the server resolves the
          contract's lot_size from the instrument master and sends lots×lot_size.
    """
    from ..services import broker_utils as bu

    body = request.get_json(silent=True) or {}

    ticker     = body.get('ticker', '').upper().strip()
    action     = body.get('action', 'BUY').upper()
    instrument = body.get('instrument', 'EQ').upper()
    lots       = body.get('lots')
    quantity   = body.get('quantity')
    price_type = body.get('price_type', 'MARKET').upper()
    price      = float(body.get('price', 0))
    product    = body.get('product', 'MIS').upper()
    exchange   = body.get('exchange', 'NSE').upper()
    if not ticker:
        return jsonify({"success": False, "error": "ticker is required"}), 400

    # Convert lots → units (F&O only) using the broker instrument master
    if instrument in ('FUT', 'CE', 'PE'):
        meta = bu.fno_meta(ticker)
        lot_size = (meta or {}).get('lot_size') or 0
        if lots is not None:
            if lot_size <= 0:
                return jsonify({"success": False,
                                "error": f"lot_size unknown for {ticker}; "
                                         f"cannot translate lots → units"}), 400
            quantity = int(lots) * int(lot_size)
        elif quantity is None:
            return jsonify({"success": False,
                            "error": "Provide either 'lots' or 'quantity'"}), 400
        else:
            quantity = int(quantity)
            if lot_size and quantity % lot_size != 0:
                return jsonify({"success": False,
                                "error": f"quantity {quantity} not a multiple of "
                                         f"lot_size {lot_size} for {ticker}"}), 400
        if not exchange or exchange == 'NSE':
            exchange = (meta or {}).get('exchange', 'NFO')
    else:
        if quantity is None:
            quantity = 1
        quantity = int(quantity)

    if quantity <= 0:
        return jsonify({"success": False, "error": "quantity must be > 0"}), 400

    # ── Live order via broker ─────────────────────────────────────────────
    client = _get_openalgo_client()
    if not client:
        return jsonify({
            "success": False,
            "error": "OpenAlgo not configured. Add OPENALGO_API_KEY and OPENALGO_HOST to .env",
        }), 503

    try:
        resp = client.placeorder(
            strategy="PhoenixTrade",
            symbol=ticker,
            action=action,
            exchange=exchange,
            price_type=price_type,
            product=product,
            quantity=quantity,
            price=price if price_type == 'LIMIT' else 0,
        )
        return jsonify({
            "success": True,
            "data": {
                "mode":      "LIVE",
                "ticker":    ticker,
                "action":    action,
                "quantity":  quantity,
                "exchange":  exchange,
                "response":  resp,
                "timestamp": datetime.now().isoformat(),
            }
        })
    except Exception as e:
        logger.error(f"OpenAlgo order failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


def _compute_levels(df, exec_price: float = None) -> dict:
    """
    exec_price: if provided, anchor SL/targets to this price (actual buy price)
                instead of the ideal entry derived from S&R. Used by ai_trade()
                so displayed levels match actual trade risk, not planned entry.
    """
    """
    Shared helper: compute entry, stop-loss, and targets from OHLCV DataFrame.
    Used by both get_trade_levels() and ai_trade() so values are always consistent.
    """
    import numpy as np
    c = df['close']; h = df['high']; l = df['low']
    price = float(c.iloc[-1])

    tr  = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
    atr = float(tr.rolling(14).mean().iloc[-1])

    delta = c.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])

    ema20  = float(c.ewm(span=20,  adjust=False).mean().iloc[-1])
    ema50  = float(c.ewm(span=50,  adjust=False).mean().iloc[-1])

    window = 5
    prices = c.tolist()
    highs, lows = [], []
    for i in range(window, len(prices) - window):
        if prices[i] == max(prices[i-window:i+window+1]):
            highs.append(prices[i])
        if prices[i] == min(prices[i-window:i+window+1]):
            lows.append(prices[i])

    supports    = sorted([v for v in lows  if v < price], reverse=True)
    resistances = sorted([v for v in highs if v > price])

    if rsi < 40:
        entry = supports[0] if supports and (price - supports[0]) / price < 0.03 else price
    elif price > ema20 > ema50:
        entry = ema20
    elif supports:
        entry = supports[0]
    else:
        entry = price

    # If we have an actual execution price, anchor SL/targets from there
    anchor = exec_price if exec_price else entry

    sl_atr     = anchor - atr * 1.5
    # Place SL just below nearest support below anchor (+ 0.5 ATR buffer)
    supports_below_anchor = sorted([v for v in supports if v < anchor], reverse=True)
    sl_support = (supports_below_anchor[0] - atr * 0.5) if supports_below_anchor else sl_atr
    stop_loss  = max(sl_atr, sl_support)
    risk       = max(anchor - stop_loss, atr * 0.5)   # floor to avoid zero risk

    t1 = resistances[0] if resistances and (resistances[0] - anchor) >= risk * 1.5 else anchor + risk * 1.5
    t2 = resistances[1] if len(resistances) > 1 and (resistances[1] - anchor) >= risk * 2.5 else anchor + risk * 2.5
    t3 = anchor + risk * 4.0

    return {
        "entry":      round(entry, 4),
        "stop_loss":  round(stop_loss, 4),
        "target_1":   round(t1, 4),
        "target_2":   round(t2, 4),
        "target_3":   round(t3, 4),
        "risk":       round(risk, 4),
        "atr":        round(atr, 4),
        "supports":   [round(v, 4) for v in supports[:3]],
        "resistances":[round(v, 4) for v in resistances[:3]],
    }


@trade_bp.route('/levels/<ticker>', methods=['GET'])
def get_trade_levels(ticker: str):
    """
    Compute precise entry price, target price, and stop loss for a ticker.

    Method:
    - Support/resistance from recent swing highs/lows
    - Entry: nearest support above 200d low or RSI-oversold zone
    - Stop loss: entry - 1.5 * ATR (below nearest support)
    - Target 1: nearest resistance (1.5:1 R/R minimum)
    - Target 2: next resistance (2.5:1 R/R)
    - Target 3: extended move (4:1 R/R)
    """
    ticker  = _resolve_ticker(ticker.upper().strip())
    days    = int(request.args.get('days', 180))

    levels_key = _cache_key('levels', ticker, days)
    cached = _cache_get(levels_key)
    if cached:
        return jsonify({"success": True, "data": cached, "_cached": True})

    try:
        import numpy as np

        df = _fetch_ohlcv(ticker, days=days)
        if df is None or len(df) < 5:
            return jsonify({"success": False, "error": f"Not enough data for {ticker}"}), 404

        lv = _compute_levels(df)
        price  = float(df['close'].iloc[-1])
        c      = df['close']
        ema20  = float(c.ewm(span=20,  adjust=False).mean().iloc[-1])
        ema50  = float(c.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
        rsi    = lv['atr']  # recalculate rsi separately
        delta  = c.diff()
        gain   = delta.clip(lower=0).rolling(14).mean()
        loss   = (-delta.clip(upper=0)).rolling(14).mean()
        import numpy as _np
        rsi    = float((100 - (100 / (1 + gain / loss.replace(0, _np.nan)))).iloc[-1])

        entry = lv['entry']
        risk  = lv['risk']

        if rsi < 40:
            reason = f"RSI oversold ({rsi:.1f}) — momentum reversal entry"
        elif price > ema20 > ema50:
            reason = f"Uptrend intact (EMA20 > EMA50) — buy pullback to EMA20"
        elif lv['supports']:
            reason = "Entry at nearest support level"
        else:
            reason = "Entry at current market price"

        if rsi > 60 and price < ema20:
            bias = "BEARISH"
        elif rsi < 40 or (price > ema20 > ema50):
            bias = "BULLISH"
        else:
            bias = "NEUTRAL"

        levels_data = {
            "ticker":           ticker,
            "current_price":    round(price, 4),
            "bias":             bias,
            "entry":            lv['entry'],
            "stop_loss":        lv['stop_loss'],
            "target_1":         lv['target_1'],
            "target_2":         lv['target_2'],
            "target_3":         lv['target_3'],
            "risk_per_share":   lv['risk'],
            "reward_t1":        round(lv['target_1'] - entry, 4),
            "rr_t1":            round((lv['target_1'] - entry) / risk, 2) if risk > 0 else None,
            "rr_t2":            round((lv['target_2'] - entry) / risk, 2) if risk > 0 else None,
            "atr":              lv['atr'],
            "rsi":              round(rsi, 1),
            "ema20":            round(ema20, 4),
            "ema50":            round(ema50, 4),
            "ema200":           round(ema200, 4),
            "entry_reason":     reason,
            "supports":         lv['supports'],
            "resistances":      lv['resistances'],
        }
        _cache_set(levels_key, levels_data, ttl=300)
        return jsonify({"success": True, "data": levels_data})

    except Exception as e:
        logger.error(f"Levels failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Paper Wallet ──────────────────────────────────────────────────────────────
import os as _os
_WALLET_FILE = _os.path.join(_os.path.dirname(__file__), '../../uploads/paper_wallet.json')

def _load_wallet() -> dict:
    """Load paper wallet state from disk."""
    try:
        if _os.path.exists(_WALLET_FILE):
            with open(_WALLET_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "cash": 10000.0,
        "starting_cash": 10000.0,
        "positions": {},      # ticker -> {qty, avg_entry, entry_date}
        "trades": [],         # completed trades log
        "total_trades": 0,
        "won": 0,
        "lost": 0,
        "created_at": datetime.now().isoformat(),
    }

def _save_wallet(wallet: dict):
    _os.makedirs(_os.path.dirname(_WALLET_FILE), exist_ok=True)
    with open(_WALLET_FILE, 'w') as f:
        json.dump(wallet, f, indent=2)

def _current_prices(tickers: list) -> dict:
    """Bulk-fetch latest prices for a list of tickers."""
    import yfinance as yf
    prices = {}
    try:
        if not tickers:
            return prices
        data = yf.download(tickers if len(tickers) > 1 else tickers[0],
                           period='1d', progress=False, auto_adjust=True)
        if len(tickers) == 1:
            import pandas as pd
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = [c[0] for c in data.columns]
            prices[tickers[0]] = float(data['Close'].iloc[-1])
        else:
            import pandas as pd
            for t in tickers:
                try:
                    prices[t] = float(data['Close'][t].iloc[-1])
                except Exception:
                    pass
    except Exception:
        pass
    return prices


@trade_bp.route('/wallet', methods=['GET'])
def get_wallet():
    """
    Return current paper wallet state with live P&L.
    """
    wallet = _load_wallet()
    tickers = list(wallet['positions'].keys())
    prices  = _current_prices(tickers)

    positions_out = []
    total_market_value = 0.0
    for tkr, pos in wallet['positions'].items():
        cp    = prices.get(tkr, pos['avg_entry'])
        mv    = cp * pos['qty']
        cost  = pos['avg_entry'] * pos['qty']
        pnl   = mv - cost
        pnl_pct = (pnl / cost * 100) if cost else 0
        total_market_value += mv
        positions_out.append({
            "ticker":          tkr,
            "qty":             pos['qty'],
            "avg_entry":       round(pos['avg_entry'], 4),
            "current_price":   round(cp, 4),
            "market_value":    round(mv, 2),
            "pnl":             round(pnl, 2),
            "pnl_pct":         round(pnl_pct, 2),
            "entry_date":      pos.get('entry_date', ''),
            "stop_loss":       pos.get('stop_loss'),
            "target_1":        pos.get('target_1'),
            # F&O fields
            "instrument_type": pos.get('instrument_type', 'EQ'),
            "lot_size":        pos.get('lot_size', 1),
            "expiry":          pos.get('expiry'),
            "strike_price":    pos.get('strike_price'),
            "underlying":      pos.get('underlying', tkr),
        })

    total_value = wallet['cash'] + total_market_value
    total_pnl   = total_value - wallet['starting_cash']
    win_rate    = round(wallet['won'] / wallet['total_trades'] * 100, 1) if wallet['total_trades'] else 0

    return jsonify({
        "success": True,
        "data": {
            "cash":              round(wallet['cash'], 2),
            "starting_cash":     wallet['starting_cash'],
            "total_value":       round(total_value, 2),
            "total_pnl":         round(total_pnl, 2),
            "total_pnl_pct":     round(total_pnl / wallet['starting_cash'] * 100, 2),
            "positions":         positions_out,
            "total_trades":      wallet['total_trades'],
            "won":               wallet['won'],
            "lost":              wallet['lost'],
            "win_rate_pct":      win_rate,
            "recent_trades":     wallet['trades'][-20:],
            "created_at":        wallet['created_at'],
        }
    })


@trade_bp.route('/wallet/trade', methods=['POST'])
def manual_trade():
    """
    Manual paper trade against the wallet.
    Body: { "ticker": "SBIN.NS", "action": "BUY"|"SELL", "qty": 10 }
    Uses live price from yfinance fast_info.
    """
    body   = request.get_json(silent=True) or {}
    ticker = _resolve_ticker(body.get('ticker', ''))
    action = body.get('action', 'BUY').upper()
    qty    = int(body.get('qty', 1))

    if not ticker:
        return jsonify({"success": False, "error": "ticker required"}), 400
    if action not in ('BUY', 'SELL'):
        return jsonify({"success": False, "error": "action must be BUY or SELL"}), 400
    if qty < 1:
        return jsonify({"success": False, "error": "qty must be ≥ 1"}), 400

    try:
        import yfinance as yf
        info  = yf.Ticker(ticker).fast_info
        price = float(getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None) or 0)
        if not price:
            return jsonify({"success": False, "error": f"Could not fetch live price for {ticker}"}), 404

        wallet = _load_wallet()
        trade_result = None

        if action == 'BUY':
            cost = qty * price
            if cost > wallet['cash']:
                return jsonify({"success": False, "error": f"Insufficient cash: need ₹{cost:.0f}, have ₹{wallet['cash']:.0f}"}), 400
            wallet['cash'] -= cost
            if ticker in wallet['positions']:
                pos = wallet['positions'][ticker]
                total_qty  = pos['qty'] + qty
                avg_entry  = (pos['avg_entry'] * pos['qty'] + price * qty) / total_qty
                pos['qty'] = total_qty; pos['avg_entry'] = avg_entry
            else:
                wallet['positions'][ticker] = {"qty": qty, "avg_entry": price, "entry_date": datetime.now().isoformat()}
            wallet['total_trades'] += 1
            trade_result = {"action": "BUY", "qty": qty, "price": price, "cost": round(cost, 2)}
            wallet['trades'].append({
                "ticker": ticker, "action": "BUY", "qty": qty,
                "price": price, "cost": round(cost, 2),
                "date": datetime.now().isoformat(),
            })

        elif action == 'SELL':
            if ticker not in wallet['positions']:
                return jsonify({"success": False, "error": f"No position in {ticker}"}), 400
            pos = wallet['positions'][ticker]
            sell_qty = min(qty, pos['qty'])
            proceeds = sell_qty * price
            pnl      = round((price - pos['avg_entry']) * sell_qty, 2)
            wallet['cash'] += proceeds
            if sell_qty >= pos['qty']:
                wallet['positions'].pop(ticker)
            else:
                pos['qty'] -= sell_qty
            if pnl >= 0: wallet['won'] += 1
            else:        wallet['lost'] += 1
            wallet['total_trades'] += 1
            wallet['trades'].append({"ticker": ticker, "action": "SELL", "qty": sell_qty,
                                     "price": price, "pnl": pnl, "date": datetime.now().isoformat()})
            trade_result = {"action": "SELL", "qty": sell_qty, "price": price, "proceeds": round(proceeds, 2), "pnl": pnl}

        _save_wallet(wallet)
        return jsonify({"success": True, "data": {
            "ticker": ticker, "action": action, "price": round(price, 2),
            "trade": trade_result, "wallet_cash": round(wallet['cash'], 2),
        }})

    except Exception as e:
        logger.error(f"Manual trade failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@trade_bp.route('/wallet/ai-trade', methods=['POST'])
def ai_trade():
    """
    LLM-powered AI trade decision using comprehensive trading knowledge base.
    Computes 10+ technical signals, detects candlestick patterns, then calls
    Claude/LLM with the full trading ruleset to decide BUY / SELL / HOLD.
    Executes the decision against the paper wallet.

    Body: { "ticker": "SBIN.NS", "capital_pct": 0.1 }
    """
    import json as _json
    import re as _re

    body    = request.get_json(silent=True) or {}
    ticker  = _resolve_ticker(body.get('ticker', ''))
    cap_pct = float(body.get('capital_pct', 0.10))

    if not ticker:
        return jsonify({"success": False, "error": "ticker required"}), 400

    # ── Market hours check (DST-aware, with public holidays) ─────────────────
    is_indian = ticker.endswith('.NS') or ticker.endswith('.BO')
    try:
        from zoneinfo import ZoneInfo
        tz_key = 'Asia/Kolkata' if is_indian else 'America/New_York'
        now_loc = datetime.now(ZoneInfo(tz_key))
    except ImportError:
        from datetime import timezone, timedelta
        _off = timedelta(hours=5, minutes=30) if is_indian else timedelta(hours=-5)
        now_loc = datetime.now(timezone(_off))

    market_name      = 'NSE/BSE'      if is_indian else 'NYSE/NASDAQ'
    market_hours_str = '9:15 AM – 3:30 PM IST, Mon–Fri' if is_indian else '9:30 AM – 4:00 PM ET, Mon–Fri'

    # Public / national holidays
    _nse_holidays = {
        '2025-01-26','2025-03-14','2025-04-14','2025-04-18',
        '2025-05-01','2025-08-15','2025-08-27','2025-10-02',
        '2025-10-20','2025-10-21','2025-11-05','2025-12-25',
        '2026-01-26','2026-03-02','2026-04-03','2026-04-14',
        '2026-05-01','2026-08-17','2026-09-15','2026-10-02',
        '2026-11-03','2026-11-25','2026-12-25',
    }
    _nyse_holidays = {
        '2025-01-01','2025-01-20','2025-02-17','2025-04-18',
        '2025-05-26','2025-06-19','2025-07-04','2025-09-01',
        '2025-11-27','2025-12-25',
        '2026-01-01','2026-01-19','2026-02-16','2026-04-03',
        '2026-05-25','2026-06-19','2026-07-04','2026-09-07',
        '2026-11-26','2026-12-25',
    }
    _today = now_loc.strftime('%Y-%m-%d')
    _is_holiday = (_today in _nse_holidays) if is_indian else (_today in _nyse_holidays)

    _mo = now_loc.replace(hour=9,  minute=15, second=0, microsecond=0) if is_indian \
          else now_loc.replace(hour=9,  minute=30, second=0, microsecond=0)
    _mc = now_loc.replace(hour=15, minute=30, second=0, microsecond=0) if is_indian \
          else now_loc.replace(hour=16, minute=0,  second=0, microsecond=0)

    market_open = (now_loc.weekday() < 5) and not _is_holiday and (_mo <= now_loc <= _mc)
    if not market_open:
        day  = now_loc.strftime('%A')
        time = now_loc.strftime('%I:%M %p')
        reason = 'public holiday' if _is_holiday else ('weekend' if now_loc.weekday() >= 5 else 'after hours')
        return jsonify({
            "success": False,
            "error": f"Market is closed ({day} {time} — {reason}). {market_name} trades {market_hours_str}.",
            "market_closed": True,
        }), 400

    try:
        import yfinance as yf
        import numpy as np
        from ..knowledge.trading_decision_rules import TRADING_SYSTEM_PROMPT, build_trade_prompt
        from ..utils.llm_client import LLMClient

        wallet = _load_wallet()

        # ── 1. Fetch OHLCV ────────────────────────────────────────────────────
        df = _fetch_ohlcv(ticker, days=30, interval='1h')
        if df is None or len(df) < 20:
            df = _fetch_ohlcv(ticker, days=90, interval='1d')
        if df is None or len(df) < 20:
            return jsonify({"success": False, "error": f"No data for {ticker}"}), 404

        c = df['close']
        h = df['high']
        l = df['low']
        v = df['volume']

        # ── 2. Live price ─────────────────────────────────────────────────────
        try:
            info  = yf.Ticker(ticker).fast_info
            live  = getattr(info, 'last_price', None) or getattr(info, 'regularMarketPrice', None)
            price = float(live) if live else float(c.iloc[-1])
        except Exception:
            price = float(c.iloc[-1])

        # ── 3. Indicators ─────────────────────────────────────────────────────
        delta = c.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = float((100 - (100 / (1 + gain / loss.replace(0, np.nan)))).iloc[-1])

        ema9   = float(c.ewm(span=9,   adjust=False).mean().iloc[-1])
        ema21  = float(c.ewm(span=21,  adjust=False).mean().iloc[-1])
        ema50  = float(c.ewm(span=50,  adjust=False).mean().iloc[-1])
        ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])

        tr  = np.maximum(h - l, np.maximum(abs(h - c.shift()), abs(l - c.shift())))
        atr = float(tr.rolling(14).mean().iloc[-1])

        avg_vol   = float(v.iloc[-21:-1].mean()) if len(v) > 21 else float(v.mean())
        vol_ratio = float(v.iloc[-1] / avg_vol) if avg_vol > 0 else 1.0

        bars    = min(len(df), 6)
        typical = (h.iloc[-bars:] + l.iloc[-bars:] + c.iloc[-bars:]) / 3
        vwap    = float((typical * v.iloc[-bars:]).sum() / v.iloc[-bars:].sum()) if v.iloc[-bars:].sum() > 0 else price

        chg_1d = float((c.iloc[-1] - c.iloc[-7])  / c.iloc[-7]  * 100) if len(c) >= 7  else 0
        chg_5d = float((c.iloc[-1] - c.iloc[-35]) / c.iloc[-35] * 100) if len(c) >= 35 else 0

        # ── 4. Composite score (for context) ──────────────────────────────────
        rsi_score  = max(0, min(100, (50 - rsi) * 2 + 50))
        ema_score  = 80 if ema9 > ema21 else 20
        vwap_score = 70 if price > vwap else 30
        vol_score  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
        mom_score  = min(100, max(0, chg_1d * 10 + 50))
        score      = round(rsi_score * 0.25 + ema_score * 0.25 + vwap_score * 0.20
                           + vol_score * 0.15 + mom_score * 0.15, 1)

        # ── 5. Candlestick pattern detection (last 3 bars) ────────────────────
        def _candle_desc(i):
            o, hi, lo, cl = float(df['open'].iloc[i]), float(h.iloc[i]), float(l.iloc[i]), float(c.iloc[i])
            body  = abs(cl - o)
            range_ = hi - lo
            bull  = cl > o
            upper_wick = hi - max(o, cl)
            lower_wick = min(o, cl) - lo
            if range_ == 0:
                return "Doji"
            if body / range_ < 0.1:
                if upper_wick > 2 * lower_wick:
                    return "Gravestone Doji (bearish)"
                if lower_wick > 2 * upper_wick:
                    return "Dragonfly Doji (bullish)"
                return "Doji (indecision)"
            if body / range_ > 0.85:
                return f"{'Bullish' if bull else 'Bearish'} Marubozu (strong {'up' if bull else 'down'})"
            if lower_wick > 2 * body and upper_wick < body:
                return f"{'Hammer (bullish reversal)' if not bull else 'Hammer (bullish)'}"
            if upper_wick > 2 * body and lower_wick < body:
                return f"{'Shooting Star (bearish reversal)' if bull else 'Inverted Hammer (bullish tentative)'}"
            return f"{'Bullish' if bull else 'Bearish'} candle ({body/range_*100:.0f}% body)"

        n = len(df)
        candle_lines = []
        for i in range(max(0, n - 3), n):
            label = ["3 bars ago", "2 bars ago", "Last bar"][i - (n - 3)]
            candle_lines.append(f"  {label}: {_candle_desc(i)}")

        # Multi-candle pattern detection
        if n >= 3:
            o1, c1 = float(df['open'].iloc[-3]), float(c.iloc[-3])
            o2, c2 = float(df['open'].iloc[-2]), float(c.iloc[-2])
            o3, c3 = float(df['open'].iloc[-1]), float(c.iloc[-1])
            body1 = abs(c1 - o1); body3 = abs(c3 - o3)
            if c1 < o1 and abs(c2 - o2) < 0.3 * body1 and c3 > o3 and body3 > 0.5 * body1:
                candle_lines.append("  ⭐ PATTERN: Morning Star (BULLISH REVERSAL)")
            if c1 > o1 and abs(c2 - o2) < 0.3 * body1 and c3 < o3 and body3 > 0.5 * body1:
                candle_lines.append("  ⭐ PATTERN: Evening Star (BEARISH REVERSAL)")
            if n >= 2:
                prev_body = abs(float(c.iloc[-2]) - float(df['open'].iloc[-2]))
                curr_body = abs(float(c.iloc[-1]) - float(df['open'].iloc[-1]))
                prev_bull = float(c.iloc[-2]) > float(df['open'].iloc[-2])
                curr_bull = float(c.iloc[-1]) > float(df['open'].iloc[-1])
                if not prev_bull and curr_bull and curr_body > prev_body:
                    candle_lines.append("  ⭐ PATTERN: Bullish Engulfing (STRONG BUY SIGNAL)")
                if prev_bull and not curr_bull and curr_body > prev_body:
                    candle_lines.append("  ⭐ PATTERN: Bearish Engulfing (STRONG SELL SIGNAL)")

        candles_desc = "\n".join(candle_lines)

        # ── 6. Signals: FOMO, news (multi-source), Reddit ────────────────────
        fomo_score       = 50
        news_headlines   = []
        reddit_sentiment = 0.5
        try:
            # FOMO from already-computed technicals
            vol_s  = min(100, max(0, (vol_ratio - 1) * 40 + 50))
            mom_s  = min(100, max(0, chg_1d * 10 + 50))
            chg5_s = min(100, max(0, chg_5d * 5 + 50))
            fomo_score = int(max(0, min(100, vol_s * 0.4 + mom_s * 0.3 + chg5_s * 0.3)))

            # ── Priority 1: signals cache (has MC + ET + SA + Reddit already) ──
            from .market import _cache_get, _cache_key
            import xml.etree.ElementTree as _ET
            import re as _re
            sig_cached = _cache_get(_cache_key('signals', ticker))
            if sig_cached:
                cached_news = sig_cached.get('news', [])
                news_headlines = [h.get('title', '') for h in cached_news if h.get('title')][:10]
                reddit_sentiment = sig_cached.get('reddit_sentiment', 0.5)

            # ── Priority 2: fresh multi-source fetch if cache miss ─────────────
            if not news_headlines:
                _is_indian = ticker.endswith('.NS') or ticker.endswith('.BO')
                _clean_q   = ticker.replace('.NS', '').replace('.BO', '')
                _hdrs      = {'User-Agent': 'PhoenixTrade/1.0 ai-trade'}

                # yfinance news (always)
                try:
                    raw_news = getattr(yf.Ticker(ticker), 'news', None) or []
                    for n in raw_news[:5]:
                        t = n.get('title', '')
                        if t:
                            news_headlines.append(t)
                except Exception:
                    pass

                if _is_indian:
                    # Moneycontrol RSS
                    try:
                        mc = requests.get('https://www.moneycontrol.com/rss/latestnews.xml',
                                          headers=_hdrs, timeout=6)
                        if mc.ok:
                            root = _ET.fromstring(mc.content)
                            for item in root.findall('./channel/item')[:40]:
                                t = (item.findtext('title') or '').strip()
                                if t and _re.search(_clean_q, t, _re.I):
                                    news_headlines.append(f'[Moneycontrol] {t}')
                    except Exception:
                        pass
                    # Economic Times RSS (two feeds)
                    for _et_url in [
                        'https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms',
                        'https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms',
                    ]:
                        try:
                            et = requests.get(_et_url, headers=_hdrs, timeout=5)
                            if et.ok:
                                root = _ET.fromstring(et.content)
                                for item in root.findall('./channel/item')[:20]:
                                    t = (item.findtext('title') or '').strip()
                                    if t and _re.search(_clean_q, t, _re.I):
                                        news_headlines.append(f'[ET Markets] {t}')
                        except Exception:
                            pass
                else:
                    # Seeking Alpha RSS (US tickers)
                    try:
                        sa = requests.get(
                            f'https://seekingalpha.com/api/sa/combined/{_clean_q}.xml',
                            headers=_hdrs, timeout=5)
                        if sa.ok:
                            root = _ET.fromstring(sa.content)
                            for item in root.findall('./channel/item')[:8]:
                                t = (item.findtext('title') or '').strip()
                                if t:
                                    news_headlines.append(f'[Seeking Alpha] {t}')
                    except Exception:
                        pass

                # Reddit sentiment (fresh fetch)
                try:
                    _subs = (['IndiaInvestments', 'IndianStockMarket', 'stocks']
                             if _is_indian else ['wallstreetbets', 'stocks', 'investing'])
                    _rp = []
                    for _sub in _subs[:3]:
                        r = requests.get(f'https://www.reddit.com/r/{_sub}/search.json',
                                         headers=_hdrs,
                                         params={'q': _clean_q, 'sort': 'hot',
                                                 'limit': 5, 't': 'week', 'restrict_sr': 1},
                                         timeout=4)
                        if r.status_code == 200:
                            for p in r.json().get('data', {}).get('children', []):
                                d = p.get('data', {})
                                if d.get('title'):
                                    _rp.append({'score': d.get('score', 1),
                                                'upvote_ratio': d.get('upvote_ratio', 0.5)})
                    if _rp:
                        _total = sum(x['score'] for x in _rp)
                        if _total > 0:
                            reddit_sentiment = round(
                                sum(x['upvote_ratio'] * x['score'] for x in _rp) / _total, 3)
                except Exception:
                    pass

            news_headlines = [h for h in news_headlines if h][:10]
        except Exception:
            pass

        # ── 7. Fundamentals (PE, debt, ROE) ──────────────────────────────────
        fundamentals = {}
        try:
            info = yf.Ticker(ticker).info
            def _fi(k): return info.get(k)
            fundamentals = {
                'pe_ratio':        _fi('trailingPE') or _fi('forwardPE'),
                'pb_ratio':        _fi('priceToBook'),
                'debt_to_equity':  _fi('debtToEquity'),
                'roe':             round(_fi('returnOnEquity') * 100, 1) if _fi('returnOnEquity') else None,
                'revenue_growth':  round(_fi('revenueGrowth') * 100, 1) if _fi('revenueGrowth') else None,
                'profit_margin':   round(_fi('profitMargins') * 100, 1) if _fi('profitMargins') else None,
                'market_cap_cr':   round(_fi('marketCap') / 1e7, 0) if _fi('marketCap') else None,
                'sector':          _fi('sector'),
                'analyst_target':  _fi('targetMeanPrice'),
                'dividend_yield':  round(_fi('dividendYield') * 100, 2) if _fi('dividendYield') else None,
            }
        except Exception:
            pass

        # ── 8. Call LLM with trading knowledge ───────────────────────────────
        in_position    = ticker in wallet['positions']
        pos_entry      = wallet['positions'].get(ticker, {}).get('avg_entry') if in_position else None
        # Use shared levels calculator anchored to live price (not ideal entry)
        # so SL/T1 reflect actual trade risk, not the planned entry zone
        lv       = _compute_levels(df, exec_price=price)
        user_msg = build_trade_prompt(
            ticker=ticker, price=price, rsi=rsi,
            ema9=ema9, ema21=ema21, ema50=ema50, ema200=ema200,
            vwap=vwap, vol_ratio=vol_ratio, atr=atr,
            chg_1d=chg_1d, chg_5d=chg_5d, score=score,
            candles_desc=candles_desc, cash=wallet['cash'],
            in_position=in_position, position_entry=pos_entry,
            levels=lv,
            fomo_score=fomo_score,
            news_headlines=news_headlines,
            reddit_sentiment=reddit_sentiment,
            fundamentals=fundamentals,
        )
        llm_decision = None
        action = "HOLD"
        reason = f"Score={score}, RSI={rsi:.1f}"
        sl_price = lv['stop_loss']
        t1_price = lv['target_1']
        t2_price = lv['target_2']

        try:
            llm    = LLMClient()
            raw    = llm.chat(
                messages=[
                    {"role": "system", "content": TRADING_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0.2,
                max_tokens=512,
            )
            # Extract JSON from response
            m = _re.search(r'\{[\s\S]*\}', raw)
            if m:
                llm_decision = _json.loads(m.group())
                action   = llm_decision.get('action', 'HOLD').upper()
                reason   = llm_decision.get('reason', reason)
                sl_price = float(llm_decision.get('stop_loss', sl_price))
                t1_price = float(llm_decision.get('target_1', t1_price))
                t2_price = float(llm_decision.get('target_2', t2_price))
                logger.info(f"LLM decision for {ticker}: {action} (confidence={llm_decision.get('confidence')})")
        except Exception as llm_err:
            # Fallback to rule-based if LLM fails
            logger.warning(f"LLM call failed for {ticker}, using rule-based fallback: {llm_err}")
            oversold  = rsi < 38
            overbought = rsi > 68
            if in_position:
                pos = wallet['positions'][ticker]
                old_sl = pos.get('stop_loss', pos['avg_entry'] - atr * 1.2)
                old_t1 = pos.get('target_1', pos['avg_entry'] + atr * 1.5)
                if price <= old_sl:
                    action = "SELL"; reason = f"Stop loss hit at {price:.2f}"
                elif price >= old_t1:
                    action = "SELL"; reason = f"Target reached at {price:.2f}"
                elif score <= 45 or overbought:
                    action = "SELL"; reason = f"Deteriorated (score={score})"
                else:
                    reason = f"Holding — score={score}"
            else:
                if (score >= 55 and rsi < 68) or (oversold and price > ema21):
                    action = "BUY"
                    reason = f"{'Oversold bounce' if oversold else 'Signal'} — score={score}, RSI={rsi:.1f}"

        # ── 7. Execute the decision ───────────────────────────────────────────
        trade_result = None
        trade_skipped_reason = None

        if action == "BUY" and not in_position:
            max_spend = wallet['cash'] * min(cap_pct, 0.25)
            qty  = max(1, int(max_spend / price))
            cost = qty * price
            if cost <= wallet['cash']:
                wallet['cash'] -= cost
                wallet['positions'][ticker] = {
                    "qty": qty, "avg_entry": price,
                    "entry_date": datetime.now().isoformat(),
                    "stop_loss": sl_price, "target_1": t1_price, "target_2": t2_price,
                }
                trade_result = {"action": "BUY", "qty": qty, "price": price, "cost": round(cost, 2)}
                wallet['total_trades'] += 1
                wallet['trades'].append({
                    "ticker": ticker, "action": "BUY", "qty": qty,
                    "price": price, "cost": round(cost, 2),
                    "date": datetime.now().isoformat(), "reason": reason,
                })
            else:
                trade_skipped_reason = f"Insufficient cash (need ₹{cost:.0f}, have ₹{wallet['cash']:.0f})"
        elif action == "BUY" and in_position:
            trade_skipped_reason = "Already holding this stock — waiting for exit before re-entry"

        elif action == "SELL" and in_position:
            pos      = wallet['positions'].pop(ticker)
            proceeds = pos['qty'] * price
            wallet['cash'] += proceeds
            pnl = proceeds - pos['qty'] * pos['avg_entry']
            won = pnl > 0
            wallet['won']  += 1 if won else 0
            wallet['lost'] += 0 if won else 1
            wallet['trades'].append({
                "ticker": ticker, "action": "SELL", "qty": pos['qty'],
                "entry": pos['avg_entry'], "exit": price,
                "pnl": round(pnl, 2), "pnl_pct": round(pnl / (pos['qty'] * pos['avg_entry']) * 100, 2),
                "result": "WIN" if won else "LOSS",
                "date": datetime.now().isoformat(), "reason": reason,
            })
            trade_result = {"action": "SELL", "qty": pos['qty'], "price": price,
                            "pnl": round(pnl, 2), "result": "WIN" if won else "LOSS"}
            wallet['total_trades'] += 1
        elif action == "SELL" and not in_position:
            trade_skipped_reason = "No open position to sell — AI is watching for a BUY entry"
        elif action == "HOLD":
            trade_skipped_reason = "Holding — no trade triggered"

        _save_wallet(wallet)

        return jsonify({
            "success": True,
            "data": {
                "ticker":      ticker,
                "action":      action,
                "reason":      reason,
                "score":       score,
                "price":       price,
                "rsi":         round(rsi, 1),
                "stop_loss":   sl_price,
                "target_1":    t1_price,
                "target_2":    t2_price,
                "signals":     llm_decision.get('signals_triggered', []) if llm_decision else [],
                "confidence":  llm_decision.get('confidence', None) if llm_decision else None,
                "trade":               trade_result,
                "trade_skipped_reason": trade_skipped_reason,
                "wallet_cash": round(wallet['cash'], 2),
            }
        })

    except Exception as e:
        logger.error(f"AI trade failed for {ticker}: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@trade_bp.route('/wallet/reset', methods=['POST'])
def reset_wallet():
    """Reset the paper wallet to $10,000 starting capital."""
    body = request.get_json(silent=True) or {}
    cash = float(body.get('cash', 10000))
    wallet = {
        "cash": cash, "starting_cash": cash,
        "positions": {}, "trades": [],
        "total_trades": 0, "won": 0, "lost": 0,
        "created_at": datetime.now().isoformat(),
    }
    _save_wallet(wallet)
    return jsonify({"success": True, "data": {"message": f"Wallet reset to ${cash:,.0f}", "cash": cash}})


@trade_bp.route('/positions', methods=['GET'])
def get_positions():
    """Get current open positions from OpenAlgo broker."""
    client = _get_openalgo_client()
    if not client:
        return jsonify({"success": True, "data": [], "mode": "NOT_CONFIGURED"})
    try:
        positions = client.positions()
        return jsonify({"success": True, "data": positions, "mode": "LIVE"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@trade_bp.route('/funds', methods=['GET'])
def get_funds():
    """Get account funds from OpenAlgo broker."""
    client = _get_openalgo_client()
    if not client:
        return jsonify({"success": True, "data": {}, "mode": "NOT_CONFIGURED"})
    try:
        funds = client.funds()
        return jsonify({"success": True, "data": funds, "mode": "LIVE"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── F&O Auto-Scanner endpoints ────────────────────────────────────────────────

@trade_bp.route('/fo-scanner/start', methods=['POST'])
def fo_scanner_start():
    """Start the F&O auto-scanner background service."""
    from ..services.fo_scanner import start as _start
    _start()
    return jsonify({"success": True, "data": {"message": "F&O scanner started"}})


@trade_bp.route('/fo-scanner/stop', methods=['POST'])
def fo_scanner_stop():
    """Stop the F&O auto-scanner."""
    from ..services.fo_scanner import stop as _stop
    _stop()
    return jsonify({"success": True, "data": {"message": "F&O scanner stopped"}})


@trade_bp.route('/fo-scanner/trigger', methods=['POST'])
def fo_scanner_trigger():
    """Trigger an immediate scan cycle right now."""
    from ..services.fo_scanner import trigger_now as _trigger
    _trigger()
    return jsonify({"success": True, "data": {"message": "Scan cycle triggered"}})


@trade_bp.route('/live-feed', methods=['GET'])
def live_feed():
    """
    SSE stream of real-time trading events (1 tick/sec):
      - tick_update : every option-leg tick with greeks + decision
      - exit_partial / exit_full : actual exits
      - commentary  : every 30s plain-English narrative
    """
    from flask import Response, stream_with_context
    from ..services import live_monitor
    import queue as _queue

    q = live_monitor.subscribe_sse()

    def gen():
        try:
            yield 'retry: 5000\n\n'
            yield f': connected at {datetime.now().isoformat()}\n\n'
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f'data: {msg}\n\n'
                except _queue.Empty:
                    yield ': keepalive\n\n'
        finally:
            live_monitor.unsubscribe_sse(q)

    return Response(stream_with_context(gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


@trade_bp.route('/live-status', methods=['GET'])
def live_status():
    """Current per-position decision snapshot (last tick evaluation)."""
    from ..services import live_monitor
    return jsonify({"success": True, "data": live_monitor.last_status()})


@trade_bp.route('/option-chain', methods=['GET'])
def option_chain():
    """
    Return ±N strikes around ATM for an underlying, with full broker
    metadata so the client can BS-reprice each strike on every spot tick.

    Query:
      underlying  — '^NSEI', '^NSEBANK', 'RELIANCE.NS' …
      strikes     — number of strikes total (default 11 → ATM ±5)
      min_dte     — min days to expiry (default 3)
      max_dte     — max days to expiry (default 21)
    """
    from ..services.option_planner import (
        _nse_base, _strike_step, _stock_strike_step,
        _nearest_expiry, _atm_iv_estimate, _live_spot,
    )
    from ..services import broker_utils as bu
    from ..api.indmoney import _load_instruments

    ticker  = (request.args.get('underlying') or '').strip()
    n       = int(request.args.get('strikes') or 11)
    # Wider default than the planner uses: chain is for browsing — show whichever
    # expiry exists, including 1-DTE (today/tomorrow) and monthly-only contracts
    # like BANKNIFTY (NSE removed BANKNIFTY weekly expiries in 2024).
    min_dte = int(request.args.get('min_dte') or 2)
    max_dte = int(request.args.get('max_dte') or 35)

    if not ticker:
        return jsonify({"success": False, "error": "underlying required"}), 400

    spot = _live_spot(ticker)
    if not spot:
        return jsonify({"success": False, "error": f"no spot for {ticker}"}), 404

    base = _nse_base(ticker)
    step = _strike_step(base) or _stock_strike_step(spot)
    if not step:
        return jsonify({"success": False, "error": f"no strike step for {base}"}), 404

    # Pick the nearest expiry (CE side; same expiry serves PE)
    exp = _nearest_expiry(base, 'CE', max_dte=max_dte, min_dte=min_dte)
    if not exp:
        return jsonify({"success": False,
                        "error": f"no expiry in [{min_dte},{max_dte}]d for {base}"}), 404

    expiry_d = exp['expiry']
    dte_d    = (expiry_d - bu.today_ist()).days
    iv       = _atm_iv_estimate(base, spot, expiry_d, 'CE')

    # Build the ladder of strikes around ATM (n strikes, centred)
    atm_strike = int(round(spot / step) * step)
    half       = n // 2
    target_strikes = [atm_strike + (i - half) * step for i in range(n)]

    # Index F&O master by (option_type, strike) for fast lookup
    rows_ce = {}
    rows_pe = {}
    for inst in _load_instruments('fno'):
        sym = str(bu._field(inst, 'trading_symbol', '')).strip().upper()
        if not sym.startswith(base):
            continue
        if bu._parse_expiry(bu._field(inst, 'expiry', '')) != expiry_d:
            continue
        opt = str(bu._field(inst, 'option_type', '')).strip().upper()
        try:
            k = int(float(bu._field(inst, 'strike', 0) or 0))
        except Exception:
            continue
        exch = str(bu._field(inst, 'exchange', 'NFO')).strip().upper()
        if 'NFO' in exch or 'NSE' in exch:   exch = 'NFO'
        elif 'BFO' in exch or 'BSE' in exch: exch = 'BFO'
        try: lot = int(float(bu._field(inst, 'lot_size', 0) or 0))
        except: lot = 0
        meta = {
            'security_id':    str(bu._field(inst, 'security_id', '')).strip(),
            'exchange':       exch,
            'lot_size':       lot,
            'strike':         k,
            'trading_symbol': sym,
        }
        if opt == 'CE': rows_ce[k] = meta
        elif opt == 'PE': rows_pe[k] = meta

    chain = []
    for k in target_strikes:
        ce = rows_ce.get(k)
        pe = rows_pe.get(k)
        if not ce and not pe:
            continue
        chain.append({'strike': k, 'ce': ce, 'pe': pe})

    if not chain:
        return jsonify({"success": False,
                        "error": f"no strikes resolved around ATM for {base}"}), 404

    # ── Live-quote enrichment: OI / volume / bid-ask for every strike ────────
    # Single batched REST call (chunked to 20-codes per HTTP) — far cheaper
    # than 44 individual quote requests.
    from ..api.indmoney import _ind_option_quotes_batch
    codes = []
    for row in chain:
        if row['ce'] and row['ce'].get('security_id'):
            codes.append(f"{row['ce']['exchange']}_{row['ce']['security_id']}")
        if row['pe'] and row['pe'].get('security_id'):
            codes.append(f"{row['pe']['exchange']}_{row['pe']['security_id']}")
    quotes = _ind_option_quotes_batch(codes) if codes else {}

    def _attach(meta):
        if not meta:
            return None
        c = f"{meta['exchange']}_{meta['security_id']}"
        q = quotes.get(c) or {}
        bid, ask = float(q.get('bid') or 0), float(q.get('ask') or 0)
        ltp      = float(q.get('ltp') or 0)
        spread_pct = round((ask - bid) / ((bid + ask) / 2) * 100, 2) if (bid > 0 and ask > 0) else None
        return {**meta,
                'bid':        bid,
                'ask':        ask,
                'ltp':        ltp,
                'oi':         int(q.get('oi') or 0),
                'volume':     int(q.get('volume') or 0),
                'spread_pct': spread_pct}

    enriched = []
    for row in chain:
        enriched.append({
            'strike': row['strike'],
            'ce':     _attach(row['ce']),
            'pe':     _attach(row['pe']),
        })

    # ── Aggregate metrics: max-pain, PCR, IV skew ────────────────────────────
    total_ce_oi  = sum((r['ce']['oi'] for r in enriched if r['ce']) or [0])
    total_pe_oi  = sum((r['pe']['oi'] for r in enriched if r['pe']) or [0])
    total_ce_vol = sum((r['ce']['volume'] for r in enriched if r['ce']) or [0])
    total_pe_vol = sum((r['pe']['volume'] for r in enriched if r['pe']) or [0])
    pcr_oi  = round(total_pe_oi  / total_ce_oi,  2) if total_ce_oi  else None
    pcr_vol = round(total_pe_vol / total_ce_vol, 2) if total_ce_vol else None

    # Max-pain = strike where total option-writer payout is minimised, i.e. the
    # strike that maximises premium-decay revenue for sellers. Heuristic:
    # for each candidate strike K, compute Σ_i |K - strike_i| * (CE_OI_i + PE_OI_i)
    # weighted toward the side that's ITM. Pick the strike with the LOWEST sum.
    max_pain = None
    if total_ce_oi + total_pe_oi > 0:
        candidates = [r['strike'] for r in enriched]
        scores = []
        for K in candidates:
            pain = 0.0
            for r in enriched:
                s = r['strike']
                ce_oi = r['ce']['oi'] if r['ce'] else 0
                pe_oi = r['pe']['oi'] if r['pe'] else 0
                # CE writer loses if K > strike (price above strike at expiry)
                if K > s:
                    pain += (K - s) * ce_oi
                # PE writer loses if K < strike (price below strike at expiry)
                elif K < s:
                    pain += (s - K) * pe_oi
            scores.append((pain, K))
        max_pain = min(scores)[1]

    return jsonify({"success": True, "data": {
        "underlying":     ticker,
        "base":           base,
        "spot":           round(float(spot), 2),
        "atm_strike":     atm_strike,
        "step":           step,
        "expiry":         expiry_d.isoformat(),
        "days_to_expiry": dte_d,
        "iv_estimate":    round(float(iv), 4),
        "lot_size":       enriched[0]['ce']['lot_size'] if enriched[0].get('ce') else (enriched[0]['pe']['lot_size'] if enriched[0].get('pe') else 0),
        "strikes":        enriched,
        "totals": {
            "ce_oi":     total_ce_oi,
            "pe_oi":     total_pe_oi,
            "ce_volume": total_ce_vol,
            "pe_volume": total_pe_vol,
            "pcr_oi":    pcr_oi,     # >1 = put-heavy (bullish-contrarian); <0.7 = call-heavy
            "pcr_volume": pcr_vol,
            "max_pain":  max_pain,   # spot magnet near expiry
        },
    }})


@trade_bp.route('/option-plan', methods=['POST', 'GET'])
def option_plan():
    """
    Produce a precise, executable option trade ticket.

    Body / query:
      ticker        — '^NSEI' | 'RELIANCE.NS' | etc.
      bias          — 'BULL' | 'BEAR'
      target_delta  — default 0.50  (ATM)
      min_dte       — default 3     (skip same-day + 1-DTE theta cliff)
      max_dte       — default 21
      spot          — optional override for the underlying spot price

    Returns the ticket dict (symbol, entry premium, SL/T1/T2 in ₹, time-exit,
    Greeks, breakeven, max-loss, fees) so you can decide and execute manually
    or feed it directly to /api/trade/execute.
    """
    from ..services.option_planner import plan_option_trade

    body = request.get_json(silent=True) if request.method == 'POST' else None
    body = body or request.args.to_dict()
    ticker = (body.get('ticker') or '').strip()
    bias   = (body.get('bias')   or '').upper().strip()
    if not ticker or bias not in ('BULL', 'BEAR'):
        return jsonify({"success": False,
                        "error": "ticker and bias=BULL|BEAR required"}), 400
    try:
        _f = lambda k: float(body[k]) if body.get(k) not in (None, '', 0) else None
        ticket = plan_option_trade(
            underlying=ticker,
            bias=bias,
            spot=_f('spot'),
            target_delta=float(body.get('target_delta') or 0.50),
            min_dte=int(body.get('min_dte') or 3),
            max_dte=int(body.get('max_dte') or 21),
            rationale=body.get('rationale') or 'manual /option-plan',
            spot_target_1  = _f('spot_target_1'),
            spot_target_2  = _f('spot_target_2'),
            spot_stop_loss = _f('spot_stop_loss'),
            atr            = _f('atr'),
        )
        if not ticket:
            return jsonify({"success": False,
                            "error": "Could not build ticket — check ticker, "
                                     "broker token, or expiry availability"}), 404
        return jsonify({"success": True, "data": ticket})
    except Exception as e:
        logger.error(f"option-plan failed: {e}", exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


# ── Manual Position Tracker (independent of paper wallet) ────────────────────
# When the user clicks "I entered" on a LIVE ticket card, the full ticket is
# pinned via this endpoint. Pinned positions persist across browser refreshes
# and aren't overwritten by future AI verdicts. They continue to live-reprice
# and fire exit alerts via the LIVE tick repricer (frontend-side).
@trade_bp.route('/tracked', methods=['GET'])
def tracked_list():
    from ..services import tracked_positions as tp
    return jsonify({"success": True, "data": tp.list_tracked()})


@trade_bp.route('/tracked', methods=['POST'])
def tracked_add():
    from ..services import tracked_positions as tp
    body = request.get_json(silent=True) or {}
    ticket = body.get('ticket') or {}
    qty    = int(body.get('qty') or 1)
    notes  = body.get('notes') or ''
    try:
        rec = tp.add_tracked(ticket, qty=qty, notes=notes)
        return jsonify({"success": True, "data": rec})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@trade_bp.route('/tracked/<track_id>', methods=['DELETE'])
def tracked_remove(track_id: str):
    from ..services import tracked_positions as tp
    body = request.get_json(silent=True) or {}
    # Prefer query string (DELETE-with-body is unreliable across browsers)
    qp_premium = request.args.get('exit_premium')
    qp_reason  = request.args.get('exit_reason')
    try:
        exit_px = float(qp_premium) if qp_premium not in (None, '') else body.get('exit_premium')
    except ValueError:
        exit_px = body.get('exit_premium')
    rec = tp.remove_tracked(
        track_id,
        exit_premium=exit_px,
        exit_reason=qp_reason or body.get('exit_reason') or 'manual',
    )
    if not rec:
        return jsonify({"success": False, "error": "not found"}), 404
    return jsonify({"success": True, "data": rec})


# ── Backend watcher for tracked positions ────────────────────────────────────
# Server-side reprice + alert pipeline. Runs even when the browser is closed
# (so when the user reconnects, any SL/T1/T2 transitions that happened in the
# meantime are replayed via this SSE stream's initial snapshot push).

@trade_bp.route('/tracked/alerts/stream', methods=['GET'])
def tracked_alerts_stream():
    """
    SSE endpoint pushing real-time alerts for tracked positions.

    Event payload:
      { type:'tracked_alert', id, trading_symbol, status, message,
        premium, entry_premium, spot, pnl_pct, sl, t1, t2, timestamp }

    On connect, the latest payload for every position currently in an alert
    state is replayed once so a reconnecting client immediately re-arms the
    UI (no missed alerts on tab reload).
    """
    from flask import Response, stream_with_context
    from ..services import tracked_monitor as tm

    @stream_with_context
    def gen():
        q = tm.subscribe_sse()
        try:
            yield 'data: {"type":"alerts_connected"}\n\n'
            while True:
                try:
                    payload = q.get(timeout=15.0)
                    yield f'data: {json.dumps(payload, default=str)}\n\n'
                except Exception:
                    yield 'data: {"type":"heartbeat"}\n\n'
        except GeneratorExit:
            pass
        finally:
            tm.unsubscribe_sse(q)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@trade_bp.route('/tracked/alerts/state', methods=['GET'])
def tracked_alerts_state():
    """Diagnostic — current per-position last-known status & alert payload."""
    from ..services import tracked_monitor as tm
    return jsonify({"success": True, "data": tm.get_status()})


@trade_bp.route('/fo-scanner/status', methods=['GET'])
def fo_scanner_status():
    """Get current scanner state: signals, trades placed, errors."""
    from ..services.fo_scanner import get_state as _get_state
    return jsonify({"success": True, "data": _get_state()})


@trade_bp.route('/fo-scanner/stream', methods=['GET'])
def fo_scanner_stream():
    """SSE stream of live scanner events (signals, trades, skips)."""
    from flask import Response, stream_with_context
    from ..services.fo_scanner import subscribe_sse, unsubscribe_sse
    import queue as _queue

    q = subscribe_sse()

    def _gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except _queue.Empty:
                    yield 'data: {"type":"heartbeat"}\n\n'
        except GeneratorExit:
            pass
        finally:
            unsubscribe_sse(q)

    return Response(stream_with_context(_gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@trade_bp.route('/monitor/stream', methods=['GET'])
def monitor_stream():
    """SSE stream of live position monitor events (exits, entries)."""
    from flask import Response, stream_with_context
    from ..services.position_monitor import subscribe_sse, unsubscribe_sse
    import queue as _queue

    q = subscribe_sse()

    def _gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"data: {msg}\n\n"
                except _queue.Empty:
                    yield 'data: {"type":"heartbeat"}\n\n'
        except GeneratorExit:
            pass
        finally:
            unsubscribe_sse(q)

    return Response(stream_with_context(_gen()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
