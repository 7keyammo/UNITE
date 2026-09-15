from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any


# The four stages TASKS.md P0.2 and the real-host plan ask to separate. Treating
# a turn as one number hides which part is slow: a 3-second reply caused by slow
# STT needs a different fix from one caused by a slow first token.
STAGES: tuple[tuple[str, str, str], ...] = (
    ("transcribe", "speech_end", "stt_done"),        # speech end -> final transcript
    ("think", "stt_done", "first_token"),            # final transcript -> first model token
    ("speak", "first_token", "first_audio"),         # first token -> first audible speech
    ("total", "speech_end", "complete"),             # the whole perceived turn
)

# Perceived responsiveness is dominated by how soon the user hears *anything*.
PERCEIVED_MARKS = ("speech_end", "first_audio")

MARKS = ("speech_end", "stt_done", "first_token", "first_audio", "complete")


@dataclass
class TurnTimeline:
    """Timing marks for one spoken turn.

    Records durations only. No transcript text, no audio, no model output ever
    enters a timeline: latency diagnostics are written to disk and shared when
    debugging, and they should never become a second copy of what was said.
    """

    turn_id: str
    session_id: str = ""
    interaction_mode: str = "voice_call"
    marks: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)

    def mark(self, name: str, at: float | None = None) -> float | None:
        """Record a stage boundary. The first mark of a name wins.

        Later marks are ignored on purpose: `first_token` means the *first* one,
        and a re-mark would quietly turn a first-token measurement into a
        last-token one.
        """
        if name not in MARKS:
            raise ValueError(f"Unknown timeline mark: {name}")
        if name in self.marks:
            return self.marks[name]
        value = time.perf_counter() if at is None else float(at)
        self.marks[name] = value
        return value

    def stage_ms(self, start: str, end: str) -> float | None:
        if start not in self.marks or end not in self.marks:
            return None
        delta = self.marks[end] - self.marks[start]
        # A negative stage means marks arrived out of order, usually because a
        # client reported audio late. Report nothing rather than a fiction.
        return round(delta * 1000.0, 1) if delta >= 0 else None

    def stages(self) -> dict[str, float | None]:
        return {name: self.stage_ms(start, end) for name, start, end in STAGES}

    @property
    def perceived_ms(self) -> float | None:
        """Speech end to first audible response — what the user actually feels."""
        return self.stage_ms(*PERCEIVED_MARKS)

    @property
    def complete(self) -> bool:
        return "complete" in self.marks

    def missing_marks(self) -> list[str]:
        return [name for name in MARKS if name not in self.marks]

    def as_dict(self) -> dict[str, Any]:
        stages = self.stages()
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "interaction_mode": self.interaction_mode,
            "stages_ms": stages,
            "perceived_ms": self.perceived_ms,
            "complete": self.complete,
            "missing_marks": self.missing_marks(),
            "meta": dict(self.meta),
            "started_at": self.started_at,
        }


class LatencyRecorder:
    """Persist turn timings so slowness can be measured, not just felt."""

    def __init__(self, memory, *, max_rows: int = 2000):
        self.memory = memory
        self.max_rows = max(100, int(max_rows))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_latency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                turn_id TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                interaction_mode TEXT NOT NULL DEFAULT '',
                transcribe_ms REAL,
                think_ms REAL,
                speak_ms REAL,
                total_ms REAL,
                perceived_ms REAL,
                provider TEXT,
                model TEXT,
                stt_backend TEXT,
                audio_ms REAL,
                created_at REAL NOT NULL
            )
            """
        )
        self.memory.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_latency_id ON turn_latency(id DESC)"
        )
        self.memory.conn.commit()

    def record(self, timeline: TurnTimeline) -> int | None:
        stages = timeline.stages()
        # A turn with no measurable stage carries no information.
        if not any(value is not None for value in stages.values()):
            return None
        meta = timeline.meta or {}
        cursor = self.memory.conn.execute(
            """
            INSERT INTO turn_latency(turn_id, session_id, interaction_mode, transcribe_ms, think_ms,
                                     speak_ms, total_ms, perceived_ms, provider, model, stt_backend,
                                     audio_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timeline.turn_id, timeline.session_id, timeline.interaction_mode,
                stages.get("transcribe"), stages.get("think"), stages.get("speak"),
                stages.get("total"), timeline.perceived_ms,
                meta.get("provider"), meta.get("model"), meta.get("stt_backend"),
                meta.get("audio_ms"), time.time(),
            ),
        )
        self.memory.conn.execute(
            "DELETE FROM turn_latency WHERE id NOT IN (SELECT id FROM turn_latency ORDER BY id DESC LIMIT ?)",
            (self.max_rows,),
        )
        self.memory.conn.commit()
        return int(cursor.lastrowid)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute(
            "SELECT * FROM turn_latency ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),)
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _summarize(values: list[float]) -> dict[str, Any] | None:
        clean = [v for v in values if v is not None]
        if not clean:
            return None
        ordered = sorted(clean)
        index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
        return {
            "samples": len(ordered),
            "median_ms": round(statistics.median(ordered), 1),
            "p95_ms": round(ordered[index], 1),
            "min_ms": round(ordered[0], 1),
            "max_ms": round(ordered[-1], 1),
        }

    def stats(self, limit: int = 200) -> dict[str, Any]:
        rows = self.recent(limit=limit)
        if not rows:
            return {
                "turns": 0,
                "stages": {},
                "note": "No voice turns recorded yet. Timings appear once a spoken turn completes.",
            }
        stages = {
            name: self._summarize([row.get(f"{name}_ms") for row in rows])
            for name in ("transcribe", "think", "speak", "total")
        }
        perceived = self._summarize([row.get("perceived_ms") for row in rows])
        return {
            "turns": len(rows),
            "stages": {k: v for k, v in stages.items() if v},
            "perceived": perceived,
            "slowest_stage": self._slowest(stages),
            "note": (
                "transcribe = speech end to final transcript; think = transcript to first model "
                "token; speak = first token to first audible speech; perceived = speech end to "
                "first audible speech."
            ),
        }

    @staticmethod
    def _slowest(stages: dict[str, Any]) -> str | None:
        """Name the bottleneck, so 'it feels slow' becomes a specific fix."""
        candidates = {
            name: data["median_ms"]
            for name, data in stages.items()
            if data and name != "total"
        }
        return max(candidates, key=candidates.get) if candidates else None
