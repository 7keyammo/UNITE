from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator


VALID_ROLES = ("planner", "executor", "critic")
VALID_PROVIDERS = ("claude_cli", "openai", "anthropic", "ollama", "mock")


@dataclass
class BrainResponse:
    text: str
    provider: str
    model: str
    role: str
    fallback_used: bool = False


@dataclass
class ProviderStatus:
    provider: str
    available: bool
    detail: str


class BrainAdapter:
    provider = "base"
    model = "base"

    def available(self) -> ProviderStatus:
        return ProviderStatus(self.provider, True, "available")

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        raise NotImplementedError

    def stream_generate(
        self,
        system: str,
        messages: list[dict[str, str]],
        role: str,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """Yield response text incrementally. Providers may override with native streaming."""
        response = self.generate(system, messages, role)
        text = response.text or ""
        # Fallback streaming still makes the transport/UI uniform, even when a provider
        # does not expose token streaming in the installed client.
        step = 48
        for i in range(0, len(text), step):
            if cancelled and cancelled():
                break
            yield text[i : i + step]


class MockBrain(BrainAdapter):
    provider = "mock"
    model = "daqauntum-mock"

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        latest = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        if role == "planner":
            text = json.dumps(
                {
                    "goal": latest[:160] or "Handle the request",
                    "steps": [
                        {
                            "id": "s1",
                            "description": "Use the selected specialist workflow",
                            "tool": None,
                            "arguments": {},
                            "rationale": "Offline deterministic planning fallback",
                        },
                        {
                            "id": "s2",
                            "description": "Synthesize a direct response",
                            "tool": None,
                            "arguments": {},
                            "rationale": "Complete the request without external inference",
                        },
                    ],
                }
            )
        elif role == "critic":
            text = json.dumps({"approved": True, "issues": [], "improved_answer": None})
        else:
            user_request = latest
            if "USER REQUEST\n" in latest:
                user_request = latest.split("USER REQUEST\n", 1)[1].split("\n\n", 1)[0].strip()
            text = (
                "DaQauntum Alpha v0.4.0 is running in offline mock mode. "
                f"Executor received: '{user_request}'. Multi-brain routing, specialist execution, memory, "
                "tools, permissions, planning, and critic routing are active."
            )
        return BrainResponse(text=text, provider=self.provider, model=self.model, role=role)

    def stream_generate(self, system: str, messages: list[dict[str, str]], role: str, cancelled=None) -> Iterator[str]:
        text = self.generate(system, messages, role).text
        words = text.split()
        for index, word in enumerate(words):
            if cancelled and cancelled():
                break
            yield ("" if index == 0 else " ") + word


class ClaudeCLIBrain(BrainAdapter):
    """Use an authenticated Claude Code CLI as a conversational brain.

    The subprocess is constrained to plan mode, one turn, and a dedicated empty working
    directory. This adapter is classified as hosted inference by DaQauntum policy even
    though the CLI process itself runs locally.
    """

    provider = "claude_cli"

    def __init__(self, model: str = "sonnet", binary: str = "claude", timeout_seconds: int = 120):
        self.model = model or "sonnet"
        self.binary = binary
        self.timeout_seconds = int(timeout_seconds)

    def available(self) -> ProviderStatus:
        path = shutil.which(self.binary)
        return ProviderStatus(self.provider, bool(path), path or f"{self.binary} CLI not installed")

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        if not shutil.which(self.binary):
            raise RuntimeError(f"{self.binary} CLI is not installed")
        conversation = []
        for msg in messages:
            if msg.get("role") in {"user", "assistant"}:
                conversation.append(f"{msg.get('role','user').upper()}: {msg.get('content','')}")
        prompt = "\n\n".join(conversation) or "Respond to the current DaQauntum request."
        with tempfile.TemporaryDirectory(prefix="daqauntum-claude-brain-") as td:
            cmd = [
                self.binary, "-p", prompt,
                "--output-format", "text",
                "--permission-mode", "plan",
                "--max-turns", "1",
                "--model", self.model,
                "--system-prompt", system,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds, cwd=td)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "Claude CLI failed").strip()[:4000])
        return BrainResponse(text=proc.stdout.strip(), provider=self.provider, model=self.model, role=role)


