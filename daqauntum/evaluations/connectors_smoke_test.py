#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from connectors import DeviceBridgeThread
from core.kernel import DaQauntumKernel
from learning.manager import LearningTopic


def post_json(url: str, payload: dict, headers: dict | None = None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="dq-connectors-") as td:
        root = Path(td)
        project = root / "project"; project.mkdir()
        external = root / "external"; external.mkdir()
        inbox = root / "device_inbox"
        db = root / "dq.db"
        cfg = {
            "version": "0.4.0",
            "memory": {"db_path": str(db)},
            "sources": {"project_root": str(project), "allow_external_paths": False, "allow_url_fetch": False},
            "tools": {"project_root": str(project), "notes_dir": str(root / "notes")},
            "connectors": {"enabled": True, "device_inbox_dir": str(inbox), "max_files_per_sync": 50},
            "learning": {"enabled": True, "project_root": str(project), "reports_dir": "data/learning/reports", "outbox_dir": "data/learning/outbox", "auto_sync_connected_sources": True},
            "models": {"roles": {"planner": {"provider": "mock"}, "executor": {"provider": "mock"}, "critic": {"provider": "mock"}}},
        }
        cfg_path = root / "config.yaml"; cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        k = DaQauntumKernel(str(cfg_path))

        # Explicit folder grant works without enabling global external paths.
        doc = external / "research.md"
        doc.write_text("Quantum learning source alpha. Evidence about atomic memory.", encoding="utf-8")
        c = k.connectors.add_folder(external, name="External Research", project="Connector Test")
        first = k.connectors.sync(c["id"]).as_dict()
        assert first["indexed"] == 1 and first["errors"] == 0, first
        sources1 = k.sources.list_sources(limit=20)
        assert any(s["title"] == "research.md" for s in sources1)

        # Unchanged source deduplicates; a changed file creates a new source version.
        second = k.connectors.sync(c["id"]).as_dict()
        assert second["unchanged"] == 1, second
        doc.write_text("Quantum learning source beta. New evidence about atomic memory.", encoding="utf-8")
        third = k.connectors.sync(c["id"]).as_dict()
        assert third["indexed"] == 1, third
        versions = [s for s in k.sources.list_sources(limit=50) if s["title"] == "research.md"]
        assert len(versions) >= 2 and any(s.get("supersedes_source_id") for s in versions), versions

        # Network connector can be registered while network fetching remains opt-in/blocked.
        web = k.connectors.add_web_url("https://example.com/research", name="Example Research")
        try:
            k.connectors.sync(web["id"])
            raise AssertionError("web sync should be blocked while allow_url_fetch is false")
        except PermissionError:
            pass

        # Apify dataset connections store only a token-env name and snapshot structured results.
        apify = k.connectors.add_apify_dataset(
            "datasetABC123", name="Course Research Dataset", project="Connector Test", token_env="APIFY_TOKEN", limit=2
        )
        assert apify["config"]["token_env"] == "APIFY_TOKEN" and "token" not in apify["config"], apify
        try:
            k.connectors.sync(apify["id"])
            raise AssertionError("Apify sync should be blocked while allow_url_fetch is false")
        except PermissionError:
            pass
        k.sources.allow_url_fetch = True
        original_fetch_apify = k.connectors._fetch_apify_items
        k.connectors._fetch_apify_items = lambda dataset_id, limit, token_env: ([
            {"title": "Result A", "text": "apify structured evidence alpha"},
            {"title": "Result B", "text": "apify structured evidence beta"},
        ], f"https://api.apify.com/v2/datasets/{dataset_id}/items")
        try:
            apify_sync = k.connectors.sync(apify["id"]).as_dict()
            assert apify_sync["indexed"] == 1 and apify_sync["errors"] == 0, apify_sync
            apify_hits = k.sources.search("structured evidence alpha", limit=10)
            assert any("dataset-datasetABC123" in h["source"]["title"] for h in apify_hits), apify_hits
        finally:
            k.connectors._fetch_apify_items = original_fetch_apify
            k.sources.allow_url_fetch = False

        # Daily learning synchronizes a newly added file before reasoning.
        new_doc = external / "new-observation.md"
        new_doc.write_text("A newly connected observation for the morning learning ritual.", encoding="utf-8")
        topic = LearningTopic("research", "Connected evidence audit", "newly connected observation evidence", tuple())
        learned = k.learning.run_daily(topic=topic)
        assert learned.get("connector_sync") is not None, learned
        assert learned["connector_sync"].get("indexed", 0) >= 1, learned["connector_sync"]
        report = Path(learned["report_path"]).read_text(encoding="utf-8")
        assert "Connected-source sync" in report

        # Paired phone bridge accepts a file and indexes it immediately.
        bridge = DeviceBridgeThread(k.connectors, "127.0.0.1", 0, {"max_upload_bytes": 2_000_000, "pairing_ttl_seconds": 60, "session_ttl_seconds": 60}).start()
        try:
            base = f"http://127.0.0.1:{bridge.port}"
            paired = post_json(base + "/pair", {"code": bridge.pairing_code, "device": "Test Phone"})
            token = paired["token"]
            body = b"Phone-originated research note about molecular collaboration."
            req = urllib.request.Request(base + "/upload", data=body, method="POST", headers={
                "Authorization": "Bearer " + token,
                "X-Filename": "phone-research.md",
                "X-Project": "Connector%20Test",
                "X-Device": "Test%20Phone",
                "Content-Type": "application/octet-stream",
            })
            with urllib.request.urlopen(req, timeout=5) as r:
                uploaded = json.loads(r.read().decode("utf-8"))
            assert uploaded["ok"] and uploaded["source"]["title"].endswith("phone-research.md"), uploaded
            hits = k.sources.search("molecular collaboration", limit=10)
            assert any("phone-research" in h["source"]["title"] for h in hits), hits
        finally:
            bridge.stop()

        st = k.connectors.stats()
        assert st["connectors"] >= 4 and st["items"] >= 4, st
        print("DaQauntum v0.4.0 connected knowledge smoke test: PASS")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
