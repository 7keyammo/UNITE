from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LearningTopic:
    specialty: str
    topic: str
    query: str
    code_paths: tuple[str, ...] = ()


TOPICS: tuple[LearningTopic, ...] = (
    LearningTopic("software", "Permission architecture audit", "permission levels approval irreversible action safety", ("core/permissions.py", "tools/registry.py")),
    LearningTopic("software", "Realtime latency architecture", "realtime streaming latency cancellation time to first token", ("realtime/session.py", "realtime/duplex.py", "core/kernel.py")),
    LearningTopic("software", "Memory retrieval quality", "memory retrieval ranking salience contradiction consolidation", ("memory/intelligence.py", "memory/manager.py")),
    LearningTopic("software", "Model routing and privacy policy", "model routing local hosted privacy sensitive cognition mode", ("core/policy.py", "core/runtime.py", "core/brain.py")),
    LearningTopic("research", "Evidence provenance and claim quality", "evidence provenance claims sources contradictions confidence", ("knowledge/graph.py", "knowledge/sources.py")),
    LearningTopic("research", "Falsifiability and experiment design", "falsifiability hypothesis experiment alternative explanation evidence", ()),
    LearningTopic("science", "Measurement uncertainty and error propagation", "measurement uncertainty propagation significant figures experimental error", ()),
    LearningTopic("science", "Dimensional analysis as a reasoning tool", "dimensional analysis units modeling physics consistency", ()),
    LearningTopic("science", "Feedback, emergence, and complex systems", "feedback emergence complex systems stability nonlinear dynamics", ()),
    LearningTopic("education", "Formative assessment and mastery", "formative assessment mastery learning feedback retrieval practice", ()),
    LearningTopic("education", "Inquiry-based STEM learning", "inquiry based STEM learning student modeling experiment reflection", ()),
    LearningTopic("education", "Cognitive load and scaffolding", "cognitive load scaffolding worked examples gradual release", ()),
    LearningTopic("engineering", "Fault tolerance and graceful degradation", "fault tolerance graceful degradation fallback reliability observability", ("core/brain.py", "interface/server.py")),
    LearningTopic("engineering", "Sensor calibration and validation", "sensor calibration validation uncertainty drift reference standard", ()),
    LearningTopic("engineering", "Control loops and stability", "control loop stability feedback PID response latency", ()),
)


