#!/usr/bin/env python3
"""Guided setup for reaching DaQauntum from your phone.

Walks the whole path in order and stops at the first thing that is not ready,
rather than reporting a wall of status. The supported route is Tailscale: the
control API stays bound to loopback and Tailscale Serve proxies to it over your
own tailnet, so DaQauntum is never exposed to the public internet.

    PYTHONPATH=. python scripts/setup_remote_access.py            # check and explain
    PYTHONPATH=. python scripts/setup_remote_access.py --apply    # run tailscale serve
    PYTHONPATH=. python scripts/setup_remote_access.py --enrol "Louis iPhone"
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from typing import Any

from core.kernel import DaQauntumKernel


OK, TODO, WARN = "✓", "→", "!"


def run(command: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def tailscale_state() -> dict[str, Any]:
    """Installed, logged in, and what this machine's tailnet name is."""
    binary = shutil.which("tailscale")
    if not binary:
        return {"installed": False, "logged_in": False, "dns_name": None, "detail": "tailscale CLI not found"}
    code, output = run([binary, "status", "--json"], 15)
    if code != 0:
        return {"installed": True, "logged_in": False, "dns_name": None, "detail": output[:200]}
    try:
        status = json.loads(output)
    except ValueError:
        return {"installed": True, "logged_in": False, "dns_name": None, "detail": "could not parse tailscale status"}
    self_node = status.get("Self") or {}
    backend = str(status.get("BackendState", ""))
    return {
        "installed": True,
        "logged_in": backend == "Running",
        "backend_state": backend,
        "dns_name": (self_node.get("DNSName") or "").rstrip("."),
        "detail": f"backend state: {backend}",
    }


def serve_state(port: int) -> dict[str, Any]:
    binary = shutil.which("tailscale")
    if not binary:
        return {"active": False, "detail": "tailscale CLI not found"}
    code, output = run([binary, "serve", "status"], 10)
    if code != 0:
        return {"active": False, "detail": (output or "serve status unavailable")[:200]}
    return {"active": str(port) in output, "detail": output[:400] or "nothing served"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Guided DaQauntum remote-access setup")
    parser.add_argument("--apply", action="store_true", help="Actually run `tailscale serve --bg <port>`")
    parser.add_argument("--enrol", default="", help="Also mint an enrollment code for a device with this name")
    parser.add_argument("--scopes", default="read,chat,approve,ingest", help="Scopes for --enrol")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    kernel = DaQauntumKernel()
    port = args.port or int(kernel.config.get("gui", {}).get("port", 8765))
    host = str(kernel.config.get("gui", {}).get("host", "127.0.0.1"))
    identity = kernel.identity

    print("DaQauntum remote access")
    print("=" * 56)
    steps: list[tuple[str, str, str]] = []

    # 1. The control API must stay private.
    loopback = host.startswith("127.") or host in {"localhost", "::1"}
    steps.append((
        OK if loopback else WARN,
        f"Control API binds to {host}:{port}",
        "" if loopback else "This is reachable beyond this machine. Prefer the loopback default and let Tailscale do the reaching.",
    ))

    # 2. Authentication must be on.
    stats = identity.stats()
    steps.append((
        OK if (stats["enabled"] and stats["require_auth_for_remote"]) else WARN,
        "Remote authentication required",
        "" if stats["require_auth_for_remote"] else "Set identity.require_auth_for_remote: true before exposing anything.",
    ))

    # 3. Tailscale.
    tailscale = tailscale_state()
    if not tailscale["installed"]:
        steps.append((TODO, "Tailscale installed", "Install it: https://tailscale.com/download"))
    elif not tailscale["logged_in"]:
        steps.append((TODO, "Tailscale logged in", f"Run: sudo tailscale up   ({tailscale['detail']})"))
    else:
        steps.append((OK, f"Tailscale up as {tailscale['dns_name'] or 'this machine'}", ""))

    # 4. Serve.
    serve = serve_state(port) if tailscale["logged_in"] else {"active": False, "detail": "tailscale not running"}
    if tailscale["logged_in"] and not serve["active"]:
        if args.apply:
            try:
                result = kernel.integrations.tailscale_serve(port, apply=True)
                steps.append((OK, "Tailscale Serve started", result[:160]))
                serve = serve_state(port)
            except Exception as exc:
                steps.append((WARN, "Tailscale Serve failed", str(exc)[:160]))
        else:
            steps.append((TODO, "Tailscale Serve for DaQauntum",
                          f"Re-run with --apply, or: tailscale serve --bg {port}"))
    elif serve["active"]:
        steps.append((OK, f"Tailscale Serve is proxying port {port}", ""))

    # 5. A device to connect from.
    devices = [d for d in identity.list_devices() if d["active"]]
    if devices:
        steps.append((OK, f"{len(devices)} enrolled device(s)", ", ".join(d["name"] for d in devices[:4])))
    else:
        steps.append((TODO, "Enrol your phone",
                      "Re-run with --enrol \"My phone\", or use the ⛨ Devices view in the GUI."))

    for marker, title, detail in steps:
        print(f"  {marker} {title}")
        if detail:
            print(f"      {detail}")

    if args.enrol:
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        issued = identity.create_enrollment_code(device_name=args.enrol, scopes=scopes)
        minutes = round(issued["expires_in_seconds"] / 60)
        print("\n" + "=" * 56)
        print(f"  Enrollment code:  {issued['code']}")
        print(f"  Grants:           {', '.join(issued['scopes'])}")
        print(f"  Valid for:        {minutes} minute(s), single use")
        print("=" * 56)

    url_host = tailscale["dns_name"] or "<this-machine>.<your-tailnet>.ts.net"
    print("\nOn the phone, once it is on your tailnet, open:")
    print(f"  https://{url_host}/m" if serve.get("active") else f"  http://{url_host}:{port}/m")
    print("\nDo not port-forward DaQauntum or bind it to a public address. The device")
    print("token is defence in depth, not a reason to put the control API on the internet.")

    blocking = [title for marker, title, _ in steps if marker == TODO]
    raise SystemExit(1 if blocking else 0)


if __name__ == "__main__":
    main()
