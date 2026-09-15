from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import yaml

from core.brain import CognitiveModelRouter
from core.kernel import DaQauntumKernel
from core.permissions import Action, PermissionManager
from core.policy import ConfidenceEstimator


def build_kernel(root: Path, permission_level: int = 2, db_name: str | None = None) -> DaQauntumKernel:
    db_name = db_name or f"test-l{permission_level}.db"
    cfg = {
        "version": "0.4.0-test",
        "permission_level": permission_level,
        "models": {
            "fallback_to_mock": True,
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "roles": {
                "planner": {"provider": "mock", "preference": ["mock"]},
                "executor": {"provider": "mock", "preference": ["mock"]},
                "critic": {"provider": "mock", "preference": ["mock"]},
            },
        },
        "model_policy": {
            "enabled": True,
            "local_only_for_sensitive": True,
            "second_opinion_enabled": True,
            "second_opinion_complexity": 4,
            "confidence_threshold": 0.65,
            "local_providers": ["mock"],
            "hosted_providers": ["openai", "anthropic"],
        },
        "cognition": {"planner_enabled": True, "critic_enabled": True},
        "memory": {
            "db_path": str(root / db_name),
            "max_recent_messages": 8,
            "structured_enabled": True,
            "structured_retrieve_limit": 6,
            "auto_episode": True,
            "auto_decisions": True,
            "auto_procedures": True,
            "auto_semantic_cues": True,
            "training_confidence_threshold": 0.85,
            "intelligence_enabled": True,
            "embedding_dimensions": 256,
            "consolidation_similarity": 0.50,
            "consolidation_min_cluster": 3,
            "contradiction_similarity": 0.58,
            "auto_maintain_every": 0,
        },
        "knowledge_graph": {"enabled": True, "context_limit": 5, "backfill_on_start": True},
        "sources": {
            "enabled": True,
            "project_root": str(root),
            "max_file_bytes": 2000000,
            "chunk_chars": 400,
            "chunk_overlap": 40,
            "context_limit": 4,
            "allow_external_paths": False,
            "allow_url_fetch": False,
            "allow_private_networks": False,
        },
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "gui": {"host": "127.0.0.1", "port": 8765},
        "call": {"calls_dir": str(root / "calls"), "max_transcript_chars": 12000, "auto_run_tasks": False},
    }
    config_path = root / f"config-l{permission_level}-{db_name}.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(config_path))


def create_v023_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO messages(role, content) VALUES ('user', 'old memory survives')")
    conn.commit()
    conn.close()


