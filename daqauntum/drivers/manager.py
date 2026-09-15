from __future__ import annotations

import threading
import time
from typing import Any

from drivers.base import DeviceDriver, DriverError, DriverUnavailable
from drivers.ble import BLEDriver
from drivers.homeassistant import HomeAssistantDriver
from drivers.mqtt_subscriber import MQTTSubscriberDriver
from drivers.serial_sensor import SerialSensorDriver
from events.models import Event


class DriverManager:
    """Registry and poll loop for approved device adapters.

    Drivers hand observations here and the manager publishes them to the event
    bus. Drivers never publish directly, so there is exactly one path from the
    physical world into DaQauntum, and it is the audited one.

    The manager also holds the write path, but only as a relay: it refuses any
    write the driver has not been opened for, and it is itself reached only
    from the permission-gated tool registry.
    """

    def __init__(self, config: dict[str, Any] | None = None, *, event_system=None, integrations=None):
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.events = event_system
        self.integrations = integrations
        self.poll_interval = max(5, int(self.config.get("poll_interval_seconds", 30)))
        self.drivers: dict[str, DeviceDriver] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._last_poll_at: float | None = None
        self._poll_errors: list[dict[str, Any]] = []
        self._register_builtins()

    def _register_builtins(self) -> None:
        self.register(SerialSensorDriver(self.config.get("serial", {})))
        self.register(BLEDriver(self.config.get("ble", {})))
        self.register(MQTTSubscriberDriver(self.config.get("mqtt", {}), integrations=self.integrations))
        self.register(HomeAssistantDriver(self.config.get("home_assistant", {}), integrations=self.integrations))

    def register(self, driver: DeviceDriver) -> DeviceDriver:
        if not isinstance(driver, DeviceDriver):
            raise TypeError("Drivers must subclass DeviceDriver")
        with self._lock:
            self.drivers[driver.name] = driver
        return driver

    def get(self, name: str) -> DeviceDriver:
        driver = self.drivers.get(str(name or "").strip().lower())
        if driver is None:
            raise DriverUnavailable(f"Unknown driver '{name}'. Available: {', '.join(sorted(self.drivers))}")
        return driver

    @property
    def active_drivers(self) -> list[DeviceDriver]:
        """Drivers the user enabled and that have at least one approved target."""
        return [driver for driver in self.drivers.values() if driver.enabled and driver.targets()]

    # Operations --------------------------------------------------------------
    def discover(self, name: str) -> list[dict[str, Any]]:
        """Explicitly enumerate what one driver can see.

        Discovery output is inventory. Each entry says whether it is approved;
        nothing here authorizes a connection.
        """
        driver = self.get(name)
        driver.require_enabled()
        return driver.discover()

    def read(self, name: str, target: str | None = None) -> dict[str, Any]:
        driver = self.get(name)
        driver.require_enabled()
        if hasattr(driver, "read_port"):
            return driver.read_port(target)
        if hasattr(driver, "read_device"):
            return driver.read_device(target or "")
        if hasattr(driver, "state"):
            return driver.state(target)
        raise DriverUnavailable(f"Driver '{name}' does not support direct reads")

    def write(self, name: str, target: str, payload: Any, **kwargs: Any) -> str:
        """Relay a write to a driver.

        Authority was already decided by the permission manager before this is
        reached. The driver applies its own allowlist on top, so both the user's
        permission level and the device's declared scope must agree.
        """
        driver = self.get(name)
        return driver.write(target, payload, **kwargs)

    # Polling -----------------------------------------------------------------
    def poll_once(self) -> dict[str, Any]:
        """Poll every active driver and publish what they observed."""
        if not self.enabled:
            return {"polled": 0, "published": 0, "suppressed": 0, "errors": []}
        published = suppressed = polled = 0
        errors: list[dict[str, Any]] = []
        for driver in self.active_drivers:
            polled += 1
            try:
                observations = driver.poll()
            except Exception as exc:
                error = {"driver": driver.name, "error": f"{type(exc).__name__}: {exc}", "at": time.time()}
                errors.append(error)
                self._poll_errors.append(error)
                del self._poll_errors[:-20]
                continue
            for event in observations:
                if not isinstance(event, Event):
                    continue
                if self.events is None:
                    suppressed += 1
                    continue
                result = self.events.publish(event)
                if result.accepted:
                    published += 1
                else:
                    suppressed += 1
        self._last_poll_at = time.time()
        return {"polled": polled, "published": published, "suppressed": suppressed, "errors": errors}

    def start(self, interval_seconds: int | None = None) -> bool:
        """Start the background poll loop, if any driver is actually usable."""
        if not self.enabled:
            return False
        if not self.active_drivers:
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            if interval_seconds:
                self.poll_interval = max(5, int(interval_seconds))
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="daqauntum-drivers", daemon=True)
            self._thread.start()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # pragma: no cover - defensive
                self._poll_errors.append({"driver": "manager", "error": f"{type(exc).__name__}: {exc}", "at": time.time()})
                del self._poll_errors[:-20]
            self._stop.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        for driver in self.drivers.values():
            stop = getattr(driver, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # Status ------------------------------------------------------------------
    def statuses(self) -> list[dict[str, Any]]:
        return [driver.status().as_dict() for driver in self.drivers.values()]

    def stats(self) -> dict[str, Any]:
        statuses = self.statuses()
        return {
            "enabled": self.enabled,
            "running": self.running,
            "poll_interval_seconds": self.poll_interval,
            "last_poll_at": self._last_poll_at,
            "registered": sorted(self.drivers),
            "usable": [item["name"] for item in statuses if item["usable"]],
            "drivers": statuses,
            "recent_errors": list(self._poll_errors[-5:]),
        }


__all__ = ["DriverManager", "DriverError", "DriverUnavailable"]
