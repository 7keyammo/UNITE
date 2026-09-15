from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any

from connectors import DeviceBridgeThread
from core.kernel import DaQauntumKernel
from interface import create_server
from realtime import DuplexServerThread
from presence import PresenceMonitorThread


def is_loopback(host: str) -> bool:
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def local_lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return "127.0.0.1"


@dataclass
class HostEndpoints:
    gui_url: str
    websocket_url: str | None
    device_bridge_url: str | None
    pairing_code: str | None


class DaQauntumHost:
    """Persistent process host shared by the daemon and desktop launcher."""

    def __init__(self, *, config_path: str | None = None, host: str | None = None, port: int | None = None, ws_port: int | None = None,
                 allow_lan: bool = False, device_bridge: bool = False, device_bridge_port: int | None = None):
        self.kernel = DaQauntumKernel(config_path)
        gui_cfg = self.kernel.config.get("gui", {})
        self.host = host or str(gui_cfg.get("host", "127.0.0.1"))
        self.port = int(port or gui_cfg.get("port", 8765))
        self.ws_port = int(ws_port or gui_cfg.get("websocket_port", self.port + 1))
        self.device_bridge_port = int(device_bridge_port or gui_cfg.get("device_bridge_port", 8767))
        self.device_bridge_requested = bool(device_bridge)
        if not is_loopback(self.host) and not allow_lan:
            raise RuntimeError("Refusing non-local main GUI bind. Use secure Tailscale Serve or --allow-lan explicitly.")
        self.http = None
        self.duplex = None
        self.bridge = None
        self.presence_monitor = None
        self.endpoints: HostEndpoints | None = None

    def start(self) -> HostEndpoints:
        last_error = None
        for candidate in range(self.port, self.port + 10):
            try:
                self.http = create_server(self.kernel, host=self.host, port=candidate)
                self.port = candidate
                break
            except OSError as exc:
                last_error = exc
        if self.http is None:
            raise RuntimeError(f"Could not bind GUI server near port {self.port}: {last_error}")

        url_host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        duplex_enabled = bool(self.kernel.config.get("full_duplex", {}).get("enabled", True)) and self.kernel.runtime.module_enabled("duplex")
        if duplex_enabled:
            for candidate in range(self.ws_port, self.ws_port + 10):
                try:
                    self.duplex = DuplexServerThread(self.kernel, self.host, candidate, self.kernel.config.get("full_duplex", {})).start()
                    self.ws_port = candidate
                    break
                except RuntimeError:
                    continue
            duplex_enabled = self.duplex is not None
        self.kernel.duplex_endpoint = {"enabled": duplex_enabled, "host": url_host, "port": self.ws_port if duplex_enabled else None}

        if self.kernel.runtime.module_enabled("presence") and bool(self.kernel.config.get("presence", {}).get("enabled", True)):
            try:
                self.kernel.presence.passive_snapshot(save=True)
            except Exception:
                pass
            self.presence_monitor = PresenceMonitorThread(
                self.kernel.presence,
                int(self.kernel.config.get("presence", {}).get("sample_interval_seconds", 20)),
            ).start()

        # Device drivers poll only when the user enabled at least one and gave
        # it an explicit allowlist; start() returns False otherwise.
        drivers_cfg = self.kernel.config.get("drivers", {})
        if bool(drivers_cfg.get("enabled", True)) and getattr(self.kernel, "drivers", None) is not None:
            try:
                self.kernel.drivers.start(int(drivers_cfg.get("poll_interval_seconds", 30)))
            except Exception:
                pass

        bridge_cfg = self.kernel.config.get("device_bridge", {})
        bridge_enabled = bool(self.device_bridge_requested or bridge_cfg.get("enabled", False)) and self.kernel.runtime.module_enabled("connectors")
        bridge_url = None
        pairing_code = None
        if bridge_enabled:
            bridge_host = str(bridge_cfg.get("host", "0.0.0.0"))
            for candidate in range(self.device_bridge_port, self.device_bridge_port + 10):
                try:
                    self.bridge = DeviceBridgeThread(self.kernel.connectors, bridge_host, candidate, bridge_cfg).start()
                    self.device_bridge_port = self.bridge.port
                    break
                except OSError:
                    continue
            if self.bridge is not None:
                bridge_url = f"http://{local_lan_ip()}:{self.device_bridge_port}/"
                pairing_code = self.bridge.pairing_code
                self.kernel.device_bridge_controller = self.bridge
                self.kernel.device_bridge_endpoint = {"enabled": True, "host": bridge_host, "port": self.device_bridge_port, "url": bridge_url, "pairing_code": pairing_code}
            else:
                bridge_enabled = False
        if not bridge_enabled:
            self.kernel.device_bridge_controller = None
            self.kernel.device_bridge_endpoint = {"enabled": False, "host": None, "port": None, "url": None, "pairing_code": None}

        self.endpoints = HostEndpoints(
            gui_url=f"http://{url_host}:{self.port}/",
            websocket_url=f"ws://{url_host}:{self.ws_port}" if duplex_enabled else None,
            device_bridge_url=bridge_url,
            pairing_code=pairing_code,
        )
        return self.endpoints

    def serve_forever(self) -> None:
        if self.http is None:
            self.start()
        assert self.http is not None
        try:
            self.http.serve_forever(poll_interval=0.2)
        finally:
            self.stop()

    def stop(self) -> None:
        if self.http is not None:
            try:
                self.http.server_close()
            except Exception:
                pass
            self.http = None
        if self.duplex is not None:
            try:
                self.duplex.stop()
            except Exception:
                pass
            self.duplex = None
        if self.bridge is not None:
            try:
                self.bridge.stop()
            except Exception:
                pass
            self.bridge = None
        if self.presence_monitor is not None:
            try:
                self.presence_monitor.stop()
            except Exception:
                pass
            self.presence_monitor = None
        if getattr(self.kernel, "drivers", None) is not None:
            try:
                self.kernel.drivers.stop()
            except Exception:
                pass
