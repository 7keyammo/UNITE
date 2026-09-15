"""DaQauntum v0.4.1 event bus, reaction engine and notification queue.

Sources publish normalized observations; deterministic rules may notify the
user, queue a task, or propose a tool call. No part of this package executes a
tool or changes external state.
"""

from events.bus import EventBus
from events.conditions import ConditionError
from events.manager import EventSystem
from events.models import (
    SEVERITIES,
    Event,
    PublishResult,
    normalize_severity,
    severity_at_least,
)
from events.notifications import NotificationCenter
from events.rules import ReactionEngine, RuleError

__all__ = [
    "SEVERITIES",
    "ConditionError",
    "Event",
    "EventBus",
    "EventSystem",
    "NotificationCenter",
    "PublishResult",
    "ReactionEngine",
    "RuleError",
    "normalize_severity",
    "severity_at_least",
]
