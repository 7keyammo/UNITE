#!/usr/bin/env python3
from __future__ import annotations

import argparse

from runtime import DaQauntumHost


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DaQauntum as a persistent local/server process")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--ws-port", type=int, default=None)
    parser.add_argument("--allow-lan", action="store_true")
    parser.add_argument("--device-bridge", action="store_true")
    args = parser.parse_args()

    host = DaQauntumHost(config_path=args.config, host=args.host, port=args.port, ws_port=args.ws_port, allow_lan=args.allow_lan, device_bridge=args.device_bridge)
    ep = host.start()
    print("\nDaQauntum v0.4.0 — Presence + Guided Demo")
    print(f"Core/GUI: {ep.gui_url}")
    if ep.websocket_url:
        print(f"Realtime: {ep.websocket_url}")
    if ep.device_bridge_url:
        print(f"Phone bridge: {ep.device_bridge_url}")
        print(f"Pairing code: {ep.pairing_code}")
    print("The core remains alive even when no browser window is open.\n")
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DaQauntum server...")
        host.stop()


if __name__ == "__main__":
    main()
