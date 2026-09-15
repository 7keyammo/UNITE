"""Regression guard for the guided Eyes + Hands vertical slice.

The flow under test is: look -> describe -> propose -> approve -> act ->
re-capture -> verify, with every stage on the record.

Two properties are the reason this exists:

* nothing executes without an explicit approval, at any autonomy or permission
  level;
* verification fails closed - without a fresh frame or a working vision route
  the verdict is UNCERTAIN, never PASS.
"""
from __future__ import annotations

import base64
import json
import struct
import tempfile
import zlib
from pathlib import Path

import yaml

from computer.task import GuidedTaskManager, TaskError
from core.kernel import DaQauntumKernel


def _png_data_url() -> str:
    """A tiny real PNG, so perception stores a genuine frame."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * 2 for _ in range(2))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _kernel(root: Path, *, permission_level: int = 2) -> DaQauntumKernel:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.3-eyes-hands-test",
        "permission_level": permission_level,
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
        "perception": {"frames_dir": str(root / "frames")},
    }
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(path))


def _stub_vision(kernel: DaQauntumKernel, text: str, provider: str | None = "stub") -> None:
    """Replace the vision route with a deterministic stub.

    No multimodal model runs in CI, and a test that depended on one would be
    testing the model rather than the flow.
    """
    def analyze(frame_id, prompt, *, operation_mode="auto"):
        return {
            "frame": kernel.perception.get(frame_id),
            "text": text,
            "provider": provider,
            "model": "stub-vision" if provider else None,
            "fallback": provider is None,
        }

    kernel.perception.analyze = analyze  # type: ignore[assignment]


def _share_screen(kernel: DaQauntumKernel, label: str = "screen") -> dict:
    """Frames exist only because the user shared one. Nothing self-captures."""
    return kernel.perception.add_data_url(_png_data_url(), frame_type="screen", label=label)


def test_requires_a_shared_frame(root: Path) -> None:
    kernel = _kernel(root / "noframe")
    try:
        kernel.computer_tasks.start("Open the test page")
    except TaskError as exc:
        assert "share" in str(exc).lower(), exc
    else:
        raise AssertionError("a task started without any shared screen frame")


def test_full_flow_pass(root: Path) -> None:
    kernel = _kernel(root / "pass")
    _share_screen(kernel)
    _stub_vision(kernel, "A browser window is open on a blank tab.")

    task = kernel.computer_tasks.start("Open the browser and navigate to the safe test page")
    assert task["status"] == "observing", task["status"]
    assert task["observation"], "no screen description was recorded"
    assert task["before_frame_id"], "the observed frame was not recorded"

    task = kernel.computer_tasks.propose(task["id"], "Navigate the browser to the local safe test page")
    assert task["status"] == "awaiting_approval", task
    assert task["proposal"]["tool"] == "computer_action"
    assert task["permission_outcome"] in {"confirm", "execute"}, task["permission_outcome"]

    # The action has not run.
    assert not kernel.computer_tasks.get(task["id"])["result"], "a proposal produced a result before approval"

    executed: list[tuple] = []
    original = kernel.tools.execute

    def tracking_execute(name, args, approved=False):
        executed.append((name, approved))
        from tools.registry import ToolResult
        return ToolResult(name, True, "Navigated to the safe test page.")

    kernel.tools.execute = tracking_execute  # type: ignore[assignment]
    _stub_vision(kernel, "PASS\nThe browser now shows the safe test page with its heading visible.")
    after = _share_screen(kernel, "after")

    task = kernel.computer_tasks.approve(
        task["id"], decided_by="Louis", reason="safe local page", verify_frame_id=after["id"]
    )
    kernel.tools.execute = original  # type: ignore[assignment]

    assert executed == [("computer_action", True)], executed
    assert task["status"] == "verified", task["status"]
    assert task["verdict"] == "pass", task["verdict"]
    assert task["after_frame_id"] == after["id"], "verification used the wrong frame"
    assert task["approved_by"] == "Louis"

    # The audit trail covers every stage.
    kinds = [step["kind"] for step in task["steps"]]
    for expected in ("observe", "propose", "decision", "execute", "verify"):
        assert expected in kinds, f"{expected} missing from the audit trail: {kinds}"
    assert any("Approved by Louis" in step["detail"] for step in task["steps"])


def test_verification_fails_closed(root: Path) -> None:
    """Without evidence, the verdict is UNCERTAIN - never PASS."""
    def run(vision_text: str, provider, verify_frame, label: str) -> str:
        kernel = _kernel(root / f"closed-{label}")
        _share_screen(kernel)
        _stub_vision(kernel, "A window is open.")
        task = kernel.computer_tasks.start("Do the thing")
        task = kernel.computer_tasks.propose(task["id"], "Do the thing")

        from tools.registry import ToolResult
        kernel.tools.execute = lambda n, a, approved=False: ToolResult(n, True, "done")  # type: ignore[assignment]
        _stub_vision(kernel, vision_text, provider)
        frame_id = _share_screen(kernel, "after")["id"] if verify_frame else None
        result = kernel.computer_tasks.approve(task["id"], decided_by="t", verify_frame_id=frame_id)
        return result["verdict"]

    # No post-action frame at all.
    assert run("PASS\nlooks fine", "stub", False, "noframe") == "uncertain", \
        "a verdict was returned without a post-action frame"
    # A frame, but no vision route: the text is metadata, not a judgement.
    assert run("Captured screen frame #2 (2x2).", None, True, "novision") == "uncertain", \
        "frame metadata was read as a PASS"
    # A vision route that will not commit.
    assert run("UNCERTAIN\nThe screen does not clearly show the result.", "stub", True, "unsure") == "uncertain"
    # A vision route that says it failed.
    assert run("FAIL\nThe page did not load; an error is shown.", "stub", True, "failed") == "fail"
    # Unparseable output is not a pass.
    assert run("Well, it seems like it probably worked?", "stub", True, "vague") == "uncertain", \
        "unparseable vision output was read as a PASS"


def test_nothing_executes_without_approval(root: Path) -> None:
    """The core invariant, checked at every permission level.

    Proposing must never run anything, and a rejected task must never run
    anything afterwards.
    """
    for level in (0, 2, 4):
        kernel = _kernel(root / f"noexec-{level}", permission_level=level)
        _share_screen(kernel)
        _stub_vision(kernel, "A window is open.")

        executed: list[str] = []
        original = kernel.tools.execute
        kernel.tools.execute = lambda n, a, approved=False: (  # type: ignore[assignment]
            executed.append(n) or original(n, a, approved)
        )

        task = kernel.computer_tasks.start(f"Task at L{level}")
        task = kernel.computer_tasks.propose(task["id"], "Do something that changes state")
        assert executed == [], f"L{level}: proposing executed a tool: {executed}"

        if level == 0:
            # The gate refuses outright, so the task is rejected rather than queued.
            assert task["status"] == "rejected", f"L{level}: {task['status']}"
            assert task["permission_outcome"] == "deny", task
            try:
                kernel.computer_tasks.approve(task["id"], decided_by="t")
            except TaskError:
                pass
            else:
                raise AssertionError("a denied task was approved")
        else:
            assert task["status"] == "awaiting_approval", f"L{level}: {task['status']}"
            rejected = kernel.computer_tasks.reject(task["id"], decided_by="Louis", reason="not now")
            assert rejected["status"] == "rejected"
            try:
                kernel.computer_tasks.approve(task["id"], decided_by="Louis")
            except TaskError:
                pass
            else:
                raise AssertionError("a rejected task was approved afterwards")

        assert executed == [], f"L{level}: something executed: {executed}"
        assert kernel.computer_tasks.stats()["executes_without_approval"] is False
        kernel.tools.execute = original  # type: ignore[assignment]


def test_failed_execution_is_not_verified(root: Path) -> None:
    kernel = _kernel(root / "failedexec")
    _share_screen(kernel)
    _stub_vision(kernel, "A window is open.")
    task = kernel.computer_tasks.start("Do the thing")
    task = kernel.computer_tasks.propose(task["id"], "Do the thing")

    from tools.registry import ToolResult
    kernel.tools.execute = lambda n, a, approved=False: ToolResult(n, False, "Adapter unavailable")  # type: ignore[assignment]
    result = kernel.computer_tasks.approve(task["id"], decided_by="Louis")
    assert result["status"] == "failed", result["status"]
    assert result["verdict"] == "uncertain", "a failed action produced a confident verdict"
    assert not result["after_frame_id"], "a failed action recorded a verification frame"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_requires_a_shared_frame(root)
        test_full_flow_pass(root)
        test_verification_fails_closed(root)
        test_nothing_executes_without_approval(root)
        test_failed_execution_is_not_verified(root)
    print("DaQauntum guided computer task smoke test: PASS")


if __name__ == "__main__":
    main()
