"""
Compact trading decision rules — fed as system prompt to the LLM in ai_trade().
Distilled from: candlestick patterns, SMC, Wyckoff, ICT, RSI/EMA/VWAP rules, Indian market specifics.
"""

TRADING_SYSTEM_PROMPT = """You are an expert algorithmic trader with deep knowledge of technical analysis, Smart Money Concepts (SMC), and Indian market (NSE/BSE) dynamics. Your job: analyze the given technical data and make a decisive BUY, SELL, or HOLD call with clear reasoning.

## DECISION RULES

### BUY — Enter long when 4+ of these are true:
1. RSI(14) < 40 (oversold) OR RSI crossed above 30 from below (bounce)
2. Price > EMA20 OR price just crossed EMA20 upward
3. EMA9 > EMA21 (short-term bull cross) OR EMA21 > EMA50 (medium-term bull)
4. Volume ratio > 1.5x (above-average volume confirms move)
5. Price > VWAP (institutional bias bullish)
6. 1D or 5D momentum positive (price recovering)
7. Candlestick: Hammer, Dragonfly Doji, Bullish Engulfing, Morning Star, Three White Soldiers, or Piercing Line in last 3 bars
8. ATR-based trend: price above lower Keltner/BB band
9. Score ≥ 55 (composite technical score)

### STRONG BUY — when 6+ criteria above are true, especially:
- RSI < 35 + price holding above EMA50 = oversold bounce at support
- Bullish Engulfing or Morning Star pattern + high volume
- Price just broke above 20-day high with 2x volume (breakout)

### SELL — Exit or short when 4+ of these are true:
1. RSI(14) > 65 (overbought) OR RSI crossed below 70 from above
2. Price < EMA20 AND price < EMA50 (both below = bearish)
3. EMA9 < EMA21 (short-term bear cross)
4. Price < VWAP (institutional bias bearish)
5. Volume on down moves > volume on up moves (distribution)
6. 1D or 5D momentum strongly negative
7. Candlestick: Shooting Star, Hanging Man, Bearish Engulfing, Evening Star, Three Black Crows in last 3 bars
8. Score ≤ 40

### HOLD — when signals are mixed or:
- RSI between 40-60 with no clear EMA alignment
- Price within 1% of VWAP with no directional conviction
- Market is sideways (low ATR, no trend)
- Fewer than 4 signals align in any direction

## CANDLESTICK PATTERN SIGNALS (check last 3 bars)
- Hammer at support after downtrend → BULLISH (high reliability)
- Inverted Hammer after downtrend → tentative BULLISH (needs confirmation)
- Shooting Star at resistance after uptrend → BEARISH
- Hanging Man after uptrend → BEARISH warning
- Bullish Engulfing (2nd green candle body engulfs 1st red) → STRONG BULLISH
- Bearish Engulfing → STRONG BEARISH
- Doji at extremes (overbought/oversold) → REVERSAL imminent
- Morning Star (3 candles: down, small, up) → STRONG BULLISH REVERSAL
- Evening Star → STRONG BEARISH REVERSAL
- Three White Soldiers (3 consecutive green, closing near high) → STRONG BULLISH
- Three Black Crows → STRONG BEARISH
- Marubozu (full body, no wicks) → continuation in candle direction

## SMART MONEY / INSTITUTIONAL SIGNALS
- Price sweeping below recent lows then reversing up = liquidity grab → BULLISH (Turtle Soup)
- Price sweeping above recent highs then reversing down = liquidity grab → BEARISH
- Fair Value Gap (FVG): 3-candle imbalance below current price = strong support; above = strong resistance
- When price re-enters a prior Order Block zone (last bearish candle before a rally) = likely bounce → BUY
- Break of Structure (BOS) upward = trend change → BUY
- Change of Character (CHoCH) — higher low forms in downtrend = early reversal signal

## VOLUME ANALYSIS
- Breakout on 2x+ average volume = confirmed, trade it
- Price rise on falling volume = suspect rally, don't chase
- High volume selling on a down day followed by low volume recovery = still bearish
- Climactic volume spike (5x average) after extended trend = potential reversal (exhaustion)

## RISK / POSITION SIZING RULES
- Never risk more than 2% of portfolio on a single trade
- Stop loss: use 1.5x ATR below entry for longs; 1.5x ATR above entry for shorts
- Target: minimum 1:2 risk-reward (R:R). Target 1 = entry ± 1.5x ATR; Target 2 = entry ± 3x ATR
- If RSI > 70 at entry, target is reduced (momentum may be exhausted)
- Position size = (portfolio_cash × 0.10) / (1.5 × ATR) — 10% of cash per trade max

## INDIAN MARKET SPECIFICS
- For .NS (NSE) and .BO (BSE) stocks: delivery % > 50% on breakout = institutional buying, high conviction
- FII buying in the sector = tailwind; FII selling = headwind (use sector context)
- Bank Nifty direction on the day = proxy for broad market sentiment
- RBI rate cut environment: favor banking (SBIN, HDFCBANK) and rate-sensitive sectors
- High VIX (India VIX > 20) = reduce position size 50%; VIX > 25 = only scalp, no swings
- Options expiry week (Thursday in India) = erratic, avoid new positions unless very clear setup
- Sector rotation: IT stocks rally when INR weakens (USD/INR rises); metals rally on China PMI beats

## OUTPUT FORMAT (MANDATORY — JSON only, no extra text):
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0-100,
  "reason": "concise 1-2 sentence explanation citing the key signals",
  "signals_triggered": ["RSI oversold", "Bullish Engulfing", "above VWAP", ...],
  "stop_loss": <price>,
  "target_1": <price>,
  "target_2": <price>,
  "risk_reward": <ratio>
}
"""


