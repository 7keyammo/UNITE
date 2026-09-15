from __future__ import annotations

import asyncio
import io
import json
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from realtime.timeline import LatencyRecorder, TurnTimeline

try:
    from websockets.asyncio.server import serve
except Exception:  # pragma: no cover - optional dependency at import time
    serve = None


@dataclass
class DuplexSession:
    id: str
    interaction_mode: str = "voice_call"
    call_id: str | None = None
    sample_rate: int = 16000
    channels: int = 1
    created_at: float = field(default_factory=time.time)
    audio: bytearray = field(default_factory=bytearray)
    last_partial_at: float = 0.0
    last_partial_text: str = ""
    active_turn_id: str | None = None
    turns: int = 0
    audio_bytes: int = 0
    partial_transcripts: int = 0
    committed_utterances: int = 0
    last_stt_ms: float | None = None
    last_audio_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "interaction_mode": self.interaction_mode,
            "call_id": self.call_id,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "age_ms": round((time.time() - self.created_at) * 1000.0, 1),
            "active_turn_id": self.active_turn_id,
            "turns": self.turns,
            "audio_bytes": self.audio_bytes,
            "partial_transcripts": self.partial_transcripts,
            "committed_utterances": self.committed_utterances,
            "last_stt_ms": self.last_stt_ms,
            "last_audio_ms": self.last_audio_ms,
        }


class DuplexSessionRegistry:
    def __init__(self, keep_recent: int = 32):
        self.keep_recent = max(8, int(keep_recent))
        self._lock = threading.RLock()
        self._sessions: dict[str, DuplexSession] = {}
        self._order: list[str] = []

    def create(self, interaction_mode: str = "voice_call") -> DuplexSession:
        session = DuplexSession(id=uuid.uuid4().hex[:12], interaction_mode=interaction_mode)
        with self._lock:
            self._sessions[session.id] = session
            self._order.append(session.id)
            while len(self._order) > self.keep_recent:
                old = self._order.pop(0)
                self._sessions.pop(old, None)
        return session

    def get(self, session_id: str) -> DuplexSession | None:
        with self._lock:
            return self._sessions.get(str(session_id))

    def recent(self, limit: int = 8) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self._order[-max(1, int(limit)):]))
            return [self._sessions[i].as_dict() for i in ids if i in self._sessions]


