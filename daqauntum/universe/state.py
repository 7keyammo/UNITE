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

    def __init__(self, memory, structured_memory, knowledge, sources, calls, runtime, kernel=None):
        self.memory = memory
        self.structured_memory = structured_memory
        self.knowledge = knowledge
        self.sources = sources
        self.calls = calls
        self.runtime = runtime
        # Optional back-reference so the brain view can read live subsystem
        # state. Kept optional so UniverseState still works standalone.
        self.kernel = kernel
        identity = self.memory.get_state("universe.identity", None)
        if not isinstance(identity, dict):
            identity = {
                "atom_id": uuid.uuid4().hex[:16],
                "name": "DaQauntum",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.memory.set_state("universe.identity", identity)
        self.identity = identity

    # Brain structure ---------------------------------------------------------
    # Each shell is a layer of the atom, ordered from the nucleus outwards by how
    # directly it constitutes DaQauntum's identity: what it knows, then how it
    # thinks, then what it can perceive, then what it can do, then how it grows.
    SHELLS: tuple[dict[str, Any], ...] = (
        {
            "name": "identity",
            "title": "Memory & Knowledge",
            "description": "What DaQauntum is: durable memory, provenance-tracked knowledge, indexed sources.",
            "subsystems": ("memory", "knowledge", "sources"),
        },
        {
            "name": "cognition",
            "title": "Reasoning",
            "description": "How it thinks: planning, critique, and deterministic model/privacy routing.",
            "subsystems": ("planner", "critic", "policy"),
        },
        {
            "name": "senses",
            "title": "Perception & Presence",
            "description": "What it can observe, only where explicitly approved.",
            "subsystems": ("voice", "perception", "presence", "events", "drivers"),
        },
        {
            "name": "hands",
            "title": "Action",
            "description": "What it can do, always through the permission gate.",
            "subsystems": ("tools", "computer", "integrations", "connectors"),
        },
        {
            "name": "growth",
            "title": "Learning & Workspaces",
            "description": "How it improves: autonomous learning, workspaces, native-model candidates.",
            "subsystems": ("learning", "workspaces", "native_model", "obsidian"),
        },
    )

    def brain_structure(self) -> dict[str, Any]:
        """A live systems view of the atom, for the universe visualization.

        Every node carries real runtime state rather than a fixed diagram: it is
        dimmed when its module is switched off, sized by how much it actually
        holds, and marked degraded when the subsystem says it is. A picture that
        showed capability the system does not have would be the front-end
        equivalent of a fake integration card.
        """
        snapshot = self.snapshot()
        modules = snapshot.get("modules") or {}
        status: dict[str, Any] = {}
        if self.kernel is not None:
            try:
                status = self.kernel.status()
            except Exception:
                status = {}

        shells: list[dict[str, Any]] = []
        for index, shell in enumerate(self.SHELLS):
            nodes = []
            for name in shell["subsystems"]:
                node = self._subsystem_node(name, modules, status, snapshot)
                if node:
                    nodes.append(node)
            shells.append({
                "name": shell["name"],
                "title": shell["title"],
                "description": shell["description"],
                "index": index,
                "nodes": nodes,
                "active": sum(1 for n in nodes if n["enabled"]),
                "total": len(nodes),
            })

        return {
            "nucleus": snapshot["atom"],
            "shells": shells,
            "molecules": snapshot["molecules"],
            "network": self._network_view(status),
            "counts": snapshot["counts"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _subsystem_node(
        self,
        name: str,
        modules: dict[str, Any],
        status: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any] | None:
        counts = snapshot.get("counts") or {}
        enabled = bool(modules.get(name, True))
        mass = 0
        detail = ""
        degraded = False

        if name == "memory":
            mass = int(counts.get("memories", 0))
            detail = f"{counts.get('memories', 0)} memories · {counts.get('messages', 0)} messages"
        elif name == "knowledge":
            mass = int(counts.get("knowledge_nodes", 0))
            detail = f"{counts.get('knowledge_nodes', 0)} nodes"
        elif name == "sources":
            mass = int(counts.get("sources", 0))
            detail = f"{counts.get('sources', 0)} indexed sources"
        elif name in {"planner", "critic"}:
            role = (status.get("roles") or {}).get("executor" if name == "critic" else "planner") or {}
            enabled = bool(modules.get(name, True))
            detail = f"{role.get('provider', '—')}/{role.get('model', '—')}" if role else "not routed"
            mass = 6
        elif name == "policy":
            policy = status.get("policy") or {}
            detail = "local-only for sensitive" if policy.get("local_only_for_sensitive") else "standard routing"
            mass = 6
            enabled = True
        elif name == "voice":
            voice = status.get("voice") or {}
            stt = (voice.get("stt") or {}).get("available")
            detail = "local STT ready" if stt else "local STT not configured"
            degraded = not stt
            mass = 6
        elif name == "perception":
            perception = status.get("perception") or {}
            mass = int(perception.get("frames", 0) or 0)
            detail = f"{mass} frames"
        elif name == "presence":
            presence = (status.get("presence") or {}).get("stats") or {}
            mass = int(presence.get("samples", 0) or 0)
            detail = f"{mass} samples"
        elif name == "events":
            events = (status.get("events") or {}).get("bus") or {}
            mass = int(events.get("events", 0) or 0)
            detail = f"{mass} events · {(status.get('events') or {}).get('reactions', {}).get('rules_enabled', 0)} rules"
        elif name == "drivers":
            drivers = status.get("drivers") or {}
            usable = drivers.get("usable") or []
            mass = len(usable) * 4
            detail = f"{len(usable)} usable driver(s)" if usable else "no driver configured"
            degraded = not usable
        elif name == "tools":
            mass = len(status.get("tools") or [])
            detail = f"{mass} registered tools · L{status.get('permission_level', '?')}"
            enabled = True
        elif name == "computer":
            computer = status.get("computer") or {}
            detail = f"autonomy: {computer.get('autonomy', 'observe')}"
            mass = 6
        elif name == "integrations":
            integrations = status.get("integrations") or {}
            healthy = int(integrations.get("healthy", 0) or 0)
            mass = healthy * 3
            detail = f"{healthy} healthy"
            degraded = healthy == 0
        elif name == "connectors":
            connectors = status.get("connectors") or {}
            mass = int(connectors.get("connectors", 0) or 0)
            detail = f"{mass} connected source(s)"
        elif name == "learning":
            mass = int(counts.get("learning_sessions", 0))
            detail = f"{mass} completed sessions"
        elif name == "workspaces":
            workspaces = status.get("workspaces") or {}
            mass = int(workspaces.get("workspaces", 0) or 0)
            detail = f"{mass} workspace(s)"
        elif name == "native_model":
            native = status.get("native_model") or {}
            registry = native.get("registry") or {}
            mass = int(native.get("candidate_examples", 0) or 0) // 10
            promoted = registry.get("promoted")
            detail = f"{native.get('candidate_examples', 0)} candidate examples"
            if promoted:
                detail += f" · live: {promoted.get('name')}"
        elif name == "obsidian":
            vault = (status.get("obsidian") or {}).get("vault") or {}
            mass = int(vault.get("user_notes", 0) or 0)
            detail = f"{vault.get('user_notes', 0)} of your notes indexed" if vault.get("exists") else "vault not created"
            degraded = not vault.get("exists")
        else:
            return None

        return {
            "name": name,
            "enabled": enabled,
            "degraded": bool(degraded and enabled),
            "mass": max(0, int(mass)),
            "detail": detail,
        }

    def _network_view(self, status: dict[str, Any]) -> dict[str, Any]:
        """The real DaQauntum network: this atom and its enrolled devices.

        Peer atoms stay explicitly planned. Drawing them as though they existed
        is exactly the kind of claim the reality map exists to prevent.
        """
        identity = status.get("identity") or {}
        devices = identity.get("devices") or {}
        peers = self.memory.get_state("universe.peers", [])
        if not isinstance(peers, list):
            peers = []
        device_list: list[dict[str, Any]] = []
        if self.kernel is not None and getattr(self.kernel, "identity", None) is not None:
            try:
                device_list = [
                    {
                        "name": d["name"],
                        "platform": d.get("platform"),
                        "scopes": d.get("scopes", []),
                        "active": d.get("active"),
                        "last_seen_at": d.get("last_seen_at"),
                    }
                    for d in self.kernel.identity.list_devices() if d.get("active")
                ]
            except Exception:
                device_list = []
        return {
            "connected": bool(device_list),
            "mode": "single_atom_with_devices",
            "devices": device_list,
            "device_count": int(devices.get("active", len(device_list))),
            "peers": peers,
            "peer_atoms_supported": False,
            "note": (
                "This atom plus the devices you enrolled. Peer-atom networking is not "
                "implemented; it waits on mature identity, ACLs and a shared protocol."
            ),
        }

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
