from __future__ import annotations

import json
import re
import time
from typing import Any, Callable


# The guided vertical slice from the real-host plan:
#
#   look -> describe -> propose -> approve -> act -> re-capture -> verify
#
# Each stage is recorded, so a completed task is an audit trail rather than a
# claim that something happened.
STATUSES: tuple[str, ...] = (
    "observing",
    "awaiting_approval",
    "executing",
    "verified",
    "rejected",
    "failed",
)

VERDICTS: tuple[str, ...] = ("pass", "fail", "uncertain")

STEP_KINDS: tuple[str, ...] = ("observe", "propose", "decision", "execute", "verify")

_VERDICT_RE = re.compile(r"^\s*(PASS|FAIL|UNCERTAIN)\b", re.I)


class TaskError(RuntimeError):
    """Raised for invalid guided-task transitions."""


class GuidedTaskManager:
    """One reliable look → act → verify flow, with every stage on the record.

    Two properties make this trustworthy rather than merely automated:

    **Nothing executes without an explicit approval.** `propose()` records what
    DaQauntum wants to do and evaluates it against the permission manager, but
    running it is a separate call that the user makes. The autonomy level and
    the permission level both have to allow it.

    **Verification uses an independent capture.** The previous flow asked the
    computer-use adapter whether its own action had worked, which is
    self-assessment: an adapter that failed silently would report success just
    as confidently. Instead a fresh frame is captured and read by the vision
    route, so the evidence comes from looking at the screen rather than from
    asking the actor.
    """

    def __init__(self, kernel, controller, perception, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.controller = controller
        self.perception = perception
        self.config = dict(config or {})
        self.memory = kernel.memory
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS computer_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'observing',
                autonomy TEXT NOT NULL DEFAULT 'observe',
                permission_level INTEGER,
                before_frame_id INTEGER,
                after_frame_id INTEGER,
                observation TEXT,
                proposal_json TEXT,
                permission_outcome TEXT,
                permission_reason TEXT,
                approved_by TEXT,
                decision_reason TEXT,
                result TEXT,
                verification TEXT,
                verdict TEXT,
                created_at REAL NOT NULL,
                completed_at REAL
            )
            """
        )
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS computer_task_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                frame_id INTEGER,
                actor TEXT NOT NULL DEFAULT 'daqauntum',
                created_at REAL NOT NULL
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_computer_tasks_status ON computer_tasks(status)",
            "CREATE INDEX IF NOT EXISTS idx_computer_task_steps_task ON computer_task_steps(task_id)",
        ):
            self.memory.conn.execute(statement)
        self.memory.conn.commit()

    # Audit -------------------------------------------------------------------
    def _step(self, task_id: int, kind: str, detail: str = "", *, frame_id: int | None = None, actor: str = "daqauntum") -> None:
        if kind not in STEP_KINDS:
            raise ValueError(f"Unknown step kind: {kind}")
        self.memory.conn.execute(
            "INSERT INTO computer_task_steps(task_id, kind, detail, frame_id, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (int(task_id), kind, str(detail)[:4000], frame_id, actor, time.time()),
        )
        self.memory.conn.commit()

    def steps(self, task_id: int) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute(
            "SELECT * FROM computer_task_steps WHERE task_id = ? ORDER BY id ASC", (int(task_id),)
        ).fetchall()
        return [dict(row) for row in rows]

    # Stage 1: look -----------------------------------------------------------
    def start(self, goal: str, *, frame_id: int | None = None, operation_mode: str = "auto") -> dict[str, Any]:
        """Begin a task by looking at the screen the user has shared.

        Requires a frame that already exists: DaQauntum does not capture the
        screen on its own. A frame arrives only because the user explicitly
        shared one, which is the consent step for seeing anything at all.
        """
        goal = str(goal or "").strip()
        if not goal:
            raise TaskError("A guided task needs a goal")

        frame = self.perception.get(int(frame_id)) if frame_id else self.perception.latest("screen")
        if not frame:
            raise TaskError(
                "No shared screen frame is available. Share your screen first - DaQauntum "
                "does not capture the screen by itself."
            )

        cursor = self.memory.conn.execute(
            """
            INSERT INTO computer_tasks(goal, status, autonomy, permission_level, before_frame_id, created_at)
            VALUES (?, 'observing', ?, ?, ?, ?)
            """,
            (goal, self.controller.autonomy, int(self.kernel.permissions.level), int(frame["id"]), time.time()),
        )
        task_id = int(cursor.lastrowid)
        self.memory.conn.commit()
        self._step(task_id, "observe", f"Using shared screen frame #{frame['id']}", frame_id=int(frame["id"]))

        analysis = self.perception.analyze(
            int(frame["id"]),
            "Describe what is currently on screen: the active application, what the user appears to be "
            "doing, and any elements relevant to this goal:\n" + goal,
            operation_mode=operation_mode,
        )
        observation = str(analysis.get("text", "")).strip()
        self.memory.conn.execute(
            "UPDATE computer_tasks SET observation = ? WHERE id = ?", (observation, task_id)
        )
        self.memory.conn.commit()
        self._step(
            task_id, "observe",
            f"Vision route: {analysis.get('provider') or 'none'}/{analysis.get('model') or '—'}",
            frame_id=int(frame["id"]),
        )
        self.memory.add_event("computer_task_started", {"task_id": task_id, "frame_id": frame["id"]})
        return self.get(task_id) or {}

    # Stage 2: propose --------------------------------------------------------
    def propose(self, task_id: int, instruction: str, *, tool: str = "computer_action") -> dict[str, Any]:
        """Record what DaQauntum wants to do. Nothing runs here.

        The permission manager is consulted so a proposal the gate would refuse
        is recorded as denied instead of sitting in the queue looking
        actionable. An allowed proposal still waits for the user.
        """
        task = self.get(task_id)
        if task is None:
            raise TaskError(f"No task {task_id}")
        if task["status"] not in {"observing", "awaiting_approval"}:
            raise TaskError(f"Task {task_id} is {task['status']} and cannot take a new proposal")
        instruction = str(instruction or "").strip()
        if not instruction:
            raise TaskError("A proposal needs an instruction")

        spec = self.kernel.tools.get(tool)
        if spec is None:
            outcome, reason, status = "deny", f"Unknown tool '{tool}'", "rejected"
            required_level, irreversible = None, False
        else:
            from core.permissions import Action

            decision = self.kernel.permissions.evaluate(Action(spec.name, spec.required_level, spec.irreversible))
            outcome, reason = decision.outcome, decision.reason
            status = "rejected" if decision.outcome == "deny" else "awaiting_approval"
            required_level, irreversible = spec.required_level, bool(spec.irreversible)

        proposal = {
            "tool": tool,
            "instruction": instruction,
            "required_level": required_level,
            "irreversible": irreversible,
            "autonomy": self.controller.autonomy,
        }
        self.memory.conn.execute(
            "UPDATE computer_tasks SET status = ?, proposal_json = ?, permission_outcome = ?, permission_reason = ? WHERE id = ?",
            (status, json.dumps(proposal, ensure_ascii=False), outcome, reason, task_id),
        )
        self.memory.conn.commit()
        self._step(task_id, "propose", f"{tool}: {instruction} (gate says '{outcome}': {reason})")
        self.memory.add_event("computer_task_proposed", {"task_id": task_id, "tool": tool, "status": status})
        return self.get(task_id) or {}

    # Stage 3: decide ---------------------------------------------------------
    def reject(self, task_id: int, *, decided_by: str = "user", reason: str = "") -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise TaskError(f"No task {task_id}")
        if task["status"] != "awaiting_approval":
            raise TaskError(f"Task {task_id} is {task['status']}, not awaiting approval")
        self.memory.conn.execute(
            "UPDATE computer_tasks SET status = 'rejected', decision_reason = ?, approved_by = ?, completed_at = ? WHERE id = ?",
            (str(reason or "declined"), decided_by, time.time(), task_id),
        )
        self.memory.conn.commit()
        self._step(task_id, "decision", f"Rejected by {decided_by}: {reason or 'no reason given'}", actor=decided_by)
        return self.get(task_id) or {}

    def approve(
        self,
        task_id: int,
        *,
        decided_by: str = "user",
        reason: str = "",
        verify_frame_id: int | None = None,
        capture_verification: Callable[[], dict[str, Any] | None] | None = None,
        operation_mode: str = "auto",
    ) -> dict[str, Any]:
        """Run the proposed action, then verify it by looking again.

        Execution goes through the tool registry, so the permission manager
        re-evaluates the call. Approval here is the user's decision to proceed;
        it is not a second authority that can override the gate.
        """
        task = self.get(task_id)
        if task is None:
            raise TaskError(f"No task {task_id}")
        if task["status"] != "awaiting_approval":
            raise TaskError(f"Task {task_id} is {task['status']}, not awaiting approval")
        proposal = task.get("proposal") or {}
        tool = str(proposal.get("tool") or "computer_action")
        instruction = str(proposal.get("instruction") or "")

        self.memory.conn.execute(
            "UPDATE computer_tasks SET status = 'executing', approved_by = ?, decision_reason = ? WHERE id = ?",
            (decided_by, str(reason or "approved"), task_id),
        )
        self.memory.conn.commit()
        self._step(task_id, "decision", f"Approved by {decided_by}: {reason or 'no reason given'}", actor=decided_by)

        result = self.kernel.tools.execute(tool, {"instruction": instruction}, approved=True)
        self._step(task_id, "execute", f"{tool} -> ok={result.ok}: {str(result.output)[:500]}")

        if not result.ok:
            self.memory.conn.execute(
                "UPDATE computer_tasks SET status = 'failed', result = ?, verdict = 'uncertain', completed_at = ? WHERE id = ?",
                (result.output, time.time(), task_id),
            )
            self.memory.conn.commit()
            self.memory.add_event("computer_task_failed", {"task_id": task_id, "output": result.output[:200]})
            return self.get(task_id) or {}

        verification = self._verify(
            task_id, task["goal"], instruction,
            verify_frame_id=verify_frame_id,
            capture_verification=capture_verification,
            operation_mode=operation_mode,
        )
        self.memory.conn.execute(
            """
            UPDATE computer_tasks
               SET status = 'verified', result = ?, verification = ?, verdict = ?, after_frame_id = ?, completed_at = ?
             WHERE id = ?
            """,
            (result.output, verification["text"], verification["verdict"], verification["frame_id"], time.time(), task_id),
        )
        self.memory.conn.commit()
        self.memory.add_event(
            "computer_task_verified", {"task_id": task_id, "verdict": verification["verdict"]}
        )
        return self.get(task_id) or {}

    # Stage 4: verify ---------------------------------------------------------
    def _verify(
        self,
        task_id: int,
        goal: str,
        instruction: str,
        *,
        verify_frame_id: int | None,
        capture_verification: Callable[[], dict[str, Any] | None] | None,
        operation_mode: str,
    ) -> dict[str, Any]:
        """Look again and judge the outcome.

        Fails closed: without a fresh frame or a working vision route the
        verdict is UNCERTAIN, never PASS. A verification that cannot see
        anything has not verified anything.
        """
        frame = None
        if verify_frame_id:
            frame = self.perception.get(int(verify_frame_id))
        elif capture_verification is not None:
            try:
                frame = capture_verification()
            except Exception as exc:
                self._step(task_id, "verify", f"Verification capture failed: {type(exc).__name__}: {exc}")

        if not frame:
            text = (
                "UNCERTAIN\nNo post-action screen frame was available, so the outcome could not be "
                "checked visually. Share the screen again to verify."
            )
            self._step(task_id, "verify", "No verification frame; verdict UNCERTAIN")
            return {"verdict": "uncertain", "text": text, "frame_id": None}

        prompt = (
            "Look at this screen capture taken immediately AFTER an action was performed.\n"
            f"The goal was: {goal}\n"
            f"The action performed was: {instruction}\n\n"
            "Answer with exactly one of PASS, FAIL or UNCERTAIN on the first line, then give the "
            "specific visual evidence you used. Answer UNCERTAIN if the screen does not clearly "
            "show whether the goal was achieved - do not guess."
        )
        try:
            analysis = self.perception.analyze(int(frame["id"]), prompt, operation_mode=operation_mode)
            text = str(analysis.get("text", "")).strip()
            provider = analysis.get("provider")
        except Exception as exc:
            text = f"UNCERTAIN\nVisual verification failed: {type(exc).__name__}: {exc}"
            provider = None

        verdict = self._parse_verdict(text, provider)
        self._step(
            task_id, "verify",
            f"Verdict {verdict.upper()} from frame #{frame['id']} via {provider or 'no vision route'}",
            frame_id=int(frame["id"]),
        )
        return {"verdict": verdict, "text": text, "frame_id": int(frame["id"])}

    @staticmethod
    def _parse_verdict(text: str, provider: Any) -> str:
        """Extract the verdict, defaulting to uncertain.

        A missing vision provider means the analysis text is frame metadata,
        not a judgement, so it can never be read as PASS.
        """
        if not provider:
            return "uncertain"
        match = _VERDICT_RE.match(str(text or ""))
        if not match:
            return "uncertain"
        return match.group(1).lower()

    # Reads -------------------------------------------------------------------
    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        data = dict(row)
        raw = data.pop("proposal_json", None)
        try:
            data["proposal"] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            data["proposal"] = None
        return data

    def get(self, task_id: int) -> dict[str, Any] | None:
        row = self.memory.conn.execute(
            "SELECT * FROM computer_tasks WHERE id = ?", (int(task_id),)
        ).fetchone()
        if row is None:
            return None
        task = self._row(row)
        task["steps"] = self.steps(task_id)
        return task

    def list(self, *, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.memory.conn.execute(
                "SELECT * FROM computer_tasks WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, max(1, min(int(limit), 100))),
            ).fetchall()
        else:
            rows = self.memory.conn.execute(
                "SELECT * FROM computer_tasks ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 100)),)
            ).fetchall()
        return [self._row(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        rows = self.memory.conn.execute(
            "SELECT status, COUNT(*) AS n FROM computer_tasks GROUP BY status"
        ).fetchall()
        verdicts = self.memory.conn.execute(
            "SELECT verdict, COUNT(*) AS n FROM computer_tasks WHERE verdict IS NOT NULL GROUP BY verdict"
        ).fetchall()
        return {
            "tasks": {row["status"]: int(row["n"]) for row in rows},
            "verdicts": {row["verdict"]: int(row["n"]) for row in verdicts},
            "awaiting_approval": sum(int(r["n"]) for r in rows if r["status"] == "awaiting_approval"),
            "autonomy": self.controller.autonomy,
            "vision": self.perception.vision_status(),
            # Stated plainly so no status surface can imply otherwise.
            "executes_without_approval": False,
        }
