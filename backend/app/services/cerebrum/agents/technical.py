from __future__ import annotations

import json
from typing import Any

from ..agent import Agent, AgentInput, AgentOutput, registry


class TechnicalAnalystAgent(Agent):

    def name(self)          -> str: return 'TechnicalAnalyst'
    def role(self)          -> str: return 'Technical Analyst'
    def firm(self)          -> str: return 'Independent Research'
    def domain(self)        -> str: return 'technical'
    def entity_source(self) -> str: return 'domain_expert'

    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput:
        entity       = input.task_input or {}
        agent_name   = entity.get('name', self.name())
        agent_role   = entity.get('role', self.role())
        agent_firm   = entity.get('firm', self.firm())
        data_focus   = entity.get('data_focus', 'RSI, EMA, VWAP, ATR, momentum, candlestick patterns')
        entity_source = entity.get('entity_source', self.entity_source())

        try:
            technicals   = input.blackboard.get('technicals', {})
            candlesticks = input.blackboard.get('candlesticks', {})

            patterns = technicals.get('candle_patterns', [])
            pattern_block = '\n'.join(f"  • {p}" for p in patterns) if patterns else '  • No significant pattern detected'

            prompt = f"""You are {agent_name}, {agent_role} at {agent_firm}.
Evaluate {input.ticker} ({input.company_name}) at ₹{input.price} using institutional-grade technical analysis.

Indicators:
{json.dumps({k: v for k, v in technicals.items() if k != 'candle_patterns'}, indent=2)}

PRE-COMPUTED CANDLESTICK PATTERNS (last 5 bars):
{pattern_block}

Raw OHLCV bars (last {len(candlesticks) if isinstance(candlesticks, list) else 20} bars, newest last):
{json.dumps(candlesticks[-20:] if isinstance(candlesticks, list) else candlesticks, indent=2)}

Apply this framework in order:

1. MARKET STRUCTURE (Smart Money Concepts)
   - Is price making Higher Highs / Higher Lows (uptrend) or Lower Highs / Lower Lows (downtrend)?
   - Has there been a Break of Structure (BOS) — decisive close beyond the last swing high/low?
   - Any Change of Character (CHoCH) — first opposing break after a trend, signalling potential reversal?

2. LIQUIDITY & ORDER FLOW
   - Are there equal highs or equal lows nearby (liquidity pools above/below)?
   - Has price recently swept liquidity (wick through a level then reversed)?
   - Is price trading inside a Fair Value Gap (FVG) — an imbalance between two non-overlapping candles?
   - Identify the nearest Order Block (last bearish candle before a bullish impulse, or vice versa)

3. KEY LEVELS
   - VWAP: is price above (institutional buy bias) or below (sell bias)?
   - 52-week high/low proximity: within 3% = extreme level with high reaction probability
   - ATR({technicals.get('atr', 'N/A')}): set stop-loss 1.5×ATR from entry, target 2.5×ATR (minimum 2.5R)

4. MOMENTUM CONFIRMATION
   - RSI({technicals.get('rsi', 'N/A')}): divergence vs price more important than level alone
   - EMA stack: price vs EMA20({technicals.get('ema20', 'N/A')}) / EMA50({technicals.get('ema50', 'N/A')}) / EMA200({technicals.get('ema200', 'N/A')})
   - Supertrend: {technicals.get('supertrend_signal','N/A')} (dir={technicals.get('supertrend_dir')}, line={technicals.get('supertrend_line')}) — primary trend filter
   - ADX: {technicals.get('adx','N/A')} ({technicals.get('adx_trend_strength','')}) +DI={technicals.get('adx_plus_di')} -DI={technicals.get('adx_minus_di')} — ADX>25 = tradeable trend
   - BB rating: {technicals.get('bb_signal','N/A')} (score {technicals.get('bb_rating',0)}/3) — BB upper={technicals.get('bb_upper')} lower={technicals.get('bb_lower')}
   - MACD: {technicals.get('macd_cross','N/A')} crossover, histogram={technicals.get('macd_hist')}
   - Volume: is the last move accompanied by expanding or contracting volume? vol_ratio={technicals.get('vol_ratio')}

5. TRADE SETUP
   - Only take the trade if at least 3 of the above factors align
   - State your entry trigger (what price action confirms the trade)
   - State invalidation level (what would prove the thesis wrong)

Return a JSON object with exactly these keys:
{{
  "verdict": "STRONG BUY | BUY | HOLD | SELL | STRONG SELL",
  "confidence": <0-100>,
  "key_findings": ["finding 1", "finding 2", "finding 3"],
  "reasoning": "<concise paragraph citing specific levels and structure>",
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
                target_price=float(data.get('target_price', input.price * 1.05)),
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
                target_price=input.price * 1.05,
                data_focus=data_focus,
                entity_source=entity_source,
                error=str(exc),
            )


registry.register(TechnicalAnalystAgent)
