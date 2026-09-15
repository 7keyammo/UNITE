from __future__ import annotations

import asyncio
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
from websockets.asyncio.client import connect


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_http(base: str, process: subprocess.Popen, timeout: float = 12.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GUI process exited early with {process.returncode}")
        try:
            if get_json(base + "/api/status").get("ok"):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(0.1)
    raise RuntimeError("GUI did not become ready")


async def recv_json(ws, timeout: float = 10.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    if isinstance(raw, bytes):
        raise AssertionError("Expected JSON text event")
    return json.loads(raw)


async def exercise(ws_url: str) -> None:
    async with connect(ws_url, max_size=8_000_000) as ws:
        hello = await recv_json(ws)
        assert hello["type"] == "hello" and hello["capabilities"]["binary_pcm16"] is True, hello
        await ws.send(json.dumps({"type": "session.start", "create_call": True, "title": "Duplex Smoke", "interaction_mode": "voice_call"}))
        ready = await recv_json(ws)
        assert ready["type"] == "session.ready" and ready["session"]["call_id"], ready

        # Persistent text turn -> streamed model response on the same connection.
        await ws.send(json.dumps({"type": "turn.text", "text": "Give me a short physics greeting."}))
        seen = []
        while True:
            event = await recv_json(ws)
            seen.append(event.get("type"))
            if event.get("type") in {"done", "interrupted"}:
                break
        assert "user.final" in seen and "delta" in seen and seen[-1] == "done", seen

        # Binary PCM -> local STT -> transcript -> model turn, still on the same socket.
        await ws.send(json.dumps({"type": "audio.start", "sample_rate": 16000, "channels": 1}))
        assert (await recv_json(ws))["type"] == "audio.ready"
        await ws.send(b"\x00\x00" * 3200)
        await ws.send(json.dumps({"type": "audio.commit"}))
        transcript = None
        finished = False
        deadline = time.time() + 12
        while time.time() < deadline and not finished:
            event = await recv_json(ws)
            if event.get("type") == "transcript.final":
                transcript = event.get("text")
            if event.get("type") == "done":
                finished = True
        assert transcript and "duplex-smoke" in transcript, transcript
        assert finished

        await ws.send(json.dumps({"type": "status"}))
        status = await recv_json(ws)
        assert status["type"] == "status" and status["session"]["turns"] >= 2, status

        await ws.send(json.dumps({"type": "session.end", "process_tasks": False}))
        ended = await recv_json(ws)
        assert ended["type"] == "session.ended" and ended["call"]["status"] == "ended", ended


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="daqauntum-duplex-") as tmp:
        tmp_root = Path(tmp)
        http_port = free_port()
        ws_port = free_port()
        project = tmp_root / "project"
        project.mkdir()
        config = {
            "version": "0.4.0-duplex-test",
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
            "runtime": {"operation_mode": "local", "cognition_mode": "realtime", "modules": {"memory": True, "knowledge": True, "sources": False, "planner": True, "critic": True, "voice": True, "streaming": True, "barge_in": True, "duplex": True}},
            "memory": {"db_path": str(tmp_root / "memory.db"), "max_recent_messages": 8, "structured_enabled": True, "structured_retrieve_limit": 4, "auto_episode": True, "auto_decisions": True, "auto_procedures": True, "auto_semantic_cues": True, "training_confidence_threshold": 0.85, "intelligence_enabled": True, "embedding_dimensions": 64, "auto_maintain_every": 0},
            "knowledge_graph": {"enabled": True, "context_limit": 3, "backfill_on_start": True},
            "sources": {"enabled": False, "project_root": str(project)},
            "tools": {"project_root": str(project), "notes_dir": "notes"},
            "call": {"calls_dir": str(tmp_root / "calls"), "save_transcripts": True},
            "voice": {"stt": {"backend": "mock", "mock_transcript": "DaQauntum duplex-smoke audio turn"}, "tts": {"backend": "mock"}},
            "gui": {"host": "127.0.0.1", "port": http_port, "websocket_port": ws_port},
            "full_duplex": {"enabled": True, "partial_stt_enabled": False, "max_audio_bytes": 8_000_000},
        }
        cfg = tmp_root / "config.yaml"
        cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
        log = tmp_root / "server.log"
        with log.open("w", encoding="utf-8") as out:
            process = subprocess.Popen(
                [sys.executable, "daqauntum_gui.py", "--no-browser", "--config", str(cfg), "--port", str(http_port), "--ws-port", str(ws_port)],
                cwd=root,
                stdout=out,
                stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONPATH": str(root)},
            )
            try:
                wait_http(f"http://127.0.0.1:{http_port}", process)
                asyncio.run(exercise(f"ws://127.0.0.1:{ws_port}"))
                print("DaQauntum v0.4.0 duplex smoke test: PASS")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    main()
