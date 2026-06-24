from __future__ import annotations

import json
from typing import Any

from ..agent import Agent, AgentInput, AgentOutput, registry


class FundamentalAnalystAgent(Agent):

    def name(self)          -> str: return 'FundamentalAnalyst'
    def role(self)          -> str: return 'Fundamental Analyst'
    def firm(self)          -> str: return 'ICICI Securities'
    def domain(self)        -> str: return 'fundamental'
    def entity_source(self) -> str: return 'domain_expert'

    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput:
        entity        = input.task_input or {}
        agent_name    = entity.get('name', self.name())
        agent_role    = entity.get('role', self.role())
        agent_firm    = entity.get('firm', self.firm())
        data_focus    = entity.get('data_focus', 'PE, PB, ROE, D/E, revenue growth, analyst targets')
        entity_source = entity.get('entity_source', self.entity_source())

        try:
            fundamentals = input.blackboard.get('fundamentals', {})
            financials   = input.blackboard.get('financials', {})

            is_indian = input.ticker.endswith('.NS') or input.ticker.endswith('.BO')
            market_context = "Indian (NSE/BSE)" if is_indian else "US (NYSE/NASDAQ)"
            specific_framework = """
INDIA-SPECIFIC CHECKS:
- ROCE > 15% is the Indian benchmark for a quality business (Screener.in standard)
- Promoter holding: >50% = skin in game; declining promoter holding = red flag
- FII/DII holding trend: rising FII = foreign confidence; rising DII = domestic SIP inflow support
- Pledged promoter shares %: >20% = governance risk
- Working capital cycle: Indian manufacturing/retail businesses are often working-capital intensive
- GST compliance and revenue recognition: watch for revenue inflation in small/mid caps
- Related party transactions: common governance risk in Indian family-owned businesses
- Debt: Indian companies carry higher cost of debt (~9-11% vs US ~5-6%) — adjust D/E tolerance accordingly
""" if is_indian else """
US-SPECIFIC CHECKS:
- Owner earnings (Buffett): Net Income + D&A - Maintenance CapEx — more reliable than GAAP EPS
- ROIC vs WACC spread: ROIC > 10% WACC = economic value creation; every $ reinvested creates value
- FCF yield vs 10Y Treasury (~4.5%): FCF yield > Treasury = equity is cheap on absolute basis
- Share buybacks: is management reducing share count? Check diluted shares trend over 3Y
- Institutional 13F flow: rising institutional ownership = smart money accumulating
- Goodwill / intangibles as % of assets: >40% = acquisition-heavy, watch for impairments
- SaaS/tech: Rule of 40 (Revenue Growth % + FCF Margin % > 40 = healthy)
- Insider buying vs selling: SEC Form 4 — insider buying at market prices is the strongest signal
"""

            prompt = f"""You are {agent_name}, {agent_role} at {agent_firm}.
Evaluate {input.ticker} ({input.company_name}) — a {market_context} stock — at ₹{input.price}.

Fundamental data:
{json.dumps(fundamentals, indent=2)}

Financial data (last 3 years):
{json.dumps(financials, indent=2)}

Apply this framework:

1. ECONOMIC MOAT
   - Pricing power, switching costs, network effects, cost advantages, brand/regulatory moat?
   - Wide / Narrow / None — back it with a specific data point, not a generic statement

2. QUALITY OF EARNINGS
   - Is reported profit converting to cash? (FCF vs Net Income)
   - Revenue growth({fundamentals.get('revenue_growth', 'N/A')}): organic or acquisition-driven?
   - Margin trend: expanding = pricing power, compressing = competitive pressure
   - ROE({fundamentals.get('roe', 'N/A')}): sustained >15% = compounding machine; check if driven by leverage

3. VALUATION — MARGIN OF SAFETY
   - PE({fundamentals.get('pe', 'N/A')}) vs 5Y historical average and sector median
   - PB({fundamentals.get('pb', 'N/A')}): meaningful for banks/financials; less so for asset-light businesses
   - Analyst consensus target: {fundamentals.get('analyst_target', 'N/A')} — what growth rate does current price imply?
   - Is there a 20%+ margin of safety at current price?

4. BALANCE SHEET STRESS TEST
   - D/E({fundamentals.get('debt_to_equity', 'N/A')}): can the company survive a rate spike or demand shock?
   - Interest coverage: below 3× is danger zone
   - Cash runway if revenues drop 30%
{specific_framework}
5. VERDICT DISCIPLINE
   - Only rate STRONG BUY if moat is wide AND margin of safety >20% AND quality is high
   - Rate SELL if moat is absent AND valuation is stretched AND earnings quality is deteriorating

Return a JSON object with exactly these keys:
{{
  "verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "confidence": <0-100>,
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "reasoning": "<concise paragraph citing moat, ROIC/ROCE, valuation and margin of safety with specific numbers>",
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
                target_price=float(data.get('target_price', input.price * 1.10)),
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
                target_price=input.price * 1.10,
                data_focus=data_focus,
                entity_source=entity_source,
                error=str(exc),
            )


registry.register(FundamentalAnalystAgent)
