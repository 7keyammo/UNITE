#!/usr/bin/env python3
from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.kernel import DaQauntumKernel


def print_status(console: Console, kernel: DaQauntumKernel) -> None:
    status = kernel.status()
    table = Table(title="DaQauntum Brain Status")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("version", str(status["version"]))
    table.add_row("permission_level", str(status["permission_level"]))
    table.add_row("planner", str(status["planner"]))
    table.add_row("critic", str(status["critic"]))
    table.add_row("model_policy", str(status["model_policy"]))
    for role in ("planner", "executor", "critic"):
        selected = status["roles"][role]
        table.add_row(role, f"{selected['provider']} / {selected['model']}")
    memory = status.get("memory", {})
    table.add_row("structured memory", str(memory.get("structured_total", 0)))
    table.add_row("active project", str(memory.get("active_project") or "none"))
    graph = status.get("knowledge_graph", {})
    table.add_row("knowledge nodes", str(graph.get("nodes", 0)))
    table.add_row("knowledge edges", str(graph.get("edges", 0)))
    table.add_row("provenance records", str(graph.get("provenance_records", 0)))
    sources = status.get("sources", {})
    table.add_row("sources", str(sources.get("sources", 0)))
    table.add_row("source chunks", str(sources.get("chunks", 0)))
    table.add_row("URL fetch", "enabled" if sources.get("url_fetch_enabled") else "disabled")
    learning = status.get("learning", {})
    table.add_row("learning sessions", str(learning.get("completed", 0)))
    native = status.get("native_model", {})
    table.add_row("native seed traces", str(native.get("training_memories", 0)))
    table.add_row("tools", ", ".join(status["tools"]))
    table.add_row("pending approvals", ", ".join(status["pending_approvals"]) or "none")
    console.print(table)


def print_models(console: Console, kernel: DaQauntumKernel) -> None:
    matrix = kernel.model_matrix()
    roles = Table(title="DaQauntum Cognitive Role Matrix")
    roles.add_column("Role")
    roles.add_column("Selected")
    roles.add_column("Fallback path")
    for role, info in matrix["roles"].items():
        selected = info.get("selected")
        selected_text = f"{selected['provider']} / {selected['model']}" if selected else "none"
        fallback = " -> ".join(item["provider"] for item in info.get("candidates", []))
        roles.add_row(role, selected_text, fallback or "none")
    console.print(roles)

    providers = Table(title="Provider Health")
    providers.add_column("Provider")
    providers.add_column("Default model")
    providers.add_column("Ready")
    providers.add_column("Detail")
    for item in matrix["providers"]:
        providers.add_row(item["provider"], item["model"], "yes" if item["available"] else "no", item["detail"])
    console.print(providers)


def print_policy_preview(console: Console, kernel: DaQauntumKernel, request: str) -> None:
    preview = kernel.preview_policy(request)
    profile = preview["profile"]
    decision = preview["decision"]

    profile_table = Table(title="DaQauntum Task Profile")
    profile_table.add_column("Field")
    profile_table.add_column("Value")
    for field in ("domain", "privacy", "complexity", "risk", "latency", "cost"):
        profile_table.add_row(field, str(profile[field]))
    profile_table.add_row("signals", ", ".join(profile.get("signals", [])) or "none")
    console.print(profile_table)

    route_table = Table(title="Model Policy Route")
    route_table.add_column("Role")
    route_table.add_column("Selected now")
    route_table.add_column("Policy preference")
    for role in ("planner", "executor", "critic"):
        resolved = preview["resolved"][role]
        selected = resolved.get("selected")
        selected_text = f"{selected['provider']} / {selected['model']}" if selected else "none"
        hint = decision.get("role_hints", {}).get(role, {})
        preference = " -> ".join(hint.get("preference", [])) or "configured default"
        tier = hint.get("model_tier")
        if tier:
            preference += f" [{tier}]"
        route_table.add_row(role, selected_text, preference)
    console.print(route_table)
    if decision.get("reasons"):
        console.print("[dim]policy: " + "; ".join(decision["reasons"]) + "[/dim]")


