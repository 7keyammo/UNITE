from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import yaml


STAGES = ("manual", "identify", "semi_automate", "expand", "handoff")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return value or "workspace"


def _safe_name(text: str, fallback: str = "item") -> str:
    value = re.sub(r"[^A-Za-z0-9._ -]+", "", str(text)).strip()
    return value[:80] or fallback


class WorkspaceManager:
    """FRAME-inspired, portable workspaces for one-job AI operating systems.

    Durable context is stored as editable Markdown. Runtime metadata is mirrored into
    SQLite so the GUI and autonomous processes can inspect it without parsing every
    file on every request. Connections are declarative only here; execution still
    goes through DaQauntum's existing connectors/tools and permission manager.
    """

    def __init__(self, memory, config: dict[str, Any] | None = None):
        self.memory = memory
        self.conn = memory.conn
        self.config = dict(config or {})
        self.root = Path(self.config.get("root", "data/workspaces")).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.obsidian_root = Path(self.config.get("obsidian_root", "data/obsidian/DaQauntum")).expanduser().resolve()
        self.lock = threading.RLock()
        self._ensure_schema()
        self._ensure_manifest()

    def _ensure_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS os_workspaces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    focus TEXT NOT NULL,
                    done_looks_like TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'manual',
                    active INTEGER NOT NULL DEFAULT 1,
                    path TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS os_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    path TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, slug)
                );
                CREATE TABLE IF NOT EXISTS os_agents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    path TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workspace_id, slug)
                );
                CREATE TABLE IF NOT EXISTS os_connections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    env_names_json TEXT NOT NULL DEFAULT '[]',
                    executable INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_os_skills_workspace ON os_skills(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_os_agents_workspace ON os_agents(workspace_id);
                CREATE INDEX IF NOT EXISTS idx_os_connections_workspace ON os_connections(workspace_id);
                """
            )
            self.conn.commit()

    def _ensure_manifest(self) -> None:
        path = self.root / ".mcp.json"
        if not path.exists():
            path.write_text(json.dumps({"mcpServers": {}}, indent=2) + "\n", encoding="utf-8")

    def _row(self, row) -> dict[str, Any] | None:
        if row is None:
            return None
        return dict(row)

    def _workspace_dir(self, workspace: dict[str, Any]) -> Path:
        path = Path(workspace["path"]).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Workspace path escaped configured workspace root") from exc
        return path

    def list(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM os_workspaces"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY updated_at DESC, id DESC"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get(self, workspace_id: int | str) -> dict[str, Any] | None:
        if isinstance(workspace_id, str) and not workspace_id.isdigit():
            row = self.conn.execute("SELECT * FROM os_workspaces WHERE slug = ?", (workspace_id,)).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM os_workspaces WHERE id = ?", (int(workspace_id),)).fetchone()
        return self._row(row)

    @property
    def active_id(self) -> int | None:
        value = self.memory.get_state("workspace.active_id", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def active(self) -> dict[str, Any] | None:
        wid = self.active_id
        return self.get(wid) if wid else None

    def set_active(self, workspace_id: int | str | None) -> dict[str, Any] | None:
        if workspace_id in (None, "", 0, "0"):
            self.memory.set_state("workspace.active_id", None)
            self.memory.add_event("workspace_active", {"workspace_id": None})
            return None
        workspace = self.get(workspace_id)
        if not workspace or not workspace.get("active"):
            raise ValueError("Workspace not found or inactive")
        self.memory.set_state("workspace.active_id", int(workspace["id"]))
        self.memory.add_event("workspace_active", {"workspace_id": int(workspace["id"]), "name": workspace["name"]})
        return workspace

    def create(self, name: str, focus: str, done_looks_like: str, *, stage: str = "manual", resources: list[str] | None = None) -> dict[str, Any]:
        name = _safe_name(name, "DaQauntum Workspace")
        focus = str(focus).strip()
        done = str(done_looks_like).strip()
        if not focus or not done:
            raise ValueError("focus and done_looks_like are required")
        if stage not in STAGES:
            raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
        base = _slug(name)
        slug = base
        i = 2
        while self.get(slug):
            slug = f"{base}-{i}"
            i += 1
        path = self.root / slug
        path.mkdir(parents=True, exist_ok=False)
        (path / "resources").mkdir()
        (path / "skills").mkdir()
        (path / "agents").mkdir()
        (path / "outputs").mkdir()
        self._write_workspace_files(path, name, focus, done, stage, resources or [])
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO os_workspaces(slug,name,focus,done_looks_like,stage,path) VALUES(?,?,?,?,?,?)",
                (slug, name, focus, done, stage, str(path)),
            )
            self.conn.commit()
            wid = int(cur.lastrowid)
        self.memory.add_event("workspace_created", {"workspace_id": wid, "slug": slug, "name": name, "stage": stage})
        workspace = self.get(wid)
        if workspace and self.active_id is None:
            self.set_active(wid)
        return workspace or {}

    def _write_workspace_files(self, path: Path, name: str, focus: str, done: str, stage: str, resources: list[str]) -> None:
        (path / "FOCUS.md").write_text(
            f"# {name} — Focus\n\n## One job\n{focus}\n\n## Done looks like\n{done}\n\n## Guardrail\nStay on this one job unless the user deliberately changes the workspace focus.\n",
            encoding="utf-8",
        )
        (path / "ACCESS.md").write_text(
            "# Access\n\nNo external connection is authorized merely because it is listed here.\n"
            "Connections must be explicitly registered in DaQauntum and still pass its permission system.\n\n## Connected resources\n- None yet.\n",
            encoding="utf-8",
        )
        stage_explain = {
            "manual": "Do the workflow manually and define quality before automation.",
            "identify": "Identify the bottleneck and the smallest useful handoff.",
            "semi_automate": "Agents may prepare work; a human reviews consequential outputs.",
            "expand": "Connect proven pieces into a larger workflow while preserving approvals.",
            "handoff": "Trusted reversible work may run autonomously inside explicit boundaries.",
        }[stage]
        (path / "ENGINE.md").write_text(
            f"# Engine\n\n## Maturity stage\n`{stage}`\n\n{stage_explain}\n\n"
            "## Runtime\nDaQauntum chooses local / hybrid / cloud according to runtime settings and privacy policy.\n\n"
            "## Authority\nWorkspace maturity never overrides DaQauntum's global permission manager.\n",
            encoding="utf-8",
        )
        (path / "AGENTS.md").write_text(
            f"# {name} — Agents\n\nThis workspace uses plain-text agents and skills.\n"
            "Agent workbench runs are read-only drafts by default; consequential execution must be handed back to DaQauntum's permission-gated tool system.\n",
            encoding="utf-8",
        )
        (path / "resources" / "README.md").write_text(
            "# Resources\n\nPut examples, rules, voice notes, reference material, and other durable context here.\n"
            + ("\n## Initial resources\n" + "\n".join(f"- {x}" for x in resources) + "\n" if resources else ""),
            encoding="utf-8",
        )
        (path / "workspace.yaml").write_text(yaml.safe_dump({"name": name, "focus": focus, "done_looks_like": done, "stage": stage}, sort_keys=False), encoding="utf-8")

    def set_stage(self, workspace_id: int | str, stage: str) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of: {', '.join(STAGES)}")
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        with self.lock:
            self.conn.execute("UPDATE os_workspaces SET stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (stage, workspace["id"]))
            self.conn.commit()
        path = self._workspace_dir(workspace)
        engine = path / "ENGINE.md"
        text = engine.read_text(encoding="utf-8") if engine.exists() else "# Engine\n"
        text = re.sub(r"## Maturity stage\n`[^`]+`", f"## Maturity stage\n`{stage}`", text, count=1)
        engine.write_text(text, encoding="utf-8")
        self.memory.add_event("workspace_stage", {"workspace_id": workspace["id"], "stage": stage})
        return self.get(workspace["id"]) or {}

    def add_resource_text(self, workspace_id: int | str, name: str, content: str) -> dict[str, Any]:
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        filename = _slug(name) + ".md"
        path = self._workspace_dir(workspace) / "resources" / filename
        path.write_text(f"# {_safe_name(name)}\n\n{str(content).strip()}\n", encoding="utf-8")
        self.conn.execute("UPDATE os_workspaces SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (workspace["id"],))
        self.conn.commit()
        self.memory.add_event("workspace_resource", {"workspace_id": workspace["id"], "path": str(path)})
        return {"name": name, "path": str(path)}

    def add_skill(self, workspace_id: int | str, name: str, description: str, instructions: str) -> dict[str, Any]:
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        name = _safe_name(name, "Skill")
        slug = _slug(name)
        description = str(description).strip() or "Reusable workspace skill."
        instructions = str(instructions).strip()
        if not instructions:
            raise ValueError("Skill instructions are required")
        path = self._workspace_dir(workspace) / "skills" / f"{slug}.md"
        path.write_text(
            "---\n" + yaml.safe_dump({"name": name, "slug": slug, "description": description}, sort_keys=False).strip() + "\n---\n\n"
            f"# {name}\n\n## Purpose\n{description}\n\n## Instructions\n{instructions}\n",
            encoding="utf-8",
        )
        with self.lock:
            self.conn.execute(
                """INSERT INTO os_skills(workspace_id,slug,name,description,instructions,path) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(workspace_id,slug) DO UPDATE SET name=excluded.name,description=excluded.description,instructions=excluded.instructions,path=excluded.path,active=1,updated_at=CURRENT_TIMESTAMP""",
                (workspace["id"], slug, name, description, instructions, str(path)),
            )
            self.conn.commit()
        self.memory.add_event("workspace_skill", {"workspace_id": workspace["id"], "slug": slug})
        return self.get_skill(workspace["id"], slug) or {}

    def create_skill_from_description(self, workspace_id: int | str, description: str, name: str | None = None) -> dict[str, Any]:
        description = str(description).strip()
        if not description:
            raise ValueError("Describe the skill you want")
        skill_name = name or " ".join(description.split()[:5]).title()
        instructions = (
            "1. Restate the expected output and success criteria.\n"
            "2. Read the workspace Focus and relevant Resources before doing the work.\n"
            f"3. Perform this job: {description}\n"
            "4. Check the result against Done looks like.\n"
            "5. Surface uncertainty and any action that requires user approval."
        )
        return self.add_skill(workspace_id, skill_name, description, instructions)

    def get_skill(self, workspace_id: int, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM os_skills WHERE workspace_id=? AND slug=? AND active=1", (workspace_id, slug)).fetchone()
        return self._row(row)

    def skills(self, workspace_id: int | str) -> list[dict[str, Any]]:
        wid = int(self.get(workspace_id)["id"]) if self.get(workspace_id) else -1
        return [dict(r) for r in self.conn.execute("SELECT * FROM os_skills WHERE workspace_id=? AND active=1 ORDER BY name", (wid,)).fetchall()]

    def add_agent(self, workspace_id: int | str, name: str, role: str, system_prompt: str, skills: list[str] | None = None) -> dict[str, Any]:
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        name = _safe_name(name, "Agent")
        slug = _slug(name)
        role = str(role).strip() or "Workspace specialist"
        system_prompt = str(system_prompt).strip() or f"You are {name}. {role}"
        skills = [_slug(x) for x in (skills or []) if str(x).strip()]
        path = self._workspace_dir(workspace) / "agents" / f"{slug}.md"
        path.write_text(
            "---\n" + yaml.safe_dump({"name": name, "slug": slug, "role": role, "skills": skills}, sort_keys=False).strip() + "\n---\n\n"
            f"# {name}\n\n## Role\n{role}\n\n## System prompt\n{system_prompt}\n",
            encoding="utf-8",
        )
        with self.lock:
            self.conn.execute(
                """INSERT INTO os_agents(workspace_id,slug,name,role,system_prompt,skills_json,path) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id,slug) DO UPDATE SET name=excluded.name,role=excluded.role,system_prompt=excluded.system_prompt,skills_json=excluded.skills_json,path=excluded.path,active=1,updated_at=CURRENT_TIMESTAMP""",
                (workspace["id"], slug, name, role, system_prompt, json.dumps(skills), str(path)),
            )
            self.conn.commit()
        self.memory.add_event("workspace_agent", {"workspace_id": workspace["id"], "slug": slug})
        return self.get_agent(workspace["id"], slug) or {}

    def create_agent_from_description(self, workspace_id: int | str, description: str, name: str | None = None, skills: list[str] | None = None) -> dict[str, Any]:
        description = str(description).strip()
        if not description:
            raise ValueError("Describe the agent you want")
        agent_name = name or " ".join(description.split()[:4]).title()
        prompt = (
            f"You are {agent_name}, a DaQauntum workspace agent. Your role is: {description}. "
            "Stay within the workspace Focus, use workspace Resources, make uncertainty visible, and never claim to have executed tools. "
            "Your workbench outputs are drafts until explicitly handed to DaQauntum's permission-gated execution path."
        )
        return self.add_agent(workspace_id, agent_name, description, prompt, skills)

    def get_agent(self, workspace_id: int, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM os_agents WHERE workspace_id=? AND slug=? AND active=1", (workspace_id, slug)).fetchone()
        item = self._row(row)
        if item:
            item["skills"] = json.loads(item.pop("skills_json", "[]") or "[]")
        return item

    def agents(self, workspace_id: int | str) -> list[dict[str, Any]]:
        workspace = self.get(workspace_id)
        if not workspace:
            return []
        rows = self.conn.execute("SELECT * FROM os_agents WHERE workspace_id=? AND active=1 ORDER BY name", (workspace["id"],)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["skills"] = json.loads(item.pop("skills_json", "[]") or "[]")
            out.append(item)
        return out

    def add_connection(self, workspace_id: int | str, name: str, kind: str, config: dict[str, Any] | None = None, env_names: list[str] | None = None) -> dict[str, Any]:
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        env_names = [str(x).strip() for x in (env_names or []) if str(x).strip()]
        for env_name in env_names:
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", env_name):
                raise ValueError(f"Invalid environment variable name: {env_name}")
        config = dict(config or {})
        # Connections registered here are intentionally declarative. They cannot execute
        # arbitrary commands until a DaQauntum adapter is explicitly implemented/enabled.
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO os_connections(workspace_id,name,kind,config_json,env_names_json,executable) VALUES(?,?,?,?,?,0)",
                (workspace["id"], _safe_name(name, "Connection"), str(kind), json.dumps(config), json.dumps(env_names)),
            )
            self.conn.commit()
        self._refresh_access_markdown(workspace["id"])
        self._refresh_mcp_manifest()
        self.memory.add_event("workspace_connection", {"workspace_id": workspace["id"], "connection_id": int(cur.lastrowid), "kind": kind})
        return self.connection(int(cur.lastrowid)) or {}

    def connection(self, connection_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM os_connections WHERE id=?", (int(connection_id),)).fetchone()
        item = self._row(row)
        if item:
            item["config"] = json.loads(item.pop("config_json", "{}") or "{}")
            item["env_names"] = json.loads(item.pop("env_names_json", "[]") or "[]")
        return item

    def connections(self, workspace_id: int | str) -> list[dict[str, Any]]:
        workspace = self.get(workspace_id)
        if not workspace:
            return []
        return [self.connection(r["id"]) for r in self.conn.execute("SELECT id FROM os_connections WHERE workspace_id=? AND active=1 ORDER BY id", (workspace["id"],)).fetchall()]

    def _refresh_mcp_manifest(self) -> None:
        servers: dict[str, Any] = {}
        rows = self.conn.execute("SELECT * FROM os_connections WHERE active=1 AND kind='mcp' ORDER BY id").fetchall()
        for row in rows:
            item = dict(row)
            cfg = json.loads(item.get("config_json") or "{}")
            env_names = json.loads(item.get("env_names_json") or "[]")
            slug = _slug(item.get("name") or f"mcp-{item['id']}")
            server: dict[str, Any] = {}
            if cfg.get("command"):
                server["command"] = str(cfg["command"])
                if isinstance(cfg.get("args"), list):
                    server["args"] = [str(x) for x in cfg["args"]]
            elif cfg.get("url"):
                server["url"] = str(cfg["url"])
            else:
                server["disabled"] = True
            if env_names:
                server["env"] = {name: "${" + name + "}" for name in env_names}
            server["x-daqauntum-workspace-id"] = int(item["workspace_id"])
            server["x-daqauntum-declarative-only"] = True
            servers[slug] = server
        (self.root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}, indent=2) + "\n", encoding="utf-8")

    def _refresh_access_markdown(self, workspace_id: int) -> None:
        workspace = self.get(workspace_id)
        if not workspace:
            return
        lines = ["# Access", "", "Connections listed here are declarations only. Runtime authority still comes from DaQauntum's connector/tool registry and permission manager.", "", "## Registered connections"]
        conns = self.connections(workspace_id)
        if not conns:
            lines.append("- None yet.")
        for item in conns:
            env = ", ".join(item.get("env_names", [])) or "none"
            lines.append(f"- **{item['name']}** — `{item['kind']}` — executable: `false` — env refs: {env}")
        (self._workspace_dir(workspace) / "ACCESS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def context_messages(self, query: str = "", workspace_id: int | str | None = None, *, max_chars: int = 12000) -> list[dict[str, str]]:
        workspace = self.get(workspace_id) if workspace_id not in (None, "") else self.active()
        if not workspace:
            return []
        path = self._workspace_dir(workspace)
        pieces: list[str] = []
        for filename in ("FOCUS.md", "ACCESS.md", "ENGINE.md", "AGENTS.md"):
            f = path / filename
            if f.exists():
                pieces.append(f"[{filename}]\n{f.read_text(encoding='utf-8')}")
        # Resources are user-owned durable context. Bound their size for prompt hygiene.
        for f in sorted((path / "resources").glob("*.md"))[:20]:
            text = f.read_text(encoding="utf-8")
            pieces.append(f"[resource:{f.name}]\n{text}")
        body = "\n\n".join(pieces)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n[workspace context truncated]"
        return [{"role": "system", "content": f"ACTIVE DAQAUNTUM WORKSPACE: {workspace['name']}\n{body}"}]

    def details(self, workspace_id: int | str) -> dict[str, Any]:
        workspace = self.get(workspace_id)
        if not workspace:
            raise ValueError("Workspace not found")
        return {**workspace, "skills": self.skills(workspace["id"]), "agents": self.agents(workspace["id"]), "connections": self.connections(workspace["id"])}

    def stats(self) -> dict[str, Any]:
        return {
            "workspaces": int(self.conn.execute("SELECT COUNT(*) FROM os_workspaces WHERE active=1").fetchone()[0]),
            "skills": int(self.conn.execute("SELECT COUNT(*) FROM os_skills WHERE active=1").fetchone()[0]),
            "agents": int(self.conn.execute("SELECT COUNT(*) FROM os_agents WHERE active=1").fetchone()[0]),
            "connections": int(self.conn.execute("SELECT COUNT(*) FROM os_connections WHERE active=1").fetchone()[0]),
            "active_workspace_id": self.active_id,
            "active_workspace": self.active(),
            "root": str(self.root),
        }
