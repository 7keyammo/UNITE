from __future__ import annotations

from dataclasses import dataclass

from core.brain import CognitiveModelRouter
from core.planner import Planner


@dataclass
class CriticResult:
    approved: bool
    issues: list[str]
    improved_answer: str | None = None
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False


class Critic:
    """Quality-control layer. It returns concise critique, not hidden reasoning traces."""

    def __init__(self, brain: CognitiveModelRouter, enabled: bool = True):
        self.brain = brain
        self.enabled = enabled

    def review(
        self,
        request: str,
        draft: str,
        agent_name: str,
        routing: dict | None = None,
    ) -> CriticResult:
        if not self.enabled:
            return CriticResult(True, [])

        system = """You are DaQauntum's critic. Evaluate the draft for correctness, usefulness, unsupported claims, and whether it actually answers the request.
Do not provide chain-of-thought. Return only concise JSON:
{
  "approved": true,
  "issues": ["short issue"],
  "improved_answer": null
}
If a small revision would fix the draft, put a complete improved final answer in improved_answer. Otherwise use null.
"""
        prompt = f"Agent: {agent_name}\nUser request:\n{request}\n\nDraft:\n{draft}"
        response = self.brain.generate("critic", system, [{"role": "user", "content": prompt}], routing=routing)
        parsed = Planner._extract_json(response.text)
        if not parsed:
            return CriticResult(
                True,
                ["Critic returned non-JSON; draft kept."],
                provider=response.provider,
                model=response.model,
                fallback_used=response.fallback_used,
            )

        approved = bool(parsed.get("approved", False))
        issues = [str(item) for item in parsed.get("issues", []) if str(item).strip()][:6]
        improved = parsed.get("improved_answer")
        if improved is not None:
            improved = str(improved).strip() or None
        return CriticResult(
            approved=approved,
            issues=issues,
            improved_answer=improved,
            provider=response.provider,
            model=response.model,
            fallback_used=response.fallback_used,
        )
