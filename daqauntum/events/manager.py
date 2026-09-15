from __future__ import annotations

import time
from typing import Any

from events.bus import EventBus
from events.models import Event, PublishResult
from events.notifications import NotificationCenter
from events.rules import ReactionEngine, RuleError


class EventSystem:
    """Facade over the v0.4.1 event pipeline.

    One direction of flow, and only one:

        source -> EventBus (normalize, suppress, persist)
               -> ReactionEngine (deterministic rules)
               -> NotificationCenter / task queue / tool *proposal*

    Nothing in this pipeline can change external state. Sources observe,
    rules advise, and any action a rule proposes is handed back to the
    kernel's permission-gated tool path for the user to decide on.
    """

    def __init__(
        self,
        memory,
        *,
        config: dict[str, Any] | None = None,
        tool_registry=None,
        permission_manager=None,
    ):
        self.memory = memory
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.bus = EventBus(memory, self.config.get("bus", {}))
        self.notifications = NotificationCenter(memory, self.config.get("notifications", {}))
        self.reactions = ReactionEngine(
            memory,
            self.notifications,
            config=self.config.get("reactions", {}),
            tool_registry=tool_registry,
            permission_manager=permission_manager,
        )
        self._last_reactions: list[dict[str, Any]] = []
        self.bus.subscribe(self._on_event, name="reaction-engine")

    # Pipeline ----------------------------------------------------------------
    def _on_event(self, event: Event) -> None:
        self._last_reactions = self.reactions.react(event)

    def publish(self, event: Event | None = None, **kwargs: Any) -> PublishResult:
        """Offer an observation and return what the pipeline did with it."""
        if not self.enabled:
            prepared = event or Event(**kwargs)
            return PublishResult(event=prepared, accepted=False, reason="events-disabled")
        self._last_reactions = []
        result = self.bus.publish(event, **kwargs)
        if not result.accepted:
            return result
        return PublishResult(
            event=result.event,
            accepted=True,
            reason=result.reason,
            event_id=result.event_id,
            reactions=list(self._last_reactions),
        )

    def publish_many(self, events: list[Event]) -> list[PublishResult]:
        return [self.publish(event) for event in events]

    # Presence bridge ---------------------------------------------------------
    def ingest_presence_sample(self, sample: dict[str, Any]) -> list[PublishResult]:
        """Turn an already-collected presence sample into normalized events.

        This reads a snapshot the Presence Layer already produced under its own
        consent rules. It starts no scan, opens no radio and connects to
        nothing; it only gives existing observations a common shape.
        """
        if not self.enabled or not isinstance(sample, dict):
            return []
        occurred_at = float(sample.get("timestamp") or time.time())
        system = sample.get("system") or {}
        host = str(system.get("hostname") or "host").lower()
        results: list[PublishResult] = []

        for alert in sample.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            kind = str(alert.get("kind") or "presence_alert")
            results.append(
                self.publish(
                    Event(
                        source="presence",
                        kind=kind,
                        subject=self._alert_subject(kind, alert, system, host),
                        severity=alert.get("severity") or "info",
                        message=str(alert.get("message") or ""),
                        attributes=self._alert_attributes(kind, alert, sample),
                        occurred_at=occurred_at,
                    )
                )
            )
        return results

    @staticmethod
    def _alert_subject(kind: str, alert: dict[str, Any], system: dict[str, Any], host: str) -> str:
        if kind == "high_temperature":
            message = str(alert.get("message") or "")
            return message.split(" is ")[0].strip().lower() or "thermal"
        if kind in {"serial_attached", "serial_removed"}:
            message = str(alert.get("message") or "")
            return message.rsplit(":", 1)[-1].strip().lower() or "serial"
        if kind == "wifi_change":
            return "wifi"
        if kind == "low_battery":
            return "battery"
        return host or "host"

    @staticmethod
    def _alert_attributes(kind: str, alert: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        """Attach just enough structured context for rules to match on.

        Rule conditions must compare numbers, not parse prose, so the numeric
        readings behind an alert are carried explicitly.
        """
        system = sample.get("system") or {}
        attributes: dict[str, Any] = {"alert_kind": kind}
        if kind == "low_battery":
            attributes["percent"] = system.get("battery_percent")
            attributes["power_plugged"] = bool(system.get("power_plugged", False))
            if system.get("battery_status"):
                attributes["battery_status"] = system.get("battery_status")
        elif kind == "high_temperature":
            temperatures = system.get("temperatures") or []
            hottest = None
            for item in temperatures:
                try:
                    value = float(item.get("celsius"))
                except (TypeError, ValueError):
                    continue
                if hottest is None or value > hottest[0]:
                    hottest = (value, item.get("sensor"))
            if hottest:
                attributes["celsius"], attributes["sensor"] = hottest[0], hottest[1]
        elif kind == "wifi_change":
            wifi = ((sample.get("network") or {}).get("wifi") or {})
            attributes["connected"] = bool(wifi.get("connected"))
            attributes["connection"] = wifi.get("connection")
        elif kind in {"serial_attached", "serial_removed"}:
            attributes["attached"] = kind == "serial_attached"
            attributes["devices"] = (sample.get("sensors") or {}).get("serial_devices") or []
        attributes["host"] = system.get("hostname")
        return attributes

    # Convenience passthroughs ------------------------------------------------
    def recent_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.bus.recent(**kwargs)

    def create_rule(self, **kwargs: Any) -> dict[str, Any]:
        return self.reactions.create_rule(**kwargs)

    def list_rules(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.reactions.list_rules(**kwargs)

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "bus": self.bus.stats(),
            "reactions": self.reactions.stats(),
            "notifications": self.notifications.stats(),
        }

    def overview(self, limit: int = 20) -> dict[str, Any]:
        """Everything the GUI needs for the events panel in one read."""
        return {
            "stats": self.stats(),
            "events": self.recent_events(limit=limit),
            "rules": self.list_rules(),
            "notifications": self.notifications.list(status="pending", limit=limit),
            "tasks": self.reactions.list_tasks(status="open", limit=limit),
            "proposals": self.reactions.list_proposals(status="pending_approval", limit=limit),
        }


__all__ = ["EventSystem", "RuleError"]
