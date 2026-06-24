from __future__ import annotations

import json
from typing import Any

from ..agent import Agent, AgentInput, AgentOutput, registry


class RiskManagerAgent(Agent):
    """
    Runs at HIGH priority so the CIO receives an early risk signal
    before fundamental and sentiment agents complete.
    """

    def name(self)          -> str: return 'RiskManager'
    def role(self)          -> str: return 'Risk Manager'
    def firm(self)          -> str: return 'BlackRock Risk'
    def domain(self)        -> str: return 'risk'
    def entity_source(self) -> str: return 'domain_expert'

    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput:
        entity        = input.task_input or {}
        agent_name    = entity.get('name', self.name())
        agent_role    = entity.get('role', self.role())
        agent_firm    = entity.get('firm', self.firm())
        data_focus    = entity.get('data_focus', '52W high/low, ATR volatility, VIX level, max drawdown, circuit limits')
        entity_source = entity.get('entity_source', self.entity_source())

        try:
            technicals = input.blackboard.get('technicals', {})
            macro      = input.blackboard.get('macro', {})

            is_indian = input.ticker.endswith('.NS') or input.ticker.endswith('.BO')
            atr   = technicals.get('atr', 0)
            beta  = technicals.get('beta', 1.0) or 1.0
            high_52 = technicals.get('52w_high', input.price)
            low_52  = technicals.get('52w_low', input.price)
            pct_from_high = round((input.price - high_52) / high_52 * 100, 1) if high_52 else 0
            pct_from_low  = round((input.price - low_52)  / low_52  * 100, 1) if low_52 else 0

            prompt = f"""You are {agent_name}, {agent_role} at {agent_firm}.
Conduct an institutional-grade risk assessment for {input.ticker} ({input.company_name}) at ₹{input.price}.

Technical risk data:
{json.dumps(technicals, indent=2)}

Macro risk context:
{json.dumps(macro, indent=2)}

Pre-computed risk metrics:
- ATR (14): ₹{atr} → daily expected move range ±{round(atr, 2)}
- Beta: {beta} → a 1% Nifty/S&P move = {round(beta, 2)}% move in this stock
- 52W High: ₹{high_52} → price is {pct_from_high}% from the high
- 52W Low:  ₹{low_52}  → price is {pct_from_low}% from the low

Apply this risk framework:

1. POSITION SIZING (Kelly Criterion — conservative half-Kelly)
   - Estimate win probability (p) and reward-to-risk ratio (b) from technical setup
   - Full Kelly = p - (1-p)/b; use HALF Kelly for real trading
   - Cap at 5% of portfolio for single stock regardless of Kelly output
   - For high-beta stocks (beta > 1.5), cap at 3%

2. STOP-LOSS DISCIPLINE
   - ATR-based stop: entry - 1.5×ATR = ₹{round(input.price - 1.5 * atr, 2) if atr else 'N/A'}
   - Structure-based stop: below the last significant swing low (from candlestick data)
   - Use the WIDER of the two — never place stops at obvious round numbers
   - Maximum acceptable loss per trade: 1-2% of total portfolio

3. PRICE LEVEL RISK
   - {pct_from_high}% from 52W high: {"near highs — resistance overhead, reduce size" if pct_from_high > -5 else "extended from highs — lower resistance risk" if pct_from_high < -20 else "mid-range — neutral"}
   - {pct_from_low}% from 52W low: {"near lows — support nearby, but falling knife risk" if pct_from_low < 10 else "well above lows — support is distant"}
   - Circuit limit risk (India: ±5%/10%/20%): is the stock in ASM/GSM surveillance list?

4. VOLATILITY REGIME
   - ATR as % of price: {round(atr / input.price * 100, 2) if atr and input.price else 'N/A'}% daily move
   - {"India VIX elevated: widen stops by 1.5×, reduce position size by 30%" if is_indian else "VIX elevated: high IV means option premium expensive; directional trades need wider stops"}
   - Beta {beta}: {"high sensitivity — use smaller size, tighter stop" if beta > 1.3 else "low sensitivity — can hold larger size through volatility"}

5. TAIL RISK EVENTS
   - Identify any binary events in the next 14 days (earnings, RBI/Fed meeting, AGM, index expiry)
   - Binary events = do NOT hold full position into the event; reduce by 50% or hedge
   - Liquidity risk: avg daily volume vs position size — can you exit in 1 day without moving the price?

{"" if not is_indian else """6. INDIA-SPECIFIC RISK FLAGS
   - F&O expiry week (last Thursday of month): heightened volatility, avoid new entries Tuesday-Thursday
   - ASM/GSM surveillance: stocks in enhanced surveillance have circuit-applied restrictions
   - Promoter pledge > 30%: forced selling risk if stock falls, creates negative spiral
   - Operator-driven stocks (low float, high delivery %): avoid — manipulation risk
"""}

{"" if is_indian else """6. US-SPECIFIC RISK FLAGS
   - Earnings in <2 weeks: options implied move = (ATM call + ATM put) straddle price — is risk/reward worth it?
   - Short squeeze risk: high short interest (>15% float) = can work for OR against you
   - Insider selling cluster in last 90 days: not illegal but signals management sees limited upside
   - Sector ETF outflows: if the sector ETF is under distribution, individual stock faces structural headwind
"""}

Return a JSON object with exactly these keys:
{{
  "verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "confidence": <0-100>,
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "reasoning": "<concise paragraph citing specific risk metrics: ATR stop level, Kelly position size, key risk events, and tail risks>",
  "entry_price": <float>,
  "stop_loss": <float>,
  "target_price": <float>
}}
Return only the JSON — no markdown, no extra text."""

            response = llm_client.complete(
                agent_id=agent_name,
                prompt=prompt,
                max_tokens=600,
            )
            data = json.loads(response.strip())
            return AgentOutput(
                agent_id=agent_name,
                name=agent_name,
                role=agent_role,
                firm=agent_firm,
                verdict=data.get('verdict', 'HOLD'),
                confidence=int(data.get('confidence', 50)),
                key_findings=data.get('key_findings', []),
                reasoning=data.get('reasoning', ''),
                entry_price=float(data.get('entry_price', input.price)),
                stop_loss=float(data.get('stop_loss', input.price * 0.93)),
                target_price=float(data.get('target_price', input.price * 1.06)),
                data_focus=data_focus,
                entity_source=entity_source,
            )
        except Exception as exc:
            return AgentOutput(
                agent_id=agent_name,
                name=agent_name,
                role=agent_role,
                firm=agent_firm,
                verdict='HOLD',
                confidence=0,
                key_findings=[],
                reasoning='',
                entry_price=input.price,
                stop_loss=input.price * 0.93,
                target_price=input.price * 1.06,
                data_focus=data_focus,
                entity_source=entity_source,
                error=str(exc),
            )


registry.register(RiskManagerAgent)
