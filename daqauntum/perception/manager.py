from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any


class PerceptionManager:
    """Local-first visual frame store + optional multimodal analysis.

    Capturing a frame and interpreting a frame are separate operations. Frames are
    persisted locally; hosted analysis is only considered when runtime policy allows it.
    """

    def __init__(self, memory, config: dict[str, Any] | None = None):
        self.memory = memory
        self.config = config or {}
        self.root = Path(str(self.config.get("frames_dir", "data/perception/frames"))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(self.config.get("max_frame_bytes", 8_000_000))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS perception_frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_type TEXT NOT NULL,
                file_path TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                width INTEGER,
                height INTEGER,
                label TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                analysis TEXT,
                analysis_provider TEXT,
                analysis_model TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_perception_frames_type ON perception_frames(frame_type, id DESC)"
        )
        self.memory.conn.commit()

    @staticmethod
    def _safe_type(value: str) -> str:
        value = (value or "screen").strip().lower()
        return value if value in {"screen", "camera", "image", "verification"} else "image"

    def add_frame(
        self,
        image_bytes: bytes,
        *,
        frame_type: str = "screen",
        mime_type: str = "image/jpeg",
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not image_bytes:
            raise ValueError("Image frame is empty")
        if len(image_bytes) > self.max_bytes:
            raise ValueError(f"Image exceeds maximum frame size ({self.max_bytes} bytes)")
        frame_type = self._safe_type(frame_type)
        digest = hashlib.sha256(image_bytes).hexdigest()
        suffix = ".png" if "png" in mime_type else ".jpg"
        filename = f"{frame_type}-{digest[:16]}{suffix}"
        path = self.root / filename
        if not path.exists():
            path.write_bytes(image_bytes)
        width = height = None
        try:
            from PIL import Image  # type: ignore
            with Image.open(path) as img:
                width, height = img.size
        except Exception:
            pass
        cursor = self.memory.conn.execute(
            """
            INSERT INTO perception_frames(frame_type, file_path, mime_type, sha256, width, height, label, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                frame_type,
                str(path),
                mime_type,
                digest,
                width,
                height,
                (label or "").strip() or None,
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.memory.conn.commit()
        frame_id = int(cursor.lastrowid)
        self.memory.add_event("perception_frame", {"frame_id": frame_id, "frame_type": frame_type, "sha256": digest})
        return self.get(frame_id) or {}

    def add_data_url(self, data_url: str, **kwargs: Any) -> dict[str, Any]:
        if "," not in data_url:
            raise ValueError("Invalid image data URL")
        header, encoded = data_url.split(",", 1)
        mime = "image/jpeg"
        if header.startswith("data:") and ";" in header:
            mime = header[5:].split(";", 1)[0] or mime
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Invalid base64 image") from exc
        return self.add_frame(raw, mime_type=mime, **kwargs)

    def _row(self, row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.pop("metadata_json") or "{}")
        except Exception:
            data["metadata"] = {}
            data.pop("metadata_json", None)
        return data

    def get(self, frame_id: int) -> dict[str, Any] | None:
        row = self.memory.conn.execute("SELECT * FROM perception_frames WHERE id = ?", (int(frame_id),)).fetchone()
        return self._row(row) if row else None

    def latest(self, frame_type: str | None = None) -> dict[str, Any] | None:
        if frame_type:
            row = self.memory.conn.execute(
                "SELECT * FROM perception_frames WHERE frame_type = ? ORDER BY id DESC LIMIT 1",
                (self._safe_type(frame_type),),
            ).fetchone()
        else:
            row = self.memory.conn.execute("SELECT * FROM perception_frames ORDER BY id DESC LIMIT 1").fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute(
            "SELECT * FROM perception_frames ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 100)),)
        ).fetchall()
        return [self._row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        row = self.memory.conn.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN frame_type='screen' THEN 1 ELSE 0 END) screens, "
            "SUM(CASE WHEN frame_type='camera' THEN 1 ELSE 0 END) cameras, "
            "SUM(CASE WHEN analysis IS NOT NULL THEN 1 ELSE 0 END) analyzed FROM perception_frames"
        ).fetchone()
        latest = self.latest()
        return {
            "frames": int(row["total"] or 0),
            "screens": int(row["screens"] or 0),
            "cameras": int(row["cameras"] or 0),
            "analyzed": int(row["analyzed"] or 0),
            "latest": latest,
            "vision": self.vision_status(),
        }

    def vision_status(self) -> dict[str, Any]:
        ollama_model = str(self.config.get("ollama_model", os.getenv("DAQAUNTUM_VISION_OLLAMA_MODEL", ""))).strip()
        openai_model = str(self.config.get("openai_model", "gpt-5.6-sol")).strip()
        anthropic_model = str(self.config.get("anthropic_model", "claude-sonnet-5")).strip()
        return {
            "ollama": {"configured": bool(ollama_model), "model": ollama_model or None},
            "openai": {"configured": bool(os.getenv("OPENAI_API_KEY")), "model": openai_model},
            "anthropic": {"configured": bool(os.getenv("ANTHROPIC_API_KEY")), "model": anthropic_model},
        }

    def analyze(self, frame_id: int, prompt: str, *, operation_mode: str = "auto") -> dict[str, Any]:
        frame = self.get(frame_id)
        if not frame:
            raise ValueError(f"Perception frame #{frame_id} not found")
        prompt = (prompt or "Describe what is visible. Focus on information useful for assisting the user.").strip()
        path = Path(frame["file_path"])
        image = path.read_bytes()
        providers = self._provider_order(operation_mode)
        errors: list[str] = []
        for provider in providers:
            try:
                if provider == "ollama":
                    text, model = self._analyze_ollama(image, prompt)
                elif provider == "openai":
                    text, model = self._analyze_openai(image, frame["mime_type"], prompt)
                elif provider == "anthropic":
                    text, model = self._analyze_anthropic(image, frame["mime_type"], prompt)
                else:
                    continue
                self.memory.conn.execute(
                    "UPDATE perception_frames SET analysis = ?, analysis_provider = ?, analysis_model = ? WHERE id = ?",
                    (text, provider, model, frame_id),
                )
                self.memory.conn.commit()
                self.memory.add_event("perception_analysis", {"frame_id": frame_id, "provider": provider, "model": model})
                return {"frame": self.get(frame_id), "text": text, "provider": provider, "model": model, "fallback": False}
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
        metadata = f"Captured {frame['frame_type']} frame #{frame_id} ({frame.get('width') or '?'}x{frame.get('height') or '?'}, sha256 {frame['sha256'][:12]}…)."
        text = metadata + " No configured vision model was available, so DaQauntum stored the frame but did not infer visual content."
        return {"frame": frame, "text": text, "provider": "metadata", "model": "none", "fallback": True, "errors": errors}

    def _provider_order(self, operation_mode: str) -> list[str]:
        mode = (operation_mode or "auto").lower()
        if mode == "local":
            return ["ollama"]
        if mode == "cloud":
            return ["openai", "anthropic", "ollama"]
        if mode == "hybrid":
            return ["ollama", "openai", "anthropic"]
        return ["ollama", "openai", "anthropic"]

    def _analyze_ollama(self, image: bytes, prompt: str) -> tuple[str, str]:
        model = str(self.config.get("ollama_model", os.getenv("DAQAUNTUM_VISION_OLLAMA_MODEL", ""))).strip()
        if not model:
            raise RuntimeError("DAQAUNTUM_VISION_OLLAMA_MODEL is not configured")
        base = str(self.config.get("ollama_base_url", "http://127.0.0.1:11434")).rstrip("/")
        body = json.dumps({"model": model, "stream": False, "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(image).decode("ascii")]}]}).encode("utf-8")
        req = urllib.request.Request(base + "/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_seconds", 90))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data.get("message", {}).get("content", "")).strip(), model

    def _analyze_openai(self, image: bytes, mime: str, prompt: str) -> tuple[str, str]:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        from openai import OpenAI  # type: ignore
        model = str(self.config.get("openai_model", "gpt-5.6-sol"))
        encoded = base64.b64encode(image).decode("ascii")
        client = OpenAI()
        response = client.responses.create(
            model=model,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"},
            ]}],
        )
        return str(response.output_text or "").strip(), model

    def _analyze_anthropic(self, image: bytes, mime: str, prompt: str) -> tuple[str, str]:
        token = os.getenv("ANTHROPIC_API_KEY", "")
        if not token:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        model = str(self.config.get("anthropic_model", "claude-sonnet-5"))
        body = json.dumps({
            "model": model,
            "max_tokens": int(self.config.get("max_tokens", 1200)),
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(image).decode("ascii")}},
                {"type": "text", "text": prompt},
            ]}],
        }).encode("utf-8")
        req = urllib.request.Request(
            str(self.config.get("anthropic_base_url", "https://api.anthropic.com")).rstrip("/") + "/v1/messages",
            data=body,
            headers={"Content-Type": "application/json", "x-api-key": token, "anthropic-version": "2023-06-01"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=float(self.config.get("timeout_seconds", 90))) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        parts = data.get("content") or []
        text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("type") == "text")
        return text.strip(), model
