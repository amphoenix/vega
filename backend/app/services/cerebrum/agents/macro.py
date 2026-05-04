from __future__ import annotations

import json
from typing import Any

from ..agent import Agent, AgentInput, AgentOutput, registry


class MacroEconomistAgent(Agent):

    def name(self)          -> str: return 'MacroEconomist'
    def role(self)          -> str: return 'Macro Economist'
    def firm(self)          -> str: return 'Goldman Sachs'
    def domain(self)        -> str: return 'macro'
    def entity_source(self) -> str: return 'domain_expert'

    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput:
        entity        = input.task_input or {}
        agent_name    = entity.get('name', self.name())
        agent_role    = entity.get('role', self.role())
        agent_firm    = entity.get('firm', self.firm())
        data_focus    = entity.get('data_focus', 'India VIX, CNN Fear&Greed, Nifty/BankNifty, FII flows, interest rates')
        entity_source = entity.get('entity_source', self.entity_source())

        try:
            macro      = input.blackboard.get('macro', {})
            technicals = input.blackboard.get('technicals', {})

            is_indian = input.ticker.endswith('.NS') or input.ticker.endswith('.BO')

            prompt = f"""You are {agent_name}, {agent_role} at {agent_firm}.
Evaluate the macro backdrop for {input.ticker} ({input.company_name}) at ₹{input.price}.

Macro context:
{json.dumps(macro, indent=2)}

Market technicals (index context):
{json.dumps(technicals, indent=2)}

{"INDIA MACRO FRAMEWORK:" if is_indian else "US MACRO FRAMEWORK:"}

{"" if not is_indian else """
1. RBI POLICY STANCE
   - Current repo rate and direction (hiking / pausing / cutting cycle)
   - Real interest rate = repo rate minus CPI inflation — negative real rates = equity tailwind
   - RBI stance on liquidity (SLR, CRR, OMO) — tight liquidity hurts credit growth
   - Rate-sensitive sectors: Banks, NBFCs, Real Estate react most to rate changes

2. FII vs DII FLOWS
   - FII net buying/selling in equity cash segment (monthly trend matters more than daily)
   - DII (mutual funds + insurance) flows: SIP inflows have become a structural floor for Indian markets
   - When FII sells and DII absorbs — market holds; when both sell — watch out
   - Sector-specific: FIIs favour IT, Pharma, Private Banks; DIIs rotate into domestic cyclicals

3. INDIA VIX
   - Below 13 = complacency / risk-on; 13–18 = normal; 18–22 = elevated; above 22 = fear regime
   - VIX spike before results/budget/elections = elevated option premiums, widen stops
   - VIX crush after event = IV collapse, short-premium strategies win

4. NIFTY 50 & BANKNIFTY STRUCTURE
   - Is Nifty above its 200-DMA? Below = bear market, stocks face structural headwind
   - BankNifty relative strength: outperforming = credit growth healthy = broad market bullish
   - Advance-Decline ratio: breadth confirms or diverges from index movement

5. CURRENCY & GLOBAL LINKAGE
   - USD/INR: rupee depreciation >2% in a month = FII outflow pressure, import costs rise
   - Crude oil (Brent): India imports ~85% of oil needs — every $10/barrel = ~0.4% GDP drag
   - China PMI: Indian IT exports and commodity sectors are linked to Chinese demand
   - US Fed rate decision impact: global risk-off hits Indian mid/small caps hardest
"""}

{"" if is_indian else """
1. FED POLICY & RATE CYCLE
   - Current Fed Funds rate and dot plot trajectory
   - Real rates (nominal rate - PCE inflation): rising real rates = PE compression for growth stocks
   - Yield curve shape: inverted = recession signal; steepening after inversion = recovery beginning
   - Fed balance sheet (QT vs QE): quantitative tightening drains liquidity from risk assets

2. US ECONOMIC CYCLE POSITIONING
   - ISM Manufacturing PMI: above 50 = expansion; below 48 = contraction warning
   - Non-farm payrolls trend: strong jobs = Fed stays hawkish; weak jobs = pivot hope
   - Consumer confidence and retail spending: drives 70% of US GDP
   - Credit spreads (IG and HY): widening spreads = financial stress, de-risk equities

3. MARKET REGIME
   - CNN Fear & Greed index: extreme fear (<20) = contrarian buy; extreme greed (>80) = reduce risk
   - S&P 500 vs 200-DMA: above = bull regime, adds tailwind to individual longs
   - VIX level: below 15 = complacency; above 25 = fear; above 35 = panic (buy the fear)
   - Sector rotation: where is institutional money flowing? (Tech → Staples = risk-off)

4. DOLLAR & GLOBAL FLOWS
   - DXY strength: strong dollar = EM outflows, commodity pressure, US multinational earnings drag
   - 10Y Treasury yield: above 4.5% = competition for equity risk premium
   - Global credit impulse: tightening global credit = 6-9 month lag negative impact on earnings

5. EARNINGS ENVIRONMENT
   - S&P 500 forward earnings revision trend: are analysts upgrading or cutting estimates?
   - Earnings growth vs multiple expansion: which is driving the market?
   - Sector earnings quality: cyclicals (energy, materials) vs defensives (staples, utilities)
"""}

6. STOCK-SPECIFIC MACRO IMPACT
   - Which of the above factors directly affects {input.company_name}'s sector?
   - Is the macro tailwind or headwind stronger than the stock's individual story?
   - What is the single biggest macro risk to this position in the next 90 days?

Return a JSON object with exactly these keys:
{{
  "verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "confidence": <0-100>,
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "reasoning": "<concise paragraph citing specific macro factors, rates, flows and their direct impact on this stock>",
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
                stop_loss=float(data.get('stop_loss', input.price * 0.95)),
                target_price=float(data.get('target_price', input.price * 1.08)),
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
                stop_loss=input.price * 0.95,
                target_price=input.price * 1.08,
                data_focus=data_focus,
                entity_source=entity_source,
                error=str(exc),
            )


registry.register(MacroEconomistAgent)
