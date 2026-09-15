from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from events.models import Event


class DriverError(RuntimeError):
    """Base class for driver failures."""


class DriverUnavailable(DriverError):
    """The driver's optional dependency, binary or hardware is missing."""


class DriverNotConfigured(DriverError):
    """The driver is available but the user has not granted it any scope.

    An empty allowlist is not an invitation to talk to everything; it means
    nothing has been approved yet.
    """


class DriverWriteNotPermitted(DriverError):
    """A write was attempted that the user has not enabled for this driver."""


@dataclass(frozen=True)
class Capability:
    """One thing a driver can do with an approved target.

    Capabilities are declared, not inferred. A device that was merely detected
    has no capabilities until a verified adapter maps them, so discovery output
    never turns into a controllable-device claim.
    """

    name: str
    mode: str  # "read" | "notify" | "write"
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "mode": self.mode, "description": self.description}


@dataclass
class DriverStatus:
    """Honest, four-part readiness so the GUI cannot overstate capability."""

    name: str
    kind: str
    enabled: bool = False          # the user turned it on in config
    available: bool = False        # dependency/binary/hardware present
    configured: bool = False       # the user allowlisted at least one target
    healthy: bool = False          # last interaction actually worked
    detail: str = ""
    writes_allowed: bool = False
    capabilities: list[Capability] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    last_poll_at: float | None = None
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "enabled": self.enabled,
            "available": self.available,
            "configured": self.configured,
            "healthy": self.healthy,
            "detail": self.detail,
            "writes_allowed": self.writes_allowed,
            "capabilities": [item.as_dict() for item in self.capabilities],
            "targets": list(self.targets),
            "last_poll_at": self.last_poll_at,
            "last_error": self.last_error,
            "usable": self.enabled and self.available and self.configured,
        }


class DeviceDriver:
    """Base class for an approved-device adapter.

    The contract is deliberately one-directional:

    * ``poll()`` returns Events. A driver never publishes them itself and never
      reacts to them, so a passive source cannot start an action.
    * ``discover()`` returns metadata about what is nearby. Detecting a device
      grants nothing: it is not paired, trusted or controllable.
    * ``write()`` is the only state-changing entry point. It refuses unless the
      user explicitly enabled writes for this driver, and it is reached only
      through the permission-gated tool registry.
    """

    name = "driver"
    kind = "generic"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", False))
        self.allow_writes = bool(self.config.get("allow_writes", False))
        self.last_poll_at: float | None = None
        self.last_error: str | None = None

    # Introspection -----------------------------------------------------------
    def available(self) -> tuple[bool, str]:
        """Return (dependency present, human-readable detail)."""
        return True, ""

    def targets(self) -> list[str]:
        """The explicitly approved devices/ports/topics this driver may touch."""
        return []

    def capabilities(self) -> list[Capability]:
        return []

    def status(self) -> DriverStatus:
        available, detail = self.available()
        targets = self.targets()
        return DriverStatus(
            name=self.name,
            kind=self.kind,
            enabled=self.enabled,
            available=available,
            configured=bool(targets),
            healthy=bool(self.enabled and available and targets and self.last_error is None),
            detail=detail or self._default_detail(available, targets),
            writes_allowed=self.allow_writes,
            capabilities=self.capabilities(),
            targets=targets,
            last_poll_at=self.last_poll_at,
            last_error=self.last_error,
        )

    def _default_detail(self, available: bool, targets: list[str]) -> str:
        if not self.enabled:
            return f"{self.name} is disabled. Enable it under drivers.{self.name} to use it."
        if not available:
            return f"{self.name} is enabled but its dependency is missing."
        if not targets:
            return f"{self.name} is available but nothing is allowlisted yet."
        return f"{self.name} ready for {len(targets)} approved target(s)."

    # Operations --------------------------------------------------------------
    def discover(self) -> list[dict[str, Any]]:
        """Explicitly look for nearby devices. Returns metadata only."""
        raise DriverUnavailable(f"{self.name} does not support discovery")

    def poll(self) -> list[Event]:
        """Read approved targets once and describe what was observed."""
        return []

    def write(self, target: str, payload: Any, **kwargs: Any) -> str:
        raise DriverWriteNotPermitted(f"{self.name} does not support writes")

    # Helpers -----------------------------------------------------------------
    def require_enabled(self) -> None:
        if not self.enabled:
            raise DriverNotConfigured(f"{self.name} driver is disabled")

    def require_write_permission(self, target: str) -> None:
        """Second gate for writes, inside the driver itself.

        The permission manager decides whether the *user* authorized the tool
        call. This decides whether the *device* was ever opened for writing at
        all. Both must agree.
        """
        self.require_enabled()
        if not self.allow_writes:
            raise DriverWriteNotPermitted(
                f"Writes are disabled for the {self.name} driver. "
                f"Set drivers.{self.name}.allow_writes: true to allow them."
            )
        if target not in self.targets():
            raise DriverWriteNotPermitted(
                f"'{target}' is not in the {self.name} allowlist. Add it explicitly before writing."
            )

    def require_target(self, target: str) -> str:
        target = str(target or "").strip()
        approved = self.targets()
        if not approved:
            raise DriverNotConfigured(f"No targets are allowlisted for the {self.name} driver")
        if target and target not in approved:
            raise DriverNotConfigured(f"'{target}' is not in the {self.name} allowlist")
        return target or approved[0]

    def _note_success(self) -> None:
        self.last_poll_at = time.time()
        self.last_error = None

    def _note_failure(self, exc: Exception) -> None:
        self.last_poll_at = time.time()
        self.last_error = f"{type(exc).__name__}: {exc}"

    def event(
        self,
        kind: str,
        subject: str,
        *,
        message: str = "",
        severity: str = "info",
        attributes: dict[str, Any] | None = None,
    ) -> Event:
        """Build an Event tagged with this driver's identity."""
        payload = dict(attributes or {})
        payload.setdefault("driver", self.name)
        return Event(
            source="driver",
            kind=kind,
            subject=subject,
            severity=severity,
            message=message,
            attributes=payload,
            dedupe_key=f"driver.{self.name}:{kind}:{str(subject).strip().lower()}",
        )
