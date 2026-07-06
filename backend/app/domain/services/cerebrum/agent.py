from __future__ import annotations

import builtins
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any


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
    key_findings:  list[str]
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
        self._registry: dict[str, Agent] = {}

    def register(self, agent_class: type[Agent]) -> type[Agent]:
        instance = agent_class()
        self._registry[instance.name()] = instance
        return agent_class

    def get(self, name: str) -> Agent | None:
        return self._registry.get(name)

    def list(self) -> builtins.list[str]:
        return list(self._registry.keys())

    def all_instances(self) -> builtins.list[Agent]:
        return list(self._registry.values())


registry = AgentRegistry()
