from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Type


@dataclass
class AgentInput:
    execution_id: str
    ticker:       str
    price:        float
    company_name: str
    blackboard:   dict
    task_input:   dict


@dataclass
class AgentOutput:
    agent_id:      str
    name:          str
    role:          str
    firm:          str
    verdict:       str
    confidence:    int
    key_findings:  List[str]
    reasoning:     str
    entry_price:   float
    stop_loss:     float
    target_price:  float
    data_focus:    str
    entity_source: str
    error:         str = ''

    def to_dict(self) -> dict:
        return asdict(self)


class Agent(ABC):
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def role(self) -> str: ...

    @abstractmethod
    def firm(self) -> str: ...

    @abstractmethod
    def domain(self) -> str: ...

    @abstractmethod
    def entity_source(self) -> str: ...

    @abstractmethod
    def run(self, llm_client: Any, input: AgentInput) -> AgentOutput: ...


class AgentRegistry:
    def __init__(self):
        self._registry: Dict[str, Agent] = {}

    def register(self, agent_class: Type[Agent]) -> Type[Agent]:
        instance = agent_class()
        self._registry[instance.name()] = instance
        return agent_class

    def get(self, name: str) -> Optional[Agent]:
        return self._registry.get(name)

    def list(self) -> List[str]:
        return list(self._registry.keys())

    def all_instances(self) -> List[Agent]:
        return list(self._registry.values())


registry = AgentRegistry()
