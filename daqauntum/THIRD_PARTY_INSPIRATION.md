# Third-party inspiration and feature audit

DaQauntum v0.3.7 was developed after reviewing Calvin Hia / Dainami AI's public AI-OS course and linked public resources. The purpose was to identify useful concepts DaQauntum did not yet formalize, then implement them inside DaQauntum's existing local-first, permission-gated architecture.

## References reviewed

- Video: `https://www.youtube.com/watch?v=BOjYV-GxjHs`
- AI OS starter: `https://github.com/mrdainami/ai-os-starter`
- Nami: `https://github.com/mrdainami/nami`
- Dainami AI: `https://dainami.ai/`
- Apify Dataset Items API: `https://docs.apify.com/api/v2/dataset-items-get`

The public starter/Nami repositories advertise MIT licensing. DaQauntum does **not** vendor or copy their implementation source; the features below are independently implemented against DaQauntum's own kernel, memory, permission, provenance, connector, and GUI systems.

## Feature audit

| Concept observed | Before v0.3.7 | DaQauntum v0.3.7 |
|---|---|---|
| Swappable model/agent engine | Already present | Preserved |
| Persistent memory/context | Already present | Preserved |
| Tool/connectors | Already present | Preserved + Apify dataset adapter |
| Human approval before actions | Already present | Preserved globally |
| One AI OS = one job | Informal project concept | Formal portable workspace |
| FRAME-style durable context | Not formalized | Focus / Resources / Access / Make / Engine |
| OS-builder interview | Missing | `scripts/build_os.py` guided interview |
| Editable plain-text skills | Missing | `skills/*.md` Skill Studio |
| Editable named agents | Partial specialist classes | Workspace `agents/*.md` Agent Studio |
| Parallel agent panes | Missing | Agent Workbench |
| Connections as workspace context | Global connectors only | Access Studio + declarative `.mcp.json` |
| Obsidian visualization/export | Missing | Clickable Markdown vault export |
| Automation maturity ladder | Permission levels existed | Workspace manual → identify → semi_automate → expand → handoff |
| Apify structured results | Missing | Explicit dataset connector + local source snapshot |

## DaQauntum-specific boundaries

DaQauntum deliberately modifies the referenced patterns in several ways:

1. Parallel workspace agents are draft-only and do not receive tool authority.
2. Handoff runs through DaQauntum's existing `PermissionManager`.
3. Connection declarations never contain secret values and are not automatically executable.
4. Workspace maturity cannot override global approval requirements.
5. Connected/scraped material becomes Source Intelligence with hashes and provenance before autonomous learning uses it.
6. Model-provider choice remains subordinate to privacy policy.
7. Autonomous learning accumulates evaluated training traces but cannot silently fine-tune or deploy a native model.