class FullDuplexHub:
    """Persistent local WebSocket session for audio, text deltas and control events.

    Audio is PCM16 little-endian mono by default. The browser may continuously append frames,
    request partial local transcription, commit an utterance, interrupt generation, and receive
    model deltas on the same connection. Tool permissions remain owned by DaQauntumKernel.
    """

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        cfg = config or {}
        self.partial_stt_enabled = bool(cfg.get("partial_stt_enabled", True))
        self.partial_stt_interval_ms = int(cfg.get("partial_stt_interval_ms", 900))
        self.partial_stt_min_ms = int(cfg.get("partial_stt_min_ms", 650))
        self.max_audio_bytes = int(cfg.get("max_audio_bytes", kernel.voice.max_audio_bytes))
        # How long a remote client has to present its token before the
        # connection is closed. Short, because it is one message.
        self.auth_timeout_seconds = max(2.0, min(float(cfg.get("auth_timeout_seconds", 10)), 60.0))
        # Per-turn timings, so "it feels slow" can be attributed to a stage.
        self.latency = LatencyRecorder(kernel.memory)
        self._timelines: dict[str, TurnTimeline] = {}
        self._pending_timeline: TurnTimeline | None = None
        self.registry = DuplexSessionRegistry(int(cfg.get("keep_recent_sessions", 32)))

    @staticmethod
    def pcm16_to_wav(raw_pcm: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(max(1, int(channels)))
            wav.setsampwidth(2)
            wav.setframerate(max(8000, int(sample_rate)))
            wav.writeframes(raw_pcm)
        return out.getvalue()

    @staticmethod
    def _peer_is_loopback(websocket) -> bool:
        """Whether the WebSocket peer is on this machine.

        Read from the socket's real peer address, never a header, for the same
        reason the HTTP gate does: a remote caller controls its own headers.
        """
        try:
            host = str(websocket.remote_address[0])
        except Exception:
            return False
        return host in {"127.0.0.1", "::1", "::ffff:127.0.0.1"} or host.startswith("127.")

    async def _authorize(self, websocket) -> tuple[bool, str]:
        """Authenticate a duplex connection before any audio is accepted.

        The realtime channel carries the same conversation as /api/chat, so it
        needs the same protection. A browser cannot set headers on a WebSocket
        and a token in a URL leaks into logs, so a remote client sends its token
        as the first control message instead.
        """
        identity = getattr(self.kernel, "identity", None)
        if identity is None or not identity.enabled:
            return True, "identity-disabled"
        loopback = self._peer_is_loopback(websocket)
        if loopback and not identity.require_auth_for_loopback:
            return True, "loopback"
        if not loopback and not identity.require_auth_for_remote:
            return True, "remote-auth-disabled"

        remote = ""
        try:
            remote = str(websocket.remote_address[0])
        except Exception:
            pass
        await self._send(websocket, {"type": "auth_required", "message": "Send {\"type\":\"auth\",\"token\":\"...\"} to continue."})
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=self.auth_timeout_seconds)
        except asyncio.TimeoutError:
            identity.audit("duplex_auth_timeout", remote=remote)
            return False, "auth-timeout"
        except Exception:
            return False, "auth-closed"
        if isinstance(raw, bytes):
            # Audio before authentication is refused outright: nothing is
            # buffered, transcribed or answered for an unauthenticated peer.
            identity.audit("duplex_auth_failed", remote=remote, detail="audio before auth")
            return False, "audio-before-auth"
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            return False, "bad-auth-message"
        if not isinstance(event, dict) or event.get("type") != "auth":
            return False, "expected-auth-message"

        result = identity.authenticate(str(event.get("token") or ""), remote=remote)
        if not result.ok:
            return False, "unauthenticated"
        if not result.has_scope("chat"):
            identity.audit(
                "scope_denied",
                device_id=result.device.device_id if result.device else None,
                remote=remote,
                detail="duplex needs chat",
            )
            return False, "missing-chat-scope"
        identity.audit("duplex_authenticated", device_id=result.device.device_id if result.device else None, remote=remote)
        return True, "authenticated"

    async def handler(self, websocket) -> None:
        allowed, reason = await self._authorize(websocket)
        if not allowed:
            # The client is told only that authentication is required; the
            # specific reason stays in the audit log.
            await self._send(websocket, {"type": "error", "error": "Authentication required for remote realtime access."})
            await websocket.close(code=4401, reason="unauthorized")
            return

        session = self.registry.create()
        partial_task: asyncio.Task | None = None
        await self._send(websocket, {
            "type": "hello",
            "session": session.as_dict(),
            "version": self.kernel.config.get("version", "0.4.0"),
            "authenticated": reason == "authenticated",
            "capabilities": {
                "binary_pcm16": True,
                "partial_stt": self.partial_stt_enabled,
                "barge_in": self.kernel.runtime.module_enabled("barge_in"),
                "streaming": self.kernel.runtime.module_enabled("streaming"),
                "local_voice": self.kernel.voice.status().get("local_only", True),
            },
        })
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    if len(session.audio) + len(message) > self.max_audio_bytes:
                        await self._send(websocket, {"type": "error", "error": "Audio buffer exceeds configured limit"})
                        session.audio.clear()
                        continue
                    session.audio.extend(message)
                    session.audio_bytes += len(message)
                    if self._partial_due(session) and (partial_task is None or partial_task.done()):
                        snapshot = bytes(session.audio)
                        session.last_partial_at = time.time()
                        partial_task = asyncio.create_task(self._partial_transcribe(websocket, session, snapshot))
                    continue

                try:
                    event = json.loads(message)
                except json.JSONDecodeError:
                    await self._send(websocket, {"type": "error", "error": "Invalid JSON control event"})
                    continue
                if not isinstance(event, dict):
                    continue
                await self._handle_control(websocket, session, event)
        finally:
            if partial_task and not partial_task.done():
                partial_task.cancel()
            if session.active_turn_id:
                self.kernel.interrupt_realtime(session.active_turn_id)

    def _partial_due(self, session: DuplexSession) -> bool:
        if not self.partial_stt_enabled or not self.kernel.runtime.module_enabled("voice"):
            return False
        status = self.kernel.voice.status()
        if not status.get("stt", {}).get("available"):
            return False
        sample_bytes = max(1, session.sample_rate * session.channels * 2)
        duration_ms = len(session.audio) / sample_bytes * 1000.0
        if duration_ms < self.partial_stt_min_ms:
            return False
        return (time.time() - session.last_partial_at) * 1000.0 >= self.partial_stt_interval_ms

    async def _partial_transcribe(self, websocket, session: DuplexSession, pcm: bytes) -> None:
        try:
            wav = self.pcm16_to_wav(pcm, session.sample_rate, session.channels)
            result = await asyncio.to_thread(self.kernel.voice.transcribe_wav_bytes, wav, True)
            text = str(result.get("text", "")).strip()
            if text and text != session.last_partial_text:
                session.last_partial_text = text
                session.partial_transcripts += 1
                await self._send(websocket, {
                    "type": "transcript.partial",
                    "session_id": session.id,
                    "text": text,
                    "backend": result.get("backend"),
                    "audio_ms": session.last_audio_ms,
                "stt_ms": session.last_stt_ms,
                })
        except asyncio.CancelledError:
            return
        except Exception as exc:
            await self._send(websocket, {"type": "transcript.partial_error", "error": f"{type(exc).__name__}: {exc}"})

    async def _handle_control(self, websocket, session: DuplexSession, event: dict[str, Any]) -> None:
        kind = str(event.get("type", "")).strip()
        if kind == "ping":
            await self._send(websocket, {"type": "pong", "ts": time.time()})
            return
        if kind in {"session.start", "session.attach"}:
            session.interaction_mode = str(event.get("interaction_mode", "voice_call"))
            requested_call = str(event.get("call_id", "")).strip() or None
            if requested_call:
                existing = await asyncio.to_thread(self.kernel.calls.get, requested_call)
                if not existing:
                    await self._send(websocket, {"type": "error", "error": "Call session not found"})
                    return
                session.call_id = requested_call
            elif bool(event.get("create_call", False)):
                title = str(event.get("title", "")).strip() or None
                state = await asyncio.to_thread(self.kernel.calls.start, title)
                session.call_id = state["id"]
            session.sample_rate = int(event.get("sample_rate", session.sample_rate) or session.sample_rate)
            session.channels = int(event.get("channels", session.channels) or session.channels)
            await self._send(websocket, {"type": "session.ready", "session": session.as_dict()})
            return
        if kind == "audio.start":
            session.sample_rate = int(event.get("sample_rate", session.sample_rate) or session.sample_rate)
            session.channels = int(event.get("channels", session.channels) or session.channels)
            session.audio.clear()
            session.last_partial_text = ""
            await self._send(websocket, {"type": "audio.ready", "session_id": session.id})
            return
        if kind == "turn.audio":
            # The client tells us when the user first *hears* the reply. We
            # cannot observe browser speech synthesis from here, and guessing
            # would produce a number that looks precise and means nothing.
            turn_id = str(event.get("turn_id", "")).strip()
            timeline = self._timelines.get(turn_id)
            if timeline is not None:
                timeline.mark("first_audio")
            return
        if kind == "audio.clear":
            session.audio.clear()
            session.last_partial_text = ""
            await self._send(websocket, {"type": "audio.cleared", "session_id": session.id})
            return
        if kind == "audio.commit":
            if not session.audio:
                await self._send(websocket, {"type": "transcript.final", "text": "", "empty": True})
                return
            pcm = bytes(session.audio)
            session.audio.clear()
            session.last_partial_text = ""
            wav = self.pcm16_to_wav(pcm, session.sample_rate, session.channels)
            # Speech ended when the client committed the buffer; everything after
            # this point is DaQauntum's latency, not the user's speaking time.
            timeline = TurnTimeline(turn_id="", session_id=session.id,
                                    interaction_mode=session.interaction_mode)
            timeline.mark("speech_end")
            try:
                result = await asyncio.to_thread(self.kernel.voice.transcribe_wav_bytes, wav)
            except Exception as exc:
                await self._send(websocket, {"type": "error", "error": f"Local STT failed: {type(exc).__name__}: {exc}"})
                return
            timeline.mark("stt_done")
            text = str(result.get("text", "")).strip()
            timeline.meta["stt_backend"] = result.get("backend")
            session.committed_utterances += 1
            session.last_stt_ms = timeline.stage_ms("speech_end", "stt_done") or 0.0
            # Held for the turn this transcript is about to start.
            self._pending_timeline = timeline if text else None
            session.last_audio_ms = round(len(pcm) / max(1, session.sample_rate * session.channels * 2) * 1000.0, 1)
            await self._send(websocket, {
                "type": "transcript.final",
                "session_id": session.id,
                "text": text,
                "backend": result.get("backend"),
                "audio_ms": session.last_audio_ms,
                "stt_ms": session.last_stt_ms,
            })
            if text:
                await self._run_turn(websocket, session, text)
            return
        if kind == "turn.text":
            text = str(event.get("text", "")).strip()
            if text:
                await self._run_turn(websocket, session, text)
            return
        if kind == "interrupt":
            stopped = False
            if session.active_turn_id:
                stopped = bool(await asyncio.to_thread(self.kernel.interrupt_realtime, session.active_turn_id))
            voice = await asyncio.to_thread(self.kernel.voice.stop)
            await self._send(websocket, {"type": "interrupted", "turn_id": session.active_turn_id, "generation_stopped": stopped, "voice": voice})
            return
        if kind == "session.end":
            call_state = None
            processed = []
            if session.call_id:
                call_state = await asyncio.to_thread(self.kernel.calls.end, session.call_id)
                if bool(event.get("process_tasks", False)):
                    processed = await asyncio.to_thread(self.kernel.calls.run_all, session.call_id)
                    call_state = await asyncio.to_thread(self.kernel.calls.get, session.call_id)
            await self._send(websocket, {"type": "session.ended", "session": session.as_dict(), "call": call_state, "processed_tasks": processed})
            return
        if kind == "status":
            await self._send(websocket, {"type": "status", "session": session.as_dict(), "realtime": self.kernel.realtime.status()})
            return
        await self._send(websocket, {"type": "error", "error": f"Unknown duplex event: {kind}"})

    async def _run_turn(self, websocket, session: DuplexSession, text: str) -> None:
        # Barge-in is session-global: a new user turn interrupts an old generation first.
        if session.active_turn_id and self.kernel.runtime.module_enabled("barge_in"):
            await asyncio.to_thread(self.kernel.interrupt_realtime, session.active_turn_id)
            await asyncio.to_thread(self.kernel.voice.stop)
        turn = self.kernel.realtime.begin(session.interaction_mode)
        session.active_turn_id = turn.id
        session.turns += 1
        timeline = self._pending_timeline or TurnTimeline(
            turn_id=turn.id, session_id=session.id, interaction_mode=session.interaction_mode
        )
        timeline.turn_id = turn.id
        self._pending_timeline = None
        self._timelines[turn.id] = timeline
        # Keep only the most recent turns; a long call must not grow unbounded.
        for stale in list(self._timelines)[:-32]:
            self._timelines.pop(stale, None)
        await self._send(websocket, {"type": "user.final", "text": text, "turn_id": turn.id, "session_id": session.id})
        if session.call_id:
            factory: Callable[[], Iterable[dict[str, Any]]] = lambda: self.kernel.calls.add_turn_stream(session.call_id, text, turn_id=turn.id)
        else:
            factory = lambda: self.kernel.process_stream(text, interaction_mode=session.interaction_mode, turn_id=turn.id)
        try:
            async for event in self._async_events(factory):
                outgoing = dict(event)
                outgoing["duplex_session_id"] = session.id
                kind = outgoing.get("type")
                if kind == "delta":
                    timeline.mark("first_token")
                elif kind == "meta":
                    timeline.meta["provider"] = outgoing.get("provider")
                    timeline.meta["model"] = outgoing.get("model")
                elif kind in {"done", "interrupted"}:
                    # An interrupted turn is still a real latency sample: the
                    # user heard something, which is what perceived latency is.
                    timeline.mark("complete")
                    self.latency.record(timeline)
                    outgoing["latency"] = timeline.as_dict()
                await self._send(websocket, outgoing)
        finally:
            if session.active_turn_id == turn.id:
                session.active_turn_id = None

    async def _async_events(self, factory: Callable[[], Iterable[dict[str, Any]]]):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        def worker() -> None:
            try:
                for item in factory():
                    asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(queue.put({"type": "error", "error": f"{type(exc).__name__}: {exc}"}), loop).result()
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(done), loop).result()

        thread = threading.Thread(target=worker, daemon=True, name="daqauntum-duplex-turn")
        thread.start()
        while True:
            item = await queue.get()
            if item is done:
                break
            yield item

    @staticmethod
    async def _send(websocket, payload: dict[str, Any]) -> None:
        await websocket.send(json.dumps(payload, ensure_ascii=False, default=str))


