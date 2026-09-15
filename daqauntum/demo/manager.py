from __future__ import annotations

import time
import uuid
from typing import Any


class DemoManager:
    """Guided capability tour for a live DaQauntum instance."""

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.config = config or {}
        self.session: dict[str, Any] | None = None

    def _capability_snapshot(self) -> dict[str, Any]:
        voice = self.kernel.voice.status(refresh=False) if hasattr(self.kernel, "voice") else {}
        try:
            resolved = self.kernel.brain.resolve("executor", routing=self.kernel.runtime.routing_hint("executor", {}, "realtime"))
            brain_ready = bool(resolved.get("selected"))
        except Exception:
            brain_ready = False
        modules = getattr(self.kernel.runtime, "modules", {})
        return {
            "brain": brain_ready,
            "voice_stt": bool((voice.get("stt") or {}).get("available")),
            "voice_tts": bool((voice.get("tts") or {}).get("available")),
            "duplex": bool(getattr(self.kernel, "duplex_endpoint", {}).get("enabled")),
            "memory": bool(modules.get("memory", True)),
            "presence": bool(modules.get("presence", True)),
            "perception": bool(modules.get("perception", True)),
            "computer": bool(modules.get("computer", True)),
            "connectors": bool(modules.get("connectors", True)),
            "learning": bool(modules.get("learning", True)),
        }

    def build_steps(self) -> list[dict[str, Any]]:
        caps = self._capability_snapshot()
        presence = self.kernel.presence.latest() if hasattr(self.kernel, "presence") else {}
        voice = self.kernel.voice.status(refresh=False) if hasattr(self.kernel, "voice") else {}
        steps = [
            {
                "id": "hello",
                "title": "Meet DaQauntum",
                "say": "Hello. I am DaQauntum version 0.4.0. I can talk with you, remember our work, learn from connected sources, and operate approved tools through permission gates.",
                "instruction": "This is the same persistent atom across chat, calls, workspaces, learning, and connected devices.",
                "ready": True,
            },
            {
                "id": "brain",
                "title": "Brain and compute modes",
                "say": "My brain can route between local, hybrid, and cloud models. Realtime mode is optimized for conversation, while Deep mode uses planner, executor, and critic passes for serious work.",
                "instruction": "Try asking: 'Explain what mode you are using right now.'",
                "ready": caps["brain"],
            },
            {
                "id": "voice",
                "title": "Two-way voice",
                "say": "Open Call Mode and talk to me. I can transcribe locally when Whisper is configured, stream my response, speak it back, and let you interrupt me.",
                "instruction": "Use Call Mode. If local STT is unavailable, browser speech or typed call mode remains available.",
                "ready": caps["voice_stt"] or caps["voice_tts"],
                "detail": voice,
            },
            {
                "id": "presence",
                "title": "Awareness beyond prompts",
                "say": "My Presence Layer can passively observe approved local signals such as network state, battery, temperature, cameras, serial devices, and paired Bluetooth devices. Active Wi-Fi or Bluetooth scans require an explicit request.",
                "instruction": "Open Presence and click Refresh Awareness. Then try an explicit Wi-Fi or Bluetooth scan.",
                "ready": caps["presence"],
                "detail": presence,
            },
            {
                "id": "eyes",
                "title": "Eyes",
                "say": "I can receive a screen or camera frame only after your browser grants access. Visual understanding follows your local, hybrid, or cloud privacy mode.",
                "instruction": "Open Eyes and Hands, capture your screen, then ask: 'DaQauntum, what do you see?'",
                "ready": caps["perception"],
            },
            {
                "id": "hands",
                "title": "Hands and approvals",
                "say": "Computer and device actions are separate from observation. At the normal permission level, I prepare state-changing actions and wait for your approval before execution.",
                "instruction": "Ask for a harmless computer action and watch it appear in Pending Approvals.",
                "ready": caps["computer"],
            },
            {
                "id": "connections",
                "title": "Connected knowledge",
                "say": "I can learn from folders, your phone inbox, web feeds, research sources, and optional external integrations. Sources remain traceable through provenance.",
                "instruction": "Connect a folder or send a file from your phone bridge, then search for it in Connected Knowledge.",
                "ready": caps["connectors"],
            },
            {
                "id": "learning",
                "title": "Autonomous learning",
                "say": "At seven in the morning I can run a Deep learning session and at seven in the evening build a digest. Approved training traces feed the future DaQauntum native model dataset without silently changing model weights.",
                "instruction": "Open Learning and choose Learn now to run the cycle manually.",
                "ready": caps["learning"],
            },
            {
                "id": "finish",
                "title": "Your DaQauntum is alive",
                "say": "That is the core loop: sense, remember, reason, ask permission, act, verify, and learn. You can now use me as a persistent local-first intelligence instead of a sandbox demo.",
                "instruction": "Start with a real project and let the atom accumulate experience.",
                "ready": True,
            },
        ]
        return steps

    def start(self) -> dict[str, Any]:
        self.session = {
            "id": uuid.uuid4().hex[:12],
            "started_at": time.time(),
            "index": 0,
            "steps": self.build_steps(),
            "completed": False,
        }
        self.kernel.memory.add_event("demo_started", {"session_id": self.session["id"]})
        return self.state()

    def state(self) -> dict[str, Any]:
        if not self.session:
            return {"active": False, "session_id": None, "index": 0, "total": 0, "current": None, "completed": False}
        steps = self.session["steps"]
        idx = min(int(self.session["index"]), max(0, len(steps) - 1))
        return {
            "active": not bool(self.session.get("completed")),
            "session_id": self.session["id"],
            "index": idx,
            "total": len(steps),
            "current": steps[idx] if steps else None,
            "completed": bool(self.session.get("completed")),
            "steps": steps,
        }

    def next(self) -> dict[str, Any]:
        if not self.session:
            return self.start()
        if self.session["index"] + 1 >= len(self.session["steps"]):
            self.session["completed"] = True
            self.kernel.memory.add_event("demo_completed", {"session_id": self.session["id"]})
        else:
            self.session["index"] += 1
        return self.state()

    def previous(self) -> dict[str, Any]:
        if not self.session:
            return self.start()
        self.session["completed"] = False
        self.session["index"] = max(0, int(self.session["index"]) - 1)
        return self.state()

    def speak_current(self) -> dict[str, Any]:
        state = self.state()
        current = state.get("current") or {}
        text = str(current.get("say") or "").strip()
        if not text:
            return {"ok": False, "message": "No demo step is available to speak."}
        result = self.kernel.voice.speak(text)
        return {"ok": True, "text": text, "voice": result}

    def readiness(self) -> dict[str, Any]:
        caps = self._capability_snapshot()
        score = round(100.0 * sum(1 for v in caps.values() if v) / max(1, len(caps)))
        return {"score": score, "capabilities": caps, "steps": self.build_steps()}