def print_memory_stats(console: Console, kernel: DaQauntumKernel) -> None:
    stats = kernel.structured_memory.stats()
    table = Table(title="DaQauntum Structured Memory")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("enabled", str(stats.get("enabled")))
    table.add_row("active project", str(stats.get("active_project") or "none"))
    table.add_row("raw messages", str(stats.get("messages", 0)))
    table.add_row("events", str(stats.get("events", 0)))
    table.add_row("structured total", str(stats.get("structured_total", 0)))
    for kind, count in sorted(stats.get("by_kind", {}).items()):
        table.add_row(f"  {kind}", str(count))
    intelligence = stats.get("intelligence", {})
    table.add_row("intelligence", str(intelligence.get("enabled", False)))
    table.add_row("embedded memories", str(intelligence.get("embedded_memories", 0)))
    table.add_row("relations", str(intelligence.get("relations", 0)))
    table.add_row("contradictions", str(intelligence.get("contradictions", 0)))
    table.add_row("consolidated links", str(intelligence.get("consolidated_relations", 0)))
    console.print(table)


def print_memories(console: Console, memories: list[dict]) -> None:
    if not memories:
        console.print("[yellow]No matching structured memories.[/yellow]")
        return
    table = Table(title="Structured Memories")
    table.add_column("ID", justify="right")
    table.add_column("Kind")
    table.add_column("Project")
    table.add_column("Confidence")
    table.add_column("Score")
    table.add_column("Memory")
    for item in memories:
        content = item["content"].replace("\n", " ")
        if len(content) > 100:
            content = content[:97] + "..."
        table.add_row(
            str(item["id"]),
            item["kind"],
            item.get("project") or "-",
            f"{item.get('confidence', 1.0):.2f}",
            f"{item.get('retrieval', {}).get('total', 0.0):.2f}" if item.get('retrieval') else "-",
            f"{item['title']} | {content}",
        )
    console.print(table)



def print_knowledge(console: Console, nodes: list[dict]) -> None:
    if not nodes:
        console.print("[yellow]No matching knowledge nodes.[/yellow]")
        return
    table = Table(title="DaQauntum Knowledge Graph")
    table.add_column("Node", justify="right")
    table.add_column("Type")
    table.add_column("Project")
    table.add_column("Score")
    table.add_column("Name")
    for node in nodes:
        name = str(node.get("name", "")).replace("\n", " ")
        if len(name) > 120:
            name = name[:117] + "..."
        table.add_row(str(node.get("id")), str(node.get("node_type")), str(node.get("project") or "-"),
                      f"{node.get('graph_score', 0.0):.2f}" if "graph_score" in node else "-", name)
    console.print(table)


def print_sources(console: Console, results: list[dict]) -> None:
    if not results:
        console.print("[yellow]No matching sources.[/yellow]")
        return
    table = Table(title="DaQauntum Source Intelligence")
    table.add_column("Source")
    table.add_column("Chunk")
    table.add_column("Type")
    table.add_column("Project")
    table.add_column("Score")
    table.add_column("Title / Excerpt")
    for item in results:
        source = item.get("source", item)
        chunk = item.get("chunk") if isinstance(item, dict) else None
        excerpt = ""
        if chunk:
            excerpt = " ".join(str(chunk.get("content", "")).split())
            if len(excerpt) > 90:
                excerpt = excerpt[:87] + "..."
        title = str(source.get("title", ""))
        if excerpt:
            title += " | " + excerpt
        table.add_row(
            str(source.get("id", "-")),
            str(chunk.get("id")) if chunk else "-",
            str(source.get("source_type", "-")),
            str(source.get("project") or "-"),
            f"{item.get('score', 0.0):.2f}" if isinstance(item, dict) and "score" in item else "-",
            title,
        )
    console.print(table)



def print_learning(console: Console, kernel: DaQauntumKernel) -> None:
    stats = kernel.learning.stats()
    native = kernel.native_model.stats()
    table = Table(title="DaQauntum Autonomous Learning")
    table.add_column("Field"); table.add_column("Value")
    table.add_row("enabled", str(stats.get("enabled")))
    table.add_row("sessions", str(stats.get("sessions", 0)))
    table.add_row("completed", str(stats.get("completed", 0)))
    table.add_row("critic approved", str(stats.get("critic_approved", 0)))
    latest = stats.get("latest") or {}
    table.add_row("latest topic", str(latest.get("topic") or "none"))
    table.add_row("training memories", str(native.get("training_memories", 0)))
    table.add_row("dataset exports", str(native.get("exports", 0)))
    table.add_row("fine tuning", "disabled" if not native.get("fine_tuning_enabled") else "enabled")
    console.print(table)

