from __future__ import annotations

import json
from typing import Any

_DOMAIN_DEFAULTS = [
    {'name': 'Technical Expert',    'role': 'Technical Analyst',  'firm': 'Independent Research', 'domain': 'technical',   'data_focus': 'RSI, EMA, VWAP, ATR, momentum',             'entity_source': 'domain_expert'},
    {'name': 'Fundamental Expert',  'role': 'Fundamental Analyst','firm': 'ICICI Securities',      'domain': 'fundamental', 'data_focus': 'PE, PB, ROE, D/E, revenue growth',           'entity_source': 'domain_expert'},
    {'name': 'Macro Economist',     'role': 'Macro Economist',    'firm': 'Goldman Sachs',         'domain': 'macro',       'data_focus': 'India VIX, FII flows, interest rates',        'entity_source': 'domain_expert'},
    {'name': 'Sentiment Analyst',   'role': 'Sentiment Analyst',  'firm': 'Social Intelligence',   'domain': 'sentiment',   'data_focus': 'news sentiment, reddit, FOMO score',          'entity_source': 'domain_expert'},
    {'name': 'Risk Manager',        'role': 'Risk Manager',       'firm': 'BlackRock Risk',        'domain': 'risk',        'data_focus': '52W high/low, ATR volatility, VIX, drawdown', 'entity_source': 'domain_expert'},
]


def extract_entities(
    llm_client: Any,
    ticker: str,
    company: str,
    news_titles: list[str],
    real_analysts: list[str],
    real_holders: list[str],
) -> list[dict]:
    """
    Extract named analysts, fund managers, and insiders from news and
    ownership data, then merge with domain defaults to build the
    20-agent panel.

    Returns a list of dicts with keys:
      name, role, firm, domain, data_focus, entity_source
    """
    news_text     = '\n'.join(f'- {t}' for t in news_titles[:30])
    analysts_text = '\n'.join(f'- {a}' for a in real_analysts[:20])
    holders_text  = '\n'.join(f'- {h}' for h in real_holders[:20])

    prompt = f"""Extract named financial professionals mentioned in connection with {ticker} ({company}).

News headlines:
{news_text}

Analyst coverage (from research platforms):
{analysts_text}

Major shareholders / fund managers:
{holders_text}

For each real, named person you can identify, return a JSON object with:
- name: full name
- role: their professional role (e.g. "Equity Analyst", "Fund Manager", "Promoter", "CEO")
- firm: their employer or fund
- domain: one of technical | fundamental | macro | sentiment | risk
- data_focus: what data they specialise in (1 short phrase)
- entity_source: "real_entity"

Return a JSON array of up to 15 such objects.
Only include people you are confident about — do not hallucinate names.
If fewer than 5 real entities are found, return an empty array rather than fabricating people.
Return only the JSON array — no markdown, no extra text."""

    extracted: list[dict] = []
    try:
        response = llm_client.complete(
            agent_id='EntityExtractor',
            prompt=prompt,
            max_tokens=900,
        )
        extracted = json.loads(response.strip())
        if not isinstance(extracted, list):
            extracted = []
    except Exception:
        extracted = []

    seen_names = {e['name'].lower() for e in extracted}
    for default in _DOMAIN_DEFAULTS:
        if default['name'].lower() not in seen_names:
            extracted.append(default)

    return extracted[:20]
