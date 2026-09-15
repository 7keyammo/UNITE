from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from obsidian.vault import EXPORT_FOLDERS, Vault, safe_filename, wikilink


class ObsidianMemoryBridge:
    """Two-way link between DaQauntum's memory and a local Obsidian vault.

    Two directions, with deliberately different trust:

    * **Export** writes DaQauntum's structured memory, decisions, projects and
      knowledge nodes into the vault as linked Markdown. This is a *projection*,
      not the store. The SQLite database stays authoritative; the vault can be
      deleted and regenerated without losing anything.

    * **Import** indexes the user's own notes back as provenance-tracked
      sources. A note becomes retrievable evidence with a citation - it does not
      become a semantic fact. Retrieval is not proof, so nothing read from the
      vault is promoted to durable knowledge on its own.

    Notes DaQauntum wrote are skipped on import. Without that, an exported
    memory would come back as an independent source and DaQauntum would cite
    itself as corroboration for its own claim.
    """

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.config = dict(config or {})
        root = self.config.get("vault_root") or self.config.get("obsidian_root") or "data/obsidian/DaQauntum"
        self.vault = Vault(root)
        self.enabled = bool(self.config.get("enabled", True))
        self.export_limit = int(self.config.get("export_limit", 2000))
        self.import_limit = int(self.config.get("import_limit", 1000))
        self.min_confidence = float(self.config.get("min_export_confidence", 0.0))

    # Export ------------------------------------------------------------------
    def export(self, *, project: str | None = None) -> dict[str, Any]:
        """Project DaQauntum's memory into the vault as linked notes."""
        if not self.enabled:
            return {"ok": False, "reason": "obsidian-disabled"}
        self.vault.ensure()
        started = time.perf_counter()

        memories = self._export_memories(project=project)
        knowledge = self._export_knowledge(project=project)
        projects = self._export_projects()
        index = self._write_index(memories, knowledge, projects)

        result = {
            "ok": True,
            "root": str(self.vault.root),
            "memories": len(memories),
            "knowledge_nodes": len(knowledge),
            "projects": len(projects),
            "index": str(index),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
            "note": "The vault is a projection of DaQauntum's memory, not the authoritative store.",
        }
        self.kernel.memory.add_event("obsidian_exported", {k: result[k] for k in ("memories", "knowledge_nodes", "projects")})
        return result

    def _export_memories(self, *, project: str | None = None) -> list[dict[str, Any]]:
        clauses = ["active = 1", "kind != 'training'"]
        params: list[Any] = []
        if project:
            clauses.append("project = ?")
            params.append(project)
        if self.min_confidence > 0:
            clauses.append("confidence >= ?")
            params.append(self.min_confidence)
        params.append(self.export_limit)
        rows = self.kernel.memory.conn.execute(
            f"SELECT * FROM memory_items WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", params
        ).fetchall()

        exported: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            try:
                tags = json.loads(data.get("tags_json") or "[]")
            except (TypeError, ValueError):
                tags = []
            title = str(data.get("title") or f"Memory {data['id']}")
            name = f"{data['id']:06d} {safe_filename(title, max_length=60)}"
            folder = "decisions" if data.get("kind") == "decision" else "memory"

            body_lines = [f"# {title}", ""]
            if data.get("project"):
                body_lines += [f"Project: {wikilink(safe_filename(str(data['project'])))}", ""]
            body_lines += [str(data.get("content") or ""), ""]
            if tags:
                body_lines += ["", " ".join(f"#{safe_filename(str(t), max_length=30).replace(' ', '-')}" for t in tags)]
            body_lines += [
                "",
                "---",
                "",
                "*Written by DaQauntum from its structured memory. Edits here are not read back "
                "as memory; DaQauntum's database remains authoritative.*",
            ]

            path = self.vault.write_note(
                folder,
                name,
                {
                    "memory_id": data["id"],
                    "kind": data.get("kind"),
                    "project": data.get("project"),
                    "confidence": round(float(data.get("confidence") or 0.0), 3),
                    "source": data.get("source"),
                    "created_at": data.get("created_at"),
                    "tags": tags,
                },
                "\n".join(body_lines),
            )
            exported.append({"id": data["id"], "title": title, "path": str(path), "note": name, "folder": folder})
        return exported

    def _export_knowledge(self, *, project: str | None = None) -> list[dict[str, Any]]:
        """Export graph nodes with their edges as wikilinks.

        Provenance is carried into the note, because a claim without its
        evidence link is exactly the kind of free-floating assertion the
        knowledge layer exists to prevent.
        """
        try:
            rows = self.kernel.memory.conn.execute(
                "SELECT * FROM knowledge_nodes WHERE active = 1 ORDER BY id DESC LIMIT ?",
                (self.export_limit,),
            ).fetchall()
        except Exception:
            return []

        exported: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            if project and data.get("project") and data["project"] != project:
                continue
            label = str(data.get("label") or f"Node {data['id']}")
            name = f"K{data['id']:05d} {safe_filename(label, max_length=60)}"
            body = [f"# {label}", ""]
            if data.get("summary"):
                body += [str(data["summary"]), ""]

            try:
                neighbours = self.kernel.knowledge.neighbors(int(data["id"]), limit=30)
            except Exception:
                neighbours = []
            if neighbours:
                body += ["## Linked", ""]
                for edge in neighbours:
                    other = edge.get("label") or edge.get("target_label") or "node"
                    relation = edge.get("relation", "related")
                    body.append(f"- `{relation}` → {wikilink(safe_filename(str(other), max_length=60))}")
                body.append("")

            try:
                provenance = self.kernel.knowledge.provenance(node_id=int(data["id"]), limit=20)
            except Exception:
                provenance = []
            if provenance:
                body += ["## Provenance", ""]
                for item in provenance:
                    body.append(f"- {item.get('kind', 'evidence')}: {item.get('detail') or item.get('note') or '—'}")
                body.append("")
            else:
                body += ["## Provenance", "", "*No explicit evidence recorded for this node.*", ""]

            path = self.vault.write_note(
                "knowledge",
                name,
                {
                    "node_id": data["id"],
                    "node_type": data.get("node_type"),
                    "project": data.get("project"),
                    "confidence": data.get("confidence"),
                },
                "\n".join(body),
            )
            exported.append({"id": data["id"], "label": label, "note": name, "path": str(path)})
        return exported

    def _export_projects(self) -> list[dict[str, Any]]:
        rows = self.kernel.memory.conn.execute(
            """
            SELECT project, COUNT(*) AS memories, MAX(updated_at) AS updated_at
            FROM memory_items
            WHERE active = 1 AND project IS NOT NULL AND TRIM(project) != ''
            GROUP BY project ORDER BY memories DESC LIMIT 100
            """
        ).fetchall()
        exported: list[dict[str, Any]] = []
        for row in rows:
            name = safe_filename(str(row["project"]))
            recent = self.kernel.memory.conn.execute(
                "SELECT id, title, kind FROM memory_items WHERE active = 1 AND project = ? "
                "ORDER BY id DESC LIMIT 40",
                (row["project"],),
            ).fetchall()
            body = [f"# {row['project']}", "", f"{row['memories']} memories. Last updated {row['updated_at']}.", "", "## Recent", ""]
            for item in recent:
                note = f"{item['id']:06d} {safe_filename(str(item['title'] or 'Memory'), max_length=60)}"
                label = str(item["title"] or f"Memory {item['id']}")
                body.append(f"- `{item['kind']}` {wikilink(note, label)}")
            path = self.vault.write_note(
                "projects", name,
                {"project": row["project"], "memories": int(row["memories"])},
                "\n".join(body),
            )
            exported.append({"project": row["project"], "memories": int(row["memories"]), "path": str(path)})
        return exported

    def _write_index(self, memories, knowledge, projects) -> Path:
        body = [
            "# DaQauntum",
            "",
            "This folder is written by DaQauntum from its own memory. It is a projection:",
            "the authoritative store is DaQauntum's database, and this can be regenerated",
            "at any time. Your own notes elsewhere in the vault are never modified.",
            "",
            f"Last export: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Contents",
            "",
            f"- **Memory** — {len(memories)} note(s) in `{EXPORT_FOLDERS['memory']}`",
            f"- **Decisions** — in `{EXPORT_FOLDERS['decisions']}`",
            f"- **Knowledge** — {len(knowledge)} node(s) in `{EXPORT_FOLDERS['knowledge']}`",
            f"- **Projects** — {len(projects)} in `{EXPORT_FOLDERS['projects']}`",
            "",
            "## Projects",
            "",
        ]
        for project in projects:
            body.append(f"- {wikilink(safe_filename(str(project['project'])))} — {project['memories']} memories")
        body += [
            "",
            "## How DaQauntum reads this vault",
            "",
            "Your own notes are indexed as **sources**: DaQauntum can retrieve and cite them.",
            "Retrieval is not proof, so a note becomes evidence you can see, not a fact",
            "DaQauntum asserts. Notes in this DaQauntum folder are skipped on import, so it",
            "never cites its own export back to you as independent corroboration.",
            "",
        ]
        return self.vault.write_note("index", "DaQauntum Home", {"kind": "index"}, "\n".join(body))

    # Import ------------------------------------------------------------------
    def import_vault(self, *, project: str | None = None) -> dict[str, Any]:
        """Index the user's own vault notes as provenance-tracked sources."""
        if not self.enabled:
            return {"ok": False, "reason": "obsidian-disabled"}
        if not self.vault.root.exists():
            return {"ok": False, "reason": "vault-not-found", "root": str(self.vault.root)}

        indexed = skipped_generated = unchanged = failed = 0
        errors: list[dict[str, str]] = []
        seen = 0

        for path, fields, _body in self.vault.iter_notes(include_generated=True, max_files=self.import_limit):
            seen += 1
            if fields.get("daqauntum_generated"):
                # DaQauntum's own export. Re-indexing it would let the system
                # cite itself as an independent source.
                skipped_generated += 1
                continue
            try:
                before = self.kernel.sources.stats().get("sources", 0)
                source = self.kernel.sources.ingest_file(
                    path,
                    project=project or str(fields.get("project") or "") or None,
                    title=path.stem,
                    allowed_root=self.vault.root,
                )
                after = self.kernel.sources.stats().get("sources", 0)
                if after > before:
                    indexed += 1
                else:
                    unchanged += 1
                _ = source
            except Exception as exc:
                failed += 1
                if len(errors) < 10:
                    errors.append({"note": path.name, "error": f"{type(exc).__name__}: {exc}"})

        result = {
            "ok": True,
            "root": str(self.vault.root),
            "scanned": seen,
            "indexed": indexed,
            "unchanged": unchanged,
            "skipped_generated": skipped_generated,
            "failed": failed,
            "errors": errors,
            "note": (
                "Vault notes are indexed as citable sources. Retrieval is not proof: "
                "nothing here is promoted to a durable fact without explicit evidence logic."
            ),
        }
        self.kernel.memory.add_event("obsidian_imported", {
            "indexed": indexed, "skipped_generated": skipped_generated, "failed": failed,
        })
        return result

    def sync(self, *, project: str | None = None) -> dict[str, Any]:
        """Export first, then import. Export before import is deliberate: it
        marks DaQauntum's own notes so the import step can skip them."""
        return {"export": self.export(project=project), "import": self.import_vault(project=project)}

    # Status ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        vault = self.vault.stats()
        return {
            "enabled": self.enabled,
            "vault": vault,
            "authoritative_store": "daqauntum_database",
            "export_is_projection": True,
            "generated_notes_reimported": False,
        }
