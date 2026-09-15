from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class IntegrationStatus:
    name: str
    kind: str
    available: bool
    configured: bool
    healthy: bool
    detail: str
    capabilities: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class IntegrationManager:
    """Optional bridges to proven external systems.

    Integrations never bypass DaQauntum permissions. This class only detects or
    invokes explicitly configured tools. Secrets are referenced by env-var name.
    """

    def __init__(self, config: dict[str, Any] | None = None, project_root: str | Path = "."):
        self.config = config or {}
        self.project_root = Path(project_root).resolve()

    def _cfg(self, name: str) -> dict[str, Any]:
        raw = self.config.get(name, {})
        return raw if isinstance(raw, dict) else {}

    def _which(self, binary: str) -> str | None:
        return shutil.which(binary)

    def _http_json(self, url: str, *, token: str | None = None, method: str = "GET", body: dict[str, Any] | None = None, timeout: float = 5.0) -> Any:
        headers = {"Accept": "application/json", "User-Agent": "DaQauntum/0.4.0"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
            return json.loads(raw.decode("utf-8")) if raw else {}

    def status(self, name: str) -> IntegrationStatus:
        name = name.strip().lower().replace("-", "_")
        fn = getattr(self, f"_status_{name}", None)
        if not fn:
            return IntegrationStatus(name, "unknown", False, False, False, "Unknown integration", [])
        return fn()

    def statuses(self) -> list[dict[str, Any]]:
        names = ["open_interpreter", "langgraph", "flux", "home_assistant", "mqtt", "postgres_pgvector", "tailscale", "obsidian"]
        return [self.status(name).as_dict() for name in names]

    def summary(self) -> dict[str, Any]:
        items = self.statuses()
        return {
            "integrations": items,
            "available": sum(1 for x in items if x["available"]),
            "configured": sum(1 for x in items if x["configured"]),
            "healthy": sum(1 for x in items if x["healthy"]),
        }

    def _status_open_interpreter(self) -> IntegrationStatus:
        binary = str(self._cfg("open_interpreter").get("binary", "interpreter"))
        path = self._which(binary)
        return IntegrationStatus("open_interpreter", "computer_use", bool(path), bool(path), bool(path), path or "interpreter CLI not installed", ["computer_use", "coding", "browser_qa", "native_app_qa", "mcp", "acp"])

    def _status_langgraph(self) -> IntegrationStatus:
        try:
            import langgraph  # type: ignore  # noqa: F401
            ok = True
        except Exception:
            ok = False
        return IntegrationStatus("langgraph", "workflow_engine", ok, ok, ok, "Python package available" if ok else "Optional langgraph package not installed", ["durable_workflows", "human_in_loop", "checkpoints", "streaming"])

    def _status_flux(self) -> IntegrationStatus:
        cfg = self._cfg("flux")
        url = str(cfg.get("mcp_url", "") or os.getenv(str(cfg.get("url_env", "FLUX_MCP_URL")), "")).strip()
        configured = bool(url)
        return IntegrationStatus("flux", "hardware_design_mcp", configured, configured, configured, f"MCP endpoint declared: {url}" if url else "Set FLUX_MCP_URL or config integrations.flux.mcp_url", ["pcb_context", "schematic_design", "pcb_design", "hardware_agent"])

    def _status_home_assistant(self) -> IntegrationStatus:
        cfg = self._cfg("home_assistant")
        base = str(cfg.get("base_url", "") or os.getenv(str(cfg.get("url_env", "HOME_ASSISTANT_URL")), "")).rstrip("/")
        token_env = str(cfg.get("token_env", "HOME_ASSISTANT_TOKEN"))
        token = os.getenv(token_env, "")
        configured = bool(base and token)
        healthy = False
        detail = "Set HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN"
        if configured:
            try:
                result = self._http_json(base + "/api/", token=token, timeout=float(cfg.get("timeout_seconds", 4)))
                healthy = bool(result)
                detail = str(result.get("message", "Home Assistant API reachable")) if isinstance(result, dict) else "Home Assistant API reachable"
            except Exception as exc:
                detail = f"Configured but unreachable: {exc}"
        return IntegrationStatus("home_assistant", "physical_world", configured, configured, healthy, detail, ["assist", "entities", "automations", "iot", "sensors"])

    def _status_mqtt(self) -> IntegrationStatus:
        cfg = self._cfg("mqtt")
        host = os.getenv(str(cfg.get("host_env", "MQTT_HOST")), "").strip()
        sub = self._which(str(cfg.get("sub_binary", "mosquitto_sub")))
        pub = self._which(str(cfg.get("pub_binary", "mosquitto_pub")))
        available = bool(sub and pub)
        configured = bool(host)
        detail = f"broker={host}" if configured else "Set MQTT_HOST; install mosquitto-clients for generic IoT pub/sub"
        return IntegrationStatus("mqtt", "iot_message_bus", available, configured, available and configured, detail, ["read_retained", "publish", "sensors", "esp32", "raspberry_pi", "iot"])

    def _status_postgres_pgvector(self) -> IntegrationStatus:
        cfg = self._cfg("postgres_pgvector")
        dsn_env = str(cfg.get("dsn_env", "DAQAUNTUM_POSTGRES_DSN"))
        dsn = str(cfg.get("dsn", "") or os.getenv(dsn_env, "")).strip()
        try:
            import psycopg  # type: ignore
            package = True
        except Exception:
            psycopg = None  # type: ignore
            package = False
        configured = bool(dsn)
        healthy = False
        detail = "Install psycopg and set DAQAUNTUM_POSTGRES_DSN"
        if package and configured:
            try:
                with psycopg.connect(dsn, connect_timeout=int(cfg.get("timeout_seconds", 4))) as conn:  # type: ignore[attr-defined]
                    with conn.cursor() as cur:
                        cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
                        has_vector = bool(cur.fetchone()[0])
                healthy = has_vector
                detail = "PostgreSQL reachable; pgvector enabled" if has_vector else "PostgreSQL reachable; pgvector extension not enabled"
            except Exception as exc:
                detail = f"Configured but unavailable: {exc}"
        return IntegrationStatus("postgres_pgvector", "memory_backend", package, configured, healthy, detail, ["sql_memory", "vector_search", "hybrid_search", "scaling"])

    def _status_tailscale(self) -> IntegrationStatus:
        binary = str(self._cfg("tailscale").get("binary", "tailscale"))
        path = self._which(binary)
        healthy = False
        detail = path or "tailscale CLI not installed"
        if path:
            try:
                proc = subprocess.run([binary, "status", "--json"], capture_output=True, text=True, timeout=5)
                healthy = proc.returncode == 0
                if healthy:
                    data = json.loads(proc.stdout or "{}")
                    detail = f"Tailscale connected: {data.get('Self', {}).get('DNSName') or data.get('Self', {}).get('HostName') or 'node'}"
                else:
                    detail = (proc.stderr or proc.stdout or "Tailscale not connected").strip()[:300]
            except Exception as exc:
                detail = str(exc)
        return IntegrationStatus("tailscale", "secure_remote_access", bool(path), bool(path), healthy, detail, ["tailnet_access", "https_serve", "device_identity"])

    def _status_obsidian(self) -> IntegrationStatus:
        cfg = self._cfg("obsidian")
        path = Path(str(cfg.get("vault_path", "data/obsidian/DaQauntum"))).expanduser()
        exists = path.exists()
        return IntegrationStatus("obsidian", "knowledge_interface", True, True, exists, f"Vault path: {path}", ["markdown_vault", "bidirectional_folder_sync", "graph_view"])

    def open_interpreter(self, prompt: str, *, mode: str = "read_only", timeout: int = 300) -> str:
        cfg = self._cfg("open_interpreter")
        binary = str(cfg.get("binary", "interpreter"))
        if not self._which(binary):
            raise RuntimeError("Open Interpreter is not installed")
        sandbox = "read-only" if mode == "read_only" else "workspace-write"
        approval = "untrusted" if mode == "read_only" else "on-request"
        cmd = [binary, "exec", "--ephemeral", "--sandbox", sandbox, "--ask-for-approval", approval, "--cd", str(self.project_root), prompt]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "Open Interpreter failed").strip()[:4000])
        return proc.stdout.strip()

    def home_assistant_assist(self, text: str, conversation_id: str | None = None) -> dict[str, Any]:
        cfg = self._cfg("home_assistant")
        base = str(cfg.get("base_url", "") or os.getenv(str(cfg.get("url_env", "HOME_ASSISTANT_URL")), "")).rstrip("/")
        token = os.getenv(str(cfg.get("token_env", "HOME_ASSISTANT_TOKEN")), "")
        if not base or not token:
            raise RuntimeError("Home Assistant is not configured")
        body: dict[str, Any] = {"text": text, "language": str(cfg.get("language", "en"))}
        if conversation_id:
            body["conversation_id"] = conversation_id
        return self._http_json(base + "/api/conversation/process", token=token, method="POST", body=body, timeout=float(cfg.get("timeout_seconds", 20)))

    def _mqtt_cmd_base(self, binary_key: str) -> list[str]:
        cfg = self._cfg("mqtt")
        host = os.getenv(str(cfg.get("host_env", "MQTT_HOST")), "").strip()
        if not host:
            raise RuntimeError("MQTT_HOST is not configured")
        binary = str(cfg.get(binary_key, "mosquitto_sub" if binary_key == "sub_binary" else "mosquitto_pub"))
        if not self._which(binary):
            raise RuntimeError(f"{binary} is not installed")
        port = os.getenv(str(cfg.get("port_env", "MQTT_PORT")), "1883").strip() or "1883"
        cmd = [binary, "-h", host, "-p", str(int(port))]
        username = os.getenv(str(cfg.get("username_env", "MQTT_USERNAME")), "").strip()
        password = os.getenv(str(cfg.get("password_env", "MQTT_PASSWORD")), "")
        if username:
            cmd += ["-u", username]
        if password:
            cmd += ["-P", password]
        return cmd

    def mqtt_read_once(self, topic: str, *, timeout: int = 5) -> str:
        topic = str(topic or "").strip()
        if not topic:
            raise ValueError("MQTT topic is required")
        cmd = self._mqtt_cmd_base("sub_binary") + ["-t", topic, "-C", "1", "-W", str(max(1, min(int(timeout), 30)))]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(3, min(int(timeout) + 3, 35)))
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "MQTT read failed").strip())
        return proc.stdout.strip()

    def mqtt_publish(self, topic: str, payload: str, *, retain: bool = False) -> str:
        topic = str(topic or "").strip()
        if not topic:
            raise ValueError("MQTT topic is required")
        cmd = self._mqtt_cmd_base("pub_binary") + ["-t", topic, "-m", str(payload)]
        if retain:
            cmd.append("-r")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "MQTT publish failed").strip())
        return f"Published MQTT message to {topic}"

    def bootstrap_pgvector(self) -> str:
        cfg = self._cfg("postgres_pgvector")
        dsn = str(cfg.get("dsn", "") or os.getenv(str(cfg.get("dsn_env", "DAQAUNTUM_POSTGRES_DSN")), "")).strip()
        if not dsn:
            raise RuntimeError("DAQAUNTUM_POSTGRES_DSN is not configured")
        try:
            import psycopg  # type: ignore
        except Exception as exc:
            raise RuntimeError("Install psycopg[binary] first") from exc
        schema = """
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS daqauntum_memory_mirror (
          id BIGSERIAL PRIMARY KEY,
          local_id BIGINT UNIQUE,
          kind TEXT NOT NULL,
          project TEXT,
          content TEXT NOT NULL,
          metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS daqauntum_memory_mirror_kind_idx ON daqauntum_memory_mirror(kind);
        """
        with psycopg.connect(dsn) as conn:  # type: ignore[attr-defined]
            with conn.cursor() as cur:
                cur.execute(schema)
        return "PostgreSQL/pgvector backend schema initialized"

    def tailscale_serve(self, port: int, *, apply: bool = False) -> str:
        cfg = self._cfg("tailscale")
        binary = str(cfg.get("binary", "tailscale"))
        if not self._which(binary):
            raise RuntimeError("tailscale CLI is not installed")
        cmd = [binary, "serve", "--bg", str(int(port))]
        if not apply:
            return "DRY RUN: " + " ".join(cmd)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "tailscale serve failed").strip())
        return (proc.stdout or "Tailscale Serve enabled").strip()
