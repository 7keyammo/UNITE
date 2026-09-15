from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from memory.store import MemoryStore


_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "into", "your", "you", "are", "was", "were",
    "will", "would", "should", "could", "have", "has", "had", "our", "their", "they", "them", "then",
    "than", "about", "what", "when", "where", "which", "while", "how", "why", "who", "can", "not", "but",
    "use", "using", "used", "user", "daqauntum", "request", "response", "outcome", "project", "memory",
}
_NEGATION = {"not", "never", "no", "without", "avoid", "deny", "forbid", "forbidden", "cannot", "can't", "won't"}
_CONCEPT_ALIASES = {
    "privacy": {"private", "privacy", "sensitive", "confidential", "credential", "credentials", "password", "secret", "student-record", "records"},
    "local": {"local", "offline", "device", "on-device", "ollama"},
    "model": {"model", "models", "brain", "brains", "inference", "gpt", "claude", "ollama"},
    "permission": {"permission", "permissions", "approval", "authority", "authorize", "allow", "deny", "gate"},
    "memory": {"memory", "memories", "remember", "recall", "retrieve", "retrieval", "episodic", "semantic"},
    "release": {"release", "version", "archive", "zip", "package", "packaging", "repository", "repo"},
    "test": {"test", "tests", "testing", "verify", "verification", "validate", "validation", "smoke"},
    "education": {"teacher", "teaching", "lesson", "classroom", "student", "students", "curriculum"},
    "physics": {"physics", "force", "motion", "velocity", "acceleration", "energy", "momentum"},
    "research": {"research", "hypothesis", "experiment", "analysis", "evidence", "data", "simulation"},
}


@dataclass
class RetrievalScore:
    memory_id: int
    lexical: float
    semantic: float
    salience: float
    recency: float
    confidence: float
    project_bonus: float
    total: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "lexical": round(self.lexical, 4),
            "semantic": round(self.semantic, 4),
            "salience": round(self.salience, 4),
            "recency": round(self.recency, 4),
            "confidence": round(self.confidence, 4),
            "project_bonus": round(self.project_bonus, 4),
            "total": round(self.total, 4),
        }


class LocalSemanticEncoder:
    """Dependency-free local feature hashing for semantic-ish retrieval.

    This is intentionally modest: it is not a neural embedding model. It gives
    DaQauntum a private, deterministic similarity layer that works offline and can
    later be replaced by a true local embedding model without changing the store API.
    """

    def __init__(self, dimensions: int = 256):
        self.dimensions = max(64, int(dimensions))

    def encode(self, text: str) -> list[float]:
        tokens = self._tokens(text)
        if not tokens:
            return [0.0] * self.dimensions
        features: Counter[str] = Counter(tokens)
        # Add adjacent token pairs. These help distinguish related phrases without
        # shipping text to a hosted embedding service.
        features.update(f"{a}_{b}" for a, b in zip(tokens, tokens[1:]))
        for token in tokens:
            if len(token) >= 5 and not token.startswith("concept:"):
                padded = f"^{token}$"
                features.update(f"char:{padded[i:i+3]}" for i in range(len(padded) - 2))
        vector = [0.0] * self.dimensions
        for feature, count in features.items():
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "big")
            index = raw % self.dimensions
            sign = -1.0 if ((raw >> 8) & 1) else 1.0
            vector[index] += sign * (1.0 + math.log(float(count)))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    @staticmethod
    def cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        return max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))

    @staticmethod
    def _tokens(text: str) -> list[str]:
        raw = re.findall(r"[a-z0-9][a-z0-9_'-]*", text.lower())
        tokens: list[str] = []
        for token in raw:
            if len(token) < 3 or token in _STOPWORDS:
                continue
            # Tiny normalization to improve matching while remaining deterministic.
            if token.endswith("ies") and len(token) > 5:
                token = token[:-3] + "y"
            elif token.endswith("ing") and len(token) > 6:
                token = token[:-3]
            elif token.endswith("ed") and len(token) > 5:
                token = token[:-2]
            elif token.endswith("s") and len(token) > 4:
                token = token[:-1]
            tokens.append(token)
        token_set = set(tokens)
        concepts = [f"concept:{name}" for name, aliases in _CONCEPT_ALIASES.items() if token_set & aliases]
        return (tokens + concepts)[:256]


