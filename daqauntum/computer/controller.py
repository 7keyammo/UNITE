from __future__ import annotations

import json
from typing import Any


VALID_AUTONOMY = ("observe", "assist", "execute", "autonomous")


class ComputerController:
    """Computer-use policy layer.

    This does not grant authority. DaQauntum's ToolRegistry/PermissionManager remains
    authoritative. The controller only constrains how the external computer-use arm
    may be invoked and records verification evidence.
    """

    def __init__(self, memory, integrations, perception, config: dict[str, Any] | None = None):
        self.memory = memory
        self.integrations = integrations
        self.perception = perception
        self.config = config or {}
        saved = str(self.memory.get_state("computer.autonomy", self.config.get("autonomy", "observe")) or "observe").lower()
        self.autonomy = saved if saved in VALID_AUTONOMY else "observe"
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS computer_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request TEXT NOT NULL,
                autonomy TEXT NOT NULL,
                status TEXT NOT NULL,
                before_frame_id INTEGER,
                result TEXT,
                verification TEXT,
                verification_status TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
            """
        )
        self.memory.conn.commit()

    def set_autonomy(self, level: str) -> dict[str, Any]:
        level = str(level or "").lower()
        if level not in VALID_AUTONOMY:
            raise ValueError(f"Computer autonomy must be one of: {', '.join(VALID_AUTONOMY)}")
        self.autonomy = level
        self.memory.set_state("computer.autonomy", level)
        self.memory.add_event("computer_autonomy", {"level": level})
        return self.status()

    def status(self) -> dict[str, Any]:
        rows = self.memory.conn.execute("SELECT status, COUNT(*) n FROM computer_actions GROUP BY status").fetchall()
        counts = {str(row["status"]): int(row["n"]) for row in rows}
        oi = self.integrations.status("open_interpreter").as_dict() if self.integrations else {}
        return {
            "autonomy": self.autonomy,
            "levels": list(VALID_AUTONOMY),
            "open_interpreter": oi,
            "actions": counts,
        }

    def observe(self, request: str) -> str:
        if not self.integrations:
            raise RuntimeError("Integration Runtime is unavailable")
        prompt = (
            "OBSERVE ONLY. Do not click, type, navigate, change files, or alter state. "
            "Inspect the current computer/browser state using available read-only visual tools and answer this request:\n"
            + request
        )
        return self.integrations.open_interpreter(prompt, mode="read_only")

    def execute(self, request: str) -> dict[str, Any]:
        if self.autonomy in {"observe", "assist"}:
            return {
                "ok": False,
                "status": "blocked_by_autonomy",
                "message": f"Computer autonomy is '{self.autonomy}'. Switch to Execute or Autonomous before running state-changing computer actions.",
            }
        before = self.perception.latest("screen") if self.perception else None
        cursor = self.memory.conn.execute(
            "INSERT INTO computer_actions(request, autonomy, status, before_frame_id) VALUES (?, ?, 'running', ?)",
            (request, self.autonomy, before.get("id") if before else None),
        )
        action_id = int(cursor.lastrowid)
        self.memory.conn.commit()
        action_prompt = (
            "You are DaQauntum's computer-use arm. Carry out ONLY the requested task. "
            "Stay inside the current desktop/browser environment and project workspace. "
            "Do not send messages, purchase, publish, delete data, or change credentials unless the exact request explicitly requires it; "
            "those categories require separate DaQauntum tools and approval. Task:\n" + request
        )
        try:
            result = self.integrations.open_interpreter(action_prompt, mode="workspace_write")
            verification_prompt = (
                "OBSERVE ONLY. Do not change state. Visually inspect the current screen/application after the prior task and verify whether this goal was achieved: "
                + request
                + "\nReturn exactly one of PASS, FAIL, or UNCERTAIN on the first line, then concise visual evidence."
            )
            try:
                verification = self.integrations.open_interpreter(verification_prompt, mode="read_only")
            except Exception as exc:
                verification = f"UNCERTAIN\nVisual verification unavailable: {exc}"
            first = verification.strip().splitlines()[0].upper() if verification.strip() else "UNCERTAIN"
            status = "pass" if first.startswith("PASS") else "fail" if first.startswith("FAIL") else "uncertain"
            self.memory.conn.execute(
                "UPDATE computer_actions SET status='completed', result=?, verification=?, verification_status=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (result, verification, status, action_id),
            )
            self.memory.conn.commit()
            self.memory.add_event("computer_action", {"action_id": action_id, "verification": status})
            return {"ok": True, "action_id": action_id, "result": result, "verification": verification, "verification_status": status}
        except Exception as exc:
            self.memory.conn.execute(
                "UPDATE computer_actions SET status='failed', result=?, completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (str(exc), action_id),
            )
            self.memory.conn.commit()
            return {"ok": False, "action_id": action_id, "status": "failed", "message": str(exc)}

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute("SELECT * FROM computer_actions ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
        return [dict(row) for row in rows]
