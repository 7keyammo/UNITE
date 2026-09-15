from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any


class LocalVoiceEngine:
    """Local-first speech engine with optional faster-whisper/whisper.cpp STT and local TTS.

    Browser speech remains a UI fallback. This engine never sends audio to a hosted API.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.stt_cfg = self.config.get("stt", {})
        self.tts_cfg = self.config.get("tts", {})
        self.max_audio_bytes = int(self.config.get("max_audio_bytes", 8_000_000))
        self.max_speak_chars = int(self.config.get("max_speak_chars", 4000))
        self._fw_model = None
        self._status_cache: dict[str, Any] | None = None
        self._tts_lock = threading.RLock()
        self._tts_process: subprocess.Popen | None = None

    # ------------------------------------------------------------------ status
    def status(self, refresh: bool = False) -> dict[str, Any]:
        if self._status_cache is not None and not refresh:
            return self._status_cache
        stt = self._detect_stt()
        tts = self._detect_tts()
        wake = self.config.get("wake", {})
        self._status_cache = {
            "local_only": True,
            "stt": stt,
            "tts": tts,
            "wake": {
                "enabled": bool(wake.get("enabled", False)),
                "phrase": str(wake.get("phrase", "daqauntum")),
            },
        }
        return self._status_cache

    def _detect_stt(self) -> dict[str, Any]:
        requested = str(self.stt_cfg.get("backend", "auto")).lower()
        candidates = []
        if requested == "auto":
            candidates = ["faster_whisper", "whisper_cpp"]
        else:
            candidates = [requested]

        for backend in candidates:
            if backend == "faster_whisper":
                info = self._faster_whisper_status()
            elif backend == "whisper_cpp":
                info = self._whisper_cpp_status()
            elif backend == "mock":
                info = {"backend": "mock", "available": True, "detail": "test-only local STT"}
            elif backend in {"off", "none", "disabled"}:
                return {"backend": "none", "available": False, "detail": "Local STT disabled"}
            else:
                info = {"backend": backend, "available": False, "detail": "Unknown STT backend"}
            if info.get("available"):
                return info

        return {
            "backend": "none",
            "available": False,
            "detail": "Install/configure faster-whisper or whisper.cpp; browser speech can still be used as fallback.",
        }

    def _faster_whisper_status(self) -> dict[str, Any]:
        if importlib.util.find_spec("faster_whisper") is None:
            return {"backend": "faster_whisper", "available": False, "detail": "Python package not installed"}
        cfg = self.stt_cfg.get("faster_whisper", {})
        model = str(cfg.get("model", "base.en"))
        allow_download = bool(cfg.get("allow_model_download", False))
        model_path = Path(model).expanduser()
        ready = model_path.exists() or allow_download
        detail = (
            f"model={model}; local model path ready"
            if model_path.exists()
            else (f"model={model}; model download allowed on first use" if allow_download else f"model={model}; model must already exist locally or enable allow_model_download")
        )
        return {"backend": "faster_whisper", "available": ready, "detail": detail, "model": model}

    def _whisper_cpp_status(self) -> dict[str, Any]:
        cfg = self.stt_cfg.get("whisper_cpp", {})
        binary = str(cfg.get("binary", "whisper-cli"))
        resolved = shutil.which(binary) if not Path(binary).exists() else str(Path(binary).expanduser())
        model = Path(str(cfg.get("model", ""))).expanduser() if cfg.get("model") else None
        available = bool(resolved and model and model.exists())
        detail = f"binary={resolved or binary}; model={model or 'not configured'}"
        return {"backend": "whisper_cpp", "available": available, "detail": detail, "binary": resolved, "model": str(model) if model else None}

    def _detect_tts(self) -> dict[str, Any]:
        requested = str(self.tts_cfg.get("backend", "auto")).lower()
        candidates = ["piper", "system"] if requested == "auto" else [requested]
        for backend in candidates:
            if backend == "piper":
                info = self._piper_status()
            elif backend == "system":
                info = self._system_tts_status()
            elif backend == "mock":
                info = {"backend": "mock", "available": True, "detail": "test-only local TTS"}
            elif backend in {"off", "none", "disabled"}:
                return {"backend": "none", "available": False, "detail": "Local TTS disabled"}
            else:
                info = {"backend": backend, "available": False, "detail": "Unknown TTS backend"}
            if info.get("available"):
                return info
        return {"backend": "none", "available": False, "detail": "No local TTS backend detected; browser speech synthesis can be used as fallback."}

    def _piper_status(self) -> dict[str, Any]:
        cfg = self.tts_cfg.get("piper", {})
        binary = str(cfg.get("binary", "piper"))
        resolved = shutil.which(binary) if not Path(binary).exists() else str(Path(binary).expanduser())
        model_value = str(cfg.get("model", "")).strip()
        model = Path(model_value).expanduser() if model_value else None
        allow_download = bool(cfg.get("allow_model_download", False))
        available = bool(resolved and model_value and ((model and model.exists()) or allow_download))
        return {
            "backend": "piper",
            "available": available,
            "detail": f"binary={resolved or binary}; model={model_value or 'not configured'}",
            "binary": resolved,
            "model": model_value or None,
        }

    def _system_tts_status(self) -> dict[str, Any]:
        system = platform.system().lower()
        if system == "darwin" and shutil.which("say"):
            return {"backend": "system", "available": True, "detail": "macOS say", "engine": "say"}
        if system == "linux":
            binary = shutil.which("espeak-ng") or shutil.which("espeak")
            if binary:
                return {"backend": "system", "available": True, "detail": Path(binary).name, "engine": binary}
        if system == "windows" and (shutil.which("powershell") or shutil.which("powershell.exe")):
            return {"backend": "system", "available": True, "detail": "Windows System.Speech", "engine": "powershell"}
        return {"backend": "system", "available": False, "detail": f"No supported system TTS found on {platform.system()}"}

    # --------------------------------------------------------------- transcription
    def transcribe_wav_bytes(self, audio: bytes, realtime: bool = False) -> dict[str, Any]:
        if not audio:
            raise ValueError("Audio payload is empty")
        if len(audio) > self.max_audio_bytes:
            raise ValueError(f"Audio payload exceeds {self.max_audio_bytes} bytes")
        if not audio.startswith(b"RIFF") or b"WAVE" not in audio[:16]:
            raise ValueError("Local STT expects PCM WAV audio")

        status = self.status(refresh=True)["stt"]
        if not status.get("available"):
            raise RuntimeError(status.get("detail", "Local STT unavailable"))

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio)
            path = Path(handle.name)
        try:
            if status["backend"] == "faster_whisper":
                text = self._transcribe_faster_whisper(path, realtime=realtime)
            elif status["backend"] == "whisper_cpp":
                text = self._transcribe_whisper_cpp(path)
            elif status["backend"] == "mock":
                text = str(self.stt_cfg.get("mock_transcript", "DaQauntum local voice test"))
            else:
                raise RuntimeError("No supported local STT backend selected")
        finally:
            path.unlink(missing_ok=True)

        text = " ".join(str(text).strip().split())
        return {"text": text, "backend": status["backend"], "local": True}

    def _transcribe_faster_whisper(self, path: Path, realtime: bool = False) -> str:
        from faster_whisper import WhisperModel

        cfg = self.stt_cfg.get("faster_whisper", {})
        if self._fw_model is None:
            model = str(cfg.get("model", "base.en"))
            self._fw_model = WhisperModel(
                model,
                device=str(cfg.get("device", "cpu")),
                compute_type=str(cfg.get("compute_type", "int8")),
                download_root=cfg.get("download_root") or None,
                local_files_only=not bool(cfg.get("allow_model_download", False)),
            )
        segments, _info = self._fw_model.transcribe(
            str(path),
            beam_size=int(cfg.get("realtime_beam_size", 1) if realtime else cfg.get("beam_size", 3)),
            vad_filter=False if realtime else bool(cfg.get("vad_filter", True)),
            language=cfg.get("language") or "en",
            condition_on_previous_text=False if realtime else True,
        )
        return " ".join(segment.text.strip() for segment in segments if segment.text.strip())

    def _transcribe_whisper_cpp(self, path: Path) -> str:
        cfg = self.stt_cfg.get("whisper_cpp", {})
        binary = str(cfg.get("binary", "whisper-cli"))
        binary = shutil.which(binary) or binary
        model = str(Path(str(cfg.get("model"))).expanduser())
        with tempfile.TemporaryDirectory(prefix="daqauntum-whisper-") as tmp:
            output_base = Path(tmp) / "transcript"
            cmd = [binary, "-m", model, "-f", str(path), "-otxt", "-of", str(output_base), "-nt"]
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=int(cfg.get("timeout_seconds", 120)))
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "whisper.cpp failed").strip()[-1200:])
            txt = output_base.with_suffix(".txt")
            if txt.exists():
                return txt.read_text(encoding="utf-8", errors="replace")
            return completed.stdout

    # ------------------------------------------------------------------------- TTS
    def speak(self, text: str) -> dict[str, Any]:
        text = " ".join(str(text).strip().split())[: self.max_speak_chars]
        if not text:
            raise ValueError("Speech text is empty")
        status = self.status(refresh=True)["tts"]
        if not status.get("available"):
            raise RuntimeError(status.get("detail", "Local TTS unavailable"))
        backend = status["backend"]
        if backend == "piper":
            self._speak_piper(text)
        elif backend == "system":
            self._speak_system(text, status)
        elif backend == "mock":
            pass
        else:
            raise RuntimeError("No supported local TTS backend selected")
        return {"ok": True, "backend": backend, "local": True}

    def _speak_system(self, text: str, status: dict[str, Any]) -> None:
        system = platform.system().lower()
        cfg = self.tts_cfg.get("system", {})
        timeout = int(cfg.get("timeout_seconds", 90))
        if system == "darwin":
            cmd = ["say"]
            voice = str(cfg.get("voice", "")).strip()
            if voice:
                cmd += ["-v", voice]
            cmd.append(text)
            self._run_tts_process(cmd, timeout=timeout)
            return
        if system == "linux":
            engine = str(status.get("engine"))
            cmd = [engine]
            voice = str(cfg.get("voice", "")).strip()
            if voice:
                cmd += ["-v", voice]
            cmd.append(text)
            self._run_tts_process(cmd, timeout=timeout)
            return
        if system == "windows":
            ps = shutil.which("powershell") or shutil.which("powershell.exe") or "powershell"
            escaped = text.replace("'", "''")
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Speak('{escaped}')"
            )
            self._run_tts_process([ps, "-NoProfile", "-Command", script], timeout=timeout)
            return
        raise RuntimeError("Unsupported system TTS")

    def _speak_piper(self, text: str) -> None:
        cfg = self.tts_cfg.get("piper", {})
        binary = str(cfg.get("binary", "piper"))
        binary = shutil.which(binary) or binary
        model = str(cfg.get("model", "")).strip()
        if not model:
            raise RuntimeError("Piper model is not configured")
        with tempfile.TemporaryDirectory(prefix="daqauntum-piper-") as tmp:
            wav = Path(tmp) / "speech.wav"
            cmd = [binary, "--model", model, "--output_file", str(wav)]
            if bool(cfg.get("cuda", False)):
                cmd.append("--cuda")
            completed = subprocess.run(
                cmd,
                input=text,
                text=True,
                capture_output=True,
                timeout=int(cfg.get("timeout_seconds", 120)),
            )
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout or "Piper failed").strip()[-1200:])
            self._play_audio(wav)

    def _play_audio(self, path: Path) -> None:
        candidates = []
        system = platform.system().lower()
        if system == "darwin":
            candidates = [["afplay", str(path)]]
        elif system == "linux":
            candidates = [["aplay", str(path)], ["paplay", str(path)]]
        elif system == "windows":
            ps = shutil.which("powershell") or shutil.which("powershell.exe")
            if ps:
                escaped = str(path).replace("'", "''")
                candidates = [[ps, "-NoProfile", "-Command", f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()"]]
        for cmd in candidates:
            if shutil.which(cmd[0]) or Path(cmd[0]).exists():
                self._run_tts_process(cmd, timeout=120)
                return
        raise RuntimeError("Piper synthesized audio but no local audio player was found")

    def stop(self) -> dict[str, Any]:
        """Stop the currently speaking local TTS process for barge-in."""
        with self._tts_lock:
            proc = self._tts_process
            if not proc or proc.poll() is not None:
                self._tts_process = None
                return {"ok": True, "stopped": False}
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            finally:
                self._tts_process = None
        return {"ok": True, "stopped": True}

    def _run_tts_process(self, cmd: list[str], timeout: int = 90) -> None:
        self.stop()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with self._tts_lock:
            self._tts_process = proc
        try:
            code = proc.wait(timeout=timeout)
            if code != 0:
                raise RuntimeError(f"Local TTS process exited with code {code}")
        except subprocess.TimeoutExpired as exc:
            try:
                proc.kill()
            finally:
                raise RuntimeError("Local TTS timed out") from exc
        finally:
            with self._tts_lock:
                if self._tts_process is proc:
                    self._tts_process = None
