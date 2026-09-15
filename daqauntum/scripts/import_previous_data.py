#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import yaml
from datetime import datetime
from pathlib import Path


def backup_if_exists(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(path.name + f".backup-{stamp}")
    if path.is_dir():
        shutil.copytree(path, backup)
    else:
        shutil.copy2(path, backup)
    print(f"Backed up {path} -> {backup}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import user data from a previous DaQauntum release into this full release."
    )
    parser.add_argument("previous", help="Path to previous DaQauntum release directory")
    args = parser.parse_args()

    current = Path(__file__).resolve().parents[1]
    previous = Path(args.previous).expanduser().resolve()
    if not previous.exists() or not previous.is_dir():
        raise SystemExit(f"Previous release directory not found: {previous}")
    if previous == current:
        raise SystemExit("Previous release path cannot be the current release directory.")

    current_data = current / "data"
    current_data.mkdir(parents=True, exist_ok=True)

    old_db = previous / "data" / "daqauntum.db"
    new_db = current_data / "daqauntum.db"
    if old_db.exists():
        backup_if_exists(new_db)
        shutil.copy2(old_db, new_db)
        print(f"Imported database: {old_db}")
    else:
        print("No previous data/daqauntum.db found; skipped database import.")

    old_notes = previous / "data" / "notes"
    new_notes = current_data / "notes"
    if old_notes.exists() and old_notes.is_dir():
        new_notes.mkdir(parents=True, exist_ok=True)
        for item in old_notes.iterdir():
            target = new_notes / item.name
            if item.is_file():
                shutil.copy2(item, target)
        print(f"Imported notes from: {old_notes}")

    old_calls = previous / "data" / "calls"
    new_calls = current_data / "calls"
    if old_calls.exists() and old_calls.is_dir():
        new_calls.mkdir(parents=True, exist_ok=True)
        for item in old_calls.iterdir():
            target = new_calls / item.name
            if item.is_file():
                shutil.copy2(item, target)
        print(f"Imported call artifacts from: {old_calls}")

    for dirname in ("learning", "native_model", "device_inbox", "connected", "workspaces", "obsidian"):
        old_dir = previous / "data" / dirname
        new_dir = current_data / dirname
        if old_dir.exists() and old_dir.is_dir():
            new_dir.mkdir(parents=True, exist_ok=True)
            for item in old_dir.rglob("*"):
                if item.is_file():
                    target = new_dir / item.relative_to(old_dir)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
            print(f"Imported {dirname} artifacts from: {old_dir}")

    old_config = previous / "config.yaml"
    new_config = current / "config.yaml"
    if old_config.exists():
        backup_if_exists(new_config)
        shutil.copy2(old_config, new_config)
        # Preserve user settings but keep the running release identity current.
        try:
            cfg = yaml.safe_load(new_config.read_text(encoding="utf-8")) or {}
            cfg["version"] = (current / "VERSION").read_text(encoding="utf-8").strip()
            new_config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        except Exception as exc:
            print(f"Warning: could not refresh config version: {exc}")
        print(f"Imported config: {old_config}")
    else:
        print("No previous config.yaml found; current defaults/config example remain available.")

    print("Import complete. Run: PYTHONPATH=. python evaluations/smoke_test.py")


if __name__ == "__main__":
    main()