class DuplexServerThread:
    def __init__(self, kernel, host: str, port: int, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.host = host
        self.port = int(port)
        self.hub = FullDuplexHub(kernel, config)
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server = None
        self.ready = threading.Event()
        self.error: Exception | None = None

    def start(self, timeout: float = 5.0) -> "DuplexServerThread":
        if serve is None:
            raise RuntimeError("The websockets package is not installed. Run pip install -r requirements.txt")
        self.thread = threading.Thread(target=self._run, daemon=True, name="daqauntum-duplex-server")
        self.thread.start()
        if not self.ready.wait(timeout):
            raise RuntimeError("Timed out starting DaQauntum duplex WebSocket server")
        if self.error:
            raise RuntimeError(f"Could not start duplex WebSocket server: {self.error}")
        return self

    def _run(self) -> None:
        async def runner() -> None:
            try:
                async with serve(
                    self.hub.handler,
                    self.host,
                    self.port,
                    compression=None,
                    max_size=self.hub.max_audio_bytes,
                    ping_interval=20,
                    ping_timeout=20,
                ) as server:
                    self.server = server
                    self.ready.set()
                    await asyncio.Future()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.error = exc
                self.ready.set()
                raise

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        task = self.loop.create_task(runner())
        try:
            self.loop.run_until_complete(task)
        except Exception:
            pass
        finally:
            pending = asyncio.all_tasks(self.loop)
            for p in pending:
                p.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.close()

    def stop(self) -> None:
        if self.loop and self.loop.is_running():
            def stopper() -> None:
                for task in asyncio.all_tasks(self.loop):
                    task.cancel()
            self.loop.call_soon_threadsafe(stopper)
        if self.thread:
            self.thread.join(timeout=3.0)
