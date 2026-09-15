from __future__ import annotations

import os
import time
from typing import Any

from drivers.base import Capability, DeviceDriver, DriverNotConfigured, DriverUnavailable
from events.models import Event


# States that mean "no reading", not "a reading of unknown".
_ABSENT_STATES = {"unknown", "unavailable", "none", ""}


class HomeAssistantDriver(DeviceDriver):
    """Ingest state changes for explicitly listed Home Assistant entities.

    v0.4.0 could send a natural-language command to Assist but could not notice
    anything on its own. This polls the REST state API and turns changes into
    events, so a door opening can reach the reaction engine.

    Reading states is not controlling them. Service calls stay where they were:
    behind the permission-gated home_assistant_command tool.
    """

    name = "home_assistant"
    kind = "home_automation"

    def __init__(self, config: dict[str, Any] | None = None, integrations=None):
        super().__init__(config)
        self.integrations = integrations
        self.max_entities = max(1, int(self.config.get("max_entities", 200)))
        self.timeout_seconds = max(2.0, float(self.config.get("timeout_seconds", 10)))
        self._states: dict[str, dict[str, Any]] = {}
        self._last_fetch_at: float | None = None

    # Credentials come from the existing integration configuration so there is
    # one place to configure Home Assistant, and no second copy of a token.
    def _base_url(self) -> str:
        if self.integrations is not None:
            cfg = self.integrations._cfg("home_assistant")  # noqa: SLF001 - shared adapter config
            base = str(cfg.get("base_url", "") or os.getenv(str(cfg.get("url_env", "HOME_ASSISTANT_URL")), ""))
        else:
            base = os.getenv("HOME_ASSISTANT_URL", "")
        return base.rstrip("/")

    def _token(self) -> str:
        if self.integrations is not None:
            cfg = self.integrations._cfg("home_assistant")  # noqa: SLF001
            return os.getenv(str(cfg.get("token_env", "HOME_ASSISTANT_TOKEN")), "")
        return os.getenv("HOME_ASSISTANT_TOKEN", "")

    def available(self) -> tuple[bool, str]:
        if not self._base_url() or not self._token():
            return False, "Set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN to ingest Home Assistant events."
        return True, ""

    def targets(self) -> list[str]:
        entities = self.config.get("entities") or []
        if isinstance(entities, str):
            entities = [entities]
        return [str(entity).strip() for entity in entities if str(entity).strip()][: self.max_entities]

    def capabilities(self) -> list[Capability]:
        return [Capability(entity, "read", "Entity state ingested into the event bus.") for entity in self.targets()]

    def _fetch_states(self) -> list[dict[str, Any]]:
        base, token = self._base_url(), self._token()
        if not base or not token:
            raise DriverUnavailable("Home Assistant is not configured")
        if self.integrations is not None:
            data = self.integrations._http_json(  # noqa: SLF001 - shared HTTP helper
                base + "/api/states", token=token, timeout=self.timeout_seconds
            )
        else:  # pragma: no cover - integration manager is always present in the kernel
            import json
            import urllib.request

            request = urllib.request.Request(
                base + "/api/states",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, list):
            raise DriverUnavailable("Unexpected Home Assistant response")
        return data

    def discover(self) -> list[dict[str, Any]]:
        """List entities Home Assistant exposes.

        Everything is reported, with 'approved' marking the entities the user
        actually listed. Seeing a lock here does not mean DaQauntum may open it.
        """
        self.require_enabled()
        approved = set(self.targets())
        entities = []
        for item in self._fetch_states()[:1000]:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id", ""))
            entities.append(
                {
                    "entity_id": entity_id,
                    "state": item.get("state"),
                    "friendly_name": (item.get("attributes") or {}).get("friendly_name") or entity_id,
                    "domain": entity_id.split(".", 1)[0] if "." in entity_id else "",
                    "approved": entity_id in approved,
                }
            )
        self._note_success()
        return entities

    @staticmethod
    def _severity_for(entity_id: str, state: Any) -> str:
        """Map a few well-understood domains to a useful severity."""
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        text = str(state).strip().lower()
        if domain in {"binary_sensor"} and text == "on":
            return "notice"
        if domain in {"lock"} and text == "unlocked":
            return "notice"
        if domain in {"alarm_control_panel"} and text.startswith("triggered"):
            return "warning"
        return "info"

    def poll(self) -> list[Event]:
        """Emit an event for each approved entity whose state changed."""
        if not self.enabled:
            return []
        approved = self.targets()
        if not approved:
            return []
        try:
            states = self._fetch_states()
        except Exception as exc:
            self._note_failure(exc)
            return [
                self.event(
                    "driver_error",
                    "home_assistant",
                    severity="warning",
                    message=f"Home Assistant poll failed: {exc}",
                    attributes={"error": str(exc)},
                )
            ]

        wanted = set(approved)
        events: list[Event] = []
        now = time.time()
        for item in states:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id", ""))
            if entity_id not in wanted:
                continue
            state = item.get("state")
            attributes = item.get("attributes") or {}
            previous = self._states.get(entity_id)
            self._states[entity_id] = {"state": state, "at": now}
            if previous is not None and previous.get("state") == state:
                continue
            if str(state).strip().lower() in _ABSENT_STATES:
                continue
            friendly = attributes.get("friendly_name") or entity_id
            events.append(
                self.event(
                    "entity_state_changed",
                    entity_id,
                    severity=self._severity_for(entity_id, state),
                    message=(
                        f"{friendly} is {state}"
                        if previous is None
                        else f"{friendly} changed from {previous.get('state')} to {state}"
                    ),
                    attributes={
                        "entity_id": entity_id,
                        "state": state,
                        "previous_state": (previous or {}).get("state"),
                        "friendly_name": friendly,
                        "domain": entity_id.split(".", 1)[0] if "." in entity_id else "",
                        "unit": attributes.get("unit_of_measurement"),
                        "device_class": attributes.get("device_class"),
                    },
                )
            )
        self._last_fetch_at = now
        self._note_success()
        return events

    def state(self, entity_id: str | None = None) -> dict[str, Any]:
        if entity_id:
            cached = self._states.get(str(entity_id).strip())
            if not cached:
                raise DriverNotConfigured(f"No cached state for '{entity_id}' yet")
            return dict(cached)
        return {key: dict(value) for key, value in self._states.items()}

    def stats(self) -> dict[str, Any]:
        return {
            "entities": self.targets(),
            "cached_entities": len(self._states),
            "last_fetch_at": self._last_fetch_at,
            "configured": bool(self._base_url() and self._token()),
        }
