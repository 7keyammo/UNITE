#!/usr/bin/env python3
from __future__ import annotations

import argparse
from core.kernel import DaQauntumKernel

STAGES = ["manual", "identify", "semi_automate", "expand", "handoff"]


def ask(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def yesno(label: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    value = input(f"{label} [{marker}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a portable DaQauntum FRAME-inspired workspace")
    ap.add_argument("--config", default=None)
    ap.add_argument("--name")
    ap.add_argument("--focus")
    ap.add_argument("--done")
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--agent", action="append", default=[], help="Optional agent description; repeatable")
    ap.add_argument("--skill", action="append", default=[], help="Optional skill description; repeatable")
    args = ap.parse_args()

    guided = not any([args.name, args.focus, args.done, args.agent, args.skill, args.stage])
    if guided:
        print("\nDaQauntum OS Builder — one workspace, one job\n")

    name = args.name or ask("Workspace name")
    focus = args.focus or ask("FOCUS — What is the ONE job this workspace should do?")
    done = args.done or ask("FOCUS — What does done look like?")
    stage = args.stage or (ask("ENGINE — maturity stage (manual/identify/semi_automate/expand/handoff)", "manual") if guided else "manual")
    if stage not in STAGES:
        raise SystemExit("Invalid stage. Choose: " + ", ".join(STAGES))
    if not name or not focus or not done:
        raise SystemExit("name, focus, and done are required")

    resources: list[str] = []
    workflow = bottleneck = first_skill = first_agent = ""
    if guided:
        resource_text = ask("RESOURCES — What rules/examples/reference material should this OS know? (comma-separated, optional)")
        resources = [x.strip() for x in resource_text.split(",") if x.strip()]
        workflow = ask("ENGINE — Describe the current manual workflow (optional)")
        bottleneck = ask("ENGINE — What is the biggest bottleneck right now? (optional)")
        first_skill = ask("MAKE — Describe the first reusable skill to create (optional)")
        first_agent = ask("MAKE — Describe the first specialist agent to create (optional)")

    kernel = DaQauntumKernel(args.config)
    workspace = kernel.workspaces.create(name, focus, done, stage=stage, resources=resources)
    if workflow:
        kernel.workspaces.add_resource_text(workspace["id"], "Current Manual Workflow", workflow)
    if bottleneck:
        kernel.workspaces.add_resource_text(workspace["id"], "Current Bottleneck", bottleneck)

    skill_descriptions = list(args.skill)
    agent_descriptions = list(args.agent)
    if first_skill:
        skill_descriptions.append(first_skill)
    if first_agent:
        agent_descriptions.append(first_agent)
    for description in skill_descriptions:
        kernel.workspaces.create_skill_from_description(workspace["id"], description)
    for description in agent_descriptions:
        kernel.workspaces.create_agent_from_description(workspace["id"], description)

    if guided and yesno("ACCESS — Register a declarative connection now?", False):
        connection_name = ask("Connection name", "Workspace Connection")
        connection_kind = ask("Connection kind (mcp/note/other)", "mcp")
        env = ask("Secret environment variable names, comma-separated (optional)")
        env_names = [x.strip().upper() for x in env.split(",") if x.strip()]
        kernel.workspaces.add_connection(workspace["id"], connection_name, connection_kind, {}, env_names)

    kernel.workspaces.set_active(workspace["id"])

    print(f"\nCreated workspace #{workspace['id']}: {workspace['name']}")
    print(f"Path: {workspace['path']}")
    print("FRAME: FOCUS.md · resources/ · ACCESS.md · skills/agents/ · ENGINE.md")
    print("Active workspace set. Launch DaQauntum and open Workspaces / Agent Studio to extend it.")


if __name__ == "__main__":
    main()