class LearningManager:
    """Autonomous local learning orchestration.

    This does not alter model weights. It creates audited learning sessions, reports,
    episodic memories, and high-quality traces that can later seed a native-model dataset.
    """

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.memory = kernel.memory
        self.structured_memory = kernel.structured_memory
        self.knowledge = kernel.knowledge
        self.sources = kernel.sources
        self.connectors = getattr(kernel, "connectors", None)
        self.cfg = config or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.project_root = Path(self.cfg.get("project_root", ".")).resolve()
        self.report_dir = (self.project_root / self.cfg.get("reports_dir", "data/learning/reports")).resolve()
        self.outbox_dir = (self.project_root / self.cfg.get("outbox_dir", "data/learning/outbox")).resolve()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = self.memory.conn
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL UNIQUE,
                specialty TEXT NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                report_path TEXT,
                report_sha256 TEXT,
                summary TEXT,
                confidence REAL,
                critic_approved INTEGER,
                provider TEXT,
                model TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                completed_at DATETIME
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_sessions_topic ON learning_sessions(topic)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_sessions_started ON learning_sessions(started_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                report_path TEXT NOT NULL,
                delivery_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(session_id) REFERENCES learning_sessions(id)
            )
            """
        )
        conn.commit()

    def stats(self) -> dict[str, Any]:
        conn = self.memory.conn
        total = int(conn.execute("SELECT COUNT(*) FROM learning_sessions").fetchone()[0])
        completed = int(conn.execute("SELECT COUNT(*) FROM learning_sessions WHERE status='completed'").fetchone()[0])
        approved = int(conn.execute("SELECT COUNT(*) FROM learning_sessions WHERE critic_approved=1").fetchone()[0])
        latest = conn.execute("SELECT * FROM learning_sessions ORDER BY id DESC LIMIT 1").fetchone()
        return {
            "enabled": self.enabled,
            "sessions": total,
            "completed": completed,
            "critic_approved": approved,
            "latest": dict(latest) if latest else None,
            "reports_dir": str(self.report_dir),
            "outbox_dir": str(self.outbox_dir),
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.memory.conn.execute(
            "SELECT * FROM learning_sessions ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]

    def choose_topic(self) -> LearningTopic:
        recent_rows = self.memory.conn.execute(
            "SELECT topic FROM learning_sessions WHERE status='completed' ORDER BY id DESC LIMIT 12"
        ).fetchall()
        recent = {str(r["topic"]) for r in recent_rows}
        for topic in TOPICS:
            if topic.topic not in recent:
                return topic
        # Once the catalog is exhausted, choose the least recently used topic.
        usage = {
            str(r["topic"]): int(r["last_id"])
            for r in self.memory.conn.execute(
                "SELECT topic, MAX(id) AS last_id FROM learning_sessions GROUP BY topic"
            ).fetchall()
        }
        return min(TOPICS, key=lambda item: usage.get(item.topic, -1))

    def _code_evidence(self, topic: LearningTopic) -> list[str]:
        blocks: list[str] = []
        terms = [t.lower() for t in re.findall(r"[a-zA-Z_]{4,}", topic.query)[:10]]
        for rel in topic.code_paths:
            path = (self.project_root / rel).resolve()
            if self.project_root not in path.parents and path != self.project_root:
                continue
            if not path.exists() or not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            hits: list[int] = []
            for idx, line in enumerate(lines):
                lower = line.lower()
                if any(term in lower for term in terms):
                    hits.append(idx)
                if len(hits) >= 4:
                    break
            if not hits:
                hits = [0]
            seen: set[int] = set()
            selected: list[str] = []
            for hit in hits:
                start, end = max(0, hit - 4), min(len(lines), hit + 7)
                for i in range(start, end):
                    if i in seen:
                        continue
                    seen.add(i)
                    selected.append(f"{i+1:04d}: {lines[i]}")
            blocks.append(f"FILE {rel}\n" + "\n".join(selected[:80]))
        return blocks

    def _local_context(self, topic: LearningTopic) -> dict[str, Any]:
        memories = self.structured_memory.retrieve(topic.query, limit=6)
        graph = self.knowledge.search(topic.query, limit=6, project=self.structured_memory.active_project)
        sources = self.sources.search(topic.query, limit=6, project=self.structured_memory.active_project)
        return {
            "code": self._code_evidence(topic),
            "memories": memories,
            "graph": graph,
            "sources": sources,
        }

    @staticmethod
    def _compact(value: Any, max_chars: int = 1800) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2) if not isinstance(value, str) else value
        return text if len(text) <= max_chars else text[: max_chars - 3] + "..."

    def _prompt(self, topic: LearningTopic, context: dict[str, Any]) -> str:
        evidence_sections: list[str] = []
        if context["code"]:
            evidence_sections.append("\n\n".join(context["code"]))
        if context["memories"]:
            evidence_sections.append("STRUCTURED MEMORY\n" + self._compact(context["memories"], 5000))
        if context["graph"]:
            evidence_sections.append("KNOWLEDGE GRAPH\n" + self._compact(context["graph"], 5000))
        if context["sources"]:
            evidence_sections.append("INDEXED SOURCES\n" + self._compact(context["sources"], 5000))
        evidence = "\n\n---\n\n".join(evidence_sections) or "No local evidence was available for this topic."
        return f"""AUTONOMOUS DAILY LEARNING SESSION

Specialty: {topic.specialty}
Topic: {topic.topic}
Research query: {topic.query}

Goal: Learn ONE useful thing that improves DaQauntum's science, education, engineering, software, or research capability without repeating prior learning.

Rules:
1. Use the local evidence below first. Cite code evidence by file and exact line numbers shown. Cite indexed sources only with their supplied source/chunk handles.
2. If local evidence is absent, you may synthesize from model knowledge, but label it clearly as MODEL SYNTHESIS / NOT EXTERNALLY VERIFIED.
3. Separate observation, inference, and recommendation.
4. Identify at least one limitation, contradiction, or uncertainty.
5. Propose one concrete experiment, code change, or follow-up question.
6. Do NOT modify files or execute consequential tools in this learning pass.
7. Be concise enough for a daily research log but rigorous enough to audit later.

Return these headings:
## Question
## Evidence inspected
## Findings
## Critique / uncertainty
## Implication for DaQauntum
## Next experiment
## Training takeaway

