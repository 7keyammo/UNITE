#!/usr/bin/env python3
from __future__ import annotations

import argparse
from core.config import load_config
from integrations import IntegrationManager


def main() -> None:
    p = argparse.ArgumentParser(description="Preview or enable secure Tailscale access to the localhost DaQauntum server")
    p.add_argument("--apply", action="store_true", help="Actually run tailscale serve --bg. Without this flag the command is only previewed.")
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args()
    cfg = load_config()
    mgr = IntegrationManager(cfg.get("integrations", {}), project_root=cfg.get("tools", {}).get("project_root", "."))
    port = int(args.port or cfg.get("gui", {}).get("port", 8765))
    print(mgr.tailscale_serve(port, apply=args.apply))


if __name__ == "__main__":
    main()
