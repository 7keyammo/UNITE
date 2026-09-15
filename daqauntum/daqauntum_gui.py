#!/usr/bin/env python3
from __future__ import annotations

import argparse
import threading
import urllib.request
import webbrowser

from runtime import DaQauntumHost


def _server_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/status", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch or open the DaQauntum GUI")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--ws-port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--allow-lan", action="store_true")
    parser.add_argument("--device-bridge", action="store_true")
    parser.add_argument("--client-only", action="store_true", help="Only open an already-running persistent DaQauntum server")
    parser.add_argument("--demo", action="store_true", help="Open the guided v0.4.0 demo and speak the first step")
    parser.add_argument("--server-url", default="http://127.0.0.1:8765/")
    args = parser.parse_args()

    if args.client_only:
        if not _server_alive(args.server_url):
            raise SystemExit(f"No DaQauntum server detected at {args.server_url}. Start: python daqauntum_server.py")
        if not args.no_browser:
            webbrowser.open(args.server_url.rstrip("/") + ("/?demo=1" if args.demo else "/"))
        print(f"Connected to persistent DaQauntum: {args.server_url}")
        return

    host = DaQauntumHost(config_path=args.config, host=args.host, port=args.port, ws_port=args.ws_port, allow_lan=args.allow_lan, device_bridge=args.device_bridge)
    ep = host.start()
    print("\nDaQauntum v0.4.0 — Presence + Guided Demo")
    print(f"Open: {ep.gui_url}")
    if ep.websocket_url:
        print(f"Realtime: {ep.websocket_url}")
    if ep.device_bridge_url:
        print(f"Phone bridge: {ep.device_bridge_url}")
        print(f"Pairing code: {ep.pairing_code}")
    print("Tip: for always-on use, run daqauntum_server.py as a user service, then use --client-only.\n")
    if not args.no_browser:
        launch_url = ep.gui_url.rstrip("/") + ("/?demo=1" if args.demo else "/")
        threading.Timer(0.35, lambda: webbrowser.open(launch_url)).start()
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DaQauntum...")
        host.stop()


if __name__ == "__main__":
    main()
