from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class AgentResult:
    """Deterministic specialist hand-off used by the planner and executor.

    The specialist does not answer the user directly. It contributes a domain-specific
    workflow, priorities, and constraints that the rest of DaQauntum must act on.
    """

    agent: str
    summary: str
    workflow: list[str] = field(default_factory=list)
    priorities: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class BaseAgent:
    name = "base"
    keywords: tuple[str, ...] = ()
    system_prompt = "You are a general DaQauntum specialist."

    def score(self, request: str) -> int:
        text = request.lower()
        return sum(1 for word in self.keywords if word in text)

    def run(self, request: str, context: list[dict[str, Any]]) -> AgentResult:
        return AgentResult(
            agent=self.name,
            summary=f"Use the {self.name} specialist workflow for this request.",
            workflow=["Clarify the goal", "Use relevant evidence or tools", "Produce a useful final response"],
            priorities=["accuracy", "clarity"],
        )
