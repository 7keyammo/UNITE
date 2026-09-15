from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel


# 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dq-perception-") as tmp:
        root = Path(tmp)
        cfg = {
            "name": "DaQauntum",
            "version": "0.4.0-perception-test",
            "permission_level": 2,
            "models": {
                "fallback_to_mock": True,
                "providers": {"mock": {"model": "daqauntum-mock"}},
                "roles": {
                    "planner": {"provider": "mock"},
                    "executor": {"provider": "mock"},
                    "critic": {"provider": "mock"},
                },
            },
            "runtime": {"operation_mode": "local", "cognition_mode": "realtime"},
            "memory": {"db_path": str(root / "data" / "dq.db"), "max_recent_messages": 8},
            "tools": {"project_root": str(root), "notes_dir": "data/notes"},
            "perception": {"frames_dir": str(root / "data" / "perception" / "frames"), "ollama_model": ""},
            "computer": {"autonomy": "observe"},
            "sources": {"project_root": str(root)},
            "workspaces": {"root": str(root / "data" / "workspaces")},
            "learning": {"project_root": str(root)},
        }
        config_path = root / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        kernel = DaQauntumKernel(str(config_path))

        frame = kernel.perception.add_frame(PNG, frame_type="screen", mime_type="image/png", label="test")
        assert frame["id"] > 0
        assert kernel.perception.stats()["screens"] == 1
        assert Path(frame["file_path"]).exists()

        analysis = kernel.perception.analyze(frame["id"], "What is visible?", operation_mode="local")
        assert analysis["provider"] == "metadata", analysis
        assert "stored the frame" in analysis["text"]

        # The fast planner should recognize visual requests and use the read-only perception tool.
        answer = kernel.process("Look at my screen and tell me what you see.", interaction_mode="gui_chat")
        tools = [x.get("tool") for x in answer.get("observations", [])]
        assert "perception_analyze" in tools, answer.get("observations")

        # State-changing computer use is still gated at L2.
        blocked = kernel.tools.execute("computer_action", {"request": "click the save button"})
        assert blocked.output.startswith("APPROVAL_REQUIRED"), blocked

        # Simulate a healthy computer-use adapter to test Execute + visual verification at L4.
        kernel.permissions.level = 4
        kernel.computer.set_autonomy("execute")
        def fake_interpreter(prompt: str, *, mode: str = "read_only", timeout: int = 300):
            if mode == "read_only":
                return "PASS\nThe requested result is visible on screen."
            return "Clicked the requested control."
        kernel.integrations.open_interpreter = fake_interpreter  # type: ignore[method-assign]
        ran = kernel.tools.execute("computer_action", {"request": "click the save button"})
        assert ran.ok, ran.output
        payload = json.loads(ran.output)
        assert payload["verification_status"] == "pass", payload
        recent = kernel.computer.recent(1)
        assert recent and recent[0]["verification_status"] == "pass"

    print("DaQauntum v0.4.0 perception + computer control smoke test: PASS")


if __name__ == "__main__":
    main()
