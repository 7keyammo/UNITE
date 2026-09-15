from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.permissions import Action, PermissionManager


@dataclass
class ToolResult:
    tool: str
    ok: bool
    output: str


@dataclass
class ToolSpec:
    name: str
    description: str
    required_level: int
    irreversible: bool
    handler: Callable[[dict[str, Any]], ToolResult]

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required_level": self.required_level,
            "irreversible": self.irreversible,
        }


class ToolRegistry:
    def __init__(self, memory, permission_manager: PermissionManager, project_root: str = ".", notes_dir: str = "data/notes", structured_memory=None, knowledge_graph=None, source_manager=None, integration_manager=None, perception_manager=None, computer_controller=None, presence_manager=None, driver_manager=None, event_system=None):
        self.memory = memory
        self.structured_memory = structured_memory
        self.knowledge_graph = knowledge_graph
        self.source_manager = source_manager
        self.integration_manager = integration_manager
        self.perception_manager = perception_manager
        self.computer_controller = computer_controller
        self.presence_manager = presence_manager
        self.driver_manager = driver_manager
        self.event_system = event_system
        self.permission_manager = permission_manager
        self.project_root = Path(project_root).resolve()
        self.notes_dir = (self.project_root / notes_dir).resolve()
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.tools: dict[str, ToolSpec] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register(ToolSpec("memory_search", "Search DaQauntum's raw and structured local memory.", 0, False, self._memory_search))
        self.register(ToolSpec("knowledge_search", "Search DaQauntum's local provenance-aware knowledge graph.", 0, False, self._knowledge_search))
        self.register(ToolSpec("source_search", "Search indexed local source files, papers, notebooks, datasets, and registered URLs with source/chunk citations.", 0, False, self._source_search))
        self.register(ToolSpec("source_read", "Read a specific indexed source record or source chunk by ID.", 0, False, self._source_read))
        self.register(ToolSpec("list_project_files", "List files inside the DaQauntum project root.", 0, False, self._list_project_files))
        self.register(ToolSpec("read_project_file", "Read a UTF-8 text file inside the DaQauntum project root.", 0, False, self._read_project_file))
        self.register(ToolSpec("write_note", "Create or overwrite a markdown note inside data/notes. Requires approval at default L2.", 3, False, self._write_note))
        self.register(ToolSpec("write_project_file", "Create or overwrite a UTF-8 text file inside the DaQauntum project root. Requires approval.", 3, False, self._write_project_file))
        self.register(ToolSpec("append_project_file", "Append UTF-8 text to a file inside the DaQauntum project root. Requires approval.", 3, False, self._append_project_file))
        self.register(ToolSpec("integration_status", "Inspect availability/health of DaQauntum external integrations.", 0, False, self._integration_status))
        self.register(ToolSpec("open_interpreter_task", "Delegate a coding/computer-use task to Open Interpreter. State-changing mode requires approval.", 3, False, self._open_interpreter_task))
        self.register(ToolSpec("home_assistant_command", "Send a natural-language command to Home Assistant Assist. Requires approval because it can affect physical devices.", 3, False, self._home_assistant_command))
        self.register(ToolSpec("mqtt_read", "Read one message from an explicitly configured MQTT topic. Read-only.", 0, False, self._mqtt_read))
        self.register(ToolSpec("mqtt_publish", "Publish a message to an explicitly configured MQTT topic. Requires approval because it can affect IoT devices.", 3, False, self._mqtt_publish))
        self.register(ToolSpec("postgres_pgvector_bootstrap", "Initialize DaQauntum's optional PostgreSQL/pgvector mirror schema. Requires approval.", 3, False, self._postgres_bootstrap))
        self.register(ToolSpec("tailscale_serve", "Securely expose the local DaQauntum GUI to your Tailscale tailnet using Tailscale Serve. Requires approval.", 3, False, self._tailscale_serve))
        self.register(ToolSpec("perception_latest", "Read metadata/analysis for DaQauntum's latest screen or camera frame.", 0, False, self._perception_latest))
        self.register(ToolSpec("perception_analyze", "Analyze a captured screen/camera frame with the configured local/hosted vision model according to runtime policy.", 0, False, self._perception_analyze))
        self.register(ToolSpec("computer_observe", "Ask the computer-use integration to inspect the current desktop/browser without changing state.", 0, False, self._computer_observe))
        self.register(ToolSpec("computer_action", "Carry out a computer/browser action through DaQauntum's computer-use controller and visually verify the outcome. Requires approval unless L4.", 3, False, self._computer_action))
        self.register(ToolSpec("presence_status", "Read DaQauntum's latest passive environmental awareness: network, battery, temperature, local sensors, cameras, serial devices, and paired Bluetooth metadata.", 0, False, self._presence_status))
        self.register(ToolSpec("wifi_scan", "Explicitly scan nearby Wi-Fi networks without joining them.", 0, False, self._wifi_scan))
        self.register(ToolSpec("bluetooth_scan", "Explicitly scan nearby Bluetooth devices without pairing or connecting.", 0, False, self._bluetooth_scan))
        self.register(ToolSpec("service_discovery", "Explicitly discover local mDNS/Bonjour services visible on the current network.", 0, False, self._service_discovery))
        self.register(ToolSpec("wifi_activate_profile", "Activate an already-saved operating-system Wi-Fi profile. Requires approval.", 3, False, self._wifi_activate_profile))
        self.register(ToolSpec("bluetooth_connect", "Connect an already-paired Bluetooth device. Requires approval.", 3, False, self._bluetooth_connect))
        self.register(ToolSpec("driver_status", "Report which v0.4.1 device drivers are enabled, available, configured and usable.", 0, False, self._driver_status))
        self.register(ToolSpec("driver_discover", "Explicitly enumerate devices, ports, topics or entities one device driver can see. Discovery does not authorize connecting or controlling anything.", 0, False, self._driver_discover))
        self.register(ToolSpec("driver_read", "Read a current value from an explicitly allowlisted device, serial port, MQTT topic or Home Assistant entity.", 0, False, self._driver_read))
        self.register(ToolSpec("driver_write", "Send a command to an allowlisted device through a device driver. Requires approval and the driver must have writes explicitly enabled.", 3, False, self._driver_write))
        self.register(ToolSpec("events_recent", "Read DaQauntum's recent normalized device/presence events.", 0, False, self._events_recent))
        self.register(ToolSpec("notifications_pending", "Read DaQauntum's pending notifications, queued tasks and proposed actions awaiting approval.", 0, False, self._notifications_pending))

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def descriptions(self) -> list[dict[str, Any]]:
        return [spec.describe() for spec in self.tools.values()]

    def get(self, name: str) -> ToolSpec | None:
        return self.tools.get(name)

    def execute(self, name: str, arguments: dict[str, Any], approved: bool = False) -> ToolResult:
        spec = self.get(name)
        if not spec:
            return ToolResult(name, False, f"Unknown tool: {name}")
        action = Action(spec.name, spec.required_level, spec.irreversible)
        decision = self.permission_manager.evaluate(action)
        if decision.outcome == "deny":
            return ToolResult(name, False, f"PERMISSION_DENIED: {decision.reason}")
        if decision.requires_confirmation and not approved:
            return ToolResult(name, False, f"APPROVAL_REQUIRED: {decision.reason}")
        return spec.handler(arguments)


    def _integration_status(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("integration_status", False, "Integration Runtime is not configured.")
        name = str(args.get("name", "")).strip()
        if name:
            data = self.integration_manager.status(name).as_dict()
        else:
            data = self.integration_manager.summary()
        import json
        return ToolResult("integration_status", True, json.dumps(data, indent=2))

    def _open_interpreter_task(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("open_interpreter_task", False, "Integration Runtime is not configured.")
        prompt = str(args.get("prompt", "")).strip()
        if not prompt:
            return ToolResult("open_interpreter_task", False, "Provide a prompt.")
        mode = str(args.get("mode", "workspace_write")).strip().lower()
        try:
            output = self.integration_manager.open_interpreter(prompt, mode="read_only" if mode == "read_only" else "workspace_write")
            return ToolResult("open_interpreter_task", True, output)
        except Exception as exc:
            return ToolResult("open_interpreter_task", False, str(exc))

    def _home_assistant_command(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("home_assistant_command", False, "Integration Runtime is not configured.")
        text = str(args.get("text", "")).strip()
        if not text:
            return ToolResult("home_assistant_command", False, "Provide a Home Assistant command.")
        try:
            result = self.integration_manager.home_assistant_assist(text, conversation_id=args.get("conversation_id"))
            import json
            return ToolResult("home_assistant_command", True, json.dumps(result, indent=2))
        except Exception as exc:
            return ToolResult("home_assistant_command", False, str(exc))

    def _mqtt_read(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("mqtt_read", False, "Integration Runtime is not configured.")
        try:
            return ToolResult("mqtt_read", True, self.integration_manager.mqtt_read_once(str(args.get("topic", "")), timeout=int(args.get("timeout", 5))))
        except Exception as exc:
            return ToolResult("mqtt_read", False, str(exc))

    def _mqtt_publish(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("mqtt_publish", False, "Integration Runtime is not configured.")
        try:
            return ToolResult("mqtt_publish", True, self.integration_manager.mqtt_publish(str(args.get("topic", "")), str(args.get("payload", "")), retain=bool(args.get("retain", False))))
        except Exception as exc:
            return ToolResult("mqtt_publish", False, str(exc))

    def _postgres_bootstrap(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("postgres_pgvector_bootstrap", False, "Integration Runtime is not configured.")
        try:
            return ToolResult("postgres_pgvector_bootstrap", True, self.integration_manager.bootstrap_pgvector())
        except Exception as exc:
            return ToolResult("postgres_pgvector_bootstrap", False, str(exc))

    def _tailscale_serve(self, args: dict[str, Any]) -> ToolResult:
        if self.integration_manager is None:
            return ToolResult("tailscale_serve", False, "Integration Runtime is not configured.")
        try:
            port = int(args.get("port", 8765))
            return ToolResult("tailscale_serve", True, self.integration_manager.tailscale_serve(port, apply=True))
        except Exception as exc:
            return ToolResult("tailscale_serve", False, str(exc))

    def _perception_latest(self, args: dict[str, Any]) -> ToolResult:
        if self.perception_manager is None:
            return ToolResult("perception_latest", False, "Perception is not configured.")
        frame = self.perception_manager.latest(str(args.get("frame_type", "")).strip() or None)
        if not frame:
            return ToolResult("perception_latest", True, "No captured visual frame is available yet.")
        import json
        return ToolResult("perception_latest", True, json.dumps(frame, indent=2, default=str))

    def _perception_analyze(self, args: dict[str, Any]) -> ToolResult:
        if self.perception_manager is None:
            return ToolResult("perception_analyze", False, "Perception is not configured.")
        try:
            frame_id = int(args.get("frame_id") or (self.perception_manager.latest() or {}).get("id"))
        except Exception:
            return ToolResult("perception_analyze", False, "No valid frame_id was provided and no latest frame exists.")
        prompt = str(args.get("prompt", "Describe what is visible and what matters for the user's current task."))
        mode = str(args.get("operation_mode", "auto"))
        try:
            result = self.perception_manager.analyze(frame_id, prompt, operation_mode=mode)
            import json
            return ToolResult("perception_analyze", True, json.dumps(result, indent=2, default=str))
        except Exception as exc:
            return ToolResult("perception_analyze", False, str(exc))

    def _computer_observe(self, args: dict[str, Any]) -> ToolResult:
        if self.computer_controller is None:
            return ToolResult("computer_observe", False, "Computer control is not configured.")
        request = str(args.get("request", "Describe the current screen and application state.")).strip()
        try:
            return ToolResult("computer_observe", True, self.computer_controller.observe(request))
        except Exception as exc:
            return ToolResult("computer_observe", False, str(exc))

    def _computer_action(self, args: dict[str, Any]) -> ToolResult:
        if self.computer_controller is None:
            return ToolResult("computer_action", False, "Computer control is not configured.")
        request = str(args.get("request", "")).strip()
        if not request:
            return ToolResult("computer_action", False, "Provide a computer action request.")
        try:
            result = self.computer_controller.execute(request)
            import json
            return ToolResult("computer_action", bool(result.get("ok")), json.dumps(result, indent=2, default=str))
        except Exception as exc:
            return ToolResult("computer_action", False, str(exc))

    def _presence_status(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("presence_status", False, "Presence Layer is not configured.")
        try:
            import json
            if bool(args.get("refresh", False)):
                data = self.presence_manager.passive_snapshot(save=True)
            else:
                data = self.presence_manager.latest()
            return ToolResult("presence_status", True, json.dumps(data, indent=2, default=str))
        except Exception as exc:
            return ToolResult("presence_status", False, str(exc))

    def _wifi_scan(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("wifi_scan", False, "Presence Layer is not configured.")
        try:
            import json
            return ToolResult("wifi_scan", True, json.dumps(self.presence_manager.scan_wifi(), indent=2))
        except Exception as exc:
            return ToolResult("wifi_scan", False, str(exc))

    def _bluetooth_scan(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("bluetooth_scan", False, "Presence Layer is not configured.")
        try:
            import json
            seconds = int(args.get("seconds", 6))
            return ToolResult("bluetooth_scan", True, json.dumps(self.presence_manager.scan_bluetooth(seconds), indent=2))
        except Exception as exc:
            return ToolResult("bluetooth_scan", False, str(exc))

    def _service_discovery(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("service_discovery", False, "Presence Layer is not configured.")
        try:
            import json
            return ToolResult("service_discovery", True, json.dumps(self.presence_manager.discover_services(), indent=2))
        except Exception as exc:
            return ToolResult("service_discovery", False, str(exc))

    def _wifi_activate_profile(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("wifi_activate_profile", False, "Presence Layer is not configured.")
        try:
            return ToolResult("wifi_activate_profile", True, self.presence_manager.activate_wifi_profile(str(args.get("profile", ""))))
        except Exception as exc:
            return ToolResult("wifi_activate_profile", False, str(exc))

    def _driver_status(self, args: dict[str, Any]) -> ToolResult:
        if self.driver_manager is None:
            return ToolResult("driver_status", False, "Device drivers are not configured.")
        import json
        name = str(args.get("driver", "")).strip()
        try:
            if name:
                data = self.driver_manager.get(name).status().as_dict()
            else:
                data = self.driver_manager.stats()
            return ToolResult("driver_status", True, json.dumps(data, indent=2, default=str))
        except Exception as exc:
            return ToolResult("driver_status", False, str(exc))

    def _driver_discover(self, args: dict[str, Any]) -> ToolResult:
        """Explicit, read-only enumeration.

        Discovery is a separate step from connecting on purpose: the result
        marks which entries are approved, and an unapproved one stays unusable.
        """
        if self.driver_manager is None:
            return ToolResult("driver_discover", False, "Device drivers are not configured.")
        name = str(args.get("driver", "")).strip()
        if not name:
            return ToolResult("driver_discover", False, "Provide a driver name, for example: serial, ble, mqtt, home_assistant.")
        import json
        try:
            found = self.driver_manager.discover(name)
            return ToolResult(
                "driver_discover",
                True,
                json.dumps(
                    {
                        "driver": name,
                        "found": len(found),
                        "note": "Detected devices are not approved, paired or controllable. Allowlist them in config first.",
                        "items": found[:80],
                    },
                    indent=2,
                    default=str,
                ),
            )
        except Exception as exc:
            return ToolResult("driver_discover", False, str(exc))

    def _driver_read(self, args: dict[str, Any]) -> ToolResult:
        if self.driver_manager is None:
            return ToolResult("driver_read", False, "Device drivers are not configured.")
        name = str(args.get("driver", "")).strip()
        if not name:
            return ToolResult("driver_read", False, "Provide a driver name.")
        target = str(args.get("target", "")).strip() or None
        import json
        try:
            return ToolResult("driver_read", True, json.dumps(self.driver_manager.read(name, target), indent=2, default=str))
        except Exception as exc:
            return ToolResult("driver_read", False, str(exc))

    def _driver_write(self, args: dict[str, Any]) -> ToolResult:
        """Only state-changing driver entry point.

        Reaching this handler means the permission gate already approved the
        call. The driver still applies its own allowlist and allow_writes flag,
        so both the user's permission level and the device's declared scope
        have to agree before anything is sent.
        """
        if self.driver_manager is None:
            return ToolResult("driver_write", False, "Device drivers are not configured.")
        name = str(args.get("driver", "")).strip()
        target = str(args.get("target", "")).strip()
        payload = args.get("payload", args.get("value", ""))
        if not name or not target:
            return ToolResult("driver_write", False, "Provide 'driver' and 'target'.")
        extra = {key: value for key, value in args.items() if key not in {"driver", "target", "payload", "value"}}
        try:
            output = self.driver_manager.write(name, target, payload, **extra)
            self.memory.add_event("driver_write", {"driver": name, "target": target})
            return ToolResult("driver_write", True, output)
        except Exception as exc:
            return ToolResult("driver_write", False, str(exc))

    def _events_recent(self, args: dict[str, Any]) -> ToolResult:
        if self.event_system is None:
            return ToolResult("events_recent", False, "Event bus is not configured.")
        import json
        try:
            events = self.event_system.recent_events(
                limit=int(args.get("limit", 25)),
                source=str(args.get("source", "")).strip() or None,
                kind=str(args.get("kind", "")).strip() or None,
                min_severity=str(args.get("min_severity", "")).strip() or None,
            )
            return ToolResult("events_recent", True, json.dumps(events, indent=2, default=str))
        except Exception as exc:
            return ToolResult("events_recent", False, str(exc))

    def _notifications_pending(self, args: dict[str, Any]) -> ToolResult:
        if self.event_system is None:
            return ToolResult("notifications_pending", False, "Event bus is not configured.")
        import json
        try:
            limit = int(args.get("limit", 20))
            data = {
                "notifications": self.event_system.notifications.list(status="pending", limit=limit),
                "tasks": self.event_system.reactions.list_tasks(status="open", limit=limit),
                "proposals": self.event_system.reactions.list_proposals(status="pending_approval", limit=limit),
                "note": "Proposed actions have not run. They execute only if you approve them.",
            }
            return ToolResult("notifications_pending", True, json.dumps(data, indent=2, default=str))
        except Exception as exc:
            return ToolResult("notifications_pending", False, str(exc))

    def _bluetooth_connect(self, args: dict[str, Any]) -> ToolResult:
        if self.presence_manager is None:
            return ToolResult("bluetooth_connect", False, "Presence Layer is not configured.")
        try:
            return ToolResult("bluetooth_connect", True, self.presence_manager.connect_bluetooth(str(args.get("address", ""))))
        except Exception as exc:
            return ToolResult("bluetooth_connect", False, str(exc))

    def _resolve_safe(self, relative_path: str) -> Path:
        candidate = (self.project_root / relative_path).resolve()
        if candidate != self.project_root and self.project_root not in candidate.parents:
            raise ValueError("Path escapes the DaQauntum project root")
        return candidate

    def _memory_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", "")).strip()
        limit = min(max(int(args.get("limit", 6)), 1), 20)
        if not query:
            return ToolResult("memory_search", True, "No query provided.")

        if self.structured_memory is not None:
            structured = self.structured_memory.retrieve(query, limit=limit)
        else:
            structured = self.memory.search_memories(query, limit=limit)
        raw = list(self.memory.search(query, limit=limit))
        if not structured and not raw:
            return ToolResult("memory_search", True, "No matching memories found.")

        lines: list[str] = []
        if structured:
            lines.append("Structured memory:")
            for item in structured:
                project = f" project={item['project']}" if item.get('project') else ""
                lines.append(
                    f"- [memory:{item['id']} {item['kind']}{project}] {item['title']}: {item['content']}"
                )
        if raw:
            lines.append("Raw conversation memory:")
            lines.extend(f"- [{r['created_at']}] {r['role']}: {r['content']}" for r in raw)
        return ToolResult("memory_search", True, "\n".join(lines))

    def _knowledge_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", "")).strip()
        limit = min(max(int(args.get("limit", 6)), 1), 20)
        if not query:
            return ToolResult("knowledge_search", True, "No query provided.")
        if self.knowledge_graph is None:
            return ToolResult("knowledge_search", False, "Knowledge graph is not configured.")
        nodes = self.knowledge_graph.search(query, limit=limit)
        if not nodes:
            return ToolResult("knowledge_search", True, "No matching knowledge nodes found.")
        lines = []
        for node in nodes:
            neighbors = self.knowledge_graph.neighbors(node["id"], limit=4)
            rel = "; ".join(
                f"{edge['direction']} {edge['relation']} -> {edge['other_node']['node_type']}:{edge['other_node']['name']}"
                for edge in neighbors
            )
            line = f"- [node:{node['id']} {node['node_type']}] {node['name']}"
            if rel:
                line += f" | {rel}"
            lines.append(line)
        return ToolResult("knowledge_search", True, "\n".join(lines))

    def _source_search(self, args: dict[str, Any]) -> ToolResult:
        query = str(args.get("query", "")).strip()
        limit = min(max(int(args.get("limit", 6)), 1), 20)
        if self.source_manager is None:
            return ToolResult("source_search", False, "Source Intelligence is not configured.")
        if not query:
            return ToolResult("source_search", True, "No query provided.")
        results = self.source_manager.search(query, limit=limit)
        if not results:
            return ToolResult("source_search", True, "No matching indexed sources found.")
        lines: list[str] = []
        for item in results:
            source = item["source"]
            chunk = item.get("chunk")
            line = f"- {item['citation']} {source['title']} | {source['locator']}"
            if chunk:
                excerpt = " ".join(chunk["content"].split())
                if len(excerpt) > 700:
                    excerpt = excerpt[:697] + "..."
                line += f" | {excerpt}"
            lines.append(line)
        return ToolResult("source_search", True, "\n".join(lines))

    def _source_read(self, args: dict[str, Any]) -> ToolResult:
        if self.source_manager is None:
            return ToolResult("source_read", False, "Source Intelligence is not configured.")
        chunk_id = args.get("chunk_id")
        source_id = args.get("source_id")
        if chunk_id is not None:
            try:
                chunk = self.source_manager.get_chunk(int(chunk_id))
            except (TypeError, ValueError):
                return ToolResult("source_read", False, "chunk_id must be an integer")
            if not chunk:
                return ToolResult("source_read", False, f"Source chunk #{chunk_id} not found.")
            return ToolResult("source_read", True, f"[source:{chunk['source_id']} chunk:{chunk['id']}] {chunk['content']}")
        if source_id is None:
            return ToolResult("source_read", False, "Provide source_id or chunk_id.")
        try:
            source = self.source_manager.get_source(int(source_id))
        except (TypeError, ValueError):
            return ToolResult("source_read", False, "source_id must be an integer")
        if not source:
            return ToolResult("source_read", False, f"Source #{source_id} not found.")
        chunks = self.source_manager.chunks_for_source(int(source_id), limit=5)
        lines = [f"[source:{source['id']}] {source['title']} | locator={source['locator']} | type={source['source_type']} | sha256={source.get('content_sha256') or 'unfetched'}"]
        for chunk in chunks:
            excerpt = " ".join(chunk["content"].split())
            if len(excerpt) > 1000:
                excerpt = excerpt[:997] + "..."
            lines.append(f"[source:{source['id']} chunk:{chunk['id']}] {excerpt}")
        return ToolResult("source_read", True, "\n".join(lines))

    def _list_project_files(self, args: dict[str, Any]) -> ToolResult:
        relative = str(args.get("relative_path", "."))
        try:
            path = self._resolve_safe(relative)
        except ValueError as exc:
            return ToolResult("list_project_files", False, str(exc))
        if not path.exists() or not path.is_dir():
            return ToolResult("list_project_files", False, f"Directory not found: {relative}")
        items = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:100]
        text = "\n".join(("[dir] " if item.is_dir() else "[file] ") + item.name for item in items)
        return ToolResult("list_project_files", True, text or "Directory is empty.")

    def _read_project_file(self, args: dict[str, Any]) -> ToolResult:
        relative = str(args.get("path", "")).strip()
        try:
            path = self._resolve_safe(relative)
        except ValueError as exc:
            return ToolResult("read_project_file", False, str(exc))
        if not path.exists() or not path.is_file():
            return ToolResult("read_project_file", False, f"File not found: {relative}")
        if path.stat().st_size > 250_000:
            return ToolResult("read_project_file", False, "File is too large for this alpha tool (>250 KB).")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult("read_project_file", False, "This alpha tool only reads UTF-8 text files.")
        return ToolResult("read_project_file", True, content)

    def _write_note(self, args: dict[str, Any]) -> ToolResult:
        raw_name = str(args.get("name", "daqauntum-note")).strip() or "daqauntum-note"
        safe_name = "".join(ch for ch in raw_name if ch.isalnum() or ch in "-_").strip("-_") or "daqauntum-note"
        path = self.notes_dir / f"{safe_name}.md"
        content = str(args.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return ToolResult("write_note", True, f"Saved note: {path.relative_to(self.project_root)}")

    def _write_project_file(self, args: dict[str, Any]) -> ToolResult:
        relative = str(args.get("path", "")).strip()
        if not relative:
            return ToolResult("write_project_file", False, "Provide a path.")
        try:
            path = self._resolve_safe(relative)
        except ValueError as exc:
            return ToolResult("write_project_file", False, str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        path.write_text(content, encoding="utf-8")
        return ToolResult("write_project_file", True, f"Wrote project file: {path.relative_to(self.project_root)}")

    def _append_project_file(self, args: dict[str, Any]) -> ToolResult:
        relative = str(args.get("path", "")).strip()
        if not relative:
            return ToolResult("append_project_file", False, "Provide a path.")
        try:
            path = self._resolve_safe(relative)
        except ValueError as exc:
            return ToolResult("append_project_file", False, str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = str(args.get("content", ""))
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return ToolResult("append_project_file", True, f"Appended project file: {path.relative_to(self.project_root)}")
