#!/usr/bin/env python3
from __future__ import annotations

import shutil
from pathlib import Path

from core.kernel import DaQauntumKernel


def main() -> None:
    kernel = DaQauntumKernel()
    workspace = kernel.workspaces.active()
    if not workspace:
        raise SystemExit("No active DaQauntum workspace. Activate one first.")
    details = kernel.workspaces.details(workspace["id"])
    root = Path.cwd()
    skills_root = root / ".agents" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    exported = []
    for skill in details.get("skills", []):
        source = Path(skill["path"])
        target_dir = skills_root / skill["slug"]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "SKILL.md"
        shutil.copy2(source, target)
        exported.append(str(target.relative_to(root)))

    agents_md = root / "AGENTS.md"
    lines = [
        f"# DaQauntum Active Workspace — {details['name']}",
        "",
        "This file is generated from DaQauntum's active portable workspace so compatible coding agents can share its durable project context.",
        "",
        "## One job",
        details["focus"],
        "",
        "## Done looks like",
        details["done_looks_like"],
        "",
        "## Agents",
    ]
    for agent in details.get("agents", []):
        lines.append(f"- **{agent['name']}** — {agent['role']}")
    lines += ["", "## DaQauntum authority", "External agents may inspect/draft inside their configured sandbox. Consequential execution remains subject to DaQauntum PermissionManager.", ""]
    agents_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"Exported AGENTS.md and {len(exported)} portable skill(s).")
    for item in exported:
        print("-", item)


if __name__ == "__main__":
    main()
