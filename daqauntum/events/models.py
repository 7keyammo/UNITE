from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any


# Ordered from least to most urgent. Severity never grants authority; it only
# affects presentation, retention and reaction matching.
SEVERITIES: tuple[str, ...] = ("debug", "info", "notice", "warning", "critical")

SEVERITY_RANK: dict[str, int] = {name: index for index, name in enumerate(SEVERITIES)}

# An event source declares where an observation came from. Sources observe;
# they never act. Anything that changes external state must travel back through
# the tool registry and PermissionManager.
KNOWN_SOURCES: tuple[str, ...] = (
    "presence",
    "driver",
    "mqtt",
    "home_assistant",
    "connector",
    "runtime",
    "user",
    "test",
)

_SLUG_RE = re.compile(r"[^a-z0-9_.:\-/]+")


def normalize_severity(value: Any, default: str = "info") -> str:
    """Coerce arbitrary input into a known severity without raising."""
    text = str(value or "").strip().lower()
    if text in SEVERITY_RANK:
        return text
    aliases = {
        "warn": "warning",
        "err": "critical",
        "error": "critical",
        "fatal": "critical",
        "alert": "warning",
        "trace": "debug",
        "verbose": "debug",
        "information": "info",
    }
    return aliases.get(text, default if default in SEVERITY_RANK else "info")


def severity_at_least(severity: str, minimum: str) -> bool:
    return SEVERITY_RANK.get(normalize_severity(severity), 1) >= SEVERITY_RANK.get(normalize_severity(minimum), 0)


def _slug(value: Any, fallback: str = "unknown") -> str:
    text = _SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return text or fallback


@dataclass(frozen=True)
class Event:
    """A normalized observation from an approved source.

    An Event is evidence that something was observed. It is deliberately inert:
    it carries no capability, no authority and no callback. The Reaction Engine
    may respond to it, but every response is either advisory (notify / queue a
    task) or a proposal that must still pass the permission gate.
    """

    source: str
    kind: str
    subject: str = "default"
    severity: str = "info"
    message: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    occurred_at: float = field(default_factory=time.time)
    dedupe_key: str | None = None
    event_id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _slug(self.source, "unknown"))
        object.__setattr__(self, "kind", _slug(self.kind, "unspecified"))
        object.__setattr__(self, "subject", _slug(self.subject, "default"))
        object.__setattr__(self, "severity", normalize_severity(self.severity))
        object.__setattr__(self, "message", str(self.message or ""))
        attributes = self.attributes if isinstance(self.attributes, dict) else {}
        object.__setattr__(self, "attributes", dict(attributes))
        try:
            object.__setattr__(self, "occurred_at", float(self.occurred_at))
        except (TypeError, ValueError):
            object.__setattr__(self, "occurred_at", time.time())
        if not self.dedupe_key:
            object.__setattr__(self, "dedupe_key", f"{self.source}:{self.kind}:{self.subject}")
        else:
            object.__setattr__(self, "dedupe_key", str(self.dedupe_key))

    @property
    def state_fingerprint(self) -> str:
        """Stable hash of the observed state.

        Two events with the same dedupe key and the same fingerprint describe
        the same world state, so the second one is a repeat rather than news.
        The message is included because a source may encode meaningful detail
        there even when attributes are identical.
        """
        payload = json.dumps(
            {"message": self.message, "severity": self.severity, "attributes": self.attributes},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, path: str, default: Any = None) -> Any:
        """Read a dotted attribute path, with a few event-level aliases.

        Rule conditions address event data through this single accessor so that
        matching stays deterministic and cannot reach outside the event.
        """
        key = str(path or "").strip()
        if not key:
            return default
        top = {
            "source": self.source,
            "kind": self.kind,
            "subject": self.subject,
            "severity": self.severity,
            "message": self.message,
            "occurred_at": self.occurred_at,
        }
        if key in top:
            return top[key]
        if key.startswith("attributes."):
            key = key[len("attributes.") :]
        current: Any = self.attributes
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
                continue
            if isinstance(current, (list, tuple)):
                try:
                    current = current[int(part)]
                    continue
                except (ValueError, IndexError):
                    return default
            return default
        return current

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "source": self.source,
            "kind": self.kind,
            "subject": self.subject,
            "severity": self.severity,
            "message": self.message,
            "attributes": dict(self.attributes),
            "occurred_at": self.occurred_at,
            "dedupe_key": self.dedupe_key,
        }

    def with_id(self, event_id: int | None) -> "Event":
        return Event(
            source=self.source,
            kind=self.kind,
            subject=self.subject,
            severity=self.severity,
            message=self.message,
            attributes=dict(self.attributes),
            occurred_at=self.occurred_at,
            dedupe_key=self.dedupe_key,
            event_id=event_id,
        )

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        data = dict(row)
        try:
            attributes = json.loads(data.get("attributes_json") or "{}")
        except (TypeError, ValueError):
            attributes = {}
        return cls(
            source=data.get("source", "unknown"),
            kind=data.get("kind", "unspecified"),
            subject=data.get("subject", "default"),
            severity=data.get("severity", "info"),
            message=data.get("message", ""),
            attributes=attributes if isinstance(attributes, dict) else {},
            occurred_at=data.get("occurred_at", 0.0),
            dedupe_key=data.get("dedupe_key"),
            event_id=data.get("id"),
        )


@dataclass(frozen=True)
class PublishResult:
    """Outcome of offering an event to the bus."""

    event: Event
    accepted: bool
    reason: str
    event_id: int | None = None
    reactions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "event_id": self.event_id,
            "event": self.event.as_dict(),
            "reactions": list(self.reactions),
        }
