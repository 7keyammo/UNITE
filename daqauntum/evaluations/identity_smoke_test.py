"""Regression guard for v0.4.2 device identity, enrollment and the API gate.

The governing rule under test: **identity is not authority**. A device proves
which client is calling and which surfaces it may reach. It never changes what
DaQauntum is permitted to do, which stays with the PermissionManager.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

import yaml

import interface.server as server_module
from core.kernel import DaQauntumKernel
from identity import IdentityError, IdentityManager
from identity.models import canonical_code, hash_token, normalize_scopes
from interface.server import required_scope


def _kernel(root: Path, **overrides) -> DaQauntumKernel:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.2-identity-test",
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
    }
    cfg.update(overrides)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(path))


def test_scope_table_fails_closed() -> None:
    """An unmapped endpoint must require admin, and a GET mapping must never
    authorize the POST on the same prefix."""
    assert required_scope("/api/some/endpoint/added/later", "POST") == "admin"
    assert required_scope("/api/some/endpoint/added/later", "GET") == "admin"
    assert required_scope("/api/events", "GET") == "read"
    assert required_scope("/api/events/rules/create", "POST") == "admin", "a read scope authorized a write"
    assert required_scope("/api/events/publish", "POST") == "admin"
    assert required_scope("/api/devices", "GET") == "admin", "the device roster is readable without admin"
    assert required_scope("/api/devices/revoke", "POST") == "admin"
    assert required_scope("/api/devices/redeem", "POST") == "", "enrollment must be reachable without a token"
    assert required_scope("/api/approve", "POST") == "approve"
    assert required_scope("/api/chat", "POST") == "chat"
    assert required_scope("/api/computer/action", "POST") == "admin"


def test_enrollment_lifecycle(root: Path) -> None:
    kernel = _kernel(root / "enroll")
    identity = kernel.identity

    issued = identity.create_enrollment_code(device_name="Phone", scopes=["read", "chat", "approve"])
    code = issued["code"]
    assert issued["scopes"] == ["read", "chat", "approve"], issued

    # Typing is forgiving; guessing is not.
    result = identity.redeem_enrollment_code(code.lower().replace("-", " "), device_name="iPhone",
                                             platform="ios", remote="10.0.0.5")
    token, device = result["token"], result["device"]
    assert device["active"] and device["scopes"] == ["read", "chat", "approve"], device

    # Single use.
    try:
        identity.redeem_enrollment_code(code, remote="10.0.0.5")
    except IdentityError:
        pass
    else:
        raise AssertionError("an enrollment code was redeemed twice")

    # Secrets are not stored in usable form.
    tokens_dump = " ".join(str(dict(r)) for r in kernel.memory.conn.execute("SELECT * FROM device_tokens"))
    codes_dump = " ".join(str(dict(r)) for r in kernel.memory.conn.execute("SELECT * FROM enrollment_codes"))
    assert token not in tokens_dump, "a session token was stored in plaintext"
    assert canonical_code(code) not in codes_dump.upper().replace("-", ""), "an enrollment code was stored in plaintext"
    assert hash_token(token) in tokens_dump

    auth = identity.authenticate(token, remote="10.0.0.5")
    assert auth.ok and auth.has_scope("approve") and not auth.has_scope("admin"), auth

    # Rotation retires the old token immediately.
    rotated = identity.rotate_token(token, remote="10.0.0.5")
    assert not identity.authenticate(token, remote="10.0.0.5").ok, "a rotated token still worked"
    assert identity.authenticate(rotated["token"], remote="10.0.0.5").ok

    # Revoking a device kills every token it holds.
    identity.revoke_device(device["device_id"])
    assert not identity.authenticate(rotated["token"], remote="10.0.0.5").ok, "a revoked device still authenticated"
    assert identity.get_device(device["device_id"]).active is False

    events = {item["event"] for item in identity.recent_auth_events(50)}
    assert {"device_enrolled", "token_issued", "device_revoked"} <= events, events


def test_superseding_and_throttling(root: Path) -> None:
    kernel = _kernel(root / "throttle", identity={"max_auth_failures": 3, "auth_failure_window_seconds": 300})
    identity = kernel.identity

    first = identity.create_enrollment_code(scopes=["read"])["code"]
    second = identity.create_enrollment_code(scopes=["read"])["code"]
    try:
        identity.redeem_enrollment_code(first, remote="10.0.0.7")
    except IdentityError:
        pass
    else:
        raise AssertionError("an older enrollment code stayed valid after a new one was minted")
    identity.clear_failures()
    assert identity.redeem_enrollment_code(second, remote="10.0.0.7")["device"]["active"]

    # Repeated guesses are throttled per address.
    identity.clear_failures()
    live = identity.create_enrollment_code(scopes=["read"])["code"]
    for _ in range(3):
        try:
            identity.redeem_enrollment_code("ZZZZ-ZZZZ", remote="10.0.0.8")
        except IdentityError:
            pass
    try:
        identity.redeem_enrollment_code(live, remote="10.0.0.8")
    except IdentityError as exc:
        assert "Too many" in str(exc), exc
    else:
        raise AssertionError("throttling did not apply after repeated failures")

    # A different address is unaffected by another's failures.
    assert identity.redeem_enrollment_code(live, remote="10.0.0.9")["device"]["active"]


def test_scopes_never_raise_permission(root: Path) -> None:
    """A device scope must not become authority.

    An "approve" device can ask for an action that is already awaiting approval
    to proceed. At L0, where the permission gate refuses state-changing actions
    outright, that approval must still fail.
    """
    kernel = _kernel(root / "escalate", permission_level=0)
    identity = kernel.identity
    result = identity.redeem_enrollment_code(
        identity.create_enrollment_code(scopes=["read", "chat", "approve", "admin"])["code"],
        remote="10.0.0.5",
    )
    auth = identity.authenticate(result["token"], remote="10.0.0.5")
    assert auth.has_scope("admin") and auth.has_scope("approve")

    # The permission level is untouched by enrollment.
    assert kernel.permissions.level == 0

    # Even with every scope, a state-changing tool is refused at L0, and an
    # explicit approval does not override the gate.
    denied = kernel.tools.execute("write_note", {"name": "x", "content": "y"})
    assert not denied.ok and denied.output.startswith("PERMISSION_DENIED"), denied
    still_denied = kernel.tools.execute("write_note", {"name": "x", "content": "y"}, approved=True)
    assert not still_denied.ok and still_denied.output.startswith("PERMISSION_DENIED"), still_denied
    assert not (root / "escalate" / "notes" / "x.md").exists()

    # Approving an id that is not in the pending queue does nothing: a device
    # cannot invent an action, only approve one DaQauntum already prepared.
    assert kernel.approve("not-a-real-id")["ok"] is False


def test_api_gate(root: Path) -> None:
    kernel = _kernel(root / "api")
    srv = server_module.create_server(kernel, "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    original_loopback = server_module.DaQauntumRequestHandler._is_loopback

    def call(path, body=None, token=None, method=None):
        method = method or ("POST" if body is not None else "GET")
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            return 200, json.loads(urllib.request.urlopen(request, timeout=10).read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        # Loopback stays the trusted control surface, exactly as in v0.4.0.
        assert call("/api/status")[0] == 200
        assert call("/api/devices")[0] == 200

        code = call("/api/devices/enroll-code", {"device_name": "Phone", "scopes": ["read", "chat"]})[1]["enrollment"]["code"]
        status, body = call("/api/devices/redeem", {"code": code, "device_name": "iPhone", "platform": "ios"})
        assert status == 200, body
        token = body["token"]

        # From here on, behave as a remote caller.
        server_module.DaQauntumRequestHandler._is_loopback = lambda self: False

        assert call("/api/status")[0] == HTTPStatus.UNAUTHORIZED, "a remote caller reached the API without a token"
        assert call("/api/status", token="dq_not_a_real_token")[0] == HTTPStatus.UNAUTHORIZED
        assert call("/api/status", token=token)[0] == 200
        assert call("/api/events", token=token)[0] == 200

        # A read/chat device must not reach admin surfaces.
        for path, payload in (
            ("/api/devices", None),
            ("/api/devices/revoke", {"device_id": "whatever"}),
            ("/api/events/rules/create", {"name": "x", "action": {"type": "notify", "title": "x"}}),
            ("/api/computer/action", {"instruction": "do something"}),
            ("/api/connectors/add-folder", {"path": "/tmp"}),
        ):
            status, _ = call(path, payload, token=token)
            assert status == HTTPStatus.FORBIDDEN, f"{path} was reachable without admin ({status})"

        # It also must not approve, having no approve scope.
        assert call("/api/approve", {"approval_id": "x"}, token=token)[0] == HTTPStatus.FORBIDDEN

        # A failed authentication must not reveal why.
        status, body = call("/api/status", token="dq_wrong")
        assert "revoked" not in body["error"].lower() and "expired" not in body["error"].lower(), body

        # Enrollment stays reachable without a token, and a bad code is a 401.
        assert call("/api/devices/redeem", {"code": "ZZZZ-ZZZZ"})[0] == HTTPStatus.UNAUTHORIZED

        # A device can rotate its own token, and the old one dies at once.
        status, rotated = call("/api/devices/rotate-token", {}, token=token)
        assert status == 200, rotated
        assert call("/api/status", token=token)[0] == HTTPStatus.UNAUTHORIZED
        assert call("/api/status", token=rotated["token"])[0] == 200

        # Revoking from the trusted surface locks the device out immediately.
        server_module.DaQauntumRequestHandler._is_loopback = original_loopback
        device_id = call("/api/devices")[1]["devices"][0]["device_id"]
        assert call("/api/devices/revoke", {"device_id": device_id})[0] == 200
        server_module.DaQauntumRequestHandler._is_loopback = lambda self: False
        assert call("/api/status", token=rotated["token"])[0] == HTTPStatus.UNAUTHORIZED
    finally:
        server_module.DaQauntumRequestHandler._is_loopback = original_loopback
        srv.shutdown()


def test_mobile_client(root: Path) -> None:
    """The phone client is served and a device can complete the whole journey."""
    kernel = _kernel(root / "mobile")
    srv = server_module.create_server(kernel, "127.0.0.1", 0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    original_loopback = server_module.DaQauntumRequestHandler._is_loopback

    def page(path):
        with urllib.request.urlopen(base + path, timeout=10) as response:
            return response.read().decode("utf-8")

    def call(path, body=None, token=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(base + path, data=data,
                                         method="POST" if data is not None else "GET", headers=headers)
        try:
            return 200, json.loads(urllib.request.urlopen(request, timeout=30).read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        for route in ("/m", "/mobile", "/phone"):
            body = page(route)
            assert "Pair this device" in body, f"{route} did not serve the phone client"
        assert "LOCAL-FIRST QUANTUM INTELLIGENCE" in page("/"), "the desktop GUI was replaced"

        code = call("/api/devices/enroll-code", {"device_name": "Phone", "scopes": ["read", "chat", "approve"]})[1]["enrollment"]["code"]

        server_module.DaQauntumRequestHandler._is_loopback = lambda self: False
        status, body = call("/api/devices/redeem", {"code": code, "device_name": "iPhone", "platform": "iOS"})
        assert status == 200, body
        token = body["token"]

        assert call("/api/chat", {"text": "hello"}, token=token)[0] == 200
        assert call("/api/events?limit=5", token=token)[0] == 200
        assert call("/api/remember", {"text": "Phone note", "kind": "semantic"}, token=token)[0] == 200
        # Admin surfaces stay closed to the phone's scopes.
        assert call("/api/devices", token=token)[0] == HTTPStatus.FORBIDDEN

        # Rotation works and retires the previous token.
        status, rotated = call("/api/devices/rotate-token", {}, token=token)
        assert status == 200, rotated
        assert call("/api/chat", {"text": "again"}, token=token)[0] == HTTPStatus.UNAUTHORIZED
        assert call("/api/chat", {"text": "again"}, token=rotated["token"])[0] == 200
    finally:
        server_module.DaQauntumRequestHandler._is_loopback = original_loopback
        srv.shutdown()


def test_duplex_requires_auth(root: Path) -> None:
    """The realtime WebSocket is gated like the HTTP API.

    The duplex channel carries the same conversation as /api/chat, so leaving it
    open while the HTTP API is authenticated would be a door left unlocked next
    to one that was locked. This matters specifically when the host is started
    with --allow-lan, where both bind to a non-loopback address.
    """
    import asyncio

    try:
        import websockets
    except ImportError:  # pragma: no cover - websockets is a base requirement
        print("  (skipped duplex auth: websockets not installed)")
        return

    from realtime.duplex import DuplexServerThread, FullDuplexHub

    kernel = _kernel(root / "duplex")
    identity = kernel.identity
    chat_token = identity.redeem_enrollment_code(
        identity.create_enrollment_code(scopes=["read", "chat"])["code"], remote="10.0.0.5"
    )["token"]
    read_token = identity.redeem_enrollment_code(
        identity.create_enrollment_code(scopes=["read"])["code"], remote="10.0.0.6"
    )["token"]
    chat_device = identity.list_devices()[1]["device_id"]

    original_loopback = FullDuplexHub._peer_is_loopback
    FullDuplexHub._peer_is_loopback = staticmethod(lambda websocket: False)
    server = DuplexServerThread(kernel, "127.0.0.1", 0, kernel.config.get("full_duplex", {})).start()
    port = list(server.server.sockets)[0].getsockname()[1]

    async def probe(first_message=None, binary=None):
        """Returns the frame type after the handshake: 'hello' or 'error'."""
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}", open_timeout=8) as websocket:
                opening = json.loads(await asyncio.wait_for(websocket.recv(), 8))
                if opening.get("type") != "auth_required":
                    return f"unexpected:{opening.get('type')}"
                if binary is not None:
                    await websocket.send(binary)
                elif first_message is not None:
                    await websocket.send(json.dumps(first_message))
                return json.loads(await asyncio.wait_for(websocket.recv(), 8)).get("type")
        except Exception as exc:
            return f"closed:{type(exc).__name__}"

    async def run_all():
        return {
            "no token": await probe({"type": "ping"}),
            "bad token": await probe({"type": "auth", "token": "dq_not_real"}),
            "audio before auth": await probe(binary=b"\x00" * 64),
            "read-only token": await probe({"type": "auth", "token": read_token}),
            "valid chat token": await probe({"type": "auth", "token": chat_token}),
        }

    try:
        results = asyncio.run(run_all())
        assert results["valid chat token"] == "hello", results
        for label in ("no token", "bad token", "audio before auth", "read-only token"):
            assert results[label] != "hello", f"duplex accepted a connection with: {label}"

        # Revoking the device closes the realtime channel too.
        identity.revoke_device(chat_device)
        assert asyncio.run(probe({"type": "auth", "token": chat_token})) != "hello", "a revoked device kept realtime access"
    finally:
        FullDuplexHub._peer_is_loopback = original_loopback
        try:
            server.stop()
        except Exception:
            pass


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        assert normalize_scopes(["admin", "read", "not_a_scope"]) == ["read", "admin"]
        test_scope_table_fails_closed()
        test_enrollment_lifecycle(root)
        test_superseding_and_throttling(root)
        test_scopes_never_raise_permission(root)
        test_api_gate(root)
        test_mobile_client(root)
        test_duplex_requires_auth(root)
        print("DaQauntum v0.4.2 device identity smoke test: PASS")


if __name__ == "__main__":
    main()
