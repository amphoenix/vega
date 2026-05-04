from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional

from .agent import AgentInput, AgentOutput, registry
from .agents import technical, fundamental, macro, sentiment, risk, debate, cio, entity_agent
from .agents.debate import run_debate
from .agents.cio import run_cio
from .agents.entity_agent import extract_entities

from ..kernel import (
    blackboard  as _blackboard_singleton,
    budget      as _budget_singleton,
    event_bus,
    process_store,
    priority_queue,
    Blackboard,
)
from ..kernel.queue import HIGH, NORMAL
from ..kernel.event_bus import (
    agent_submitted_event,
    agent_completed_event,
    agent_failed_event,
    phase_event,
)


_HIGH_DOMAINS  = {'risk', 'technical'}


class AnalysisRunner:
    """
    Full multi-agent analysis pipeline.

    Orchestrates data fetching, entity extraction, parallel agent dispatch,
    Bull/Bear debate, and CIO synthesis — emitting SSE events throughout.
    """

    def run(
        self,
        ticker: str,
        emit_fn: Callable[[str, dict], None],
        llm_client: Any,
        market_data_fn: Optional[Callable[[str], dict]] = None,
    ) -> dict:
        bb = Blackboard()

        emit_fn('phase', {'phase': 'start', 'msg': f'Starting analysis for {ticker}'})
        event_bus.publish(phase_event('start', f'Starting analysis for {ticker}', ticker=ticker))

        raw = {}
        if market_data_fn:
            try:
                emit_fn('phase', {'phase': 'data_fetch', 'msg': 'Fetching market data'})
                raw = market_data_fn(ticker)
                bb.write('technicals',   raw.get('technicals', {}))
                bb.write('candlesticks', raw.get('candlesticks', {}))
                bb.write('fundamentals', raw.get('fundamentals', {}))
                bb.write('financials',   raw.get('financials', {}))
                bb.write('macro',        raw.get('macro', {}))
                bb.write('news',         raw.get('news', []))
                bb.write('reddit',       raw.get('reddit', []))
                bb.write('fomo_score',   raw.get('fomo_score', 0))
            except Exception as exc:
                emit_fn('phase', {'phase': 'data_fetch_error', 'msg': str(exc)})

        company      = raw.get('company_name', ticker)
        price        = float(raw.get('price', 0.0))
        news_titles  = [n.get('title', '') for n in raw.get('news', [])]
        real_analysts = raw.get('analysts', [])
        real_holders  = raw.get('holders', [])

        emit_fn('phase', {'phase': 'entity_extraction', 'msg': 'Identifying analyst panel'})
        entities = extract_entities(llm_client, ticker, company, news_titles, real_analysts, real_holders)
        emit_fn('phase', {'phase': 'entities_ready', 'msg': f'{len(entities)} entities identified', 'entities': entities})

        board_snapshot = bb.snapshot()

        high_agents:   List[dict] = []
        normal_agents: List[dict] = []

        for entity in entities:
            domain = entity.get('domain', 'fundamental')
            agent  = registry.get(self._agent_key_for_domain(domain))
            if agent is None:
                continue
            entry = {'agent': agent, 'entity': entity}
            if domain in _HIGH_DOMAINS:
                high_agents.append(entry)
            else:
                normal_agents.append(entry)

        agent_results: List[AgentOutput] = []

        def _dispatch(entry: dict) -> AgentOutput:
            agent  = entry['agent']
            entity = entry['entity']
            exec_id = str(uuid.uuid4())
            process_store.register(exec_id, agent.name(), ticker)
            event_bus.publish(agent_submitted_event(exec_id, agent.name()))
            emit_fn('agent_submitted', {'agent_id': agent.name(), 'execution_id': exec_id})

            inp = AgentInput(
                execution_id=exec_id,
                ticker=ticker,
                price=price,
                company_name=company,
                blackboard=board_snapshot,
                task_input=entity,
            )
            try:
                result = agent.run(llm_client, inp)
                process_store.complete(exec_id, result.to_dict())
                event_bus.publish(agent_completed_event(exec_id, agent.name(), result.to_dict()))
                emit_fn('agent', {'agent': result.to_dict()})
                return result
            except Exception as exc:
                process_store.fail(exec_id, str(exc))
                event_bus.publish(agent_failed_event(exec_id, agent.name(), str(exc)))
                emit_fn('agent_failed', {'agent_id': agent.name(), 'error': str(exc)})
                raise

        total_agents = len(high_agents) + len(normal_agents)
        emit_fn('phase', {'phase': 'dispatch',
                          'msg': f'Dispatching {total_agents} agents in parallel',
                          'agent_count': total_agents})

        with ThreadPoolExecutor(max_workers=8) as pool:
            high_futures   = {pool.submit(_dispatch, e): e for e in high_agents}
            normal_futures = {pool.submit(_dispatch, e): e for e in normal_agents}
            all_futures    = {**high_futures, **normal_futures}

            for future in as_completed(all_futures):
                try:
                    agent_results.append(future.result())
                except Exception:
                    pass

        tech_result  = next((r for r in agent_results if 'RSI'  in r.data_focus), None)
        fund_result  = next((r for r in agent_results if 'PE'   in r.data_focus), None)
        risk_result  = next((r for r in agent_results if 'Risk' in r.role), None)

        tech_block = tech_result.reasoning  if tech_result  else ''
        fund_block = fund_result.reasoning  if fund_result  else ''
        risk_block = risk_result.reasoning  if risk_result  else ''

        emit_fn('phase', {'phase': 'debate', 'msg': 'Running Bull vs Bear debate'})
        debate_result = run_debate(
            llm_client=llm_client,
            ticker=ticker,
            company=company,
            price=price,
            agent_results=agent_results,
            tech_block=tech_block,
            fund_block=fund_block,
            risk_block=risk_block,
        )
        emit_fn('debate', debate_result)
        event_bus.publish(phase_event('debate_complete', 'Debate complete', ticker=ticker))

        emit_fn('phase', {'phase': 'cio', 'msg': 'CIO synthesising final verdict'})
        cio_result = run_cio(
            llm_client=llm_client,
            ticker=ticker,
            company=company,
            price=price,
            agent_results=agent_results,
            debate_result=debate_result,
            tech_block=tech_block,
        )
        emit_fn('phase', {'phase': 'cio_complete', 'msg': 'CIO verdict ready', **cio_result})
        event_bus.publish(phase_event('cio_complete', 'CIO complete', ticker=ticker))

        return {
            'ticker':        ticker,
            'company_name':  company,
            'price':         price,
            'agents':        [r.to_dict() for r in agent_results],
            'debate':        debate_result,
            'cio':           cio_result,
            'entities':      entities,
            'budget':        _budget_singleton.summary(),
        }

    @staticmethod
    def _agent_key_for_domain(domain: str) -> str:
        mapping = {
            'technical':   'TechnicalAnalyst',
            'fundamental': 'FundamentalAnalyst',
            'macro':       'MacroEconomist',
            'sentiment':   'SentimentAnalyst',
            'risk':        'RiskManager',
        }
        return mapping.get(domain, 'FundamentalAnalyst')
