from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from identity import IdentityError


class DaQauntumHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_cls, *, kernel, web_root: Path):
        super().__init__(server_address, handler_cls)
        self.kernel = kernel
        self.web_root = web_root
        self.daemon_threads = True


# Which scope a remote device needs for each API surface.
#
# A scope controls which surface a device may reach. It never changes what
# DaQauntum is permitted to do: every state-changing call still passes through
# the tool registry and PermissionManager exactly as a local request does.
#
# Reads and writes are mapped separately and deliberately. Sharing one table
# between them would let a GET mapping quietly authorize the POST on the same
# prefix - a read-only phone could then create reaction rules on /api/events.
#
# Anything not listed needs "admin", so a newly added endpoint is closed to
# remote devices until someone opens it on purpose.
READ_SCOPES: tuple[tuple[str, str], ...] = (
    ("/api/devices", "admin"),          # the device roster is sensitive even to read
    ("/api/status", "read"),
    ("/api/models", "read"),
    ("/api/memory", "read"),
    ("/api/runtime", "read"),
    ("/api/universe", "read"),
    ("/api/presence", "read"),
    ("/api/perception", "read"),
    ("/api/learning", "read"),
    ("/api/workspaces", "read"),
    ("/api/workspace", "read"),
    ("/api/workbench", "read"),
    ("/api/connectors", "read"),
    ("/api/integrations", "read"),
    ("/api/events", "read"),
    ("/api/drivers", "read"),
    ("/api/notifications", "read"),
    ("/api/demo", "read"),
    ("/api/calls", "chat"),
    ("/api/call", "chat"),
    ("/api/voice", "chat"),
)

WRITE_SCOPES: tuple[tuple[str, str], ...] = (
    # Enrollment cannot require a token: it is how a device gets its first one.
    # It is protected by the single-use, short-lived code and by rate limiting.
    ("/api/devices/redeem", ""),
    ("/api/devices/rotate-token", "read"),   # a device may refresh its own token
    ("/api/chat", "chat"),
    ("/api/call", "chat"),
    ("/api/voice", "chat"),
    ("/api/realtime", "chat"),
    ("/api/remember", "chat"),
    ("/api/approve", "approve"),
    ("/api/events/proposals/resolve", "approve"),
    # Acknowledging notifications and queued tasks is part of consuming them,
    # so it rides with "read" rather than needing a wider scope.
    ("/api/notifications/status", "read"),
    ("/api/notifications/dismiss-all", "read"),
    ("/api/events/tasks/status", "read"),
)

DEFAULT_SCOPE = "admin"


def _match(path: str, rules: tuple[tuple[str, str], ...]) -> str | None:
    """Longest matching path prefix wins, so specific rules beat general ones."""
    matches = [(prefix, scope) for prefix, scope in rules if path == prefix or path.startswith(prefix + "/")]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def required_scope(path: str, method: str) -> str:
    """Resolve the scope a request needs. Unmapped surfaces require admin."""
    rules = READ_SCOPES if method.upper() in {"GET", "HEAD"} else WRITE_SCOPES
    scope = _match(path, rules)
    return DEFAULT_SCOPE if scope is None else scope


