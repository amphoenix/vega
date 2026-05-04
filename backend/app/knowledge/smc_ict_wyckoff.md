# Smart Money Concepts (SMC), ICT, Wyckoff, Elliott Wave & Advanced Price Action
## Comprehensive Trading Reference Guide

> Compiled from: ICT (Inner Circle Trader) teachings, SMC frameworks, Wyckoff methodology, Elliott Wave theory, and institutional trading research.

---

## Table of Contents

1. [Smart Money Concepts (SMC)](#1-smart-money-concepts-smc)
   - [Order Blocks](#11-order-blocks)
   - [Fair Value Gaps (FVG / Imbalance)](#12-fair-value-gaps-fvg--imbalance)
   - [Liquidity Pools](#13-liquidity-pools)
   - [Break of Structure (BOS) vs Change of Character (CHoCH)](#14-break-of-structure-bos-vs-change-of-character-choch)
   - [Premium / Discount Zones](#15-premium--discount-zones)
   - [Market Maker Models](#16-market-maker-models)
   - [Inducement](#17-inducement)
2. [ICT Concepts](#2-ict-inner-circle-trader-concepts)
   - [Power of 3](#21-power-of-3-po3)
   - [Optimal Trade Entry (OTE)](#22-optimal-trade-entry-ote)
   - [Killzones](#23-killzones)
   - [Turtle Soup](#24-turtle-soup-pattern)
   - [Breaker Blocks](#25-breaker-blocks)
   - [Mitigation Blocks](#26-mitigation-blocks)
3. [Wyckoff Method](#3-wyckoff-method)
   - [Accumulation Schematic](#31-accumulation-schematic)
   - [Distribution Schematic](#32-distribution-schematic)
   - [Cause and Effect](#33-cause-and-effect)
   - [Three Laws](#34-three-laws)
4. [Elliott Wave Theory](#4-elliott-wave-theory)
   - [5-Wave Impulse](#41-5-wave-impulse-rules)
   - [3-Wave Corrective](#42-3-wave-corrective-rules)
   - [Fibonacci Ratios](#43-fibonacci-ratios-for-waves)
   - [Common Patterns](#44-common-corrective-patterns)
5. [Supply and Demand Zones](#5-supply-and-demand-zones)
   - [Zone Types (RBR, DBD, RBD, DBR)](#51-zone-types)
   - [Zone Freshness Rules](#52-zone-freshness-rules)
   - [Entry at Zones](#53-entry-at-zones-with-tight-stop-loss)
6. [Risk Management Rules](#6-risk-management-rules)
   - [Position Sizing](#61-position-sizing)
   - [Risk:Reward Ratios](#62-riskrreward-minimum-standards)
   - [Maximum Drawdown](#63-maximum-drawdown-rules)
   - [Correlation Risk](#64-correlation-risk)
   - [Kelly Criterion](#65-kelly-criterion)
7. [Putting It All Together — Trade Framework](#7-putting-it-all-together--multi-confluence-trade-framework)

---

## 1. Smart Money Concepts (SMC)

Smart Money Concepts is an institutional trading methodology that models the behavior of large players (banks, hedge funds, central banks — collectively "smart money") and their footprint on price charts. The core premise: retail trading patterns (support/resistance, trendlines, indicators) are **engineered targets** for liquidity collection by institutions.

---

### 1.1 Order Blocks

An **Order Block (OB)** is the last bullish or bearish candle before a significant impulsive move away from a price area. It represents the zone where a large institution placed its orders. Price is expected to return to this zone to pair off remaining orders.

#### Bullish Order Block
- **Definition**: The last **bearish (red) candle** before a strong bullish impulse move that breaks structure to the upside.
- **Logic**: Institutions accumulated long positions inside this candle. When price retraces back into this area, remaining buy orders get filled.
- **Visual**: A bearish candle (or cluster) immediately preceding a sharp upward move, often with a Fair Value Gap above it.

#### Bearish Order Block
- **Definition**: The last **bullish (green) candle** before a strong bearish impulse move that breaks structure to the downside.
- **Logic**: Institutions distributed / placed short orders inside this candle.
- **Visual**: A bullish candle immediately preceding a sharp downward move.

#### How to Identify
1. Find a significant Break of Structure (BOS) — a swing high/low being taken out impulsively.
2. Look left — the **last opposite-colored candle** before the impulse started is the Order Block.
3. The OB zone = the body of that candle (high to low, or open to close depending on methodology).
4. Mark the entire candle including wicks for conservative entries; body only for aggressive entries.

#### Entry Rules for Order Blocks
| Factor | Criteria |
|--------|----------|
| Higher Timeframe Bias | Must align with HTF trend direction |
| Timeframe | Mark on HTF (4H/Daily), refine entry on LTF (15m/5m) |
| Confirmation | Wait for LTF CHoCH or BOS after price enters OB |
| Stop Loss | Below/above the OB (including wick) — typically 2-5 pips beyond |
| Target | Next liquidity pool, FVG, or opposing OB |
| Invalidation | If price closes through the OB with momentum, block is broken |

#### OB Strength Factors
- **Imbalance present**: A Fair Value Gap immediately after the OB = stronger block
- **Minimal revisits**: A fresh (untested) OB is stronger than one that has been visited
- **Strong impulse**: The more impulsive the departure move, the stronger the OB
- **Confluence**: Aligns with HTF premium/discount zone, round numbers, or session levels

#### Refined Order Block (ROB)
The most recent bearish candle within the OB candle cluster before the move. Provides a tighter entry with a smaller stop loss.

---

### 1.2 Fair Value Gaps (FVG / Imbalance)

A **Fair Value Gap** (also called imbalance or inefficiency) is a three-candle formation where the middle candle moves so aggressively that its range does not overlap with the wicks of the first and third candles — leaving a price gap that was never traded through in both directions.

#### Identification (Three-Candle Rule)
```
Bullish FVG:
  Candle 1: High at H1
  Candle 2: Strong bullish candle
  Candle 3: Low at L3
  
  Gap = H1 to L3 (space between wick of candle 1 and wick of candle 3)
  If H1 < L3 → NO gap
  If H1 > L3 → FVG exists between L3 and H1

Bearish FVG:
  Candle 1: Low at L1
  Candle 2: Strong bearish candle
  Candle 3: High at H3
  
  Gap = H3 to L1
```

#### Why FVGs Matter
- They represent **inefficient price delivery** — the market moved so fast that buy and sell orders did not fully pair.
- Markets have a **mechanical tendency to return** to fill these gaps (seek equilibrium).
- Institutions often place orders inside FVGs to participate at better prices.

#### How Price Fills Gaps
1. **Partial fill**: Price returns to the 50% of the FVG (equilibrium) and reacts — most common.
2. **Full fill**: Price returns and fills the entire gap before continuing.
3. **Overshoot**: Price fills and slightly exceeds the gap boundary (stop hunt) before reversing.

#### FVG Entry Rules
- **Bullish FVG**: Enter long when price returns into the gap from above; place SL below the lower boundary of the FVG.
- **Bearish FVG**: Enter short when price returns into the gap from below; place SL above the upper boundary.
- **Best FVGs**: Found within or just after Order Blocks, on high-timeframe charts, during killzone hours.
- **Invalidation**: If price closes fully through the FVG with a strong momentum candle, gap is likely not holding.

#### FVG Types
| Type | Description |
|------|-------------|
| Standard FVG | Classic 3-candle formation described above |
| Propulsion FVG | FVG that occurs during a trend continuation impulse — high probability |
| Inversion FVG (iFVG) | When price fills an FVG and then continues in original direction — the filled gap becomes support/resistance |
| Consequent Encroachment (CE) | The 50% level of the FVG — key magnet price |

---

### 1.3 Liquidity Pools

**Liquidity** is where stop-loss orders and pending orders cluster. Smart money needs liquidity to execute large orders without excessive slippage — so price is driven toward these areas to collect orders before reversing.

#### Types of Liquidity

**Buy-side Liquidity (BSL)**
- Located **above** swing highs, equal highs, and previous day/week highs
- Stop losses of short sellers + buy-stop orders of breakout buyers
- When price sweeps above these levels, it's collecting BSL

**Sell-side Liquidity (SSL)**
- Located **below** swing lows, equal lows, and previous day/week lows
- Stop losses of long holders + sell-stop orders of breakout sellers
- When price sweeps below these levels, it's collecting SSL

#### Key Liquidity Areas
| Level | Description | Strength |
|-------|-------------|----------|
| Equal Highs (EQH) | Two or more swing highs at the same price | High |
| Equal Lows (EQL) | Two or more swing lows at the same price | High |
| Previous Day High (PDH) | Prior daily session high | Medium-High |
| Previous Day Low (PDL) | Prior daily session low | Medium-High |
| Previous Week High (PWH) | Prior weekly candle high | High |
| Previous Week Low (PWL) | Prior weekly candle low | High |
| Round numbers | 1.3000, 1.3050, etc. | Medium |
| Old swing highs/lows | Obvious chart structures retail traders use | High |
| Trendline liquidity | Stops clustering along drawn trendlines | Medium |

#### Liquidity Sweep (Stop Hunt)
1. Price approaches a liquidity level
2. Price **briefly** pierces through (taking stops)
3. Price **reverses sharply** in the opposite direction
4. Entry is taken on the reversal after confirmation

**Key**: The sweep must be quick — a wick through the level followed by a close back inside. A full candle close through the level is a **breakout**, not a sweep.

---

### 1.4 Break of Structure (BOS) vs Change of Character (CHoCH)

These two concepts define **market structure** and where trends shift.

#### Market Structure Basics
- **Bullish structure**: Series of Higher Highs (HH) and Higher Lows (HL)
- **Bearish structure**: Series of Lower Highs (LH) and Lower Lows (LL)

#### Break of Structure (BOS)
- **Definition**: Price breaks through a swing high (in uptrend) or swing low (in downtrend) **in the direction of the prevailing trend**.
- **Meaning**: Trend **continuation** signal. Smart money is continuing to push price in the trend direction.
- **Example (Bullish BOS)**: In an uptrend, price makes a new Higher High, breaking above the previous swing high → BOS to the upside.
- **Trade implication**: Enter on retracement into the OB/FVG that formed during the BOS impulse.

#### Change of Character (CHoCH)
- **Definition**: Price breaks a swing high or low **against the prevailing trend** — the first sign the trend may be reversing.
- **Meaning**: **Potential trend reversal** signal. Smart money may be shifting direction.
- **Example**: In a downtrend (LH, LL pattern), price suddenly breaks above the most recent Lower High → CHoCH (now could become a Higher High, signaling bullish reversal).
- **Trade implication**: Wait for confirmation — a subsequent BOS in the new direction before entering.

#### BOS vs CHoCH Summary
| Feature | BOS | CHoCH |
|---------|-----|-------|
| Direction | With the trend | Against the trend |
| Signal | Continuation | Reversal / shift |
| Reliability | Higher in established trends | Needs confirmation |
| Entry timing | After retracement to OB/FVG | After follow-through BOS |

#### Swing High/Low Classification
- **Internal Structure**: Short-term swings on lower timeframes (LTF) within a larger move
- **External Structure**: Major swings on higher timeframes (HTF) that define the overall trend

---

### 1.5 Premium / Discount Zones

This concept uses the **50% retracement (equilibrium)** of any price range as a dividing line between "expensive" (premium) and "cheap" (discount) prices.

#### The Logic
```
Premium Zone:  Above 50% of the range → price is "expensive"
Equilibrium:   Exactly 50% of the range → fair value
Discount Zone: Below 50% of the range → price is "cheap"
```

#### Application
- **Bullish bias** → Only look to **buy in the Discount zone** (below 50% of the range)
- **Bearish bias** → Only look to **sell in the Premium zone** (above 50% of the range)

#### How to Measure
1. Identify the **range** — from a significant swing low to a significant swing high (or the inverse)
2. Apply a Fibonacci retracement:
   - **0% to 38.2%** = Deep Discount
   - **38.2% to 50%** = Discount
   - **50%** = Equilibrium
   - **50% to 61.8%** = Premium
   - **61.8% to 100%** = Deep Premium
3. In an uptrend: look to buy between 50%-38.2% (discount zone OBs and FVGs)
4. In a downtrend: look to sell between 50%-61.8% (premium zone OBs and FVGs)

#### Nested Ranges
The concept applies fractally — you can have premium/discount within a premium zone. Always align with the HTF bias first, then find LTF entries within the correct HTF zone.

---

### 1.6 Market Maker Models

The **Market Maker Model (MMM)** describes the three-phase cycle through which smart money (market makers / institutions) moves price to accumulate and distribute large positions.

#### Three Phases

**Phase 1: Accumulation**
- Price consolidates in a range after a previous downtrend
- Market makers are quietly **buying** large positions
- Retail traders see "ranging" market — no clear direction
- Inducement may be set above/below the range to trap breakout traders
- Volume is relatively low; price appears directionless

**Phase 2: Manipulation (The Raid)**
- Price makes a **false move** — sweeps liquidity in the opposite direction of the intended move
- In a bullish setup: price drops below the range lows, taking out retail stop losses (SSL sweep)
- This sweep provides the final liquidity for smart money to complete their accumulation
- Creates a "spring" (Wyckoff concept) or "Judas Swing" (ICT concept)
- Retail traders get stopped out or enter short just as smart money finishes buying

**Phase 3: Distribution (The Mark-up)**
- Price reverses sharply and moves in the intended direction (up in a bullish setup)
- Smart money is now distributing (selling) their position to retail traders chasing the breakout
- Price typically delivers to a "Draw on Liquidity" — an obvious higher level (Previous Week High, etc.)
- The cycle then repeats at the higher level (accumulation → manipulation → distribution)

#### Bullish MMM Example
```
[Consolidation Range]
     ↓ (Manipulation — stop hunt below range lows)
     ↑↑↑ (Distribution — strong bullish impulse to target)
```

#### Bearish MMM Example
```
[Consolidation Range]
     ↑ (Manipulation — stop hunt above range highs)
     ↓↓↓ (Distribution — strong bearish impulse to target)
```

#### Draw on Liquidity (DOL)
The **target** that price is being "drawn" to. Always identify the DOL before entering a trade:
- Bullish: Previous Day/Week/Month High, equal highs above, Buy-side liquidity above
- Bearish: Previous Day/Week/Month Low, equal lows below, Sell-side liquidity below

---

### 1.7 Inducement

**Inducement (IDM)** is a deliberate move by smart money to trigger retail entry in the wrong direction — luring retail traders in with a fake setup before reversing.

#### How It Works
1. Smart money has already set its directional bias (e.g., bullish)
2. Price creates a **minor liquidity sweep** or a **small Break of Structure to the downside**
3. Retail traders see this as a bearish signal and enter short (or exit longs)
4. Smart money uses this retail liquidity to fill their buy orders
5. Price then reverses sharply upward — the actual intended move

#### Identifying Inducement
- A **small, weak CHoCH** that doesn't come with strong momentum
- A swing high/low that looks "obvious" and is then immediately swept
- Often appears as a "fake breakout" of a consolidation range
- Occurs right before or during the manipulation phase of the MMM

#### Inducement vs Genuine CHoCH
| Feature | Inducement | Genuine CHoCH |
|---------|-----------|---------------|
| Momentum | Weak, slow | Strong, impulsive |
| Follow-through | None — reverses quickly | Followed by more structure in new direction |
| Volume | Low | High |
| Context | Before major move in opposite direction | Start of trend reversal |

---

## 2. ICT (Inner Circle Trader) Concepts

ICT concepts were developed and popularized by Michael J. Huddleston. They focus heavily on **time-based price delivery** and institutional order flow.

---

### 2.1 Power of 3 (PO3)

The **Power of 3** is ICT's model for how price behaves within any given trading session or candle:

```
A — Accumulation (range-bound, low volatility, "balance")
M — Manipulation (fake move, stop hunt, Judas Swing)
D — Distribution (true directional move to the actual target)
```

#### Daily PO3 Example (Bullish Day)
- **Asian session (00:00–08:00 GMT)**: Accumulation — price consolidates, a range forms
- **London Open / Overlap**: Manipulation — price drops below the Asian low, sweeps sell-side liquidity, trapping shorts
- **NY session**: Distribution — price reverses and rallies strongly through the Asian high to the upside target (previous day high, weekly high, etc.)

#### Daily PO3 Example (Bearish Day)
- **Asian session**: Accumulation — range forms
- **London/Early NY**: Manipulation — price spikes above the Asian high (buy-side sweep), trapping longs
- **NY session**: Distribution — price drops hard through the Asian low to the downside target

#### Key Insight
The **Judas Swing** (the manipulation phase) is the trade entry opportunity. Retail traders chase the manipulation move; smart money is entering in the opposite direction.

**Judas Swing Entry**:
1. Identify the Asian range (accumulation)
2. Watch for a sweep of one side of the range during London open
3. Enter in the opposite direction when price shows reversal signals (CHoCH on LTF, OB retest, FVG fill)
4. Target the opposite extreme + beyond (draw on liquidity)

---

### 2.2 Optimal Trade Entry (OTE)

**OTE** is ICT's Fibonacci-based entry zone for trades after a structural move.

#### OTE Fibonacci Levels
```
Draw Fibonacci from swing low to swing high (for bullish OTE):
  - 0.0    = Swing High
  - 0.236  = Minimal retracement target
  - 0.382  = Shallow retracement
  - 0.5    = Equilibrium
  - 0.618  = OTE zone begins (key level)
  - 0.705  = OTE midpoint
  - 0.786  = OTE zone ends (key level)
  - 1.0    = Swing Low
  - -0.272 = Extension target 1 (TP1)
  - -0.618 = Extension target 2 (TP2)
```

**OTE Zone = 62% to 79% retracement** (often cited as 61.8% to 78.6%)

#### Rules for OTE
1. **Establish directional bias** (HTF structure bullish or bearish)
2. **Identify the swing** that created the structural move (BOS or CHoCH)
3. **Draw Fibonacci** from the origin of the move to its terminus
4. **Wait for price to retrace** into the 62-79% zone
5. **Confirm entry** with:
   - LTF Order Block within the OTE zone
   - LTF CHoCH/BOS in the direction of the trade
   - FVG within the OTE zone
6. **Stop Loss**: Below the 100% level (swing low for longs / swing high for shorts) + small buffer
7. **Targets**: -27.2% and -61.8% extensions (1.272 and 1.618 of original move)

#### OTE Validity
- OTE is only valid if the original structural move was impulsive (strong, fast, with minimal pullbacks)
- The retracement must be corrective (smaller candles, consolidation — not another impulse)
- Higher timeframe OTE setups (4H, Daily) are more reliable than 5m/15m setups

---

### 2.3 Killzones

**Killzones** are specific time windows when institutional activity is highest and price movements are most reliable. Outside killzones, avoid trading.

#### Four Primary Killzones

**1. Asian Killzone**
- **Time**: 20:00 – 00:00 New York time (01:00 – 05:00 GMT next day)
- **Characteristics**: Range-building session; price creates the "accumulation" range
- **Best for**: Identifying the Asian range for subsequent Judas Swing setups; less active for directional trades
- **Focus**: Mark the Asian High and Asian Low — these become the targets for London manipulation

**2. London Open Killzone**
- **Time**: 02:00 – 05:00 New York time (07:00 – 10:00 GMT)
- **Characteristics**: Highest volume session open; major trend reversals and continuations initiate here
- **Best for**: Judas Swing setups (fade the initial move), trend reversals, OTE setups
- **Best pairs**: EUR/USD, GBP/USD, EUR/GBP, EUR/JPY

**3. New York Open Killzone (NYKZ)**
- **Time**: 07:00 – 10:00 New York time (12:00 – 15:00 GMT)
- **Characteristics**: Second-highest liquidity window; often continues or reverses the London move
- **Best for**: Continuation of London trends, OTE re-entries, ICT Silver Bullet (09:00–11:00 NY)
- **Best pairs**: EUR/USD, GBP/USD, US indices (NQ, ES, YM)

**4. London Close Killzone**
- **Time**: 10:00 – 12:00 New York time (15:00 – 17:00 GMT)
- **Characteristics**: Often a retracement or reversal of the NY session trend
- **Best for**: Scalping, capturing the London close retracement
- **Note**: Lower reliability than London/NY open killzones

#### ICT Silver Bullet
A specific ICT setup that occurs:
- **02:00 – 03:00 NY time** (London open)
- **10:00 – 11:00 NY time** (NY session)
- **14:00 – 15:00 NY time** (London close)

**Silver Bullet Setup**:
1. During the silver bullet window, wait for a Fair Value Gap to form
2. Price must trade above/below a prior swing high/low to engineer liquidity
3. Enter when price returns to fill the FVG
4. Target: 1:2 to 1:5 R:R within the session

---

### 2.4 Turtle Soup Pattern

The **Turtle Soup** is ICT's version of the stop hunt reversal, named after the "Turtle Traders" from the 1980s who used breakout strategies.

#### Background
The original Turtle Trading system (Richard Dennis) bought on 20-day breakouts (new 20-day highs). ICT's Turtle Soup **fades** these breakouts — anticipating that smart money will engineer a false breakout to collect turtle trader stops before reversing.

#### Turtle Soup Setup (Bullish — fade the bearish breakout)
1. Identify a **prior swing low** or a **20-day low** that is obvious and well-tested
2. Price breaks below this low (tripping turtle sell-stop orders)
3. Wait for price to **reverse and close back above** the prior swing low
4. Enter **long** on the close back above the level (or on a small retracement)
5. Stop Loss: Below the new low made during the sweep
6. Target: Back to the origin swing high, or next significant buy-side liquidity

#### Turtle Soup Setup (Bearish — fade the bullish breakout)
1. Identify a **prior swing high** or **20-day high**
2. Price breaks above this high (triggering turtle buy-stop orders)
3. Wait for price to **reverse and close back below** the prior swing high
4. Enter **short** on the close back below the level
5. Stop Loss: Above the new high made during the sweep
6. Target: Back to the origin swing low, or next sell-side liquidity

#### Key Filters
- Must occur during a Killzone (London or NY open) for highest probability
- The break should be **quick** — a wick or 1-2 candle spike, not a sustained move
- Best when the prior low/high has been tested multiple times (more stop orders clustered there)

---

### 2.5 Breaker Blocks

A **Breaker Block** forms when an Order Block **fails** — meaning price trades through it — and then becomes a **resistance/support level in the opposite direction**.

#### Bullish Breaker Block (forms from a failed bearish OB)
1. Price creates a swing high (bearish OB forms — the last bullish candle before the drop)
2. Price drops but then reverses and **breaks above the swing high** (the bearish OB fails)
3. The zone of the old bearish OB now becomes **support** (bullish breaker)
4. When price returns to this zone, it should hold and launch higher

#### Bearish Breaker Block (forms from a failed bullish OB)
1. Price creates a swing low (bullish OB forms — the last bearish candle before the rally)
2. Price rallies but then reverses and **breaks below the swing low** (the bullish OB fails)
3. The zone of the old bullish OB now becomes **resistance** (bearish breaker)
4. When price returns to this zone, it should reject and move lower

#### Why Breakers Work
- The OB zone previously attracted buy/sell orders. After the break, those orders are now trapped on the wrong side.
- When price returns, those trapped traders' exit orders (stop losses) provide further fuel in the new direction.
- Institutions use this zone to re-distribute/re-accumulate.

#### Entry at Breaker Blocks
- Same as OB entry: enter on the return to the zone, with LTF confirmation
- Stop Loss: Beyond the far end of the breaker zone
- Target: Next significant liquidity pool in the direction of the trade

---

### 2.6 Mitigation Blocks

A **Mitigation Block** is where smart money goes back to "mitigate" (close out / reduce) a position that was previously accumulated at a loss, once the price returns to allow them to break even or reduce exposure.

#### Context
- Institution opens a large long position in Zone A
- Price unexpectedly drops below Zone A (the long is temporarily losing)
- Eventually price rallies back to Zone A
- Institution uses this return to Zone A to **exit their losing longs** (mitigation) — or to add more longs at a better price
- After mitigation, price often continues in the original intended direction

#### Mitigation Block vs Order Block
| Feature | Order Block | Mitigation Block |
|---------|-------------|-----------------|
| Context | First visit to the zone | Return to a zone after price moved against it |
| Reason | Initial order placement | Closing out / managing the initial position |
| Expectation | Strong reaction | May have a reaction but can also be a pause point |
| Frequency | More common | Less common |

#### Trading Mitigation Blocks
- Best used as **confluence** — when a mitigation block aligns with an FVG or OTE zone
- Entry confirmation same as OB: LTF structure shift required
- Particularly relevant in lower timeframe scalping setups

---

## 3. Wyckoff Method

Developed by **Richard D. Wyckoff** in the early 20th century, the Wyckoff Method is one of the most respected institutional trading frameworks. It models how "Composite Man" (the collective of large operators) accumulates and distributes large positions.

---

### 3.1 Accumulation Schematic

The Wyckoff Accumulation has **two schematics** (#1 and #2). Below is the classic Schematic #1.

#### Accumulation Phases

**Phase A — Stopping the Prior Downtrend**
| Event | Symbol | Description |
|-------|--------|-------------|
| Preliminary Support | PS | First attempt to slow the downtrend; volume increases, spread widens |
| Selling Climax | SC | Panic selling; high volume, wide spread down candle. Composite Man absorbs supply |
| Automatic Rally | AR | Sharp bounce from SC as selling pressure exhausts; defines top of trading range |
| Secondary Test | ST | Price retests the SC area on lower volume and narrower spread — confirms SC was the bottom |

**Phase B — Building the Cause**
- Price oscillates between the SC (support) and AR (resistance) of the trading range
- Composite Man continues to accumulate stock quietly
- Volume is generally lower than Phase A
- Multiple tests of the trading range boundaries occur
- This phase can last weeks to months — "building the cause" for the subsequent markup
- Additional Secondary Tests (ST) may occur
- **Secondary Test in Phase B**: Tests the AR area on diminishing volume

**Phase C — The Spring**
| Event | Symbol | Description |
|-------|--------|-------------|
| Spring | Spring | Price breaks BELOW the SC low (trading range support), trips stop losses, then reverses back into the range. Composite Man shakes out remaining weak holders. |
| Shakeout | Shakeout | More aggressive version of the Spring — deeper penetration below support |
| Last Point of Support | LPS | Minor pullback after the Spring, before the markup. Higher low relative to the Spring. |

*Note*: Not all accumulations have a Spring — some go directly from Phase B to Phase D (called a "Spring Variant" or Schematic #2 where the Spring is a test above the range rather than below).

**Phase D — Trend Confirmation**
| Event | Symbol | Description |
|-------|--------|-------------|
| Sign of Strength | SOS | Strong rally on high volume that breaks above the AR (trading range resistance). Confirms accumulation complete. |
| Last Point of Support | LPS | Pullback after the SOS — the last buying opportunity before full markup. Must hold above the trading range. |
| Back-Up to the Creek | BU | Price returns to test the breakout level (former resistance now support) on low volume |

**Phase E — Markup**
- Price breaks out of the trading range decisively
- Strong uptrend begins; pullbacks are shallow and on low volume
- Subsequent trading ranges higher up the chart may form new Wyckoff accumulations (re-accumulation)

#### Accumulation Trade Entries
- **Entry 1**: At the Spring/LPS with SL below the Spring low
- **Entry 2**: At the BU (back-up to creek) after the SOS breakout
- **Conservative Entry**: After the SOS breakout on a retest of the AR level

---

### 3.2 Distribution Schematic

Distribution is the mirror image of accumulation — Composite Man is selling large positions to retail buyers.

#### Distribution Phases

**Phase A — Stopping the Prior Uptrend**
| Event | Symbol | Description |
|-------|--------|-------------|
| Preliminary Supply | PSY | First selling by Composite Man into the rally; volume increases, spread widens up |
| Buying Climax | BC | Climactic buying on high volume/wide spread — peak of demand. Composite Man distributes heavily. |
| Automatic Reaction | AR | Sharp drop after BC as demand dries up; defines bottom of trading range |
| Secondary Test | ST | Price rallies back to BC area on lower volume/narrower spread — confirms BC was the top |

**Phase B — Building the Cause (Distribution)**
- Price oscillates between AR (support) and BC (resistance)
- Composite Man continues to distribute at high prices
- Signs of weakness appear during rallies (lower volume, narrower spread)
- Minor new highs may be made (head fakes above the BC level)

**Phase C — Upthrust After Distribution (UTAD)**
| Event | Symbol | Description |
|-------|--------|-------------|
| UTAD | UTAD | Price breaks ABOVE the BC high (trading range resistance), trips buy stops, then reverses back into the range — the distribution equivalent of the Spring. Traps longs. |

**Phase D — Trend Confirmation (Bearish)**
| Event | Symbol | Description |
|-------|--------|-------------|
| Sign of Weakness | SOW | Strong decline on high volume that breaks below the AR (trading range support) |
| Last Point of Supply | LPSY | Minor rally after the SOW — last selling opportunity. Must stay below the trading range. |

**Phase E — Markdown**
- Price breaks below the trading range decisively
- Strong downtrend begins; rallies are shallow and on low volume

#### Distribution Trade Entries
- **Entry 1**: At the UTAD or Last Point of Supply (LPSY) with SL above the UTAD high
- **Entry 2**: Short at the LPSY after the SOW breakdown
- **Conservative Entry**: Short after the SOW breakdown on a retest of the AR level (now resistance)

---

### 3.3 Cause and Effect

**The Cause** is built during the trading range (accumulation or distribution). The **Effect** is the subsequent markup or markdown.

#### Point and Figure (P&F) Count
Wyckoff used **Point & Figure charts** to estimate the potential extent of the Effect:

1. Count the horizontal **width** of the trading range on a P&F chart
2. Multiply by the box size and reversal size
3. **Cause = Width of the P&F horizontal count**
4. **Effect = Price target** projected upward (accumulation) or downward (distribution) from the trading range

**Formula**: Price Target = Price at count line + (Column count × Box size × Reversal amount)

The wider the trading range (more time spent accumulating/distributing), the larger the subsequent move.

#### Modern Application (Bar Charts)
Without P&F charts, estimate the cause by:
- Measuring the **horizontal width** of the trading range in bars/candles
- A wider, longer-duration range = larger expected move after breakout
- Minimum target = height of the trading range projected from the breakout point

---

### 3.4 Three Laws

#### Law 1: Supply and Demand
- Price rises when demand exceeds supply; price falls when supply exceeds demand
- **Volume** is the proxy for supply/demand intensity
- Wide spread + high volume up = demand dominating
- Narrow spread + low volume on rally = supply emerging (lack of demand)
- Wide spread + high volume down = supply dominating

#### Law 2: Cause and Effect
- Every Effect must have a proportional Cause
- You cannot have a large markup without a substantial accumulation period
- Trading ranges ARE the cause being built; trending moves are the effects
- This law prevents chasing: wait for cause to be built before trading the effect

#### Law 3: Effort vs. Result (Harmony and Divergence)
- The **effort** (volume) should harmonize with the **result** (price spread/movement)
- **Harmony**: High volume + wide spread in direction → healthy trend
- **Divergence (warning signs)**:
  - High volume but small spread = effort without result (absorption, potential reversal)
  - Low volume on correction = effort without result (weak correction = trend continuation likely)
  - Rising price on declining volume = lack of buying effort (distribution, weakening uptrend)

---

## 4. Elliott Wave Theory

Developed by **Ralph Nelson Elliott** in the 1930s, Elliott Wave Theory proposes that markets move in **predictable, fractal wave patterns** driven by collective investor psychology.

---

### 4.1 5-Wave Impulse Rules

An **impulse wave** consists of 5 sub-waves (labeled 1-2-3-4-5), with waves 1, 3, 5 moving in the direction of the trend ("motive waves") and waves 2 and 4 moving against the trend ("corrective waves").

#### Three Unbreakable Rules (Impulse)
1. **Wave 2 never retraces more than 100% of Wave 1** — if it does, the wave count is invalid and Wave 1 wasn't actually Wave 1.
2. **Wave 3 is never the shortest motive wave** — Wave 3 must be longer than at least one of Wave 1 or Wave 5 (it is usually the longest).
3. **Wave 4 never overlaps Wave 1's territory** — the low of Wave 4 cannot enter the price territory of Wave 1's high (except in diagonal triangles, which are a special case).

#### Wave Personality / Characteristics
| Wave | Character | Typical Retracement | Volume |
|------|-----------|---------------------|--------|
| Wave 1 | Hesitant start; often mistaken for a correction rally | — | Below average |
| Wave 2 | Deep retracement; tests the resolve of new trend | 50%-61.8% of Wave 1 | Declining from Wave 1 |
| Wave 3 | Strongest, most impulsive wave; clear trend recognition | — | Highest |
| Wave 4 | Consolidation; alternates with Wave 2 in pattern | 38.2% of Wave 3 | Declining |
| Wave 5 | Final push; less momentum than Wave 3; divergence often present | — | Lower than Wave 3 |

#### Guideline: Alternation
Wave 2 and Wave 4 tend to **alternate** in form:
- If Wave 2 is a sharp (zigzag) correction → Wave 4 tends to be a flat or triangle
- If Wave 2 is a flat correction → Wave 4 tends to be a sharp (zigzag)

#### Guideline: Wave Equality
- When Wave 3 is extended (most common), Wave 1 and Wave 5 tend to be approximately equal in length
- Or Wave 5 = 61.8% of Wave 1 length

---

### 4.2 3-Wave Corrective Rules

Corrections move against the main trend and are labeled with letters (A-B-C for simple corrections, W-X-Y or W-X-Y-X-Z for complex corrections).

#### Simple ABC Zigzag
```
Structure: 5-3-5 (Wave A = 5 waves, Wave B = 3 waves, Wave C = 5 waves)
Character: Sharp, deep correction
Wave B: Retraces 38.2% to 61.8% of Wave A
Wave C: Usually equals Wave A in length, or 61.8% / 161.8% of Wave A
Use: Common as Wave 2 corrections in an impulse
```

#### Flat Correction
```
Structure: 3-3-5 (Wave A = 3 waves, Wave B = 3 waves, Wave C = 5 waves)
Character: Sideways, shallow correction
Wave B: Retraces close to 90%+ of Wave A (approaches or exceeds Wave A start)
Wave C: Does not go significantly beyond the end of Wave A
Types:
  - Regular Flat: Wave B ≈ Wave A; Wave C ≈ Wave A
  - Expanded Flat (Irregular): Wave B > Wave A; Wave C > Wave A (most common flat type)
  - Running Flat: Wave B > Wave A; Wave C doesn't reach the end of Wave A
Use: Common as Wave 4 corrections; also as Wave B in larger corrections
```

#### Triangle (Contracting / Expanding)
```
Structure: 3-3-3-3-3 (labeled A-B-C-D-E)
Character: Converging or expanding trendlines; volume declines throughout
Contracting: Lower highs and higher lows (most common) — "pennant-like"
Expanding: Higher highs and lower lows (rare)
Use: Almost always occurs as Wave 4, Wave B, or Wave X in a complex correction
Thrust: After the triangle completes (Wave E), a sharp move in the direction of the prior trend (the "thrust") equals the width of the triangle at its widest point
```

#### Complex Corrections (WXY, WXYXZ)
```
Double Three (WXY):
  W = First correction (any simple type)
  X = Connecting move (corrective, going against W)
  Y = Second correction (any simple type, usually different from W)

Triple Three (WXYXZ):
  Even more complex; three simple corrections connected by two X waves
  Typically seen in very long consolidation periods
```

---

### 4.3 Fibonacci Ratios for Waves

#### Wave Retracements
| Wave | Common Fibonacci Retracement of Prior Wave |
|------|-------------------------------------------|
| Wave 2 | 50%, 61.8%, 76.4% of Wave 1 |
| Wave 4 | 23.6%, 38.2% of Wave 3 |
| Wave B (Zigzag) | 38.2% to 61.8% of Wave A |
| Wave B (Flat) | 90%–100% of Wave A |

#### Wave Extensions and Projections
| Wave | Common Fibonacci Extension |
|------|--------------------------|
| Wave 3 | 161.8% of Wave 1 (most common); also 261.8%, 423.6% |
| Wave 5 | 100% of Wave 1; or 61.8% of Waves 1+3 combined |
| Wave C (Zigzag) | 61.8%, 100%, or 161.8% of Wave A |
| Wave C (Flat) | 100%–123.6% of Wave A |
| Thrust after Triangle | Equal to widest part of triangle |

#### Key Fibonacci Numbers Used in Elliott Wave
- **0.236** (23.6%) — shallow retracement target (Wave 4)
- **0.382** (38.2%) — common Wave 4 retracement; Wave 3 target
- **0.500** (50.0%) — Wave 2 retracement
- **0.618** (61.8%) — "Golden Ratio"; Wave 2 deep retracement; Wave 3 extension start
- **0.786** (78.6%) — deep retracement (√0.618); often near the maximum for Wave 2
- **1.000** (100%) — Wave equality; Wave 5 = Wave 1
- **1.272** (127.2%) — √1.618; extension target
- **1.618** (161.8%) — Golden Ratio extension; typical Wave 3 target
- **2.618** (261.8%) — Wave 3 extended target
- **4.236** (423.6%) — Rare super-extended Wave 3

---

### 4.4 Common Corrective Patterns

#### Diagonal Triangles (Wedges)
- **Leading Diagonal**: Occurs as Wave 1 or Wave A; structure is 5-3-5-3-5 with overlapping waves
- **Ending Diagonal**: Occurs as Wave 5 or Wave C; structure is 3-3-3-3-3 with overlapping waves
- Both types feature converging trendlines; often precede sharp reversals after completion
- The reversal from an ending diagonal typically retraces to the origin of the diagonal

---

## 5. Supply and Demand Zones

Supply and Demand (S&D) trading, pioneered by traders like Sam Seiden and taught extensively by Online Trading Academy, identifies the price levels where significant institutional orders were placed.

---

### 5.1 Zone Types

The four key zone types are based on what happens **before** the zone forms:

#### RBR — Rally-Base-Rally (Demand Zone)
```
Rally → Base (consolidation) → Rally
```
- Price rallies, consolidates briefly, then rallies again sharply
- The **base** (consolidation candles) is the demand zone
- Logic: Institutions placed large buy orders in the base; when those ran out of supply, price launched
- **Bias**: Bullish — enter long when price returns to the base
- **Strength**: Very strong — continuation of existing buying momentum

#### DBD — Drop-Base-Drop (Supply Zone)
```
Drop → Base (consolidation) → Drop
```
- Price drops, consolidates briefly, then drops sharply
- The **base** is the supply zone
- Logic: Institutions placed large sell orders in the base
- **Bias**: Bearish — enter short when price returns to the base
- **Strength**: Very strong — continuation of existing selling momentum

#### DBR — Drop-Base-Rally (Demand Zone — Reversal)
```
Drop → Base → Rally
```
- Price drops into an area, consolidates, then reverses sharply upward
- The **base** is the demand zone where the reversal occurred
- Logic: Institutions absorbed all supply and overwhelmed it with buy orders
- **Bias**: Bullish — enter long when price returns to the base
- **Strength**: Very strong — shows clear institutional demand absorption

#### RBD — Rally-Base-Drop (Supply Zone — Reversal)
```
Rally → Base → Drop
```
- Price rallies into an area, consolidates, then reverses sharply downward
- The **base** is the supply zone where the reversal occurred
- Logic: Institutions distributed all their long positions and began selling
- **Bias**: Bearish — enter short when price returns to the base
- **Strength**: Very strong — shows clear institutional supply distribution

#### Ranking by Strength
```
1. RBR / DBD (continuation) — Strongest (institutions adding to existing positions)
2. DBR / RBD (reversal) — Very strong (institutions reversing direction)
3. Simple Support/Resistance — Weakest (no structural analysis)
```

---

### 5.2 Zone Freshness Rules

**Fresh zones** are the most tradeable. A zone's quality decreases each time price tests and bounces from it.

#### Zone Freshness Ranking
| Status | Description | Tradeable? |
|--------|-------------|-----------|
| Fresh (1st touch) | Price has never returned to the zone after its formation | Highest probability |
| 1x tested | Price touched once and held | Good |
| 2x tested | Price touched twice and held | Moderate |
| 3x+ tested | Price has touched 3+ times | Low — zone is likely to break |

#### Why Freshness Matters
- Each time price returns to a zone, institutional orders are being filled and depleted
- A zone with 3+ tests has had most of its orders consumed — little fuel left to hold
- The first touch of a fresh zone typically produces the strongest reaction

#### Additional Freshness Criteria
1. **Zone left quickly**: The faster price left the zone (steeper departure angle), the stronger the zone
2. **Departure candle size**: Large candles leaving the zone = strong institutional imbalance
3. **No overlap**: The base candles in the zone should have tight bodies, not large overlapping wicks
4. **Higher timeframe first**: Daily/4H zones override 1H/15m zones

---

### 5.3 Entry at Zones with Tight Stop Loss

#### Entry Method 1: Limit Order (Set and Forget)
- Place a **limit buy** at the top of the demand zone / limit sell at the bottom of the supply zone
- Stop Loss: A few pips beyond the far end of the zone
- Best for: Strong fresh zones on HTF; when you cannot monitor the chart

#### Entry Method 2: Confirmation Entry (Lower Risk)
- Wait for price to enter the zone
- Watch for a **confirmation signal** on a lower timeframe:
  - Bullish engulfing candle
  - Pin bar / hammer (demand) or shooting star (supply)
  - LTF CHoCH or BOS
  - FVG fill within the zone
- Enter on the confirmation signal
- Stop Loss: Below the zone low (demand) / above the zone high (supply)

#### Entry Method 3: Zone-to-Zone (Conservative)
- Wait for price to **reach the zone AND form a reversal pattern**
- Only enter when the zone holds — not before
- Stop Loss tighter than Method 1 (below the candle that showed reversal, not the entire zone)

#### Stop Loss Placement
```
Demand Zone Entry:
  - Aggressive: Below the base candles (inside the zone) + buffer
  - Conservative: Below the lowest wick of the entire zone + buffer (3-5 pips/ticks)
  
Supply Zone Entry:
  - Aggressive: Above the base candles (inside the zone) + buffer
  - Conservative: Above the highest wick of the entire zone + buffer
```

#### Target Selection
1. **Next opposing zone**: If entering at a demand zone, target the next supply zone above
2. **Previous high/low**: Target the prior swing high (for longs) or prior swing low (for shorts)
3. **Risk multiple**: Minimum 2R target; ideal 3R+
4. **Partial profits**: Take 50% at 1:2 R:R; move SL to BE; run remainder to 1:4 or 1:5

---

## 6. Risk Management Rules

Risk management is the **single most important skill** separating profitable traders from losing traders. No strategy works without strict risk management.

---

### 6.1 Position Sizing

#### The 1-2% Rule
- **Never risk more than 1-2% of total account capital on a single trade**
- Professional institutional traders typically risk 0.25%–0.5% per trade
- Retail recommendation: 1% per trade (never exceed 2%)

#### Position Size Formula
```
Position Size = (Account Value × Risk %) / (Stop Loss in pips × Pip Value)

Example:
  Account: $10,000
  Risk %: 1% → Risk amount = $100
  Stop Loss: 20 pips
  Pip value (EUR/USD standard lot): $10/pip
  
  Position Size = $100 / (20 × $10) = $100 / $200 = 0.5 lots
```

#### For Stocks / Futures
```
Position Size = Risk Amount / (Stop Loss in price units per share/contract)

Example (Stock):
  Account: $50,000
  Risk %: 1% → Risk amount = $500
  Entry: $100, Stop: $95 → Stop distance = $5/share
  
  Position Size = $500 / $5 = 100 shares
```

#### Risk Per Trade Scenarios
| Account | Risk 1% | Risk 2% | Max Consecutive Losses (1%) |
|---------|---------|---------|----------------------------|
| $1,000 | $10 | $20 | 20 losses before -18% drawdown |
| $5,000 | $50 | $100 | 20 losses before -18% drawdown |
| $10,000 | $100 | $200 | Same proportional |
| $100,000 | $1,000 | $2,000 | Same proportional |

---

### 6.2 Risk:Reward Minimum Standards

#### Minimum R:R Ratios
| Setup Quality | Minimum R:R | Target R:R |
|--------------|-------------|------------|
| Scalp (1-5m) | 1:1.5 | 1:2 |
| Intraday (15m-1H) | 1:2 | 1:3 |
| Swing (4H-Daily) | 1:2.5 | 1:4+ |
| Position (Weekly) | 1:3 | 1:5+ |

#### Why R:R > 1:2 is the Minimum
Even with only a **40% win rate**, a 1:2 R:R strategy is profitable:
```
10 trades × 1% risk each:
  4 wins × 2% = +8%
  6 losses × 1% = -6%
  Net: +2% gain with only 40% win rate
```

With 1:3 R:R, you only need a **25% win rate** to break even.

#### The Break-Even Win Rate Formula
```
Break-even Win Rate = 1 / (1 + R:R ratio)

Examples:
  R:R 1:1 → need 50% win rate to break even
  R:R 1:2 → need 33% win rate to break even
  R:R 1:3 → need 25% win rate to break even
  R:R 1:5 → need 17% win rate to break even
```

#### Partial Profit Taking
- **TP1 (1:2)**: Close 50-60% of position — locks in profit, removes pressure
- **Move SL to Break Even** after TP1: Zero-risk trade
- **TP2 (1:4 or 1:5)**: Let the remainder ride with a trailing stop

---

### 6.3 Maximum Drawdown Rules

#### Hard Limits
| Type | Rule |
|------|------|
| Daily Stop Loss | Stop trading if you lose 3-5% of account in a single day |
| Weekly Stop Loss | Stop trading if you lose 5-7% of account in a single week |
| Monthly Stop Loss | Stop trading if you lose 10-12% of account in a single month |
| Account Max Drawdown | If account drops 20%+, stop all trading, reassess strategy |

#### Why Drawdown Matters (Recovery Math)
```
To recover from:
  10% drawdown → need 11.1% gain
  20% drawdown → need 25.0% gain
  30% drawdown → need 42.9% gain
  40% drawdown → need 66.7% gain
  50% drawdown → need 100% gain
  75% drawdown → need 300% gain
```
This math shows why **protecting capital is more important than chasing profits**.

#### Drawdown Recovery Protocol
1. **After daily limit hit**: No more trades that day. Review journal.
2. **After weekly limit hit**: Take 2-3 days off; review all losing trades.
3. **After monthly limit hit**: Take 1-2 weeks off; full strategy review.
4. **After 20%+ drawdown**: Reduce position size by 50% when returning; rebuild slowly.

---

### 6.4 Correlation Risk

#### What Is Correlation Risk?
If you hold multiple positions that move together, you are effectively taking **one large position** with multiplied risk.

#### High Correlation Pairs/Assets
```
Positively Correlated (move together):
  EUR/USD ↑ and GBP/USD ↑ (correlation: ~0.85-0.95)
  EUR/USD ↑ and AUD/USD ↑ (correlation: ~0.70-0.85)
  Gold ↑ and Silver ↑ (correlation: ~0.90+)
  S&P 500 ↑ and NASDAQ ↑ (correlation: ~0.95+)

Negatively Correlated (move opposite):
  USD/CHF ↑ and EUR/USD ↓ (correlation: ~-0.85 to -0.95)
  USD/JPY and Gold often move inversely
  S&P 500 ↑ and VIX ↓ (correlation: ~-0.75 to -0.90)
```

#### Correlation Risk Rules
1. **Never hold more than 2-3 highly correlated positions simultaneously**
2. **Combined risk** of correlated positions should not exceed your single-trade risk limit × 1.5
3. **Diversify across asset classes**: Don't be all-in on FX when you also trade indices
4. **Reduce size**: If taking 2 correlated pairs, halve the position size on each
5. **Monitor**: Correlations change — recalculate regularly, especially during macro events

#### Practical Rule
```
If Correlation > 0.70: Treat as the same asset for risk purposes
  → Either pick one position or reduce each position to 0.5% risk
  
If Correlation 0.40–0.70: Moderate correlation
  → Reduce each position to 0.7% risk
  
If Correlation < 0.40: Low correlation
  → Can use normal 1% risk per position
```

---

### 6.5 Kelly Criterion

The **Kelly Criterion** is a mathematical formula for calculating the optimal fraction of capital to risk on each trade to maximize long-run geometric growth.

#### Full Kelly Formula
```
f* = (bp - q) / b

Where:
  f* = fraction of capital to risk
  b  = odds received on the wager (R:R ratio — net profit per unit risked)
  p  = probability of winning
  q  = probability of losing (1 - p)
```

#### Example Calculation
```
Win rate (p) = 50% = 0.50
Lose rate (q) = 50% = 0.50
R:R ratio (b) = 2 (win $2 for every $1 risked)

f* = (2 × 0.50 - 0.50) / 2
f* = (1.00 - 0.50) / 2
f* = 0.50 / 2
f* = 0.25 → Kelly says risk 25% of capital

This is FAR too aggressive for practical trading.
```

#### Fractional Kelly (Practical Application)
Traders **never use Full Kelly** — it leads to massive drawdowns and volatility.

| Kelly Fraction | Risk per Trade | Recommended For |
|---------------|----------------|-----------------|
| Full Kelly (100%) | 25% (example above) | Gambling — NEVER use |
| Half Kelly (50%) | 12.5% | Still too aggressive for most |
| Quarter Kelly (25%) | 6.25% | Aggressive traders |
| Tenth Kelly (10%) | 2.5% | Moderate traders |
| Twentieth Kelly (5%) | ~1.25% | Conservative (aligns with 1-2% rule) |

#### Practical Trading Kelly Application
```
For a trader with:
  Win rate: 45%
  Average R:R: 1:2.5
  
f* = (2.5 × 0.45 - 0.55) / 2.5
f* = (1.125 - 0.55) / 2.5
f* = 0.575 / 2.5
f* = 0.23 (23% Full Kelly)

Half Kelly = 11.5% — still aggressive
Quarter Kelly = 5.75%
Tenth Kelly = 2.3%

→ Use Tenth Kelly or below: risk 1-2% per trade
```

#### When Kelly is Useful
- **Comparing strategies**: Higher Kelly fraction = mathematically superior strategy
- **Portfolio allocation**: Full Kelly can be used to **allocate across uncorrelated strategies** (not individual trades)
- **Setting maximums**: Full Kelly value tells you the absolute mathematical maximum — never exceed it

---

## 7. Putting It All Together — Multi-Confluence Trade Framework

A high-probability trade combines **multiple concepts aligning simultaneously**. The more confluence, the higher the probability.

### The Top-Down Analysis Framework

```
Step 1: WEEKLY CHART
  → Determine macro trend direction (BOS / CHoCH)
  → Mark major liquidity pools (weekly highs/lows)
  → Identify weekly OBs, FVGs, Supply/Demand zones
  → Determine if price is in a weekly Premium or Discount zone

Step 2: DAILY CHART
  → Confirm trend alignment with weekly
  → Mark daily OBs, FVGs, and liquidity levels
  → Identify the daily "Draw on Liquidity" (where is price going?)
  → Mark previous day high/low

Step 3: 4-HOUR CHART
  → Identify the MMM phase (accumulation/manipulation/distribution?)
  → Mark 4H OBs, breaker blocks, mitigation blocks
  → Confirm premium/discount on the 4H range

Step 4: 1-HOUR CHART
  → Look for OTE setup (62-79% retracement)
  → Identify the entry OB or FVG
  → Confirm BOS or CHoCH in trade direction

Step 5: 15-MIN / 5-MIN CHART (Entry Timeframe)
  → Wait for the Killzone (London open or NY open)
  → Look for Turtle Soup / Judas Swing
  → Enter at OB/FVG with LTF confirmation
  → Set tight SL beyond the OB/FVG
  → Set TP at the identified draw on liquidity
```

### Confluence Checklist (Minimum 4/7 for a Trade)

```
[ ] HTF Trend alignment (Weekly/Daily pointing in same direction)
[ ] Price in correct Premium/Discount zone
[ ] Fresh Order Block or Supply/Demand Zone at entry area
[ ] Fair Value Gap within or adjacent to the OB
[ ] Fibonacci OTE zone (62-79%) aligns with OB/FVG
[ ] Killzone timing (London or NY open)
[ ] Liquidity sweep (stop hunt) confirmed before entry
[ ] R:R of at least 1:2 achievable
```

### Quick Reference — Pattern Identification Flowchart

```
Is price trending up (HH, HL) or down (LH, LL)?
  ↓
UP: Look to BUY only in Discount Zone (below 50% of last major swing)
DOWN: Look to SELL only in Premium Zone (above 50% of last major swing)
  ↓
Where is the nearest fresh Demand (up) / Supply (down) zone?
  ↓
Is there an Order Block within the zone?
  ↓
Is there a Fair Value Gap at or above/below the OB?
  ↓
Is it a Killzone? (London 02:00-05:00 NY / NY 07:00-10:00 NY)
  ↓
Has price swept liquidity (stop hunt) before entering your zone?
  ↓
YES to most: Enter on LTF CHoCH confirmation within the zone
  ↓
Set SL beyond OB/FVG, set TP at next liquidity pool / opposing zone
```

---

## Key Terms Quick Reference

| Term | Definition |
|------|-----------|
| OB | Order Block — last candle before an impulsive structural move |
| FVG | Fair Value Gap — 3-candle imbalance where middle candle moves without overlap |
| BOS | Break of Structure — trend-confirming break of swing high/low |
| CHoCH | Change of Character — counter-trend break signaling potential reversal |
| IDM | Inducement — fake move to trap retail before the real move |
| PO3 | Power of 3 — Accumulation, Manipulation, Distribution cycle |
| OTE | Optimal Trade Entry — 62-79% Fibonacci retracement entry zone |
| HTF | Higher Timeframe |
| LTF | Lower Timeframe |
| PDH/PDL | Previous Day High / Previous Day Low |
| PWH/PWL | Previous Week High / Previous Week Low |
| BSL | Buy-side Liquidity — stops above highs |
| SSL | Sell-side Liquidity — stops below lows |
| DOL | Draw on Liquidity — where price is being drawn to |
| MMM | Market Maker Model — Accumulation / Manipulation / Distribution |
| RBR | Rally-Base-Rally (demand zone type) |
| DBD | Drop-Base-Drop (supply zone type) |
| RBD | Rally-Base-Drop (supply zone type — reversal) |
| DBR | Drop-Base-Rally (demand zone type — reversal) |
| PS | Preliminary Support (Wyckoff accumulation) |
| SC | Selling Climax (Wyckoff) |
| AR | Automatic Rally (Wyckoff) |
| ST | Secondary Test (Wyckoff) |
| SOS | Sign of Strength (Wyckoff) |
| LPS | Last Point of Support (Wyckoff) |
| BC | Buying Climax (Wyckoff distribution) |
| SOW | Sign of Weakness (Wyckoff distribution) |
| UTAD | Upthrust After Distribution (Wyckoff) |
| LPSY | Last Point of Supply (Wyckoff distribution) |
| P&F | Point and Figure chart (Wyckoff counting) |

---

*Note: This guide is for educational purposes. All trading carries substantial risk of loss. No strategy guarantees profits. Always backtest concepts rigorously before risking real capital. Past performance of any pattern or method does not guarantee future results.*