class OpenAIBrain(BrainAdapter):
    provider = "openai"

    def __init__(self, model: str):
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed") from exc
        self.model = model
        self.client = OpenAI()

    def available(self) -> ProviderStatus:
        configured = bool(os.getenv("OPENAI_API_KEY"))
        return ProviderStatus(self.provider, configured, "API key configured" if configured else "OPENAI_API_KEY not set")

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        response = self.client.responses.create(
            model=self.model,
            instructions=system,
            input=messages,
        )
        return BrainResponse(
            text=response.output_text or "",
            provider=self.provider,
            model=self.model,
            role=role,
        )

    def stream_generate(self, system: str, messages: list[dict[str, str]], role: str, cancelled=None) -> Iterator[str]:
        stream_method = getattr(self.client.responses, "stream", None)
        if not callable(stream_method):
            yield from super().stream_generate(system, messages, role, cancelled)
            return
        emitted = False
        try:
            with stream_method(model=self.model, instructions=system, input=messages) as stream:
                for event in stream:
                    if cancelled and cancelled():
                        break
                    event_type = getattr(event, "type", "")
                    if event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            emitted = True
                            yield str(delta)
        except Exception:
            # Client versions differ; preserve functionality if streaming failed before output.
            if cancelled and cancelled():
                return
            if emitted:
                raise
            yield from super().stream_generate(system, messages, role, cancelled)


class AnthropicBrain(BrainAdapter):
    provider = "anthropic"

    def __init__(
        self,
        model: str,
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 4096,
        timeout_seconds: int = 120,
    ):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.timeout_seconds = timeout_seconds

    def available(self) -> ProviderStatus:
        configured = bool(os.getenv("ANTHROPIC_API_KEY"))
        return ProviderStatus(self.provider, configured, "API key configured" if configured else "ANTHROPIC_API_KEY not set")

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        payload_messages = [
            {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
            for m in messages
            if m.get("role") in {"user", "assistant"}
        ]
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": payload_messages,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Anthropic HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach Anthropic API: {exc}") from exc

        blocks = data.get("content", [])
        text = "".join(str(block.get("text", "")) for block in blocks if block.get("type") == "text")
        return BrainResponse(text=text, provider=self.provider, model=self.model, role=role)

    def stream_generate(self, system: str, messages: list[dict[str, str]], role: str, cancelled=None) -> Iterator[str]:
        payload_messages = [
            {"role": m.get("role", "user"), "content": str(m.get("content", ""))}
            for m in messages if m.get("role") in {"user", "assistant"}
        ]
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": payload_messages,
            "stream": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/messages", data=body,
            headers={"Content-Type": "application/json", "x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            method="POST",
        )
        emitted = False
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for raw in response:
                    if cancelled and cancelled():
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if not data_text or data_text == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            emitted = True
                            yield str(delta["text"])
        except (urllib.error.HTTPError, urllib.error.URLError):
            if cancelled and cancelled():
                return
            if emitted:
                raise
            yield from super().stream_generate(system, messages, role, cancelled)


