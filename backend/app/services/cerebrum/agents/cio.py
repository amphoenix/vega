from __future__ import annotations

import json
from typing import Any, List

from ..agent import AgentOutput


def run_cio(
    llm_client: Any,
    ticker: str,
    company: str,
    price: float,
    agent_results: List[AgentOutput],
    debate_result: dict,
    tech_block: str,
) -> dict:
    """
    Chief Investment Officer final synthesis.

    Reads all agent outputs and the Bull/Bear debate, then issues
    the definitive investment verdict with full position parameters.
    """
    verdicts = [r.verdict for r in agent_results]
    bull_count    = sum(1 for v in verdicts if 'BUY'  in v)
    bear_count    = sum(1 for v in verdicts if 'SELL' in v)
    neutral_count = len(verdicts) - bull_count - bear_count

    agent_summary = '\n'.join(
        f"- {r.name} ({r.firm}) | {r.verdict} | conf {r.confidence}% | "
        + ' | '.join(r.key_findings[:2])
        for r in agent_results
    )

    is_fo_universe = any(x in ticker.upper() for x in [
        'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY',
        'RELIANCE', 'TCS', 'HDFCBANK', 'ICICIBANK', 'INFY',
        'SBIN', 'AXISBANK', 'WIPRO', 'BAJFINANCE', 'MARUTI',
        'TATAMOTORS', 'TATASTEEL', 'ONGC', 'NTPC', 'SUNPHARMA',
    ])

    fo_block = """
F&O INSTRUMENT SELECTION (mandatory — fill all fields):
You must recommend the BEST instrument to trade right now:
  - instrument_type: "FUT" | "CE" | "PE"
    FUT  = futures (directional, no time decay risk, use when trend is clear)
    CE   = call option (bullish, leveraged, time decay applies — only if move expected within expiry)
    PE   = put option (bearish, leveraged, time decay applies — only if fall expected within expiry)

  - expiry: nearest weekly expiry if high confidence, monthly if moderate
    Format: "DDMMMYY" e.g. "10APR25"
    Current month expiry is always safer. Use next month only if thesis needs >2 weeks.

  - strike_price: for CE/PE only — use delta 0.30–0.45 (slightly OTM, best risk/reward)
    CE: slightly OTM = current price + 1–3% (round to nearest 50 for NIFTY/BANKNIFTY)
        Example: NIFTY @ 24000 → pick 24250 CE (≈1% OTM, delta ~0.35)
    PE: slightly OTM = current price - 1–3%
        Example: NIFTY @ 24000 → pick 23750 PE (≈1% OTM, delta ~0.35)
    Deep OTM (>5% away) = AVOID unless very high conviction (delta <0.15 = lottery ticket)
    ATM = delta ~0.50, highest premium but max theta decay
    FUT: set strike_price to 0

  - lot_size: standard NSE lot sizes
    NIFTY=50, BANKNIFTY=15, FINNIFTY=40, MIDCPNIFTY=75
    Stocks: RELIANCE=250, TCS=150, HDFCBANK=550, ICICIBANK=700,
            INFY=300, SBIN=1500, AXISBANK=1200, WIPRO=3000,
            BAJFINANCE=125, MARUTI=50, TATAMOTORS=900, TATASTEEL=5500

  - estimated_premium: approximate option premium in ₹ per lot (for CE/PE)
    Use: ATM premium ≈ 0.4-0.6% of underlying for weekly, 0.8-1.2% for monthly
    For FUT: set to 0

  - confidence_to_trade: 0-100
    >= 70 → short_term_action = "BUY NOW" (bullish: CE/FUT long) or "SELL NOW" (bearish: PE/FUT short)
    < 70  → short_term_action = "WAIT FOR DIP" or "AVOID"
    BE DECISIVE: if Supertrend is BEARISH + ADX > 25 + RSI < 45, that is a high-confidence SELL → PE
    BE DECISIVE: if Supertrend is BULLISH + ADX > 25 + RSI > 55, that is a high-confidence BUY → CE
    Do NOT default to HOLD when technicals are clear — pick a direction

  SAFETY RULES (non-negotiable):
  - Do NOT recommend any instrument if VIX > 20 unless it is a PE hedge
  - Do NOT recommend if close is within 30 min of market close (15:00-15:30)
  - Do NOT recommend if stop_loss breach would exceed 2% of total portfolio
  - If bull_count < 3 AND bear_count < 3: recommend HOLD, short_term_action = WAIT
  - For CE/PE: AVOID buying on the morning of expiry day (Tuesday for NIFTY weekly) — theta destroys premium by afternoon
  - For CE/PE: prefer weekly expiry only if move expected within 2–3 days; else use monthly
""" if is_fo_universe else ""

    prompt = f"""You are the Chief Investment Officer making the final investment decision for {ticker} ({company}).
Current price: ₹{price}

Panel summary ({len(agent_results)} analysts):
{agent_summary}

Bull count: {bull_count}  |  Bear count: {bear_count}  |  Neutral: {neutral_count}

Bull argument:
{debate_result.get('bull_arg', '')}

Bear argument:
{debate_result.get('bear_arg', '')}

Technical snapshot:
{tech_block}

{fo_block}

As CIO, synthesise all inputs and issue your final investment decision.
Be decisive. Acknowledge key risks. If this is an F&O universe stock/index,
you MUST fill all F&O fields — do not leave them null.

Return a JSON object with exactly these keys (no extra keys, no markdown):
{{
  "final_verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "consensus_score": <-100 to 100, positive = bullish>,
  "bull_count": {bull_count},
  "bear_count": {bear_count},
  "neutral_count": {neutral_count},
  "entry_price": <float>,
  "stop_loss": <float>,
  "target_1": <float>,
  "target_2": <float>,
  "target_3": <float>,
  "time_horizon": "intraday | 1 week | 1 month | 3 months",
  "investment_thesis": "<2-3 sentence thesis>",
  "bull_case": "<strongest bull argument>",
  "bear_case": "<strongest bear argument>",
  "key_risks": ["risk 1", "risk 2", "risk 3"],
  "position_size_pct": <recommended % of portfolio 1-10>,
  "short_term_action": "BUY NOW | SELL NOW | WAIT FOR DIP | AVOID | TAKE PROFITS",
  "short_term_reason": "<one sentence>",
  "instrument_type": "FUT | CE | PE | EQ",
  "expiry": "<DDMMMYY or null>",
  "strike_price": <float or 0>,
  "lot_size": <int>,
  "estimated_premium": <float>,
  "confidence_to_trade": <0-100>
}}"""

    response = llm_client.complete(
        agent_id='CIO',
        prompt=prompt,
        max_tokens=900,
    )

    try:
        raw = response.strip()
        # Strip markdown fences if model wraps in ```json
        import re as _re
        raw = _re.sub(r'^```(?:json)?\s*', '', raw, flags=_re.IGNORECASE)
        raw = _re.sub(r'\s*```$', '', raw)
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        data = {
            'final_verdict':      'HOLD',
            'consensus_score':    0,
            'bull_count':         bull_count,
            'bear_count':         bear_count,
            'neutral_count':      neutral_count,
            'entry_price':        price,
            'stop_loss':          round(price * 0.95, 2),
            'target_1':           round(price * 1.05, 2),
            'target_2':           round(price * 1.10, 2),
            'target_3':           round(price * 1.15, 2),
            'time_horizon':       'intraday',
            'investment_thesis':  response[:300],
            'bull_case':          '',
            'bear_case':          '',
            'key_risks':          [],
            'position_size_pct':  2,
            'short_term_action':  'WAIT FOR DIP',
            'short_term_reason':  'Insufficient consensus.',
            'instrument_type':    'EQ',
            'expiry':             None,
            'strike_price':       0,
            'lot_size':           1,
            'estimated_premium':  0,
            'confidence_to_trade': 0,
        }

    return data
