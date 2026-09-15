#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path


EXCLUDE_PARTS = {"__pycache__", ".venv", ".git"}
EXCLUDE_NAMES = {"RELEASE_MANIFEST.txt", "config.yaml", "daqauntum.db"}


def included(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part in EXCLUDE_PARTS for part in relative.parts):
        return False
    if path.name in EXCLUDE_NAMES or path.suffix == ".pyc":
        return False
    if relative.parts and relative.parts[0] == "data" and path.name != ".gitkeep":
        return False
    return path.is_file()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    files = sorted(path for path in root.rglob("*") if included(path, root))
    lines = [f"# DaQauntum v{version} SHA256 release manifest"]
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    (root / "RELEASE_MANIFEST.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Built release manifest for DaQauntum v{version}: {len(files)} files")


if __name__ == "__main__":
    main()