def main() -> None:
    console = Console()
    kernel = DaQauntumKernel()
    console.print(
        Panel.fit(
            "DaQauntum Alpha v0.4.0 - Eyes + Hands\n"
            "Commands: /status, /models, /memory, /memories <query>, /remember <text>, "
            "/project <name|clear>, /memory-maintain, /contradictions, /graph <query>, /sources <query>, /source-add file <path>, /source <id>, /evidence <source> <relation> <node>, /profile <request>, /policy on|off, "
            "/model <role> <provider> (model optional), /learn, /learning, /evening-report, /native-export, /approve <id>, /help, exit",
            title="DAQAUNTUM",
        )
    )
    print_status(console, kernel)

    while True:
        try:
            request = console.input("[bold]You > [/bold]").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if request.lower() in {"exit", "quit"}:
            break
        if not request:
            continue

        if request == "/status":
            print_status(console, kernel)
            continue
        if request == "/models":
            print_models(console, kernel)
            continue
        if request == "/learning":
            print_learning(console, kernel)
            continue
        if request == "/learn":
            console.print("[cyan]Running one Deep autonomous learning session…[/cyan]")
            try:
                result = kernel.learning.run_daily()
                console.print(f"[green]Learning complete: {result['topic']}[/green]")
                console.print(f"Report: {result['report_path']}")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request == "/evening-report":
            try:
                result = kernel.learning.build_evening_digest()
                console.print(f"[green]Evening report: {result['report_path']}[/green]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request == "/native-export":
            try:
                result = kernel.native_model.export()
                console.print(f"[green]Exported {result['records']} records: {result['path']}[/green]")
                console.print("[dim]No model weights were changed.[/dim]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request == "/memory":
            print_memory_stats(console, kernel)
            continue
        if request == "/graph":
            stats = kernel.knowledge.stats()
            console.print_json(json.dumps(stats, ensure_ascii=False))
            print_knowledge(console, kernel.search_knowledge("", limit=20))
            continue
        if request.startswith("/graph "):
            query = request.split(maxsplit=1)[1].strip()
            print_knowledge(console, kernel.search_knowledge(query, limit=20))
            continue
        if request == "/graph-rebuild":
            console.print_json(json.dumps(kernel.rebuild_knowledge(), ensure_ascii=False))
            continue
        if request == "/sources":
            print_sources(console, kernel.sources.list_sources(limit=50, project=kernel.structured_memory.active_project))
            continue
        if request.startswith("/sources "):
            query = request.split(maxsplit=1)[1].strip()
            print_sources(console, kernel.search_sources(query, limit=20))
            continue
        if request.startswith("/source-add file "):
            path = request[len("/source-add file "):].strip()
            if not path:
                console.print("[red]Usage: /source-add file <relative-path>[/red]")
                continue
            try:
                item = kernel.ingest_source_file(path)
                console.print(f"[green]Indexed source #{item['id']}: {item['title']} ({item.get('chunk_count', 0)} chunks)[/green]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request.startswith("/source-add url "):
            url = request[len("/source-add url "):].strip()
            if not url:
                console.print("[red]Usage: /source-add url <https://...>[/red]")
                continue
            try:
                item = kernel.register_source_url(url, fetch=None)
                mode = "indexed" if item.get("status") == "indexed" else "registered (not fetched)"
                console.print(f"[green]Source #{item['id']}: {item['title']} - {mode}[/green]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request.startswith("/source-fetch "):
            url = request[len("/source-fetch "):].strip()
            try:
                item = kernel.register_source_url(url, fetch=True)
                console.print(f"[green]Fetched/indexed source #{item['id']}: {item['title']}[/green]")
            except Exception as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request.startswith("/source "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                source_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /source <source-id>[/red]")
                continue
            item = kernel.source_record(source_id)
            if item:
                item["chunks"] = kernel.sources.chunks_for_source(source_id, limit=20)
                console.print_json(json.dumps(item, ensure_ascii=False))
            else:
                console.print(f"[yellow]Source #{source_id} not found.[/yellow]")
            continue
        if request.startswith("/chunk "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                chunk_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /chunk <chunk-id>[/red]")
                continue
            chunk = kernel.source_chunk(chunk_id)
            if chunk:
                console.print_json(json.dumps(chunk, ensure_ascii=False))
            else:
                console.print(f"[yellow]Source chunk #{chunk_id} not found.[/yellow]")
            continue
        if request.startswith("/evidence "):
            parts = request.split()
            if len(parts) not in {4, 5}:
                console.print("[red]Usage: /evidence <source-id> <supports|contradicts|context_for> <node-id> [chunk-id][/red]")
                continue
            try:
                source_id = int(parts[1]); relation = parts[2]; node_id = int(parts[3])
                chunk_id = int(parts[4]) if len(parts) == 5 else None
                result = kernel.link_source_evidence(source_id, relation, node_id, chunk_id=chunk_id)
                console.print_json(json.dumps(result, ensure_ascii=False))
            except (ValueError, TypeError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request == "/source-rebuild":
            console.print_json(json.dumps(kernel.rebuild_sources(), ensure_ascii=False))
            continue
        if request.startswith("/node "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                node_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /node <knowledge-node-id>[/red]")
                continue
            node = kernel.knowledge_node(node_id)
            if node:
                console.print_json(json.dumps(node, ensure_ascii=False))
            else:
                console.print(f"[yellow]Knowledge node #{node_id} not found.[/yellow]")
            continue
        if request.startswith("/provenance "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                node_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /provenance <knowledge-node-id>[/red]")
                continue
            console.print_json(json.dumps(kernel.knowledge_provenance(node_id), ensure_ascii=False))
            continue
        if request.startswith("/edge-provenance "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                edge_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /edge-provenance <knowledge-edge-id>[/red]")
                continue
            console.print_json(json.dumps(kernel.knowledge_edge_provenance(edge_id), ensure_ascii=False))
            continue
        if request.startswith("/link "):
            parts = request.split(maxsplit=3)
            if len(parts) != 4:
                console.print("[red]Usage: /link <source-node-id> <relation> <target-node-id>[/red]")
                continue
            try:
                result = kernel.link_knowledge(int(parts[1]), parts[2], int(parts[3]))
                console.print_json(json.dumps(result, ensure_ascii=False))
            except (ValueError, TypeError) as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request == "/memory-maintain":
            result = kernel.maintain_memory()
            console.print_json(json.dumps(result, ensure_ascii=False))
            continue
        if request == "/contradictions":
            relations = kernel.memory_contradictions(limit=50)
            if relations:
                console.print_json(json.dumps(relations, ensure_ascii=False))
            else:
                console.print("[green]No active contradiction relations detected.[/green]")
            continue
        if request.startswith("/relations"):
            parts = request.split(maxsplit=1)
            memory_id = None
            if len(parts) == 2:
                try:
                    memory_id = int(parts[1])
                except ValueError:
                    console.print("[red]Usage: /relations <memory-id optional>[/red]")
                    continue
            relations = kernel.structured_memory.relations(memory_id=memory_id, limit=50)
            console.print_json(json.dumps(relations, ensure_ascii=False))
            continue
        if request == "/raw-memory":
            console.print_json(json.dumps(kernel.memory.recent(10), ensure_ascii=False))
            continue
        if request == "/memories":
            print_memories(console, kernel.memory.list_memories(limit=20))
            continue
        if request.startswith("/memories "):
            query = request.split(maxsplit=1)[1].strip()
            print_memories(console, kernel.search_structured_memory(query, limit=20))
            continue
        if request.startswith("/remember "):
            text = request.split(maxsplit=1)[1].strip()
            if not text:
                console.print("[red]Usage: /remember <fact or durable context>[/red]")
                continue
            try:
                item = kernel.remember(text)
                console.print(f"[green]Stored semantic memory #{item['id']}: {item['title']}[/green]")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request.startswith("/forget "):
            raw = request.split(maxsplit=1)[1].strip()
            try:
                memory_id = int(raw)
            except ValueError:
                console.print("[red]Usage: /forget <memory-id>[/red]")
                continue
            if kernel.forget_memory(memory_id):
                console.print(f"[green]Deactivated structured memory #{memory_id}.[/green]")
            else:
                console.print(f"[yellow]No active structured memory #{memory_id} found.[/yellow]")
            continue
        if request == "/project":
            console.print(f"Active project: {kernel.structured_memory.active_project or 'none'}")
            continue
        if request.startswith("/project "):
            name = request.split(maxsplit=1)[1].strip()
            if name.lower() in {"clear", "none", "off"}:
                active = kernel.set_active_project(None)
            else:
                active = kernel.set_active_project(name)
            console.print(f"[green]Active project: {active or 'none'}[/green]")
            continue
        if request == "/policy":
            console.print(f"Model policy is {'enabled' if kernel.policy.enabled else 'disabled'}.")
            continue
        if request.startswith("/policy "):
            value = request.split(maxsplit=1)[1].strip().lower()
            if value not in {"on", "off"}:
                console.print("[red]Usage: /policy on|off[/red]")
                continue
            kernel.set_policy(value == "on")
            console.print(f"Model policy {'enabled' if kernel.policy.enabled else 'disabled'}.")
            continue
        if request.startswith("/profile "):
            text = request.split(maxsplit=1)[1].strip()
            if not text:
                console.print("[red]Usage: /profile <request>[/red]")
                continue
            print_policy_preview(console, kernel, text)
            continue
        if request == "/help":
            console.print(
                "/status  /models  /profile <request>  /policy on|off\n"
                "/learning  /learn  /evening-report  /native-export\n"
                "/memory  /memories <query>  /remember <text>  /forget <id>  /raw-memory\n/memory-maintain  /contradictions  /relations <memory-id optional>\n"
                "/graph <query>  /graph-rebuild  /node <id>  /provenance <id>  /edge-provenance <edge-id>  /link <src> <relation> <dst>\n"
                "/project [name|clear]\n"
                "/model <planner|executor|critic> <auto|openai|anthropic|ollama|mock> (model optional)\n"
                "/approve <id>  /plan on|off  /critic on|off  exit"
            )
            continue
        if request.startswith("/model "):
            parts = request.split(maxsplit=3)
            if len(parts) < 3:
                console.print("[red]Usage: /model <role> <provider> (model optional)[/red]")
                continue
            role, provider = parts[1], parts[2]
            model = parts[3] if len(parts) == 4 else None
            try:
                resolved = kernel.set_model_role(role, provider, model)
                selected = resolved.get("selected")
                if selected:
                    console.print(f"[green]{role} -> {selected['provider']} / {selected['model']}[/green]")
                else:
                    console.print(f"[yellow]{role} override saved, but no configured provider is currently available.[/yellow]")
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
            continue
        if request.startswith("/approve "):
            approval_id = request.split(maxsplit=1)[1].strip()
            result = kernel.approve(approval_id)
            style = "green" if result["ok"] else "red"
            console.print(f"[{style}]{result['message']}[/{style}]")
            continue
        if request.startswith("/plan "):
            value = request.split(maxsplit=1)[1].strip().lower()
            kernel.planner.enabled = value == "on"
            console.print(f"Planner {'enabled' if kernel.planner.enabled else 'disabled'}.")
            continue
        if request.startswith("/critic "):
            value = request.split(maxsplit=1)[1].strip().lower()
            kernel.critic.enabled = value == "on"
            console.print(f"Critic {'enabled' if kernel.critic.enabled else 'disabled'}.")
            continue

        try:
            result = kernel.process(request)
        except Exception as exc:
            console.print(f"[bold red]DaQauntum error:[/bold red] {exc}")
            continue

        cognition = result.get("cognition", {})
        planner_brain = cognition.get("planner", {})
        executor_brain = cognition.get("executor", {})
        critic_brain = cognition.get("critic", {})
        path = (
            f"planner={planner_brain.get('provider')}/{planner_brain.get('model')} -> "
            f"executor={executor_brain.get('provider')}/{executor_brain.get('model')} -> "
            f"critic={critic_brain.get('provider')}/{critic_brain.get('model')}"
        )
        profile = result.get("profile", {})
        console.print(
            f"[dim]agent={result['agent']} | {path} | confidence={result.get('confidence')} | "
            f"privacy={profile.get('privacy')} complexity={profile.get('complexity')} risk={profile.get('risk')} | "
            f"project={result.get('active_project') or '-'} | "
            f"memory+={len(result.get('structured_memory_ids', []))} | "
            f"critic={'pass' if result['critic']['approved'] else 'review'}[/dim]"
        )
        if result["critic"]["issues"]:
            console.print("[dim]critic notes: " + "; ".join(result["critic"]["issues"]) + "[/dim]")
        secondary = result.get("secondary_review", {})
        if secondary.get("status") not in {None, "not_requested"}:
            console.print(f"[dim]secondary review: {secondary.get('status')}[/dim]")
        console.print(f"[bold cyan]DaQauntum >[/bold cyan] {result['response']}\n")


if __name__ == "__main__":
    main()
