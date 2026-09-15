from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any


class CallSessionManager:
    """Local voice-call session state, notes, and action-item execution.

    Speech recognition/TTS may be provided by DaQauntum's local voice engine or
    by the browser fallback. This class owns the durable transcript, structured
    call summary, extracted tasks, and the bridge back into the kernel for execution.
    """

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.memory = kernel.memory
        self.conn = self.memory.conn
        self.config = config or {}
        self.calls_dir = Path(self.config.get("calls_dir", "data/calls")).resolve()
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self.max_transcript_chars = int(self.config.get("max_transcript_chars", 24000))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                project TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                summary TEXT,
                notes_json TEXT NOT NULL DEFAULT '[]',
                decisions_json TEXT NOT NULL DEFAULT '[]',
                followups_json TEXT NOT NULL DEFAULT '[]',
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at DATETIME
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                speaker TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES call_sessions(id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS call_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                description TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'queued',
                result TEXT,
                pending_approvals_json TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, description),
                FOREIGN KEY(session_id) REFERENCES call_sessions(id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_call_turns_session ON call_turns(session_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_call_tasks_session ON call_tasks(session_id)")
        self.conn.commit()


    def stats(self) -> dict[str, Any]:
        sessions = int(self.conn.execute("SELECT COUNT(*) FROM call_sessions").fetchone()[0])
        active = int(self.conn.execute("SELECT COUNT(*) FROM call_sessions WHERE status = 'active'").fetchone()[0])
        tasks = int(self.conn.execute("SELECT COUNT(*) FROM call_tasks").fetchone()[0])
        queued = int(self.conn.execute("SELECT COUNT(*) FROM call_tasks WHERE status IN ('queued','pending_approval')").fetchone()[0])
        return {"sessions": sessions, "active": active, "tasks": tasks, "open_tasks": queued}

    def start(self, title: str | None = None) -> dict[str, Any]:
        session_id = uuid.uuid4().hex[:12]
        project = self.kernel.structured_memory.active_project
        clean_title = (title or "DaQauntum Call").strip()[:120] or "DaQauntum Call"
        self.conn.execute(
            "INSERT INTO call_sessions(id, title, project, status) VALUES (?, ?, ?, 'active')",
            (session_id, clean_title, project),
        )
        self.conn.commit()
        self.memory.add_event("call_started", {"session_id": session_id, "title": clean_title, "project": project})
        return self.get(session_id) or {"id": session_id, "title": clean_title, "status": "active"}

    def get(self, session_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM call_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        turns = [dict(r) for r in self.conn.execute(
            "SELECT id, speaker, text, created_at FROM call_turns WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()]
        tasks = [self._task_row(r) for r in self.conn.execute(
            "SELECT * FROM call_tasks WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()]
        result = dict(row)
        for key in ("notes_json", "decisions_json", "followups_json"):
            result[key[:-5]] = self._json_load(result.pop(key), [])
        result["turns"] = turns
        result["tasks"] = tasks
        result["live_notes"] = self._live_notes(turns)
        return result

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM call_sessions ORDER BY started_at DESC LIMIT ?", (max(1, min(limit, 50)),)
        ).fetchall()
        return [dict(r) for r in rows]

    def add_turn(self, session_id: str, text: str) -> dict[str, Any]:
        session = self.get(session_id)
        if not session:
            raise ValueError("Call session not found")
        if session["status"] != "active":
            raise ValueError("Call session has already ended")
        clean = " ".join(text.strip().split())
        if not clean:
            raise ValueError("Nothing was said")

        self._insert_turn(session_id, "user", clean)
        self._extract_tasks_heuristic(session_id, clean)

        response = self.kernel.process(clean, interaction_mode="voice_call")
        assistant_text = response["response"]
        self._insert_turn(session_id, "assistant", assistant_text)
        self.memory.add_event(
            "call_turn",
            {
                "session_id": session_id,
                "agent": response.get("agent"),
                "pending_approvals": response.get("pending_approvals", []),
            },
        )
        state = self.get(session_id) or {}
        state["last_response"] = response
        return state

    def add_turn_stream(self, session_id: str, text: str, turn_id: str | None = None):
        """Stream a voice-call response while keeping the durable transcript consistent."""
        session = self.get(session_id)
        if not session:
            raise ValueError("Call session not found")
        if session["status"] != "active":
            raise ValueError("Call session has already ended")
        clean = " ".join(text.strip().split())
        if not clean:
            raise ValueError("Nothing was said")

        self._insert_turn(session_id, "user", clean)
        self._extract_tasks_heuristic(session_id, clean)
        final_result = None
        for event in self.kernel.process_stream(clean, interaction_mode="voice_call", turn_id=turn_id):
            if event.get("type") in {"done", "interrupted"}:
                final_result = event.get("result") or {}
                assistant_text = str(final_result.get("response", "")).strip()
                if assistant_text:
                    self._insert_turn(session_id, "assistant", assistant_text)
                self.memory.add_event(
                    "call_turn",
                    {
                        "session_id": session_id,
                        "agent": final_result.get("agent"),
                        "pending_approvals": final_result.get("pending_approvals", []),
                        "streaming": True,
                        "interrupted": bool(final_result.get("interrupted")),
                    },
                )
                state = self.get(session_id) or {}
                state["last_response"] = final_result
                event = {**event, "call": state}
            yield event

    def end(self, session_id: str) -> dict[str, Any]:
        session = self.get(session_id)
        if not session:
            raise ValueError("Call session not found")
        if session["status"] == "ended":
            return session

        transcript = self._transcript_text(session["turns"])
        intelligence = self._summarize(transcript)
        for task in intelligence.get("tasks", []):
            if isinstance(task, str):
                self._insert_task(session_id, task, "normal")
            elif isinstance(task, dict):
                self._insert_task(session_id, str(task.get("description", "")), str(task.get("priority", "normal")))

        notes = [str(x) for x in intelligence.get("notes", []) if str(x).strip()]
        decisions = [str(x) for x in intelligence.get("decisions", []) if str(x).strip()]
        followups = [str(x) for x in intelligence.get("follow_up_questions", []) if str(x).strip()]
        summary = str(intelligence.get("summary", "")).strip() or self._heuristic_summary(session["turns"])

        self.conn.execute(
            """
            UPDATE call_sessions
            SET status = 'ended', summary = ?, notes_json = ?, decisions_json = ?,
                followups_json = ?, ended_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (summary, json.dumps(notes), json.dumps(decisions), json.dumps(followups), session_id),
        )
        self.conn.commit()

        # Promote durable call knowledge into the existing memory/graph system.
        memory_ids: list[int] = []
        if summary:
            item = self.kernel.remember(f"Call {session_id} summary: {summary}", kind="episodic")
            if item.get("id"):
                memory_ids.append(int(item["id"]))
        for decision in decisions[:10]:
            item = self.kernel.remember(f"Call {session_id} decision: {decision}", kind="decision")
            if item.get("id"):
                memory_ids.append(int(item["id"]))

        note_path = self._write_call_note(session_id) if bool(self.config.get("save_transcripts", True)) else None
        self.memory.add_event(
            "call_ended",
            {
                "session_id": session_id,
                "summary": summary,
                "memory_ids": memory_ids,
                "note_path": str(note_path) if note_path else None,
            },
        )
        return self.get(session_id) or {}

    def run_task(self, task_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM call_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not row:
            raise ValueError(f"Call task #{task_id} not found")
        task = self._task_row(row)
        self.conn.execute(
            "UPDATE call_tasks SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (task_id,),
        )
        self.conn.commit()

        result = self.kernel.process(
            "Implement this action item from our call now. Use registered tools when useful and do not claim completion without tool evidence:\n"
            + task["description"],
            interaction_mode="call_task",
        )
        pending = list(result.get("pending_approvals", []))
        executed = [o for o in result.get("observations", []) if o.get("status") == "executed"]
        if pending:
            status = "pending_approval"
        elif executed:
            status = "completed"
        else:
            status = "processed"
        self.conn.execute(
            """
            UPDATE call_tasks
            SET status = ?, result = ?, pending_approvals_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, result.get("response", ""), json.dumps(pending), task_id),
        )
        self.conn.commit()
        self.memory.add_event(
            "call_task_processed",
            {"task_id": task_id, "status": status, "pending_approvals": pending},
        )
        self._maybe_refresh_note(task["session_id"])
        return {"task": self.task(task_id), "response": result}

    def run_all(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id FROM call_tasks WHERE session_id = ? AND status IN ('queued', 'processed') ORDER BY id",
            (session_id,),
        ).fetchall()
        results = []
        for row in rows:
            results.append(self.run_task(int(row["id"])))
        return results

    def task(self, task_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM call_tasks WHERE id = ?", (int(task_id),)).fetchone()
        return self._task_row(row) if row else None

    def mark_approval(self, approval_id: str, ok: bool, message: str) -> None:
        changed_sessions: set[str] = set()
        rows = self.conn.execute(
            "SELECT * FROM call_tasks WHERE status = 'pending_approval' ORDER BY id"
        ).fetchall()
        for row in rows:
            task = self._task_row(row)
            pending = list(task.get("pending_approvals", []))
            if approval_id not in pending:
                continue
            pending.remove(approval_id)
            status = "completed" if ok and not pending else ("pending_approval" if pending else "failed")
            result = (task.get("result") or "") + f"\n\nApproval {approval_id}: {message}"
            self.conn.execute(
                "UPDATE call_tasks SET status = ?, result = ?, pending_approvals_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, result.strip(), json.dumps(pending), task["id"]),
            )
            changed_sessions.add(task["session_id"])
        self.conn.commit()
        for session_id in changed_sessions:
            self._maybe_refresh_note(session_id)

    def _summarize(self, transcript: str) -> dict[str, Any]:
        if not transcript.strip():
            return {"summary": "Empty call.", "notes": [], "decisions": [], "tasks": [], "follow_up_questions": []}

        profile = self.kernel.policy.analyze(
            "Summarize and extract action items from this local voice-call transcript.",
            "general",
            context=[{"role": "user", "content": transcript[-self.max_transcript_chars:]}],
        )
        policy = self.kernel.policy.decide(profile)
        routing = policy.role_hints.get("executor") if policy.enabled else None
        routing = self.kernel.runtime.routing_hint("executor", routing, "balanced")
        system = """You are DaQauntum's call-notes module. Return ONLY valid JSON.
Extract concise, faithful notes from the transcript. Do not invent commitments.
Shape:
{
  "summary": "short paragraph",
  "notes": ["fact or topic"],
  "decisions": ["explicit decision"],
  "tasks": [{"description": "action item", "priority": "low|normal|high"}],
  "follow_up_questions": ["unresolved question"]
}
"""
        try:
            response = self.kernel.brain.generate(
                "executor",
                system,
                [{"role": "user", "content": transcript[-self.max_transcript_chars:]}],
                routing=routing,
            )
            if response.provider != "mock":
                parsed = self._extract_json(response.text)
                if parsed:
                    return parsed
        except Exception:
            pass
        return self._heuristic_intelligence(transcript)

    def _heuristic_intelligence(self, transcript: str) -> dict[str, Any]:
        user_lines = []
        for line in transcript.splitlines():
            if line.startswith("You: "):
                user_lines.append(line[5:].strip())
        tasks: list[dict[str, str]] = []
        decisions: list[str] = []
        notes: list[str] = []
        for text in user_lines:
            if text not in notes:
                notes.append(text)
            for task in self._task_candidates(text):
                if task and not any(t["description"].lower() == task.lower() for t in tasks):
                    tasks.append({"description": task, "priority": "normal"})
            lowered = text.lower()
            if any(token in lowered for token in ("we decided", "i decided", "we will", "we'll", "going to")):
                decisions.append(text)
        return {
            "summary": " ".join(user_lines[:6])[:1200] or "Call completed.",
            "notes": notes[:20],
            "decisions": decisions[:10],
            "tasks": tasks[:20],
            "follow_up_questions": [],
        }

    def _extract_tasks_heuristic(self, session_id: str, text: str) -> None:
        for task in self._task_candidates(text):
            self._insert_task(session_id, task, "normal")

    @staticmethod
    def _task_candidates(text: str) -> list[str]:
        candidates: list[str] = []
        segments = [s.strip(" .,-") for s in re.split(r"[.!?\n]+", text) if s.strip()]
        patterns = [
            r"(?:^|\b)(?:i|we|you)\s+(?:need|have|want)\s+to\s+(.+)$",
            r"(?:^|\b)(?:let['’]?s|lets)\s+(.+)$",
            r"(?:^|\b)(?:remember to|make sure to|todo\s*:|task\s*:)\s*(.+)$",
            r"(?:^|\b)(?:please)\s+(.+)$",
        ]
        for segment in segments:
            for pattern in patterns:
                match = re.search(pattern, segment, flags=re.IGNORECASE)
                if match:
                    task = " ".join(match.group(1).strip().split())
                    if len(task) >= 4 and task.lower() not in {c.lower() for c in candidates}:
                        candidates.append(task[0].upper() + task[1:])
                    break
        return candidates

    def _insert_task(self, session_id: str, description: str, priority: str = "normal") -> None:
        clean = " ".join(description.strip().split())
        if not clean:
            return
        priority = priority if priority in {"low", "normal", "high"} else "normal"
        self.conn.execute(
            "INSERT OR IGNORE INTO call_tasks(session_id, description, priority) VALUES (?, ?, ?)",
            (session_id, clean[:1000], priority),
        )
        self.conn.commit()

    def _insert_turn(self, session_id: str, speaker: str, text: str) -> None:
        self.conn.execute(
            "INSERT INTO call_turns(session_id, speaker, text) VALUES (?, ?, ?)",
            (session_id, speaker, text),
        )
        self.conn.commit()

    def _maybe_refresh_note(self, session_id: str) -> None:
        if not bool(self.config.get("save_transcripts", True)):
            return
        state = self.get(session_id)
        if state and state.get("status") == "ended":
            self._write_call_note(session_id)

    def _write_call_note(self, session_id: str) -> Path:
        state = self.get(session_id)
        if not state:
            raise ValueError("Call session not found")
        path = self.calls_dir / f"{session_id}.md"
        lines = [
            f"# {state['title']}",
            "",
            f"- Session: `{session_id}`",
            f"- Project: {state.get('project') or 'none'}",
            f"- Started: {state.get('started_at')}",
            f"- Ended: {state.get('ended_at')}",
            "",
            "## Summary",
            "",
            state.get("summary") or "",
            "",
            "## Notes",
            "",
        ]
        lines.extend(f"- {item}" for item in state.get("notes", []))
        lines.extend(["", "## Decisions", ""])
        lines.extend(f"- {item}" for item in state.get("decisions", []))
        lines.extend(["", "## Action Items", ""])
        lines.extend(f"- [{task['status']}] {task['description']}" for task in state.get("tasks", []))
        lines.extend(["", "## Transcript", ""])
        for turn in state.get("turns", []):
            who = "You" if turn["speaker"] == "user" else "DaQauntum"
            lines.append(f"**{who}:** {turn['text']}")
            lines.append("")
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return path

    @staticmethod
    def _transcript_text(turns: list[dict[str, Any]]) -> str:
        return "\n".join(
            f"{'You' if t['speaker'] == 'user' else 'DaQauntum'}: {t['text']}" for t in turns
        )

    @staticmethod
    def _live_notes(turns: list[dict[str, Any]]) -> list[str]:
        notes: list[str] = []
        for turn in turns:
            if turn["speaker"] != "user":
                continue
            text = " ".join(turn["text"].split())
            if text and text not in notes:
                notes.append(text)
        return notes[-12:]

    @staticmethod
    def _heuristic_summary(turns: list[dict[str, Any]]) -> str:
        user = [" ".join(t["text"].split()) for t in turns if t["speaker"] == "user"]
        return " ".join(user[:6])[:1200] if user else "Call completed."

    @staticmethod
    def _task_row(row) -> dict[str, Any]:
        result = dict(row)
        result["pending_approvals"] = CallSessionManager._json_load(result.pop("pending_approvals_json"), [])
        return result

    @staticmethod
    def _json_load(value: str | None, default: Any) -> Any:
        try:
            return json.loads(value or "")
        except (json.JSONDecodeError, TypeError):
            return default

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        text = text.strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                return None
            try:
                parsed = json.loads(match.group(0))
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
