from __future__ import annotations

import json
from typing import Any

from ..agent import Agent, AgentInput, AgentOutput, registry


class SentimentAnalystAgent(Agent):

    def name(self)          -> str: return 'SentimentAnalyst'
    def role(self)          -> str: return 'Sentiment Analyst'
    def firm(self)          -> str: return 'Social Intelligence'
    def domain(self)        -> str: return 'sentiment'
    def entity_source(self) -> str: return 'domain_expert'

    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput:
        entity        = input.task_input or {}
        agent_name    = entity.get('name', self.name())
        agent_role    = entity.get('role', self.role())
        agent_firm    = entity.get('firm', self.firm())
        data_focus    = entity.get('data_focus', 'news sentiment, reddit posts, FOMO score')
        entity_source = entity.get('entity_source', self.entity_source())

        try:
            news       = input.blackboard.get('news', [])
            reddit     = input.blackboard.get('reddit', [])
            fomo_score = input.blackboard.get('fomo_score', 0)

            is_indian = input.ticker.endswith('.NS') or input.ticker.endswith('.BO')

            prompt = f"""You are {agent_name}, {agent_role} at {agent_firm}.
Evaluate market sentiment for {input.ticker} ({input.company_name}) at ₹{input.price}.

Recent news:
{json.dumps(news, indent=2)}

Social media / Reddit posts:
{json.dumps(reddit, indent=2)}

FOMO score (0-100, higher = stronger retail excitement):
{fomo_score}

Apply this sentiment framework:

1. NEWS TONE & CATALYST CLASSIFICATION
   - Categorise each headline: Earnings beat/miss | Management change | Regulatory action |
     M&A / block deal | Analyst upgrade/downgrade | Macro linkage | Product launch | Litigation
   - Weight: regulatory + management change headlines carry 3× the impact of generic coverage
   - Recency: news from the last 48 hours outweighs week-old news
   - Sentiment direction: net positive / net negative / conflicted

2. SMART MONEY vs DUMB MONEY DIVERGENCE
   - FOMO score {fomo_score}/100: above 70 = retail crowded, contrarian signal to be cautious
   - Reddit/social bullishness when price is near highs = distribution phase warning
   - Reddit/social panic when price is near lows = accumulation opportunity
   - Rule: when retail is euphoric (FOMO > 75) AND price extended, reduce confidence in BUY

{"" if not is_indian else """3. INDIA-SPECIFIC SENTIMENT SIGNALS
   - Bulk/block deal mentions in news: large block at discount = institutional exit; at premium = accumulation
   - Promoter activity: promoter buying from open market = strongest bullish signal in India
   - SEBI actions, pledging news, insider trading alerts = immediate red flags
   - Quarterly results calendar: within 2 weeks of results = elevated volatility, widen stops by 1.5×
   - Budget / RBI policy proximity: stocks in rate-sensitive sectors need extra caution pre-event
   - IPO / QIP news for the company: dilution is bearish; buyback announcement is bullish
"""}

{"" if is_indian else """3. US-SPECIFIC SENTIMENT SIGNALS
   - Options flow signals in news: unusual call buying = smart money positioning bullish
   - Short interest context: high short interest (>15% float) = short squeeze potential if stock rallies
   - Insider buying vs selling: Form 4 filings — cluster buying by multiple insiders is the strongest signal
   - Analyst rating changes: upgrade from Sell to Buy carries more weight than Buy to Strong Buy
   - Earnings whisper vs consensus: if whisper number is above consensus, beat bar is higher
   - Institutional 13F mentions: any news of major fund initiating or exiting a position
"""}

4. EVENT CALENDAR RISK
   - Identify any known events in the next 30 days: earnings, AGM, central bank meeting,
     index rebalancing, expiry week (F&O/options)
   - Rate each event: High / Medium / Low impact on this specific stock

5. CONTRARIAN SIGNAL CHECK
   - Extreme bullish sentiment (FOMO > 80, all positive news) = be skeptical, ask "who is left to buy?"
   - Extreme bearish sentiment (all negative news, social panic) = look for capitulation bottom signals
   - The best entries are when sentiment and fundamentals diverge

Return a JSON object with exactly these keys:
{{
  "verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "confidence": <0-100>,
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "reasoning": "<concise paragraph citing specific news catalysts, FOMO level, smart/dumb money signals and upcoming events>",
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
                target_price=float(data.get('target_price', input.price * 1.07)),
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
                target_price=input.price * 1.07,
                data_focus=data_focus,
                entity_source=entity_source,
                error=str(exc),
            )


registry.register(SentimentAnalystAgent)
