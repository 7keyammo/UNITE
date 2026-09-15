#!/usr/bin/env python3
from __future__ import annotations

import json
from core.config import load_config
from integrations import IntegrationManager


def main() -> None:
    cfg = load_config()
    mgr = IntegrationManager(cfg.get("integrations", {}), project_root=cfg.get("tools", {}).get("project_root", "."))
    print(json.dumps(mgr.summary(), indent=2))


if __name__ == "__main__":
    main()
