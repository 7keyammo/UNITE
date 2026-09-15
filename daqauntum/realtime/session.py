from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RealtimeTurn:
    id: str
    interaction_mode: str
    created_at: float = field(default_factory=time.time)
    first_token_at: float | None = None
    finished_at: float | None = None
    cancelled: bool = False
    done: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def as_dict(self) -> dict[str, Any]:
        now = time.time()
        return {
            "id": self.id,
            "interaction_mode": self.interaction_mode,
            "cancelled": self.cancelled,
            "done": self.done,
            "age_ms": round((now - self.created_at) * 1000.0, 1),
            "time_to_first_token_ms": round((self.first_token_at - self.created_at) * 1000.0, 1) if self.first_token_at else None,
            "duration_ms": round((self.finished_at - self.created_at) * 1000.0, 1) if self.finished_at else None,
        }


class RealtimeSessionManager:
    """Tracks cancellable generation turns for streaming/barge-in.

    The manager does not grant tool authority. It only controls whether an in-flight
    response should keep generating and records latency telemetry.
    """

    def __init__(self, keep_recent: int = 64):
        self.keep_recent = max(8, int(keep_recent))
        self._lock = threading.RLock()
        self._turns: dict[str, RealtimeTurn] = {}
        self._order: list[str] = []

    def begin(self, interaction_mode: str = "chat") -> RealtimeTurn:
        turn = RealtimeTurn(id=uuid.uuid4().hex[:12], interaction_mode=interaction_mode)
        with self._lock:
            self._turns[turn.id] = turn
            self._order.append(turn.id)
            self._trim()
        return turn

    def get(self, turn_id: str) -> RealtimeTurn | None:
        with self._lock:
            return self._turns.get(str(turn_id))

    def cancel(self, turn_id: str) -> bool:
        with self._lock:
            turn = self._turns.get(str(turn_id))
            if not turn:
                return False
            turn.cancelled = True
            turn.cancel_event.set()
            return True

    def mark_first_token(self, turn_id: str) -> None:
        with self._lock:
            turn = self._turns.get(str(turn_id))
            if turn and turn.first_token_at is None:
                turn.first_token_at = time.time()

    def finish(self, turn_id: str) -> None:
        with self._lock:
            turn = self._turns.get(str(turn_id))
            if turn:
                turn.done = True
                turn.finished_at = time.time()

    def cancelled(self, turn_id: str) -> bool:
        turn = self.get(turn_id)
        return bool(turn and turn.cancel_event.is_set())

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            ids = list(reversed(self._order[-max(1, int(limit)):]))
            return [self._turns[i].as_dict() for i in ids if i in self._turns]

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = [t for t in self._turns.values() if not t.done]
            recent = self.recent(5)
            return {"active": len(active), "recent": recent}

    def _trim(self) -> None:
        if len(self._order) <= self.keep_recent:
            return
        removable = self._order[:-self.keep_recent]
        self._order = self._order[-self.keep_recent:]
        for turn_id in removable:
            turn = self._turns.get(turn_id)
            if turn and turn.done:
                self._turns.pop(turn_id, None)
