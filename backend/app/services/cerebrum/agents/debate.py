from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, List

from ..agent import AgentOutput


def run_debate(
    llm_client: Any,
    ticker: str,
    company: str,
    price: float,
    agent_results: List[AgentOutput],
    tech_block: str,
    fund_block: str,
    risk_block: str,
) -> dict:
    """
    Orchestrate a Bull vs Bear debate.

    Bull argues strongly for buying. Bear reads Bull's argument first,
    then argues strongly against. Both run in parallel; Bear's prompt
    includes a placeholder that is replaced after Bull completes.
    """
    verdicts = [r.verdict for r in agent_results]
    bull_count = sum(1 for v in verdicts if 'BUY' in v)
    bear_count = sum(1 for v in verdicts if 'SELL' in v)

    findings_text = '\n'.join(
        f"- [{r.name} / {r.firm}] {r.verdict} (confidence {r.confidence}%): "
        + '; '.join(r.key_findings[:2])
        for r in agent_results
    )

    context = f"""Stock: {ticker} ({company})
Current price: ₹{price}

Agent verdicts summary:
{findings_text}

Technical snapshot:
{tech_block}

Fundamental snapshot:
{fund_block}

Risk assessment:
{risk_block}"""

    bull_prompt = f"""You are the Bull advocate in a structured investment debate.
{context}

Construct the strongest possible bullish argument for buying {ticker} right now.
Use specific data points from the agent findings above.
Be direct, confident, and compelling. 3-4 paragraphs maximum."""

    bear_prompt_template = f"""You are the Bear advocate in a structured investment debate.
{context}

The Bull has made the following argument:
{{bull_argument}}

Now construct the strongest possible bearish counter-argument against buying {ticker}.
Directly refute the bull's points using specific data. Point out risks, valuation concerns,
and technical weaknesses the bull ignored. 3-4 paragraphs maximum."""

    def run_bull() -> str:
        return llm_client.complete(
            agent_id='DebateBull',
            prompt=bull_prompt,
            max_tokens=600,
        )

    bull_arg = ''
    bear_arg = ''

    with ThreadPoolExecutor(max_workers=2) as pool:
        bull_future = pool.submit(run_bull)
        bull_arg = bull_future.result()

        bear_prompt = bear_prompt_template.format(bull_argument=bull_arg)

        def run_bear() -> str:
            return llm_client.complete(
                agent_id='DebateBear',
                prompt=bear_prompt,
                max_tokens=600,
            )

        bear_future = pool.submit(run_bear)
        bear_arg = bear_future.result()

    return {
        'bull_arg':    bull_arg,
        'bear_arg':    bear_arg,
        'bull_count':  bull_count,
        'bear_count':  bear_count,
    }
