from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from memory.store import MemoryStore


_GENERIC_TAGS = {
    "turn", "project-turn", "explicit", "remembered", "approved-trace",
    "workflow", "decision", "release-test", "general", "teacher", "researcher",
    "scientist", "coder", "engineer",
}


class KnowledgeGraph:
    """Local deterministic knowledge/provenance graph introduced in DaQauntum v0.2.6 and used by v0.2.7.

    The graph is stored in the same SQLite database as memory. Automatic graph
    writes are derived only from existing structured memories, explicit memory
    relations, project state, or direct user graph commands. It never invents
    evidence relationships from semantic similarity alone.
    """

    def __init__(self, store: MemoryStore, config: dict[str, Any] | None = None):
        self.store = store
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.context_limit = int(cfg.get("context_limit", 5))
        self.backfill_on_start = bool(cfg.get("backfill_on_start", True))
        self.max_backfill = int(cfg.get("max_backfill", 10000))
        self.conn = store.conn
        self._ensure_schema()
        if self.enabled and self.backfill_on_start:
            self.backfill()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                name TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                project TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(node_type, canonical_key)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_node_id INTEGER NOT NULL,
                target_node_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                confidence REAL NOT NULL DEFAULT 1.0,
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_node_id, target_node_id, relation),
                FOREIGN KEY(source_node_id) REFERENCES knowledge_nodes(id),
                FOREIGN KEY(target_node_id) REFERENCES knowledge_nodes(id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id INTEGER,
                edge_id INTEGER,
                memory_id INTEGER,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                note TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(node_id) REFERENCES knowledge_nodes(id),
                FOREIGN KEY(edge_id) REFERENCES knowledge_edges(id),
                FOREIGN KEY(memory_id) REFERENCES memory_items(id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON knowledge_nodes(node_type)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_nodes_project ON knowledge_nodes(project)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON knowledge_edges(source_node_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON knowledge_edges(target_node_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_edges_relation ON knowledge_edges(relation)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_kg_prov_memory ON knowledge_provenance(memory_id)")
        self.conn.commit()

    # Creation ---------------------------------------------------------------
    def add_node(
        self,
        node_type: str,
        name: str,
        *,
        project: str | None = None,
        canonical_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        node_type = self._clean_relation(node_type) or "concept"
        name = self._compact(name, 700) or "Untitled"
        key = canonical_key or self._canonical(name)
        self.conn.execute(
            """
            INSERT INTO knowledge_nodes(node_type, name, canonical_key, project, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(node_type, canonical_key) DO UPDATE SET
                name = excluded.name,
                project = COALESCE(excluded.project, knowledge_nodes.project),
                metadata_json = excluded.metadata_json,
                active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (node_type, name, key, project, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        row = self.conn.execute(
            "SELECT id FROM knowledge_nodes WHERE node_type = ? AND canonical_key = ?",
            (node_type, key),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def add_edge(
        self,
        source_node_id: int,
        target_node_id: int,
        relation: str,
        *,
        weight: float = 1.0,
        confidence: float = 1.0,
    ) -> int:
        relation = self._clean_relation(relation) or "related_to"
        self.conn.execute(
            """
            INSERT INTO knowledge_edges(source_node_id, target_node_id, relation, weight, confidence)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_node_id, target_node_id, relation) DO UPDATE SET
                weight = MAX(knowledge_edges.weight, excluded.weight),
                confidence = MAX(knowledge_edges.confidence, excluded.confidence),
                active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                int(source_node_id), int(target_node_id), relation,
                float(max(0.0, weight)), float(max(0.0, min(1.0, confidence))),
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM knowledge_edges WHERE source_node_id = ? AND target_node_id = ? AND relation = ?",
            (source_node_id, target_node_id, relation),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def add_provenance(
        self,
        *,
        node_id: int | None = None,
        edge_id: int | None = None,
        memory_id: int | None = None,
        source_type: str,
        source_ref: str,
        confidence: float = 1.0,
        note: str | None = None,
    ) -> int:
        if node_id is None and edge_id is None:
            raise ValueError("Provenance requires node_id or edge_id")
        existing = self.conn.execute(
            """
            SELECT id FROM knowledge_provenance
            WHERE COALESCE(node_id, -1) = COALESCE(?, -1)
              AND COALESCE(edge_id, -1) = COALESCE(?, -1)
              AND COALESCE(memory_id, -1) = COALESCE(?, -1)
              AND source_type = ? AND source_ref = ? AND active = 1
            LIMIT 1
            """,
            (node_id, edge_id, memory_id, source_type, source_ref),
        ).fetchone()
        if existing:
            return int(existing["id"])
        cursor = self.conn.execute(
            """
            INSERT INTO knowledge_provenance(node_id, edge_id, memory_id, source_type, source_ref, confidence, note, active)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                node_id, edge_id, memory_id, source_type, source_ref,
                float(max(0.0, min(1.0, confidence))), note,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    # Memory -> graph --------------------------------------------------------
    def sync_memory(self, memory_id: int) -> list[int]:
        if not self.enabled:
            return []
        memory = self.store.get_memory(memory_id)
        if not memory or not memory.get("active"):
            return []

        node_type = self._node_type_for_memory(memory["kind"])
        if memory["kind"] in {"episodic", "project", "source", "training"}:
            canonical = f"memory:{memory_id}"
        else:
            canonical = self._canonical(memory["content"])

        name = memory["content"] if memory["kind"] in {"semantic", "decision", "procedural"} else memory["title"]
        node_id = self.add_node(
            node_type,
            name,
            project=memory.get("project"),
            canonical_key=canonical,
            metadata={"memory_kind": memory["kind"], "title": memory["title"]},
        )
        self.add_provenance(
            node_id=node_id,
            memory_id=memory_id,
            source_type=memory.get("source") or "structured_memory",
            source_ref=f"memory:{memory_id}",
            confidence=float(memory.get("confidence", 1.0)),
            note="Node derived from structured memory",
        )

        created = [node_id]
        project = memory.get("project")
        if project:
            project_id = self.add_node("project", project, canonical_key=self._canonical(project))
            edge_id = self.add_edge(project_id, node_id, "contains", confidence=memory.get("confidence", 1.0))
            self.add_provenance(
                edge_id=edge_id,
                memory_id=memory_id,
                source_type="project_context",
                source_ref=f"memory:{memory_id}",
                confidence=memory.get("confidence", 1.0),
            )
            created.append(project_id)

        # Tags become explicit concepts only when they are informative.
        for tag in memory.get("tags", []):
            tag_clean = str(tag).strip().lower()
            if not tag_clean or tag_clean in _GENERIC_TAGS or len(tag_clean) < 3:
                continue
            concept_id = self.add_node("concept", tag_clean, canonical_key=self._canonical(tag_clean))
            edge_id = self.add_edge(node_id, concept_id, "tagged_with", confidence=memory.get("confidence", 1.0))
            self.add_provenance(
                edge_id=edge_id,
                memory_id=memory_id,
                source_type="memory_tag",
                source_ref=f"memory:{memory_id}",
                confidence=memory.get("confidence", 1.0),
            )
            created.append(concept_id)

        return list(dict.fromkeys(created))

    def sync_memories(self, memory_ids: list[int]) -> dict[str, int]:
        nodes_before = self._count("knowledge_nodes")
        edges_before = self._count("knowledge_edges", "active = 1")
        for memory_id in memory_ids:
            self.sync_memory(int(memory_id))
        self.sync_memory_relations(memory_ids=memory_ids)
        return {
            "nodes_added": max(0, self._count("knowledge_nodes") - nodes_before),
            "edges_added": max(0, self._count("knowledge_edges", "active = 1") - edges_before),
        }

    def sync_memory_relations(self, memory_ids: list[int] | None = None) -> int:
        relations = self.store.list_memory_relations(limit=100000)
        touched = set(int(value) for value in memory_ids or [])
        added = 0
        for relation in relations:
            src_mem = int(relation["source_memory_id"])
            dst_mem = int(relation["target_memory_id"])
            if touched and src_mem not in touched and dst_mem not in touched:
                continue
            src_node = self.primary_node_for_memory(src_mem)
            dst_node = self.primary_node_for_memory(dst_mem)
            if not src_node or not dst_node:
                continue
            edge_id = self.add_edge(
                src_node,
                dst_node,
                relation["relation"],
                weight=float(relation.get("score", 1.0)),
                confidence=min(
                    float((self.store.get_memory(src_mem) or {}).get("confidence", 1.0)),
                    float((self.store.get_memory(dst_mem) or {}).get("confidence", 1.0)),
                ),
            )
            ref = f"memory_relation:{relation['id']}"
            self.add_provenance(
                edge_id=edge_id,
                memory_id=src_mem,
                source_type="memory_relation",
                source_ref=ref,
                note=relation.get("note"),
            )
            self.add_provenance(
                edge_id=edge_id,
                memory_id=dst_mem,
                source_type="memory_relation",
                source_ref=ref,
                note=relation.get("note"),
            )
            added += 1
        return added

    def backfill(self) -> dict[str, int]:
        if not self.enabled:
            return {"memories_scanned": 0, "nodes_added": 0, "edges_added": 0}
        memories = self.store.list_memories(limit=self.max_backfill)
        before_nodes = self._count("knowledge_nodes")
        before_edges = self._count("knowledge_edges", "active = 1")
        for item in reversed(memories):
            self.sync_memory(item["id"])
        self.sync_memory_relations()
        return {
            "memories_scanned": len(memories),
            "nodes_added": max(0, self._count("knowledge_nodes") - before_nodes),
            "edges_added": max(0, self._count("knowledge_edges", "active = 1") - before_edges),
        }

    def primary_node_for_memory(self, memory_id: int) -> int | None:
        row = self.conn.execute(
            """
            SELECT node_id FROM knowledge_provenance
            WHERE memory_id = ? AND node_id IS NOT NULL
            ORDER BY id ASC LIMIT 1
            """,
            (memory_id,),
        ).fetchone()
        return int(row["node_id"]) if row else None

    # Retrieval --------------------------------------------------------------
    def search(self, query: str, limit: int = 10, project: str | None = None) -> list[dict[str, Any]]:
        terms = self._terms(query)
        clauses = ["active = 1"]
        params: list[Any] = []
        if project:
            clauses.append("(project = ? OR project IS NULL)")
            params.append(project)
        if terms:
            term_sql = []
            for term in terms:
                term_sql.append("(LOWER(name) LIKE ? OR LOWER(metadata_json) LIKE ?)")
                params.extend([f"%{term}%", f"%{term}%"])
            clauses.append("(" + " OR ".join(term_sql) + ")")
        params.append(max(limit * 5, limit))
        rows = self.conn.execute(
            "SELECT * FROM knowledge_nodes WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()
        items = [self._node_row(row) for row in rows]
        if not terms:
            return items[:limit]
        q = set(terms)
        scored: list[tuple[float, dict[str, Any]]] = []
        type_weight = {"claim": 2.0, "decision": 1.9, "procedure": 1.7, "project": 1.6, "source": 1.5, "concept": 1.2}
        for index, item in enumerate(items):
            text = f"{item['name']} {json.dumps(item.get('metadata', {}), ensure_ascii=False)}".lower()
            overlap = sum(1 for term in q if term in text)
            score = overlap * type_weight.get(item["node_type"], 1.0)
            if project and item.get("project") == project:
                score += 1.5
            score += max(0.0, 0.5 - index * 0.01)
            if score > 0:
                item["graph_score"] = round(score, 4)
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def context_messages(self, query: str, *, project: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        nodes = self.search(query, limit=limit or self.context_limit, project=project)
        messages: list[dict[str, Any]] = []
        for node in nodes:
            neighbors = self.neighbors(node["id"], limit=6)
            relation_text = "; ".join(
                f"{edge['direction']} {edge['relation']} {edge['other_node']['node_type']}:{edge['other_node']['name']}"
                for edge in neighbors[:4]
            )
            provenance = self.provenance(node_id=node["id"], limit=4)
            source_text = ", ".join(
                f"{item['source_type']}:{item['source_ref']}" for item in provenance[:3]
            )
            content = f"[knowledge-node id={node['id']} type={node['node_type']}] {node['name']}"
            if relation_text:
                content += f" | relations: {relation_text}"
            if source_text:
                content += f" | provenance: {source_text}"
            messages.append({"role": "knowledge", "content": content, "knowledge_node_id": node["id"]})
        return messages

    def get_node(self, node_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM knowledge_nodes WHERE id = ?", (node_id,)).fetchone()
        if not row:
            return None
        node = self._node_row(row)
        node["neighbors"] = self.neighbors(node_id, limit=100)
        node["provenance"] = self.provenance(node_id=node_id, limit=100)
        return node

    def neighbors(self, node_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT e.*, s.node_type AS s_type, s.name AS s_name, s.project AS s_project,
                   t.node_type AS t_type, t.name AS t_name, t.project AS t_project
            FROM knowledge_edges e
            JOIN knowledge_nodes s ON s.id = e.source_node_id
            JOIN knowledge_nodes t ON t.id = e.target_node_id
            WHERE e.active = 1 AND (e.source_node_id = ? OR e.target_node_id = ?)
            ORDER BY e.id DESC LIMIT ?
            """,
            (node_id, node_id, limit),
        ).fetchall()
        out = []
        for row in rows:
            outgoing = int(row["source_node_id"]) == node_id
            other = {
                "id": int(row["target_node_id"] if outgoing else row["source_node_id"]),
                "node_type": row["t_type"] if outgoing else row["s_type"],
                "name": row["t_name"] if outgoing else row["s_name"],
                "project": row["t_project"] if outgoing else row["s_project"],
            }
            out.append({
                "edge_id": int(row["id"]),
                "relation": row["relation"],
                "direction": "outgoing" if outgoing else "incoming",
                "weight": float(row["weight"]),
                "confidence": float(row["confidence"]),
                "other_node": other,
            })
        return out

    def provenance(self, *, node_id: int | None = None, edge_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if node_id is None and edge_id is None:
            return []
        clauses = []
        params: list[Any] = []
        if node_id is not None:
            clauses.append("p.node_id = ?")
            params.append(node_id)
        if edge_id is not None:
            clauses.append("p.edge_id = ?")
            params.append(edge_id)
        params.append(limit)
        sql = (
            "SELECT p.*, m.kind AS memory_kind, m.title AS memory_title, m.content AS memory_content "
            "FROM knowledge_provenance p "
            "LEFT JOIN memory_items m ON m.id = p.memory_id "
            "WHERE p.active = 1 AND (" + " OR ".join(clauses) + ") ORDER BY p.id DESC LIMIT ?"
        )
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def link_nodes(self, source_node_id: int, relation: str, target_node_id: int, note: str | None = None) -> dict[str, Any]:
        if not self.get_node(source_node_id) or not self.get_node(target_node_id):
            raise ValueError("Both knowledge node IDs must exist")
        edge_id = self.add_edge(source_node_id, target_node_id, relation)
        self.add_provenance(
            edge_id=edge_id,
            source_type="user_explicit",
            source_ref="cli:/link",
            note=note or "Relationship explicitly created by user",
        )
        return {"edge_id": edge_id, "source_node_id": source_node_id, "relation": self._clean_relation(relation), "target_node_id": target_node_id}


    def deactivate_memory(self, memory_id: int) -> dict[str, int]:
        """Deactivate provenance from a forgotten memory and orphaned graph items."""
        node_rows = self.conn.execute(
            "SELECT DISTINCT node_id FROM knowledge_provenance WHERE memory_id = ? AND node_id IS NOT NULL AND active = 1",
            (memory_id,),
        ).fetchall()
        edge_rows = self.conn.execute(
            "SELECT DISTINCT edge_id FROM knowledge_provenance WHERE memory_id = ? AND edge_id IS NOT NULL AND active = 1",
            (memory_id,),
        ).fetchall()
        self.conn.execute("UPDATE knowledge_provenance SET active = 0 WHERE memory_id = ?", (memory_id,))
        deactivated_nodes = 0
        deactivated_edges = 0
        for row in edge_rows:
            edge_id = int(row["edge_id"])
            remaining = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_provenance WHERE edge_id = ? AND active = 1", (edge_id,)
            ).fetchone()[0]
            if not remaining:
                self.conn.execute("UPDATE knowledge_edges SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (edge_id,))
                deactivated_edges += 1
        for row in node_rows:
            node_id = int(row["node_id"])
            remaining = self.conn.execute(
                "SELECT COUNT(*) FROM knowledge_provenance WHERE node_id = ? AND active = 1", (node_id,)
            ).fetchone()[0]
            if not remaining:
                self.conn.execute("UPDATE knowledge_nodes SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (node_id,))
                self.conn.execute(
                    "UPDATE knowledge_edges SET active = 0, updated_at = CURRENT_TIMESTAMP WHERE source_node_id = ? OR target_node_id = ?",
                    (node_id, node_id),
                )
                deactivated_nodes += 1
        self.conn.commit()
        return {"nodes": deactivated_nodes, "edges": deactivated_edges}

    def stats(self) -> dict[str, Any]:
        node_types = {
            row["node_type"]: int(row["count"])
            for row in self.conn.execute(
                "SELECT node_type, COUNT(*) AS count FROM knowledge_nodes WHERE active = 1 GROUP BY node_type"
            ).fetchall()
        }
        edge_types = {
            row["relation"]: int(row["count"])
            for row in self.conn.execute(
                "SELECT relation, COUNT(*) AS count FROM knowledge_edges WHERE active = 1 GROUP BY relation"
            ).fetchall()
        }
        return {
            "enabled": self.enabled,
            "nodes": sum(node_types.values()),
            "edges": sum(edge_types.values()),
            "provenance_records": self._count("knowledge_provenance", "active = 1"),
            "node_types": node_types,
            "edge_types": edge_types,
        }

    # Helpers ----------------------------------------------------------------
    def _count(self, table: str, where: str | None = None) -> int:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += " WHERE " + where
        return int(self.conn.execute(sql).fetchone()[0])

    @staticmethod
    def _node_row(row) -> dict[str, Any]:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        return {
            "id": int(row["id"]),
            "node_type": row["node_type"],
            "name": row["name"],
            "canonical_key": row["canonical_key"],
            "project": row["project"],
            "metadata": metadata,
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _node_type_for_memory(kind: str) -> str:
        return {
            "semantic": "claim",
            "decision": "decision",
            "procedural": "procedure",
            "source": "source",
            "project": "project_record",
            "episodic": "episode",
            "training": "training_trace",
        }.get(kind, "memory")

    @staticmethod
    def _clean_relation(value: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9_\- ]+", "", str(value).strip().lower()).replace("-", "_").replace(" ", "_")
        return re.sub(r"_+", "_", value).strip("_")[:64]

    @staticmethod
    def _canonical(value: str) -> str:
        text = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        return re.sub(r"\s+", " ", text)[:1000] or "unnamed"

    @staticmethod
    def _compact(value: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(value).strip())
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

    @staticmethod
    def _terms(query: str) -> list[str]:
        terms = []
        seen = set()
        for raw in re.findall(r"[a-zA-Z0-9_\-]+", query.lower()):
            if len(raw) < 3 or raw in seen:
                continue
            seen.add(raw)
            terms.append(raw)
            if len(terms) >= 12:
                break
        return terms
