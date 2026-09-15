from __future__ import annotations

from copy import deepcopy
from typing import Any


VALID_OPERATION_MODES = ("auto", "local", "hybrid", "cloud")
VALID_COGNITION_MODES = ("auto", "realtime", "balanced", "deep")
DEFAULT_MODULES = {
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
    "perception": True,
    "computer": True,
    "presence": True,
    "demo": True,
}


class RuntimeController:
    """Mutable runtime controls for locality, cognition depth, and optional modules.

    Runtime controls change *how* DaQauntum reasons, never what tool permissions it has.
    Sensitive-task policy remains authoritative and can still force local routing.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.operation_mode = self._operation(cfg.get("operation_mode", "auto"))
        self.cognition_mode = self._cognition(cfg.get("cognition_mode", "auto"))
        self.modules = dict(DEFAULT_MODULES)
        self.modules.update({k: bool(v) for k, v in (cfg.get("modules", {}) or {}).items() if k in self.modules})

    @staticmethod
    def _operation(value: Any) -> str:
        value = str(value or "auto").lower()
        return value if value in VALID_OPERATION_MODES else "auto"

    @staticmethod
    def _cognition(value: Any) -> str:
        value = str(value or "auto").lower()
        return value if value in VALID_COGNITION_MODES else "auto"

    def set_modes(self, operation_mode: str | None = None, cognition_mode: str | None = None) -> dict[str, Any]:
        if operation_mode is not None:
            op = str(operation_mode).lower()
            if op not in VALID_OPERATION_MODES:
                raise ValueError(f"operation_mode must be one of: {', '.join(VALID_OPERATION_MODES)}")
            self.operation_mode = op
        if cognition_mode is not None:
            cog = str(cognition_mode).lower()
            if cog not in VALID_COGNITION_MODES:
                raise ValueError(f"cognition_mode must be one of: {', '.join(VALID_COGNITION_MODES)}")
            self.cognition_mode = cog
        return self.status()

    def set_module(self, name: str, enabled: bool) -> dict[str, Any]:
        key = str(name).lower()
        if key not in self.modules:
            raise ValueError(f"Unknown runtime module: {name}")
        self.modules[key] = bool(enabled)
        return self.status()

    def module_enabled(self, name: str) -> bool:
        return bool(self.modules.get(name, False))

    def effective_cognition(self, interaction_mode: str, complexity: int) -> str:
        if self.cognition_mode != "auto":
            return self.cognition_mode
        if interaction_mode == "voice_call":
            return "realtime"
        if interaction_mode == "call_task":
            return "deep"
        if complexity >= 4:
            return "deep"
        if complexity >= 3:
            return "balanced"
        return "realtime"

    def routing_hint(self, role: str, base: dict[str, Any] | None, cognition: str) -> dict[str, Any]:
        hint = deepcopy(base or {})
        base_allow_hosted = bool(hint.get("allow_hosted", True))

        if self.operation_mode == "local":
            hint["allow_hosted"] = False
            hint["preference"] = ["ollama", "mock"]
        elif self.operation_mode == "hybrid":
            if not base_allow_hosted:
                hint["allow_hosted"] = False
                hint["preference"] = ["ollama", "mock"]
            elif role in {"planner", "critic"}:
                hint["preference"] = ["ollama", "claude_cli", "openai", "anthropic", "mock"]
            else:
                hint["preference"] = ["claude_cli", "openai", "anthropic", "ollama", "mock"]
        elif self.operation_mode == "cloud":
            # Privacy policy may already have disabled hosted inference. Never undo that.
            if base_allow_hosted:
                hint["preference"] = ["claude_cli", "openai", "anthropic", "ollama", "mock"]
            else:
                hint["allow_hosted"] = False
                hint["preference"] = ["ollama", "mock"]

        if cognition == "realtime":
            hint["model_tier"] = "fast"
        elif cognition == "balanced":
            hint["model_tier"] = "balanced"
        elif cognition == "deep":
            hint["model_tier"] = "strong" if role == "executor" else "balanced"
        return hint

    def status(self) -> dict[str, Any]:
        return {
            "operation_mode": self.operation_mode,
            "cognition_mode": self.cognition_mode,
            "modules": dict(self.modules),
            "operation_modes": list(VALID_OPERATION_MODES),
            "cognition_modes": list(VALID_COGNITION_MODES),
        }