class DaQauntumRequestHandler(BaseHTTPRequestHandler):
    server: DaQauntumHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep the terminal focused on DaQauntum rather than every browser asset request.
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    # Authentication ---------------------------------------------------------
    @property
    def _remote(self) -> str:
        try:
            return str(self.client_address[0])
        except Exception:
            return ""

    def _is_loopback(self) -> bool:
        """True when the request came from this machine.

        Loopback is DaQauntum's private control surface and is trusted by
        default, which is why the server binds to 127.0.0.1 and why remote
        access is expected to arrive over Tailscale rather than an open port.
        This reads the socket's real peer address, never a forwarded header,
        which a remote caller could set at will.
        """
        remote = self._remote
        if remote in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
            return True
        return remote.startswith("127.")

    def _bearer_token(self) -> str:
        header = self.headers.get("Authorization", "") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return (self.headers.get("X-DaQauntum-Token", "") or "").strip()

    def _authorize(self, path: str, method: str) -> bool:
        """Gate one API request. Returns False once a response has been sent."""
        # Always defined, so a handler can read self.auth without guarding.
        # None means "the local trusted surface", not "an authenticated device".
        self.auth = None
        kernel = self.server.kernel
        identity = getattr(kernel, "identity", None)
        scope = required_scope(path, method)
        if scope == "":
            return True  # Enrollment must be reachable without a token.
        if identity is None or not identity.enabled:
            return True

        loopback = self._is_loopback()
        if loopback and not identity.require_auth_for_loopback:
            return True
        if not loopback and not identity.require_auth_for_remote:
            return True

        result = identity.authenticate(self._bearer_token(), remote=self._remote)
        if not result.ok:
            # The caller is told only that authentication failed; the specific
            # reason stays in the audit log so probing reveals nothing.
            self._json(
                {"ok": False, "error": "Authentication required. Enrol this device from the DaQauntum host."},
                HTTPStatus.UNAUTHORIZED,
            )
            return False
        if not result.has_scope(scope):
            identity.audit(
                "scope_denied", device_id=result.device.device_id if result.device else None,
                remote=self._remote, detail=f"{method} {path} needs {scope}",
            )
            self._json(
                {"ok": False, "error": f"This device is not permitted to use {path}.",
                 "required_scope": scope, "device_scopes": result.scopes},
                HTTPStatus.FORBIDDEN,
            )
            return False
        self.auth = result
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            if not self._authorize(parsed.path, "GET"):
                return
            self._handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._json({"ok": False, "error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        if not self._authorize(parsed.path, "POST"):
            return
        try:
            if parsed.path == "/api/voice/transcribe":
                if not self.server.kernel.runtime.module_enabled("voice"):
                    raise ValueError("Voice module is disabled in Runtime Modules")
                audio = self._read_bytes(self.server.kernel.voice.max_audio_bytes)
                result = self.server.kernel.voice.transcribe_wav_bytes(audio)
                self._json({"ok": True, "result": result})
                return
            payload = self._read_json()
            if parsed.path == "/api/chat/stream":
                text = str(payload.get("text", "")).strip()
                if not text:
                    raise ValueError("Message is empty")
                turn = self.server.kernel.realtime.begin("gui_chat")
                self._stream_ndjson(self.server.kernel.process_stream(text, interaction_mode="gui_chat", turn_id=turn.id))
                return
            if parsed.path == "/api/call/turn/stream":
                session_id = str(payload.get("session_id", "")).strip()
                text = str(payload.get("text", "")).strip()
                if not session_id or not text:
                    raise ValueError("session_id and text are required")
                turn = self.server.kernel.realtime.begin("voice_call")
                self._stream_ndjson(self.server.kernel.calls.add_turn_stream(session_id, text, turn_id=turn.id))
                return
            self._handle_api_post(parsed.path, payload)
        except IdentityError as exc:
            # A bad or expired enrollment code, or a throttled caller. This is a
            # refusal, not a server fault, and the message is already safe to
            # show: it never says whether a code exists.
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # local alpha UI: return concise failure, keep server alive
            self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        kernel = self.server.kernel
        if path == "/api/status":
            self._json({"ok": True, "status": kernel.status()})
            return
        if path == "/api/models":
            self._json({"ok": True, "models": kernel.model_matrix()})
            return
        if path == "/api/memory":
            q = (query.get("query") or [""])[0]
            items = kernel.search_structured_memory(q, limit=20) if q else kernel.memory.list_memories(limit=20)
            self._json({"ok": True, "items": items})
            return
        if path == "/api/calls/recent":
            self._json({"ok": True, "calls": kernel.calls.recent(limit=12)})
            return
        if path == "/api/voice/status":
            self._json({"ok": True, "voice": kernel.voice.status(refresh=True)})
            return
        if path == "/api/runtime":
            self._json({"ok": True, "runtime": kernel.runtime.status()})
            return
        if path == "/api/universe":
            self._json({"ok": True, "universe": kernel.universe_snapshot()})
            return
        if path == "/api/connectors":
            self._json({
                "ok": True,
                "connectors": kernel.connectors.list(),
                "stats": kernel.connectors.stats(),
                "device_bridge": dict(kernel.device_bridge_endpoint),
            })
            return
        if path == "/api/integrations":
            self._json({"ok": True, **kernel.integrations.summary()})
            return
        if path == "/api/perception":
            self._json({"ok": True, "perception": kernel.perception.stats(), "frames": kernel.perception.list(limit=30), "computer": kernel.computer.status()})
            return
        if path == "/api/presence":
            self._json({"ok": True, "stats": kernel.presence.stats(), "presence": kernel.presence.latest()})
            return
        if path == "/api/events":
            limit = int((query.get("limit") or ["25"])[0])
            self._json({"ok": True, **kernel.events.overview(limit=limit)})
            return
        if path == "/api/devices":
            self._json({
                "ok": True,
                "devices": kernel.identity.list_devices(),
                "stats": kernel.identity.stats(),
                "auth_events": kernel.identity.recent_auth_events(limit=int((query.get("events") or ["25"])[0])),
            })
            return
        if path == "/api/drivers":
            self._json({"ok": True, "drivers": kernel.drivers.stats()})
            return
        if path == "/api/demo":
            self._json({"ok": True, "demo": kernel.demo.state(), "readiness": kernel.demo.readiness()})
            return
        if path == "/api/perception/image":
            raw_id = (query.get("id") or [""])[0]
            if not raw_id:
                raise ValueError("frame id is required")
            frame = kernel.perception.get(int(raw_id))
            if not frame:
                self._json({"ok": False, "error": "Frame not found"}, HTTPStatus.NOT_FOUND)
                return
            data = Path(frame["file_path"]).read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", frame.get("mime_type") or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/workspaces":
            active = kernel.workspaces.active()
            self._json({"ok": True, "workspaces": kernel.workspaces.list(), "active": active, "stats": kernel.workspaces.stats(), "workbench": kernel.workbench.stats()})
            return
        if path == "/api/workspace":
            workspace_id = (query.get("workspace_id") or [""])[0]
            if not workspace_id:
                raise ValueError("workspace_id is required")
            self._json({"ok": True, "workspace": kernel.workspaces.details(workspace_id), "jobs": kernel.workbench.list(workspace_id, limit=40)})
            return
        if path == "/api/workbench/jobs":
            workspace_id = (query.get("workspace_id") or [""])[0] or None
            self._json({"ok": True, "jobs": kernel.workbench.list(workspace_id, limit=60), "stats": kernel.workbench.stats()})
            return
        if path == "/api/learning":
            self._json({"ok": True, "learning": kernel.learning.stats(), "reports": kernel.learning.list_reports(limit=30), "native_model": kernel.native_model.stats()})
            return
        if path == "/api/learning/report":
            name = (query.get("name") or [""])[0]
            if not name:
                raise ValueError("report name is required")
            text = kernel.learning.read_report(name)
            data = text.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{Path(name).name}"')
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/call":
            session_id = (query.get("session_id") or [""])[0]
            state = kernel.calls.get(session_id)
            if not state:
                self._json({"ok": False, "error": "Call session not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._json({"ok": True, "call": state})
            return
        self._json({"ok": False, "error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)

    def _handle_api_post(self, path: str, payload: dict[str, Any]) -> None:
        kernel = self.server.kernel
        if path == "/api/chat":
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ValueError("Message is empty")
            result = kernel.process(text, interaction_mode="gui_chat")
            self._json({"ok": True, "result": result, "status": kernel.status()})
            return

        if path == "/api/realtime/interrupt":
            turn_id = str(payload.get("turn_id", "")).strip()
            if not turn_id:
                raise ValueError("turn_id is required")
            stopped = kernel.interrupt_realtime(turn_id)
            voice = kernel.voice.stop() if kernel.runtime.module_enabled("voice") else {"ok": True, "stopped": False}
            self._json({"ok": stopped, "turn_id": turn_id, "voice": voice})
            return

        if path == "/api/voice/stop":
            result = kernel.voice.stop()
            self._json({"ok": True, "result": result})
            return

        if path == "/api/approve":
            approval_id = str(payload.get("approval_id", "")).strip()
            if not approval_id:
                raise ValueError("approval_id is required")
            result = kernel.approve(approval_id)
            kernel.calls.mark_approval(approval_id, bool(result.get("ok")), str(result.get("message", "")))
            self._json({"ok": bool(result.get("ok")), "result": result, "status": kernel.status()})
            return

        if path == "/api/project":
            project = str(payload.get("project", "")).strip() or None
            active = kernel.set_active_project(project)
            self._json({"ok": True, "project": active, "status": kernel.status()})
            return

        if path == "/api/runtime/modes":
            state = kernel.set_runtime_modes(
                operation_mode=payload.get("operation_mode"),
                cognition_mode=payload.get("cognition_mode"),
            )
            self._json({"ok": True, "runtime": state, "status": kernel.status()})
            return

        if path == "/api/runtime/module":
            name = str(payload.get("name", "")).strip()
            if not name:
                raise ValueError("module name is required")
            state = kernel.set_runtime_module(name, bool(payload.get("enabled", True)))
            self._json({"ok": True, "runtime": state, "status": kernel.status()})
            return

        if path == "/api/remember":
            text = str(payload.get("text", "")).strip()
            kind = str(payload.get("kind", "semantic")).strip() or "semantic"
            if not text:
                raise ValueError("Memory text is empty")
            item = kernel.remember(text, kind=kind)
            self._json({"ok": True, "memory": item})
            return

        if path == "/api/perception/frame":
            data_url = str(payload.get("data_url", "")).strip()
            if not data_url:
                raise ValueError("data_url is required")
            frame = kernel.perception.add_data_url(
                data_url,
                frame_type=str(payload.get("frame_type", "screen")),
                label=str(payload.get("label", "")).strip() or None,
                metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            )
            self._json({"ok": True, "frame": frame, "perception": kernel.perception.stats()})
            return

        if path == "/api/perception/analyze":
            raw_id = payload.get("frame_id") or (kernel.perception.latest() or {}).get("id")
            if not raw_id:
                raise ValueError("No perception frame is available")
            result = kernel.perception.analyze(
                int(raw_id),
                str(payload.get("prompt", "Describe what is visible and what matters for the user's task.")),
                operation_mode=kernel.runtime.operation_mode,
            )
            self._json({"ok": True, "result": result, "perception": kernel.perception.stats()})
            return

        if path == "/api/presence/refresh":
            data = kernel.presence.passive_snapshot(save=True)
            self._json({"ok": True, "presence": data, "stats": kernel.presence.stats()})
            return

        if path == "/api/presence/wifi-scan":
            self._json({"ok": True, "networks": kernel.presence.scan_wifi()})
            return

        if path == "/api/presence/bluetooth-scan":
            self._json({"ok": True, "devices": kernel.presence.scan_bluetooth(int(payload.get("seconds", 6)))})
            return

        if path == "/api/presence/service-scan":
            self._json({"ok": True, "services": kernel.presence.discover_services()})
            return

        if path == "/api/presence/wifi-connect":
            profile = str(payload.get("profile", "")).strip()
            if not profile:
                raise ValueError("profile is required")
            result = kernel.tools.execute("wifi_activate_profile", {"profile": profile})
            if result.output.startswith("APPROVAL_REQUIRED"):
                import uuid
                approval_id = uuid.uuid4().hex[:8]
                kernel.pending[approval_id] = {"tool": "wifi_activate_profile", "arguments": {"profile": profile}, "description": "Activate saved Wi-Fi profile: " + profile}
                self._json({"ok": True, "pending_approval": approval_id, "message": result.output})
            else:
                self._json({"ok": result.ok, "result": result.output})
            return

        if path == "/api/presence/bluetooth-connect":
            address = str(payload.get("address", "")).strip()
            if not address:
                raise ValueError("address is required")
            result = kernel.tools.execute("bluetooth_connect", {"address": address})
            if result.output.startswith("APPROVAL_REQUIRED"):
                import uuid
                approval_id = uuid.uuid4().hex[:8]
                kernel.pending[approval_id] = {"tool": "bluetooth_connect", "arguments": {"address": address}, "description": "Connect paired Bluetooth device: " + address}
                self._json({"ok": True, "pending_approval": approval_id, "message": result.output})
            else:
                self._json({"ok": result.ok, "result": result.output})
            return

        if path == "/api/events/publish":
            # Manual publish is a user-authored observation. It enters the same
            # pipeline as any sensor and gains no extra authority from doing so.
            from events.models import Event

            result = kernel.events.publish(
                Event(
                    source="user",
                    kind=str(payload.get("kind", "manual")).strip() or "manual",
                    subject=str(payload.get("subject", "user")).strip() or "user",
                    severity=str(payload.get("severity", "info")),
                    message=str(payload.get("message", "")),
                    attributes=payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {},
                )
            )
            self._json({"ok": True, "result": result.as_dict()})
            return

        if path == "/api/events/rules/create":
            rule = kernel.events.create_rule(
                name=str(payload.get("name", "")),
                description=str(payload.get("description", "")),
                match_source=payload.get("match_source") or None,
                match_kind=payload.get("match_kind") or None,
                match_subject=payload.get("match_subject") or None,
                min_severity=str(payload.get("min_severity", "debug")),
                conditions=payload.get("conditions") or [],
                action=payload.get("action") or {},
                scope=payload.get("scope") or {},
                cooldown_seconds=payload.get("cooldown_seconds"),
                expires_in_seconds=payload.get("expires_in_seconds"),
                enabled=bool(payload.get("enabled", True)),
            )
            self._json({"ok": True, "rule": rule, "rules": kernel.events.list_rules()})
            return

        if path == "/api/events/rules/update":
            rule_id = int(payload.get("rule_id", 0))
            changes = {key: payload[key] for key in ("enabled", "description", "cooldown_seconds", "conditions", "action", "scope") if key in payload}
            rule = kernel.events.reactions.update_rule(rule_id, **changes)
            if rule is None:
                self._json({"ok": False, "error": f"No rule with id {rule_id}"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True, "rule": rule, "rules": kernel.events.list_rules()})
            return

        if path == "/api/events/rules/delete":
            rule_id = int(payload.get("rule_id", 0))
            deleted = kernel.events.reactions.delete_rule(rule_id)
            self._json({"ok": deleted, "rules": kernel.events.list_rules()})
            return

        if path == "/api/notifications/status":
            notification_id = int(payload.get("notification_id", 0))
            status = str(payload.get("status", "read"))
            record = kernel.events.notifications.set_status(notification_id, status)
            if record is None:
                self._json({"ok": False, "error": f"No notification with id {notification_id}"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True, "notification": record, "stats": kernel.events.notifications.stats()})
            return

        if path == "/api/notifications/dismiss-all":
            count = kernel.events.notifications.dismiss_all()
            self._json({"ok": True, "dismissed": count, "stats": kernel.events.notifications.stats()})
            return

        if path == "/api/events/tasks/status":
            task_id = int(payload.get("task_id", 0))
            task = kernel.events.reactions.set_task_status(task_id, str(payload.get("status", "done")))
            if task is None:
                self._json({"ok": False, "error": f"No task with id {task_id}"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True, "task": task})
            return

        if path == "/api/events/proposals/resolve":
            # The only place a reaction's proposed action can ever run, and only
            # because the user explicitly approved this specific proposal.
            proposal_id = int(payload.get("proposal_id", 0))
            decision = str(payload.get("decision", "reject")).strip().lower()
            if decision == "approve":
                result = kernel.approve_proposal(proposal_id)
            else:
                result = kernel.reject_proposal(proposal_id)
            self._json({"ok": bool(result.get("ok")), "result": result, "proposals": kernel.events.reactions.list_proposals(status="pending_approval")})
            return

        if path == "/api/devices/enroll-code":
            # Minting a code is an admin action and, by default, only reachable
            # from loopback: the user creates it on the trusted host and reads
            # it aloud to the phone.
            issued = kernel.identity.create_enrollment_code(
                device_name=str(payload.get("device_name", "")),
                scopes=payload.get("scopes"),
                ttl_seconds=payload.get("ttl_seconds"),
            )
            self._json({"ok": True, "enrollment": issued, "stats": kernel.identity.stats()})
            return

        if path == "/api/devices/redeem":
            # The only endpoint reachable without a token. Protected by the
            # single-use short-lived code and by per-address rate limiting.
            result = kernel.identity.redeem_enrollment_code(
                str(payload.get("code", "")),
                device_name=str(payload.get("device_name", "")),
                platform=str(payload.get("platform", "unknown")),
                remote=self._remote,
            )
            self._json({"ok": True, **result})
            return

        if path == "/api/devices/rotate-token":
            token = self._bearer_token()
            if not token:
                self._json({"ok": False, "error": "Send the current token to rotate it."}, HTTPStatus.UNAUTHORIZED)
                return
            rotated = kernel.identity.rotate_token(token, remote=self._remote)
            self._json({"ok": True, "token": rotated["token"], "expires_at": rotated["expires_at"]})
            return

        if path == "/api/devices/revoke":
            device_id = str(payload.get("device_id", "")).strip()
            if not device_id:
                raise ValueError("device_id is required")
            revoked = kernel.identity.revoke_device(device_id)
            self._json({"ok": revoked, "devices": kernel.identity.list_devices()})
            return

        if path == "/api/devices/update":
            device_id = str(payload.get("device_id", "")).strip()
            if not device_id:
                raise ValueError("device_id is required")
            device = kernel.identity.update_device(
                device_id,
                name=payload.get("name"),
                scopes=payload.get("scopes"),
                note=payload.get("note"),
            )
            if device is None:
                self._json({"ok": False, "error": f"No device {device_id}"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"ok": True, "device": device, "devices": kernel.identity.list_devices()})
            return

        if path == "/api/drivers/poll":
            self._json({"ok": True, "result": kernel.drivers.poll_once(), "drivers": kernel.drivers.stats()})
            return

        if path == "/api/drivers/discover":
            name = str(payload.get("driver", "")).strip()
            if not name:
                raise ValueError("driver is required")
            result = kernel.tools.execute("driver_discover", {"driver": name})
            self._json({"ok": result.ok, "result": result.output})
            return

        if path == "/api/demo/start":
            state = kernel.demo.start()
            if bool(payload.get("speak", False)):
                kernel.demo.speak_current()
            self._json({"ok": True, "demo": state})
            return

        if path == "/api/demo/next":
            state = kernel.demo.next()
            if bool(payload.get("speak", False)) and not state.get("completed"):
                kernel.demo.speak_current()
            self._json({"ok": True, "demo": state})
            return

        if path == "/api/demo/previous":
            state = kernel.demo.previous()
            if bool(payload.get("speak", False)):
                kernel.demo.speak_current()
            self._json({"ok": True, "demo": state})
            return

        if path == "/api/demo/speak":
            self._json({"ok": True, "result": kernel.demo.speak_current(), "demo": kernel.demo.state()})
            return

        if path == "/api/computer/autonomy":
            state = kernel.computer.set_autonomy(str(payload.get("level", "observe")))
            self._json({"ok": True, "computer": state})
            return

        if path == "/api/computer/observe":
            result = kernel.tools.execute("computer_observe", {"request": str(payload.get("request", "Describe the current screen."))})
            self._json({"ok": result.ok, "result": result.output, "computer": kernel.computer.status()})
            return

        if path == "/api/computer/action":
            request = str(payload.get("request", "")).strip()
            if not request:
                raise ValueError("request is required")
            result = kernel.tools.execute("computer_action", {"request": request})
            if result.output.startswith("APPROVAL_REQUIRED"):
                import uuid
                approval_id = uuid.uuid4().hex[:8]
                kernel.pending[approval_id] = {"tool": "computer_action", "arguments": {"request": request}, "description": "Computer action: " + request[:160]}
                self._json({"ok": True, "pending_approval": approval_id, "message": result.output, "computer": kernel.computer.status()})
            else:
                self._json({"ok": result.ok, "result": result.output, "computer": kernel.computer.status()})
            return

        if path == "/api/integrations/refresh":
            self._json({"ok": True, **kernel.integrations.summary()})
            return

        if path == "/api/integrations/tailscale/preview":
            port = int(payload.get("port", kernel.config.get("gui", {}).get("port", 8765)))
            result = kernel.integrations.tailscale_serve(port, apply=False)
            self._json({"ok": True, "result": result})
            return

        if path == "/api/integrations/tailscale/enable":
            port = int(payload.get("port", kernel.config.get("gui", {}).get("port", 8765)))
            # Route through the normal permission gate by creating a pending action.
            result = kernel.tools.execute("tailscale_serve", {"port": port})
            if result.output.startswith("APPROVAL_REQUIRED"):
                import uuid
                approval_id = uuid.uuid4().hex[:8]
                kernel.pending[approval_id] = {"tool": "tailscale_serve", "arguments": {"port": port}, "description": "Enable Tailscale Serve for DaQauntum"}
                self._json({"ok": True, "pending_approval": approval_id, "message": result.output})
            else:
                self._json({"ok": result.ok, "result": result.output})
            return

        if path == "/api/integrations/postgres/bootstrap":
            result = kernel.tools.execute("postgres_pgvector_bootstrap", {})
            if result.output.startswith("APPROVAL_REQUIRED"):
                import uuid
                approval_id = uuid.uuid4().hex[:8]
                kernel.pending[approval_id] = {"tool": "postgres_pgvector_bootstrap", "arguments": {}, "description": "Initialize PostgreSQL/pgvector schema"}
                self._json({"ok": True, "pending_approval": approval_id, "message": result.output})
            else:
                self._json({"ok": result.ok, "result": result.output})
            return

        if path == "/api/workspaces/create":
            item = kernel.workspaces.create(
                str(payload.get("name", "")).strip(),
                str(payload.get("focus", "")).strip(),
                str(payload.get("done_looks_like", "")).strip(),
                stage=str(payload.get("stage", "manual")).strip() or "manual",
                resources=[str(x) for x in (payload.get("resources") or [])],
            )
            self._json({"ok": True, "workspace": item, "stats": kernel.workspaces.stats()})
            return

        if path == "/api/workspaces/active":
            item = kernel.workspaces.set_active(payload.get("workspace_id"))
            self._json({"ok": True, "workspace": item, "stats": kernel.workspaces.stats()})
            return

        if path == "/api/workspaces/stage":
            item = kernel.workspaces.set_stage(payload.get("workspace_id"), str(payload.get("stage", "")).strip())
            self._json({"ok": True, "workspace": item})
            return

        if path == "/api/workspaces/resource":
            item = kernel.workspaces.add_resource_text(payload.get("workspace_id"), str(payload.get("name", "")).strip(), str(payload.get("content", "")).strip())
            self._json({"ok": True, "resource": item})
            return

        if path == "/api/workspaces/skill":
            wid = payload.get("workspace_id")
            description = str(payload.get("description", "")).strip()
            instructions = str(payload.get("instructions", "")).strip()
            name = str(payload.get("name", "")).strip() or None
            if instructions:
                item = kernel.workspaces.add_skill(wid, name or "Workspace Skill", description or "Reusable workspace skill", instructions)
            else:
                item = kernel.workspaces.create_skill_from_description(wid, description, name=name)
            self._json({"ok": True, "skill": item})
            return

        if path == "/api/workspaces/agent":
            wid = payload.get("workspace_id")
            item = kernel.workspaces.create_agent_from_description(
                wid, str(payload.get("description", "")).strip(),
                name=str(payload.get("name", "")).strip() or None,
                skills=[str(x) for x in (payload.get("skills") or [])],
            )
            self._json({"ok": True, "agent": item})
            return

        if path == "/api/workspaces/connection":
            item = kernel.workspaces.add_connection(
                payload.get("workspace_id"),
                str(payload.get("name", "")).strip(),
                str(payload.get("kind", "mcp")).strip() or "mcp",
                config=payload.get("config") if isinstance(payload.get("config"), dict) else {},
                env_names=[str(x) for x in (payload.get("env_names") or [])],
            )
            self._json({"ok": True, "connection": item})
            return

        if path == "/api/workbench/run":
            job = kernel.workbench.submit(payload.get("workspace_id"), str(payload.get("agent_slug", "")).strip(), str(payload.get("prompt", "")).strip(), background=True)
            self._json({"ok": True, "job": job, "stats": kernel.workbench.stats()})
            return

        if path == "/api/workbench/run-parallel":
            jobs = kernel.workbench.submit_parallel(payload.get("workspace_id"), [str(x) for x in (payload.get("agent_slugs") or [])], str(payload.get("prompt", "")).strip())
            self._json({"ok": True, "jobs": jobs, "stats": kernel.workbench.stats()})
            return

        if path == "/api/workbench/handoff":
            prompt = kernel.workbench.handoff_prompt(str(payload.get("job_id", "")).strip(), str(payload.get("instruction", "")).strip() or None)
            result = kernel.process(prompt, interaction_mode="workbench_handoff")
            self._json({"ok": True, "result": result, "status": kernel.status()})
            return

        if path == "/api/workspaces/obsidian-export":
            result = kernel.obsidian.export()
            self._json({"ok": True, "result": result})
            return

        if path == "/api/connectors/add-folder":
            folder = str(payload.get("path", "")).strip()
            if not folder:
                raise ValueError("Folder path is required")
            item = kernel.connectors.add_folder(
                folder,
                name=str(payload.get("name", "")).strip() or None,
                project=str(payload.get("project", "")).strip() or None,
                recursive=bool(payload.get("recursive", True)),
                learn_enabled=bool(payload.get("learn_enabled", True)),
            )
            self._json({"ok": True, "connector": item, "stats": kernel.connectors.stats()})
            return

        if path == "/api/connectors/add-url":
            url = str(payload.get("url", "")).strip()
            if not url:
                raise ValueError("URL is required")
            item = kernel.connectors.add_web_url(
                url,
                name=str(payload.get("name", "")).strip() or None,
                project=str(payload.get("project", "")).strip() or None,
                learn_enabled=bool(payload.get("learn_enabled", True)),
            )
            self._json({"ok": True, "connector": item, "stats": kernel.connectors.stats()})
            return

        if path == "/api/connectors/add-rss":
            url = str(payload.get("url", "")).strip()
            if not url:
                raise ValueError("Feed URL is required")
            item = kernel.connectors.add_rss(
                url,
                name=str(payload.get("name", "")).strip() or None,
                project=str(payload.get("project", "")).strip() or None,
                learn_enabled=bool(payload.get("learn_enabled", True)),
            )
            self._json({"ok": True, "connector": item, "stats": kernel.connectors.stats()})
            return

        if path == "/api/connectors/add-apify":
            dataset_id = str(payload.get("dataset_id", "")).strip()
            if not dataset_id:
                raise ValueError("Apify dataset ID is required")
            item = kernel.connectors.add_apify_dataset(
                dataset_id,
                name=str(payload.get("name", "")).strip() or None,
                project=str(payload.get("project", "")).strip() or None,
                learn_enabled=bool(payload.get("learn_enabled", True)),
                token_env=str(payload.get("token_env", "APIFY_TOKEN")).strip() or "APIFY_TOKEN",
                limit=int(payload.get("limit", 100)),
            )
            self._json({"ok": True, "connector": item, "stats": kernel.connectors.stats()})
            return

        if path == "/api/connectors/sync":
            connector_id = payload.get("connector_id")
            if connector_id is None:
                result = kernel.connectors.sync_all(learning_only=bool(payload.get("learning_only", False)))
            else:
                result = kernel.connectors.sync(int(connector_id)).as_dict()
            self._json({"ok": True, "result": result, "connectors": kernel.connectors.list(), "stats": kernel.connectors.stats()})
            return

        if path == "/api/connectors/learning":
            connector_id = int(payload.get("connector_id"))
            item = kernel.connectors.set_learning(connector_id, bool(payload.get("enabled", True)))
            self._json({"ok": True, "connector": item})
            return

        if path == "/api/connectors/enabled":
            connector_id = int(payload.get("connector_id"))
            item = kernel.connectors.set_enabled(connector_id, bool(payload.get("enabled", True)))
            self._json({"ok": True, "connector": item})
            return

        if path == "/api/connectors/remove":
            connector_id = int(payload.get("connector_id"))
            kernel.connectors.remove(connector_id)
            self._json({"ok": True, "connectors": kernel.connectors.list(), "stats": kernel.connectors.stats()})
            return

        if path == "/api/device-bridge/rotate":
            controller = getattr(kernel, "device_bridge_controller", None)
            if controller is None:
                raise ValueError("Device Bridge is not running. Launch DaQauntum with --device-bridge.")
            code = controller.rotate_pairing_code()
            kernel.device_bridge_endpoint["pairing_code"] = code
            self._json({"ok": True, "device_bridge": dict(kernel.device_bridge_endpoint)})
            return

        if path == "/api/learning/run":
            result = kernel.learning.run_daily()
            self._json({"ok": True, "result": result, "learning": kernel.learning.stats(), "native_model": kernel.native_model.stats()})
            return

        if path == "/api/learning/evening":
            result = kernel.learning.build_evening_digest(str(payload.get("date", "")).strip() or None)
            self._json({"ok": True, "result": result, "learning": kernel.learning.stats()})
            return

        if path == "/api/native-model/export":
            result = kernel.native_model.export(limit=int(payload.get("limit", 5000)))
            self._json({"ok": True, "result": result, "native_model": kernel.native_model.stats()})
            return

        if path == "/api/call/start":
            state = kernel.calls.start(str(payload.get("title", "")).strip() or None)
            self._json({"ok": True, "call": state})
            return

        if path == "/api/call/turn":
            session_id = str(payload.get("session_id", "")).strip()
            text = str(payload.get("text", "")).strip()
            if not session_id or not text:
                raise ValueError("session_id and text are required")
            state = kernel.calls.add_turn(session_id, text)
            self._json({"ok": True, "call": state})
            return

        if path == "/api/call/end":
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                raise ValueError("session_id is required")
            state = kernel.calls.end(session_id)
            processed = []
            if bool(payload.get("process_tasks", False)):
                processed = kernel.calls.run_all(session_id)
                state = kernel.calls.get(session_id) or state
            self._json({"ok": True, "call": state, "processed_tasks": processed, "status": kernel.status()})
            return

        if path == "/api/call/task/run":
            task_id = int(payload.get("task_id"))
            result = kernel.calls.run_task(task_id)
            self._json({"ok": True, **result, "status": kernel.status()})
            return

        if path == "/api/call/tasks/run-all":
            session_id = str(payload.get("session_id", "")).strip()
            if not session_id:
                raise ValueError("session_id is required")
            results = kernel.calls.run_all(session_id)
            self._json({"ok": True, "results": results, "call": kernel.calls.get(session_id), "status": kernel.status()})
            return

        if path == "/api/voice/speak":
            if not kernel.runtime.module_enabled("voice"):
                raise ValueError("Voice module is disabled in Runtime Modules")
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ValueError("Speech text is empty")
            result = kernel.voice.speak(text)
            self._json({"ok": True, "result": result})
            return

        self._json({"ok": False, "error": "Unknown API endpoint"}, HTTPStatus.NOT_FOUND)

    def _stream_ndjson(self, events) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in events:
                line = (json.dumps(event, ensure_ascii=False, default=str) + "\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # Browser disconnected (usually barge-in/navigation). Generation receives cancellation
            # through the explicit interrupt endpoint when available; this keeps the server healthy.
            return
        except Exception as exc:
            try:
                line = (json.dumps({"type": "error", "error": f"{type(exc).__name__}: {exc}"}) + "\n").encode("utf-8")
                self.wfile.write(line)
                self.wfile.flush()
            except Exception:
                pass

    # The phone client is a separate page so the desktop GUI stays unchanged.
    MOBILE_ROUTES = {"m", "m/", "mobile", "mobile/", "phone", "phone/"}

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative in self.MOBILE_ROUTES:
            relative = "mobile.html"
        target = (self.server.web_root / relative).resolve()
        root = self.server.web_root.resolve()
        if target != root and root not in target.parents:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            # SPA-style fallback keeps refreshes safe if more client routes are added.
            target = root / "index.html"
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime}; charset=utf-8" if mime.startswith("text/") or mime in {"application/javascript", "application/json"} else mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_bytes(self, max_bytes: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            raise ValueError("Request body is empty")
        if length > int(max_bytes):
            raise ValueError("Request body is too large")
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 12_000_000:
            raise ValueError("Request body is too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            value = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON body") from exc
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def create_server(kernel, host: str = "127.0.0.1", port: int = 8765) -> DaQauntumHTTPServer:
    web_root = Path(__file__).resolve().parent / "web"
    return DaQauntumHTTPServer((host, port), DaQauntumRequestHandler, kernel=kernel, web_root=web_root)
