from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "name": "DaQauntum",
    "version": "0.5.0",
    "permission_level": 2,
    "models": {
        "fallback_to_mock": True,
        "providers": {
            "claude_cli": {"model": "sonnet", "models": {"fast": "sonnet", "balanced": "sonnet", "strong": "opus"}, "binary": "claude", "timeout_seconds": 120},
            "openai": {"model": "gpt-5.6-luna", "models": {"fast": "gpt-5.6-luna", "balanced": "gpt-5.6-terra", "strong": "gpt-5.6-sol"}},
            "anthropic": {
                "model": "claude-sonnet-5",
                "models": {"fast": "claude-haiku-4-5-20251001", "balanced": "claude-sonnet-5", "strong": "claude-opus-5"},
                "base_url": "https://api.anthropic.com",
                "max_tokens": 4096,
                "timeout_seconds": 120,
            },
            "ollama": {
                "model": "gemma3",
                "models": {"fast": "gemma3", "balanced": "gemma3", "strong": "gemma3"},
                "base_url": "http://localhost:11434",
                "timeout_seconds": 120,
            },
            "mock": {"model": "daqauntum-mock"},
        },
        "roles": {
            "planner": {
                "provider": "auto",
                "preference": ["ollama", "claude_cli", "openai", "anthropic", "mock"],
            },
            "executor": {
                "provider": "auto",
                "preference": ["claude_cli", "openai", "anthropic", "ollama", "mock"],
            },
            "critic": {
                "provider": "auto",
                "preference": ["ollama", "claude_cli", "anthropic", "openai", "mock"],
            },
        },
    },
    "cognition": {
        "planner_enabled": True,
        "critic_enabled": True,
        "max_revision_rounds": 1,
    },
    "runtime": {
        "operation_mode": "auto",
        "cognition_mode": "auto",
        "modules": {
            "memory": True,
            "knowledge": True,
            "sources": True,
            "planner": True,
            "critic": True,
            "voice": True,
            "streaming": True,
            "barge_in": True,
            "duplex": True,
            "learning": True,
            "connectors": True,
            "workspaces": True,
            "workbench": True,
            "integrations": True,
            "server": True,
            "perception": True,
            "computer": True,
            "presence": True,
            "demo": True,
        },
    },
    "realtime": {
        "streaming": True,
        "barge_in": True,
        "voice_activity": {"rms_threshold": 0.018, "silence_ms": 700, "min_turn_ms": 500, "max_turn_ms": 30000},
        "tts_sentence_chars": 170,
    },
    "model_policy": {
        "enabled": True,
        "local_only_for_sensitive": True,
        "second_opinion_enabled": True,
        "second_opinion_complexity": 4,
        "confidence_threshold": 0.65,
        "hosted_providers": ["claude_cli", "openai", "anthropic"],
        "local_providers": ["ollama", "mock"],
    },
    "memory": {
        "db_path": "data/daqauntum.db",
        "max_recent_messages": 12,
        "structured_enabled": True,
        "structured_retrieve_limit": 6,
        "auto_episode": True,
        "auto_decisions": True,
        "auto_procedures": True,
        "auto_semantic_cues": True,
        "training_confidence_threshold": 0.85,
        "intelligence_enabled": True,
        "embedding_dimensions": 256,
        "semantic_weight": 2.2,
        "lexical_weight": 1.5,
        "salience_weight": 1.1,
        "recency_weight": 0.7,
        "consolidation_similarity": 0.50,
        "consolidation_min_cluster": 3,
        "contradiction_similarity": 0.58,
        "auto_maintain_every": 20,
    },
    "knowledge_graph": {
        "enabled": True,
        "context_limit": 5,
        "backfill_on_start": True,
        "max_backfill": 10000,
    },
    "sources": {
        "enabled": True,
        "project_root": ".",
        "max_file_bytes": 20000000,
        "chunk_chars": 1800,
        "chunk_overlap": 200,
        "context_limit": 4,
        "allow_external_paths": False,
        "allow_url_fetch": False,
        "allow_private_networks": False,
        "max_url_bytes": 5000000,
        "user_agent": "DaQauntum/0.4.0 Presence + Demo",
    },
    "perception": {
        "enabled": True,
        "frames_dir": "data/perception/frames",
        "max_frame_bytes": 8000000,
        "ollama_model": "",
        "ollama_base_url": "http://127.0.0.1:11434",
        "openai_model": "gpt-5.6-sol",
        "anthropic_model": "claude-sonnet-5",
        "timeout_seconds": 90,
    },
    "computer": {
        "enabled": True,
        "autonomy": "observe",
    },
    "presence": {
        "enabled": True,
        "sample_interval_seconds": 20,
        "context_always": False,
        "low_battery_percent": 15,
        "high_temperature_celsius": 85,
        "radio_scan_timeout_seconds": 12,
        "bluetooth_scan_seconds": 6,
        "service_scan_timeout_seconds": 8,
    },
    "events": {
        "enabled": True,
        "presence_bridge": True,
        "bus": {
            "enabled": True,
            "debounce_seconds": 30,
            "cooldown_seconds": 0,
            "min_severity": "debug",
            "max_events": 5000,
            # Per-kind overrides win over the defaults above. Keys may be
            # "<source>:<kind>", "<kind>" or "<source>", matched in that order.
            "per_kind": {
                "low_battery": {"cooldown_seconds": 900},
                "high_temperature": {"cooldown_seconds": 600},
                "sensor_reading": {"debounce_seconds": 60},
            },
        },
        "reactions": {
            "enabled": True,
            "builtin_rules": True,
            "default_cooldown_seconds": 900,
            "max_reactions_per_event": 8,
        },
        "notifications": {
            "enabled": True,
            "min_severity": "info",
            "max_notifications": 1000,
        },
        # Outbound push is off by default: delivering a notification to an
        # external service takes it off this machine. Put secrets in the
        # environment and reference them as ${VAR} here.
        "push": {
            "enabled": False,
            "url": "",
            "method": "POST",
            "headers": {},
            "template": None,
            "min_severity": "warning",
            "include_body": True,
            "timeout_seconds": 8,
        },
    },
    "obsidian": {
        "enabled": True,
        # The vault DaQauntum writes into and reads your notes from. Point this
        # at your real Obsidian vault to use it as long-term memory.
        "vault_root": "data/obsidian/DaQauntum",
        "export_limit": 2000,
        "import_limit": 1000,
        "min_export_confidence": 0.0,
    },
    "science": {
        "enabled": True,
        # Plots are files a user opens and attaches, so they live on disk
        # beside the rest of the workspace data rather than in the database.
        "artifacts_dir": "data/science/artifacts",
    },
    "native_model": {
        # No project_root here on purpose: the lab inherits tools.project_root so
        # there is exactly one project root. A "." default here silently won the
        # deep merge and resolved relative dataset paths against the process
        # working directory instead.
        "dataset_dir": "data/native_model/datasets",
        "runs_dir": "data/native_model/runs",
        "models_dir": "data/native_model/models",
        "curation": {
            "min_confidence": 0.6,
            "min_user_chars": 8,
            "min_assistant_chars": 16,
            "max_chars": 24000,
            "near_duplicate_threshold": 0.85,
            "max_per_category": 0,
            "seed": 20260915,
            "splits": {"train": 0.8, "validation": 0.1, "test": 0.1},
        },
        "gates": {"overall": 0.35, "safety": 1.0, "per_category": 0.2},
        # Kept for compatibility with v0.4.0 status surfaces. DaQauntum never
        # fine-tunes itself; training is scripts/train_native_model.py only.
        "fine_tuning_enabled": False,
    },
    "identity": {
        "enabled": True,
        # Loopback is the trusted control surface and stays unauthenticated by
        # default, exactly as in v0.4.0. Anything arriving from another address
        # must present an enrolled device token.
        "require_auth_for_remote": True,
        "require_auth_for_loopback": False,
        "enrollment_code_ttl_seconds": 600,
        "token_ttl_seconds": 2592000,
        "max_devices": 50,
        "max_auth_failures": 8,
        "auth_failure_window_seconds": 300,
    },
    "drivers": {
        "enabled": True,
        # Every driver is opt-in and starts disabled. Discovering a device
        # never authorizes connecting to it or writing to it.
        "poll_interval_seconds": 30,
        "ble": {
            "enabled": False,
            "adapter": None,
            "scan_seconds": 6,
            "allow_devices": [],
            "profiles": {},
            "allow_writes": False,
        },
        "serial": {
            "enabled": False,
            "allow_ports": [],
            "baudrate": 9600,
            "timeout_seconds": 2,
            "parser": "line",
            "allow_writes": False,
        },
        "mqtt": {
            "enabled": False,
            "host": "127.0.0.1",
            "port": 1883,
            "topics": [],
            "client_id": "daqauntum",
            "keepalive_seconds": 60,
            "max_cached_topics": 200,
        },
        "home_assistant": {
            "enabled": False,
            "poll_interval_seconds": 60,
            "entities": [],
            "max_entities": 200,
        },
    },
    "demo": {
        "enabled": True,
        "speak_steps": True,
        "auto_open_on_first_run": True,
    },
    "tools": {"project_root": ".", "notes_dir": "data/notes"},
    "gui": {"host": "127.0.0.1", "port": 8765, "websocket_port": 8766, "device_bridge_port": 8767, "open_browser": True},
    "connectors": {
        "enabled": True,
        "device_inbox_dir": "data/device_inbox",
        "max_files_per_sync": 500,
        "max_feed_items": 20,
        "ignore_dirs": [".git", ".venv", "node_modules", "__pycache__"],
        "sync_interval_minutes": 15,
    },
    "device_bridge": {
        "enabled": False,
        "host": "0.0.0.0",
        "port": 8767,
        "max_upload_bytes": 20000000,
        "pairing_ttl_seconds": 900,
        "session_ttl_seconds": 86400,
    },
    "call": {
        "calls_dir": "data/calls",
        "max_transcript_chars": 24000,
        "save_transcripts": True,
        "auto_run_tasks": False,
    },
    "full_duplex": {
        "enabled": True,
        "partial_stt_enabled": True,
        "partial_stt_interval_ms": 900,
        "partial_stt_min_ms": 650,
        "keep_recent_sessions": 32,
        "max_audio_bytes": 8000000,
    },
    "voice": {
        "prefer_local": True,
        "max_audio_bytes": 8000000,
        "max_speak_chars": 4000,
        "stt": {
            "backend": "auto",
            "faster_whisper": {
                "model": "base.en",
                "device": "cpu",
                "compute_type": "int8",
                "language": "en",
                "beam_size": 3,
                "realtime_beam_size": 1,
                "vad_filter": True,
                "allow_model_download": False,
                "download_root": "data/models/whisper",
            },
            "whisper_cpp": {
                "binary": "whisper-cli",
                "model": "",
                "timeout_seconds": 120,
            },
        },
        "tts": {
            "backend": "auto",
            "system": {"voice": "", "timeout_seconds": 90},
            "piper": {
                "binary": "piper",
                "model": "",
                "allow_model_download": False,
                "cuda": False,
                "timeout_seconds": 120,
            },
        },
        "wake": {"enabled": False, "phrase": "daqauntum"},
    },
    "learning": {
        "enabled": True,
        "project_root": ".",
        "reports_dir": "data/learning/reports",
        "outbox_dir": "data/learning/outbox",
        "operation_mode": "auto",
        "morning_hour": 7,
        "evening_hour": 19,
        "auto_sync_connected_sources": True,
    },
    "workspaces": {
        "enabled": True,
        "root": "data/workspaces",
        "obsidian_root": "data/obsidian/DaQauntum",
        "max_parallel_agents": 4,
        "default_stage": "manual",
    },
    "integrations": {
        "enabled": True,
        "open_interpreter": {"enabled": True, "binary": "interpreter"},
        "langgraph": {"enabled": True},
        "flux": {"enabled": True, "url_env": "FLUX_MCP_URL", "mcp_url": ""},
        "home_assistant": {"enabled": True, "url_env": "HOME_ASSISTANT_URL", "token_env": "HOME_ASSISTANT_TOKEN", "language": "en", "timeout_seconds": 20},
        "postgres_pgvector": {"enabled": True, "dsn_env": "DAQAUNTUM_POSTGRES_DSN", "dsn": "", "timeout_seconds": 4},
        "tailscale": {"enabled": True, "binary": "tailscale"},
        "obsidian": {"enabled": True, "vault_path": "data/obsidian/DaQauntum"},
        "mqtt": {"enabled": True, "host_env": "MQTT_HOST", "port_env": "MQTT_PORT", "username_env": "MQTT_USERNAME", "password_env": "MQTT_PASSWORD", "sub_binary": "mosquitto_sub", "pub_binary": "mosquitto_pub"},
    },
    "server": {
        "persistent": True,
        "install_user_service": True,
        "prefer_tailscale_serve": True,
        "public_funnel_enabled": False,
    },
    "logging": {"level": "INFO"},
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_legacy_model_config(config: dict[str, Any]) -> dict[str, Any]:
    """Map v0.2/v0.2.1 single-model config into v0.2.3 provider defaults.

    Legacy config remains valid. Its provider becomes the executor preference first,
    while planner/critic keep role-specific fallbacks unless explicitly configured.
    """
    legacy = config.get("model")
    if not isinstance(legacy, dict):
        return config

    models = config.setdefault("models", deepcopy(DEFAULT_CONFIG["models"]))
    models["fallback_to_mock"] = bool(legacy.get("fallback_to_mock", models.get("fallback_to_mock", True)))
    for provider in ("openai", "ollama"):
        if isinstance(legacy.get(provider), dict):
            models.setdefault("providers", {}).setdefault(provider, {}).update(legacy[provider])

    provider = str(legacy.get("provider", "auto")).lower()
    if provider != "auto":
        models.setdefault("roles", {}).setdefault("executor", {})["provider"] = provider
    return config


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    candidate = Path(path or os.getenv("DAQAUNTUM_CONFIG", "config.yaml"))
    loaded: dict[str, Any] = {}
    if candidate.exists():
        loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
        config = _deep_merge(config, loaded)
        if "model" in loaded and "models" not in loaded:
            config = _normalize_legacy_model_config(config)

    # Provider defaults.
    if os.getenv("DAQAUNTUM_OPENAI_MODEL"):
        config["models"]["providers"]["openai"]["model"] = os.environ["DAQAUNTUM_OPENAI_MODEL"]
    if os.getenv("DAQAUNTUM_ANTHROPIC_MODEL"):
        config["models"]["providers"]["anthropic"]["model"] = os.environ["DAQAUNTUM_ANTHROPIC_MODEL"]
    if os.getenv("DAQAUNTUM_OLLAMA_MODEL"):
        config["models"]["providers"]["ollama"]["model"] = os.environ["DAQAUNTUM_OLLAMA_MODEL"]
    if os.getenv("DAQAUNTUM_OLLAMA_URL"):
        config["models"]["providers"]["ollama"]["base_url"] = os.environ["DAQAUNTUM_OLLAMA_URL"]

    # Model policy environment overrides.
    def env_bool(name: str) -> bool | None:
        value = os.getenv(name)
        if value is None:
            return None
        return value.strip().lower() in {"1", "true", "yes", "on"}

    policy_enabled = env_bool("DAQAUNTUM_POLICY_ENABLED")
    if policy_enabled is not None:
        config["model_policy"]["enabled"] = policy_enabled
    local_sensitive = env_bool("DAQAUNTUM_LOCAL_ONLY_SENSITIVE")
    if local_sensitive is not None:
        config["model_policy"]["local_only_for_sensitive"] = local_sensitive
    second_opinion = env_bool("DAQAUNTUM_SECOND_OPINION")
    if second_opinion is not None:
        config["model_policy"]["second_opinion_enabled"] = second_opinion

    # Legacy global provider override still works and now targets all three roles.
    if os.getenv("DAQAUNTUM_PROVIDER"):
        provider = os.environ["DAQAUNTUM_PROVIDER"]
        for role in ("planner", "executor", "critic"):
            config["models"]["roles"][role]["provider"] = provider

    # New role-specific overrides win over the legacy global setting.
    for role in ("planner", "executor", "critic"):
        env_name = f"DAQAUNTUM_{role.upper()}_PROVIDER"
        model_env = f"DAQAUNTUM_{role.upper()}_MODEL"
        if os.getenv(env_name):
            config["models"]["roles"][role]["provider"] = os.environ[env_name]
        if os.getenv(model_env):
            config["models"]["roles"][role]["model"] = os.environ[model_env]

    return config
