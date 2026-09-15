"""DaQauntum Obsidian long-term memory bridge.

Exports DaQauntum's memory into a vault as linked Markdown, and indexes the
user's own notes back as citable sources. The vault is a projection; the
database stays authoritative. DaQauntum's own exported notes are never
re-indexed, so it cannot cite itself as independent evidence.
"""

from obsidian.bridge import ObsidianMemoryBridge
from obsidian.vault import (
    EXPORT_FOLDERS,
    GENERATED_MARKER,
    Vault,
    is_generated,
    parse_frontmatter,
    render_frontmatter,
    safe_filename,
    wikilink,
)

__all__ = [
    "EXPORT_FOLDERS",
    "GENERATED_MARKER",
    "ObsidianMemoryBridge",
    "Vault",
    "is_generated",
    "parse_frontmatter",
    "render_frontmatter",
    "safe_filename",
    "wikilink",
]
