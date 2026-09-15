from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


class ObsidianExporter:
    """Export DaQauntum workspaces as a plain-Markdown Obsidian-compatible vault."""

    def __init__(self, workspaces, workbench, root: str | Path | None = None):
        self.workspaces = workspaces
        self.workbench = workbench
        self.root = Path(root or workspaces.obsidian_root).expanduser().resolve()

    def export(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / ".obsidian").mkdir(exist_ok=True)
        lines = ["# DaQauntum Workspace Universe", "", "Generated from your local DaQauntum workspaces. The source-of-truth remains the DaQauntum workspace folders.", "", "## Workspaces"]
        count = 0
        for workspace in self.workspaces.list():
            count += 1
            dest = self.root / workspace["slug"]
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(Path(workspace["path"]), dest)
            jobs = self.workbench.list(workspace["id"], limit=20)
            job_lines = ["# Workbench", ""]
            for job in jobs:
                job_lines += [f"## {job['agent_slug']} — {job['status']}", "", f"**Prompt:** {job['prompt']}", "", job.get("response") or job.get("error") or "No output yet.", ""]
            (dest / "WORKBENCH.md").write_text("\n".join(job_lines), encoding="utf-8")
            dashboard = [
                "---", f"workspace_id: {workspace['id']}", f"stage: {workspace['stage']}", "---", "",
                f"# {workspace['name']}", "", f"**Focus:** {workspace['focus']}", "", f"**Done looks like:** {workspace['done_looks_like']}", "",
                "## Navigate", "- [[FOCUS]]", "- [[ACCESS]]", "- [[ENGINE]]", "- [[AGENTS]]", "- [[WORKBENCH]]", "- [[resources/README|Resources]]", "",
            ]
            (dest / "DASHBOARD.md").write_text("\n".join(dashboard), encoding="utf-8")
            lines.append(f"- [[{workspace['slug']}/DASHBOARD|{workspace['name']}]] — `{workspace['stage']}`")
        (self.root / "HOME.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"ok": True, "root": str(self.root), "workspaces": count, "home": str(self.root / "HOME.md")}
