from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any


class AgentWorkbench:
    """Parallel, read-only-by-default workspace agent jobs.

    Each worker uses its own SQLite connection. This matters because N concurrent agent
    panes may finish at nearly the same instant. Workbench jobs don't call the tool
    registry; a result must be explicitly handed back to the kernel for side effects.
    """

    def __init__(self, kernel, workspaces, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.workspaces = workspaces
        self.config = dict(config or {})
        self.max_workers = max(1, min(8, int(self.config.get("max_parallel_agents", 4))))
        self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="dq-agent")
        self.lock = threading.RLock()
        self.db_path = str(kernel.memory.db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS os_jobs (
                    id TEXT PRIMARY KEY,
                    workspace_id INTEGER NOT NULL,
                    agent_slug TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    response TEXT,
                    provider TEXT,
                    model TEXT,
                    error TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    started_at DATETIME,
                    finished_at DATETIME
                );
                CREATE INDEX IF NOT EXISTS idx_os_jobs_workspace ON os_jobs(workspace_id, created_at DESC);
                """
            )

    def _event(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events(event_type,payload_json) VALUES(?,?)",
                (event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def submit(self, workspace_id: int | str, agent_slug: str, prompt: str, *, background: bool = True) -> dict[str, Any]:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        agent = self.workspaces.get_agent(int(workspace["id"]), str(agent_slug))
        if not agent:
            raise ValueError("Workspace agent not found")
        prompt = str(prompt).strip()
        if not prompt:
            raise ValueError("Job prompt is required")
        job_id = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute("INSERT INTO os_jobs(id,workspace_id,agent_slug,prompt,status) VALUES(?,?,?,?,?)", (job_id, workspace["id"], agent["slug"], prompt, "queued"))
        self._event("workbench_job_queued", {"job_id": job_id, "workspace_id": workspace["id"], "agent": agent["slug"]})
        if background:
            self.executor.submit(self._run, job_id)
        else:
            self._run(job_id)
        return self.get(job_id) or {"id": job_id, "status": "queued"}

    def submit_parallel(self, workspace_id: int | str, agent_slugs: list[str], prompt: str) -> list[dict[str, Any]]:
        unique = []
        for slug in agent_slugs:
            slug = str(slug).strip()
            if slug and slug not in unique:
                unique.append(slug)
        if not unique:
            raise ValueError("Select at least one agent")
        return [self.submit(workspace_id, slug, prompt, background=True) for slug in unique[: self.max_workers]]

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        if not job:
            return
        workspace = self.workspaces.get(int(job["workspace_id"]))
        agent = self.workspaces.get_agent(int(job["workspace_id"]), str(job["agent_slug"])) if workspace else None
        if not workspace or not agent:
            self._fail(job_id, "Workspace or agent missing")
            return
        with self._connect() as conn:
            conn.execute("UPDATE os_jobs SET status='running', started_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        try:
            skills = []
            for slug in agent.get("skills", []):
                skill = self.workspaces.get_skill(int(workspace["id"]), slug)
                if skill:
                    skills.append(f"SKILL {skill['name']}:\n{skill['instructions']}")
            context = self.workspaces.context_messages(workspace_id=workspace["id"])
            workspace_context = context[0]["content"] if context else ""
            system = (
                f"{agent['system_prompt']}\n\n"
                "You are running inside DaQauntum's parallel Agent Workbench. Produce a draft/recommendation only. "
                "Do not claim you executed tools, sent messages, changed files, or performed external actions. "
                "Consequential action must be handed back to DaQauntum's permission-gated execution path.\n\n"
                f"{workspace_context}\n\n" + "\n\n".join(skills)
            )
            profile = self.kernel.policy.analyze(job["prompt"], "workbench")
            routing = self.kernel.runtime.routing_hint("executor", {"model_tier": "balanced" if profile.complexity < 4 else "strong"}, "balanced")
            result = self.kernel.brain.generate("executor", system, [{"role": "user", "content": job["prompt"]}], routing=routing)
            with self._connect() as conn:
                conn.execute(
                    "UPDATE os_jobs SET status='completed',response=?,provider=?,model=?,error=NULL,finished_at=CURRENT_TIMESTAMP WHERE id=?",
                    (result.text, result.provider, result.model, job_id),
                )
            self._event("workbench_job_completed", {"job_id": job_id, "workspace_id": workspace["id"], "agent": agent["slug"], "provider": result.provider, "model": result.model})
        except Exception as exc:
            self._fail(job_id, f"{type(exc).__name__}: {exc}")

    def _fail(self, job_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE os_jobs SET status='failed',error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?", (str(error), job_id))
        self._event("workbench_job_failed", {"job_id": job_id, "error": str(error)})

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM os_jobs WHERE id=?", (str(job_id),)).fetchone()
            return dict(row) if row else None

    def list(self, workspace_id: int | str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if workspace_id not in (None, ""):
                workspace = self.workspaces.get(workspace_id)
                if not workspace:
                    return []
                rows = conn.execute("SELECT * FROM os_jobs WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?", (workspace["id"], int(limit))).fetchall()
            else:
                rows = conn.execute("SELECT * FROM os_jobs ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) total, SUM(status='running') running, SUM(status='queued') queued, SUM(status='completed') completed, SUM(status='failed') failed FROM os_jobs").fetchone()
            return {"total": int(row["total"] or 0), "running": int(row["running"] or 0), "queued": int(row["queued"] or 0), "completed": int(row["completed"] or 0), "failed": int(row["failed"] or 0), "max_parallel_agents": self.max_workers}

    def handoff_prompt(self, job_id: str, instruction: str | None = None) -> str:
        job = self.get(job_id)
        if not job:
            raise ValueError("Job not found")
        if job["status"] != "completed" or not job.get("response"):
            raise ValueError("Only completed jobs can be handed off")
        workspace = self.workspaces.get(int(job["workspace_id"]))
        return (
            f"Implement the following reviewed Agent Workbench result for workspace '{workspace['name'] if workspace else job['workspace_id']}'. "
            "Use DaQauntum's normal permission-gated tools and permission gates. Do not assume the draft already executed anything.\n\n"
            f"Original job: {job['prompt']}\n\nDraft result:\n{job['response']}\n\n"
            f"Additional instruction: {instruction or 'Implement only what is appropriate and ask for approval where required.'}"
        )
