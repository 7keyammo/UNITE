from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Notes DaQauntum writes carry this marker in their frontmatter. The importer
# skips them, which is the whole point: if DaQauntum re-indexed its own exports
# as sources, it would end up citing itself as independent evidence and
# manufacturing corroboration for its own claims.
GENERATED_MARKER = "daqauntum_generated"

# Vault layout. Everything DaQauntum writes lives under these folders so the
# user's own notes are never touched.
EXPORT_FOLDERS = {
    "memory": "DaQauntum/Memory",
    "knowledge": "DaQauntum/Knowledge",
    "projects": "DaQauntum/Projects",
    "decisions": "DaQauntum/Decisions",
    "sources": "DaQauntum/Sources",
    "index": "DaQauntum",
}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", flags=re.S)


def safe_filename(text: str, *, max_length: int = 80, fallback: str = "note") -> str:
    """A filename that is safe on every platform and cannot escape the vault."""
    cleaned = _UNSAFE.sub("-", str(text or "")).replace("\n", " ").strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip(". ")
    # Strip path separators entirely rather than mapping them, so no input can
    # produce a traversal even before the vault-root check.
    cleaned = cleaned.replace("..", "-")
    return (cleaned[:max_length].strip() or fallback)


def wikilink(target: str, label: str | None = None) -> str:
    target = str(target or "").replace("[", "(").replace("]", ")")
    if label and label != target:
        return f"[[{target}|{label}]]"
    return f"[[{target}]]"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Read simple YAML-ish frontmatter without requiring a YAML parse.

    Vault notes are user-authored and may contain anything; this reads only
    flat `key: value` pairs and never evaluates content.
    """
    match = _FRONTMATTER.match(str(text or ""))
    if not match:
        return {}, str(text or "")
    fields: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        lowered = value.lower()
        if lowered in {"true", "false"}:
            fields[key] = lowered == "true"
        else:
            fields[key] = value
    return fields, str(text or "")[match.end():]


def render_frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (list, tuple)):
            items = ", ".join(str(v).replace('"', "'") for v in value)
            lines.append(f"{key}: [{items}]")
        else:
            text = str(value).replace("\n", " ")
            lines.append(f'{key}: "{text}"' if ":" in text or text.strip() != text else f"{key}: {text}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def is_generated(text: str) -> bool:
    fields, _ = parse_frontmatter(text)
    return bool(fields.get(GENERATED_MARKER))


class Vault:
    """A local Obsidian vault directory.

    All writes are confined to the DaQauntum folders and verified against the
    vault root, so neither a memory title nor a note name can write outside it.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".obsidian").mkdir(exist_ok=True)
        for folder in EXPORT_FOLDERS.values():
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def path_for(self, folder_key: str, name: str) -> Path:
        folder = EXPORT_FOLDERS.get(folder_key, EXPORT_FOLDERS["index"])
        target = (self.root / folder / f"{safe_filename(name)}.md").resolve()
        if self.root not in target.parents:
            raise ValueError("Refusing to write outside the vault root")
        return target

    def write_note(self, folder_key: str, name: str, frontmatter: dict[str, Any], body: str) -> Path:
        frontmatter = {GENERATED_MARKER: True, **frontmatter}
        target = self.path_for(folder_key, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_frontmatter(frontmatter) + "\n" + body.rstrip() + "\n", encoding="utf-8")
        return target

    def iter_notes(self, *, include_generated: bool = False, max_files: int = 5000):
        """Yield (path, frontmatter, body) for markdown notes in the vault."""
        count = 0
        for path in sorted(self.root.rglob("*.md")):
            if count >= max_files:
                break
            if ".obsidian" in path.parts or ".trash" in path.parts:
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            fields, body = parse_frontmatter(raw)
            if fields.get(GENERATED_MARKER) and not include_generated:
                continue
            count += 1
            yield path, fields, body

    def stats(self) -> dict[str, Any]:
        if not self.root.exists():
            return {"exists": False, "root": str(self.root), "notes": 0, "generated": 0, "user_notes": 0}
        total = generated = 0
        for path in self.root.rglob("*.md"):
            if ".obsidian" in path.parts or ".trash" in path.parts:
                continue
            total += 1
            try:
                if is_generated(path.read_text(encoding="utf-8", errors="ignore")):
                    generated += 1
            except OSError:
                continue
        return {
            "exists": True,
            "root": str(self.root),
            "notes": total,
            "generated": generated,
            "user_notes": total - generated,
        }
