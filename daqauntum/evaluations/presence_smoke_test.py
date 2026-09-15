from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = {
            "name": "DaQauntum",
            "version": "0.4.0-presence-test",
            "permission_level": 2,
            "models": {"roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}}, "providers": {"mock": {"model": "daqauntum-mock"}}, "fallback_to_mock": True},
            "memory": {"db_path": str(root / "test.db")},
            "tools": {"project_root": str(root), "notes_dir": "notes"},
            "sources": {"project_root": str(root)},
            "learning": {"project_root": str(root)},
            "presence": {"enabled": True, "sample_interval_seconds": 60},
        }
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        kernel = DaQauntumKernel(str(path))
        snap = kernel.presence.passive_snapshot(save=True)
        assert "system" in snap and "network" in snap and "bluetooth" in snap and "sensors" in snap, snap
        assert kernel.presence.stats()["samples"] >= 1

        # Explicit scans remain separate from passive awareness; stub them for deterministic tests.
        kernel.presence.scan_wifi = lambda: [{"ssid": "LabNet", "signal": 91, "security": "WPA2", "connected": False}]
        result = kernel.tools.execute("wifi_scan", {})
        assert result.ok and "LabNet" in result.output, result

        kernel.presence.activate_wifi_profile = lambda profile: f"activated:{profile}"
        pending = kernel.tools.execute("wifi_activate_profile", {"profile": "LabNet"})
        assert not pending.ok and pending.output.startswith("APPROVAL_REQUIRED"), pending
        approved = kernel.tools.execute("wifi_activate_profile", {"profile": "LabNet"}, approved=True)
        assert approved.ok and "activated:LabNet" in approved.output, approved

        context = kernel.presence.context_messages("What is my battery and Wi-Fi status?")
        assert context and "AMBIENT PRESENCE" in context[0]["content"]
        print("DaQauntum v0.4.0 presence smoke test: PASS")


if __name__ == "__main__":
    main()