LOCAL EVIDENCE
{evidence}
"""

    def run_daily(self, *, topic: LearningTopic | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Autonomous learning is disabled")
        connector_sync = None
        if bool(self.cfg.get("auto_sync_connected_sources", True)) and self.connectors is not None and self.kernel.runtime.module_enabled("connectors"):
            try:
                connector_sync = self.connectors.sync_all(learning_only=True)
            except Exception as exc:
                connector_sync = {"errors": 1, "message": f"Connected-source sync failed: {type(exc).__name__}: {exc}"}
        topic = topic or self.choose_topic()
        now = datetime.now(timezone.utc)
        session_key = now.strftime("%Y%m%dT%H%M%SZ") + "-" + hashlib.sha1(topic.topic.encode()).hexdigest()[:6]
        cursor = self.memory.conn.execute(
            "INSERT INTO learning_sessions(session_key, specialty, topic, status) VALUES (?, ?, ?, 'running')",
            (session_key, topic.specialty, topic.topic),
        )
        session_id = int(cursor.lastrowid)
        self.memory.conn.commit()

        old_cognition = self.kernel.runtime.cognition_mode
        old_operation = self.kernel.runtime.operation_mode
        desired_operation = str(self.cfg.get("operation_mode", "auto")).lower()
        if desired_operation not in {"auto", "local", "hybrid", "cloud"}:
            desired_operation = "auto"
        self.kernel.runtime.set_modes(operation_mode=desired_operation, cognition_mode="deep")
        context = self._local_context(topic)
        context["connector_sync"] = connector_sync
        prompt = self._prompt(topic, context)
        try:
            result = self.kernel.process(prompt, interaction_mode="autonomous_learning")
        finally:
            self.kernel.runtime.set_modes(operation_mode=old_operation, cognition_mode=old_cognition)

        response = str(result.get("response", "")).strip()
        critic = result.get("critic") or {}
        approved = bool(critic.get("approved", False))
        confidence = float(result.get("confidence", 0.0) or 0.0)
        date = datetime.now().strftime("%Y-%m-%d")
        report_name = f"{date}-{session_id:04d}-{self._slug(topic.topic)}.md"
        report_path = self.report_dir / report_name
        report_text = self._render_report(session_id, session_key, topic, result, context, response)
        report_path.write_text(report_text, encoding="utf-8")
        report_sha = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

        self.memory.conn.execute(
            """
            UPDATE learning_sessions
            SET status='completed', report_path=?, report_sha256=?, summary=?, confidence=?, critic_approved=?,
                provider=?, model=?, completed_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                str(report_path.relative_to(self.project_root)), report_sha,
                response[:1200], confidence, 1 if approved else 0,
                str(result.get("provider", "")), str(result.get("model", "")), session_id,
            ),
        )
        self.memory.conn.commit()

        memory_id = self.memory.add_memory(
            "episodic",
            f"Autonomous learning: {topic.topic}",
            f"DaQauntum completed autonomous learning on {topic.topic}.\nReport: {report_path.relative_to(self.project_root)}\n\n{response[:2200]}",
            project=self.structured_memory.active_project,
            tags=["autonomous-learning", topic.specialty, "daily"],
            confidence=max(0.5, min(1.0, confidence)),
            source="autonomous_learning",
        )
        self.structured_memory.intelligence.on_memory_added(memory_id)
        self.knowledge.sync_memories([memory_id])
        self.memory.add_event("autonomous_learning_completed", {
            "session_id": session_id, "topic": topic.topic, "specialty": topic.specialty,
            "report": str(report_path.relative_to(self.project_root)), "confidence": confidence,
            "critic_approved": approved,
        })
        self._queue_delivery(session_id, report_path, "morning")
        return {
            "session_id": session_id,
            "session_key": session_key,
            "topic": topic.topic,
            "specialty": topic.specialty,
            "report_path": str(report_path),
            "confidence": confidence,
            "critic_approved": approved,
            "provider": result.get("provider"),
            "model": result.get("model"),
            "memory_id": memory_id,
            "summary": response[:500],
            "connector_sync": connector_sync,
        }

    def build_evening_digest(self, date: str | None = None) -> dict[str, Any]:
        date = date or datetime.now().strftime("%Y-%m-%d")
        rows = self.memory.conn.execute(
            "SELECT * FROM learning_sessions WHERE date(started_at, 'localtime') = ? AND status='completed' ORDER BY id",
            (date,),
        ).fetchall()
        sessions = [dict(r) for r in rows]
        lines = [f"# DaQauntum Evening Learning Report — {date}", ""]
        if not sessions:
            lines += ["No completed autonomous learning sessions were recorded today.", ""]
        else:
            lines += [f"Completed sessions: **{len(sessions)}**", ""]
            for row in sessions:
                lines += [f"## {row['topic']}", f"- Specialty: `{row['specialty']}`", f"- Critic approved: `{bool(row['critic_approved'])}`", f"- Confidence: `{float(row['confidence'] or 0):.2f}`", f"- Model: `{row['provider']}/{row['model']}`", f"- Full report: `{row['report_path']}`", "", str(row.get("summary") or "").strip(), ""]
        training_count = int(self.memory.conn.execute("SELECT COUNT(*) FROM memory_items WHERE active=1 AND kind='training'").fetchone()[0])
        lines += ["## Native-model seed status", f"- High-quality training memories available: **{training_count}**", "- Weight fine-tuning: **not enabled**", "- Current growth mechanism: audited memory + knowledge + training-trace accumulation", ""]
        lines += ["## Tomorrow", "DaQauntum will select a topic that is not among its most recent learning sessions and run another Deep cognition pass.", ""]
        path = self.report_dir / f"{date}-evening.md"
        text = "\n".join(lines)
        path.write_text(text, encoding="utf-8")
        self._queue_delivery(None, path, "evening")
        self.memory.add_event("evening_learning_report", {"date": date, "path": str(path.relative_to(self.project_root)), "sessions": len(sessions)})
        return {"date": date, "report_path": str(path), "sessions": sessions, "training_memories": training_count}

    def list_reports(self, limit: int = 30) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for path in sorted(self.report_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            reports.append({
                "name": path.name,
                "path": str(path.relative_to(self.project_root)),
                "size": path.stat().st_size,
                "modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            })
        return reports

    def read_report(self, name: str) -> str:
        path = (self.report_dir / Path(name).name).resolve()
        if path.parent != self.report_dir or not path.exists():
            raise FileNotFoundError(name)
        return path.read_text(encoding="utf-8")

    def _queue_delivery(self, session_id: int | None, report_path: Path, kind: str) -> None:
        out = self.outbox_dir / report_path.name
        if out.resolve() != report_path.resolve():
            out.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.memory.conn.execute(
            "INSERT INTO learning_deliveries(session_id, report_path, delivery_kind, status) VALUES (?, ?, ?, 'ready')",
            (session_id, str(out.relative_to(self.project_root)), kind),
        )
        self.memory.conn.commit()

    def notify(self, title: str, message: str) -> bool:
        try:
            system = platform.system().lower()
            if system == "darwin":
                script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
                subprocess.run(["osascript", "-e", script], check=False, timeout=8)
                return True
            if system == "linux" and shutil_which("notify-send"):
                subprocess.run(["notify-send", title, message], check=False, timeout=8)
                return True
        except Exception:
            return False
        return False

    def _render_report(self, session_id: int, session_key: str, topic: LearningTopic, result: dict[str, Any], context: dict[str, Any], response: str) -> str:
        critic = result.get("critic") or {}
        code_refs = []
        for block in context.get("code", []):
            first = block.splitlines()[0] if block else ""
            if first.startswith("FILE "):
                code_refs.append(first[5:])
        source_citations = []
        for item in context.get("sources", []):
            if item.get("citation"):
                source_citations.append(str(item["citation"]))
        return "\n".join([
            f"# DaQauntum Daily Learning — {datetime.now().strftime('%Y-%m-%d')}",
            "",
            f"- Session: `{session_key}` / #{session_id}",
            f"- Specialty: `{topic.specialty}`",
            f"- Topic: **{topic.topic}**",
            f"- Cognition: `deep`",
            f"- Provider/model: `{result.get('provider', 'unknown')}/{result.get('model', 'unknown')}`",
            f"- Confidence: `{float(result.get('confidence', 0) or 0):.2f}`",
            f"- Critic approved: `{bool(critic.get('approved', False))}`",
            f"- Code evidence: `{', '.join(code_refs) if code_refs else 'none'}`",
            f"- Indexed source citations: `{', '.join(source_citations) if source_citations else 'none'}`",
            f"- Connected-source sync: `{self._compact(context.get('connector_sync') or {'status': 'not-run'}, 800)}`",
            "",
            response or "No response was generated.",
            "",
            "## Audit metadata",
            f"- Critic issues: `{self._compact(critic.get('issues', []), 1200)}`",
            f"- Active project: `{self.structured_memory.active_project or 'none'}`",
            "- Weight fine-tuning performed: `false`",
            "- This report is eligible to contribute audited traces to the future DaQauntum native-model dataset.",
            "",
        ])

    @staticmethod
    def _slug(value: str) -> str:
        value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return value[:60] or "learning"


def shutil_which(name: str) -> str | None:
    from shutil import which
    return which(name)
