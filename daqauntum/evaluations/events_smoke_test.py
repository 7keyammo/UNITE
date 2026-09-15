"""Regression guard for the v0.4.1 event bus, reaction engine and notifications.

The security assertions here are the point of the file: a reaction must never
execute anything, at any permission level, and a passive observation must never
become an autonomous action.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from events.conditions import ConditionError, validate_condition
from events.bus import EventBus
from events.models import Event, normalize_severity, severity_at_least
from events.rules import RuleError, render_template


def _config(root: Path, **overrides) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.1-events-test",
        "permission_level": 2,
        "models": {
            "roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}},
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "fallback_to_mock": True,
        },
        "memory": {"db_path": str(root / "test.db")},
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "sources": {"project_root": str(root)},
        "learning": {"project_root": str(root)},
        "presence": {"enabled": True, "sample_interval_seconds": 60},
        "events": {"bus": {"debounce_seconds": 30}},
    }
    cfg.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_models_and_conditions() -> None:
    event = Event(
        source="Presence", kind="low_battery", subject="BAT0", severity="warn",
        message="Battery is 12%", attributes={"percent": 12, "power_plugged": False},
    )
    assert event.source == "presence" and event.severity == "warning", event
    assert event.dedupe_key == "presence:low_battery:bat0", event.dedupe_key
    assert event.get("attributes.percent") == 12 and event.get("percent") == 12
    assert event.get("nope", "fallback") == "fallback"
    assert severity_at_least("critical", "warning") and not severity_at_least("info", "warning")
    assert normalize_severity("bogus") == "info"

    # A threshold that cannot read a number must not fire.
    unreadable = Event(source="test", kind="k", attributes={"percent": "n/a"})
    condition = validate_condition({"path": "percent", "op": "lt", "value": 15})
    from events.conditions import evaluate_all

    assert evaluate_all(event, [condition]) is True
    assert evaluate_all(unreadable, [condition]) is False

    for bad in ({"op": "lt", "value": 1}, {"path": "x", "op": "no_such_op"}, {"path": "x", "op": "matches", "value": "["}):
        try:
            validate_condition(bad)
        except ConditionError:
            continue
        raise AssertionError(f"Malformed condition accepted: {bad}")

    assert render_template("{{attributes.percent}}% on {{subject}}", event) == "12% on bat0"


def test_bus_suppression(kernel: DaQauntumKernel) -> None:
    bus = kernel.events.bus
    now = time.time()

    def offer(value: int, at: float, kind: str = "test_reading"):
        return kernel.events.publish(
            Event(source="test", kind=kind, subject="probe", severity="warning",
                  message=f"probe at {value}", attributes={"value": value}, occurred_at=at)
        )

    assert offer(12, now).accepted, "first observation should be accepted"
    assert offer(12, now + 1).reason == "duplicate-within-debounce"
    assert offer(11, now + 2).accepted, "a changed value is news"
    assert offer(11, now + 9000).reason == "unchanged-state"
    assert offer(11, now - 5000).reason == "out-of-order"

    stats = bus.stats()
    assert stats["events"] == 2, stats
    assert stats["suppressed_repeats"] == 3, stats
    assert bus.recent(limit=5)[0]["repeat_count"] >= 1

    # The shipped per-kind policy gives low_battery a long cooldown, so a
    # draining battery cannot fire a reaction on every percentage point.
    def battery(percent: int, at: float):
        return kernel.events.publish(
            Event(source="presence", kind="low_battery", subject="bat0", severity="warning",
                  message=f"Battery is {percent}%", attributes={"percent": percent}, occurred_at=at)
        )

    assert battery(12, now).accepted
    assert battery(11, now + 60).reason == "within-cooldown", "per-kind cooldown was not applied"
    assert battery(10, now + 1000).accepted, "cooldown never expired"

    # Severity below the floor is dropped before anything else happens.
    quiet = EventBus(kernel.memory, {"min_severity": "warning", "debounce_seconds": 0})
    assert quiet.publish(Event(source="test", kind="chatter", severity="debug")).reason == "below-min-severity"


def test_rules_and_notifications(kernel: DaQauntumKernel) -> None:
    events = kernel.events
    builtin = {rule["name"] for rule in events.list_rules()}
    assert "low-battery-warning" in builtin, builtin

    rule = events.create_rule(
        name="freezer-warm",
        description="Warn when the freezer probe rises above -5 C.",
        match_kind="sensor_reading",
        conditions=[{"path": "attributes.celsius", "op": "gt", "value": -5}],
        action={"type": "notify", "title": "Freezer at {{attributes.celsius}} C", "body": "{{message}}", "severity": "warning"},
        cooldown_seconds=0,
    )
    result = events.publish(Event(source="driver", kind="sensor_reading", subject="freezer",
                                  message="freezer probe reading", attributes={"celsius": 3}))
    assert result.accepted and result.reactions, result
    assert result.reactions[0]["type"] == "notify"
    pending = events.notifications.list(status="pending")
    assert any(n["title"] == "Freezer at 3 C" for n in pending), pending

    # A rule below its severity floor, outside its scope, or expired must not fire.
    events.reactions.update_rule(rule["id"], cooldown_seconds=3600)
    before = events.reactions.stats()["fires"]
    events.publish(Event(source="driver", kind="sensor_reading", subject="freezer",
                         message="still warm", attributes={"celsius": 4}))
    assert events.reactions.stats()["fires"] == before, "cooldown did not hold"

    expired = events.create_rule(
        name="expired-rule", match_kind="sensor_reading",
        action={"type": "notify", "title": "never"}, cooldown_seconds=0, expires_in_seconds=-1,
    )
    assert expired["expired"] is True
    fires_before = events.reactions.stats()["fires"]
    events.publish(Event(source="driver", kind="sensor_reading", subject="other", attributes={"celsius": 9}))
    assert events.reactions.stats()["fires"] == fires_before, "an expired rule fired"

    for bad in (
        {"name": "bad-action", "action": {"type": "run_shell", "command": "rm -rf /"}},
        {"name": "no-title", "action": {"type": "notify"}},
        {"name": "!!", "action": {"type": "notify", "title": "x"}},
    ):
        try:
            events.create_rule(**bad)
        except RuleError:
            continue
        raise AssertionError(f"Invalid rule accepted: {bad}")

    notification = pending[0]
    assert events.notifications.mark_read(notification["id"])["status"] == "read"
    assert events.notifications.dismiss(notification["id"])["status"] == "dismissed"


def test_reactions_never_execute(root: Path) -> None:
    """The core v0.4.1 invariant, checked at every permission level.

    Even at L4, where the permission gate would allow the call outright, a
    reaction may only record a proposal. An observation is not an instruction.
    """
    for level in (0, 2, 4):
        path = _config(root / f"lvl{level}", permission_level=level)
        kernel = DaQauntumKernel(str(path))
        executed: list[str] = []
        original_execute = kernel.tools.execute
        kernel.tools.execute = lambda name, args, approved=False: (  # type: ignore[assignment]
            executed.append(name) or original_execute(name, args, approved)
        )

        kernel.events.create_rule(
            name=f"propose-{level}", match_kind="serial_attached",
            action={"type": "propose_tool", "tool": "write_note",
                    "arguments": {"name": "devices", "content": "Saw {{subject}}"}},
            cooldown_seconds=0,
        )
        result = kernel.events.publish(Event(source="presence", kind="serial_attached", subject="ttyusb0",
                                             message="Serial device attached"))
        reaction = result.reactions[0]
        proposal = kernel.events.reactions.get_proposal(reaction["proposal_id"])

        assert executed == [], f"L{level}: a reaction executed a tool: {executed}"
        assert reaction["executed"] is False, reaction
        assert kernel.events.reactions.stats()["proposals_executed"] == 0
        assert not (root / f"lvl{level}" / "notes" / "devices.md").exists(), "a proposal wrote a file"
        if level == 0:
            assert proposal["status"] == "denied", proposal
        else:
            assert proposal["status"] == "pending_approval", proposal
            assert proposal["permission_outcome"] in {"confirm", "execute"}, proposal

    # An unknown tool is recorded as denied rather than queued as actionable.
    kernel = DaQauntumKernel(str(_config(root / "unknown", permission_level=4)))
    kernel.events.create_rule(name="ghost-tool", match_kind="x",
                              action={"type": "propose_tool", "tool": "definitely_not_a_tool"}, cooldown_seconds=0)
    kernel.events.publish(Event(source="test", kind="x"))
    ghost = kernel.events.reactions.list_proposals(status="all")[0]
    assert ghost["status"] == "denied" and "Unknown tool" in ghost["permission_reason"], ghost


def test_proposal_approval(root: Path) -> None:
    kernel = DaQauntumKernel(str(_config(root / "approve", permission_level=2)))
    kernel.events.create_rule(
        name="note-device", match_kind="serial_attached",
        action={"type": "propose_tool", "tool": "write_note",
                "arguments": {"name": "devices", "content": "Saw {{subject}}"}},
        cooldown_seconds=0,
    )
    result = kernel.events.publish(Event(source="presence", kind="serial_attached", subject="ttyacm0"))
    proposal_id = result.reactions[0]["proposal_id"]
    note = root / "approve" / "notes" / "devices.md"
    assert not note.exists(), "the proposal ran before approval"

    rejected = kernel.reject_proposal(proposal_id)
    assert rejected["ok"] and not note.exists()
    assert kernel.approve_proposal(proposal_id)["ok"] is False, "a rejected proposal was approved"

    result = kernel.events.publish(Event(source="presence", kind="serial_attached", subject="ttyusb9"))
    approved = kernel.approve_proposal(result.reactions[0]["proposal_id"])
    assert approved["ok"] and note.exists(), approved
    assert kernel.events.reactions.get_proposal(result.reactions[0]["proposal_id"])["status"] == "approved"


def test_queued_tasks(kernel: DaQauntumKernel) -> None:
    kernel.events.create_rule(
        name="queue-on-offline", match_kind="wifi_change",
        conditions=[{"path": "attributes.connected", "op": "falsy"}],
        action={"type": "queue_task", "title": "Reconnect network", "detail": "{{message}}"},
        cooldown_seconds=0,
    )
    result = kernel.events.publish(Event(source="presence", kind="wifi_change", subject="wifi",
                                         message="Wi-Fi changed from home to offline",
                                         attributes={"connected": False}))
    # The built-in network-change rule also matches this event, so look for the
    # queue_task reaction rather than assuming it is the only one.
    queued = [r for r in result.reactions if r["type"] == "queue_task"]
    assert queued and queued[0]["ok"], result.reactions
    tasks = kernel.events.reactions.list_tasks(status="open")
    assert any(task["title"] == "Reconnect network" for task in tasks), tasks
    task_id = tasks[0]["id"]
    assert kernel.events.reactions.set_task_status(task_id, "done")["status"] == "done"


def test_presence_bridge(kernel: DaQauntumKernel) -> None:
    """Presence hands over snapshots it already collected and gains nothing."""
    assert kernel.presence.event_sink is not None, "presence is not bridged to the event bus"
    sample = {
        "timestamp": time.time(),
        "system": {"hostname": "test-host", "battery_percent": 9, "power_plugged": False,
                   "temperatures": [{"sensor": "cpu", "celsius": 93.0}]},
        "network": {"wifi": {"connected": True, "connection": "lab"}},
        "sensors": {"serial_devices": ["/dev/ttyUSB0"]},
        "alerts": [
            {"kind": "low_battery", "severity": "warning", "message": "Battery is 9%"},
            {"kind": "high_temperature", "severity": "warning", "message": "cpu is 93.0 C"},
        ],
    }
    results = kernel.events.ingest_presence_sample(sample)
    assert len(results) == 2, results
    kinds = {r.event.kind for r in results}
    assert kinds == {"low_battery", "high_temperature"}, kinds
    battery = next(r for r in results if r.event.kind == "low_battery")
    # Numeric context must survive the bridge so rules compare numbers, not prose.
    assert battery.event.attributes["percent"] == 9
    assert battery.event.attributes["power_plugged"] is False
    thermal = next(r for r in results if r.event.kind == "high_temperature")
    assert thermal.event.attributes["celsius"] == 93.0 and thermal.event.subject == "cpu"

    # A failing sink degrades visibly rather than breaking sampling.
    kernel.presence.event_sink = lambda sample: (_ for _ in ()).throw(RuntimeError("bridge down"))
    snapshot = kernel.presence.passive_snapshot(save=True)
    assert isinstance(snapshot, dict) and "system" in snapshot


def test_tools_and_status(kernel: DaQauntumKernel) -> None:
    names = {tool["name"] for tool in kernel.tools.descriptions()}
    assert {"events_recent", "notifications_pending", "driver_status"} <= names, names
    assert kernel.tools.execute("events_recent", {"limit": 5}).ok
    result = kernel.tools.execute("notifications_pending", {})
    assert result.ok and "have not run" in result.output
    status = kernel.status()
    assert "events" in status and "drivers" in status, sorted(status)


def test_push_adapter() -> None:
    """Outbound push is opt-in, filtered, and does not leak raw event data."""
    import json
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from events.push import WebhookPushAdapter

    # Off unless configured, and only http/https targets are accepted.
    assert WebhookPushAdapter({}).available()[0] is False
    assert WebhookPushAdapter({"enabled": True}).available()[0] is False
    blocked = WebhookPushAdapter({"enabled": True, "url": "file:///etc/passwd"})
    assert blocked.available()[0] is False and "http" in blocked.available()[1]

    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D102 - silence test server logging
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            received.append({"body": json.loads(self.rfile.read(length)), "headers": dict(self.headers)})
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["DAQAUNTUM_TEST_PUSH_TOKEN"] = "test-token-value"
    try:
        with tempfile.TemporaryDirectory() as push_root:
            kernel = DaQauntumKernel(str(_config(
                Path(push_root) / "push",
                events={
                    "bus": {"debounce_seconds": 0},
                    "push": {
                        "enabled": True,
                        "url": f"http://127.0.0.1:{port}/hook",
                        "headers": {"Authorization": "Bearer ${DAQAUNTUM_TEST_PUSH_TOKEN}"},
                        "template": {"title": "{{title}}", "message": "{{body}}"},
                        "min_severity": "warning",
                    },
                },
            )))
            assert "webhook_push" in kernel.events.notifications.adapter_names

            kernel.events.notifications.create(
                "Freezer warm", body="probe at 12 C", severity="warning", source="driver",
                kind="sensor_reading", payload={"device_address": "AA:BB:CC:DD:EE:FF"},
            )
            kernel.events.notifications.create("Routine", body="nothing wrong", severity="info")

            assert len(received) == 1, f"severity filter did not hold: {len(received)} deliveries"
            delivered = received[0]
            assert delivered["body"] == {"title": "Freezer warm", "message": "probe at 12 C"}, delivered["body"]
            assert delivered["headers"].get("Authorization") == "Bearer test-token-value", "env var was not expanded"
            # The originating event's attributes must not travel to a third party.
            assert "AA:BB:CC" not in json.dumps(delivered["body"]), "raw event attributes were pushed"
            assert kernel.events.push.stats()["sent"] == 1
    finally:
        server.shutdown()
        os.environ.pop("DAQAUNTUM_TEST_PUSH_TOKEN", None)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_models_and_conditions()

        kernel = DaQauntumKernel(str(_config(root / "main")))
        test_bus_suppression(kernel)
        test_rules_and_notifications(kernel)
        test_queued_tasks(kernel)
        test_presence_bridge(kernel)
        test_tools_and_status(kernel)

        test_reactions_never_execute(root)
        test_proposal_approval(root)
        test_push_adapter()
        print("DaQauntum v0.4.1 events + reactions smoke test: PASS")


if __name__ == "__main__":
    main()
