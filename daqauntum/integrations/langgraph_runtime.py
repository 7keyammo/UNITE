from __future__ import annotations

from typing import Any, Callable, TypedDict


class WorkflowState(TypedDict, total=False):
    request: str
    plan: Any
    result: Any
    review: Any


class LangGraphWorkflowEngine:
    """Optional durable-workflow bridge.

    DaQauntum keeps its own policy/permissions. LangGraph is used only as an
    orchestration/checkpoint engine when installed.
    """

    def __init__(self, planner: Callable[[str], Any], executor: Callable[[Any], Any], critic: Callable[[Any], Any]):
        self.planner = planner
        self.executor = executor
        self.critic = critic

    @staticmethod
    def available() -> bool:
        try:
            import langgraph  # type: ignore  # noqa: F401
            return True
        except Exception:
            return False

    def build(self):
        if not self.available():
            raise RuntimeError("langgraph is not installed; install requirements-integrations.txt")
        from langgraph.graph import END, START, StateGraph  # type: ignore

        graph = StateGraph(WorkflowState)
        graph.add_node("plan", lambda s: {"plan": self.planner(s["request"])})
        graph.add_node("execute", lambda s: {"result": self.executor(s["plan"])})
        graph.add_node("critic", lambda s: {"review": self.critic(s["result"])})
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "execute")
        graph.add_edge("execute", "critic")
        graph.add_edge("critic", END)
        return graph.compile()
