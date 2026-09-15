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
            "version": "0.4.0-demo-test",
            "permission_level": 2,
            "models": {"roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}}, "providers": {"mock": {"model": "daqauntum-mock"}}, "fallback_to_mock": True},
            "memory": {"db_path": str(root / "test.db")},
            "tools": {"project_root": str(root), "notes_dir": "notes"},
            "sources": {"project_root": str(root)},
            "learning": {"project_root": str(root)},
        }
        path = root / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        kernel = DaQauntumKernel(str(path))
        ready = kernel.demo.readiness()
        assert 0 <= ready["score"] <= 100 and len(ready["steps"]) >= 8
        state = kernel.demo.start()
        assert state["active"] and state["current"]["id"] == "hello"
        first = state["index"]
        state = kernel.demo.next()
        assert state["index"] == first + 1
        state = kernel.demo.previous()
        assert state["index"] == first
        print("DaQauntum v0.4.0 guided demo smoke test: PASS")


if __name__ == "__main__":
    main()
