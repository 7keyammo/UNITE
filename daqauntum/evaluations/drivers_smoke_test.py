"""Regression guard for the v0.4.1 device driver SDK.

Everything here runs without hardware. The v0.4.1 definition of done requires
the event and reaction architecture to be testable with hardware-independent
mocks, and requires that no passive event source can directly mutate external
state - both are asserted below.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from drivers import (
    BLEDriver,
    Capability,
    DeviceDriver,
    DriverManager,
    DriverNotConfigured,
    DriverUnavailable,
    DriverWriteNotPermitted,
    HomeAssistantDriver,
    MQTTSubscriberDriver,
    SerialSensorDriver,
    parse,
    topic_matches,
)
from events.models import Event


class MockThermometer(DeviceDriver):
    """A driver with no dependency, used to exercise the whole pipeline."""

    name, kind = "mock_thermo", "sensor"

    def __init__(self, config=None):
        super().__init__(config)
        self.readings = list((config or {}).get("readings", []))
        self.written: list[tuple[str, str]] = []

    def available(self):
        return True, "mock driver, always available"

    def targets(self):
        return ["probe-1"]

    def capabilities(self):
        return [Capability("temperature", "read", "Mock probe")]

    def poll(self):
        if not self.readings:
            return []
        celsius = self.readings.pop(0)
        return [self.event("sensor_reading", "probe-1", message=f"probe-1 at {celsius} C",
                           attributes={"celsius": celsius, "unit": "C"})]

    def write(self, target, payload, **kwargs):
        self.require_write_permission(target)
        self.written.append((target, str(payload)))
        return f"wrote {payload} to {target}"


def test_parsers() -> None:
    assert parse("23.5") == {"value": 23.5, "raw": "23.5"}
    assert parse('{"temp": "23.5", "ok": "true"}', "json") == {"temp": 23.5, "ok": True}
    assert parse("12,34.5,hello", "csv", config={"columns": ["a", "b"]}) == {"a": 12, "b": 34.5, "field_2": "hello"}
    assert parse("temp=23.5 humidity=41 fan=on", "keyvalue") == {"temp": 23.5, "humidity": 41, "fan": True}
    for bad_args in (("x", "no_such_parser"), ("not json", "json")):
        try:
            parse(*bad_args)
        except ValueError:
            continue
        raise AssertionError(f"Bad parser input accepted: {bad_args}")


def test_topic_matching() -> None:
    # Per the MQTT spec '#' also matches the parent level.
    for topic, pattern, expected in (
        ("home/kitchen/temp", "home/kitchen/temp", True),
        ("home/kitchen/temp", "home/+/temp", True),
        ("home/kitchen/temp", "home/#", True),
        ("home", "home/#", True),
        ("home/kitchen/temp", "home/+", False),
        ("home/kitchen/temp", "office/#", False),
        ("home/kitchen/temp", "home/kitchen", False),
    ):
        assert topic_matches(topic, pattern) is expected, (topic, pattern, expected)


def test_readiness_is_honest() -> None:
    """Four separate flags; an unconfigured driver is never shown as working."""
    driver = SerialSensorDriver({"enabled": True, "allow_ports": []})
    status = driver.status().as_dict()
    assert status["enabled"] is True and status["configured"] is False and status["usable"] is False, status

    configured = SerialSensorDriver({"enabled": True, "allow_ports": ["/dev/ttyUSB0"]})
    assert configured.targets() == ["/dev/ttyUSB0"]
    # Whether it is usable depends on pyserial, which is optional by design.
    assert configured.status().as_dict()["configured"] is True

    disabled = SerialSensorDriver({"enabled": False, "allow_ports": ["/dev/ttyUSB0"]})
    assert disabled.status().as_dict()["usable"] is False
    try:
        disabled.read_port("/dev/ttyUSB0")
    except (DriverNotConfigured, DriverUnavailable):
        pass
    else:
        raise AssertionError("a disabled driver performed a read")


def test_writes_are_refused_by_default() -> None:
    """A driver must refuse writes it was never opened for, before any I/O."""
    serial_driver = SerialSensorDriver({"enabled": True, "allow_ports": ["/dev/ttyUSB0"]})
    try:
        serial_driver.write("/dev/ttyUSB0", "ON")
    except DriverWriteNotPermitted:
        pass
    else:
        raise AssertionError("serial write allowed without allow_writes")

    profile = {"AA:BB:CC:DD:EE:FF": {"name": "Thermo", "characteristics": {
        "temp": {"uuid": "0000-1", "mode": "read", "encoding": "uint16"},
        "setpoint": {"uuid": "0000-2", "mode": "write"},
    }}}
    ble = BLEDriver({"enabled": True, "allow_writes": True,
                     "allow_devices": ["aa:bb:cc:dd:ee:ff"], "profiles": profile})
    for target, kwargs, why in (
        ("AA:BB:CC:DD:EE:FF", {"characteristic": "temp"}, "wrote a read-only characteristic"),
        ("AA:BB:CC:DD:EE:FF", {"characteristic": "unknown"}, "wrote an undeclared characteristic"),
        ("11:22:33:44:55:66", {"characteristic": "setpoint"}, "wrote an unallowlisted device"),
    ):
        try:
            ble.write(target, "1", **kwargs)
        except DriverWriteNotPermitted:
            continue
        except DriverUnavailable:
            raise AssertionError(f"{why}: authorization was checked after the dependency")
        raise AssertionError(why)

    assert BLEDriver._decode(bytes([0x2A, 0x00]), {"encoding": "uint16"}) == 42
    assert BLEDriver._decode(b"23.5", {}) == 23.5


def test_discovery_does_not_authorize() -> None:
    """Detected is not the same as approved, paired or controllable."""
    profiles = {"AA:BB:CC:DD:EE:FF": {"characteristics": {"temp": {"uuid": "0000-1", "mode": "read"}}}}
    ble = BLEDriver({"enabled": True, "allow_devices": ["aa:bb:cc:dd:ee:ff"], "profiles": profiles})
    assert ble.targets() == ["AA:BB:CC:DD:EE:FF"]
    try:
        ble.profile_for("11:22:33:44:55:66")
    except DriverNotConfigured:
        pass
    else:
        raise AssertionError("an unmapped device reported a usable profile")

    # An allowlisted device with no declared profile is not readable either.
    unmapped = BLEDriver({"enabled": True, "allow_devices": ["11:22:33:44:55:66"], "profiles": {}})
    assert unmapped.poll() == [], "a device without a GATT profile was read"


def test_mqtt_allowlist_is_enforced_locally() -> None:
    driver = MQTTSubscriberDriver({"enabled": True, "topics": ["home/+/temp"], "parser": "keyvalue"})
    driver._handle_line("home/kitchen/temp temp=21.5 hum=40")
    driver._handle_line("office/private/temp temp=99")
    cached = driver.state()
    assert list(cached) == ["home/kitchen/temp"], cached
    assert cached["home/kitchen/temp"]["fields"] == {"temp": 21.5, "hum": 40}
    stats = driver.stats()
    assert stats["messages_seen"] == 1 and stats["dropped_unapproved"] == 1, stats

    events = driver.poll()
    reading = next(e for e in events if e.kind == "mqtt_message")
    assert reading.attributes["temp"] == 21.5 and reading.source == "driver"

    try:
        driver.write("home/kitchen/set", "on")
    except DriverWriteNotPermitted:
        pass
    else:
        raise AssertionError("MQTT publish allowed without allow_writes")


def test_home_assistant_degrades_visibly() -> None:
    driver = HomeAssistantDriver({"enabled": True, "entities": ["binary_sensor.front_door"]})
    assert driver._severity_for("binary_sensor.front_door", "on") == "notice"
    assert driver._severity_for("alarm_control_panel.home", "triggered") == "warning"
    assert driver._severity_for("sensor.temp", "21") == "info"
    # Unconfigured credentials must produce a reported failure, not silence.
    events = driver.poll()
    assert len(events) == 1 and events[0].kind == "driver_error" and events[0].severity == "warning", events


def test_pipeline_end_to_end(root: Path) -> None:
    """Mock driver -> bus suppression -> deterministic rule -> notification."""
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.1-drivers-test",
        "permission_level": 2,
        "models": {
            "roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}},
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "fallback_to_mock": True,
        },
        "memory": {"db_path": str(root / "drivers.db")},
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "sources": {"project_root": str(root)},
        "learning": {"project_root": str(root)},
        "events": {"bus": {"debounce_seconds": 0, "per_kind": {}}},
        "drivers": {"enabled": True, "poll_interval_seconds": 5},
    }
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    kernel = DaQauntumKernel(str(path))

    assert set(kernel.drivers.stats()["registered"]) == {"serial", "ble", "mqtt", "home_assistant"}
    assert kernel.drivers.stats()["usable"] == [], "a driver claimed to be usable with no configuration"
    assert kernel.drivers.start() is False, "the poll loop started with no usable driver"

    mock = kernel.drivers.register(MockThermometer({"enabled": True, "readings": [-18, -18, 2, 12]}))
    kernel.events.create_rule(
        name="freezer-too-warm", match_kind="sensor_reading",
        conditions=[{"path": "attributes.celsius", "op": "gt", "value": 8}],
        action={"type": "notify", "title": "Freezer warm: {{attributes.celsius}} C", "severity": "warning"},
        cooldown_seconds=0,
    )
    assert kernel.drivers.stats()["usable"] == ["mock_thermo"]

    published = suppressed = 0
    for _ in range(4):
        result = kernel.drivers.poll_once()
        published += result["published"]
        suppressed += result["suppressed"]
    assert (published, suppressed) == (3, 1), (published, suppressed)

    titles = [n["title"] for n in kernel.events.notifications.list(status="pending")]
    assert "Freezer warm: 12 C" in titles, titles
    assert kernel.events.reactions.stats()["proposals_executed"] == 0

    # Driver reads are L0; writing is L3 and refused until approved.
    assert kernel.tools.execute("driver_status", {}).ok
    blocked = kernel.tools.execute("driver_write", {"driver": "mock_thermo", "target": "probe-1", "payload": "x"})
    assert not blocked.ok and blocked.output.startswith("APPROVAL_REQUIRED"), blocked
    assert mock.written == [], "a write happened before approval"

    # Approved by the user, but the driver itself was never opened for writing.
    still_blocked = kernel.tools.execute(
        "driver_write", {"driver": "mock_thermo", "target": "probe-1", "payload": "x"}, approved=True
    )
    assert not still_blocked.ok and "allow_writes" in still_blocked.output, still_blocked
    assert mock.written == [], "the driver allowlist was bypassed by user approval"

    # Both gates agreeing is what it takes.
    mock.allow_writes = True
    allowed = kernel.tools.execute(
        "driver_write", {"driver": "mock_thermo", "target": "probe-1", "payload": "calibrate"}, approved=True
    )
    assert allowed.ok and mock.written == [("probe-1", "calibrate")], (allowed, mock.written)

    discovery = kernel.tools.execute("driver_discover", {"driver": "serial"})
    assert not discovery.ok or "not approved" in discovery.output or "items" in discovery.output


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_parsers()
        test_topic_matching()
        test_readiness_is_honest()
        test_writes_are_refused_by_default()
        test_discovery_does_not_authorize()
        test_mqtt_allowlist_is_enforced_locally()
        test_home_assistant_degrades_visibly()
        test_pipeline_end_to_end(root)
        print("DaQauntum v0.4.1 device drivers smoke test: PASS")


if __name__ == "__main__":
    main()
