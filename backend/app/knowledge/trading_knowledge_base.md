# Comprehensive Trading Knowledge Base
## For AI Trading System — Vega

> Compiled from: Investopedia, StockCharts School, BabyPips, Zerodha Varsity, TradingView Wiki,
> CMT Association materials, and standard technical analysis references.
> Last updated: 2026-04-02

---

## TABLE OF CONTENTS

1. [Candlestick Patterns](#1-candlestick-patterns)
2. [Chart Patterns](#2-chart-patterns)
3. [Technical Indicators](#3-technical-indicators)
4. [Multi-Indicator Confluence Scoring](#4-multi-indicator-confluence-scoring)
5. [Volume Analysis](#5-volume-analysis)
6. [Market Structure & Smart Money Concepts](#6-market-structure--smart-money-concepts)
7. [Entry / Exit Rules](#7-entry--exit-rules)
8. [Indian Market Specifics](#8-indian-market-specifics)
9. [Sentiment Indicators](#9-sentiment-indicators)
10. [Swing vs Intraday Trading Rules](#10-swing-vs-intraday-trading-rules)

---

# 1. CANDLESTICK PATTERNS

## 1.1 Anatomy of a Candlestick

| Component | Description |
|-----------|-------------|
| Body | Rectangle between Open and Close |
| Upper Shadow (wick) | Line above body to the High |
| Lower Shadow (tail) | Line below body to the Low |
| Bullish candle | Close > Open (typically white/green) |
| Bearish candle | Close < Open (typically black/red) |

**Key Rule:** A candle's meaning is CONTEXT-DEPENDENT — always identify the prior trend before labeling a pattern.

---

## 1.2 Single Candlestick Patterns

### DOJI
- **Appearance:** Open ≈ Close; body is extremely thin or nonexistent; shadows vary by type.
- **Signal:** Indecision / potential reversal. Neither bulls nor bears in control.
- **Types:**

| Doji Type | Shape | Signal |
|-----------|-------|--------|
| Standard Doji | Equal-length shadows, tiny body | Indecision |
| Long-Legged Doji | Very long upper and lower shadows | High volatility indecision; stronger reversal signal |
| Gravestone Doji | Long upper shadow, no lower shadow | Bearish reversal at top; bulls pushed price up but bears closed it at open |
| Dragonfly Doji | Long lower shadow, no upper shadow | Bullish reversal at bottom; bears pushed down but bulls recovered fully |
| 4-Price Doji | No shadows at all (O=H=L=C) | Extremely thin market; rare |

- **Confirmation:** Next candle must confirm direction. Gravestone → next bearish candle; Dragonfly → next bullish candle.
- **Reliability:** Medium. Higher at key support/resistance levels or after extended trends.
- **Confluence boost:** + High volume on the Doji candle; + at Bollinger Band extremes; + RSI at overbought/oversold.

---

### HAMMER
- **Appearance:** Small body at the top of the candle range; lower shadow ≥ 2× body length; little or no upper shadow. Appears after a downtrend.
- **Signal:** BULLISH REVERSAL — sellers drove price down but buyers pushed it back up strongly.
- **Color:** Bullish (green/white) hammer is stronger than bearish (red/black) hammer, but both are valid.
- **Confirmation:** Next candle closes above Hammer's close/body. Volume spike on Hammer candle strengthens signal.
- **Reliability:** High (★★★★☆) — one of the most reliable single-candle reversal patterns.
- **Stop Loss:** Below the low of the Hammer's shadow.

---

### HANGING MAN
- **Appearance:** Identical shape to Hammer (small body at top, long lower shadow ≥ 2×, little upper shadow), but appears after an UPTREND.
- **Signal:** BEARISH REVERSAL — same structure as Hammer but at a top signals distribution.
- **Confirmation:** Next candle must close below Hanging Man's body. Volume on Hanging Man should be above average.
- **Reliability:** Medium (★★★☆☆) — requires strong confirmation.
- **Stop Loss:** Above the high of the Hanging Man.

---

### INVERTED HAMMER
- **Appearance:** Small body at the bottom; long upper shadow ≥ 2×; little or no lower shadow. Appears after a downtrend.
- **Signal:** BULLISH REVERSAL (tentative) — buyers attempted to push price up, failed to hold high, but signals buying interest.
- **Confirmation:** Next candle must be a strong bullish candle closing above the Inverted Hammer's body. Strong volume on confirmation candle required.
- **Reliability:** Medium (★★★☆☆). Weaker than Hammer alone.

---

### SHOOTING STAR
- **Appearance:** Identical shape to Inverted Hammer (small body at bottom of range, long upper shadow ≥ 2×, little lower shadow), but appears after an UPTREND.
- **Signal:** BEARISH REVERSAL — buyers pushed price high but sellers took over, closing near the open.
- **Confirmation:** Next candle closes below Shooting Star's body. Above-average volume on Shooting Star enhances reliability.
- **Reliability:** High (★★★★☆) — one of the most reliable bearish single-candle patterns.
- **Stop Loss:** Above the high of the Shooting Star wick.

---

### MARUBOZU
- **Appearance:** Full body candle with NO shadows (or very minimal). Entire candle range is the body.
- **Types:**

| Type | Description | Signal |
|------|-------------|--------|
| Bullish Marubozu | Green/white, O=L, C=H | Strong BULLISH continuation — bulls controlled the entire session |
| Bearish Marubozu | Red/black, O=H, C=L | Strong BEARISH continuation — bears controlled the entire session |
| Closing Marubozu | Shadow only on opening end | Moderate continuation signal |
| Opening Marubozu | Shadow only on closing end | Moderate continuation signal |

- **Reliability:** High (★★★★☆) as continuation signal in trending market. At extremes, can signal exhaustion.
- **Note:** Bullish Marubozu in downtrend = potential reversal. Bearish Marubozu in uptrend = potential reversal.

---

### SPINNING TOP
- **Appearance:** Small body (any color) with roughly equal upper and lower shadows. Body is centered.
- **Signal:** INDECISION — significant back-and-forth; neither side won the session.
- **Context:** After uptrend = bearish warning. After downtrend = bullish warning. During sideways = neutral.
- **Reliability:** Low (★★☆☆☆) on its own; meaningful only in context or when combined with other signals.

---

## 1.3 Two-Candlestick Patterns

### BULLISH ENGULFING
- **Appearance:** Small bearish candle (Day 1) followed by a large bullish candle (Day 2) whose body completely engulfs Day 1's body. Occurs after a downtrend.
- **Signal:** STRONG BULLISH REVERSAL — buying pressure overwhelmed selling pressure.
- **Confirmation:** High volume on Day 2; next candle also bullish.
- **Reliability:** High (★★★★☆).
- **Entry:** Buy on close of Day 2 or open of Day 3.
- **Stop Loss:** Below the low of Day 2 (engulfing candle).

---

### BEARISH ENGULFING
- **Appearance:** Small bullish candle (Day 1) followed by a large bearish candle (Day 2) whose body completely engulfs Day 1's body. Occurs after an uptrend.
- **Signal:** STRONG BEARISH REVERSAL.
- **Confirmation:** High volume on Day 2; next candle also bearish.
- **Reliability:** High (★★★★☆).
- **Stop Loss:** Above the high of Day 2.

---

### BULLISH HARAMI
- **Appearance:** Large bearish candle (Day 1) followed by a small bullish candle (Day 2) whose body is completely inside Day 1's body. Occurs after a downtrend.
- **Signal:** BULLISH REVERSAL (tentative) — "pregnant" candle signals slowing momentum.
- **Confirmation:** REQUIRES confirmation — next candle must be bullish. Without confirmation = weak signal.
- **Reliability:** Medium (★★★☆☆).

---

### BEARISH HARAMI
- **Appearance:** Large bullish candle (Day 1) followed by small bearish candle (Day 2) inside Day 1. Occurs after uptrend.
- **Signal:** BEARISH REVERSAL (tentative).
- **Confirmation:** Requires confirming bearish candle next.
- **Reliability:** Medium (★★★☆☆).

---

### HARAMI CROSS
- **Appearance:** Same as Harami but Day 2 is a Doji (very small body) instead of a regular candle. Considered stronger than regular Harami.
- **Signal:** Bullish or Bearish reversal depending on context. Doji adds more indecision weight.
- **Reliability:** Medium-High (★★★★☆) compared to standard Harami.

---

### TWEEZER TOP
- **Appearance:** Two candles with the same or nearly identical HIGHS. Day 1 bullish, Day 2 bearish (or vice versa in same area). Occurs at resistance after uptrend.
- **Signal:** BEARISH REVERSAL — resistance level clearly tested and rejected twice.
- **Reliability:** Medium (★★★☆☆). Stronger if both candles have long upper wicks.

---

### TWEEZER BOTTOM
- **Appearance:** Two candles with same or nearly identical LOWS. Day 1 bearish, Day 2 bullish. Occurs at support after downtrend.
- **Signal:** BULLISH REVERSAL — support level double-tested and held.
- **Reliability:** Medium (★★★☆☆).

---

### PIERCING LINE
- **Appearance:** Day 1 is a large bearish candle. Day 2 opens BELOW Day 1's low (gap down) and closes ABOVE the midpoint of Day 1's body. Occurs after downtrend.
- **Signal:** BULLISH REVERSAL — bulls fought back and reclaimed more than half of the prior day's losses.
- **Key Rule:** Close must be above 50% of Day 1 body. Below 50% = weak (On-Neck or In-Neck pattern).
- **Reliability:** High (★★★★☆).
- **Stop Loss:** Below Day 2's low.

---

### DARK CLOUD COVER
- **Appearance:** Day 1 is a large bullish candle. Day 2 opens ABOVE Day 1's high (gap up) and closes BELOW the midpoint of Day 1's body. Occurs after uptrend.
- **Signal:** BEARISH REVERSAL — mirror image of Piercing Line.
- **Key Rule:** Close must be below 50% of Day 1 body.
- **Reliability:** High (★★★★☆).
- **Stop Loss:** Above Day 2's high.

---

## 1.4 Three-Candlestick Patterns

### MORNING STAR
- **Appearance:**
  - Day 1: Large bearish candle (downtrend continuation)
  - Day 2: Small body (star) that gaps DOWN from Day 1; body is below Day 1's close
  - Day 3: Large bullish candle that closes above the midpoint (ideally 50%+) of Day 1's body
- **Signal:** STRONG BULLISH REVERSAL — one of the most reliable reversal patterns.
- **Confirmation:** Volume should increase on Day 3. Day 3 recovery above 50% of Day 1 is critical.
- **Reliability:** Very High (★★★★★).
- **Variant:** Morning Doji Star — Day 2 is a Doji. Even stronger signal.

---

### EVENING STAR
- **Appearance:**
  - Day 1: Large bullish candle (uptrend continuation)
  - Day 2: Small body (star) that gaps UP from Day 1
  - Day 3: Large bearish candle that closes below the midpoint of Day 1's body
- **Signal:** STRONG BEARISH REVERSAL — mirror of Morning Star.
- **Reliability:** Very High (★★★★★).
- **Variant:** Evening Doji Star — Day 2 is Doji. Stronger.

---

### THREE WHITE SOLDIERS
- **Appearance:** Three consecutive long bullish candles, each opening within the previous body and closing at or near its high. Small or no upper shadows.
- **Signal:** STRONG BULLISH REVERSAL (from downtrend) or BULLISH CONTINUATION (from consolidation).
- **Validity Rules:**
  - Each candle must open within prior candle's body (not gap above)
  - Each candle must close near its high
  - Candles should be roughly equal in size
- **Caution:** If the third candle has a long upper shadow = exhaustion warning (Advance Block pattern).
- **Reliability:** Very High (★★★★★) for reversal from significant lows.

---

### THREE BLACK CROWS
- **Appearance:** Three consecutive long bearish candles, each opening within the previous body and closing at or near its low. Small or no lower shadows.
- **Signal:** STRONG BEARISH REVERSAL (from uptrend) or BEARISH CONTINUATION.
- **Validity Rules:** Same mirror rules as Three White Soldiers.
- **Reliability:** Very High (★★★★★).

---

### THREE INSIDE UP / THREE INSIDE DOWN
- **Appearance:**
  - Three Inside Up: Bullish Harami (Day 1+2) followed by bullish confirmation candle (Day 3) closing above Day 1.
  - Three Inside Down: Bearish Harami (Day 1+2) followed by bearish confirmation candle (Day 3) closing below Day 1.
- **Signal:** Confirmed reversal (stronger than Harami alone).
- **Reliability:** Medium-High (★★★★☆).

---

### THREE OUTSIDE UP / THREE OUTSIDE DOWN
- **Appearance:**
  - Three Outside Up: Bullish Engulfing (Day 1+2) followed by strong bullish Day 3.
  - Three Outside Down: Bearish Engulfing (Day 1+2) followed by strong bearish Day 3.
- **Signal:** Confirmed reversal with strong momentum follow-through.
- **Reliability:** High (★★★★☆).

---

### ABANDONED BABY
- **Appearance:**
  - Bullish: Bearish candle → Doji that gaps down (no shadow overlap) → Bullish candle that gaps up.
  - Bearish: Bullish candle → Doji that gaps up (no shadow overlap) → Bearish candle that gaps down.
- **Signal:** STRONG REVERSAL. Gaps isolate the Doji, making it a true island reversal.
- **Reliability:** Very High (★★★★★) but RARE. True gap conditions required.

---

### RISING THREE METHODS
- **Appearance:** Long bullish candle → 3 small bearish candles (staying within prior bullish candle's range) → long bullish candle breaking above.
- **Signal:** BULLISH CONTINUATION — consolidation within an uptrend.
- **Reliability:** High (★★★★☆).

### FALLING THREE METHODS
- **Appearance:** Long bearish candle → 3 small bullish candles (within prior candle's range) → long bearish candle breaking below.
- **Signal:** BEARISH CONTINUATION.
- **Reliability:** High (★★★★☆).

---

### UPSIDE TASUKI GAP / DOWNSIDE TASUKI GAP
- **Upside Tasuki Gap:** Two bullish candles with gap between them; third candle opens inside second and partially fills gap but fails to fill completely.
- **Signal:** BULLISH CONTINUATION — gap acts as support.
- **Downside Tasuki Gap:** Mirror — BEARISH CONTINUATION.
- **Reliability:** Medium (★★★☆☆).

---

## 1.5 Candlestick Pattern Quick Reference Table

| Pattern | # Candles | Direction | Reliability |
|---------|-----------|-----------|-------------|
| Doji | 1 | Reversal (context) | ★★★☆☆ |
| Dragonfly Doji | 1 | Bullish Reversal | ★★★★☆ |
| Gravestone Doji | 1 | Bearish Reversal | ★★★★☆ |
| Hammer | 1 | Bullish Reversal | ★★★★☆ |
| Hanging Man | 1 | Bearish Reversal | ★★★☆☆ |
| Inverted Hammer | 1 | Bullish Reversal | ★★★☆☆ |
| Shooting Star | 1 | Bearish Reversal | ★★★★☆ |
| Bullish Marubozu | 1 | Bullish Continuation | ★★★★☆ |
| Bearish Marubozu | 1 | Bearish Continuation | ★★★★☆ |
| Spinning Top | 1 | Indecision | ★★☆☆☆ |
| Bullish Engulfing | 2 | Bullish Reversal | ★★★★☆ |
| Bearish Engulfing | 2 | Bearish Reversal | ★★★★☆ |
| Bullish Harami | 2 | Bullish Reversal | ★★★☆☆ |
| Bearish Harami | 2 | Bearish Reversal | ★★★☆☆ |
| Harami Cross | 2 | Reversal | ★★★★☆ |
| Piercing Line | 2 | Bullish Reversal | ★★★★☆ |
| Dark Cloud Cover | 2 | Bearish Reversal | ★★★★☆ |
| Tweezer Top | 2 | Bearish Reversal | ★★★☆☆ |
| Tweezer Bottom | 2 | Bullish Reversal | ★★★☆☆ |
| Morning Star | 3 | Bullish Reversal | ★★★★★ |
| Evening Star | 3 | Bearish Reversal | ★★★★★ |
| Morning Doji Star | 3 | Bullish Reversal | ★★★★★ |
| Evening Doji Star | 3 | Bearish Reversal | ★★★★★ |
| Three White Soldiers | 3 | Bullish Reversal/Cont. | ★★★★★ |
| Three Black Crows | 3 | Bearish Reversal/Cont. | ★★★★★ |
| Three Inside Up | 3 | Bullish Reversal | ★★★★☆ |
| Three Inside Down | 3 | Bearish Reversal | ★★★★☆ |
| Abandoned Baby | 3 | Strong Reversal | ★★★★★ |
| Rising Three Methods | 5 | Bullish Continuation | ★★★★☆ |
| Falling Three Methods | 5 | Bearish Continuation | ★★★★☆ |

---

# 2. CHART PATTERNS

## 2.1 Reversal Patterns

### HEAD AND SHOULDERS (H&S) — Bearish Reversal
- **Appearance:** Three peaks: Left Shoulder, Head (highest peak), Right Shoulder (similar height to Left). Neckline connects the troughs between peaks.
- **Psychology:** Uptrend loses momentum; each rally is weaker than the last.
- **Breakout Rule:** Price breaks BELOW the neckline on above-average volume.
- **Target Calculation:** Measure vertical distance from Head to Neckline → project that distance DOWN from breakout point.
  - Formula: `Target = Neckline breakout price − (Head price − Neckline price)`
- **Volume Pattern:** Decreasing volume Left Shoulder → Head → Right Shoulder; spike on neckline break.
- **Retest:** ~40% of the time price retests the neckline from below (becomes resistance). Retest entry safer.
- **Failure:** If price reclaims neckline after break = pattern failure. Exit short immediately.
- **Reliability:** Very High (★★★★★) — one of the most studied and reliable reversal patterns.

---

### INVERSE HEAD AND SHOULDERS — Bullish Reversal
- **Appearance:** Mirror of H&S — three troughs: Left Shoulder, Head (lowest), Right Shoulder.
- **Breakout Rule:** Breaks ABOVE neckline on high volume.
- **Target:** `Target = Neckline breakout price + (Neckline price − Head price)`
- **Reliability:** Very High (★★★★★).

---

### DOUBLE TOP — Bearish Reversal
- **Appearance:** Two roughly equal peaks (M shape) with a trough in between. Second peak fails to exceed first — signals exhaustion.
- **Breakout Rule:** Close BELOW the trough (swing low between the two tops) on high volume.
- **Target:** `Target = Trough price − (Peak price − Trough price)`
- **Key Rule:** Peaks must be at SIMILAR price levels (within ~3%). More time between peaks = stronger pattern.
- **Confirmation:** Second top ideally on declining volume relative to first top.
- **Reliability:** High (★★★★☆).

---

### DOUBLE BOTTOM — Bullish Reversal
- **Appearance:** Two roughly equal troughs (W shape). Second trough fails to go lower — signals exhausted sellers.
- **Breakout Rule:** Close ABOVE the peak between the two bottoms.
- **Target:** `Target = Peak price + (Peak price − Trough price)`
- **Reliability:** High (★★★★☆).

---

### TRIPLE TOP — Bearish Reversal
- **Appearance:** Three roughly equal peaks with two troughs. Extended resistance test.
- **Breakout Rule:** Close below lowest trough.
- **Target:** Same as Double Top method (measure height, project down).
- **Reliability:** Very High (★★★★★) — more tests = stronger resistance.

---

### TRIPLE BOTTOM — Bullish Reversal
- **Appearance:** Three roughly equal troughs. Extended support test.
- **Reliability:** Very High (★★★★★).

---

### ROUNDING BOTTOM (Saucer Bottom) — Bullish Reversal
- **Appearance:** Gradual, curved price decline followed by gradual recovery. Months to years in formation.
- **Signal:** Long-term accumulation and trend reversal. Volume is low during bottom, increases as right side forms.
- **Breakout:** Above the "rim" (the resistance level at the start and end of the saucer).
- **Target:** Height of the pattern projected upward.
- **Reliability:** High (★★★★☆) on weekly/monthly charts.

---

## 2.2 Continuation Patterns

### ASCENDING TRIANGLE — Bullish Continuation (sometimes Reversal)
- **Appearance:** Horizontal resistance line at top; rising support line (higher lows) at bottom. Converging.
- **Psychology:** Buyers increasingly aggressive at support; sellers defending horizontal resistance. Eventually buyers win.
- **Breakout Rule:** Close ABOVE horizontal resistance on high volume.
- **Target:** `Target = Breakout price + (Height of triangle at its widest point)`
- **Volume:** Typically decreasing during formation; spikes on breakout.
- **Timeframe:** Needs at least 2 touches on each line; ideally 3.
- **Reliability:** High (★★★★☆). ~75% break upward (per Bulkowski's statistics).
- **Bearish version:** Can break down if the horizontal resistance is particularly strong; treat as reversal if in downtrend.

---

### DESCENDING TRIANGLE — Bearish Continuation (sometimes Reversal)
- **Appearance:** Horizontal support line at bottom; declining resistance line (lower highs) at top.
- **Breakout Rule:** Close BELOW horizontal support on high volume.
- **Target:** `Target = Breakdown price − (Height of triangle at widest point)`
- **Reliability:** High (★★★★☆). ~72% break downward.

---

### SYMMETRICAL TRIANGLE — Neutral (Continuation)
- **Appearance:** Converging trendlines — lower highs and higher lows. Price coils.
- **Psychology:** Equilibrium of buyers and sellers. Direction of breakout = direction of continuation (usually prior trend).
- **Breakout Rule:** Close beyond either trendline on volume surge. Entry at breakout candle's close.
- **Target:** Height of triangle at widest point projected from breakout.
- **False Breakout Risk:** Higher than other triangles. Wait for close BEYOND the line, not just a wick.
- **Reliability:** Medium-High (★★★☆☆) — direction not predictable.

---

### FLAG — Bullish/Bearish Continuation
- **Appearance:** Sharp, near-vertical move (the "flagpole") followed by a small rectangular consolidation that slopes slightly AGAINST the trend.
  - Bullish Flag: Flagpole up, flag slopes down slightly.
  - Bearish Flag: Flagpole down, flag slopes up slightly.
- **Duration:** Short (1–4 weeks on daily chart; few candles on intraday).
- **Breakout Rule:** Break of the flag's upper (bullish) or lower (bearish) trendline, ideally on volume.
- **Target:** `Target = Breakout point + length of flagpole`
- **Volume:** Must decrease during flag consolidation; increase sharply on breakout.
- **Reliability:** Very High (★★★★★) — one of the highest win-rate continuation patterns.

---

### PENNANT — Bullish/Bearish Continuation
- **Appearance:** Sharp flagpole followed by a small SYMMETRICAL TRIANGLE (converging lines) as consolidation. Similar to flag but converging rather than parallel.
- **Breakout Rule:** Break above/below the pennant's converging lines.
- **Target:** Same as Flag — flagpole height from breakout.
- **Reliability:** Very High (★★★★★).
- **Difference from Flag:** Pennant has converging lines (triangle); Flag has parallel lines (rectangle/channel).

---

### CUP AND HANDLE — Bullish Continuation/Reversal
- **Appearance:**
  - Cup: Rounded bottom (U-shape, not V-shape) over weeks to months.
  - Handle: Small pullback/consolidation after the cup forms, in the upper half of the cup.
  - Handle should NOT retrace more than 50% of cup depth.
- **Breakout Rule:** Break ABOVE the cup's rim (resistance) with high volume.
- **Target:** `Target = Breakout price + depth of cup`
- **Volume:** Low during cup formation; spike on breakout. Handle should form on low volume.
- **Reliability:** Very High (★★★★★) on weekly charts.
- **Key Rules:**
  - Cup duration: 7 weeks to 65 weeks (ideal).
  - Handle forms in upper third of cup.
  - Handle drift: <15% from cup top.
  - Prior uptrend required.

---

### RECTANGLE — Continuation/Reversal
- **Appearance:** Price oscillates between horizontal support and resistance. Two parallel horizontal lines.
- **Breakout Rule:** Close beyond either line with volume. Direction = prior trend (continuation) or opposite (if extended base = reversal).
- **Target:** Height of rectangle projected from breakout.
- **Reliability:** Medium (★★★☆☆). Works better with more touches on each line.

---

### RISING WEDGE — Bearish Signal
- **Appearance:** Both support and resistance lines slope UPWARD, but support rises faster → converging. Often in uptrend or as counter-rally in downtrend.
- **Signal:** BEARISH — momentum is decreasing despite rising prices.
- **Breakout Rule:** Break BELOW lower support line.
- **Target:** Prior swing low before wedge formation.
- **Volume:** Decreasing as wedge narrows.
- **Reliability:** High (★★★★☆). False breakouts upward are common — wait for confirmation.

---

### FALLING WEDGE — Bullish Signal
- **Appearance:** Both lines slope DOWNWARD, resistance falls faster → converging. Often in downtrend or pullback within uptrend.
- **Signal:** BULLISH — declining momentum in a downward move.
- **Breakout Rule:** Break ABOVE upper resistance line.
- **Reliability:** High (★★★★☆).

---

### CHANNEL (Price Channel)
- **Types:** Ascending, Descending, Horizontal
- **Appearance:** Two parallel trendlines (support + resistance) containing price action.
- **Trading Rule:**
  - BUY at lower channel support, target upper channel resistance (in uptrend).
  - SELL at upper channel resistance, target lower channel support (in downtrend).
  - BREAKOUT trade: Break outside channel with volume = new trend beginning.
- **Target for breakout:** Channel height projected from breakout point.
- **Reliability:** Medium-High (★★★☆☆) for channel trades; High for breakout from channel.

---

## 2.3 Pattern Success Rate Reference (Bulkowski Statistics)

| Pattern | Breakout Direction | Average Move | Success Rate |
|---------|--------------------|--------------|--------------|
| Head & Shoulders | Down | −22% | 93% |
| Inverse H&S | Up | +37% | 89% |
| Double Top | Down | −18% | 72% |
| Double Bottom | Up | +40% | 78% |
| Ascending Triangle | Up | +36% | 75% |
| Descending Triangle | Down | −16% | 72% |
| Symmetrical Triangle | Either | ±18% | 54% up / 50% down |
| Bull Flag | Up | +23% | 67% |
| Bear Flag | Down | −22% | 67% |
| Cup & Handle | Up | +34% | 61% |
| Rising Wedge | Down | −14% | 69% |
| Falling Wedge | Up | +32% | 74% |

---

# 3. TECHNICAL INDICATORS

## 3.1 RSI — Relative Strength Index

### Calculation
```
RSI = 100 − (100 / (1 + RS))
RS = Average Gain over N periods / Average Loss over N periods
Standard period: N = 14
```

### Standard Levels
| Level | Interpretation |
|-------|----------------|
| > 70 | Overbought — potential sell / short signal |
| 30–70 | Neutral zone |
| < 30 | Oversold — potential buy signal |
| 50 | Midline — acts as trend filter (above = bullish bias, below = bearish) |
| > 80 | Extremely overbought (strong trend or exhaustion) |
| < 20 | Extremely oversold (strong trend or exhaustion) |

### RSI Rules by Market Condition
- **Trending market:** RSI overbought/oversold levels shift. In strong uptrend, RSI can stay 50–80. Oversold signals at 40, not 30.
- **Ranging market:** Classic 30/70 levels work well.
- **Adjustment:** For volatile stocks/crypto use 20/80 levels. For low-volatility stocks/indices use 35/65.

### RSI Divergence (Regular)
- **Bullish Divergence:** Price makes lower low; RSI makes higher low → momentum increasing while price falls → BUY signal.
- **Bearish Divergence:** Price makes higher high; RSI makes lower high → momentum decreasing while price rises → SELL signal.
- **Confirmation:** Divergence must be on at least 2 clear peaks/troughs separated by at least 5–10 candles.
- **Reliability:** High (★★★★☆) — especially at key S/R levels.

### RSI Hidden Divergence (Continuation)
- **Bullish Hidden Divergence:** Price makes HIGHER LOW (uptrend pullback); RSI makes LOWER LOW → trend continuation upward. BUY the pullback.
- **Bearish Hidden Divergence:** Price makes LOWER HIGH (downtrend rally); RSI makes HIGHER HIGH → trend continuation downward. SELL the rally.
- **Reliability:** High (★★★★☆) for trend-following entries.

### RSI Failure Swings (Strong Signals)
- **Bullish Failure Swing:**
  1. RSI falls below 30 (oversold)
  2. RSI bounces above 30 to a "failure swing high"
  3. RSI pulls back but does NOT go below 30 again
  4. RSI breaks above the failure swing high → BUY
- **Bearish Failure Swing:** Mirror above with 70.
- **Reliability:** Very High (★★★★★) — often precedes significant moves.

### RSI Period Settings
| Timeframe | Recommended RSI Period | Overbought/Oversold |
|-----------|------------------------|----------------------|
| Scalping (1–5 min) | 7 or 9 | 80/20 |
| Intraday (15–60 min) | 14 | 70/30 |
| Swing (Daily) | 14 | 70/30 |
| Position (Weekly) | 14 | 75/25 |

---

## 3.2 MACD — Moving Average Convergence Divergence

### Calculation
```
MACD Line = 12-period EMA − 26-period EMA
Signal Line = 9-period EMA of MACD Line
Histogram = MACD Line − Signal Line
Standard settings: (12, 26, 9)
```

### MACD Signals

#### 1. MACD Crossover (Basic Signal)
- **Bullish:** MACD line crosses ABOVE Signal line → BUY
- **Bearish:** MACD line crosses BELOW Signal line → SELL
- **Reliability:** Medium (★★★☆☆) alone; late signal. Best in trending markets, false signals in ranging markets.
- **Enhancement:** Crossover that occurs when both lines are BELOW zero is stronger bullish signal.

#### 2. Zero Line Cross
- **MACD crosses above zero:** Short-term EMA > Long-term EMA → bullish trend confirmed
- **MACD crosses below zero:** Short-term EMA < Long-term EMA → bearish trend confirmed
- **Use:** Trend filter. Only take longs when MACD > 0; only take shorts when MACD < 0.

#### 3. MACD Histogram
- **Histogram increases (bars growing):** Momentum strengthening in current direction.
- **Histogram decreases (bars shrinking):** Momentum weakening → potential reversal or pause.
- **Histogram crosses zero:** Same direction as MACD line crossover.
- **Key pattern — Divergence on Histogram:**
  - Bullish: Price lower low, histogram higher low → reversal coming.
  - Bearish: Price higher high, histogram lower high → reversal coming.

#### 4. MACD Divergence
- **Bullish:** Price makes lower low; MACD makes higher low → BUY.
- **Bearish:** Price makes higher high; MACD makes lower high → SELL.
- **Reliability:** High (★★★★☆) — stronger than RSI divergence on longer timeframes.

### MACD Settings by Timeframe
| Timeframe | Settings |
|-----------|----------|
| Scalp (5 min) | (5, 13, 3) or (8, 17, 9) |
| Intraday (15–60 min) | (12, 26, 9) standard |
| Swing (Daily) | (12, 26, 9) standard |
| Position (Weekly) | (19, 39, 9) |

---

## 3.3 Bollinger Bands

### Calculation
```
Middle Band = 20-period SMA
Upper Band = 20 SMA + (2 × 20-period Standard Deviation)
Lower Band = 20 SMA − (2 × 20-period Standard Deviation)
Standard settings: (20, 2)
```

### Key Concepts

#### Band Width and Volatility
- **Narrow bands (squeeze):** Low volatility period. Precedes explosive move. Direction unknown until breakout.
- **Wide bands:** High volatility period. Potential exhaustion of move.
- **Band Width Indicator:** `(Upper − Lower) / Middle`. Lowest band width readings in 6 months = setup for big move.

#### Price Position Relative to Bands
| Price Position | Interpretation |
|---------------|----------------|
| Touches/breaks Upper Band | Overbought in ranging market; continuation in strong trend |
| Touches/breaks Lower Band | Oversold in ranging market; continuation in strong downtrend |
| Returns to Middle Band | Mean reversion signal — price gravitates toward 20 SMA |
| Walks Upper Band | Strong bull trend; price repeatedly touches/exceeds upper band |
| Walks Lower Band | Strong bear trend |

#### Bollinger Band Signals

**1. Bollinger Squeeze (Setup, not signal)**
- Bands contract to very narrow width (low BB Width reading)
- When bands start expanding → momentum trade in direction of expansion
- Entry: Buy/sell on the first expanding candle in direction of breakout
- Often combined with volume spike for confirmation

**2. Double Bottom at Lower Band (W Pattern)**
- First touch of lower band on high volume
- Second touch on LOWER volume (less selling pressure)
- Bullish reversal signal → entry on break above middle band

**3. Three Pushes to Band (Exhaustion)**
- Three consecutive closes outside a band → exhaustion signal → mean reversion

**4. %B Indicator**
```
%B = (Price − Lower Band) / (Upper Band − Lower Band)
%B = 1.0 → Price at Upper Band
%B = 0.0 → Price at Lower Band
%B = 0.5 → Price at Middle Band
%B > 1.0 → Price above Upper Band
%B < 0.0 → Price below Lower Band
```
- **Buy signal:** %B < 0.05 (near/below lower band) + rising
- **Sell signal:** %B > 0.95 (near/above upper band) + falling
- **Combined with MFI:** %B > 0.8 AND MFI > 80 = Strong sell; %B < 0.2 AND MFI < 20 = Strong buy

### Bollinger Bands Settings by Timeframe
| Timeframe | Period | Std Dev |
|-----------|--------|---------|
| Intraday (5–15 min) | 20 | 2.0 |
| Intraday (60 min) | 20 | 2.0 |
| Swing (Daily) | 20 | 2.0 |
| Intraday scalp | 10 | 1.5 |
| Position (Weekly) | 20 | 2.5 |

---

## 3.4 EMA — Exponential Moving Average

### Calculation
```
EMA = Price × K + Previous EMA × (1 − K)
K = 2 / (N + 1)
More weight to recent prices vs SMA
```

### Key EMA Levels
| EMA | Usage |
|-----|-------|
| 9 EMA | Very short-term trend; intraday signals |
| 20 EMA | Short-term trend; dynamic support/resistance |
| 50 EMA | Medium-term trend; key S/R level for swing traders |
| 100 EMA | Long-term trend reference |
| 200 EMA | Major long-term trend; institutional benchmark |

### EMA Crossover Signals

#### Golden Cross (Bullish)
- **Definition:** 50 EMA crosses ABOVE 200 EMA (or 50 SMA above 200 SMA on daily chart)
- **Signal:** Long-term bullish trend beginning
- **Reliability:** High (★★★★☆) on daily chart; lagging but very reliable
- **Confirmation:** Rising volume + price above both EMAs

#### Death Cross (Bearish)
- **Definition:** 50 EMA crosses BELOW 200 EMA
- **Signal:** Long-term bearish trend beginning
- **Reliability:** High (★★★★☆)
- **Note:** Death cross often occurs AFTER a significant portion of decline — may be lagging.

#### Short-term Crossovers
- 9 EMA crosses above 20 EMA → short-term bullish
- 20 EMA crosses above 50 EMA → medium-term bullish
- All EMAs aligned (9 > 20 > 50 > 200) → strong uptrend ("rainbow" alignment)
- All EMAs aligned (9 < 20 < 50 < 200) → strong downtrend

### EMA as Dynamic Support/Resistance
- **Rule:** In uptrend, price pulls back to 20/50 EMA = buy opportunity.
- **Rule:** In downtrend, price rallies to 20/50 EMA = sell/short opportunity.
- **Bounce confirmation:** Price touches EMA + bullish candle pattern forms at EMA level → entry.
- **Break:** Close below 20 EMA in uptrend = caution; close below 50 EMA = trend change signal.

### EMA Ribbon
Using multiple EMAs (5, 8, 13, 21, 34, 55, 89) together:
- Ribbons fan out (spread) → strong trend
- Ribbons compress (converge) → trend weakening or reversal coming
- Ribbons flip (fast EMAs cross slow EMAs) → trend change

---

## 3.5 VWAP — Volume Weighted Average Price

### Calculation
```
VWAP = Σ(Typical Price × Volume) / Σ(Volume)
Typical Price = (High + Low + Close) / 3
Resets each trading day
```

### VWAP Significance
- **Institutional benchmark:** Large institutions use VWAP for "fair price" in execution algorithms. Price above VWAP = trading expensive vs average; below = trading cheap.
- **Intraday use only:** VWAP resets daily; not meaningful on daily/weekly charts.
- **Anchored VWAP (AVWAP):** VWAP anchored from a significant event (earnings, IPO, major low/high). Useful on any timeframe.

### VWAP Trading Rules

#### Long Setup
- Price pulls back TO VWAP from above + bullish candle at VWAP + volume increases = BUY
- Institutional support at VWAP

#### Short Setup
- Price rallies TO VWAP from below + bearish rejection at VWAP = SELL
- VWAP acting as resistance

#### Trend Confirmation
- Price consistently ABOVE VWAP = bullish intraday bias; favor long trades
- Price consistently BELOW VWAP = bearish intraday bias; favor short trades
- Price oscillating around VWAP = ranging/indecisive; avoid trend trades

#### VWAP Standard Deviations (VWAP Bands)
- +1 SD and +2 SD above VWAP = resistance / overbought zones
- −1 SD and −2 SD below VWAP = support / oversold zones
- Mean reversion: Price at ±2 SD → expect reversion toward VWAP

#### Entry/Exit Rules
- Buy entry: Price at −1 SD VWAP or touching VWAP from above
- Scale out / take profit: At +1 SD or +2 SD VWAP
- Stop: Below −1 SD VWAP for long entry

---

## 3.6 ATR — Average True Range

### Calculation
```
True Range (TR) = Max of:
  a) Current High − Current Low
  b) |Current High − Previous Close|
  c) |Current Low − Previous Close|
ATR = 14-period Wilder's Smoothed Moving Average of TR
Standard period: 14
```

### ATR Uses

#### 1. Volatility Measurement
- High ATR = high volatility; Low ATR = low volatility
- ATR rising = expanding volatility (trend acceleration or reversal)
- ATR falling = contracting volatility (consolidation)

#### 2. Stop Loss Placement (ATR-based)
```
Stop Loss = Entry Price − (ATR × Multiplier)  [for long]
Stop Loss = Entry Price + (ATR × Multiplier)  [for short]

Multiplier by style:
  - Tight stop: 1.0× ATR
  - Standard: 1.5–2.0× ATR
  - Wider: 2.5–3.0× ATR
```

#### 3. Position Sizing with ATR
```
Risk per trade = Account Size × Risk % (e.g., 1%)
Position Size = Risk per trade / (ATR × Multiplier)
```
Example: ₹5,00,000 account × 1% risk = ₹5,000 risk
If ATR = ₹50, multiplier = 2.0 → stop = ₹100
Position size = ₹5,000 / ₹100 = 50 shares

#### 4. Chandelier Exit (Trailing Stop)
```
Chandelier Exit (Long) = Highest High (since entry) − ATR × 3
Chandelier Exit (Short) = Lowest Low (since entry) + ATR × 3
```
Dynamically adjusts stop as trade moves favorably.

#### 5. ATR Breakout (Volatility Breakout)
- Entry if today's price move > Previous Day's Close ± (ATR × 1.5)
- Combines with range contraction: Enter on first day where ATR expands after multiple low-ATR days

---

## 3.7 Stochastic Oscillator

### Calculation
```
%K = ((Close − Lowest Low_N) / (Highest High_N − Lowest Low_N)) × 100
%D = 3-period SMA of %K
Standard settings: (14, 3, 3)
Fast Stochastic: raw %K
Slow Stochastic: smoothed %K (= previous Fast %D)
```

### Stochastic Levels
| Level | Interpretation |
|-------|----------------|
| > 80 | Overbought |
| < 20 | Oversold |
| 50 | Midline (trend filter) |

### Stochastic Signals

#### Crossover Signals
- **Bullish:** %K crosses ABOVE %D in oversold territory (below 20) → BUY
- **Bearish:** %K crosses BELOW %D in overbought territory (above 80) → SELL
- Best when crossover happens at extremes, not in mid-range.

#### Divergence
- **Bullish Divergence:** Price lower low; Stochastic higher low → reversal up.
- **Bearish Divergence:** Price higher high; Stochastic lower high → reversal down.

#### Failure Setup
- Stochastic exits overbought/oversold zone (crosses back through 80/20) = confirmation of reversal.

### Stochastic Settings
| Use | Settings |
|-----|----------|
| Short-term/Intraday | (5, 3, 3) |
| Standard Swing | (14, 3, 3) |
| Slow/Position | (21, 5, 5) |

---

## 3.8 CCI — Commodity Channel Index

### Calculation
```
CCI = (Typical Price − 20-period SMA of TP) / (0.015 × Mean Deviation)
Typical Price = (H + L + C) / 3
```

### CCI Levels
| Level | Interpretation |
|-------|----------------|
| > +100 | Overbought / entering strong uptrend |
| < −100 | Oversold / entering strong downtrend |
| +100 to +200 | Strong bullish momentum |
| −100 to −200 | Strong bearish momentum |
| 0 | Equilibrium |

### CCI Signals
- **Entry Long:** CCI crosses ABOVE +100 from below → trend beginning (not reversal)
- **Exit Long:** CCI crosses BACK BELOW +100 from above
- **Entry Short:** CCI crosses BELOW −100 from above
- **Divergence:** Same rules as RSI/MACD divergence.
- **Zero-line cross:** CCI crosses above 0 = bullish bias; below 0 = bearish bias.

### CCI Speciality
CCI works well for identifying cyclical turns in commodities, currencies, and stocks.
Period 20 works well for daily charts. Period 14 for shorter timeframes.

---

## 3.9 OBV — On-Balance Volume

### Calculation
```
If Close > Previous Close: OBV = Previous OBV + Current Volume
If Close < Previous Close: OBV = Previous OBV − Current Volume
If Close = Previous Close: OBV = Previous OBV
```

### OBV Signals
- **Rising OBV + Rising Price:** Volume confirming uptrend (strong)
- **Falling OBV + Falling Price:** Volume confirming downtrend (strong)
- **Rising OBV + Flat/Falling Price:** BULLISH DIVERGENCE — accumulation occurring; price likely to follow
- **Falling OBV + Flat/Rising Price:** BEARISH DIVERGENCE — distribution occurring; price likely to fall
- **OBV New High:** Confirms price new high (strong trend)
- **OBV Fails to Make New High:** Price making new highs without volume = WEAKNESS

### OBV as Trend Indicator
- OBV trending up = smart money accumulating
- OBV trending down = smart money distributing
- OBV breakout above its own resistance often precedes price breakout

---

## 3.10 MFI — Money Flow Index

### Calculation
```
Typical Price (TP) = (H + L + C) / 3
Money Flow (MF) = TP × Volume
Positive MF: Days where TP > Previous TP
Negative MF: Days where TP < Previous TP
Money Ratio = 14-day Positive MF / 14-day Negative MF
MFI = 100 − (100 / (1 + Money Ratio))
```

### MFI = Volume-Weighted RSI
- MFI > 80 = Overbought
- MFI < 20 = Oversold
- Divergence signals same as RSI

### MFI Signals
- **MFI oversold (< 20) + Price at support** → Strong buy setup
- **MFI overbought (> 80) + Price at resistance** → Strong sell setup
- **Failure Swing:** Same as RSI Failure Swing concept
- **Combined with %B:** Powerful combination (described in Bollinger Bands section)

---

# 4. MULTI-INDICATOR CONFLUENCE SCORING

## 4.1 Confluence Framework

Confluence = multiple independent signals pointing to the same trade direction simultaneously. More confluence = higher probability.

**Scoring System (assign points):**

| Signal | Points |
|--------|--------|
| Price at key support/resistance | +2 |
| Trend alignment (price above 50 EMA in uptrend) | +1 |
| RSI oversold (<30) for long | +2 |
| RSI bullish divergence | +2 |
| MACD bullish crossover | +1 |
| MACD histogram turning positive | +1 |
| BB lower band touch + price inside band | +1 |
| BB squeeze breakout in trade direction | +2 |
| Bullish candlestick pattern at level | +1 to +2 |
| Volume spike confirming direction | +2 |
| VWAP support/resistance aligning | +1 |
| OBV divergence confirming | +1 |
| Stochastic oversold + crossover | +1 |
| Higher timeframe trend aligned | +2 |

**Scoring Thresholds:**
- Score < 4: Avoid trade
- Score 4–5: Low confidence, small position only
- Score 6–7: Medium confidence, standard position
- Score 8–9: High confidence, full position
- Score 10+: Maximum confidence setup, scale in aggressively

---

## 4.2 High-Probability Setup Combinations

### Setup 1: Classic Oversold Reversal (Long)
**Conditions:**
1. Daily downtrend has been in place 10+ bars
2. Price reaches major support level (prior swing low, key Fibonacci level)
3. RSI < 30 (oversold)
4. MACD histogram showing bullish divergence (higher lows while price lower lows)
5. Bullish candlestick pattern (Hammer, Morning Star, Engulfing) forms at support
6. Volume spike on the reversal candle
7. Next candle confirms bullish (strong green close)

**Reliability:** 75–80% win rate in backtests
**Entry:** Break above reversal candle's high OR open of next confirmed bullish candle
**Stop:** Below reversal candle's low or −2 ATR from entry
**Target:** Next resistance level or 2:1 R:R minimum

---

### Setup 2: Trend Continuation Pullback (Long)
**Conditions:**
1. Strong uptrend confirmed (price above 20, 50, 200 EMA — all rising)
2. Price pulls back to 20 EMA or 50 EMA
3. RSI pulls back to 40–50 zone (not oversold — just a pullback)
4. MACD above zero, histogram pulling back but not flipping negative
5. OBV still rising (accumulation continues)
6. Bullish candle pattern at EMA + on above-average volume
7. No bearish higher timeframe signal

**Entry:** Close of bullish reversal candle at EMA support
**Stop:** −1.5 ATR below entry or below EMA
**Target:** Prior high or 2–3× ATR above entry

---

### Setup 3: VWAP + RSI Intraday Long
**Conditions:**
1. Overall market bullish (Nifty/SPX positive on the day)
2. Stock price dips to/below VWAP
3. RSI on 15-min chart < 40 (pullback oversold intraday)
4. MACD still positive / histogram just slightly negative
5. Price shows reversal candle at VWAP (hammer/engulfing)
6. Volume increases on bounce candle

**Entry:** Break above high of reversal candle
**Stop:** −0.5% or below VWAP −1 SD
**Target:** VWAP +1 SD or day's high

---

### Setup 4: Bollinger Squeeze Breakout
**Conditions:**
1. BB Width at 6-month low (maximum squeeze)
2. Price consolidating in tight range
3. Volume declining during squeeze (confirming coil)
4. Clear direction trigger: first expanding candle with volume
5. OBV breaks above its own consolidation range
6. Entry on close of breakout candle beyond band

**Entry:** Close of breakout candle
**Stop:** Back inside squeeze range (opposite band)
**Target:** 1.5–2× the squeeze height

---

### Setup 5: Double Bottom + RSI Divergence (Most Reliable)
**Conditions:**
1. Clear double bottom / W formation on price
2. RSI at second bottom is HIGHER than at first bottom (classic divergence)
3. MACD at second trough is higher than at first (confirmation)
4. Volume at second trough LOWER than first (less selling pressure)
5. Bullish candle at second trough
6. Break above neckline of double bottom

**Entry:** Neckline break with volume, or pullback to neckline
**Stop:** Below second trough
**Target:** Neckline + height of pattern

---

### Setup 6: Breakout + Retest (Safest Entry)
**Conditions:**
1. Clear resistance level tested 3+ times
2. Breakout close above resistance on 2× average volume
3. Price retests breakout level from above (former resistance = new support)
4. RSI not extreme overbought at retest
5. MACD above zero and rising
6. Bullish candlestick at retest level

**Entry:** Bounce from retest level
**Stop:** Below retest candle's low
**Target:** Previous pattern target

---

## 4.3 Indicator Combination Quick Reference

| Market Condition | Best Indicators | Avoid |
|-----------------|-----------------|-------|
| Trending (Strong Uptrend) | EMA, MACD, OBV, ATR Trailing Stop | RSI overbought signals, BB mean reversion |
| Trending (Strong Downtrend) | EMA, MACD, OBV, ATR Trailing Stop | RSI oversold signals |
| Ranging / Sideways | RSI, Stochastic, BB mean reversion, CCI | MACD crossovers (too many false signals) |
| High Volatility | ATR-based stops, BB width, ADX | Tight stops (get stopped out randomly) |
| Low Volatility (Squeeze) | BB Squeeze, ATR breakout | Momentum indicators (no momentum yet) |

---

# 5. VOLUME ANALYSIS

## 5.1 Core Volume Principles

1. **Volume precedes price:** Rising OBV while price flat = accumulation = bullish.
2. **Volume confirms breakouts:** Breakout on low volume = false breakout risk. Breakout on 2× average volume = high-probability.
3. **Volume and trend:** Rising price + rising volume = healthy trend. Rising price + falling volume = weakening trend (divergence = bearish).
4. **Volume and reversals:** Climactic volume at highs = selling exhaustion/distribution. Climactic volume at lows = buying capitulation/accumulation.

---

## 5.2 Volume Confirmation Rules

### Bullish Volume Signals
| Signal | Description |
|--------|-------------|
| Volume expansion on up days | More buyers than sellers; trend healthy |
| Low volume on pullbacks | Weak selling pressure; dip buyers ready |
| Volume spike at bottom + reversal candle | Capitulation / selling exhaustion |
| OBV breakout before price | Smart money accumulating ahead |
| Increasing volume on base breakout | Institutional buying |

### Bearish Volume Signals
| Signal | Description |
|--------|-------------|
| Volume expansion on down days | More sellers; distribution phase |
| Low volume on rallies | Weak buying; supply overhangs |
| Volume spike at top + bearish candle | Distribution / buying exhaustion |
| OBV breakdown before price | Smart money distributing ahead |
| High volume at highs with no progress | Churning = distribution |

---

## 5.3 Climactic Volume

**Definition:** Volume 2–5× the average (50-day average volume) accompanied by a wide-range candle.

### Climactic Top (Distribution Climax)
- After extended uptrend
- Extremely high volume (5× or more average)
- Wide-range bearish or reversal candle
- Price closes off the high
- Next day: Gap up then fade (exhaustion gap) OR gap down
- **Signal:** MAJOR TOP — institutional selling complete; smart money has exited.

### Climactic Bottom (Selling Climax)
- After extended downtrend
- Extremely high volume
- Wide-range candle, closes off the low (Hammer-like)
- Next day: Rally on high volume (follow-through)
- **Signal:** MAJOR BOTTOM — panic selling exhausted; smart money accumulating.

### Follow-Through Day (FTD) — IBD concept
- After climactic bottom, wait for a "follow-through day": 
  - 4+ days after the initial bounce attempt
  - A major index (Nifty/SPX) closes UP >1.5% on volume HIGHER than the prior day
  - Confirms the bottom is in; safe to buy setups

---

## 5.4 Accumulation vs Distribution (Wyckoff)

### Wyckoff Accumulation Phases
| Phase | Description |
|-------|-------------|
| A: Stopping the Downtrend | Selling climax (SC), Automatic Rally (AR), Secondary Test (ST) |
| B: Building the Cause | Back-and-forth consolidation; smart money accumulating on dips |
| C: Spring / Shakeout | False breakdown below support to shake out weak hands; LOW volume on break |
| D: Mark Up Begins | Price breaks above resistance (Sign of Strength / SOS); HIGH volume |
| E: Mark Up | Sustained uptrend; backfills are on low volume |

### Wyckoff Distribution Phases
| Phase | Description |
|-------|-------------|
| A: Stopping the Uptrend | Buying climax (BC), Automatic Reaction (AR), Secondary Test (ST) |
| B: Building the Cause | Back-and-forth consolidation near highs; smart money distributing on rallies |
| C: Upthrust After Distribution (UTAD) | False breakout above resistance; HIGH volume rejection |
| D: Mark Down Begins | Price breaks below support (Sign of Weakness / SOW) |
| E: Mark Down | Sustained downtrend |

### Volume Divergence
- **Bullish Volume Divergence:** Price making new lows while volume is DECLINING → sellers losing strength → potential reversal.
- **Bearish Volume Divergence:** Price making new highs while volume is DECLINING → buyers losing conviction → potential reversal.

---

## 5.5 Delivery Percentage (India-Specific)

For NSE stocks: Delivery % = (Delivery Volume / Total Volume) × 100

| Delivery % | Interpretation |
|------------|----------------|
| > 60–70% | Strong investment buying; not speculative; sustainable move |
| 40–60% | Mixed; normal range |
| < 30% | Highly speculative / intraday trading; move less sustainable |
| Sudden spike in delivery % | Institutions/HNIs taking positions |
| High delivery on breakout | Very bullish confirmation |

---

# 6. MARKET STRUCTURE & SMART MONEY CONCEPTS

## 6.1 Traditional Market Structure

### Higher Highs and Higher Lows (HH/HL) — Uptrend
- Each swing high exceeds the previous swing high
- Each swing low is higher than the previous swing low
- Price above major EMAs
- **Trading Rule:** BUY the Higher Low (HL) pullback; stop below the HL.

### Lower Highs and Lower Lows (LH/LL) — Downtrend
- Each swing high is lower than the previous
- Each swing low is lower than the previous
- **Trading Rule:** SELL/SHORT the Lower High (LH) rally; stop above the LH.

### Identifying Swing Points
- Swing High: Candle high surrounded by 2+ lower highs on each side
- Swing Low: Candle low surrounded by 2+ higher lows on each side
- Use ZigZag indicator or manual identification

---

## 6.2 Break of Structure (BOS)

**Definition:** When price breaks and closes beyond a significant swing high (in downtrend) or swing low (in uptrend), indicating a potential change in structure.

### Bullish BOS
- In a downtrend, price breaks ABOVE the most recent swing high (LH)
- Signals: downtrend structure broken; potential reversal to uptrend
- Confirmation: Subsequent Higher Low forms

### Bearish BOS
- In an uptrend, price breaks BELOW the most recent swing low (HL)
- Signals: uptrend structure broken; potential reversal to downtrend
- Confirmation: Subsequent Lower High forms

**Trading BOS:**
- Entry: On retest of the broken level (former resistance → support for bullish BOS)
- Stop: Below the swing low that preceded the BOS
- Target: Next major swing high or structure level

---

## 6.3 Change of Character (CHoCH)

**Definition:** The FIRST break of structure in the OPPOSITE direction of the prevailing trend. More significant than a regular BOS.

- **Bullish CHoCH:** During a downtrend, first time price takes out a swing high (LH) → character of price action changing from bearish to potentially bullish.
- **Bearish CHoCH:** During an uptrend, first time price takes out a swing low (HL) → character changing from bullish to potentially bearish.

**CHoCH vs BOS:**
- CHoCH = first sign of reversal (higher risk, earlier entry)
- BOS = confirmation of new trend (lower risk, later entry)
- CHoCH is the "warning"; BOS is the "confirmation"

---

## 6.4 Liquidity Concepts

### Liquidity Pools
**Definition:** Areas where many stop-loss orders are clustered, creating "pools" of resting orders that smart money targets.

**Common Liquidity Pool Locations:**
- Equal highs / lows (price has tested same level 2+ times without breaking)
- Swing highs and lows (retail traders place stops just beyond these)
- Round numbers (e.g., ₹1000, $100, $50)
- Moving average levels
- Previous day/week/month high and low

### Liquidity Sweep (Stop Hunt)
- Price briefly pierces beyond a swing high/low to COLLECT liquidity (trigger stops)
- Then REVERSES sharply in the opposite direction
- This is smart money executing large orders against retail stop-losses

**Identifying a Liquidity Sweep:**
1. Price spikes beyond a clear swing high/low (takes out the level)
2. Long wick forms (Shooting Star, Hammer, Pin Bar)
3. Price closes BACK beyond the swept level
4. Volume spike on the sweep candle
5. Reversal candle on the next bar

**Trading Liquidity Sweeps:**
- Wait for sweep to complete (candle closes back inside range)
- Enter in the REVERSAL direction
- Stop: Beyond the sweep wick
- Target: Opposite liquidity pool

---

## 6.5 Fair Value Gaps (FVG) / Imbalance

**Definition:** A three-candle pattern where the wicks of candle 1 and candle 3 do NOT overlap, leaving a price gap (imbalance/void) in the market.

### Bullish FVG (Demand Zone)
- Candle 1 (bearish), Candle 2 (large bullish impulse), Candle 3 (bullish)
- Gap: High of Candle 1's wick BELOW Low of Candle 3's wick
- Price moves up impulsively without returning to fill the gap → gap = demand zone
- **Entry:** When price returns to the FVG zone → BUY at bottom of gap
- **Stop:** Below the bottom of the FVG
- **Target:** Next resistance / supply zone

### Bearish FVG (Supply Zone)
- Three-candle pattern with large bearish impulse candle in middle
- Gap between wicks of candle 1 and candle 3 on the downside
- **Entry:** When price returns to fill the FVG zone → SELL at top of gap
- **Stop:** Above top of FVG
- **Target:** Next support / demand zone

### FVG Rules
- Not all FVGs are filled; strong impulse moves may never fill
- FVG + liquidity sweep reversal = very high probability setup
- FVG on higher timeframe (4H, Daily) > lower timeframe FVG
- "Consequent Encroachment" (CE) = 50% of FVG; often acts as precise entry level

---

## 6.6 Order Blocks (OB)

**Definition:** The last candle (or group of candles) before a significant impulse move that "caused" the move. Represents the candle where institutions placed their large orders.

### Bullish Order Block
- Last BEARISH candle before a significant bullish impulse move
- The body of this bearish candle = Bullish Order Block zone
- Price often returns to this zone for re-entry by institutions
- **Entry:** Price returns to bearish candle's body zone → BUY
- **Stop:** Below the low of the order block candle
- **Confirmation:** Reaction (bullish candle) on return to OB

### Bearish Order Block
- Last BULLISH candle before a significant bearish impulse move
- The body of this bullish candle = Bearish Order Block zone
- **Entry:** Price returns to bullish candle's body zone → SELL
- **Stop:** Above the high of the OB

### Order Block Quality Filters
- Higher timeframe OB > lower timeframe OB
- OB that caused BOS/CHoCH = stronger
- OB with FVG inside or beside it = "OB+FVG combo" = highest probability
- OB tested only once (fresh) > OB tested multiple times (used up)

---

## 6.7 Premium and Discount Zones

**Concept:** Smart money buys in discount zones and sells in premium zones.

```
Range = Swing High − Swing Low
Midpoint = (Swing High + Swing Low) / 2 (= 50% Fibonacci level)

Premium Zone: 50%–100% of range (above midpoint)
Discount Zone: 0%–50% of range (below midpoint)
```

**Rule:**
- Look for LONG setups only in DISCOUNT zones (buy low within a range)
- Look for SHORT setups only in PREMIUM zones (sell high within a range)
- Combine with OB, FVG for entry precision

---

## 6.8 SMC Full Trade Setup

**Bearish Example:**
1. Identify downtrend on higher timeframe (LH/LL structure)
2. Price pulls back into a PREMIUM zone (above 50% of last range)
3. Bearish Order Block identified at the premium zone
4. Bearish FVG inside the OB (supply cluster)
5. Liquidity sweep of recent swing high (to grab buy-stops)
6. Bearish CHoCH/BOS on lower timeframe within OB
7. Entry on short at OB midpoint or FVG entry
8. Stop above OB high + buffer
9. Target: Next demand zone / previous swing low (2:1 R:R minimum)

---

# 7. ENTRY / EXIT RULES

## 7.1 Entry Rules

### Price Action Entry Criteria
1. **Trend alignment:** Trade in direction of higher timeframe trend (HTF trend filter).
2. **Level identification:** Price at key S/R, EMA, VWAP, OB, FVG, Fibonacci level.
3. **Candlestick confirmation:** Reversal/continuation candle pattern at the level.
4. **Indicator confirmation:** At least 2 indicators confirm the direction.
5. **Volume confirmation:** Volume supports the move.
6. **Entry trigger:** Specific rule for exact entry (break of candle high/low, close above/below level).

### Entry Types

#### 1. Aggressive Entry (Higher Risk, Better R:R)
- Enter AT the pattern/level as it forms
- Example: Buy at Hammer low with stop below wick
- R:R: Better (tighter stop, but less confirmation)

#### 2. Conservative Entry (Lower Risk, Lower R:R)
- Wait for confirmation candle AFTER pattern
- Example: Buy ABOVE the high of the Hammer's close candle
- R:R: Worse (wider stop from actual level), but higher win rate

#### 3. Breakout Entry
- Enter on close ABOVE/BELOW a key level
- Confirmation: Close above/below, not just wick
- Volume must confirm

#### 4. Pullback/Retest Entry
- Wait for price to break level, then RETEST it
- Safer entry with level confirmed as S/R flip
- Reduces risk of false breakout

---

## 7.2 Stop Loss Placement

### ATR-Based Stops (Recommended for mechanical systems)
```
Long Stop = Entry − (ATR × N)
Short Stop = Entry + (ATR × N)
  N = 1.5 for tight (scalp/intraday)
  N = 2.0 for standard (swing)
  N = 2.5–3.0 for wide (position)
```

### Structure-Based Stops
- Long: Stop just BELOW the swing low that precedes entry (below the last HL)
- Short: Stop just ABOVE the swing high that precedes entry (above the last LH)
- Buffer: 0.1–0.5% beyond the structure level (or 0.25–0.5× ATR buffer)
- Rule: Stop must be beyond a level where the trade thesis is WRONG

### Pattern-Based Stops
| Pattern | Stop Location |
|---------|---------------|
| Hammer | Below Hammer's shadow low |
| Shooting Star | Above Shooting Star's shadow high |
| Engulfing (Bullish) | Below engulfing candle's low |
| H&S Neckline Break | Above right shoulder high |
| Double Bottom | Below second trough |
| Flag Breakout | Back inside the flag channel |
| Order Block | Beyond OB boundary (low for bullish OB) |
| FVG | Below bottom of bullish FVG |

### Stop Loss Rules
1. NEVER move stop AGAINST the trade (widen stop to avoid being hit).
2. Move stop to breakeven when trade moves 1:1 R:R (risk-free trade).
3. Trail stop using: Chandelier Exit, ATR trail, or behind swing lows/highs.
4. Maximum loss per day: 2–3% of account (circuit breaker).
5. Maximum loss per trade: 1–2% of account.

---

## 7.3 Take Profit / Target Rules

### R:R Ratio Minimums
| Strategy | Minimum R:R |
|----------|-------------|
| Scalping | 1.5:1 |
| Intraday | 2:1 |
| Swing | 2:1 to 3:1 |
| Position | 3:1 to 5:1 |

### Target Methods

#### 1. Fixed R:R
- If stop = 1% risk, take profit at 2% or 3% (2:1 or 3:1)
- Simple, consistent, easy to automate

#### 2. Next Structure Level
- Target = next swing high (for longs) or swing low (for shorts)
- Based on actual market structure, not arbitrary multiples

#### 3. Fibonacci Extensions
```
Common Extension Levels:
  1.272 extension = first target (conservative)
  1.618 extension = main target (Fibonacci golden ratio)
  2.0 extension = extended target
  2.618 extension = aggressive target

Calculation (Bullish):
  Swing Low → Swing High → Retracement Low
  Extension from Retracement Low:
    1.272: Retracement Low + (High − Low) × 1.272
    1.618: Retracement Low + (High − Low) × 1.618
```

#### 4. Pattern Targets
- H&S: Neckline − Head-to-Neckline distance
- Double Top: Breakdown − Pattern height
- Triangle: Breakout ± Triangle height (at widest)
- Flag: Breakout + Flagpole length
- Cup & Handle: Breakout + Cup depth

#### 5. Partial Profit Taking
- Close 50% at 1:1 R:R (lock in profit, reduce risk)
- Close remaining at 2:1 or 3:1 R:R
- Trail stop on remaining position

---

## 7.4 Fibonacci Retracement Levels

```
Key Fibonacci Retracement Levels:
  23.6% — Shallow pullback; strong trend
  38.2% — Common pullback in strong trend
  50.0% — Midpoint (not Fibonacci but widely watched)
  61.8% — "Golden ratio" retracement; most common entry level
  78.6% — Deep retracement; last line before trend fails
  88.6% — Very deep; failure/reversal zone

How to draw:
  Uptrend: Drag from swing low to swing high
  Downtrend: Drag from swing high to swing low
```

**Entry Rule:** Price pulls back to 38.2%–61.8% zone + reversal candle + indicator confirmation → enter in trend direction.

**Fibonacci + Confluence:** Fib level aligning with EMA, S/R zone, OB, or FVG = very high probability entry.

---

# 8. INDIAN MARKET SPECIFICS

## 8.1 NSE/BSE Market Structure

### Key Indices
| Index | Description | Significance |
|-------|-------------|--------------|
| Nifty 50 | Top 50 stocks by free-float market cap on NSE | Benchmark for all analysis; market mood indicator |
| Sensex (BSE 30) | Top 30 stocks on BSE | Alternative benchmark; closely correlated with Nifty |
| Nifty Bank | Top 12 banking stocks | Banking sector health; highly traded |
| Nifty IT | IT sector index | Tracks tech stocks; USD-INR sensitive |
| Nifty Midcap 100 | Mid-cap stocks | Risk appetite indicator |
| Nifty Smallcap 100 | Small-cap stocks | Extreme risk-on/off indicator |
| India VIX | Volatility index (NSE) | Fear/uncertainty gauge for Indian market |

### Market Hours (IST)
| Session | Time |
|---------|------|
| Pre-market (call auction) | 9:00 AM – 9:15 AM |
| Regular trading | 9:15 AM – 3:30 PM |
| Post-market (closing call auction) | 3:40 PM – 4:00 PM |
| Currency (USDINR) derivatives | 9:00 AM – 5:00 PM |
| Commodity (MCX) | 9:00 AM – 11:30 PM |

### Market Cycles
- Indian market strongly correlated with US markets (S&P 500, NASDAQ)
- Morning (9:15–10:30): Gap fill moves; highest volatility of day
- Mid-day (11:00–1:30): Trend continuation or consolidation; lower volume
- Afternoon (2:00–3:30): Institutional activity increases; often direction of close set

---

## 8.2 Nifty 50 and Individual Stocks

### Nifty-to-Stock Relationship
- **Beta:** Measure of stock's sensitivity to Nifty.
  - Beta = 1.2: Stock moves 1.2× Nifty. Nifty up 1% → stock up ~1.2%.
  - Beta < 1: Defensive stock (FMCG, pharma).
  - Beta > 1.5: Aggressive/high-risk stock.
- **High-Beta Stocks:** Useful in trending Nifty; avoid in flat/sideways Nifty.
- **Rule:** Never fight the Nifty. If Nifty is in strong downtrend, avoid long positions even in strong stocks.

### Nifty Technical Levels (Method)
- Nifty 50 chart analyzed identically to individual stocks
- Support/Resistance levels at: round numbers (22,000; 23,000; 24,000), prior swing highs/lows, Fib levels
- Nifty above 200 DMA → bullish macro; below → bearish macro
- Nifty above 50 DMA → intermediate bullish; pullback to 50 DMA = buying opportunity

### Correlation Matrix (General)
| Sector | Nifty Correlation | Notes |
|--------|------------------|-------|
| Banking (BankNifty) | Very High (0.90+) | BankNifty leads/lags Nifty |
| IT | Moderate-High | Driven by USDINR and US tech |
| FMCG | Low-Moderate | Defensive; outperforms in bear |
| Auto | High | Cyclical; GDP sensitive |
| Pharma | Low | Defensive; counter-cyclical |
| Metals | Moderate | Global commodity driven |
| Realty | Moderate-High | High beta; rate sensitive |

---

## 8.3 FII / DII Data Interpretation

### Definitions
- **FII (Foreign Institutional Investors):** Foreign funds (hedge funds, mutual funds, pension funds) investing in India. Primary market movers for large-cap stocks.
- **DII (Domestic Institutional Investors):** Indian MFs, insurance companies, banks. Tend to buy on FII selling (contrarian).
- **Provisional Data:** Published EOD on NSE website.

### FII/DII Signals

#### FII Net Buying (Positive)
- FII buys > FII sells → net inflow → bullish for market
- Sustained FII buying over 5–10 days = strong bullish signal for Nifty
- FII buying + rising Nifty = healthy trend

#### FII Net Selling (Negative)
- FII sells > FII buys → net outflow → bearish for market
- Sustained selling = potential market correction
- FII selling + DII buying (SIP flows) = support but not reversal

#### DII Flows
- DII tends to buy when FIIs sell (SIP inflows are consistent)
- Heavy DII buying = support but market may not rally if FIIs continue selling
- Both FII and DII buying = strongest bullish signal

#### Interpretation Rules
| FII | DII | Signal |
|-----|-----|--------|
| Buying | Buying | VERY BULLISH |
| Buying | Selling | Bullish (FII dominant) |
| Selling | Buying | Neutral to slightly bearish (support from DII) |
| Selling | Selling | VERY BEARISH |

### SEBI Data Sources
- NSE website: FII/DII data (daily, provisional)
- SEBI FII tracker: Weekly data
- Futures/Options: FII derivative positioning (long/short ratio)

---

## 8.4 Open Interest (OI) Analysis

### Basics
- **Open Interest (OI):** Total number of outstanding futures/options contracts that have not been settled.
- Rising OI + Rising Price = New longs being added → Bullish trend healthy
- Rising OI + Falling Price = New shorts being added → Bearish trend healthy
- Falling OI + Rising Price = Short covering rally (not new buying) → Less sustainable
- Falling OI + Falling Price = Long unwinding → Bearish but weakening

### OI Interpretation Table
| Price | OI | Interpretation |
|-------|----|----------------|
| ↑ | ↑ | Long buildup — Bullish |
| ↑ | ↓ | Short covering — Weakly Bullish |
| ↓ | ↑ | Short buildup — Bearish |
| ↓ | ↓ | Long unwinding — Weakly Bearish |

### Options OI (Max Pain)

**Max Pain Theory:** Option writers (sellers) are more powerful than buyers in Indian markets (because of high theta decay). Price tends to gravitate toward the strike price with maximum OI (Maximum Pain Strike) on expiry.

**Max Pain Calculation:**
```
For each strike price, calculate total monetary loss for option buyers if expiry is at that strike.
The strike where total loss is MAXIMUM = Max Pain level.
```

**Trading Rule:** In the last week before expiry, if current price is far from max pain, it tends to gravitate toward it.

### OI-Based Support/Resistance
- **High Put OI at a strike:** That strike is SUPPORT (put sellers will defend it)
- **High Call OI at a strike:** That strike is RESISTANCE (call sellers will defend it)
- **PCR (Put-Call Ratio):** Total Put OI / Total Call OI
  - PCR > 1.2 → Bullish (more puts = hedging; contrarian signal)
  - PCR < 0.8 → Bearish (more calls = greed; contrarian signal)
  - PCR 0.8–1.2 → Neutral

### Nifty Weekly Options OI (Most Important)
- Most actively traded options in the world by volume
- Expiry: Every Thursday
- Watch: OI buildup in Calls (resistance) vs Puts (support)
- "OI Wall" at a level = price barrier; break with high volume = trend continuation

---

## 8.5 India VIX

**VIX Formula:** Based on weighted average of implied volatilities of Nifty options across strikes.

| VIX Level | Market Condition |
|-----------|-----------------|
| < 12 | Very low fear; complacency; potential for surprise drop |
| 12–15 | Normal, low volatility; benign conditions |
| 15–20 | Mild concern; normal volatility |
| 20–25 | Elevated fear; volatile market |
| 25–30 | High fear; market stress |
| > 30 | Extreme fear/panic; crash territory |

**Trading Rules with VIX:**
- VIX spike + Nifty drop → Look for buying opportunity (fear extreme)
- VIX declining + Nifty rising → Healthy bull trend
- VIX low for extended period + complacency → Risk of sudden spike; reduce longs
- VIX > 25 → Widen stops significantly (higher volatility = larger random moves)

---

## 8.6 Sector Rotation in Indian Markets

**Typical Cycle:**
1. Early Bull Market: Banking, Infrastructure, Auto (rate-sensitive, capex)
2. Mid Bull Market: IT, FMCG, Healthcare (quality/growth)
3. Late Bull Market: Real Estate, Capital Goods, Metals (late cyclicals)
4. Bear Market: FMCG, Pharma, IT (defensive)

**Key FII Flow Sectors:**
- FIIs favor: Banking, IT, FMCG (highest weightage in indices)
- FII buying in banking = BankNifty rally = Nifty rally

---

## 8.7 Currency Impact (USDINR)

- **Rupee depreciation (USDINR up):** Negative for importers (IT companies' margins in INR actually improve; oil companies suffer).
- **USDINR rise > ₹86:** Risk-off environment; FIIs may reduce India exposure.
- **IT Sector:** Benefits from rupee depreciation (revenue in USD, costs in INR).
- **Oil/Auto/FMCG:** Hurt by rupee depreciation.

**Monitor:** USDINR daily candle; if sustained above key resistance → risk-off for Indian equities.

---

# 9. SENTIMENT INDICATORS

## 9.1 Fear & Greed Index (CNN) / Market Sentiment

**Components of CNN Fear & Greed Index:**
1. Stock Price Strength (52-week highs vs lows)
2. Market Momentum (S&P500 vs 125-day MA)
3. Stock Price Breadth (McClellan Volume Summation Index)
4. Put/Call Ratio
5. Market Volatility (VIX)
6. Junk Bond Demand (spread between junk and investment grade)
7. Safe Haven Demand (stock vs bond returns)

**Interpretation:**
| Score | Sentiment | Trading Implication |
|-------|-----------|---------------------|
| 0–25 | Extreme Fear | Potential buying opportunity (contrarian) |
| 25–45 | Fear | Cautious; look for bottoming signals |
| 45–55 | Neutral | Trend-following works best |
| 55–75 | Greed | Bullish trend; reduce new long entries at extremes |
| 75–100 | Extreme Greed | Consider taking profits; potential correction |

**Contrarian Rule:** Extreme Fear + Technical reversal = STRONG BUY. Extreme Greed + Technical top = STRONG SELL.

---

## 9.2 Put/Call Ratio (PCR)

### Total PCR (Index + Equity Options Combined)
```
PCR = Total Put Volume (or OI) / Total Call Volume (or OI)
```

| PCR | Signal |
|-----|--------|
| > 1.5 | Extreme fear / bearish bets; contrarian BULLISH |
| 1.2–1.5 | Elevated puts; mildly bullish contrarian |
| 0.9–1.2 | Neutral / balanced |
| 0.7–0.9 | More calls; mildly bearish contrarian |
| < 0.7 | Extreme call buying / greed; contrarian BEARISH |

**Key Insight for India:**
- Nifty Weekly PCR is most watched: Above 1.2 = market support; Below 0.7 = market fragile.
- FII options PCR (available from NSE): FIIs buying puts heavily = hedge/bearish bet.

---

## 9.3 VIX (Volatility Index)

**India VIX:** (Covered in Section 8.5 above)

**US VIX (CBOE VIX) — relevant for gap analysis:**
| VIX Level | US Market Condition | India Impact |
|-----------|--------------------|-----------   |
| < 15 | Calm; bull trend | India likely stable |
| 15–20 | Normal | Normal |
| 20–30 | Elevated concern | Indian markets may see selling |
| > 30 | Fear/crisis | India sell-off risk high; FII outflows likely |
| > 40 | Panic/crash | Major Indian correction likely |

**VIX + Price Divergence:**
- VIX falling + Market rising = Healthy bull trend (ideal)
- VIX rising + Market rising = Warning; complacency building
- VIX rising + Market falling = Fear increasing; look for capitulation
- VIX spike + sharp market drop → Wait 1–2 days for capitulation then BUY

---

## 9.4 Short Interest

**Definition:** Percentage of float sold short.

| Short Interest % | Signal |
|-----------------|--------|
| > 20% of float | High short interest; potential for SHORT SQUEEZE if news catalyst |
| 10–20% | Elevated; notable skepticism |
| < 5% | Low; market not expecting decline |

**Short Squeeze Setup:**
1. High short interest (> 15–20%)
2. Stock at strong technical support or forming base
3. Positive catalyst (earnings beat, buyback, upgrade)
4. Volume surges; price breaks above resistance
5. Shorts covering → explosive upside → BUY

**Days to Cover:**
```
Days to Cover = Shares Short / Average Daily Volume
```
Higher days to cover = more time for shorts to exit = longer squeeze potential.

---

## 9.5 Advance/Decline Line (Market Breadth)

**A/D Line:** Cumulative sum of (advancing stocks − declining stocks) each day.

| A/D Signal | Price Signal | Interpretation |
|------------|-------------|----------------|
| A/D rising | Price rising | Broad participation = HEALTHY BULL |
| A/D falling | Price rising | BEARISH DIVERGENCE — only large caps rising; correction coming |
| A/D rising | Price falling | BULLISH DIVERGENCE — broad base building; recovery likely |
| A/D falling | Price falling | Broad selling = HEALTHY BEAR (or no A/D divergence) |

**Indian Market Breadth:**
- Nifty 500 advance/decline ratio
- Nifty 50 advance/decline: > 35 stocks up = strong bullish day

---

## 9.6 Insider Buying/Selling (India: SEBI Disclosures)

- **Promoter buying (creeping acquisition):** Very bullish; insiders buying their own company.
- **Promoter selling / pledging:** Bearish signal.
- **Pledge percentage increasing:** Major RED FLAG — promoters in financial stress.
- **Institutional holdings increasing:** Bullish confirmation; smart money moving in.

**SEBI Rule:** Promoters must disclose acquisitions/disposals within 2 trading days.
Source: BSE filings, NSE corporate announcements.

---

# 10. SWING VS INTRADAY TRADING RULES

## 10.1 Swing Trading

### Definition
Holding trades from 2 days to several weeks (2–20 trading days). Captures medium-term moves using daily and weekly charts.

### Swing Trading Timeframe Hierarchy
```
Higher Timeframe (HTF): Weekly chart — Define major trend direction
Primary Timeframe (PTF): Daily chart — Identify setup and entry
Execution Timeframe (ETF): 4H chart — Precise entry timing
```

### Swing Trading Indicators (Most Effective)
| Indicator | Use |
|-----------|-----|
| EMA 20/50/200 | Trend direction; dynamic S/R |
| RSI (14) | Overbought/oversold; divergence |
| MACD (12,26,9) | Trend confirmation; crossover signals |
| Bollinger Bands (20,2) | Volatility; entry at band extremes |
| Volume (50-day MA comparison) | Confirmation of moves |
| ATR (14) | Stop loss sizing |
| OBV | Accumulation/distribution |
| Fibonacci Retracements | Entry levels on pullbacks |
| Weekly Pivot Points | Key S/R levels |

### Swing Trading Rules
1. Only trade in direction of weekly trend (higher timeframe filter).
2. Enter on daily chart pullbacks to 20 EMA or 50 EMA in uptrend.
3. Use RSI 40–50 pullbacks (not 30) in strong uptrends for entries.
4. Stop Loss: Below prior swing low + 0.5 ATR buffer.
5. Minimum R:R: 2:1. Do not enter if target not achievable at 2:1.
6. Hold through minor intraday volatility; only exit if daily closes below stop.
7. Check earnings dates — avoid holding through earnings unless hedged.
8. Maximum 5–6 open swing positions simultaneously.
9. Check FII/DII data, sector momentum, global cues before entry.

### Swing Trading Setups (Best)
1. Pullback to 50 EMA in uptrend + RSI 40–50 + bullish candle
2. Double Bottom + neckline break + RSI divergence
3. Ascending Triangle breakout + volume surge
4. Cup & Handle breakout (weekly chart)
5. Flag/Pennant breakout after 20%+ move

### Swing Trade Entry Checklist
- [ ] Weekly trend direction identified
- [ ] Setup visible on daily chart
- [ ] Price at key support/S/R level or EMA
- [ ] Candlestick confirmation pattern present
- [ ] RSI/MACD supporting signal
- [ ] Volume confirmation
- [ ] Stop loss < 3% from entry
- [ ] Target identified (2:1 R:R minimum)
- [ ] No earnings / major event in holding period
- [ ] Market (Nifty) not in aggressive downtrend

---

## 10.2 Intraday Trading

### Definition
Opening and closing all trades within the same trading session (9:15 AM – 3:20 PM for India).

### Intraday Timeframe Hierarchy
```
HTF: Daily/4H — Overall trend bias for the day
PTF: 15-min — Setup identification
ETF: 5-min / 3-min — Precise entry and management
```

### Intraday Indicators (Most Effective)
| Indicator | Use |
|-----------|-----|
| VWAP | Intraday fair value; institutional reference |
| EMA 9 / 20 | Short-term trend; dynamic S/R |
| RSI (14) on 15-min | Overbought/oversold intraday |
| Bollinger Bands (20,2) on 15-min | Volatility; breakouts |
| Supertrend | Dynamic trailing stop / trend filter |
| ATR (14) | Position sizing; volatility assessment |
| Volume (relative to avg) | Confirmation |
| Pivot Points (Daily) | Intraday S/R levels |

### Daily Pivot Points
```
Pivot (P) = (Previous High + Previous Low + Previous Close) / 3

Resistance 1 (R1) = 2P − Previous Low
Resistance 2 (R2) = P + (Previous High − Previous Low)
Resistance 3 (R3) = Previous High + 2(P − Previous Low)

Support 1 (S1) = 2P − Previous High
Support 2 (S2) = P − (Previous High − Previous Low)
Support 3 (S3) = Previous Low − 2(Previous High − P)
```

| Level | Role |
|-------|------|
| Pivot (P) | Intraday directional bias: above = bullish; below = bearish |
| R1, R2, R3 | Intraday resistance levels; targets for longs |
| S1, S2, S3 | Intraday support levels; targets for shorts |

### Supertrend Indicator
```
SuperTrend = ATR-based trailing stop indicator
Settings: ATR period = 7, Multiplier = 3.0 (most common for intraday)
Interpretation:
  Green line below price = BUY signal (uptrend)
  Red line above price = SELL signal (downtrend)
  Flip from red to green = entry signal
  Flip from green to red = exit signal / short entry
```

### Opening Range Breakout (ORB) — India Intraday Classic

**Definition:** Use the first 15 minutes (9:15–9:30) or first 30 minutes to define a range.

**Rules:**
1. Mark High and Low of first 15-min candle (or 30-min range)
2. After range established, wait for clear break with volume
3. BUY if price breaks above ORB High + closes above
4. SELL/SHORT if price breaks below ORB Low + closes below
5. Stop: Back inside the range (opposite end)
6. Target: 1.5× to 2× the range width

**High-Probability ORB Conditions:**
- Range in the direction of prior day's trend
- Volume increasing on breakout
- Nifty also breaking in same direction
- No major resistance within 2× target distance

### VWAP Intraday Strategy

**Setup 1: VWAP Bounce (Trend Following)**
1. Market opens and direction established (above or below VWAP)
2. Price pulls back to VWAP mid-morning
3. Reversal candle forms at VWAP
4. RSI on 5-min not extreme (30–70 range)
5. BUY above the reversal candle's high if above VWAP trend
6. Stop: Below VWAP −0.5%
7. Target: Morning high or VWAP +1 SD

**Setup 2: VWAP Breakout**
1. Price oscillating around VWAP in morning
2. Strong directional candle breaks and closes away from VWAP
3. Volume spike on the breakout candle
4. BUY/SELL in direction of breakout
5. Stop: Back through VWAP
6. Target: Previous intraday high/low

### Intraday Time-Based Rules

| Time (IST) | Behavior | Strategy |
|------------|----------|----------|
| 9:15–9:30 | High volatility; gap fills | Avoid immediate entry; observe |
| 9:30–10:30 | Trend establishing; ORB | ORB trade; VWAP direction setup |
| 10:30–12:00 | Continuation moves | Trend trades; momentum setups |
| 12:00–1:30 | Low volume; sideways | Reduce size or avoid; choppy |
| 1:30–2:30 | FII/institutional activity increases | Re-enter trends; watch for reversals |
| 2:30–3:20 | Final push/reversal common | Scalp only; close positions by 3:20 |
| 3:20–3:30 | DO NOT trade | Avoid; last 10 mins very random |

### Intraday Risk Rules (India)
1. **Max daily loss:** 2% of capital. Stop trading for the day.
2. **Max loss per trade:** 0.5–1% of capital.
3. **SEBI circular:** Intraday leverage limited by broker; use max 5:1 for F&O, 3:1 for equity CNC intraday.
4. **Circuit limits:** Stock hitting upper/lower circuit = mandatory exit; no partial liquidity.
5. **Margin requirements:** Check SPAN + Exposure margin before F&O trades.
6. **News events:** Avoid trading during RBI policy announcements, Union Budget, US Fed decisions.

### Intraday Trade Entry Checklist
- [ ] Market bias established (Nifty direction for the day)
- [ ] Pre-market analysis done (SGX Nifty, global cues, key news)
- [ ] Stock shortlisted from watchlist (momentum + volume)
- [ ] Setup visible on 15-min chart
- [ ] VWAP direction identified
- [ ] Entry trigger confirmed (ORB / VWAP bounce / indicator signal)
- [ ] Stop loss set (not moved adversely once set)
- [ ] Target identified (R:R ≥ 2:1 for intraday)
- [ ] Position size calculated (risk-based, using ATR)
- [ ] Time: Not entering after 2:30 PM for swing-intraday

---

## 10.3 Scalping

### Definition
Ultra-short-term trades: seconds to a few minutes. 1-min to 3-min charts.

### Best Indicators for Scalping
- Level 2 / Market Depth (NSE order book)
- Tick charts or 1-min charts
- EMA 9 and 20 on 1-min
- VWAP (most important reference)
- Stochastic (5,3,3) on 1-min for entry timing
- Volume profile / price clusters

### Scalping Rules
1. Trade only in the direction of the 15-min trend.
2. Use VWAP as directional filter (only long above, short below).
3. Enter on 1-min EMA 9/20 crossover + Stochastic crossover.
4. Stop: 0.1–0.3% or 0.5× ATR.
5. Target: 0.2–0.5% or 1–1.5× stop.
6. High frequency: 5–15 trades per session typical.
7. Requires Level 2 access, fast execution, low brokerage (Zerodha flat ₹20 per order).
8. Works best on: Bank Nifty options, Nifty Futures, liquid large-caps.

---

## 10.4 Indicator Summary by Timeframe

| Indicator | Scalp (1-5m) | Intraday (15-60m) | Swing (Daily) | Position (Weekly) |
|-----------|-------------|-------------------|---------------|-------------------|
| RSI | 7-period | 14-period | 14-period | 14-period |
| MACD | (5,13,3) | (12,26,9) | (12,26,9) | (19,39,9) |
| EMA (Fast) | 9 | 9 | 20 | 20 |
| EMA (Slow) | 20 | 20 | 50 | 50 |
| BB | (10,1.5) | (20,2) | (20,2) | (20,2.5) |
| ATR | 7-period | 14-period | 14-period | 14-period |
| Stochastic | (5,3,3) | (14,3,3) | (14,3,3) | (21,5,5) |
| VWAP | Essential | Essential | N/A | N/A |
| Volume | Tick/1-min | 15-min bar | Daily | Weekly |
| Pivot Points | Yes (daily pivots) | Yes | Weekly | Monthly |

---

# APPENDIX A: QUICK DECISION MATRIX

## Long Trade Signal Strength (Score /10)

| Condition | Points |
|-----------|--------|
| Price above 200 EMA | 1 |
| Price above 50 EMA | 1 |
| RSI 40-60 (pullback zone, not extreme) | 1 |
| RSI bullish divergence | 2 |
| MACD above zero | 1 |
| MACD bullish crossover | 1 |
| Bullish candlestick pattern at support | 1 |
| Volume > 1.5× average on signal candle | 1 |
| Higher timeframe bullish structure (HH/HL) | 1 |
| Price at key S/R, OB, FVG, or Fib level | 2 |
| OBV confirming (rising with price or positive divergence) | 1 |
| FII buying (for index/large cap) | 1 |
| TOTAL max | 14 |

**Trade if score ≥ 6/14. Best trades score ≥ 9/14.**

---

## APPENDIX B: PATTERN FAILURE RULES

**A pattern FAILS when:**
1. Price closes on the WRONG side of the pattern's key level (e.g., close below neckline after supposed H&S break — wait, this confirms it; but close ABOVE neckline after H&S break = failure).
2. False breakout: Price pierces level but closes back inside pattern.
3. Volume does not confirm breakout (very low volume breakout = high failure risk).
4. Opposing higher timeframe signal dominates.

**Response to Failure:**
- Exit position immediately (do not wait for "one more candle")
- Pattern failure often leads to sharp move in OPPOSITE direction
- Failed H&S becomes a very bullish signal (trapped shorts covering)
- Failed Double Top becomes very bullish

---

## APPENDIX C: FIBONACCI LEVELS REFERENCE

| Level | Significance |
|-------|-------------|
| 23.6% | Weak support; strong trend |
| 38.2% | Common support; moderate trend |
| 50.0% | Psychological midpoint |
| 61.8% | "Golden ratio" — most significant |
| 78.6% | Deep; last support before trend reversal |
| 88.6% | Very deep; often indicates trend change |
| 100% | Prior swing high/low (full retracement) |
| 127.2% | Extension (beyond prior high/low) |
| 161.8% | Golden ratio extension — primary target |
| 200.0% | 2× extension — secondary target |
| 261.8% | Aggressive extension |

---

## APPENDIX D: GLOSSARY

| Term | Definition |
|------|-----------|
| ATR | Average True Range — volatility measure |
| BOS | Break of Structure — price breaks swing high/low |
| CHoCH | Change of Character — first opposing BOS |
| FII | Foreign Institutional Investors |
| DII | Domestic Institutional Investors |
| FVG | Fair Value Gap — price imbalance zone |
| HTF | Higher Time Frame |
| LTF | Lower Time Frame |
| OB | Order Block — institutional order concentration zone |
| OI | Open Interest — outstanding F&O contracts |
| ORB | Opening Range Breakout |
| PCR | Put-Call Ratio |
| R:R | Risk-Reward Ratio |
| SMC | Smart Money Concepts |
| VWAP | Volume Weighted Average Price |
| VIX | Volatility Index |
| EMA | Exponential Moving Average |
| SMA | Simple Moving Average |
| S/R | Support and Resistance |
| BB | Bollinger Bands |
| RSI | Relative Strength Index |
| MACD | Moving Average Convergence Divergence |
| CCI | Commodity Channel Index |
| OBV | On-Balance Volume |
| MFI | Money Flow Index |
| HH | Higher High |
| HL | Higher Low |
| LH | Lower High |
| LL | Lower Low |

---

*End of Trading Knowledge Base*
*Version: 1.0 | Date: 2026-04-02*
*For use in Vega AI Trading System *
