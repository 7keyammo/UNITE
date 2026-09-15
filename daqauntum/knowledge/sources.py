from __future__ import annotations

import csv
import hashlib
import html
import ipaddress
import json
import mimetypes
import re
import socket
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

from memory.store import MemoryStore


_TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp",
    ".h", ".hpp", ".go", ".rs", ".rb", ".php", ".sh", ".zsh", ".fish", ".ps1", ".sql",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".csv",
    ".tsv", ".log", ".tex",
}
_DATASET_EXTENSIONS = {".csv", ".tsv", ".json", ".jsonl", ".parquet", ".xlsx", ".xls"}
_CODE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb", ".php", ".sh", ".sql"}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip_depth += 1
        elif tag.lower() in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        elif tag.lower() in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        value = html.unescape("".join(self.parts))
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
        return value.strip()


@dataclass
class ExtractedSection:
    text: str
    metadata: dict[str, Any]


class SourceManager:
    """Local-first source catalog and chunk index for DaQauntum v0.2.7.

    Source retrieval is evidence discovery, not evidence authority. Ingestion creates a
    source record and a graph source node, but never asserts that the source supports a
    claim until an explicit evidence link is created.
    """

    def __init__(self, store: MemoryStore, graph=None, config: dict[str, Any] | None = None):
        self.store = store
        self.graph = graph
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.project_root = Path(cfg.get("project_root", ".")).resolve()
        self.max_file_bytes = int(cfg.get("max_file_bytes", 20_000_000))
        self.chunk_chars = max(400, int(cfg.get("chunk_chars", 1800)))
        self.chunk_overlap = max(0, min(int(cfg.get("chunk_overlap", 200)), self.chunk_chars // 2))
        self.context_limit = max(1, int(cfg.get("context_limit", 4)))
        self.allow_external_paths = bool(cfg.get("allow_external_paths", False))
        self.allow_url_fetch = bool(cfg.get("allow_url_fetch", False))
        self.allow_private_networks = bool(cfg.get("allow_private_networks", False))
        self.max_url_bytes = int(cfg.get("max_url_bytes", 5_000_000))
        self.user_agent = str(cfg.get("user_agent", "DaQauntum/0.4.0 FRAME Workspaces + Agent Studio"))
        self.conn = store.conn
        self._ensure_schema()
        if self.enabled and self.graph is not None:
            self.backfill_graph()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                locator TEXT NOT NULL,
                title TEXT NOT NULL,
                mime_type TEXT,
                content_sha256 TEXT,
                locator_sha256 TEXT NOT NULL,
                project TEXT,
                status TEXT NOT NULL DEFAULT 'registered',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                graph_node_id INTEGER,
                supersedes_source_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(graph_node_id) REFERENCES knowledge_nodes(id),
                FOREIGN KEY(supersedes_source_id) REFERENCES sources(id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                char_start INTEGER,
                char_end INTEGER,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_id, ordinal),
                FOREIGN KEY(source_id) REFERENCES sources(id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_locator ON sources(locator)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sources_type ON sources(source_type)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_source_chunks_source ON source_chunks(source_id)")
        self.conn.commit()

    # Public ingestion -----------------------------------------------------
    def ingest_file(
        self,
        path: str | Path,
        *,
        project: str | None = None,
        title: str | None = None,
        allowed_root: str | Path | None = None,
        connector_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Source Intelligence is disabled")
        resolved = self._resolve_file(path, allowed_root=allowed_root)
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Source file not found: {path}")
        size = resolved.stat().st_size
        if size > self.max_file_bytes:
            raise ValueError(f"Source file is too large ({size} bytes > {self.max_file_bytes})")

        raw = resolved.read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        locator = self._display_locator(resolved)
        mime_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
        source_type = self._classify_file(resolved)
        existing = self._find_exact(locator, content_hash)
        if existing:
            return existing

        previous = self._latest_for_locator(locator)
        sections, extraction_meta = self._extract_file(resolved, raw)
        source_id = self._insert_source(
            source_type=source_type,
            locator=locator,
            title=title or resolved.name,
            mime_type=mime_type,
            content_sha256=content_hash,
            project=project,
            status="indexed" if sections else "registered",
            metadata={"bytes": size, "extension": resolved.suffix.lower(), "connector_id": connector_id, **extraction_meta},
            supersedes_source_id=previous["id"] if previous and previous.get("content_sha256") != content_hash else None,
        )
        self._replace_chunks(source_id, sections)
        self._sync_graph(source_id)
        return self.get_source(source_id) or {"id": source_id}

    def register_url(
        self,
        url: str,
        *,
        project: str | None = None,
        title: str | None = None,
        fetch: bool | None = None,
        connector_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Source Intelligence is disabled")
        normalized = self._normalize_url(url)
        should_fetch = self.allow_url_fetch if fetch is None else bool(fetch)
        content: bytes | None = None
        mime_type = "text/html"
        final_url = normalized
        metadata: dict[str, Any] = {"fetched": False, "connector_id": connector_id}
        sections: list[ExtractedSection] = []

        if should_fetch:
            if not self.allow_url_fetch:
                raise PermissionError("URL fetching is disabled. Set sources.allow_url_fetch: true to enable it.")
            content, mime_type, final_url, fetch_meta = self._fetch_url(normalized)
            metadata.update(fetch_meta)
            metadata["fetched"] = True
            sections = self._extract_url_content(content, mime_type)

        content_hash = hashlib.sha256(content).hexdigest() if content is not None else None
        existing = self._find_exact(final_url, content_hash, allow_null_hash=content is None)
        if existing:
            return existing
        previous = self._latest_for_locator(final_url)
        source_id = self._insert_source(
            source_type="url",
            locator=final_url,
            title=title or self._title_from_url(final_url),
            mime_type=mime_type,
            content_sha256=content_hash,
            project=project,
            status="indexed" if sections else "registered",
            metadata=metadata,
            supersedes_source_id=(previous["id"] if previous and previous.get("content_sha256") != content_hash else None),
        )
        self._replace_chunks(source_id, sections)
        self._sync_graph(source_id)
        return self.get_source(source_id) or {"id": source_id}

    def fetch_url_bytes(self, url: str) -> tuple[bytes, str, str, dict[str, Any]]:
        """Fetch a public URL through the same SSRF and size guards used by source ingestion.

        This method is intentionally gated by ``allow_url_fetch``; connectors may call it
        after the user explicitly enables network source fetching.
        """
        if not self.allow_url_fetch:
            raise PermissionError("URL fetching is disabled. Set sources.allow_url_fetch: true to enable it.")
        return self._fetch_url(self._normalize_url(url))

    # Retrieval ------------------------------------------------------------
    def list_sources(self, *, limit: int = 50, project: str | None = None) -> list[dict[str, Any]]:
        clauses = ["active = 1"]
        params: list[Any] = []
        if project:
            clauses.append("(project = ? OR project IS NULL)")
            params.append(project)
        params.append(max(1, min(int(limit), 200)))
        rows = self.conn.execute(
            "SELECT * FROM sources WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", params
        ).fetchall()
        return [self._source_row(row) for row in rows]

    def get_source(self, source_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (int(source_id),)).fetchone()
        if not row:
            return None
        item = self._source_row(row)
        item["chunk_count"] = int(self.conn.execute(
            "SELECT COUNT(*) FROM source_chunks WHERE source_id = ? AND active = 1", (int(source_id),)
        ).fetchone()[0])
        return item

    def get_chunk(self, chunk_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT c.*, s.title AS source_title, s.locator AS source_locator, s.source_type AS source_type,
                   s.project AS source_project
            FROM source_chunks c JOIN sources s ON s.id = c.source_id
            WHERE c.id = ? AND c.active = 1 AND s.active = 1
            """,
            (int(chunk_id),),
        ).fetchone()
        return self._chunk_row(row) if row else None

    def chunks_for_source(self, source_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM source_chunks WHERE source_id = ? AND active = 1 ORDER BY ordinal LIMIT ?",
            (int(source_id), max(1, min(int(limit), 500))),
        ).fetchall()
        return [self._chunk_row(row) for row in rows]

    def search(self, query: str, *, limit: int = 10, project: str | None = None) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit), 50))
        if not query:
            return [
                {"source": source, "chunk": None, "score": 0.0, "citation": f"[source:{source['id']}]"}
                for source in self.list_sources(limit=limit, project=project)
            ]

        terms = self._terms(query)
        source_rows = self.list_sources(limit=200, project=project)
        source_map = {item["id"]: item for item in source_rows}
        if not source_map:
            return []
        ids = list(source_map)
        placeholders = ",".join("?" for _ in ids)
        chunk_rows = self.conn.execute(
            f"SELECT * FROM source_chunks WHERE active = 1 AND source_id IN ({placeholders}) ORDER BY id DESC LIMIT 5000",
            ids,
        ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in chunk_rows:
            chunk = self._chunk_row(row)
            source = source_map.get(chunk["source_id"])
            if not source:
                continue
            haystack = f"{source['title']} {source['locator']} {chunk['content']} {json.dumps(chunk.get('metadata', {}), ensure_ascii=False)}".lower()
            overlaps = sum(1 for term in terms if term in haystack)
            if overlaps <= 0:
                continue
            phrase_bonus = 1.5 if query.lower() in haystack else 0.0
            title_bonus = sum(0.6 for term in terms if term in source["title"].lower())
            project_bonus = 0.8 if project and source.get("project") == project else 0.0
            score = overlaps + phrase_bonus + title_bonus + project_bonus
            scored.append((score, {
                "source": source,
                "chunk": chunk,
                "score": round(score, 4),
                "citation": f"[source:{source['id']} chunk:{chunk['id']}]",
            }))
        # If the source title/locator matched but there are no chunks, still return it.
        for source in source_rows:
            haystack = f"{source['title']} {source['locator']}".lower()
            overlaps = sum(1 for term in terms if term in haystack)
            if overlaps:
                scored.append((overlaps * 0.75, {
                    "source": source,
                    "chunk": None,
                    "score": round(overlaps * 0.75, 4),
                    "citation": f"[source:{source['id']}]",
                }))
        scored.sort(key=lambda pair: (pair[0], pair[1]["source"]["id"]), reverse=True)
        seen: set[tuple[int, int | None]] = set()
        results: list[dict[str, Any]] = []
        for _, item in scored:
            key = (item["source"]["id"], item["chunk"]["id"] if item["chunk"] else None)
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
            if len(results) >= limit:
                break
        return results

    def context_messages(self, query: str, *, project: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        results = self.search(query, limit=limit or self.context_limit, project=project)
        messages: list[dict[str, Any]] = []
        for item in results:
            source = item["source"]
            chunk = item.get("chunk")
            if chunk:
                excerpt = self._compact(chunk["content"], 1400)
                content = (
                    f"{item['citation']} {source['title']} | locator={source['locator']} | "
                    f"sha256={source.get('content_sha256') or 'unfetched'} | excerpt={excerpt}"
                )
            else:
                content = f"{item['citation']} {source['title']} | locator={source['locator']} | registered source"
            messages.append({"role": "source", "content": content, "source_id": source["id"], "chunk_id": chunk["id"] if chunk else None})
        return messages

    # Evidence -------------------------------------------------------------
    def link_evidence(
        self,
        source_id: int,
        relation: str,
        target_node_id: int,
        *,
        chunk_id: int | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if self.graph is None:
            raise RuntimeError("Knowledge graph is not configured")
        relation = re.sub(r"[^a-z0-9_]+", "_", relation.strip().lower()).strip("_")
        if relation not in {"supports", "contradicts", "context_for"}:
            raise ValueError("Evidence relation must be supports, contradicts, or context_for")
        source = self.get_source(source_id)
        target = self.graph.get_node(int(target_node_id))
        if not source or not source.get("active"):
            raise ValueError(f"Source #{source_id} not found")
        if not target or not target.get("active"):
            raise ValueError(f"Knowledge node #{target_node_id} not found")
        if chunk_id is not None:
            chunk = self.get_chunk(chunk_id)
            if not chunk or chunk["source_id"] != int(source_id):
                raise ValueError(f"Chunk #{chunk_id} does not belong to source #{source_id}")
        source_node = source.get("graph_node_id") or self._sync_graph(source_id)
        if isinstance(source_node, dict):
            source_node = source_node["node_id"]
        edge_id = self.graph.add_edge(int(source_node), int(target_node_id), relation, confidence=1.0)
        source_ref = f"source:{source_id}"
        if chunk_id is not None:
            source_ref += f"#chunk:{chunk_id}"
        self.graph.add_provenance(
            edge_id=edge_id,
            source_type="source_evidence",
            source_ref=source_ref,
            confidence=1.0,
            note=note or "Explicit source evidence link",
        )
        return {
            "edge_id": edge_id,
            "source_id": int(source_id),
            "source_node_id": int(source_node),
            "target_node_id": int(target_node_id),
            "relation": relation,
            "chunk_id": chunk_id,
            "citation": f"[source:{source_id}" + (f" chunk:{chunk_id}]" if chunk_id else "]"),
        }

    def backfill_graph(self) -> dict[str, int]:
        if self.graph is None:
            return {"sources_scanned": 0, "source_nodes_added": 0}
        sources = self.list_sources(limit=100000)
        added = 0
        for source in reversed(sources):
            before = source.get("graph_node_id")
            result = self._sync_graph(source["id"])
            if not before and result:
                added += 1
        return {"sources_scanned": len(sources), "source_nodes_added": added}

    def stats(self) -> dict[str, Any]:
        by_type = {
            str(row["source_type"]): int(row["count"])
            for row in self.conn.execute(
                "SELECT source_type, COUNT(*) AS count FROM sources WHERE active = 1 GROUP BY source_type"
            ).fetchall()
        }
        return {
            "enabled": self.enabled,
            "sources": int(self.conn.execute("SELECT COUNT(*) FROM sources WHERE active = 1").fetchone()[0]),
            "chunks": int(self.conn.execute("SELECT COUNT(*) FROM source_chunks WHERE active = 1").fetchone()[0]),
            "indexed_sources": int(self.conn.execute("SELECT COUNT(*) FROM sources WHERE active = 1 AND status = 'indexed'").fetchone()[0]),
            "registered_sources": int(self.conn.execute("SELECT COUNT(*) FROM sources WHERE active = 1 AND status = 'registered'").fetchone()[0]),
            "by_type": by_type,
            "url_fetch_enabled": self.allow_url_fetch,
        }

    # Internals ------------------------------------------------------------
    def _insert_source(
        self,
        *,
        source_type: str,
        locator: str,
        title: str,
        mime_type: str | None,
        content_sha256: str | None,
        project: str | None,
        status: str,
        metadata: dict[str, Any],
        supersedes_source_id: int | None,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO sources(source_type, locator, title, mime_type, content_sha256, locator_sha256,
                                project, status, metadata_json, supersedes_source_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_type, locator, self._compact(title, 500), mime_type, content_sha256,
                hashlib.sha256(locator.encode("utf-8")).hexdigest(), project, status,
                json.dumps(metadata or {}, ensure_ascii=False), supersedes_source_id,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def _replace_chunks(self, source_id: int, sections: list[ExtractedSection]) -> None:
        self.conn.execute("DELETE FROM source_chunks WHERE source_id = ?", (int(source_id),))
        ordinal = 0
        global_offset = 0
        for section in sections:
            for text, start, end in self._chunk_text(section.text):
                ordinal += 1
                meta = dict(section.metadata)
                self.conn.execute(
                    """
                    INSERT INTO source_chunks(source_id, ordinal, content, content_sha256, char_start, char_end, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(source_id), ordinal, text, hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        global_offset + start, global_offset + end, json.dumps(meta, ensure_ascii=False),
                    ),
                )
            global_offset += len(section.text) + 1
        self.conn.commit()

    def _sync_graph(self, source_id: int) -> int | dict[str, Any] | None:
        if self.graph is None:
            return None
        source = self.get_source(source_id)
        if not source:
            return None
        node_id = self.graph.add_node(
            "source",
            source["title"],
            project=source.get("project"),
            canonical_key=f"source-record:{source_id}",
            metadata={
                "source_id": source_id,
                "source_type": source["source_type"],
                "locator": source["locator"],
                "mime_type": source.get("mime_type"),
                "content_sha256": source.get("content_sha256"),
                "status": source.get("status"),
            },
        )
        self.graph.add_provenance(
            node_id=node_id,
            source_type="source_record",
            source_ref=f"source:{source_id}",
            confidence=1.0,
            note="First-class Source Intelligence record",
        )
        if source.get("project"):
            project_id = self.graph.add_node("project", source["project"], canonical_key=self.graph._canonical(source["project"]))
            edge_id = self.graph.add_edge(project_id, node_id, "contains", confidence=1.0)
            self.graph.add_provenance(
                edge_id=edge_id,
                source_type="source_record",
                source_ref=f"source:{source_id}",
                confidence=1.0,
                note="Source belongs to active project",
            )
        if source.get("supersedes_source_id"):
            previous = self.get_source(int(source["supersedes_source_id"]))
            if previous:
                previous_node = previous.get("graph_node_id") or self._sync_graph(previous["id"])
                if isinstance(previous_node, dict):
                    previous_node = previous_node.get("node_id")
                if previous_node:
                    edge_id = self.graph.add_edge(node_id, int(previous_node), "supersedes", confidence=1.0)
                    self.graph.add_provenance(
                        edge_id=edge_id,
                        source_type="source_version",
                        source_ref=f"source:{source_id}",
                        confidence=1.0,
                        note=f"Same locator as source:{previous['id']} with changed content hash",
                    )
        self.conn.execute("UPDATE sources SET graph_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (node_id, int(source_id)))
        self.conn.commit()
        return node_id

    def _extract_file(self, path: Path, raw: bytes) -> tuple[list[ExtractedSection], dict[str, Any]]:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._extract_pdf(path)
        if suffix == ".ipynb":
            return self._extract_notebook(raw)
        if suffix in {".html", ".htm"}:
            text = raw.decode("utf-8", errors="replace")
            parser = _HTMLTextExtractor()
            parser.feed(text)
            return [ExtractedSection(parser.text(), {"format": "html"})], {"extractor": "html.parser"}
        if suffix in _TEXT_EXTENSIONS or suffix in {".jsonl"}:
            text = raw.decode("utf-8", errors="replace")
            return [ExtractedSection(text, {"format": suffix.lstrip(".") or "text"})], {"extractor": "utf-8"}
        return [], {"extractor": "metadata-only", "warning": "Unsupported binary type; source registered without text chunks"}

    def _extract_pdf(self, path: Path) -> tuple[list[ExtractedSection], dict[str, Any]]:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            return [], {"extractor": "metadata-only", "warning": "Install pypdf to index PDF text"}
        reader = PdfReader(str(path))
        sections: list[ExtractedSection] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                sections.append(ExtractedSection(text, {"page": index, "format": "pdf"}))
        return sections, {"extractor": "pypdf", "pages": len(reader.pages)}

    def _extract_notebook(self, raw: bytes) -> tuple[list[ExtractedSection], dict[str, Any]]:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        sections: list[ExtractedSection] = []
        cells = data.get("cells", []) if isinstance(data, dict) else []
        for index, cell in enumerate(cells):
            if not isinstance(cell, dict):
                continue
            source = cell.get("source", [])
            text = "".join(source) if isinstance(source, list) else str(source)
            if not text.strip():
                continue
            sections.append(ExtractedSection(text, {"cell_index": index, "cell_type": cell.get("cell_type", "unknown"), "format": "ipynb"}))
        return sections, {"extractor": "notebook-json", "cells": len(cells)}

    def _extract_url_content(self, raw: bytes, mime_type: str) -> list[ExtractedSection]:
        if "pdf" in mime_type.lower():
            try:
                from pypdf import PdfReader  # type: ignore
                reader = PdfReader(BytesIO(raw))
                sections: list[ExtractedSection] = []
                for index, page in enumerate(reader.pages, start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        sections.append(ExtractedSection(text, {"page": index, "format": "pdf"}))
                return sections
            except Exception:
                return []
        text = raw.decode("utf-8", errors="replace")
        if "html" in mime_type.lower():
            parser = _HTMLTextExtractor()
            parser.feed(text)
            text = parser.text()
        return [ExtractedSection(text, {"format": mime_type})] if text.strip() else []

    def _fetch_url(self, url: str) -> tuple[bytes, str, str, dict[str, Any]]:
        self._assert_public_url(url)
        manager = self

        class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
                manager._assert_public_url(newurl)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "text/html,text/plain,application/json,application/pdf,*/*;q=0.1"})
        opener = urllib.request.build_opener(GuardedRedirectHandler())
        with opener.open(request, timeout=20) as response:  # nosec - guarded URL, guarded redirects, size cap
            final_url = response.geturl()
            self._assert_public_url(final_url)
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > self.max_url_bytes:
                raise ValueError(f"URL response too large ({content_length} bytes > {self.max_url_bytes})")
            data = response.read(self.max_url_bytes + 1)
            if len(data) > self.max_url_bytes:
                raise ValueError(f"URL response exceeds {self.max_url_bytes} byte limit")
            mime = response.headers.get_content_type() or "application/octet-stream"
            return data, mime, final_url, {"http_status": getattr(response, "status", None), "bytes": len(data)}

    def _assert_public_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Only http:// and https:// URLs are supported")
        host = parsed.hostname
        if not host:
            raise ValueError("URL is missing a hostname")
        if self.allow_private_networks:
            return
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"Could not resolve URL host: {host}") from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise ValueError("URL fetch to private/local network addresses is blocked")

    def _resolve_file(self, path: str | Path, *, allowed_root: str | Path | None = None) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        candidate = candidate.resolve()
        if allowed_root is not None:
            scoped = Path(allowed_root).expanduser().resolve()
            if candidate != scoped and scoped not in candidate.parents:
                raise ValueError("Source path escapes the explicitly connected folder")
            return candidate
        if not self.allow_external_paths and candidate != self.project_root and self.project_root not in candidate.parents:
            raise ValueError("Source path escapes sources.project_root; connect the folder explicitly or enable allow_external_paths")
        return candidate

    def _display_locator(self, path: Path) -> str:
        try:
            return path.relative_to(self.project_root).as_posix()
        except ValueError:
            return str(path)

    def _find_exact(self, locator: str, content_hash: str | None, *, allow_null_hash: bool = False) -> dict[str, Any] | None:
        if content_hash is None and allow_null_hash:
            row = self.conn.execute(
                "SELECT * FROM sources WHERE locator = ? AND content_sha256 IS NULL AND active = 1 ORDER BY id DESC LIMIT 1",
                (locator,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM sources WHERE locator = ? AND content_sha256 = ? AND active = 1 ORDER BY id DESC LIMIT 1",
                (locator, content_hash),
            ).fetchone()
        return self._source_row(row) if row else None

    def _latest_for_locator(self, locator: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM sources WHERE locator = ? AND active = 1 ORDER BY id DESC LIMIT 1", (locator,)
        ).fetchone()
        return self._source_row(row) if row else None

    def _chunk_text(self, text: str) -> Iterable[tuple[str, int, int]]:
        text = text.strip()
        if not text:
            return []
        chunks: list[tuple[str, int, int]] = []
        start = 0
        n = len(text)
        while start < n:
            hard_end = min(n, start + self.chunk_chars)
            end = hard_end
            if hard_end < n:
                boundary = max(text.rfind("\n\n", start + self.chunk_chars // 2, hard_end), text.rfind(". ", start + self.chunk_chars // 2, hard_end))
                if boundary > start:
                    end = boundary + (1 if text[boundary:boundary+2] == ". " else 0)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append((chunk, start, end))
            if end >= n:
                break
            start = max(end - self.chunk_overlap, start + 1)
        return chunks

    @staticmethod
    def _classify_file(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return "paper"
        if suffix == ".ipynb":
            return "notebook"
        if suffix in _DATASET_EXTENSIONS:
            return "dataset"
        if suffix in _CODE_EXTENSIONS:
            return "code"
        return "file"

    @staticmethod
    def _normalize_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("A complete http:// or https:// URL is required")
        parsed = parsed._replace(fragment="")
        return urllib.parse.urlunparse(parsed)

    @staticmethod
    def _title_from_url(url: str) -> str:
        parsed = urllib.parse.urlparse(url)
        tail = Path(parsed.path).name
        return tail or parsed.hostname or url

    @staticmethod
    def _terms(query: str) -> list[str]:
        return [term for term in re.findall(r"[a-z0-9][a-z0-9_-]{1,}", query.lower()) if len(term) >= 2][:24]

    @staticmethod
    def _compact(value: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", str(value)).strip()
        return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."

    @staticmethod
    def _source_row(row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        data["active"] = bool(data.get("active", 0))
        return data

    @staticmethod
    def _chunk_row(row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["metadata"] = json.loads(data.pop("metadata_json", "{}") or "{}")
        except json.JSONDecodeError:
            data["metadata"] = {}
        data["active"] = bool(data.get("active", 0))
        return data
