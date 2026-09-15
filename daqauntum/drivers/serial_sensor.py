from __future__ import annotations

import os
from typing import Any

from drivers.base import (
    Capability,
    DeviceDriver,
    DriverNotConfigured,
    DriverUnavailable,
    DriverWriteNotPermitted,
)
from drivers.parsers import get_parser
from events.models import Event


def _load_pyserial():
    try:
        import serial  # type: ignore

        return serial
    except Exception:
        return None


class SerialSensorDriver(DeviceDriver):
    """Read line-oriented sensors over an explicitly allowlisted serial port.

    Serial ports are shared, powerful and easy to get wrong: the same
    /dev/ttyUSB0 might be a temperature sensor or a microcontroller's flashing
    interface. So this driver opens only ports the user listed by name, is
    read-only unless writes are separately enabled, and never enumerates the
    system's ports as though they were approved.
    """

    name = "serial"
    kind = "sensor"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.baudrate = int(self.config.get("baudrate", 9600))
        self.timeout_seconds = max(0.1, float(self.config.get("timeout_seconds", 2)))
        self.parser_name = str(self.config.get("parser", "line"))
        self.parser_config = dict(self.config.get("parser_config", {}) or {})
        self.max_lines = max(1, int(self.config.get("max_lines_per_poll", 1)))

    def available(self) -> tuple[bool, str]:
        if _load_pyserial() is None:
            return False, "Install the optional 'pyserial' package to use serial sensors."
        return True, ""

    def targets(self) -> list[str]:
        ports = self.config.get("allow_ports") or []
        if isinstance(ports, str):
            ports = [ports]
        return [str(port).strip() for port in ports if str(port).strip()]

    def capabilities(self) -> list[Capability]:
        caps = [Capability("read_sensor", "read", f"Read one framed reading using the '{self.parser_name}' parser.")]
        if self.allow_writes:
            caps.append(Capability("write_command", "write", "Send a command string to an allowlisted port."))
        return caps

    def discover(self) -> list[dict[str, Any]]:
        """List serial ports the OS can see.

        This is inventory, not authorization: a port shows up here whether or
        not it is allowlisted, and the 'approved' flag says which is which.
        """
        serial_module = _load_pyserial()
        if serial_module is None:
            raise DriverUnavailable("pyserial is not installed")
        approved = set(self.targets())
        found: list[dict[str, Any]] = []
        try:
            from serial.tools import list_ports  # type: ignore

            for port in list_ports.comports():
                found.append(
                    {
                        "port": port.device,
                        "description": getattr(port, "description", "") or "",
                        "hwid": getattr(port, "hwid", "") or "",
                        "approved": port.device in approved,
                    }
                )
        except Exception:
            import glob

            for path in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))[:32]:
                found.append({"port": path, "description": "", "hwid": "", "approved": path in approved})
        return found

    def read_port(self, port: str | None = None) -> dict[str, Any]:
        """Read and parse one reading from an approved port."""
        self.require_enabled()
        serial_module = _load_pyserial()
        if serial_module is None:
            raise DriverUnavailable("pyserial is not installed")
        target = self.require_target(port or "")
        parser = get_parser(self.parser_name)
        try:
            with serial_module.Serial(target, self.baudrate, timeout=self.timeout_seconds) as handle:
                raw = ""
                for _ in range(self.max_lines):
                    line = handle.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        raw = line
                        break
            if not raw:
                raise DriverNotConfigured(f"No data received from {target} within {self.timeout_seconds}s")
            fields = parser(raw, config=self.parser_config)
            self._note_success()
            return {"port": target, "raw": raw, "fields": fields}
        except (DriverNotConfigured, DriverUnavailable):
            raise
        except Exception as exc:
            self._note_failure(exc)
            raise DriverUnavailable(f"Serial read from {target} failed: {exc}") from exc

    def poll(self) -> list[Event]:
        if not self.enabled:
            return []
        events: list[Event] = []
        for port in self.targets():
            try:
                reading = self.read_port(port)
            except Exception as exc:
                self._note_failure(exc)
                events.append(
                    self.event(
                        "driver_error",
                        port,
                        severity="warning",
                        message=f"Serial read failed: {exc}",
                        attributes={"port": port, "error": str(exc)},
                    )
                )
                continue
            fields = reading["fields"]
            events.append(
                self.event(
                    "sensor_reading",
                    port,
                    message=f"{port}: {reading['raw'][:120]}",
                    attributes={"port": port, "raw": reading["raw"], **fields},
                )
            )
        return events

    def write(self, target: str, payload: Any, **kwargs: Any) -> str:
        """Send a command to an approved port.

        Reached only through the permission-gated serial_write tool, and still
        refused here unless the user opened this driver for writing.
        """
        self.require_write_permission(str(target or "").strip())
        serial_module = _load_pyserial()
        if serial_module is None:
            raise DriverUnavailable("pyserial is not installed")
        data = str(payload or "")
        if not data:
            raise ValueError("A payload is required")
        if not data.endswith("\n"):
            data += "\n"
        try:
            with serial_module.Serial(target, self.baudrate, timeout=self.timeout_seconds) as handle:
                handle.write(data.encode("utf-8"))
            self._note_success()
        except Exception as exc:
            self._note_failure(exc)
            raise DriverUnavailable(f"Serial write to {target} failed: {exc}") from exc
        return f"Wrote {len(data)} bytes to {target}"
