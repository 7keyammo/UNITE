"""Regression guard for four-stage voice latency instrumentation.

TASKS.md P0.2 asks for speech-end -> transcript, transcript -> first token,
first token -> first audible speech, and total to be measured *separately*. A
single number hides which part is slow, and a wrong attribution sends the fix
to the wrong subsystem.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from realtime.timeline import MARKS, STAGES, LatencyRecorder, TurnTimeline


def _kernel(root: Path) -> DaQauntumKernel:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.3-latency-test",
        "permission_level": 2,
        "models": {
            "roles": {r: {"provider": "mock"} for r in ("planner", "executor", "critic")},
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "fallback_to_mock": True,
        },
        "memory": {"db_path": str(root / "test.db")},
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "sources": {"project_root": str(root)},
        "learning": {"project_root": str(root)},
        "obsidian": {"vault_root": str(root / "vault")},
    }
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(path))


def test_stage_arithmetic() -> None:
    timeline = TurnTimeline(turn_id="t1", session_id="s1")
    base = time.perf_counter()
    timeline.mark("speech_end", base)
    timeline.mark("stt_done", base + 0.40)
    timeline.mark("first_token", base + 1.00)
    timeline.mark("first_audio", base + 1.15)
    timeline.mark("complete", base + 3.00)

    stages = timeline.stages()
    assert stages["transcribe"] == 400.0, stages
    assert stages["think"] == 600.0, stages
    assert stages["speak"] == 150.0, stages
    assert stages["total"] == 3000.0, stages
    # Perceived latency is speech end to first audible reply, not total.
    assert timeline.perceived_ms == 1150.0, timeline.perceived_ms
    assert timeline.complete and not timeline.missing_marks()

    # The first mark of a name wins: "first token" must stay the first one.
    timeline.mark("first_token", base + 9.0)
    assert timeline.stages()["think"] == 600.0, "a re-mark overwrote first_token"

    try:
        timeline.mark("not_a_mark")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown mark was accepted")


def test_partial_and_out_of_order() -> None:
    """A stage that cannot be measured reports nothing rather than a fiction."""
    base = time.perf_counter()

    text_only = TurnTimeline(turn_id="t2")
    text_only.mark("speech_end", base)
    text_only.mark("stt_done", base + 0.2)
    text_only.mark("first_token", base + 0.8)
    text_only.mark("complete", base + 2.0)
    stages = text_only.stages()
    assert stages["speak"] is None, "speak was reported without a first_audio mark"
    assert text_only.perceived_ms is None
    assert "first_audio" in text_only.missing_marks()
    assert stages["total"] == 2000.0

    out_of_order = TurnTimeline(turn_id="t3")
    out_of_order.mark("first_token", base + 2.0)
    out_of_order.mark("first_audio", base + 1.0)
    assert out_of_order.stages()["speak"] is None, "a negative stage was reported as a duration"


def test_recorder_identifies_the_bottleneck(kernel: DaQauntumKernel) -> None:
    recorder = kernel.latency
    assert recorder.stats()["turns"] == 0

    # Deliberately make "think" the slow stage.
    for index in range(9):
        timeline = TurnTimeline(
            turn_id=f"turn-{index}",
            meta={"provider": "ollama", "model": "gemma3", "stt_backend": "faster-whisper"},
        )
        base = time.perf_counter()
        timeline.mark("speech_end", base)
        timeline.mark("stt_done", base + 0.30 + index * 0.01)
        timeline.mark("first_token", base + 1.60 + index * 0.02)
        timeline.mark("first_audio", base + 1.72 + index * 0.02)
        timeline.mark("complete", base + 3.10)
        assert recorder.record(timeline) is not None

    stats = recorder.stats()
    assert stats["turns"] == 9, stats
    assert stats["slowest_stage"] == "think", f"bottleneck misattributed: {stats['slowest_stage']}"
    for stage in ("transcribe", "think", "speak", "total"):
        assert stage in stats["stages"], stage
        assert stats["stages"][stage]["median_ms"] > 0
    assert stats["perceived"]["samples"] == 9
    # Total is never the reported bottleneck; it is the sum, not a cause.
    assert stats["slowest_stage"] != "total"

    # A timeline with nothing measurable is not stored as a zero-latency turn.
    assert recorder.record(TurnTimeline(turn_id="empty")) is None
    assert recorder.stats()["turns"] == 9


def test_no_content_is_recorded(kernel: DaQauntumKernel) -> None:
    """Latency rows must contain timings, never what was said.

    These rows are written to disk and shared when debugging; they must not
    become a second copy of the user's transcript.
    """
    timeline = TurnTimeline(
        turn_id="secret-turn",
        meta={"provider": "ollama", "model": "gemma3", "stt_backend": "faster-whisper"},
    )
    base = time.perf_counter()
    for offset, name in ((0, "speech_end"), (0.2, "stt_done"), (0.6, "first_token"),
                         (0.7, "first_audio"), (1.9, "complete")):
        timeline.mark(name, base + offset)
    kernel.latency.record(timeline)

    columns = [row[1] for row in kernel.memory.conn.execute("PRAGMA table_info(turn_latency)")]
    for forbidden in ("text", "transcript", "content", "response", "audio_blob", "prompt"):
        assert forbidden not in columns, f"turn_latency stores {forbidden}"

    rows = kernel.latency.recent(limit=5)
    assert rows, "nothing recorded"
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str):
                assert len(value) < 120, f"{key} looks like free text: {value[:60]}"


def test_status_and_duplex_wiring(kernel: DaQauntumKernel) -> None:
    status = kernel.status()
    assert "latency" in status, sorted(status)
    assert status["latency"]["turns"] >= 1

    # The duplex hub must mark every stage it is responsible for, and accept the
    # client's first-audio report for the one it cannot observe.
    source = Path("realtime/duplex.py").read_text(encoding="utf-8")
    for mark in ("speech_end", "stt_done", "first_token", "complete"):
        assert f'mark("{mark}")' in source, f"duplex never marks {mark}"
    assert "turn.audio" in source, "duplex does not accept the client's first-audio report"
    assert "self.latency.record(timeline)" in source, "duplex never persists a timeline"

    client = Path("interface/web/app.js").read_text(encoding="utf-8")
    assert "reportFirstAudio" in client, "the client never reports first audible speech"
    assert "turn.audio" in client, "the client never sends the first-audio mark"

    assert set(MARKS) == {"speech_end", "stt_done", "first_token", "first_audio", "complete"}
    assert {name for name, _, _ in STAGES} == {"transcribe", "think", "speak", "total"}


def main() -> None:
    test_stage_arithmetic()
    test_partial_and_out_of_order()
    with tempfile.TemporaryDirectory() as td:
        kernel = _kernel(Path(td) / "run")
        test_recorder_identifies_the_bottleneck(kernel)
        test_no_content_is_recorded(kernel)
        test_status_and_duplex_wiring(kernel)
    print("DaQauntum voice latency smoke test: PASS")


if __name__ == "__main__":
    main()
