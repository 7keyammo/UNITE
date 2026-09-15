from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


MEMORY_KINDS = {
    "episodic",
    "semantic",
    "project",
    "procedural",
    "decision",
    "source",
    "training",
}


class MemoryStore:
    """SQLite-backed raw conversation + structured memory store.

    v0.2.5 keeps prior tables intact and adds memory-intelligence metadata/relations
    with CREATE IF NOT EXISTS so older DaQauntum databases remain loadable.
    """

    def __init__(self, db_path: str = "data/daqauntum.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                project TEXT,
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                source TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_metrics (
                memory_id INTEGER PRIMARY KEY,
                salience REAL NOT NULL DEFAULT 0.5,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed DATETIME,
                embedding_json TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(memory_id) REFERENCES memory_items(id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_memory_id INTEGER NOT NULL,
                target_memory_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                score REAL NOT NULL DEFAULT 1.0,
                note TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_memory_id, target_memory_id, relation),
                FOREIGN KEY(source_memory_id) REFERENCES memory_items(id),
                FOREIGN KEY(target_memory_id) REFERENCES memory_items(id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_relations_relation ON memory_relations(relation)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_relations_source ON memory_relations(source_memory_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_relations_target ON memory_relations(target_memory_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_kind ON memory_items(kind)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_project ON memory_items(project)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memory_items_active ON memory_items(active)"
        )
        self.conn.commit()

    # Raw conversation memory -------------------------------------------------
    def add(self, role: str, content: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO messages(role, content) VALUES (?, ?)",
            (role, content),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_event(self, event_type: str, payload: dict[str, Any]) -> int:
        cursor = self.conn.execute(
            "INSERT INTO events(event_type, payload_json) VALUES (?, ?)",
            (event_type, json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, role, content, created_at FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def search(self, query: str, limit: int = 8) -> Iterable[dict[str, Any]]:
        terms = self._terms(query)
        if not terms:
            return iter(())
        clauses = " OR ".join("content LIKE ?" for _ in terms)
        params: list[Any] = [f"%{term}%" for term in terms] + [limit]
        rows = self.conn.execute(
            f"SELECT id, role, content, created_at FROM messages WHERE {clauses} "
            "ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return iter(dict(row) for row in rows)

    # Structured memory -------------------------------------------------------
    def add_memory(
        self,
        kind: str,
        title: str,
        content: str,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> int:
        kind = kind.strip().lower()
        if kind not in MEMORY_KINDS:
            raise ValueError(f"Unsupported memory kind: {kind}")
        cursor = self.conn.execute(
            """
            INSERT INTO memory_items(kind, title, content, project, tags_json, confidence, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                title.strip() or kind.title(),
                content.strip(),
                project.strip() if project else None,
                json.dumps(tags or [], ensure_ascii=False),
                float(max(0.0, min(1.0, confidence))),
                source,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM memory_items WHERE id = ?",
            (memory_id,),
        ).fetchone()
        return self._memory_row(row) if row else None

    def deactivate_memory(self, memory_id: int) -> bool:
        cursor = self.conn.execute(
            "UPDATE memory_items SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND active = 1",
            (memory_id,),
        )
        if cursor.rowcount > 0:
            self.conn.execute(
                "UPDATE memory_relations SET active = 0 WHERE source_memory_id = ? OR target_memory_id = ?",
                (memory_id, memory_id),
            )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_memories(
        self,
        *,
        kind: str | None = None,
        project: str | None = None,
        limit: int = 20,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if active_only:
            clauses.append("active = 1")
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if project:
            clauses.append("project = ?")
            params.append(project)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM memory_items{where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._memory_row(row) for row in rows]

    def search_memories(
        self,
        query: str,
        *,
        limit: int = 8,
        project: str | None = None,
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        terms = self._terms(query)
        clauses = ["active = 1"]
        params: list[Any] = []
        if project:
            clauses.append("(project = ? OR project IS NULL)")
            params.append(project)
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(kinds)
        if terms:
            term_clauses = []
            for term in terms:
                term_clauses.append("(title LIKE ? OR content LIKE ? OR tags_json LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
            clauses.append("(" + " OR ".join(term_clauses) + ")")
        params.append(max(limit * 4, limit))
        rows = self.conn.execute(
            "SELECT * FROM memory_items WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        memories = [self._memory_row(row) for row in rows]
        if not terms:
            return memories[:limit]

        # Lightweight deterministic ranking: token overlap + kind/project weights + recency.
        q = set(term.lower() for term in terms)
        kind_weight = {
            "decision": 2.0,
            "semantic": 1.8,
            "project": 1.7,
            "procedural": 1.5,
            "source": 1.3,
            "episodic": 1.0,
            "training": 0.7,
        }
        scored: list[tuple[float, dict[str, Any]]] = []
        for index, item in enumerate(memories):
            haystack = f"{item['title']} {item['content']} {' '.join(item['tags'])}".lower()
            overlap = sum(1 for term in q if term in haystack)
            score = overlap * kind_weight.get(item["kind"], 1.0)
            if project and item.get("project") == project:
                score += 2.5
            score += max(0.0, 1.0 - (index * 0.03))
            score *= 0.5 + (float(item.get("confidence", 1.0)) / 2.0)
            scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for score, item in scored[:limit] if score > 0]

    # Memory intelligence metadata --------------------------------------------
    def ensure_memory_metrics(self, memory_id: int, *, salience: float = 0.5) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_metrics(memory_id, salience) VALUES (?, ?)
            ON CONFLICT(memory_id) DO NOTHING
            """,
            (memory_id, float(max(0.0, min(1.0, salience)))),
        )
        self.conn.commit()

    def get_memory_metrics(self, memory_id: int) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT memory_id, salience, access_count, last_accessed, updated_at FROM memory_metrics WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        return dict(row) if row else {}

    def touch_memory(self, memory_id: int) -> None:
        self.ensure_memory_metrics(memory_id)
        self.conn.execute(
            """
            UPDATE memory_metrics
            SET access_count = access_count + 1, last_accessed = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
            WHERE memory_id = ?
            """,
            (memory_id,),
        )
        self.conn.commit()

    def set_memory_salience(self, memory_id: int, salience: float) -> None:
        self.ensure_memory_metrics(memory_id, salience=salience)
        self.conn.execute(
            "UPDATE memory_metrics SET salience = ?, updated_at = CURRENT_TIMESTAMP WHERE memory_id = ?",
            (float(max(0.0, min(1.0, salience))), memory_id),
        )
        self.conn.commit()

    def set_memory_embedding(self, memory_id: int, embedding: list[float]) -> None:
        self.ensure_memory_metrics(memory_id)
        self.conn.execute(
            "UPDATE memory_metrics SET embedding_json = ?, updated_at = CURRENT_TIMESTAMP WHERE memory_id = ?",
            (json.dumps(embedding, separators=(",", ":")), memory_id),
        )
        self.conn.commit()

    def get_memory_embedding(self, memory_id: int) -> list[float] | None:
        row = self.conn.execute(
            "SELECT embedding_json FROM memory_metrics WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        if not row or not row["embedding_json"]:
            return None
        try:
            raw = json.loads(row["embedding_json"])
            return [float(value) for value in raw] if isinstance(raw, list) else None
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def add_memory_relation(
        self,
        source_memory_id: int,
        target_memory_id: int,
        relation: str,
        score: float = 1.0,
        note: str | None = None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO memory_relations(source_memory_id, target_memory_id, relation, score, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_memory_id, target_memory_id, relation)
            DO UPDATE SET score = excluded.score, note = excluded.note, active = 1
            """,
            (source_memory_id, target_memory_id, relation, float(score), note),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def list_memory_relations(
        self,
        *,
        memory_id: int | None = None,
        relation: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["active = 1"]
        params: list[Any] = []
        if memory_id is not None:
            clauses.append("(source_memory_id = ? OR target_memory_id = ?)")
            params.extend([memory_id, memory_id])
        if relation:
            clauses.append("relation = ?")
            params.append(relation)
        params.append(limit)
        rows = self.conn.execute(
            "SELECT * FROM memory_relations WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def memory_intelligence_stats(self) -> dict[str, Any]:
        metric_count = int(self.conn.execute("SELECT COUNT(*) FROM memory_metrics").fetchone()[0])
        embedded_count = int(self.conn.execute("SELECT COUNT(*) FROM memory_metrics WHERE embedding_json IS NOT NULL").fetchone()[0])
        relation_count = int(self.conn.execute("SELECT COUNT(*) FROM memory_relations WHERE active = 1").fetchone()[0])
        contradictions = int(self.conn.execute("SELECT COUNT(*) FROM memory_relations WHERE active = 1 AND relation = 'contradicts'").fetchone()[0])
        consolidated = int(self.conn.execute("SELECT COUNT(*) FROM memory_relations WHERE active = 1 AND relation = 'consolidated_into'").fetchone()[0])
        return {
            "metric_rows": metric_count,
            "embedded_memories": embedded_count,
            "relations": relation_count,
            "contradictions": contradictions,
            "consolidated_relations": consolidated,
        }

    def memory_stats(self) -> dict[str, Any]:
        counts = {
            row["kind"]: int(row["count"])
            for row in self.conn.execute(
                "SELECT kind, COUNT(*) AS count FROM memory_items WHERE active = 1 GROUP BY kind"
            ).fetchall()
        }
        total = sum(counts.values())
        message_count = int(self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        event_count = int(self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        return {
            "structured_total": total,
            "by_kind": counts,
            "messages": message_count,
            "events": event_count,
            "active_project": self.get_state("active_project"),
        }

    # Persistent state --------------------------------------------------------
    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO memory_state(key, value_json, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = CURRENT_TIMESTAMP
            """,
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value_json FROM memory_state WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    @staticmethod
    def _terms(query: str) -> list[str]:
        cleaned = query.replace("?", " ").replace(",", " ").replace(".", " ").replace(":", " ")
        seen: set[str] = set()
        terms: list[str] = []
        for raw in cleaned.split():
            term = raw.strip().lower()
            if len(term) < 3 or term in seen:
                continue
            seen.add(term)
            terms.append(term)
            if len(terms) >= 10:
                break
        return terms

    @staticmethod
    def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            tags = json.loads(row["tags_json"] or "[]")
        except json.JSONDecodeError:
            tags = []
        return {
            "id": int(row["id"]),
            "kind": row["kind"],
            "title": row["title"],
            "content": row["content"],
            "project": row["project"],
            "tags": tags,
            "confidence": float(row["confidence"]),
            "source": row["source"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
