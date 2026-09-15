from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from core.brain import CognitiveModelRouter


@dataclass
class PlanStep:
    id: str
    description: str
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep]
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [asdict(step) for step in self.steps],
            "brain": {
                "provider": self.provider,
                "model": self.model,
                "fallback_used": self.fallback_used,
            },
        }


class Planner:
    def __init__(self, brain: CognitiveModelRouter, tool_descriptions: list[dict[str, Any]], enabled: bool = True):
        self.brain = brain
        self.tool_descriptions = tool_descriptions
        self.enabled = enabled

    def create_fast(self, request: str, agent_result: dict[str, Any]) -> Plan:
        """Deterministic low-latency plan used by Realtime cognition."""
        plan = self._heuristic_plan(request, agent_result)
        plan.provider = "runtime"
        plan.model = "heuristic-fast-path"
        plan.fallback_used = False
        return plan

    def create(
        self,
        request: str,
        agent_result: dict[str, Any],
        context: list[dict[str, Any]],
        routing: dict[str, Any] | None = None,
    ) -> Plan:
        if not self.enabled:
            return self._heuristic_plan(request, agent_result)

        system = """You are DaQauntum's planning module. Produce only JSON, never markdown.
Create a short execution plan for the user's request. You MUST use the selected specialist's workflow and constraints as planning input.
Use tools only when they are genuinely useful. Do not invent tools. Do not claim a tool has run. Keep the plan to 1-5 steps.
Return exactly this shape:
{
  "goal": "...",
  "steps": [
    {"id": "s1", "description": "...", "tool": null, "arguments": {}, "rationale": "..."}
  ]
}
Available tools:
""" + json.dumps(self.tool_descriptions, ensure_ascii=False)

        context_text = self._context_excerpt(context)
        prompt = (
            "SPECIALIST EXECUTION\n" + json.dumps(agent_result, ensure_ascii=False)
            + "\n\nRECENT CONTEXT\n" + context_text
            + "\n\nUSER REQUEST\n" + request
        )
        response = self.brain.generate("planner", system, [{"role": "user", "content": prompt}], routing=routing)
        if response.provider == "mock":
            plan = self._heuristic_plan(request, agent_result)
            plan.provider, plan.model, plan.fallback_used = response.provider, response.model, response.fallback_used
            return plan

        parsed = self._extract_json(response.text)
        if not parsed:
            plan = self._heuristic_plan(request, agent_result)
            plan.provider, plan.model, plan.fallback_used = response.provider, response.model, response.fallback_used
            return plan

        try:
            steps = []
            known_tools = {tool["name"] for tool in self.tool_descriptions}
            for index, raw in enumerate(parsed.get("steps", []), start=1):
                tool = raw.get("tool")
                if tool not in known_tools:
                    tool = None
                steps.append(
                    PlanStep(
                        id=str(raw.get("id") or f"s{index}"),
                        description=str(raw.get("description") or "Reason about the request"),
                        tool=tool,
                        arguments=raw.get("arguments") if isinstance(raw.get("arguments"), dict) else {},
                        rationale=str(raw.get("rationale") or ""),
                    )
                )
            if not steps:
                steps = [PlanStep(id="s1", description="Reason about the request")]
            return Plan(
                goal=str(parsed.get("goal") or request),
                steps=steps[:5],
                provider=response.provider,
                model=response.model,
                fallback_used=response.fallback_used,
            )
        except Exception:
            plan = self._heuristic_plan(request, agent_result)
            plan.provider, plan.model, plan.fallback_used = response.provider, response.model, response.fallback_used
            return plan

    def _heuristic_plan(self, request: str, agent_result: dict[str, Any]) -> Plan:
        text = request.lower()
        steps: list[PlanStep] = []

        if any(phrase in text for phrase in ("remember", "memory", "what did we", "previously")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Search DaQauntum memory for relevant prior context",
                    tool="memory_search",
                    arguments={"query": request, "limit": 6},
                    rationale="The request refers to stored context.",
                )
            )

        if any(phrase in text for phrase in ("what supports", "what contradicts", "knowledge graph", "provenance", "evidence for", "how is this connected", "how are these connected")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Search DaQauntum's provenance-aware knowledge graph",
                    tool="knowledge_search",
                    arguments={"query": request, "limit": 6},
                    rationale="The request asks about evidence, provenance, contradiction, or connected knowledge.",
                )
            )

        if any(phrase in text for phrase in ("look at my screen", "what is on my screen", "see my screen", "look at this screen", "look at my camera", "what do you see", "analyze this image")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Analyze DaQauntum's latest captured visual frame",
                    tool="perception_analyze",
                    arguments={"prompt": request},
                    rationale="The request explicitly asks DaQauntum to inspect visual context.",
                )
            )

        if any(phrase in text for phrase in ("what is around me", "what devices are around", "environment status", "presence status", "battery level", "computer temperature", "what sensors", "network status", "wifi status", "wi-fi status", "bluetooth status")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Read DaQauntum's passive Presence Layer",
                    tool="presence_status",
                    arguments={"refresh": True},
                    rationale="The request asks about local environmental or device awareness.",
                )
            )

        if any(phrase in text for phrase in ("scan wifi", "scan wi-fi", "nearby wifi", "nearby wi-fi", "what wifi networks")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Explicitly scan nearby Wi-Fi networks",
                    tool="wifi_scan",
                    arguments={},
                    rationale="The user explicitly requested a Wi-Fi radio scan.",
                )
            )

        if any(phrase in text for phrase in ("scan bluetooth", "nearby bluetooth", "bluetooth devices around")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Explicitly scan nearby Bluetooth devices",
                    tool="bluetooth_scan",
                    arguments={"seconds": 6},
                    rationale="The user explicitly requested a Bluetooth discovery scan.",
                )
            )

        if any(phrase in text for phrase in ("discover services", "bonjour devices", "mdns", "local network services")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Discover local mDNS/Bonjour services",
                    tool="service_discovery",
                    arguments={},
                    rationale="The request asks for explicit local-service discovery.",
                )
            )

        if any(phrase in text for phrase in ("inspect my computer", "observe my screen", "what app is open", "what is happening on my computer")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Observe the current computer state without making changes",
                    tool="computer_observe",
                    arguments={"request": request},
                    rationale="The request asks for read-only desktop observation.",
                )
            )

        if any(phrase in text for phrase in ("click it", "type it", "do it on my computer", "do this on my computer", "open the app", "fill this out", "navigate to")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Prepare the requested computer/browser action",
                    tool="computer_action",
                    arguments={"request": request},
                    rationale="The request asks DaQauntum to change computer or browser state.",
                )
            )

        if any(phrase in text for phrase in ("source", "paper", "dataset", "notebook", "citation", "cited", "where did this come from", "file evidence", "research artifact")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Search DaQauntum's indexed first-class sources",
                    tool="source_search",
                    arguments={"query": request, "limit": 6},
                    rationale="The request asks for evidence or information from indexed source artifacts.",
                )
            )

        if any(phrase in text for phrase in ("list files", "show files", "project files")):
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="List files available inside the DaQauntum project root",
                    tool="list_project_files",
                    arguments={"relative_path": "."},
                    rationale="The user asked about project files.",
                )
            )

        save_match = re.search(r"(?:save|write) (?:this )?(?:as )?(?:a )?note(?: named)?\s*[\"']?([\w.-]+)?", text)
        if save_match:
            name = save_match.group(1) or "daqauntum-note"
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Prepare a local project note",
                    tool="write_note",
                    arguments={"name": name, "content": request},
                    rationale="The user asked to save information as a note.",
                )
            )


        file_match = re.search(r"(?:create|write) (?:a )?(?:project )?file(?: named| called)?\s*[\"']?([\w./-]+)", text)
        if file_match and "note" not in text:
            path = file_match.group(1)
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description="Prepare a project text file",
                    tool="write_project_file",
                    arguments={"path": path, "content": request},
                    rationale="The user asked DaQauntum to create or write a project file.",
                )
            )

        for item in agent_result.get("workflow", []):
            if len(steps) >= 4:
                break
            steps.append(
                PlanStep(
                    id=f"s{len(steps)+1}",
                    description=str(item),
                    rationale=f"Required by the {agent_result.get('agent', 'general')} specialist workflow.",
                )
            )

        steps.append(
            PlanStep(
                id=f"s{len(steps)+1}",
                description="Synthesize the request, specialist workflow, context, and tool observations into a response",
                rationale="Produce the final DaQauntum response.",
            )
        )
        return Plan(goal=request, steps=steps[:5])

    @staticmethod
    def _context_excerpt(context: list[dict[str, Any]], max_chars: int = 4000) -> str:
        lines = [f"{item.get('role', 'unknown')}: {item.get('content', '')}" for item in context[-8:]]
        return "\n".join(lines)[-max_chars:]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        text = text.strip()
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None
