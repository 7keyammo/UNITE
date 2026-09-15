from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from memory.store import MemoryStore
from memory.intelligence import MemoryIntelligence


@dataclass
class MemoryWrite:
    kind: str
    title: str
    content: str
    project: str | None = None
    tags: list[str] | None = None
    confidence: float = 1.0
    source: str | None = None


class StructuredMemory:
    """Deterministic structured memory + local memory intelligence for DaQauntum v0.2.5.

    This layer intentionally does not let a language model silently decide what is
    permanent truth. It auto-stores episodes and high-signal categories using
    inspectable rules, while explicit facts can be pinned with /remember.
    """

    DECISION_CUES = (
        "we decided",
        "we will use",
        "let's use",
        "lets use",
        "from now on",
        "going forward",
        "our plan is",
        "the plan is",
        "we're going with",
        "we are going with",
    )
    PROCEDURAL_CUES = (
        "how do i",
        "how to",
        "walk me through",
        "steps to",
        "step by step",
        "procedure",
        "workflow",
    )
    REMEMBER_CUES = (
        "remember that",
        "remember this",
        "note that",
        "keep in mind",
    )

    def __init__(self, store: MemoryStore, config: dict[str, Any] | None = None):
        self.store = store
        cfg = config or {}
        self.enabled = bool(cfg.get("structured_enabled", True))
        self.retrieve_limit = int(cfg.get("structured_retrieve_limit", 6))
        self.auto_episode = bool(cfg.get("auto_episode", True))
        self.auto_decisions = bool(cfg.get("auto_decisions", True))
        self.auto_procedures = bool(cfg.get("auto_procedures", True))
        self.auto_semantic_cues = bool(cfg.get("auto_semantic_cues", True))
        self.training_threshold = float(cfg.get("training_confidence_threshold", 0.85))
        self.intelligence = MemoryIntelligence(store, cfg)

    @property
    def active_project(self) -> str | None:
        value = self.store.get_state("active_project")
        return str(value) if value else None

    def set_active_project(self, name: str | None) -> None:
        cleaned = name.strip() if name else None
        self.store.set_state("active_project", cleaned or None)

    def remember_explicit(
        self,
        text: str,
        *,
        kind: str = "semantic",
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> int:
        text = text.strip()
        if not text:
            raise ValueError("Memory text cannot be empty")
        memory_id = self.store.add_memory(
            kind,
            title or self._title(text),
            text,
            project=self.active_project,
            tags=tags or ["explicit"],
            confidence=1.0,
            source="user_explicit",
        )
        self.intelligence.on_memory_added(memory_id)
        return memory_id

    def retrieve(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        return self.intelligence.rank(
            query,
            limit=limit or self.retrieve_limit,
            project=self.active_project,
        )

    def context_messages(self, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        items = self.retrieve(query, limit=limit)
        return [
            {
                "role": "memory",
                "content": self._memory_context(item),
                "memory_id": item["id"],
                "memory_kind": item["kind"],
            }
            for item in items
        ]

    def capture_turn(
        self,
        *,
        request: str,
        response: str,
        agent: str,
        plan: dict[str, Any],
        observations: list[dict[str, Any]],
        confidence: float,
        critic_approved: bool,
    ) -> list[int]:
        if not self.enabled:
            return []
        writes: list[MemoryWrite] = []
        project = self.active_project
        request_l = request.lower()

        if self.auto_episode:
            writes.append(
                MemoryWrite(
                    kind="episodic",
                    title=self._title(request),
                    content=f"User: {request}\nDaQauntum: {response}",
                    project=project,
                    tags=[agent, "turn"],
                    confidence=max(0.5, min(1.0, confidence)),
                    source="conversation_turn",
                )
            )

        if self.auto_decisions and any(cue in request_l for cue in self.DECISION_CUES):
            writes.append(
                MemoryWrite(
                    kind="decision",
                    title=self._title(request),
                    content=request,
                    project=project,
                    tags=[agent, "decision"],
                    confidence=0.95,
                    source="user_decision_cue",
                )
            )

        if self.auto_procedures and any(cue in request_l for cue in self.PROCEDURAL_CUES):
            workflow = [str(step.get("description", "")) for step in plan.get("steps", []) if step.get("description")]
            content = request
            if workflow:
                content += "\nWorkflow: " + " -> ".join(workflow)
            writes.append(
                MemoryWrite(
                    kind="procedural",
                    title=self._title(request),
                    content=content,
                    project=project,
                    tags=[agent, "workflow"],
                    confidence=max(0.6, min(1.0, confidence)),
                    source="procedural_cue",
                )
            )

        if self.auto_semantic_cues and any(cue in request_l for cue in self.REMEMBER_CUES):
            fact = self._strip_remember_cue(request)
            writes.append(
                MemoryWrite(
                    kind="semantic",
                    title=self._title(fact),
                    content=fact,
                    project=project,
                    tags=["remembered", agent],
                    confidence=1.0,
                    source="remember_cue",
                )
            )

        if project:
            writes.append(
                MemoryWrite(
                    kind="project",
                    title=f"{project}: {self._title(request)}",
                    content=f"Request: {request}\nOutcome: {self._compact(response, 800)}",
                    project=project,
                    tags=[agent, "project-turn"],
                    confidence=max(0.5, min(1.0, confidence)),
                    source="active_project",
                )
            )

        for obs in observations:
            if obs.get("status") != "executed":
                continue
            if obs.get("tool") not in {"read_project_file", "memory_search"}:
                continue
            output = str(obs.get("output", ""))
            if not output:
                continue
            writes.append(
                MemoryWrite(
                    kind="source",
                    title=f"Source used: {obs.get('tool')}",
                    content=self._compact(output, 1200),
                    project=project,
                    tags=[str(obs.get("tool")), agent],
                    confidence=0.9,
                    source=str(obs.get("tool")),
                )
            )

        if critic_approved and confidence >= self.training_threshold:
            writes.append(
                MemoryWrite(
                    kind="training",
                    title=f"Successful {agent} trace",
                    content=(
                        f"Request: {request}\n"
                        f"Plan: {self._compact(str(plan), 900)}\n"
                        f"Response: {self._compact(response, 1200)}"
                    ),
                    project=project,
                    tags=[agent, "approved-trace"],
                    confidence=confidence,
                    source="successful_turn",
                )
            )

        ids: list[int] = []
        fingerprints: set[tuple[str, str]] = set()
        for write in writes:
            fingerprint = (write.kind, write.content.strip())
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            memory_id = self.store.add_memory(
                write.kind,
                write.title,
                write.content,
                project=write.project,
                tags=write.tags,
                confidence=write.confidence,
                source=write.source,
            )
            self.intelligence.on_memory_added(memory_id)
            ids.append(memory_id)
        self.intelligence.maybe_maintain()
        return ids

    def maintenance(self) -> dict[str, Any]:
        return self.intelligence.maintenance(project=self.active_project)

    def contradictions(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_memory_relations(relation="contradicts", limit=limit)

    def relations(self, memory_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_memory_relations(memory_id=memory_id, limit=limit)

    def stats(self) -> dict[str, Any]:
        result = self.store.memory_stats()
        result["enabled"] = self.enabled
        result["intelligence"] = self.intelligence.stats()
        return result

    @staticmethod
    def _title(text: str, max_len: int = 72) -> str:
        one_line = re.sub(r"\s+", " ", text.strip())
        if len(one_line) <= max_len:
            return one_line or "Memory"
        return one_line[: max_len - 3].rstrip() + "..."

    @staticmethod
    def _compact(text: str, max_len: int) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        return text if len(text) <= max_len else text[: max_len - 3].rstrip() + "..."

    def _strip_remember_cue(self, text: str) -> str:
        lowered = text.lower()
        for cue in self.REMEMBER_CUES:
            idx = lowered.find(cue)
            if idx >= 0:
                remainder = text[idx + len(cue):].lstrip(" :,-")
                return remainder or text
        return text

    @staticmethod
    def _memory_context(item: dict[str, Any]) -> str:
        project = f" project={item['project']}" if item.get("project") else ""
        return (
            f"[structured-memory id={item['id']} kind={item['kind']}{project} "
            f"confidence={item['confidence']:.2f}] {item['title']}: {item['content']}"
        )
