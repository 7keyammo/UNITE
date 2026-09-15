from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from knowledge.sources import SourceManager
from memory.store import MemoryStore


SUPPORTED_FILE_SUFFIXES = {
    ".txt", ".md", ".rst", ".pdf", ".ipynb", ".csv", ".tsv", ".json", ".jsonl",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php", ".sh", ".zsh", ".ps1", ".sql", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".log", ".tex", ".xlsx",
    ".xls", ".parquet",
}
DEFAULT_IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".cache", ".Trash"}


@dataclass
class ConnectorSyncResult:
    connector_id: int
    name: str
    kind: str
    scanned: int = 0
    indexed: int = 0
    unchanged: int = 0
    errors: int = 0
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class ConnectorManager:
    """Explicit, local-first connector catalog for DaQauntum.

    Connectors grant *scoped read access* to a folder or network source. They do not
    broaden SourceManager's global filesystem permissions. A connected folder is read
    only during sync and every indexed artifact still becomes a first-class Source
    Intelligence record with its own hash/provenance.
    """

    def __init__(self, store: MemoryStore, sources: SourceManager, config: dict[str, Any] | None = None):
        self.store = store
        self.sources = sources
        self.cfg = config or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.max_files_per_sync = max(1, int(self.cfg.get("max_files_per_sync", 500)))
        self.max_feed_items = max(1, int(self.cfg.get("max_feed_items", 20)))
        self.ignore_dirs = set(self.cfg.get("ignore_dirs", [])) | DEFAULT_IGNORE_DIRS
        self.inbox_dir = Path(self.cfg.get("device_inbox_dir", "data/device_inbox")).expanduser().resolve()
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.connected_data_dir = Path(self.cfg.get("connected_data_dir", "data/connected")).expanduser().resolve()
        self.connected_data_dir.mkdir(parents=True, exist_ok=True)
        self.conn = store.conn
        self._ensure_schema()
        self._ensure_device_connector()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connected_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                locator TEXT NOT NULL,
                project TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                learn_enabled INTEGER NOT NULL DEFAULT 1,
                recursive INTEGER NOT NULL DEFAULT 1,
                config_json TEXT NOT NULL DEFAULT '{}',
                last_sync_at DATETIME,
                last_status TEXT,
                last_error TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(kind, locator)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connector_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connector_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                external_key TEXT NOT NULL,
                observed_sha256 TEXT,
                first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(connector_id, source_id),
                FOREIGN KEY(connector_id) REFERENCES connected_sources(id),
                FOREIGN KEY(source_id) REFERENCES sources(id)
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_connected_sources_kind ON connected_sources(kind)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_connector_items_connector ON connector_items(connector_id)")
        self.conn.commit()

    def _ensure_device_connector(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM connected_sources WHERE kind='device_inbox' ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            return int(row[0])
        cur = self.conn.execute(
            """
            INSERT INTO connected_sources(name, kind, locator, project, enabled, learn_enabled, recursive, config_json)
            VALUES (?, 'device_inbox', ?, NULL, 1, 1, 1, '{}')
            """,
            ("Device Inbox", str(self.inbox_dir)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # Registration -------------------------------------------------------
    def add_folder(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        project: str | None = None,
        recursive: bool = True,
        learn_enabled: bool = True,
    ) -> dict[str, Any]:
        root = Path(path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Connected folder does not exist or is not a directory: {root}")
        return self._upsert_connector(
            name=name or root.name or str(root),
            kind="folder",
            locator=str(root),
            project=project,
            recursive=recursive,
            learn_enabled=learn_enabled,
            config={},
        )

    def add_web_url(self, url: str, *, name: str | None = None, project: str | None = None, learn_enabled: bool = True) -> dict[str, Any]:
        normalized = self.sources._normalize_url(url)
        return self._upsert_connector(
            name=name or normalized,
            kind="web_url",
            locator=normalized,
            project=project,
            recursive=False,
            learn_enabled=learn_enabled,
            config={},
        )

    def add_rss(self, url: str, *, name: str | None = None, project: str | None = None, learn_enabled: bool = True) -> dict[str, Any]:
        normalized = self.sources._normalize_url(url)
        return self._upsert_connector(
            name=name or f"Feed: {normalized}",
            kind="rss",
            locator=normalized,
            project=project,
            recursive=False,
            learn_enabled=learn_enabled,
            config={"max_items": self.max_feed_items},
        )

    def add_apify_dataset(
        self,
        dataset_id: str,
        *,
        name: str | None = None,
        project: str | None = None,
        learn_enabled: bool = True,
        token_env: str = "APIFY_TOKEN",
        limit: int = 100,
    ) -> dict[str, Any]:
        dataset_id = re.sub(r"[^A-Za-z0-9_-]", "", str(dataset_id).strip())
        if not dataset_id:
            raise ValueError("A valid Apify dataset ID is required")
        token_env = re.sub(r"[^A-Z0-9_]", "", str(token_env or "APIFY_TOKEN").upper()) or "APIFY_TOKEN"
        limit = max(1, min(int(limit), 1000))
        return self._upsert_connector(
            name=name or f"Apify dataset {dataset_id}",
            kind="apify_dataset",
            locator=dataset_id,
            project=project,
            recursive=False,
            learn_enabled=learn_enabled,
            config={"token_env": token_env, "limit": limit},
        )

    def _upsert_connector(self, *, name: str, kind: str, locator: str, project: str | None, recursive: bool, learn_enabled: bool, config: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Connected Sources are disabled")
        row = self.conn.execute("SELECT id FROM connected_sources WHERE kind=? AND locator=?", (kind, locator)).fetchone()
        if row:
            connector_id = int(row[0])
            self.conn.execute(
                """UPDATE connected_sources SET name=?, project=?, recursive=?, learn_enabled=?, enabled=1,
                   config_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                (name, project, 1 if recursive else 0, 1 if learn_enabled else 0, json.dumps(config), connector_id),
            )
        else:
            cur = self.conn.execute(
                """INSERT INTO connected_sources(name, kind, locator, project, enabled, learn_enabled, recursive, config_json)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (name, kind, locator, project, 1 if learn_enabled else 0, 1 if recursive else 0, json.dumps(config)),
            )
            connector_id = int(cur.lastrowid)
        self.conn.commit()
        self.store.add_event("connector_connected", {"connector_id": connector_id, "name": name, "kind": kind, "locator": locator, "learn_enabled": learn_enabled})
        return self.get(connector_id) or {"id": connector_id}

    def set_enabled(self, connector_id: int, enabled: bool) -> dict[str, Any]:
        self.conn.execute("UPDATE connected_sources SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if enabled else 0, int(connector_id)))
        self.conn.commit()
        item = self.get(connector_id)
        if not item:
            raise ValueError(f"Connector #{connector_id} not found")
        return item

    def set_learning(self, connector_id: int, enabled: bool) -> dict[str, Any]:
        self.conn.execute("UPDATE connected_sources SET learn_enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (1 if enabled else 0, int(connector_id)))
        self.conn.commit()
        item = self.get(connector_id)
        if not item:
            raise ValueError(f"Connector #{connector_id} not found")
        return item

    def remove(self, connector_id: int) -> None:
        row = self.get(connector_id)
        if not row:
            raise ValueError(f"Connector #{connector_id} not found")
        if row["kind"] == "device_inbox":
            raise ValueError("The Device Inbox connector is built in and cannot be removed; disable learning from it instead.")
        self.conn.execute("DELETE FROM connected_sources WHERE id=?", (int(connector_id),))
        self.conn.commit()
        self.store.add_event("connector_removed", {"connector_id": int(connector_id), "name": row["name"]})

    # Retrieval ----------------------------------------------------------
    def list(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM connected_sources"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY CASE kind WHEN 'device_inbox' THEN 0 ELSE 1 END, id"
        return [self._row(r) for r in self.conn.execute(sql).fetchall()]

    def get(self, connector_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM connected_sources WHERE id=?", (int(connector_id),)).fetchone()
        return self._row(row) if row else None

    def stats(self) -> dict[str, Any]:
        rows = self.list()
        return {
            "enabled": self.enabled,
            "connectors": len(rows),
            "active": sum(1 for r in rows if r["enabled"]),
            "learning_enabled": sum(1 for r in rows if r["enabled"] and r["learn_enabled"]),
            "items": int(self.conn.execute("SELECT COUNT(*) FROM connector_items").fetchone()[0]),
            "device_inbox": str(self.inbox_dir),
            "by_kind": {kind: sum(1 for r in rows if r["kind"] == kind) for kind in sorted({r["kind"] for r in rows})},
        }

    # Synchronization ----------------------------------------------------
    def sync_all(self, *, learning_only: bool = False) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for item in self.list(include_disabled=False):
            if learning_only and not item["learn_enabled"]:
                continue
            if item["kind"] == "device_inbox":
                # Uploads are indexed immediately; rescan only catches files copied into the inbox manually.
                pass
            try:
                results.append(self.sync(item["id"]).as_dict())
            except Exception as exc:
                self._mark_sync(item["id"], "error", str(exc))
                results.append(ConnectorSyncResult(item["id"], item["name"], item["kind"], errors=1, message=str(exc)).as_dict())
        return {
            "connectors": len(results),
            "scanned": sum(r["scanned"] for r in results),
            "indexed": sum(r["indexed"] for r in results),
            "unchanged": sum(r["unchanged"] for r in results),
            "errors": sum(r["errors"] for r in results),
            "results": results,
        }

    def sync(self, connector_id: int) -> ConnectorSyncResult:
        connector = self.get(connector_id)
        if not connector:
            raise ValueError(f"Connector #{connector_id} not found")
        if not connector["enabled"]:
            return ConnectorSyncResult(connector_id, connector["name"], connector["kind"], message="disabled")
        kind = connector["kind"]
        if kind in {"folder", "device_inbox"}:
            result = self._sync_folder(connector)
        elif kind == "web_url":
            result = self._sync_web_url(connector)
        elif kind == "rss":
            result = self._sync_rss(connector)
        elif kind == "apify_dataset":
            result = self._sync_apify_dataset(connector)
        else:
            raise ValueError(f"Unsupported connector kind: {kind}")
        self._mark_sync(connector_id, "ok" if not result.errors else "partial", result.message or None)
        self.store.add_event("connector_synced", result.as_dict())
        return result

    def _sync_folder(self, connector: dict[str, Any]) -> ConnectorSyncResult:
        root = Path(connector["locator"]).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Connected folder is unavailable: {root}")
        result = ConnectorSyncResult(connector["id"], connector["name"], connector["kind"])
        iterator: Iterable[Path] = root.rglob("*") if connector["recursive"] else root.glob("*")
        for path in iterator:
            if result.scanned >= self.max_files_per_sync:
                result.message = f"Stopped at max_files_per_sync={self.max_files_per_sync}"
                break
            try:
                rel_parts = path.relative_to(root).parts
            except ValueError:
                continue
            if any(part in self.ignore_dirs or part.startswith(".DS_Store") for part in rel_parts):
                continue
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_FILE_SUFFIXES:
                continue
            result.scanned += 1
            try:
                before = self._latest_source_for_locator(path)
                source = self.sources.ingest_file(
                    path,
                    project=connector.get("project"),
                    allowed_root=root,
                    connector_id=connector["id"],
                )
                self._record_item(connector["id"], source, str(path.relative_to(root)))
                if before and int(before["id"]) == int(source["id"]):
                    result.unchanged += 1
                else:
                    result.indexed += 1
            except Exception:
                result.errors += 1
        return result

    def _sync_web_url(self, connector: dict[str, Any]) -> ConnectorSyncResult:
        result = ConnectorSyncResult(connector["id"], connector["name"], connector["kind"], scanned=1)
        source = self.sources.register_url(
            connector["locator"],
            project=connector.get("project"),
            title=connector["name"],
            fetch=True,
            connector_id=connector["id"],
        )
        self._record_item(connector["id"], source, connector["locator"])
        result.indexed = 1
        return result

    def _sync_rss(self, connector: dict[str, Any]) -> ConnectorSyncResult:
        raw, mime, final_url, meta = self.sources.fetch_url_bytes(connector["locator"])
        result = ConnectorSyncResult(connector["id"], connector["name"], connector["kind"], scanned=1)
        links = self._feed_links(raw)
        max_items = int((connector.get("config") or {}).get("max_items", self.max_feed_items))
        for title, link in links[:max_items]:
            result.scanned += 1
            link = urllib.parse.urljoin(final_url, link)
            try:
                source = self.sources.register_url(
                    link,
                    project=connector.get("project"),
                    title=title or None,
                    fetch=True,
                    connector_id=connector["id"],
                )
                self._record_item(connector["id"], source, link)
                result.indexed += 1
            except Exception:
                result.errors += 1
        result.message = f"Feed {final_url}: {len(links[:max_items])} item(s) considered; mime={mime}; bytes={meta.get('bytes', len(raw))}"
        return result

    @staticmethod
    def _feed_links(raw: bytes) -> list[tuple[str, str]]:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ValueError(f"Feed XML could not be parsed: {exc}") from exc
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        # RSS 2.0 items
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if link and link not in seen:
                seen.add(link); found.append((title, link))
        # Atom entries, namespace agnostic.
        for entry in root.iter():
            if entry.tag.rsplit("}", 1)[-1] != "entry":
                continue
            title = ""
            link = ""
            for child in list(entry):
                local = child.tag.rsplit("}", 1)[-1]
                if local == "title" and not title:
                    title = "".join(child.itertext()).strip()
                elif local == "link" and not link:
                    href = child.attrib.get("href", "").strip()
                    rel = child.attrib.get("rel", "alternate")
                    if href and rel in {"alternate", ""}:
                        link = href
            if link and link not in seen:
                seen.add(link); found.append((title, link))
        return found

    def _sync_apify_dataset(self, connector: dict[str, Any]) -> ConnectorSyncResult:
        if not self.sources.allow_url_fetch:
            raise PermissionError("Network connector fetching is disabled. Set sources.allow_url_fetch: true to sync Apify datasets.")
        cfg = connector.get("config") or {}
        limit = max(1, min(int(cfg.get("limit", 100)), 1000))
        token_env = str(cfg.get("token_env") or "APIFY_TOKEN")
        items, endpoint = self._fetch_apify_items(connector["locator"], limit=limit, token_env=token_env)
        result = ConnectorSyncResult(connector["id"], connector["name"], connector["kind"], scanned=len(items))

        payload = json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")
        digest = __import__("hashlib").sha256(payload).hexdigest()
        out_dir = self.connected_data_dir / "apify" / str(connector["id"])
        out_dir.mkdir(parents=True, exist_ok=True)
        snapshot = out_dir / f"dataset-{connector['locator']}-{digest[:16]}.json"
        if not snapshot.exists():
            snapshot.write_bytes(payload)

        before = self._latest_source_for_locator(snapshot)
        source = self.sources.ingest_file(
            snapshot,
            project=connector.get("project"),
            allowed_root=out_dir,
            connector_id=connector["id"],
        )
        self._record_item(connector["id"], source, f"dataset:{connector['locator']}:{digest}")
        if before and int(before["id"]) == int(source["id"]):
            result.unchanged = 1
        else:
            result.indexed = 1
        result.message = f"Apify dataset {connector['locator']}: {len(items)} item(s) snapshotted from {endpoint}"
        return result

    def _fetch_apify_items(self, dataset_id: str, *, limit: int, token_env: str) -> tuple[list[dict[str, Any]], str]:
        endpoint = f"https://api.apify.com/v2/datasets/{urllib.parse.quote(dataset_id, safe='')}/items?format=json&clean=1&limit={int(limit)}"
        self.sources._assert_public_url(endpoint)
        headers = {
            "User-Agent": self.sources.user_agent,
            "Accept": "application/json",
        }
        token = os.environ.get(token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(endpoint, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec - fixed HTTPS host + public URL guard
            raw = response.read(self.sources.max_url_bytes + 1)
        if len(raw) > self.sources.max_url_bytes:
            raise ValueError(f"Apify dataset response exceeds {self.sources.max_url_bytes} byte limit")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            raise ValueError("Apify dataset items response was not a JSON array")
        return [item if isinstance(item, dict) else {"value": item} for item in data], endpoint

    # Device inbox -------------------------------------------------------
    def ingest_device_file(self, path: Path, *, project: str | None = None, device_name: str | None = None) -> dict[str, Any]:
        connector = next((c for c in self.list() if c["kind"] == "device_inbox"), None)
        if not connector:
            raise RuntimeError("Device Inbox connector is unavailable")
        source = self.sources.ingest_file(
            path,
            project=project or connector.get("project"),
            allowed_root=self.inbox_dir,
            connector_id=connector["id"],
        )
        self._record_item(connector["id"], source, path.name)
        self.store.add_event("device_file_ingested", {"source_id": source["id"], "filename": path.name, "device": device_name or "paired device"})
        return source

    # Helpers ------------------------------------------------------------
    def _latest_source_for_locator(self, path: Path) -> dict[str, Any] | None:
        locator = self.sources._display_locator(path.resolve())
        return self.sources._latest_for_locator(locator)

    def _record_item(self, connector_id: int, source: dict[str, Any], external_key: str) -> None:
        sha = source.get("content_sha256")
        self.conn.execute(
            """
            INSERT INTO connector_items(connector_id, source_id, external_key, observed_sha256)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(connector_id, source_id) DO UPDATE SET
                external_key=excluded.external_key,
                observed_sha256=excluded.observed_sha256,
                last_seen_at=CURRENT_TIMESTAMP
            """,
            (int(connector_id), int(source["id"]), external_key, sha),
        )
        self.conn.commit()

    def _mark_sync(self, connector_id: int, status: str, error: str | None) -> None:
        self.conn.execute(
            """UPDATE connected_sources SET last_sync_at=CURRENT_TIMESTAMP, last_status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
            (status, error, int(connector_id)),
        )
        self.conn.commit()

    @staticmethod
    def _row(row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["learn_enabled"] = bool(item.get("learn_enabled"))
        item["recursive"] = bool(item.get("recursive"))
        try:
            item["config"] = json.loads(item.pop("config_json", "{}") or "{}")
        except Exception:
            item["config"] = {}
        return item
