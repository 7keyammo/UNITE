"""Regression guard for the Obsidian long-term memory bridge.

The property that matters most: DaQauntum must never re-index its own exported
notes as sources. If it did, an exported memory would come back as independent
evidence and DaQauntum would cite itself as corroboration for its own claim.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from obsidian.vault import Vault, is_generated, parse_frontmatter, safe_filename


def _kernel(root: Path, vault: Path) -> DaQauntumKernel:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.3-obsidian-test",
        "permission_level": 2,
        "models": {
            "roles": {r: {"provider": "mock"} for r in ("planner", "executor", "critic")},
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "fallback_to_mock": True,
        },
        "memory": {"db_path": str(root / "test.db")},
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "sources": {"project_root": str(root)},
        "learning": {"project_root": str(root)},
        "obsidian": {"vault_root": str(vault)},
    }
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(path))


def test_vault_writes_stay_inside_root() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Vault(Path(td) / "vault")
        vault.ensure()
        # Neither a memory title nor a note name may escape the vault.
        for hostile in ("../../etc/passwd", "a/b/c", "..", "con:/x"):
            target = vault.path_for("memory", hostile)
            assert vault.root in target.parents, f"{hostile} escaped to {target}"
        assert safe_filename("") == "note"
        assert "/" not in safe_filename("a/b") and ".." not in safe_filename("..")


def test_frontmatter_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        vault = Vault(Path(td))
        vault.ensure()
        path = vault.write_note("memory", "Sample", {"kind": "decision", "confidence": 0.9}, "Body")
        raw = path.read_text(encoding="utf-8")
        fields, body = parse_frontmatter(raw)
        assert is_generated(raw), "DaQauntum notes must be marked as generated"
        assert fields["kind"] == "decision" and "Body" in body
        # A user note with no frontmatter parses cleanly and is not generated.
        plain = "# My note\n\nSome content.\n"
        assert parse_frontmatter(plain) == ({}, plain)
        assert not is_generated(plain)


def test_export_import_roundtrip(kernel: DaQauntumKernel, vault_root: Path) -> None:
    for index in range(8):
        kernel.memory.conn.execute(
            "INSERT INTO memory_items(kind, title, content, project, tags_json, confidence, source, active) "
            "VALUES (?, ?, ?, 'quantum-lab', ?, 0.9, 'conversation', 1)",
            (["semantic", "decision", "project"][index % 3], f"Insight {index}",
             f"We learned that approach {index} works better under load.", json.dumps(["lab"])),
        )
    kernel.memory.conn.commit()

    # A note the user wrote themselves, already in the vault.
    vault_root.mkdir(parents=True, exist_ok=True)
    (vault_root / "My Research.md").write_text(
        "# My Research\n\nMeasured superconducting qubit coherence times of 40 microseconds.\n",
        encoding="utf-8",
    )

    exported = kernel.obsidian_memory.export()
    assert exported["ok"] and exported["memories"] == 8, exported
    assert (vault_root / "DaQauntum" / "DaQauntum Home.md").exists()
    assert list((vault_root / "DaQauntum" / "Memory").glob("*.md")), "no memory notes were written"
    assert list((vault_root / "DaQauntum" / "Decisions").glob("*.md")), "decisions were not separated"

    # Every note DaQauntum wrote carries the generated marker.
    for note in (vault_root / "DaQauntum").rglob("*.md"):
        assert is_generated(note.read_text(encoding="utf-8")), f"{note.name} is not marked generated"

    imported = kernel.obsidian_memory.import_vault()
    assert imported["ok"], imported
    assert imported["indexed"] == 1, f"expected only the user's note to be indexed: {imported}"
    assert imported["skipped_generated"] >= 8, imported
    assert imported["failed"] == 0, imported["errors"]

    # The core guard: nothing DaQauntum wrote may appear as a source.
    generated_names = {p.stem for p in (vault_root / "DaQauntum").rglob("*.md")}
    source_titles = {s["title"] for s in kernel.sources.list_sources(limit=200)}
    leaked = generated_names & source_titles
    assert not leaked, f"DaQauntum indexed its own export as sources: {sorted(leaked)[:5]}"
    assert source_titles == {"My Research"}, source_titles

    # Re-importing changes nothing.
    again = kernel.obsidian_memory.import_vault()
    assert again["indexed"] == 0 and again["unchanged"] == 1, again

    # The user's note is retrievable with a citation, as evidence rather than fact.
    hits = kernel.sources.search("qubit coherence microseconds", limit=3)
    assert hits, "the user's own note was not retrievable"
    assert hits[0].get("citation"), "a retrieved source arrived without provenance"

    # Exporting again after an import must not start indexing its own output.
    kernel.obsidian_memory.export()
    third = kernel.obsidian_memory.import_vault()
    assert third["indexed"] == 0, third
    assert {s["title"] for s in kernel.sources.list_sources(limit=200)} == {"My Research"}


def test_stats_state_the_trust_model(kernel: DaQauntumKernel) -> None:
    stats = kernel.obsidian_memory.stats()
    assert stats["authoritative_store"] == "daqauntum_database"
    assert stats["export_is_projection"] is True
    assert stats["generated_notes_reimported"] is False
    assert "obsidian" in kernel.status()


def main() -> None:
    test_vault_writes_stay_inside_root()
    test_frontmatter_roundtrip()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "run"
        vault_root = Path(td) / "MyVault"
        kernel = _kernel(root, vault_root)
        test_export_import_roundtrip(kernel, vault_root)
        test_stats_state_the_trust_model(kernel)
    print("DaQauntum Obsidian long-term memory smoke test: PASS")


if __name__ == "__main__":
    main()
