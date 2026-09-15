from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any


class PresenceManager:
    """Local-first awareness of the computer and explicitly requested nearby radio context.

    Passive awareness never initiates a Wi-Fi/Bluetooth scan. Active radio scans are separate,
    explicit operations. State-changing network/device operations are exposed only through
    permission-gated tools.
    """

    def __init__(self, memory, integrations=None, config: dict[str, Any] | None = None):
        self.memory = memory
        self.integrations = integrations
        self.config = config or {}
        self.interval_seconds = max(5, int(self.config.get("sample_interval_seconds", 20)))
        self._latest: dict[str, Any] | None = None
        self._last_saved_at = 0.0
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.memory.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS presence_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sample_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.memory.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_presence_samples_id ON presence_samples(id DESC)"
        )
        self.memory.conn.commit()

    @staticmethod
    def _which(name: str) -> str | None:
        return shutil.which(name)

    @staticmethod
    def _run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, "", str(exc)

    @staticmethod
    def _read_text(path: str | Path) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            return None

    def _system_metrics(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "hostname": socket.gethostname(),
            "platform": platform.system().lower(),
            "platform_release": platform.release(),
        }
        try:
            import psutil  # type: ignore
            out["cpu_percent"] = round(float(psutil.cpu_percent(interval=None)), 1)
            vm = psutil.virtual_memory()
            out["memory_percent"] = round(float(vm.percent), 1)
            out["memory_available_bytes"] = int(vm.available)
            bat = psutil.sensors_battery()
            if bat is not None:
                out["battery_percent"] = round(float(bat.percent), 1)
                out["power_plugged"] = bool(bat.power_plugged)
        except Exception:
            try:
                load = self._read_text("/proc/loadavg")
                if load:
                    out["load_1m"] = float(load.split()[0])
            except Exception:
                pass

        temps: list[dict[str, Any]] = []
        for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*"))[:24]:
            raw = self._read_text(Path(zone) / "temp")
            if raw and re.fullmatch(r"-?\d+", raw):
                value = float(raw)
                if abs(value) > 500:
                    value /= 1000.0
                label = self._read_text(Path(zone) / "type") or Path(zone).name
                temps.append({"sensor": label, "celsius": round(value, 1)})
        if temps:
            out["temperatures"] = temps

        if "battery_percent" not in out:
            for bat in sorted(glob.glob("/sys/class/power_supply/BAT*"))[:4]:
                cap = self._read_text(Path(bat) / "capacity")
                if cap and cap.isdigit():
                    out["battery_percent"] = int(cap)
                status = self._read_text(Path(bat) / "status")
                if status:
                    out["battery_status"] = status
                break
        return out

    def _network_passive(self) -> dict[str, Any]:
        result: dict[str, Any] = {"manager": None, "interfaces": [], "wifi": {"connected": False}}
        if self._which("nmcli"):
            result["manager"] = "NetworkManager"
            code, stdout, _ = self._run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"], 5)
            if code == 0:
                for line in stdout.splitlines():
                    parts = line.split(":", 3)
                    if len(parts) < 4:
                        continue
                    dev, typ, state, conn = parts
                    item = {"device": dev, "type": typ, "state": state, "connection": conn}
                    result["interfaces"].append(item)
                    if typ == "wifi" and state == "connected":
                        result["wifi"] = {"connected": True, "device": dev, "connection": conn}
        else:
            for name in sorted(os.listdir("/sys/class/net")) if Path("/sys/class/net").exists() else []:
                oper = self._read_text(Path("/sys/class/net") / name / "operstate") or "unknown"
                result["interfaces"].append({"device": name, "state": oper})
        try:
            result["local_ip"] = self.local_ip()
        except Exception:
            pass
        return result

    def _bluetooth_passive(self) -> dict[str, Any]:
        out: dict[str, Any] = {"available": False, "powered": None, "paired": []}
        if not self._which("bluetoothctl"):
            return out
        out["available"] = True
        code, stdout, _ = self._run(["bluetoothctl", "show"], 5)
        if code == 0:
            match = re.search(r"Powered:\s+(yes|no)", stdout, flags=re.I)
            if match:
                out["powered"] = match.group(1).lower() == "yes"
            alias = re.search(r"Alias:\s+(.+)", stdout)
            if alias:
                out["adapter"] = alias.group(1).strip()
        code, stdout, _ = self._run(["bluetoothctl", "paired-devices"], 5)
        if code == 0:
            for line in stdout.splitlines()[:60]:
                m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line)
                if m:
                    out["paired"].append({"address": m.group(1).upper(), "name": m.group(2).strip()})
        return out

    def _local_sensors(self) -> dict[str, Any]:
        iio: list[dict[str, Any]] = []
        for dev in sorted(glob.glob("/sys/bus/iio/devices/iio:device*"))[:16]:
            p = Path(dev)
            name = self._read_text(p / "name") or p.name
            values = {}
            for item in sorted(p.glob("in_*_input"))[:24]:
                raw = self._read_text(item)
                if raw is not None:
                    values[item.stem] = raw
            iio.append({"device": p.name, "name": name, "values": values})
        return {
            "iio": iio,
            "video_devices": sorted(glob.glob("/dev/video*"))[:16],
            "serial_devices": sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))[:24],
            "sound_available": Path("/dev/snd").exists(),
        }

    def passive_snapshot(self, *, save: bool = True) -> dict[str, Any]:
        previous = self._latest or self.memory.get_state("presence.latest", None)
        sample = {
            "timestamp": time.time(),
            "system": self._system_metrics(),
            "network": self._network_passive(),
            "bluetooth": self._bluetooth_passive(),
            "sensors": self._local_sensors(),
        }
        if self.integrations is not None:
            try:
                sample["home_assistant"] = self.integrations.status("home_assistant").as_dict()
            except Exception:
                pass
        sample["alerts"] = self._derive_alerts(previous if isinstance(previous, dict) else None, sample)
        self._latest = sample
        if save:
            self.memory.conn.execute(
                "INSERT INTO presence_samples(sample_json) VALUES (?)",
                (json.dumps(sample, ensure_ascii=False, default=str),),
            )
            self.memory.conn.execute(
                "DELETE FROM presence_samples WHERE id NOT IN (SELECT id FROM presence_samples ORDER BY id DESC LIMIT 500)"
            )
            self.memory.conn.commit()
            self.memory.set_state("presence.latest", sample)
            self._last_saved_at = time.time()
            if sample.get("alerts"):
                self.memory.add_event("presence_change", {"alerts": sample["alerts"]})
        return sample

    def _derive_alerts(self, previous: dict[str, Any] | None, sample: dict[str, Any]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        system = sample.get("system") or {}
        try:
            battery = float(system.get("battery_percent"))
            if battery <= float(self.config.get("low_battery_percent", 15)) and not bool(system.get("power_plugged", False)):
                alerts.append({"kind": "low_battery", "severity": "warning", "message": f"Battery is {battery:.0f}%"})
        except Exception:
            pass
        high_temp = float(self.config.get("high_temperature_celsius", 85))
        for item in system.get("temperatures") or []:
            try:
                if float(item.get("celsius")) >= high_temp:
                    alerts.append({"kind": "high_temperature", "severity": "warning", "message": f"{item.get('sensor','sensor')} is {item.get('celsius')} C"})
            except Exception:
                pass
        if previous:
            old_wifi = ((previous.get("network") or {}).get("wifi") or {})
            new_wifi = ((sample.get("network") or {}).get("wifi") or {})
            if old_wifi.get("connection") != new_wifi.get("connection") or bool(old_wifi.get("connected")) != bool(new_wifi.get("connected")):
                alerts.append({"kind": "wifi_change", "severity": "info", "message": f"Wi-Fi changed from {old_wifi.get('connection') or 'offline'} to {new_wifi.get('connection') or 'offline'}"})
            old_serial = set((previous.get("sensors") or {}).get("serial_devices") or [])
            new_serial = set((sample.get("sensors") or {}).get("serial_devices") or [])
            for dev in sorted(new_serial - old_serial):
                alerts.append({"kind": "serial_attached", "severity": "info", "message": f"Serial/USB device attached: {dev}"})
            for dev in sorted(old_serial - new_serial):
                alerts.append({"kind": "serial_removed", "severity": "info", "message": f"Serial/USB device removed: {dev}"})
        return alerts

    def latest(self) -> dict[str, Any]:
        if self._latest:
            return self._latest
        cached = self.memory.get_state("presence.latest", None)
        if isinstance(cached, dict):
            self._latest = cached
            return cached
        return self.passive_snapshot(save=False)

    def stats(self) -> dict[str, Any]:
        row = self.memory.conn.execute("SELECT COUNT(*) AS n FROM presence_samples").fetchone()
        latest = self.latest()
        return {
            "enabled": bool(self.config.get("enabled", True)),
            "samples": int(row["n"] if row else 0),
            "sample_interval_seconds": self.interval_seconds,
            "wifi_connected": bool((latest.get("network") or {}).get("wifi", {}).get("connected")),
            "bluetooth_available": bool((latest.get("bluetooth") or {}).get("available")),
            "paired_bluetooth": len((latest.get("bluetooth") or {}).get("paired", [])),
            "video_devices": len((latest.get("sensors") or {}).get("video_devices", [])),
            "serial_devices": len((latest.get("sensors") or {}).get("serial_devices", [])),
        }

    def scan_wifi(self) -> list[dict[str, Any]]:
        if not self._which("nmcli"):
            return []
        code, stdout, err = self._run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", "--rescan", "yes"],
            float(self.config.get("radio_scan_timeout_seconds", 12)),
        )
        if code != 0:
            raise RuntimeError(err or "Wi-Fi scan failed")
        found: list[dict[str, Any]] = []
        seen = set()
        for line in stdout.splitlines():
            parts = line.rsplit(":", 3)
            if len(parts) != 4:
                continue
            ssid, signal, security, in_use = parts
            key = (ssid, security)
            if key in seen:
                continue
            seen.add(key)
            found.append({"ssid": ssid or "<hidden>", "signal": int(signal) if signal.isdigit() else None, "security": security, "connected": in_use == "*"})
        found.sort(key=lambda x: (not x["connected"], -(x["signal"] or -1)))
        self.memory.add_event("presence_wifi_scan", {"count": len(found)})
        return found[:80]

    def scan_bluetooth(self, seconds: int | None = None) -> list[dict[str, Any]]:
        if not self._which("bluetoothctl"):
            return []
        timeout = min(max(int(seconds or self.config.get("bluetooth_scan_seconds", 6)), 2), 15)
        # bluetoothctl --timeout exits by itself; scan is explicitly user-triggered.
        self._run(["bluetoothctl", "--timeout", str(timeout), "scan", "on"], timeout + 3)
        code, stdout, err = self._run(["bluetoothctl", "devices"], 5)
        if code != 0:
            raise RuntimeError(err or "Bluetooth discovery failed")
        paired = {x["address"] for x in self._bluetooth_passive().get("paired", [])}
        found = []
        for line in stdout.splitlines()[:120]:
            m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line)
            if m:
                addr = m.group(1).upper()
                found.append({"address": addr, "name": m.group(2).strip(), "paired": addr in paired})
        self.memory.add_event("presence_bluetooth_scan", {"count": len(found)})
        return found

    def discover_services(self) -> list[dict[str, Any]]:
        if self._which("avahi-browse"):
            code, stdout, err = self._run(["avahi-browse", "-artp"], float(self.config.get("service_scan_timeout_seconds", 8)))
            if code not in {0, 124} and not stdout:
                raise RuntimeError(err or "mDNS service discovery failed")
            items = []
            for line in stdout.splitlines():
                if not line.startswith("="):
                    continue
                parts = line.split(";")
                if len(parts) >= 9:
                    items.append({"interface": parts[1], "protocol": parts[2], "name": parts[3], "service": parts[4], "domain": parts[5], "host": parts[6], "address": parts[7], "port": parts[8]})
            self.memory.add_event("presence_service_scan", {"count": len(items)})
            return items[:120]
        return []

    def activate_wifi_profile(self, profile: str) -> str:
        profile = str(profile or "").strip()
        if not profile:
            raise ValueError("Wi-Fi profile name is required")
        if not self._which("nmcli"):
            raise RuntimeError("NetworkManager nmcli is not available")
        code, stdout, err = self._run(["nmcli", "connection", "up", "id", profile], 30)
        if code != 0:
            raise RuntimeError(err or stdout or "Could not activate Wi-Fi profile")
        self.passive_snapshot(save=True)
        return stdout or f"Activated Wi-Fi profile: {profile}"

    def connect_bluetooth(self, address: str) -> str:
        address = str(address or "").strip().upper()
        if not re.fullmatch(r"[0-9A-F]{2}(?::[0-9A-F]{2}){5}", address):
            raise ValueError("A valid paired Bluetooth MAC address is required")
        paired = {x["address"] for x in self._bluetooth_passive().get("paired", [])}
        if address not in paired:
            raise ValueError("DaQauntum v0.4.0 only connects already-paired Bluetooth devices. Pair through the OS first.")
        code, stdout, err = self._run(["bluetoothctl", "connect", address], 20)
        if code != 0 or "failed" in stdout.lower():
            raise RuntimeError(err or stdout or "Bluetooth connect failed")
        self.passive_snapshot(save=True)
        return stdout or f"Connected Bluetooth device: {address}"

    def context_messages(self, request: str) -> list[dict[str, str]]:
        text = (request or "").lower()
        cues = ("wifi", "wi-fi", "bluetooth", "battery", "temperature", "sensor", "around me", "nearby", "device", "network", "environment", "surroundings", "presence")
        snap = self.latest()
        sysm = snap.get("system") or {}
        important = False
        try:
            important = float(sysm.get("battery_percent", 100)) <= float(self.config.get("low_battery_percent", 15))
        except Exception:
            pass
        if snap.get("alerts"):
            important = True
        if not any(c in text for c in cues) and not important and not bool(self.config.get("context_always", False)):
            return []
        summary = self.describe(snap, compact=True)
        return [{"role": "system", "content": "AMBIENT PRESENCE (local observations, not user claims):\n" + summary}]

    def describe(self, sample: dict[str, Any] | None = None, *, compact: bool = False) -> str:
        sample = sample or self.latest()
        system = sample.get("system") or {}
        network = sample.get("network") or {}
        bt = sample.get("bluetooth") or {}
        sensors = sample.get("sensors") or {}
        lines = [f"host={system.get('hostname','?')} platform={system.get('platform','?')}"]
        wifi = network.get("wifi") or {}
        lines.append(f"wifi={'connected:'+str(wifi.get('connection')) if wifi.get('connected') else 'not connected'} local_ip={network.get('local_ip','?')}")
        lines.append(f"bluetooth={'available' if bt.get('available') else 'unavailable'} paired={len(bt.get('paired') or [])}")
        if system.get("battery_percent") is not None:
            lines.append(f"battery={system.get('battery_percent')}%")
        temps = system.get("temperatures") or []
        if temps:
            lines.append("temperatures=" + ", ".join(f"{x.get('sensor')}:{x.get('celsius')}C" for x in temps[:4]))
        lines.append(f"sensors=iio:{len(sensors.get('iio') or [])} camera:{len(sensors.get('video_devices') or [])} serial:{len(sensors.get('serial_devices') or [])} sound:{bool(sensors.get('sound_available'))}")
        if sample.get("alerts"):
            lines.append("alerts=" + "; ".join(str(x.get("message")) for x in sample.get("alerts", [])[:6]))
        if not compact and bt.get("paired"):
            lines.append("paired_bluetooth=" + ", ".join(x.get("name", x.get("address", "?")) for x in bt.get("paired", [])[:8]))
        return "\n".join(lines)

    @staticmethod
    def local_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
