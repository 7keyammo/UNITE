from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from realtime import RealtimeSessionManager


def main() -> None:
    manager = RealtimeSessionManager()
    turn = manager.begin("voice_call")
    assert manager.get(turn.id) is not None
    assert manager.cancel(turn.id) is True
    assert manager.cancelled(turn.id) is True
    manager.finish(turn.id)
    assert manager.get(turn.id).as_dict()["done"] is True

    with tempfile.TemporaryDirectory(prefix="daqauntum-realtime-") as tmp:
        root = Path(tmp)
        cfg = {
            "version": "0.4.0-realtime-test",
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
            "runtime": {"operation_mode": "local", "cognition_mode": "realtime", "modules": {"memory": True, "knowledge": True, "sources": False, "planner": True, "critic": True, "voice": True, "streaming": True, "barge_in": True}},
            "memory": {"db_path": str(root / "memory.db"), "max_recent_messages": 8, "structured_enabled": True, "structured_retrieve_limit": 4, "auto_episode": True, "auto_decisions": True, "auto_procedures": True, "auto_semantic_cues": True, "training_confidence_threshold": 0.85, "intelligence_enabled": True, "embedding_dimensions": 64, "auto_maintain_every": 0},
            "knowledge_graph": {"enabled": True, "context_limit": 3, "backfill_on_start": True},
            "sources": {"enabled": False, "project_root": str(root)},
            "tools": {"project_root": str(root), "notes_dir": "notes"},
            "call": {"calls_dir": str(root / "calls")},
            "voice": {"stt": {"backend": "none"}, "tts": {"backend": "mock"}},
        }
        cfg_path = root / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        kernel = DaQauntumKernel(str(cfg_path))

        events = list(kernel.process_stream("Explain inertia in one short sentence.", interaction_mode="gui_chat"))
        kinds = [e["type"] for e in events]
        assert kinds[0] == "start" and "meta" in kinds and "delta" in kinds and kinds[-1] == "done", events
        done = events[-1]
        assert done["result"]["effective_cognition"] == "realtime", done
        assert done["realtime"]["time_to_first_token_ms"] is not None, done
        assert done["result"]["critic"]["model"] == "realtime-stream-bypass", done

        # An already-cancelled turn must not promote partial output into structured memory.
        before = kernel.structured_memory.stats()["structured_total"]
        cancelled = kernel.realtime.begin("gui_chat")
        kernel.realtime.cancel(cancelled.id)
        interrupted = list(kernel.process_stream("Tell me a long story about motion.", interaction_mode="gui_chat", turn_id=cancelled.id))
        assert interrupted[-1]["type"] == "interrupted", interrupted
        after = kernel.structured_memory.stats()["structured_total"]
        assert after == before, (before, after)

    print("DaQauntum v0.4.0 realtime smoke test: PASS")


if __name__ == "__main__":
    main()
