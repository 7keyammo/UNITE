from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any


class UniverseState:
    """Local-first identity/evolution model for the DaQauntum Universe UI.

    v0.3.7 also surfaces local FRAME workspaces as system molecules. Peer atoms are
    represented in the schema for future authenticated synchronization, but this
    class does not perform networking.
    """

    def __init__(self, memory, structured_memory, knowledge, sources, calls, runtime):
        self.memory = memory
        self.structured_memory = structured_memory
        self.knowledge = knowledge
        self.sources = sources
        self.calls = calls
        self.runtime = runtime
        identity = self.memory.get_state("universe.identity", None)
        if not isinstance(identity, dict):
            identity = {
                "atom_id": uuid.uuid4().hex[:16],
                "name": "DaQauntum",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.memory.set_state("universe.identity", identity)
        self.identity = identity

    def snapshot(self) -> dict[str, Any]:
        mem = self.structured_memory.stats()
        graph = self.knowledge.stats()
        src = self.sources.stats()
        calls = self.calls.stats()
        messages = int(mem.get("messages", 0) or 0)
        memories = int(mem.get("structured_total", 0) or 0)
        nodes = int(graph.get("nodes", 0) or 0)
        sources = int(src.get("sources", 0) or 0)
        sessions = int(calls.get("sessions", 0) or 0)
        try:
            learning_sessions = int(self.memory.conn.execute("SELECT COUNT(*) FROM learning_sessions WHERE status='completed'").fetchone()[0])
        except Exception:
            learning_sessions = 0
        xp = messages + memories * 3 + nodes * 2 + sources * 8 + sessions * 5 + learning_sessions * 12
        level = max(1, int(math.sqrt(max(0, xp) / 18.0)) + 1)
        energy = min(1.0, 0.18 + math.log1p(max(0, xp)) / 9.0)
        radius = min(86, 30 + level * 3)

        project_rows = self.memory.conn.execute(
            """
            SELECT project, COUNT(*) AS count
            FROM memory_items
            WHERE active = 1 AND project IS NOT NULL AND TRIM(project) != ''
            GROUP BY project ORDER BY count DESC LIMIT 12
            """
        ).fetchall()
        molecules = [
            {"name": row["project"], "mass": int(row["count"]), "kind": "project"}
            for row in project_rows
        ]
        try:
            workspace_rows = self.memory.conn.execute(
                """SELECT w.id,w.name,w.stage,
                   (SELECT COUNT(*) FROM os_skills s WHERE s.workspace_id=w.id AND s.active=1) +
                   (SELECT COUNT(*) FROM os_agents a WHERE a.workspace_id=w.id AND a.active=1) +
                   (SELECT COUNT(*) FROM os_jobs j WHERE j.workspace_id=w.id AND j.status='completed') AS mass
                   FROM os_workspaces w WHERE w.active=1 ORDER BY w.updated_at DESC LIMIT 12"""
            ).fetchall()
            existing = {m["name"] for m in molecules}
            for row in workspace_rows:
                name = row["name"]
                if name not in existing:
                    molecules.append({"name": name, "mass": max(1, int(row["mass"] or 0)), "kind": "workspace", "stage": row["stage"], "workspace_id": int(row["id"])})
        except Exception:
            pass
        peers = self.memory.get_state("universe.peers", [])
        if not isinstance(peers, list):
            peers = []

        return {
            "atom": {
                **self.identity,
                "level": level,
                "experience": xp,
                "energy": round(energy, 3),
                "radius": radius,
                "active_project": self.structured_memory.active_project,
            },
            "molecules": molecules,
            "peers": peers,
            "modules": self.runtime.status()["modules"],
            "counts": {
                "messages": messages,
                "memories": memories,
                "knowledge_nodes": nodes,
                "sources": sources,
                "calls": sessions,
                "learning_sessions": learning_sessions,
            },
            "network": {
                "connected": False,
                "mode": "local_universe",
                "note": "Peer-atom synchronization is reserved for a future authenticated networking release.",
            },
        }
