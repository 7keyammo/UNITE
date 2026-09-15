from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(base: str, path: str, payload: dict | None = None) -> dict:
    if payload is None:
        request = urllib.request.Request(base + path, method="GET")
    else:
        request = urllib.request.Request(
            base + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def request_stream(base: str, path: str, payload: dict) -> list[dict]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    events = []
    with urllib.request.urlopen(request, timeout=20) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line:
                events.append(json.loads(line))
    return events


def request_bytes(base: str, path: str, data: bytes, content_type: str = "application/octet-stream") -> dict:
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": content_type},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def tiny_wav() -> bytes:
    import io
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def wait_until_ready(base: str, process: subprocess.Popen, timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GUI server exited early with code {process.returncode}")
        try:
            status = request_json(base, "/api/status")
            if status.get("ok"):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.15)
    raise RuntimeError("GUI server did not become ready")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="daqauntum-gui-smoke-") as tmp:
        tmp_root = Path(tmp)
        project = tmp_root / "project"
        project.mkdir()
        calls_dir = tmp_root / "calls"
        port = free_port()
        ws_port = free_port()
        config = {
            "version": "0.4.0-gui-test",
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
                "db_path": str(tmp_root / "gui.db"),
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
                "auto_maintain_every": 0,
            },
            "knowledge_graph": {"enabled": True, "context_limit": 5, "backfill_on_start": True},
            "sources": {
                "enabled": True,
                "project_root": str(project),
                "allow_external_paths": False,
                "allow_url_fetch": False,
                "allow_private_networks": False,
            },
            "connectors": {"enabled": True, "device_inbox_dir": str(tmp_root / "device_inbox"), "max_files_per_sync": 50},
            "device_bridge": {"enabled": False},
            "tools": {"project_root": str(project), "notes_dir": "notes"},
            "gui": {"host": "127.0.0.1", "port": port, "websocket_port": ws_port},
            "full_duplex": {"enabled": True, "partial_stt_enabled": True, "partial_stt_interval_ms": 150, "partial_stt_min_ms": 10},
            "call": {"calls_dir": str(calls_dir), "max_transcript_chars": 12000, "save_transcripts": True},
            "learning": {"enabled": True, "project_root": str(project), "reports_dir": "learning/reports", "outbox_dir": "learning/outbox", "operation_mode": "local"},
            "native_model": {"dataset_dir": "native/datasets", "fine_tuning_enabled": False},
            "workspaces": {"enabled": True, "root": str(tmp_root / "workspaces"), "obsidian_root": str(tmp_root / "obsidian"), "max_parallel_agents": 3},
            "voice": {
                "prefer_local": True,
                "max_audio_bytes": 8000000,
                "stt": {"backend": "mock", "mock_transcript": "We need to save this as a note named voice-smoke"},
                "tts": {"backend": "mock"},
                "wake": {"enabled": False, "phrase": "daqauntum"},
            },
        }
        config_path = tmp_root / "config.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        log_path = tmp_root / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [sys.executable, "daqauntum_gui.py", "--no-browser", "--config", str(config_path), "--port", str(port)],
                cwd=root,
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(root)},
            )
            base = f"http://127.0.0.1:{port}"
            try:
                wait_until_ready(base, process)
                status = request_json(base, "/api/status")["status"]
                assert status["version"] == "0.4.0-gui-test", status
                assert status["duplex"]["enabled"] is True and status["duplex"]["port"] == ws_port, status["duplex"]
                assert status["voice"]["stt"]["backend"] == "mock", status["voice"]
                assert status["voice"]["tts"]["backend"] == "mock", status["voice"]
                assert "write_project_file" in status["tools"]
                runtime = request_json(base, "/api/runtime")["runtime"]
                assert runtime["operation_mode"] == "auto", runtime
                assert runtime["modules"]["streaming"] is True and runtime["modules"]["barge_in"] is True and runtime["modules"]["duplex"] is True and runtime["modules"]["connectors"] is True, runtime
                changed = request_json(base, "/api/runtime/modes", {"operation_mode": "local", "cognition_mode": "realtime"})
                assert changed["runtime"]["operation_mode"] == "local", changed
                universe = request_json(base, "/api/universe")["universe"]
                assert universe["atom"]["atom_id"] and universe["network"]["connected"] is False, universe
                request_json(base, "/api/runtime/modes", {"operation_mode": "auto", "cognition_mode": "auto"})

                html = urllib.request.urlopen(base + "/", timeout=20).read().decode("utf-8")
                assert "CALL DAQAUNTUM" in html and "COGNITIVE ATOM" in html and "DAQAUNTUM UNIVERSE" in html and "AUTONOMOUS LEARNING" in html and "CONNECTED KNOWLEDGE" in html and "BUILD A DAQAUNTUM OS" in html and "APIFY DATASET" in html and "Access Studio" in html
                assert 'id="bargeIn"' in html and 'id="turnPause"' in html and 'id="fullDuplex"' in html and 'id="duplexChip"' in html, html

                built = request_json(base, "/api/workspaces/create", {"name": "GUI Research OS", "focus": "Produce research briefs", "done_looks_like": "A cited brief", "stage": "manual"})["workspace"]
                request_json(base, "/api/workspaces/active", {"workspace_id": built["id"]})
                skill = request_json(base, "/api/workspaces/skill", {"workspace_id": built["id"], "name": "Evidence Check", "description": "Check whether a claim has sufficient evidence"})["skill"]
                agent = request_json(base, "/api/workspaces/agent", {"workspace_id": built["id"], "name": "Reviewer", "description": "Skeptical research reviewer", "skills": [skill["slug"]]})["agent"]
                connection = request_json(base, "/api/workspaces/connection", {"workspace_id": built["id"], "name": "Research MCP", "kind": "mcp", "env_names": ["RESEARCH_API_KEY"], "config": {}})["connection"]
                assert connection["executable"] == 0 and connection["env_names"] == ["RESEARCH_API_KEY"], connection
                job = request_json(base, "/api/workbench/run", {"workspace_id": built["id"], "agent_slug": agent["slug"], "prompt": "Review claim A"})["job"]
                deadline = time.time() + 5
                job_state = None
                while time.time() < deadline:
                    jobs = request_json(base, f"/api/workbench/jobs?workspace_id={built['id']}")["jobs"]
                    job_state = next((x for x in jobs if x["id"] == job["id"]), None)
                    if job_state and job_state["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.05)
                assert job_state and job_state["status"] == "completed", job_state
                exported = request_json(base, "/api/workspaces/obsidian-export", {})["result"]
                assert exported["workspaces"] == 1 and Path(exported["home"]).exists(), exported

                connected_dir = tmp_root / "connected-folder"
                connected_dir.mkdir()
                (connected_dir / "gui-source.md").write_text("GUI connected knowledge smoke evidence", encoding="utf-8")
                added_connector = request_json(base, "/api/connectors/add-folder", {"path": str(connected_dir), "name": "GUI Connected Folder", "learn_enabled": True})["connector"]
                synced_connector = request_json(base, "/api/connectors/sync", {"connector_id": added_connector["id"]})["result"]
                assert synced_connector["indexed"] == 1, synced_connector
                connector_state = request_json(base, "/api/connectors")
                assert connector_state["stats"]["connectors"] >= 2 and connector_state["device_bridge"]["enabled"] is False, connector_state

                learning = request_json(base, "/api/learning")
                assert learning["learning"]["enabled"] is True, learning
                assert learning["native_model"]["fine_tuning_enabled"] is False, learning

                voice_status = request_json(base, "/api/voice/status")["voice"]
                assert voice_status["stt"]["available"] and voice_status["tts"]["available"], voice_status
                voice_tx = request_bytes(base, "/api/voice/transcribe", tiny_wav(), "audio/wav")
                assert "voice-smoke" in voice_tx["result"]["text"], voice_tx
                voice_sp = request_json(base, "/api/voice/speak", {"text": "Local voice smoke test"})
                assert voice_sp["result"]["local"] is True, voice_sp

                stream_events = request_stream(base, "/api/chat/stream", {"text": "Give me a quick realtime physics greeting."})
                kinds = [e.get("type") for e in stream_events]
                assert kinds[0] == "start" and "delta" in kinds and kinds[-1] == "done", stream_events
                done = stream_events[-1]
                assert done["result"]["effective_cognition"] == "realtime", done
                assert done["realtime"]["time_to_first_token_ms"] is not None, done

                started = request_json(base, "/api/call/start", {"title": "GUI Smoke Call"})["call"]
                sid = started["id"]
                call_events = request_stream(
                    base, "/api/call/turn/stream",
                    {"session_id": sid, "text": "We need to save this as a note named gui-smoke"},
                )
                assert call_events[0]["type"] == "start" and call_events[-1]["type"] == "done", call_events
                turn = call_events[-1]["call"]
                assert turn["tasks"], turn
                assert not turn["last_response"]["pending_approvals"], turn["last_response"]
                assert any(o.get("status") == "deferred_call_mode" for o in turn["last_response"]["observations"])

                ended_payload = request_json(base, "/api/call/end", {"session_id": sid, "process_tasks": True})
                ended = ended_payload["call"]
                assert ended["status"] == "ended" and ended["summary"]
                task = ended["tasks"][0]
                pending = task["pending_approvals"]
                assert pending, ended_payload
                approval = request_json(base, "/api/approve", {"approval_id": pending[0]})
                assert approval["result"]["ok"], approval
                final_call = request_json(base, f"/api/call?session_id={sid}")["call"]
                assert final_call["tasks"][0]["status"] == "completed", final_call["tasks"][0]
                assert (project / "notes" / "gui-smoke.md").exists()
                call_note = (calls_dir / f"{sid}.md").read_text(encoding="utf-8")
                assert "[completed] Save this as a note named gui-smoke" in call_note, call_note

                chat = request_json(base, "/api/chat", {"text": "Give me a short physics check."})
                assert chat["result"]["response"]
                print("DaQauntum v0.4.0 GUI smoke test: PASS")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
