#!/usr/bin/env python3
from __future__ import annotations

import json
import socket
import tempfile
import threading
import urllib.request
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from runtime import DaQauntumHost


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "obsidian").mkdir()
        http_port = free_port()
        ws_port = free_port()
        cfg = {
            "version": "0.4.0-integrations-test",
            "models": {"roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}}},
            "memory": {"db_path": str(root / "dq.db")},
            "tools": {"project_root": str(root), "notes_dir": "notes"},
            "sources": {"project_root": str(root)},
            "gui": {"host": "127.0.0.1", "port": http_port, "websocket_port": ws_port, "open_browser": False},
            "full_duplex": {"enabled": False},
            "workspaces": {"root": str(root / "workspaces"), "obsidian_root": str(root / "obsidian")},
            "integrations": {"obsidian": {"vault_path": str(root / "obsidian")}},
        }
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        kernel = DaQauntumKernel(str(path))
        summary = kernel.integrations.summary()
        assert len(summary["integrations"]) == 8, summary
        obsidian = kernel.integrations.status("obsidian")
        assert obsidian.available and obsidian.healthy, obsidian

        result = kernel.tools.execute("integration_status", {})
        assert result.ok and "open_interpreter" in result.output, result.output

        # State-changing external actions remain permission-gated at default L2,
        # even when the third-party tool is absent.
        oi = kernel.tools.execute("open_interpreter_task", {"prompt": "inspect this repo"})
        assert oi.output.startswith("APPROVAL_REQUIRED"), oi.output
        ts = kernel.tools.execute("tailscale_serve", {"port": http_port})
        assert ts.output.startswith("APPROVAL_REQUIRED"), ts.output

        host = DaQauntumHost(config_path=str(path), port=http_port, ws_port=ws_port)
        ep = host.start()
        thread = threading.Thread(target=host.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(ep.gui_url.rstrip("/") + "/api/integrations", timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            assert data["ok"] and len(data["integrations"]) == 8, data
        finally:
            if host.http is not None:
                host.http.shutdown()
            host.stop()
            thread.join(timeout=2)

    print("DaQauntum v0.4.0 integration runtime smoke test: PASS")


if __name__ == "__main__":
    main()
