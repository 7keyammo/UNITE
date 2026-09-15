from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any


# Frozen curated-example schema. TASKS.md v0.4.3 asks for this to be pinned
# before curation starts, because a dataset built against a drifting schema
# cannot be compared across runs or reproduced later.
SCHEMA_VERSION = "daqauntum.training.v1"

ROLES = ("system", "user", "assistant")

# Capability buckets, used for stratified splits and for the eval harness so a
# model is never judged only on the categories it happens to be good at.
CATEGORIES: tuple[str, ...] = (
    "teaching",
    "physics",
    "coding",
    "reasoning",
    "tool_use",
    "safety",
    "memory",
    "general",
)

DEFAULT_SYSTEM_PROMPT = (
    "You are DaQauntum. Be accurate, evidence-aware, privacy-conscious, "
    "and explicit about uncertainty."
)

# Credential shapes that must never reach a training corpus. Invariant 8 says
# secrets are references, not durable knowledge; a fine-tuned model that has
# memorised a key will repeat it, and unlike a database row it cannot be
# deleted afterwards.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("bearer_token", re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/-]{24,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("password_assignment", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{8,}")),
    ("wifi_psk", re.compile(r"(?i)\bpsk\s*[:=]\s*\S{8,}")),
)

# Interrupted or truncated model output must not become a training example
# (invariant 11). These are the markers DaQauntum itself writes.
_INTERRUPTION_MARKERS = (
    "[interrupted]",
    "[cancelled]",
    "[truncated]",
    "<interrupted>",
    "APPROVAL_REQUIRED",
    "PERMISSION_DENIED",
)


def find_secrets(text: str) -> list[str]:
    """Return the names of credential patterns present in the text."""
    found = []
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(str(text or "")):
            found.append(name)
    return found


def normalize_text(text: str) -> str:
    """Canonical form used for near-duplicate detection.

    Two examples that differ only in whitespace, punctuation spacing or
    unicode form are the same example for training purposes; keeping both
    inflates the corpus and biases the model toward whatever was duplicated.
    """
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = re.sub(r"```.*?```", " ", text, flags=re.S)  # code blocks vary cosmetically
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def shingles(text: str, size: int = 5) -> set[str]:
    """Word n-grams, for Jaccard near-duplicate comparison."""
    words = normalize_text(text).split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


@dataclass
class TrainingExample:
    """One curated supervised example, pinned to SCHEMA_VERSION."""

    user: str
    assistant: str
    system: str = DEFAULT_SYSTEM_PROMPT
    category: str = "general"
    confidence: float = 1.0
    project: str | None = None
    source: str | None = None
    memory_id: int | None = None
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.user = str(self.user or "").strip()
        self.assistant = str(self.assistant or "").strip()
        self.system = str(self.system or DEFAULT_SYSTEM_PROMPT).strip()
        category = str(self.category or "general").strip().lower()
        self.category = category if category in CATEGORIES else "general"
        try:
            self.confidence = max(0.0, min(float(self.confidence), 1.0))
        except (TypeError, ValueError):
            self.confidence = 0.0

    @property
    def exact_key(self) -> str:
        """Hash of the normalized pair, for exact-duplicate collapse."""
        payload = normalize_text(self.user) + "\x1f" + normalize_text(self.assistant)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def as_record(self) -> dict[str, Any]:
        """Serialized training row. Messages first, provenance alongside."""
        return {
            "schema": SCHEMA_VERSION,
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.user},
                {"role": "assistant", "content": self.assistant},
            ],
            "metadata": {
                "category": self.category,
                "confidence": round(self.confidence, 4),
                "project": self.project,
                "source": self.source,
                "memory_id": self.memory_id,
                "tags": list(self.tags),
                "example_id": self.exact_key[:16],
            },
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "TrainingExample":
        messages = {m.get("role"): m.get("content", "") for m in record.get("messages", [])}
        metadata = record.get("metadata") or {}
        return cls(
            user=messages.get("user", ""),
            assistant=messages.get("assistant", ""),
            system=messages.get("system", DEFAULT_SYSTEM_PROMPT),
            category=metadata.get("category", "general"),
            confidence=metadata.get("confidence", 1.0),
            project=metadata.get("project"),
            source=metadata.get("source"),
            memory_id=metadata.get("memory_id"),
            tags=metadata.get("tags") or [],
        )


@dataclass
class RejectionReason:
    code: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}


def validate_example(
    example: TrainingExample,
    *,
    min_user_chars: int = 8,
    min_assistant_chars: int = 16,
    max_chars: int = 24_000,
    min_confidence: float = 0.0,
) -> list[RejectionReason]:
    """Structural and safety gates. An example failing any gate is dropped.

    These are deliberately mechanical. A model decides nothing here: whether an
    example is fit to train on must be reproducible across runs.
    """
    reasons: list[RejectionReason] = []
    if len(example.user) < min_user_chars:
        reasons.append(RejectionReason("user_too_short", f"{len(example.user)} chars"))
    if len(example.assistant) < min_assistant_chars:
        reasons.append(RejectionReason("assistant_too_short", f"{len(example.assistant)} chars"))
    if len(example.user) + len(example.assistant) > max_chars:
        reasons.append(RejectionReason("too_long", f"{len(example.user) + len(example.assistant)} chars"))
    if example.confidence < min_confidence:
        reasons.append(RejectionReason("low_confidence", f"{example.confidence:.2f} < {min_confidence:.2f}"))

    combined = f"{example.user}\n{example.assistant}"
    secrets = find_secrets(combined)
    if secrets:
        # Reported by pattern name only. The matched value is never echoed,
        # because a rejection report is itself written to disk.
        reasons.append(RejectionReason("contains_secret", ", ".join(sorted(set(secrets)))))

    lowered = combined.lower()
    for marker in _INTERRUPTION_MARKERS:
        if marker.lower() in lowered:
            reasons.append(RejectionReason("interrupted_output", marker))
            break

    if normalize_text(example.assistant) == normalize_text(example.user):
        reasons.append(RejectionReason("echo", "assistant repeated the user turn"))
    return reasons


def load_jsonl(path) -> list[TrainingExample]:
    from pathlib import Path

    examples: list[TrainingExample] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            examples.append(TrainingExample.from_record(json.loads(line)))
        except (ValueError, TypeError, KeyError):
            continue
    return examples


def write_jsonl(path, examples: list[TrainingExample]) -> int:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for example in examples:
            handle.write(json.dumps(example.as_record(), ensure_ascii=False) + "\n")
    return len(examples)