class MemoryIntelligence:
    """Local ranking, maintenance, contradictions, and consolidation for memory."""

    def __init__(self, store: MemoryStore, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.store = store
        self.enabled = bool(cfg.get("intelligence_enabled", True))
        self.encoder = LocalSemanticEncoder(int(cfg.get("embedding_dimensions", 256)))
        self.semantic_weight = float(cfg.get("semantic_weight", 2.2))
        self.lexical_weight = float(cfg.get("lexical_weight", 1.5))
        self.salience_weight = float(cfg.get("salience_weight", 1.1))
        self.recency_weight = float(cfg.get("recency_weight", 0.7))
        self.consolidation_threshold = float(cfg.get("consolidation_similarity", 0.74))
        self.consolidation_min_cluster = int(cfg.get("consolidation_min_cluster", 3))
        self.contradiction_threshold = float(cfg.get("contradiction_similarity", 0.58))
        self.auto_maintain_every = max(0, int(cfg.get("auto_maintain_every", 20)))
        self._ensure_backfill()

    def rank(
        self,
        query: str,
        *,
        limit: int = 8,
        project: str | None = None,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            return self.store.search_memories(query, limit=limit, project=project, kinds=kinds)
        candidates = self.store.list_memories(kind=None, project=None, limit=500, active_only=True)
        if kinds:
            allowed = set(kinds)
            candidates = [item for item in candidates if item["kind"] in allowed]
        query_tokens = set(self.encoder._tokens(query))
        qvec = self.encoder.encode(query)
        now = datetime.now(timezone.utc)
        scored: list[tuple[float, dict[str, Any], RetrievalScore]] = []
        for item in candidates:
            if project and item.get("project") not in {None, project}:
                continue
            embedding = self.store.get_memory_embedding(item["id"])
            if embedding is None:
                embedding = self.encoder.encode(self._search_text(item))
                self.store.set_memory_embedding(item["id"], embedding)
            semantic = max(0.0, self.encoder.cosine(qvec, embedding))
            hay_tokens = set(self.encoder._tokens(self._search_text(item)))
            lexical = (len(query_tokens & hay_tokens) / max(1, len(query_tokens))) if query_tokens else 0.0
            metrics = self.store.get_memory_metrics(item["id"])
            salience = float(metrics.get("salience", self._initial_salience(item)))
            recency = self._recency(item.get("created_at"), item["kind"], now)
            confidence = float(item.get("confidence", 1.0))
            project_bonus = 0.55 if project and item.get("project") == project else 0.0
            kind_bonus = {
                "decision": 0.45,
                "semantic": 0.4,
                "project": 0.35,
                "procedural": 0.3,
                "source": 0.15,
                "episodic": 0.05,
                "training": 0.0,
            }.get(item["kind"], 0.0)
            total = (
                lexical * self.lexical_weight
                + semantic * self.semantic_weight
                + salience * self.salience_weight
                + recency * self.recency_weight
                + confidence * 0.55
                + project_bonus
                + kind_bonus
            )
            detail = RetrievalScore(
                item["id"], lexical, semantic, salience, recency, confidence, project_bonus, total
            )
            enriched = dict(item)
            enriched["retrieval"] = detail.as_dict()
            scored.append((total, enriched, detail))
        scored.sort(key=lambda row: row[0], reverse=True)
        selected = [item for _, item, _ in scored[:limit]]
        for item in selected:
            self.store.touch_memory(item["id"])
        return selected

    def on_memory_added(self, memory_id: int) -> list[dict[str, Any]]:
        item = self.store.get_memory(memory_id)
        if not item:
            return []
        self.store.ensure_memory_metrics(memory_id, salience=self._initial_salience(item))
        self.store.set_memory_embedding(memory_id, self.encoder.encode(self._search_text(item)))
        return self.detect_contradictions(memory_id)

    def detect_contradictions(self, memory_id: int) -> list[dict[str, Any]]:
        item = self.store.get_memory(memory_id)
        if not item or item["kind"] not in {"semantic", "decision", "project"}:
            return []
        item_text = self._search_text(item)
        item_vec = self.store.get_memory_embedding(memory_id) or self.encoder.encode(item_text)
        item_neg = self._negated(item_text)
        relations: list[dict[str, Any]] = []
        for other in self.store.list_memories(limit=500, active_only=True):
            if other["id"] == memory_id or other["kind"] not in {"semantic", "decision", "project"}:
                continue
            if item.get("project") and other.get("project") not in {None, item.get("project")}:
                continue
            other_text = self._search_text(other)
            other_vec = self.store.get_memory_embedding(other["id"])
            if other_vec is None:
                other_vec = self.encoder.encode(other_text)
                self.store.set_memory_embedding(other["id"], other_vec)
            similarity = max(0.0, self.encoder.cosine(item_vec, other_vec))
            if similarity < self.contradiction_threshold:
                continue
            other_neg = self._negated(other_text)
            opposite = item_neg != other_neg
            assignment_conflict = self._assignment_conflict(item_text, other_text)
            if not (opposite or assignment_conflict):
                continue
            note = "opposite polarity" if opposite else "conflicting assignment"
            self.store.add_memory_relation(memory_id, other["id"], "contradicts", similarity, note)
            relations.append({"memory_id": other["id"], "relation": "contradicts", "score": similarity, "note": note})
        return relations

    def consolidate(self, *, project: str | None = None, limit: int = 300) -> dict[str, Any]:
        candidates = [
            item for item in self.store.list_memories(limit=limit, active_only=True)
            if item["kind"] in {"episodic", "project", "procedural"}
            and (not project or item.get("project") == project)
            and "consolidated" not in item.get("tags", [])
        ]
        if len(candidates) < self.consolidation_min_cluster:
            return {"clusters": 0, "created": [], "relations": 0}
        vectors: dict[int, list[float]] = {}
        for item in candidates:
            vec = self.store.get_memory_embedding(item["id"])
            if vec is None:
                vec = self.encoder.encode(self._search_text(item))
                self.store.set_memory_embedding(item["id"], vec)
            vectors[item["id"]] = vec
        neighbors: dict[int, set[int]] = defaultdict(set)
        for i, item in enumerate(candidates):
            for other in candidates[i + 1:]:
                if item["kind"] != other["kind"] or item.get("project") != other.get("project"):
                    continue
                similarity = max(0.0, self.encoder.cosine(vectors[item["id"]], vectors[other["id"]]))
                if similarity >= self.consolidation_threshold:
                    neighbors[item["id"]].add(other["id"])
                    neighbors[other["id"]].add(item["id"])
        by_id = {item["id"]: item for item in candidates}
        seen: set[int] = set()
        created: list[int] = []
        relation_count = 0
        clusters = 0
        for root in by_id:
            if root in seen or root not in neighbors:
                continue
            stack = [root]
            component: set[int] = set()
            while stack:
                current = stack.pop()
                if current in component:
                    continue
                component.add(current)
                stack.extend(neighbors.get(current, ()))
            seen.update(component)
            if len(component) < self.consolidation_min_cluster:
                continue
            signature = ",".join(str(i) for i in sorted(component))
            if self.store.get_state(f"consolidated:{hashlib.sha256(signature.encode()).hexdigest()[:16]}"):
                continue
            members = [by_id[i] for i in sorted(component)]
            project_name = members[0].get("project")
            kind = members[0]["kind"]
            snippets = [self._compact(member["content"], 260) for member in members[:8]]
            content = (
                f"Consolidated pattern from {len(members)} {kind} memories.\n"
                + "\n".join(f"- {snippet}" for snippet in snippets)
            )
            new_id = self.store.add_memory(
                "semantic",
                f"Consolidated {kind} pattern: {members[0]['title']}",
                content,
                project=project_name,
                tags=["consolidated", kind, "pattern"],
                confidence=min(0.95, sum(float(m["confidence"]) for m in members) / len(members)),
                source="memory_consolidation",
            )
            self.on_memory_added(new_id)
            for member in members:
                self.store.add_memory_relation(member["id"], new_id, "consolidated_into", 1.0, "cluster consolidation")
                relation_count += 1
            self.store.set_state(f"consolidated:{hashlib.sha256(signature.encode()).hexdigest()[:16]}", new_id)
            created.append(new_id)
            clusters += 1
        return {"clusters": clusters, "created": created, "relations": relation_count}

    def maintenance(self, *, project: str | None = None) -> dict[str, Any]:
        backfilled = self._ensure_backfill()
        salience_updates = self.age_salience()
        consolidation = self.consolidate(project=project)
        contradictions = self.store.list_memory_relations(relation="contradicts", limit=100)
        return {
            "backfilled": backfilled,
            "salience_updates": salience_updates,
            "consolidation": consolidation,
            "contradictions": len(contradictions),
        }

    def age_salience(self) -> int:
        """Recalculate salience from memory type, age, confidence, and retrieval use.

        Durable decision/semantic memories decay slowly; episodic/training/source
        memories fade faster unless they continue being retrieved. Nothing is deleted.
        """
        now = datetime.now(timezone.utc)
        updated = 0
        durable = {"decision", "semantic", "procedural"}
        for item in self.store.list_memories(limit=5000, active_only=True):
            metrics = self.store.get_memory_metrics(item["id"])
            access_count = int(metrics.get("access_count", 0))
            base = self._initial_salience(item)
            recency = self._recency(item.get("created_at"), item["kind"], now)
            floor = 0.78 if item["kind"] in durable else 0.28
            access_bonus = min(0.18, math.log1p(access_count) * 0.045)
            target = min(1.0, base * (floor + (1.0 - floor) * recency) + access_bonus)
            old = float(metrics.get("salience", base)) if metrics else base
            if abs(target - old) >= 0.005:
                self.store.set_memory_salience(item["id"], target)
                updated += 1
        return updated

    def maybe_maintain(self) -> dict[str, Any] | None:
        if not self.enabled or self.auto_maintain_every <= 0:
            return None
        count = self.store.memory_stats().get("structured_total", 0)
        last = int(self.store.get_state("memory_intelligence_last_maintenance", 0) or 0)
        if count - last < self.auto_maintain_every:
            return None
        result = self.maintenance()
        self.store.set_state("memory_intelligence_last_maintenance", count)
        return result

    def stats(self) -> dict[str, Any]:
        metrics = self.store.memory_intelligence_stats()
        metrics.update({
            "enabled": self.enabled,
            "dimensions": self.encoder.dimensions,
            "auto_maintain_every": self.auto_maintain_every,
        })
        return metrics

    def _ensure_backfill(self) -> int:
        if not self.enabled:
            return 0
        count = 0
        for item in self.store.list_memories(limit=5000, active_only=True):
            metrics = self.store.get_memory_metrics(item["id"])
            if not metrics:
                self.store.ensure_memory_metrics(item["id"], salience=self._initial_salience(item))
                count += 1
            if self.store.get_memory_embedding(item["id"]) is None:
                self.store.set_memory_embedding(item["id"], self.encoder.encode(self._search_text(item)))
        return count

    @staticmethod
    def _search_text(item: dict[str, Any]) -> str:
        return f"{item.get('title','')} {item.get('content','')} {' '.join(item.get('tags', []))}"

    @staticmethod
    def _initial_salience(item: dict[str, Any]) -> float:
        base = {
            "decision": 0.95,
            "semantic": 0.9,
            "procedural": 0.82,
            "project": 0.75,
            "source": 0.65,
            "episodic": 0.55,
            "training": 0.45,
        }.get(item.get("kind"), 0.5)
        if "explicit" in item.get("tags", []):
            base = max(base, 0.98)
        return min(1.0, base * (0.75 + 0.25 * float(item.get("confidence", 1.0))))

    @staticmethod
    def _recency(created_at: str | None, kind: str, now: datetime) -> float:
        if not created_at:
            return 0.5
        try:
            parsed = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            days = max(0.0, (now - parsed).total_seconds() / 86400.0)
        except ValueError:
            return 0.5
        half_life = {
            "episodic": 30.0,
            "training": 45.0,
            "source": 90.0,
            "project": 180.0,
            "procedural": 365.0,
            "semantic": 730.0,
            "decision": 1095.0,
        }.get(kind, 180.0)
        return math.pow(0.5, days / half_life)

    @staticmethod
    def _negated(text: str) -> bool:
        tokens = set(re.findall(r"[a-z']+", text.lower()))
        return bool(tokens & _NEGATION)

    @staticmethod
    def _assignment_conflict(a: str, b: str) -> bool:
        # Conservative pattern: same left-hand phrase around " is " / " = " with different values.
        pattern = re.compile(r"(.{3,80}?)\s+(?:is|=|should be|must be)\s+([a-z0-9_.-]{2,40})", re.I)
        ma = pattern.search(a)
        mb = pattern.search(b)
        if not ma or not mb:
            return False
        left_a = " ".join(LocalSemanticEncoder._tokens(ma.group(1)))
        left_b = " ".join(LocalSemanticEncoder._tokens(mb.group(1)))
        if not left_a or not left_b or left_a != left_b:
            return False
        return ma.group(2).lower() != mb.group(2).lower()

    @staticmethod
    def _compact(text: str, max_len: int) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        return text if len(text) <= max_len else text[: max_len - 3].rstrip() + "..."
