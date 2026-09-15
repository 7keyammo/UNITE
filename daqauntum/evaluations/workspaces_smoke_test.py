from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="daqauntum-workspaces-smoke-") as tmp:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        config = {
            "version": "0.4.0-workspaces-test",
            "permission_level": 2,
            "models": {
                "fallback_to_mock": True,
                "providers": {"mock": {"model": "daqauntum-mock"}},
                "roles": {
                    "planner": {"provider": "mock", "preference": ["mock"]},
                    "executor": {"provider": "mock", "preference": ["mock"]},
                    "critic": {"provider": "mock", "preference": ["mock"]},
                },
            },
            "runtime": {"operation_mode": "local", "cognition_mode": "realtime", "modules": {"memory": True, "knowledge": True, "sources": True, "planner": True, "critic": True, "voice": False, "streaming": True, "barge_in": True, "duplex": False, "learning": True, "connectors": True, "workspaces": True, "workbench": True}},
            "memory": {"db_path": str(root / "db.sqlite"), "max_recent_messages": 6, "structured_enabled": True, "structured_retrieve_limit": 4, "auto_episode": True, "auto_decisions": True, "auto_procedures": True, "auto_semantic_cues": True, "training_confidence_threshold": 0.85, "intelligence_enabled": True, "embedding_dimensions": 128, "auto_maintain_every": 0},
            "knowledge_graph": {"enabled": True, "context_limit": 4, "backfill_on_start": False},
            "sources": {"enabled": True, "project_root": str(project), "allow_external_paths": False, "allow_url_fetch": False, "allow_private_networks": False},
            "connectors": {"enabled": True, "device_inbox_dir": str(root / "inbox")},
            "tools": {"project_root": str(project), "notes_dir": "notes"},
            "learning": {"enabled": True, "project_root": str(project), "reports_dir": str(root / "learning"), "outbox_dir": str(root / "outbox"), "operation_mode": "local"},
            "native_model": {"dataset_dir": str(root / "native"), "fine_tuning_enabled": False},
            "workspaces": {"enabled": True, "root": str(root / "workspaces"), "obsidian_root": str(root / "obsidian"), "max_parallel_agents": 3},
            "voice": {"stt": {"backend": "none"}, "tts": {"backend": "none"}},
            "full_duplex": {"enabled": False},
        }
        config_path = root / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        kernel = DaQauntumKernel(str(config_path))

        ws = kernel.workspaces.create(
            "Research OS",
            "Turn evidence into a defensible research brief.",
            "A brief with evidence, uncertainty, and next experiment.",
            stage="manual",
        )
        assert ws["id"] and Path(ws["path"]).exists(), ws
        for required in ("FOCUS.md", "ACCESS.md", "ENGINE.md", "AGENTS.md", "resources", "skills", "agents"):
            assert (Path(ws["path"]) / required).exists(), required
        assert kernel.workspaces.active_id == ws["id"]

        skill = kernel.workspaces.create_skill_from_description(ws["id"], "Compare two competing hypotheses against available evidence", name="Evidence Compare")
        assert Path(skill["path"]).exists() and "Compare" in skill["instructions"], skill
        a1 = kernel.workspaces.create_agent_from_description(ws["id"], "Skeptical research reviewer who searches for alternative explanations", name="Skeptic", skills=[skill["slug"]])
        a2 = kernel.workspaces.create_agent_from_description(ws["id"], "Experimental designer who proposes the cheapest falsifiable next test", name="Experimenter")
        assert {a1["slug"], a2["slug"]} == {"skeptic", "experimenter"}

        conn = kernel.workspaces.add_connection(ws["id"], "Research MCP", "mcp", {"command": "example-mcp", "args": ["--stdio"]}, ["RESEARCH_API_KEY"])
        assert conn["executable"] == 0 and conn["env_names"] == ["RESEARCH_API_KEY"], conn
        mcp = json.loads((root / "workspaces" / ".mcp.json").read_text(encoding="utf-8"))
        assert "research-mcp" in mcp["mcpServers"] and "RESEARCH_API_KEY" in mcp["mcpServers"]["research-mcp"]["env"]
        assert "${RESEARCH_API_KEY}" == mcp["mcpServers"]["research-mcp"]["env"]["RESEARCH_API_KEY"]

        context = kernel.workspaces.context_messages("hypothesis")
        assert context and "Turn evidence" in context[0]["content"] and "Research OS" in context[0]["content"], context

        jobs = kernel.workbench.submit_parallel(ws["id"], [a1["slug"], a2["slug"]], "Evaluate whether claim A is ready to publish.")
        ids = [j["id"] for j in jobs]
        deadline = time.time() + 5
        states = []
        while time.time() < deadline:
            states = [kernel.workbench.get(jid) for jid in ids]
            if all(j and j["status"] in {"completed", "failed"} for j in states):
                break
            time.sleep(0.05)
        assert all(j and j["status"] == "completed" and j["response"] for j in states), states
        handoff = kernel.workbench.handoff_prompt(ids[0])
        assert "permission-gated" in handoff and "Draft result" in handoff, handoff

        exported = kernel.obsidian.export()
        assert exported["workspaces"] == 1 and Path(exported["home"]).exists(), exported
        assert (root / "obsidian" / ws["slug"] / "DASHBOARD.md").exists()
        assert (root / "obsidian" / ws["slug"] / "WORKBENCH.md").exists()

        kernel.workspaces.set_stage(ws["id"], "semi_automate")
        assert kernel.workspaces.get(ws["id"])["stage"] == "semi_automate"
        assert "semi_automate" in (Path(ws["path"]) / "ENGINE.md").read_text(encoding="utf-8")

        result = kernel.process("Summarize the active workspace goal in one sentence.", interaction_mode="test")
        assert result["workspace_context"], result
        latest = kernel.memory.conn.execute("SELECT payload_json FROM events WHERE event_type='response' ORDER BY id DESC LIMIT 1").fetchone()
        event = json.loads(latest[0])
        assert event["workspace_context"] == "Research OS", event

        universe = kernel.universe_snapshot()
        assert any(m.get("kind") == "workspace" and m.get("name") == "Research OS" for m in universe["molecules"]), universe["molecules"]
        assert kernel.workspaces.stats()["agents"] == 2
        assert kernel.workbench.stats()["completed"] >= 2

        print("DaQauntum v0.4.0 workspaces smoke test: PASS")


if __name__ == "__main__":
    main()
