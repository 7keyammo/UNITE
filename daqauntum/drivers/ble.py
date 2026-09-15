from __future__ import annotations

import asyncio
import threading
from typing import Any

from drivers.base import (
    Capability,
    DeviceDriver,
    DriverNotConfigured,
    DriverUnavailable,
    DriverWriteNotPermitted,
)
from events.models import Event


def _load_bleak():
    try:
        import bleak  # type: ignore

        return bleak
    except Exception:
        return None


def _run_async(coro, timeout: float):
    """Run one bleak coroutine from synchronous code.

    DaQauntum's kernel and poll loop are threaded, not async, and a caller may
    already be inside an event loop. Running on a dedicated loop in its own
    thread keeps this safe in both cases.
    """
    result: dict[str, Any] = {}

    def _target() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            result["error"] = exc
        finally:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)

    thread = threading.Thread(target=_target, name="daqauntum-ble", daemon=True)
    thread.start()
    thread.join(timeout=timeout + 5)
    if thread.is_alive():
        raise DriverUnavailable("BLE operation timed out")
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _normalize_address(value: Any) -> str:
    return str(value or "").strip().upper()


class BLEDriver(DeviceDriver):
    """Read declared GATT characteristics from explicitly approved BLE devices.

    Three rules shape this adapter:

    * Discovery is inventory. Seeing an advertisement tells you a device exists,
      not that it is yours, trusted, or controllable.
    * A device is readable only once the user lists its address *and* declares a
      GATT profile naming the characteristics to read. There is no "read
      everything" mode, because a generic characteristic dump is a fingerprint
      of someone's body, home or car.
    * There is no pairing or PIN flow here at all. Pair through the operating
      system, deliberately, and then allowlist the device.
    """

    name = "ble"
    kind = "bluetooth_le"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.scan_seconds = max(1.0, min(float(self.config.get("scan_seconds", 6)), 30.0))
        self.connect_timeout = max(2.0, min(float(self.config.get("connect_timeout_seconds", 10)), 60.0))

    def available(self) -> tuple[bool, str]:
        if _load_bleak() is None:
            return False, "Install the optional 'bleak' package to use BLE devices."
        return True, ""

    def targets(self) -> list[str]:
        devices = self.config.get("allow_devices") or []
        if isinstance(devices, str):
            devices = [devices]
        return [_normalize_address(item) for item in devices if _normalize_address(item)]

    def profiles(self) -> dict[str, dict[str, Any]]:
        profiles = self.config.get("profiles") or {}
        if not isinstance(profiles, dict):
            return {}
        return {_normalize_address(key): value for key, value in profiles.items() if isinstance(value, dict)}

    def profile_for(self, address: str) -> dict[str, Any]:
        profile = self.profiles().get(_normalize_address(address))
        if not profile:
            raise DriverNotConfigured(
                f"No GATT profile is declared for {address}. "
                "Declare drivers.ble.profiles.<address>.characteristics before reading."
            )
        characteristics = profile.get("characteristics") or {}
        if not isinstance(characteristics, dict) or not characteristics:
            raise DriverNotConfigured(f"GATT profile for {address} declares no characteristics")
        return profile

    def capabilities(self) -> list[Capability]:
        caps: list[Capability] = []
        for address, profile in self.profiles().items():
            for label, spec in (profile.get("characteristics") or {}).items():
                mode = str((spec or {}).get("mode", "read")).lower()
                caps.append(
                    Capability(
                        f"{address}:{label}",
                        "write" if mode == "write" else ("notify" if mode == "notify" else "read"),
                        str((spec or {}).get("description", "")) or f"GATT characteristic {(spec or {}).get('uuid', '?')}",
                    )
                )
        return caps

    def discover(self) -> list[dict[str, Any]]:
        """Scan for advertising BLE devices. Explicit, bounded, read-only."""
        self.require_enabled()
        bleak = _load_bleak()
        if bleak is None:
            raise DriverUnavailable("bleak is not installed")
        approved = set(self.targets())

        async def _scan():
            from bleak import BleakScanner  # type: ignore

            return await BleakScanner.discover(timeout=self.scan_seconds)

        try:
            devices = _run_async(_scan(), timeout=self.scan_seconds + 10) or []
        except DriverUnavailable:
            raise
        except Exception as exc:
            self._note_failure(exc)
            raise DriverUnavailable(f"BLE scan failed: {exc}") from exc
        self._note_success()
        found = []
        for device in devices[:120]:
            address = _normalize_address(getattr(device, "address", ""))
            found.append(
                {
                    "address": address,
                    "name": getattr(device, "name", None) or "(unnamed)",
                    "approved": address in approved,
                    "has_profile": address in self.profiles(),
                    # Detected is not controllable: a device becomes usable only
                    # once it is allowlisted and a profile maps its capabilities.
                    "controllable": address in approved and address in self.profiles(),
                }
            )
        return found

    def read_device(self, address: str) -> dict[str, Any]:
        """Read every declared read/notify characteristic for one device."""
        self.require_enabled()
        bleak = _load_bleak()
        if bleak is None:
            raise DriverUnavailable("bleak is not installed")
        target = self.require_target(_normalize_address(address))
        profile = self.profile_for(target)
        characteristics = {
            label: spec
            for label, spec in (profile.get("characteristics") or {}).items()
            if str((spec or {}).get("mode", "read")).lower() in {"read", "notify"}
        }
        if not characteristics:
            raise DriverNotConfigured(f"GATT profile for {target} declares no readable characteristics")

        async def _read():
            from bleak import BleakClient  # type: ignore

            values: dict[str, Any] = {}
            async with BleakClient(target, timeout=self.connect_timeout) as client:
                for label, spec in characteristics.items():
                    uuid = str((spec or {}).get("uuid", "")).strip()
                    if not uuid:
                        continue
                    raw = await client.read_gatt_char(uuid)
                    values[label] = self._decode(raw, spec)
            return values

        try:
            values = _run_async(_read(), timeout=self.connect_timeout + 15) or {}
        except (DriverNotConfigured, DriverUnavailable):
            raise
        except Exception as exc:
            self._note_failure(exc)
            raise DriverUnavailable(f"BLE read from {target} failed: {exc}") from exc
        self._note_success()
        return {"address": target, "name": profile.get("name") or target, "values": values}

    @staticmethod
    def _decode(raw: Any, spec: dict[str, Any] | None) -> Any:
        """Decode a characteristic according to its declared encoding."""
        encoding = str((spec or {}).get("encoding", "utf8")).lower()
        data = bytes(raw or b"")
        if encoding in {"uint8", "u8"}:
            return int(data[0]) if data else None
        if encoding in {"uint16", "u16"}:
            return int.from_bytes(data[:2], "little") if len(data) >= 2 else None
        if encoding in {"int16", "s16"}:
            return int.from_bytes(data[:2], "little", signed=True) if len(data) >= 2 else None
        if encoding in {"uint32", "u32"}:
            return int.from_bytes(data[:4], "little") if len(data) >= 4 else None
        if encoding == "hex":
            return data.hex()
        text = data.decode("utf-8", errors="replace").strip()
        try:
            return float(text) if "." in text else int(text)
        except ValueError:
            return text

    def poll(self) -> list[Event]:
        if not self.enabled:
            return []
        events: list[Event] = []
        for address in self.targets():
            if address not in self.profiles():
                continue  # Allowlisted but not yet mapped: nothing to read.
            try:
                reading = self.read_device(address)
            except Exception as exc:
                self._note_failure(exc)
                events.append(
                    self.event(
                        "driver_error",
                        address,
                        severity="warning",
                        message=f"BLE read failed: {exc}",
                        attributes={"address": address, "error": str(exc)},
                    )
                )
                continue
            events.append(
                self.event(
                    "sensor_reading",
                    address,
                    message=f"{reading['name']}: "
                    + ", ".join(f"{k}={v}" for k, v in list(reading["values"].items())[:6]),
                    attributes={"address": address, "device_name": reading["name"], **reading["values"]},
                )
            )
        return events

    def write(self, target: str, payload: Any, **kwargs: Any) -> str:
        """Write a declared write characteristic on an approved device."""
        # Authorization is decided before the dependency is touched, so policy
        # is enforced identically whether or not bleak happens to be installed.
        address = _normalize_address(target)
        self.require_write_permission(address)
        label = str(kwargs.get("characteristic") or "").strip()
        if not label:
            raise ValueError("A 'characteristic' label from the device profile is required")
        profile = self.profile_for(address)
        spec = (profile.get("characteristics") or {}).get(label)
        if not isinstance(spec, dict):
            raise DriverWriteNotPermitted(f"Characteristic '{label}' is not declared for {address}")
        if str(spec.get("mode", "read")).lower() != "write":
            raise DriverWriteNotPermitted(f"Characteristic '{label}' is not declared writable")
        uuid = str(spec.get("uuid", "")).strip()
        if not uuid:
            raise DriverWriteNotPermitted(f"Characteristic '{label}' has no UUID")
        data = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode("utf-8")

        if _load_bleak() is None:
            raise DriverUnavailable("bleak is not installed")

        async def _write():
            from bleak import BleakClient  # type: ignore

            async with BleakClient(address, timeout=self.connect_timeout) as client:
                await client.write_gatt_char(uuid, bytes(data), response=True)
            return True

        try:
            _run_async(_write(), timeout=self.connect_timeout + 15)
            self._note_success()
        except Exception as exc:
            self._note_failure(exc)
            raise DriverUnavailable(f"BLE write to {address} failed: {exc}") from exc
        return f"Wrote {len(data)} bytes to {address}:{label}"
