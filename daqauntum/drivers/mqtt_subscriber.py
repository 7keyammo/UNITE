from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Any

from drivers.base import (
    Capability,
    DeviceDriver,
    DriverNotConfigured,
    DriverUnavailable,
    DriverWriteNotPermitted,
)
from drivers.parsers import parse
from events.models import Event


def topic_matches(topic: str, pattern: str) -> bool:
    """MQTT topic filter matching, including '+' and '#' wildcards.

    Implemented here rather than trusted to the broker because the allowlist is
    a local authorization decision: an incoming message on an unapproved topic
    must be dropped even if the broker chose to deliver it.
    """
    topic_parts = str(topic or "").split("/")
    pattern_parts = str(pattern or "").split("/")
    for index, part in enumerate(pattern_parts):
        if part == "#":
            # '#' is only legal as the final level, and per the MQTT spec it
            # matches the parent level too: 'home/#' matches 'home' itself as
            # well as 'home/kitchen/temp'.
            return index == len(pattern_parts) - 1
        if index >= len(topic_parts):
            return False
        if part == "+":
            continue
        if part != topic_parts[index]:
            return False
    return len(topic_parts) == len(pattern_parts)


class MQTTSubscriberDriver(DeviceDriver):
    """Persistent MQTT subscription with a current-state cache.

    v0.4.0 could only read one retained message at a time, which meant DaQauntum
    learned about an IoT change by asking rather than by being told. This keeps
    a subscription open and caches the latest value per topic.

    Subscribing is read-only. Publishing remains a separate, permission-gated
    tool, because a message on the wrong topic can unlock a door.
    """

    name = "mqtt"
    kind = "message_bus"

    def __init__(self, config: dict[str, Any] | None = None, integrations=None):
        super().__init__(config)
        self.integrations = integrations
        self.client_id = str(self.config.get("client_id", "daqauntum"))
        self.keepalive = max(10, int(self.config.get("keepalive_seconds", 60)))
        self.max_cached_topics = max(10, int(self.config.get("max_cached_topics", 200)))
        self.parser_name = str(self.config.get("parser", "line"))
        self.parser_config = dict(self.config.get("parser_config", {}) or {})
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._pending: list[Event] = []
        self._messages_seen = 0
        self._dropped_unapproved = 0

    # Readiness ---------------------------------------------------------------
    def _binary(self) -> str | None:
        return shutil.which(str(self.config.get("sub_binary", "mosquitto_sub")))

    def available(self) -> tuple[bool, str]:
        if not self._binary():
            return False, "Install 'mosquitto-clients' to keep an MQTT subscription open."
        if not self._broker_host():
            return False, "Set MQTT_HOST (or drivers.mqtt.host) to reach a broker."
        return True, ""

    def _broker_host(self) -> str:
        return str(os.getenv("MQTT_HOST", "") or self.config.get("host", "")).strip()

    def _broker_port(self) -> int:
        try:
            return int(os.getenv("MQTT_PORT", "") or self.config.get("port", 1883))
        except (TypeError, ValueError):
            return 1883

    def targets(self) -> list[str]:
        topics = self.config.get("topics") or []
        if isinstance(topics, str):
            topics = [topics]
        return [str(topic).strip() for topic in topics if str(topic).strip()]

    def capabilities(self) -> list[Capability]:
        caps = [Capability(topic, "notify", "Subscribed topic; latest value is cached.") for topic in self.targets()]
        if self.allow_writes:
            caps.append(Capability("publish", "write", "Publish to an allowlisted topic (permission-gated)."))
        return caps

    # Subscription lifecycle --------------------------------------------------
    def _command(self) -> list[str]:
        binary = self._binary()
        if not binary:
            raise DriverUnavailable("mosquitto_sub is not installed")
        host = self._broker_host()
        if not host:
            raise DriverNotConfigured("No MQTT broker host is configured")
        topics = self.targets()
        if not topics:
            raise DriverNotConfigured("No MQTT topics are allowlisted")
        command = [binary, "-h", host, "-p", str(self._broker_port()), "-v", "-k", str(self.keepalive), "-i", self.client_id]
        username = os.getenv("MQTT_USERNAME", "").strip()
        password = os.getenv("MQTT_PASSWORD", "")
        if username:
            command += ["-u", username]
        if password:
            command += ["-P", password]
        for topic in topics:
            command += ["-t", topic]
        return command

    def start(self) -> bool:
        """Open the subscription. Idempotent."""
        self.require_enabled()
        with self._lock:
            if self._process and self._process.poll() is None:
                return True
            command = self._command()
            self._stop.clear()
            try:
                self._process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                self._note_failure(exc)
                raise DriverUnavailable(f"Could not start MQTT subscription: {exc}") from exc
            self._reader = threading.Thread(target=self._read_loop, name="daqauntum-mqtt", daemon=True)
            self._reader.start()
            self._note_success()
            return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            process, self._process = self._process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
        if self._reader and self._reader.is_alive():
            self._reader.join(timeout=3)
        self._reader = None

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process and process.poll() is None)

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if self._stop.is_set():
                    break
                self._handle_line(line.rstrip("\n"))
        except Exception as exc:  # pragma: no cover - defensive
            self._note_failure(exc)
        finally:
            if not self._stop.is_set():
                # The broker or the client went away. Report it rather than
                # leaving the driver looking healthy with a dead subscription.
                self.last_error = "MQTT subscription ended unexpectedly"

    def _handle_line(self, line: str) -> None:
        """Turn one 'topic payload' line into a cached value and an event."""
        if not line.strip():
            return
        topic, _, payload = line.partition(" ")
        topic = topic.strip()
        if not topic:
            return
        approved = self.targets()
        if not any(topic_matches(topic, pattern) for pattern in approved):
            self._dropped_unapproved += 1
            return
        try:
            fields = parse(payload, self.parser_name, config=self.parser_config)
        except ValueError:
            fields = {"value": payload.strip()}
        now = time.time()
        with self._lock:
            self._messages_seen += 1
            self._cache[topic] = {"topic": topic, "payload": payload.strip(), "fields": fields, "at": now}
            if len(self._cache) > self.max_cached_topics:
                oldest = sorted(self._cache.items(), key=lambda item: item[1]["at"])[0][0]
                self._cache.pop(oldest, None)
            self._pending.append(
                self.event(
                    "mqtt_message",
                    topic,
                    message=f"{topic}: {payload.strip()[:120]}",
                    attributes={"topic": topic, "payload": payload.strip(), **fields},
                )
            )
            del self._pending[:-500]
        self._note_success()

    # Reads -------------------------------------------------------------------
    def poll(self) -> list[Event]:
        """Drain messages received since the last poll.

        Messages already received are always drained, even when the
        subscription cannot be restarted: a broker that went away must not
        cost us the readings that arrived before it did.
        """
        if not self.enabled:
            return []
        failure: Event | None = None
        if not self.running:
            try:
                self.start()
            except Exception as exc:
                self._note_failure(exc)
                failure = self.event(
                    "driver_error",
                    "subscription",
                    severity="warning",
                    message=f"MQTT subscription unavailable: {exc}",
                    attributes={"error": str(exc)},
                )
        with self._lock:
            pending, self._pending = self._pending, []
        if failure is not None:
            pending.append(failure)
        return pending

    def state(self, topic: str | None = None) -> dict[str, Any]:
        """Read the cached current value for one or all approved topics."""
        with self._lock:
            if topic:
                cached = self._cache.get(str(topic).strip())
                if not cached:
                    raise DriverNotConfigured(f"No cached value for topic '{topic}' yet")
                return dict(cached)
            return {key: dict(value) for key, value in self._cache.items()}

    def status(self):
        status = super().status()
        status.detail = status.detail or ""
        if self.enabled and self.running:
            status.detail = f"Subscribed to {len(self.targets())} topic filter(s); {len(self._cache)} cached."
            status.healthy = self.last_error is None
        return status

    def stats(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "topics": self.targets(),
            "cached_topics": len(self._cache),
            "messages_seen": self._messages_seen,
            "dropped_unapproved": self._dropped_unapproved,
            "pending": len(self._pending),
        }

    def write(self, target: str, payload: Any, **kwargs: Any) -> str:
        """Publish to an approved topic via the existing MQTT integration."""
        topic = str(target or "").strip()
        self.require_write_permission(topic)
        if self.integrations is None:
            raise DriverUnavailable("MQTT publishing requires the integration manager")
        return self.integrations.mqtt_publish(topic, str(payload), retain=bool(kwargs.get("retain", False)))