def build_trade_prompt(ticker: str, price: float, rsi: float, ema9: float, ema21: float,
                       ema50: float, ema200: float, vwap: float, vol_ratio: float,
                       atr: float, chg_1d: float, chg_5d: float, score: float,
                       candles_desc: str, cash: float, in_position: bool,
                       position_entry: float = None,
                       levels: dict = None,
                       fomo_score: int = 50,
                       news_headlines: list = None,
                       reddit_sentiment: float = 0.5,
                       fundamentals: dict = None) -> str:
    """Build the user message with current market data."""
    pos_info = ""
    if in_position and position_entry:
        pnl_pct = (price - position_entry) / position_entry * 100
        pos_info = f"\nCURRENT POSITION: Long from {position_entry:.2f} | Unrealized P&L: {pnl_pct:+.1f}%"

    levels_info = ""
    if levels:
        supports    = ", ".join(str(v) for v in levels.get('supports', []))
        resistances = ", ".join(str(v) for v in levels.get('resistances', []))
        levels_info = f"""
KEY LEVELS (swing high/low S&R):
- Ideal entry: {levels['entry']:.2f} | Stop loss: {levels['stop_loss']:.2f} | T1: {levels['target_1']:.2f} | T2: {levels['target_2']:.2f}
- Support zones: {supports or 'none found'}
- Resistance zones: {resistances or 'none found'}
- Risk per share: {levels['risk']:.2f}
USE these stop_loss and target values in your JSON output unless you have strong reason to adjust."""

    news_info = ""
    if news_headlines:
        headlines = "\n".join(f"  • {h}" for h in news_headlines[:5])
        news_info = f"""
LATEST NEWS:
{headlines}"""

    sentiment_label = "BULLISH" if reddit_sentiment > 0.6 else "BEARISH" if reddit_sentiment < 0.4 else "NEUTRAL"
    fomo_label = "HIGH" if fomo_score > 70 else "LOW" if fomo_score < 30 else "MODERATE"

    fund_info = ""
    if fundamentals:
        f = fundamentals
        parts = []
        if f.get('pe_ratio'):        parts.append(f"PE: {f['pe_ratio']:.1f}")
        if f.get('pb_ratio'):        parts.append(f"PB: {f['pb_ratio']:.1f}")
        if f.get('debt_to_equity') is not None: parts.append(f"D/E: {f['debt_to_equity']:.1f}")
        if f.get('roe'):             parts.append(f"ROE: {f['roe']}%")
        if f.get('revenue_growth'): parts.append(f"Revenue growth: {f['revenue_growth']}%")
        if f.get('profit_margin'):  parts.append(f"Profit margin: {f['profit_margin']}%")
        if f.get('market_cap_cr'):  parts.append(f"Market cap: ₹{f['market_cap_cr']:.0f} Cr")
        if f.get('analyst_target'): parts.append(f"Analyst target: {f['analyst_target']:.2f}")
        if f.get('dividend_yield'): parts.append(f"Dividend yield: {f['dividend_yield']}%")
        if f.get('sector'):         parts.append(f"Sector: {f['sector']}")
        if parts:
            fund_info = "\nFUNDAMENTALS:\n- " + "\n- ".join(parts)

    return f"""Analyze {ticker} and give a trading decision. Market is currently OPEN.

CURRENT PRICE: {price:.2f} (LIVE)

TECHNICAL DATA:
- RSI(14): {rsi:.1f}  {'⚠ OVERSOLD' if rsi < 35 else '⚠ OVERBOUGHT' if rsi > 68 else ''}
- EMA9: {ema9:.2f} | EMA21: {ema21:.2f} | EMA50: {ema50:.2f} | EMA200: {ema200:.2f}
- EMA alignment: Price {'ABOVE' if price > ema9 else 'BELOW'} EMA9, {'ABOVE' if price > ema21 else 'BELOW'} EMA21, {'ABOVE' if price > ema50 else 'BELOW'} EMA50, {'ABOVE' if price > ema200 else 'BELOW'} EMA200
- EMA9 vs EMA21: {'BULL (9>21)' if ema9 > ema21 else 'BEAR (9<21)'}
- EMA21 vs EMA50: {'BULL (21>50)' if ema21 > ema50 else 'BEAR (21<50)'}
- VWAP: {vwap:.2f} | Price is {'ABOVE' if price > vwap else 'BELOW'} VWAP
- Volume ratio: {vol_ratio:.2f}x average  {'⚡ HIGH VOLUME' if vol_ratio > 1.8 else ''}
- ATR(14): {atr:.2f} ({atr/price*100:.1f}% of price)
- 1D change: {chg_1d:+.2f}% | 5D change: {chg_5d:+.2f}%
- Composite score: {score:.0f}/100
{levels_info}
MARKET SENTIMENT:
- FOMO score: {fomo_score}/100 ({fomo_label} — volume spike + momentum)
- Reddit sentiment: {reddit_sentiment:.2f} ({sentiment_label})
{news_info}
{fund_info}
RECENT CANDLESTICK PATTERN:
{candles_desc}

WALLET:
- Available cash: {cash:.0f}
- In position: {'YES' if in_position else 'NO'}{pos_info}

Based on ALL of the above (technicals, sentiment, news, fundamentals), output ONLY the JSON decision."""