class OllamaBrain(BrainAdapter):
    provider = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434", timeout_seconds: int = 120):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def available(self, timeout_seconds: float = 1.5) -> ProviderStatus:
        request = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                ok = 200 <= response.status < 300
                return ProviderStatus(self.provider, ok, f"reachable at {self.base_url}" if ok else f"HTTP {response.status}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            return ProviderStatus(self.provider, False, f"not reachable at {self.base_url}: {exc}")

    def generate(self, system: str, messages: list[dict[str, str]], role: str) -> BrainResponse:
        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        body = json.dumps({"model": self.model, "messages": payload_messages, "stream": False}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data: dict[str, Any] = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        content = data.get("message", {}).get("content", "")
        return BrainResponse(text=content, provider=self.provider, model=self.model, role=role)

    def stream_generate(self, system: str, messages: list[dict[str, str]], role: str, cancelled=None) -> Iterator[str]:
        payload_messages: list[dict[str, str]] = []
        if system:
            payload_messages.append({"role": "system", "content": system})
        payload_messages.extend(messages)
        body = json.dumps({"model": self.model, "messages": payload_messages, "stream": True}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        emitted = False
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for raw in response:
                    if cancelled and cancelled():
                        break
                    try:
                        data = json.loads(raw.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    delta = data.get("message", {}).get("content", "")
                    if delta:
                        emitted = True
                        yield str(delta)
                    if data.get("done"):
                        break
        except (urllib.error.HTTPError, urllib.error.URLError):
            if cancelled and cancelled():
                return
            if emitted:
                raise
            yield from super().stream_generate(system, messages, role, cancelled)


class CognitiveModelRouter:
    """Route planner, executor, and critic independently across model providers."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.providers_cfg = config.get("providers", {})
        self.roles_cfg = config.get("roles", {})
        self.fallback_to_mock = bool(config.get("fallback_to_mock", True))
        self._runtime_overrides: dict[str, dict[str, str | None]] = {}

    def _provider_default_model(self, provider: str) -> str:
        defaults = {
            "claude_cli": "sonnet",
            "openai": "gpt-5.6-luna",
            "anthropic": "claude-sonnet-5",
            "ollama": "gemma3",
            "mock": "daqauntum-mock",
        }
        return str(self.providers_cfg.get(provider, {}).get("model") or defaults[provider])

    def _build(self, provider: str, model: str | None = None) -> BrainAdapter:
        model = model or self._provider_default_model(provider)
        cfg = self.providers_cfg.get(provider, {})
        if provider == "claude_cli":
            return ClaudeCLIBrain(
                model=model,
                binary=str(cfg.get("binary", "claude")),
                timeout_seconds=int(cfg.get("timeout_seconds", 120)),
            )
        if provider == "openai":
            return OpenAIBrain(model=model)
        if provider == "anthropic":
            return AnthropicBrain(
                model=model,
                base_url=str(cfg.get("base_url", "https://api.anthropic.com")),
                max_tokens=int(cfg.get("max_tokens", 4096)),
                timeout_seconds=int(cfg.get("timeout_seconds", 120)),
            )
        if provider == "ollama":
            return OllamaBrain(
                model=model,
                base_url=str(cfg.get("base_url", "http://localhost:11434")),
                timeout_seconds=int(cfg.get("timeout_seconds", 120)),
            )
        if provider == "mock":
            return MockBrain()
        raise ValueError(f"Unknown model provider: {provider}")

    def _role_config(self, role: str) -> dict[str, Any]:
        if role not in VALID_ROLES:
            raise ValueError(f"Unknown cognitive role: {role}")
        result = dict(self.roles_cfg.get(role, {}))
        override = self._runtime_overrides.get(role)
        if override:
            result["provider"] = override["provider"]
            if override.get("model"):
                result["model"] = override["model"]
        return result

    def _tier_model(self, provider: str, tier: str | None) -> str | None:
        if not tier:
            return None
        cfg = self.providers_cfg.get(provider, {})
        tiers = cfg.get("models", {}) if isinstance(cfg, dict) else {}
        if not isinstance(tiers, dict):
            return None
        value = tiers.get(tier)
        if value is None and tier == "balanced":
            value = tiers.get("fast") or tiers.get("strong")
        return str(value) if value else None

    def _candidate_specs(
        self,
        role: str,
        routing: dict[str, Any] | None = None,
    ) -> list[tuple[str, str | None]]:
        cfg = self._role_config(role)
        routing = routing or {}
        requested = str(cfg.get("provider", "auto")).lower()
        explicit_model = cfg.get("model")

        policy_preference = routing.get("preference")
        preference = policy_preference or cfg.get("preference") or ["claude_cli", "openai", "anthropic", "ollama", "mock"]
        preference = [str(item).lower() for item in preference if str(item).lower() in VALID_PROVIDERS]

        allow_hosted = bool(routing.get("allow_hosted", True))
        if not allow_hosted:
            preference = [p for p in preference if p not in {"claude_cli", "openai", "anthropic"}]
            if requested in {"claude_cli", "openai", "anthropic"}:
                requested = "auto"

        excluded = {str(p).lower() for p in routing.get("exclude_providers", [])}
        preference = [p for p in preference if p not in excluded]
        if requested in excluded:
            requested = "auto"

        if requested != "auto":
            providers = [requested] + [p for p in preference if p != requested]
        else:
            providers = preference

        if self.fallback_to_mock and "mock" not in providers and "mock" not in excluded:
            providers.append("mock")
        if not self.fallback_to_mock:
            providers = [p for p in providers if p != "mock"]

        tier = routing.get("model_tier")
        specs: list[tuple[str, str | None]] = []
        for provider in providers:
            model = None
            if provider == requested and explicit_model:
                model = str(explicit_model)
            else:
                model = self._tier_model(provider, str(tier) if tier else None)
            specs.append((provider, model))
        return specs

    def _adapter_is_available(self, adapter: BrainAdapter) -> ProviderStatus:
        try:
            return adapter.available()
        except Exception as exc:
            return ProviderStatus(adapter.provider, False, str(exc))

    def generate(
        self,
        role: str,
        system: str,
        messages: list[dict[str, str]],
        routing: dict[str, Any] | None = None,
    ) -> BrainResponse:
        failures: list[str] = []
        candidates = self._candidate_specs(role, routing=routing)
        for index, (provider, explicit_model) in enumerate(candidates):
            try:
                adapter = self._build(provider, explicit_model)
                status = self._adapter_is_available(adapter)
                if not status.available:
                    failures.append(f"{provider}: {status.detail}")
                    continue
                response = adapter.generate(system=system, messages=messages, role=role)
                response.fallback_used = index > 0
                return response
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
                continue
        raise RuntimeError(f"No model available for role '{role}'. " + "; ".join(failures))

    def stream_generate(
        self,
        role: str,
        system: str,
        messages: list[dict[str, str]],
        routing: dict[str, Any] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        failures: list[str] = []
        candidates = self._candidate_specs(role, routing=routing)
        for index, (provider, explicit_model) in enumerate(candidates):
            try:
                adapter = self._build(provider, explicit_model)
                status = self._adapter_is_available(adapter)
                if not status.available:
                    failures.append(f"{provider}: {status.detail}")
                    continue
                meta = {"provider": adapter.provider, "model": adapter.model, "role": role, "fallback_used": index > 0}
                yield {"type": "meta", **meta}
                chunks: list[str] = []
                for delta in adapter.stream_generate(system=system, messages=messages, role=role, cancelled=cancelled):
                    if cancelled and cancelled():
                        break
                    if delta:
                        chunks.append(delta)
                        yield {"type": "delta", "text": delta}
                response = BrainResponse(text="".join(chunks), provider=adapter.provider, model=adapter.model, role=role, fallback_used=index > 0)
                yield {"type": "done", "response": response}
                return
            except Exception as exc:
                failures.append(f"{provider}: {exc}")
                continue
        raise RuntimeError(f"No model available for role '{role}'. " + "; ".join(failures))

    def resolve(self, role: str, routing: dict[str, Any] | None = None) -> dict[str, Any]:
        candidates = self._candidate_specs(role, routing=routing)
        checked: list[dict[str, Any]] = []
        for provider, explicit_model in candidates:
            try:
                adapter = self._build(provider, explicit_model)
                status = self._adapter_is_available(adapter)
                checked.append(
                    {
                        "provider": provider,
                        "model": adapter.model,
                        "available": status.available,
                        "detail": status.detail,
                    }
                )
                if status.available:
                    return {"selected": checked[-1], "candidates": checked}
            except Exception as exc:
                checked.append(
                    {
                        "provider": provider,
                        "model": explicit_model or self._provider_default_model(provider),
                        "available": False,
                        "detail": str(exc),
                    }
                )
        return {"selected": None, "candidates": checked}

    def matrix(self) -> dict[str, dict[str, Any]]:
        return {role: self.resolve(role) for role in VALID_ROLES}

    def provider_health(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider in VALID_PROVIDERS:
            try:
                adapter = self._build(provider)
                status = self._adapter_is_available(adapter)
                rows.append(
                    {
                        "provider": provider,
                        "model": adapter.model,
                        "available": status.available,
                        "detail": status.detail,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "provider": provider,
                        "model": self._provider_default_model(provider),
                        "available": False,
                        "detail": str(exc),
                    }
                )
        return rows

    def set_role(self, role: str, provider: str, model: str | None = None) -> None:
        role = role.lower()
        provider = provider.lower()
        if role not in VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(VALID_ROLES)}")
        if provider != "auto" and provider not in VALID_PROVIDERS:
            raise ValueError(f"Provider must be auto or one of: {', '.join(VALID_PROVIDERS)}")
        self._runtime_overrides[role] = {"provider": provider, "model": model}

    def clear_role_override(self, role: str) -> None:
        self._runtime_overrides.pop(role.lower(), None)


# Backward-compatible name for external code that imported ModelRouter.
ModelRouter = CognitiveModelRouter