def create_v024_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            project TEXT,
            tags_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 1.0,
            source TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE memory_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO memory_items(kind, title, content, tags_json, confidence, source) VALUES (?, ?, ?, '[]', 1.0, ?)",
        ("semantic", "Legacy structured rule", "Private information stays local.", "v0.2.4"),
    )
    conn.commit()
    conn.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        kernel = build_kernel(root, permission_level=2)

        # v0.2.3 cognitive stack still works.
        matrix = kernel.model_matrix()
        for role in ("planner", "executor", "critic"):
            assert matrix["roles"][role]["selected"]["provider"] == "mock", matrix

        result = kernel.process("Help me design a physics experiment about acceleration.")
        assert result["agent"] == "scientist", result
        assert result["provider"] == "mock", result
        assert result["agent_execution"]["workflow"], result
        assert any("variables" in item.lower() for item in result["agent_execution"]["workflow"]), result
        assert result["plan"]["steps"], result
        assert result["cognition"]["planner"]["provider"] == "runtime", result
        assert result["cognition"]["executor"]["provider"] == "mock", result
        assert result["cognition"]["critic"]["provider"] == "runtime", result
        assert result["profile"]["domain"] == "scientist", result
        assert result["policy"]["enabled"] is True, result
        assert result["effective_cognition"] == "realtime", result
        assert result["latency_ms"] >= 0, result
        assert 0.0 < result["confidence"] <= 1.0, result

        teacher = kernel.process("Create a lesson plan for my physics class.")
        assert teacher["agent"] == "teacher", teacher

        # v0.4.0 runtime modes: realtime is one-inference fast path; deep restores full cognitive stack.
        rt = kernel.set_runtime_modes(operation_mode="local", cognition_mode="deep")
        assert rt["operation_mode"] == "local" and rt["cognition_mode"] == "deep", rt
        deep_turn = kernel.process("Analyze and compare two rigorous physics experiment designs.")
        assert deep_turn["effective_cognition"] == "deep", deep_turn
        assert deep_turn["cognition"]["planner"]["provider"] == "mock", deep_turn
        assert deep_turn["cognition"]["critic"]["provider"] == "mock", deep_turn
        kernel.set_runtime_modes(operation_mode="auto", cognition_mode="auto")

        # Structured memory: episodes are automatic and raw messages remain intact.
        stats = kernel.structured_memory.stats()
        assert stats["messages"] >= 4, stats
        assert stats["structured_total"] >= 2, stats
        assert stats["by_kind"].get("episodic", 0) >= 2, stats
        assert result["structured_memory_ids"], result

        # Explicit semantic memory can be stored, found, retrieved into context, and deactivated.
        remembered = kernel.remember("DaQauntum uses independent planner executor and critic brains.")
        assert remembered["kind"] == "semantic", remembered
        matches = kernel.search_structured_memory("planner executor critic")
        assert any(item["id"] == remembered["id"] for item in matches), matches

        recall_turn = kernel.process("What do you remember about planner executor critic brains?")
        assert any(
            item.get("memory_id") == remembered["id"] for item in recall_turn["retrieved_memories"]
        ), recall_turn["retrieved_memories"]

        # v0.2.5 local vector ranking retrieves conceptually related privacy/local-model memory.
        private_rule = kernel.remember("Sensitive information should use local models.")
        semantic_matches = kernel.search_structured_memory("keep private data on the device", limit=10)
        matched = next((item for item in semantic_matches if item["id"] == private_rule["id"]), None)
        assert matched is not None, semantic_matches
        assert matched.get("retrieval", {}).get("semantic", 0.0) > 0.0, matched

        # Conservative contradiction detection links opposite-polarity durable memories.
        opposite_rule = kernel.remember("Sensitive information should not use local models.")
        contradictions = kernel.memory_contradictions(limit=20)
        assert any(
            {row["source_memory_id"], row["target_memory_id"]} == {private_rule["id"], opposite_rule["id"]}
            for row in contradictions
        ), contradictions

        # v0.2.6+ provenance-aware knowledge graph mirrors durable memory without inventing evidence.
        graph_matches = kernel.search_knowledge("local models", limit=20)
        claim_nodes = [node for node in graph_matches if node["node_type"] == "claim"]
        assert claim_nodes, graph_matches
        private_node = next(
            node for node in claim_nodes if "local models" in node["name"].lower() and "not use" not in node["name"].lower()
        )
        provenance = kernel.knowledge_provenance(private_node["id"])
        assert any(item.get("memory_id") == private_rule["id"] for item in provenance), provenance
        private_neighbors = kernel.knowledge.neighbors(private_node["id"], limit=20)
        assert any(edge["relation"] == "contradicts" for edge in private_neighbors), private_neighbors

        # Manual graph links have explicit user provenance and remain inspectable.
        project_node = kernel.knowledge.add_node("project", "DaQauntum Test Graph")
        manual = kernel.link_knowledge(project_node, "investigates", private_node["id"])
        edge_provenance = kernel.knowledge.provenance(edge_id=manual["edge_id"], limit=10)
        assert any(item["source_type"] == "user_explicit" for item in edge_provenance), edge_provenance

        # Relevant graph context is retrieved into a normal turn.
        graph_turn = kernel.process("What supports our rule about local models?")
        assert graph_turn["retrieved_knowledge"], graph_turn
        assert any(obs.get("tool") == "knowledge_search" for obs in graph_turn["observations"]), graph_turn

        assert kernel.forget_memory(remembered["id"]) is True
        assert all(item["id"] != remembered["id"] for item in kernel.search_structured_memory("planner executor critic"))

        # User decision cues are captured separately from episodes.
        decision_turn = kernel.process("We decided to keep permission enforcement separate from model intelligence.")
        decisions = kernel.memory.list_memories(kind="decision", limit=10)
        assert decisions, decision_turn
        assert any("permission enforcement" in item["content"].lower() for item in decisions), decisions

        # Active project is persistent and project turns are captured.
        assert kernel.set_active_project("DaQauntum Brain") == "DaQauntum Brain"
        project_turn = kernel.process("Let's use structured memory for our next architecture milestone.")
        assert project_turn["active_project"] == "DaQauntum Brain", project_turn
        project_memories = kernel.memory.list_memories(project="DaQauntum Brain", limit=20)
        assert any(item["kind"] == "project" for item in project_memories), project_memories

        # Re-opening the same DB preserves project state and structured memories.
        restarted = build_kernel(root, permission_level=2, db_name="test-l2.db")
        assert restarted.structured_memory.active_project == "DaQauntum Brain"
        persisted = restarted.search_structured_memory("structured memory")
        assert persisted, persisted

        # Repeated similar experiences can be consolidated into a durable semantic pattern.
        repeated = [
            "Run release verification and smoke tests before packaging the full repository.",
            "Before packaging a release, run release verification plus the smoke tests.",
            "Verify the complete repository and run smoke tests prior to creating the release zip.",
        ]
        repeated_ids = [
            kernel.structured_memory.remember_explicit(text, kind="episodic", tags=["release-test"])
            for text in repeated
        ]
        maintenance = kernel.maintain_memory()
        assert maintenance["consolidation"]["clusters"] >= 1, maintenance
        consolidated = kernel.memory.list_memories(kind="semantic", limit=50)
        assert any("consolidated" in item.get("tags", []) for item in consolidated), consolidated
        links = kernel.structured_memory.relations(limit=100)
        assert sum(1 for row in links if row["relation"] == "consolidated_into") >= 3, links

        # Salience ages transient memories more aggressively than durable decisions.
        old_episode = kernel.memory.add_memory(
            "episodic", "Old transient episode", "A routine event from long ago.", confidence=0.9
        )
        old_decision = kernel.memory.add_memory(
            "decision", "Old durable decision", "We decided this architecture remains authoritative.", confidence=0.9
        )
        kernel.structured_memory.intelligence.on_memory_added(old_episode)
        kernel.structured_memory.intelligence.on_memory_added(old_decision)
        kernel.memory.conn.execute(
            "UPDATE memory_items SET created_at = '2020-01-01 00:00:00' WHERE id IN (?, ?)",
            (old_episode, old_decision),
        )
        kernel.memory.conn.commit()
        kernel.maintain_memory()
        episodic_salience = kernel.memory.get_memory_metrics(old_episode)["salience"]
        decision_salience = kernel.memory.get_memory_metrics(old_decision)["salience"]
        assert decision_salience > episodic_salience, (decision_salience, episodic_salience)

        # High-privacy routing policy still works.
        sensitive = kernel.preview_policy("Analyze these confidential student grades and student records.")
        assert sensitive["profile"]["privacy"] == "high", sensitive
        for role in ("planner", "executor", "critic"):
            hint = sensitive["decision"]["role_hints"][role]
            assert hint["allow_hosted"] is False, sensitive
            assert "openai" not in hint["preference"], sensitive
            assert "anthropic" not in hint["preference"], sensitive

        context_sensitive = kernel.policy.analyze(
            "Summarize that for me.",
            "general",
            context=[{"role": "memory", "content": "These are confidential student grades."}],
        )
        assert context_sensitive.privacy == "high", context_sensitive

        privacy_router = CognitiveModelRouter(
            {
                "fallback_to_mock": True,
                "providers": {"openai": {"model": "gpt-5.6-luna"}, "mock": {"model": "daqauntum-mock"}},
                "roles": {"executor": {"provider": "openai", "preference": ["openai", "mock"]}},
            }
        )
        privacy_resolved = privacy_router.resolve(
            "executor",
            routing={"preference": ["mock"], "allow_hosted": False, "model_tier": "strong"},
        )
        assert privacy_resolved["selected"]["provider"] == "mock", privacy_resolved

        # Deep requests still request stronger routing and independent review.
        complex_profile = kernel.policy.analyze(
            "Deep research: analyze, compare, evaluate, and design a rigorous statistical simulation architecture.",
            "researcher",
        )
        complex_decision = kernel.policy.decide(complex_profile)
        assert complex_profile.complexity >= 4, complex_profile
        assert complex_decision.role_hints["executor"]["model_tier"] == "strong", complex_decision
        assert complex_decision.independent_critic is True, complex_decision

        low_conf = ConfidenceEstimator.estimate(
            "I am uncertain and cannot verify this.",
            executor_fallback=True,
            critic_approved=False,
            critic_issues=["unsupported claim", "missing evidence"],
        )
        assert low_conf < kernel.policy.confidence_threshold, low_conf

        # Permission system remains enforced underneath memory/model routing.
        note_request = kernel.process("Save this as a note named brain-test")
        assert note_request["pending_approvals"], note_request
        assert not (root / "notes" / "brain-test.md").exists()
        approval_id = note_request["pending_approvals"][0]
        approved = kernel.approve(approval_id)
        assert approved["ok"], approved
        assert (root / "notes" / "brain-test.md").exists()

        low_permission = build_kernel(root, permission_level=1, db_name="low.db")
        denied = low_permission.process("Save this as a note named should-not-write")
        assert not denied["pending_approvals"], denied
        assert any(obs.get("status") == "denied" for obs in denied["observations"]), denied

        pm = PermissionManager(4)
        assert pm.evaluate(Action("write_note", 3, False)).outcome == "execute"
        assert pm.evaluate(Action("send_email", 3, False)).outcome == "confirm"

        # v0.2.7 Source Intelligence: ingest, hash, chunk, search, cite, graph, and evidence-link local artifacts.
        paper = root / "research-note.md"
        paper.write_text(
            "Quantum flux correlation test.\n\nThe measurement protocol uses repeated calibration and a control sample. "
            "This source is evidence only when explicitly linked to a claim.",
            encoding="utf-8",
        )
        source = kernel.ingest_source_file("research-note.md")
        assert source["status"] == "indexed", source
        assert source["chunk_count"] >= 1, source
        assert source["content_sha256"], source
        assert source["graph_node_id"], source

        source_hits = kernel.search_sources("repeated calibration control sample", limit=10)
        assert source_hits and source_hits[0]["source"]["id"] == source["id"], source_hits
        assert source_hits[0]["chunk"] is not None, source_hits
        chunk_id = source_hits[0]["chunk"]["id"]
        assert source_hits[0]["citation"] == f"[source:{source['id']} chunk:{chunk_id}]"

        # First-class source retrieval is available to the read-only tool layer.
        source_tool = kernel.tools.execute("source_search", {"query": "calibration control", "limit": 5})
        assert source_tool.ok and f"[source:{source['id']} chunk:" in source_tool.output, source_tool
        source_read = kernel.tools.execute("source_read", {"chunk_id": chunk_id})
        assert source_read.ok and "repeated calibration" in source_read.output.lower(), source_read

        # Source retrieval feeds normal cognition but does not auto-assert support.
        source_turn = kernel.process("What does the indexed source say about repeated calibration?")
        assert source_turn["retrieved_sources"], source_turn
        assert any(f"source:{source['id']}" in item["content"] for item in source_turn["retrieved_sources"]), source_turn
        source_node_neighbors = kernel.knowledge.neighbors(source["graph_node_id"], limit=50)
        assert not any(edge["relation"] == "supports" for edge in source_node_neighbors), source_node_neighbors

        # Explicit evidence relation creates graph authority with traceable source/chunk provenance.
        claim = kernel.remember("Repeated calibration is required by the current flux protocol.")
        claim_node = kernel.knowledge.primary_node_for_memory(claim["id"])
        assert claim_node, claim
        evidence = kernel.link_source_evidence(source["id"], "supports", claim_node, chunk_id=chunk_id)
        assert evidence["relation"] == "supports", evidence
        edge_provenance = kernel.knowledge.provenance(edge_id=evidence["edge_id"], limit=20)
        assert any(item["source_type"] == "source_evidence" and f"source:{source['id']}#chunk:{chunk_id}" == item["source_ref"] for item in edge_provenance), edge_provenance

        # Re-ingesting unchanged content deduplicates; changed content becomes a new superseding source version.
        same_source = kernel.ingest_source_file("research-note.md")
        assert same_source["id"] == source["id"], (same_source, source)
        paper.write_text(paper.read_text(encoding="utf-8") + "\nNew revision adds uncertainty analysis.", encoding="utf-8")
        revised = kernel.ingest_source_file("research-note.md")
        assert revised["id"] != source["id"], revised
        assert revised["supersedes_source_id"] == source["id"], revised
        revised_neighbors = kernel.knowledge.neighbors(revised["graph_node_id"], limit=20)
        assert any(edge["relation"] == "supersedes" and edge["other_node"]["id"] == source["graph_node_id"] for edge in revised_neighbors), revised_neighbors

        # URLs can be registered without network access; fetching remains opt-in/off by default.
        registered_url = kernel.register_source_url("https://example.com/research/paper")
        assert registered_url["status"] == "registered", registered_url
        assert registered_url["content_sha256"] is None, registered_url
        try:
            kernel.register_source_url("https://example.com/research/paper", fetch=True)
            raise AssertionError("URL fetch should be blocked when sources.allow_url_fetch is false")
        except PermissionError:
            pass

        # Source path sandbox prevents accidental ingestion outside configured project root.
        try:
            kernel.ingest_source_file("../outside.txt")
            raise AssertionError("Source path escape should be rejected")
        except ValueError:
            pass

        # v0.3.1 Call Mode: durable transcript, notes, task extraction, task execution, and approvals.
        call = kernel.calls.start("Smoke Test Call")
        assert call["status"] == "active", call
        call_state = kernel.calls.add_turn(call["id"], "We need to save this as a note named call-smoke")
        assert len(call_state["turns"]) == 2, call_state
        assert any("call-smoke" in task["description"].lower() for task in call_state["tasks"]), call_state
        ended_call = kernel.calls.end(call["id"])
        assert ended_call["status"] == "ended", ended_call
        assert ended_call["summary"], ended_call
        assert (root / "calls" / f"{call['id']}.md").exists()
        call_task = next(task for task in ended_call["tasks"] if "call-smoke" in task["description"].lower())
        task_result = kernel.calls.run_task(call_task["id"])
        pending = task_result["task"]["pending_approvals"]
        assert pending, task_result
        call_approval = pending[0]
        approved_call_task = kernel.approve(call_approval)
        kernel.calls.mark_approval(call_approval, approved_call_task["ok"], approved_call_task["message"])
        assert approved_call_task["ok"], approved_call_task
        assert kernel.calls.task(call_task["id"])["status"] == "completed"
        assert (root / "notes" / "call-smoke.md").exists()
        call_note = (root / "calls" / f"{call['id']}.md").read_text(encoding="utf-8")
        assert "[completed] Save this as a note named call-smoke" in call_note, call_note

        # Runtime model role switching remains independent by cognitive role.
        selected = kernel.set_model_role("planner", "mock")
        assert selected["selected"]["provider"] == "mock", selected
        selected = kernel.set_model_role("critic", "mock", "daqauntum-mock")
        assert selected["selected"]["model"] == "daqauntum-mock", selected

        try:
            kernel.set_model_role("teacher", "mock")
            raise AssertionError("Invalid cognitive role should fail")
        except ValueError:
            pass

        # A real v0.2.3-style database (messages + events only) upgrades in place.
        old_db = root / "v023.db"
        create_v023_database(old_db)
        upgraded = build_kernel(root, permission_level=2, db_name="v023.db")
        old_rows = upgraded.memory.recent(10)
        assert any(row["content"] == "old memory survives" for row in old_rows), old_rows
        upgraded.remember("new structured memory after in-place upgrade")
        assert upgraded.memory.memory_stats()["structured_total"] >= 1
        intel_stats = upgraded.memory.memory_intelligence_stats()
        assert intel_stats["embedded_memories"] >= 1, intel_stats

        # A real v0.2.4 structured-memory database is backfilled with metrics/vectors in place.
        old_v024 = root / "v024.db"
        create_v024_database(old_v024)
        upgraded_v024 = build_kernel(root, permission_level=2, db_name="v024.db")
        legacy_structured = upgraded_v024.memory.list_memories(kind="semantic", limit=10)
        assert legacy_structured and legacy_structured[0]["content"] == "Private information stays local.", legacy_structured
        legacy_id = legacy_structured[0]["id"]
        assert upgraded_v024.memory.get_memory_embedding(legacy_id) is not None
        assert upgraded_v024.memory.get_memory_metrics(legacy_id), legacy_id
        fuzzy = upgraded_v024.search_structured_memory("confidential data remains on device", limit=10)
        assert any(item["id"] == legacy_id for item in fuzzy), fuzzy
        legacy_graph = upgraded_v024.search_knowledge("private information", limit=10)
        assert legacy_graph, legacy_graph
        legacy_prov = upgraded_v024.knowledge_provenance(legacy_graph[0]["id"])
        assert any(item.get("memory_id") == legacy_id for item in legacy_prov), legacy_prov

        # v0.2.1 single-model YAML remains loadable.
        legacy_path = root / "legacy.yaml"
        legacy_path.write_text(
            yaml.safe_dump(
                {
                    "version": "legacy-test",
                    "model": {"provider": "mock", "fallback_to_mock": True},
                    "memory": {"db_path": str(root / "legacy.db")},
                    "tools": {"project_root": str(root), "notes_dir": "legacy-notes"},
                }
            ),
            encoding="utf-8",
        )
        legacy_kernel = DaQauntumKernel(str(legacy_path))
        legacy_status = legacy_kernel.status()
        assert legacy_status["roles"]["executor"]["provider"] == "mock", legacy_status
        assert "memory" in legacy_status, legacy_status

        status = kernel.status()
        assert "memory_search" in status["tools"]
        assert "knowledge_search" in status["tools"]
        assert "source_search" in status["tools"]
        assert "source_read" in status["tools"]
        assert "write_project_file" in status["tools"]
        assert "append_project_file" in status["tools"]
        assert status["calls"]["sessions"] >= 1
        assert status["sources"]["sources"] >= 3
        assert status["sources"]["chunks"] >= 2
        assert status["knowledge_graph"]["nodes"] > 0
        assert status["knowledge_graph"]["provenance_records"] > 0
        assert status["model_policy"] is True
        assert status["memory"]["structured_total"] > 0
        assert status["memory"]["intelligence"]["enabled"] is True
        assert status["memory"]["intelligence"]["embedded_memories"] > 0
        assert status["runtime"]["operation_mode"] == "auto", status["runtime"]
        assert status["universe"]["atom"]["atom_id"], status["universe"]
        assert status["universe"]["atom"]["level"] >= 1, status["universe"]
        print("DaQauntum v0.4.0 smoke test: PASS")


if __name__ == "__main__":
    main()
