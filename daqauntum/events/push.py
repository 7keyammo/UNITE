from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from events.models import SEVERITY_RANK, normalize_severity


# Sending a notification to an outside service takes DaQauntum's observations
# off the machine. That is the whole point of a push notification, but it is a
# privacy boundary, so this adapter is disabled by default, must be pointed at
# an explicit URL, and sends a deliberately narrow payload.
_ALLOWED_SCHEMES = {"http", "https"}

# Fields that are safe to send. The notification's `payload` is excluded on
# purpose: it carries the originating event's raw attributes, which can include
# device addresses, entity names and sensor readings the user never asked to
# publish to a third-party service.
_SAFE_FIELDS = ("title", "body", "severity", "kind", "source", "created_at")

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class PushConfigError(ValueError):
    """Raised when the push adapter is misconfigured."""


class WebhookPushAdapter:
    """Deliver notifications to a user-configured webhook.

    Works with anything that accepts an HTTP POST: ntfy, Gotify, Pushover, a
    Home Assistant webhook, or a personal relay. DaQauntum deliberately does not
    bundle a hosted push service, because that would mean routing a private
    assistant's alerts through a third party by default.

    Credentials belong in environment variables, never in config.yaml, so the
    URL and any header value may reference `${ENV_VAR}`.
    """

    name = "webhook_push"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", False))
        self.min_severity = normalize_severity(self.config.get("min_severity", "warning"), "warning")
        self.include_body = bool(self.config.get("include_body", True))
        self.timeout_seconds = max(1.0, min(float(self.config.get("timeout_seconds", 8)), 30.0))
        self.method = str(self.config.get("method", "POST")).upper()
        self.template = self.config.get("template")
        self.sent = 0
        self.failed = 0
        self.last_error: str | None = None
        self.last_sent_at: float | None = None

    # Configuration -----------------------------------------------------------
    @staticmethod
    def _expand(value: Any) -> str:
        """Resolve ${ENV_VAR} references so secrets stay out of config files."""
        text = str(value or "")
        return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.getenv(m.group(1), ""), text)

    def url(self) -> str:
        raw = self._expand(self.config.get("url", "")).strip()
        if not raw:
            raise PushConfigError("No push URL configured (events.push.url)")
        scheme = raw.split("://", 1)[0].lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise PushConfigError(f"Push URL must be http or https, not '{scheme}'")
        return raw

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "DaQauntum/0.4.2"}
        for key, value in (self.config.get("headers") or {}).items():
            resolved = self._expand(value)
            if resolved:
                headers[str(key)] = resolved
        return headers

    def available(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Push delivery is disabled (events.push.enabled)."
        try:
            self.url()
        except PushConfigError as exc:
            return False, str(exc)
        return True, "Push delivery configured."

    # Delivery ----------------------------------------------------------------
    def _payload(self, notification: dict[str, Any]) -> dict[str, Any]:
        safe = {field: notification.get(field) for field in _SAFE_FIELDS}
        if not self.include_body:
            safe["body"] = ""
        if isinstance(self.template, dict):
            # A template lets the user match a specific service's schema, e.g.
            # ntfy's {"topic": ..., "title": ..., "message": ...}.
            rendered: dict[str, Any] = {}
            for key, value in self.template.items():
                if isinstance(value, str):
                    rendered[key] = _PLACEHOLDER_RE.sub(
                        lambda m: str(safe.get(m.group(1), "") or ""), self._expand(value)
                    )
                else:
                    rendered[key] = value
            return rendered
        return safe

    def __call__(self, notification: dict[str, Any]) -> None:
        """Deliver one notification. Raises so the caller records the failure."""
        if not self.enabled:
            return
        severity = normalize_severity(notification.get("severity"))
        if SEVERITY_RANK.get(severity, 1) < SEVERITY_RANK.get(self.min_severity, 3):
            return
        url = self.url()
        body = json.dumps(self._payload(notification), ensure_ascii=False, default=str).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=self.method, headers=self.headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Push endpoint returned HTTP {response.status}")
            self.sent += 1
            self.last_sent_at = time.time()
            self.last_error = None
        except (urllib.error.URLError, OSError, RuntimeError) as exc:
            self.failed += 1
            # The URL may embed a token, so it is never echoed into an error.
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"Push delivery failed: {type(exc).__name__}") from exc

    def stats(self) -> dict[str, Any]:
        available, detail = self.available()
        return {
            "name": self.name,
            "enabled": self.enabled,
            "available": available,
            "detail": detail,
            "min_severity": self.min_severity,
            "include_body": self.include_body,
            "sent": self.sent,
            "failed": self.failed,
            "last_sent_at": self.last_sent_at,
            "last_error": self.last_error,
        }
