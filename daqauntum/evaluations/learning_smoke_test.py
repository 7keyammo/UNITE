from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from learning.manager import LearningTopic


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="daqauntum-learning-") as tmp:
        root = Path(tmp)
        project = root / "project"
        project.mkdir()
        # Give the learner an inspectable local code artifact.
        (project / "sample.py").write_text("def safe_action(level):\n    return level >= 3\n", encoding="utf-8")
        cfg = {
            "version": "0.4.0-learning-test",
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
            "model_policy": {"enabled": True, "local_only_for_sensitive": True, "local_providers": ["mock"], "hosted_providers": []},
            "cognition": {"planner_enabled": True, "critic_enabled": True},
            "runtime": {"operation_mode": "local", "cognition_mode": "realtime", "modules": {"memory": True, "knowledge": True, "sources": False, "planner": True, "critic": True, "voice": False, "streaming": True, "barge_in": True, "duplex": False, "learning": True}},
            "memory": {"db_path": str(root / "learning.db"), "max_recent_messages": 8, "structured_enabled": True, "structured_retrieve_limit": 4, "auto_episode": True, "auto_decisions": True, "auto_procedures": True, "auto_semantic_cues": True, "training_confidence_threshold": 0.0, "intelligence_enabled": True, "embedding_dimensions": 64, "auto_maintain_every": 0},
            "knowledge_graph": {"enabled": True, "context_limit": 3, "backfill_on_start": True},
            "sources": {"enabled": False, "project_root": str(project)},
            "tools": {"project_root": str(project), "notes_dir": "notes"},
            "call": {"calls_dir": str(root / "calls")},
            "learning": {"enabled": True, "project_root": str(project), "reports_dir": "learning/reports", "outbox_dir": "learning/outbox", "operation_mode": "local"},
            "native_model": {"dataset_dir": "native/datasets", "fine_tuning_enabled": False},
            "voice": {"stt": {"backend": "none"}, "tts": {"backend": "none"}},
        }
        config = root / "config.yaml"
        config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        kernel = DaQauntumKernel(str(config))
        before_mode = kernel.runtime.cognition_mode
        topic = LearningTopic("software", "Smoke permission audit", "safe action permission level", ("sample.py",))
        result = kernel.learning.run_daily(topic=topic)
        report_path = Path(result["report_path"])
        assert report_path.exists(), result
        report_text = report_path.read_text(encoding="utf-8")
        assert "sample.py" in report_text, report_text
        assert kernel.runtime.cognition_mode == before_mode, kernel.runtime.status()
        assert kernel.learning.stats()["completed"] == 1
        assert kernel.learning.list_reports(), kernel.learning.stats()
        episodes = kernel.memory.list_memories(kind="episodic", limit=20)
        assert any("Autonomous learning:" in m["title"] for m in episodes), episodes
        digest = kernel.learning.build_evening_digest()
        assert Path(digest["report_path"]).exists(), digest
        exported = kernel.native_model.export()
        assert Path(exported["path"]).exists(), exported
        assert exported["records"] >= 1, exported
        assert exported["fine_tuning_performed"] is False

        # Timer unit rendering must be portable and must not need a live systemd user bus.
        import subprocess, sys
        units = root / "units"
        subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "install_learning_timers.py"), "--render-only", str(units)], check=True)
        assert (units / "daqauntum-learn.timer").exists() and (units / "daqauntum-report.timer").exists() and (units / "daqauntum-connectors.timer").exists()
        assert kernel.native_model.stats()["fine_tuning_enabled"] is False
        print("DaQauntum v0.4.0 autonomous learning smoke test: PASS")


if __name__ == "__main__":
    main()
