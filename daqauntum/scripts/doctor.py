#!/usr/bin/env python3
"""One diagnostic for the whole DaQauntum host.

KNOWN_GAPS records that diagnostics were fragmented across several doctor
scripts, and TASKS.md P0.1 asks for a single SYSTEM_HEALTH.md written from the
real machine. This runs every check in one pass, says plainly what is working
and what is not, and gives the exact command to fix each problem.

It is read-only with one deliberate exception: it takes a passive presence
snapshot, exactly as the presence doctor already did. It starts no radio scan,
connects to no device, and changes no configuration.

    PYTHONPATH=. python scripts/doctor.py
    PYTHONPATH=. python scripts/doctor.py --redact      # safe to share
    PYTHONPATH=. python scripts/doctor.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.kernel import DaQauntumKernel


PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
ICONS = {PASS: "✓", WARN: "!", FAIL: "✗", INFO: "·"}


@dataclass
class Check:
    section: str
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "data": self.data,
        }


class Doctor:
    def __init__(self, *, redact: bool = False):
        self.redact = redact
        self.checks: list[Check] = []
        self.kernel: DaQauntumKernel | None = None

    # Helpers -----------------------------------------------------------------
    def add(self, section: str, name: str, status: str, detail: str = "", fix: str = "", **data: Any) -> None:
        self.checks.append(Check(section, name, status, detail, fix, data))

    def hide(self, value: Any) -> str:
        """Redact host-identifying values when the report will be shared."""
        if not self.redact:
            return str(value)
        return "<redacted>" if value not in (None, "", "unknown") else str(value)

    @staticmethod
    def _run(command: list[str], timeout: float = 8.0) -> tuple[int, str]:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, (proc.stdout or proc.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, str(exc)

    # Sections ----------------------------------------------------------------
    def check_environment(self) -> None:
        version = sys.version_info
        if version >= (3, 10):
            self.add("Environment", "Python version", PASS, f"{platform.python_version()}")
        else:
            self.add(
                "Environment", "Python version", FAIL,
                f"{platform.python_version()} is too old",
                "DaQauntum needs Python 3.10 or newer.",
            )
        self.add("Environment", "Platform", INFO, f"{platform.system()} {platform.release()}")
        self.add(
            "Environment", "Virtual environment",
            PASS if sys.prefix != sys.base_prefix else WARN,
            "running inside a venv" if sys.prefix != sys.base_prefix else "not running inside a venv",
            "python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt",
        )

        required = {"yaml": "PyYAML", "rich": "rich", "websockets": "websockets", "psutil": "psutil"}
        missing = []
        for module, package in required.items():
            try:
                __import__(module)
            except Exception:
                missing.append(package)
        if missing:
            self.add(
                "Environment", "Core dependencies", FAIL,
                f"missing: {', '.join(missing)}",
                "pip install -r requirements.txt",
            )
        else:
            self.add("Environment", "Core dependencies", PASS, "all core packages importable")

        optional = {
            "faster_whisper": ("Local STT", "pip install -r requirements-voice.txt"),
            "bleak": ("BLE driver", "pip install bleak"),
            "serial": ("Serial driver", "pip install pyserial"),
            "pypdf": ("PDF source indexing", "pip install pypdf"),
        }
        for module, (label, fix) in optional.items():
            try:
                __import__(module)
                self.add("Environment", f"Optional: {label}", PASS, f"{module} installed")
            except Exception:
                self.add("Environment", f"Optional: {label}", INFO, f"{module} not installed", fix)

        config_exists = Path("config.yaml").exists()
        self.add(
            "Environment", "config.yaml",
            PASS if config_exists else WARN,
            "present" if config_exists else "missing; defaults are in use",
            "cp -n config.example.yaml config.yaml",
        )

    def check_kernel(self) -> None:
        started = time.perf_counter()
        try:
            self.kernel = DaQauntumKernel()
        except Exception as exc:
            self.add(
                "Runtime", "Kernel boot", FAIL, f"{type(exc).__name__}: {exc}",
                "Fix the error above; every other check depends on the kernel starting.",
            )
            return
        elapsed = (time.perf_counter() - started) * 1000.0
        self.add("Runtime", "Kernel boot", PASS, f"booted in {elapsed:.0f} ms", version=self.kernel.config.get("version"))
        self.add("Runtime", "Version", INFO, str(self.kernel.config.get("version", "?")))
        self.add(
            "Runtime", "Permission level", INFO, f"L{self.kernel.permissions.level}",
            "Permission level is deliberate policy, not a problem to fix.",
        )

    def check_brain(self) -> None:
        if not self.kernel:
            return
        real_routes: list[str] = []
        for provider in ("ollama", "claude_cli", "openai", "anthropic"):
            try:
                adapter = self.kernel.brain._build(provider)  # noqa: SLF001 - diagnostic introspection
                status = adapter.available()
            except Exception as exc:
                self.add("Brain", provider, INFO, f"unavailable: {exc}")
                continue
            if status.available:
                real_routes.append(provider)
                self.add("Brain", provider, PASS, f"{status.detail or 'available'} (model {adapter.model})")
            else:
                self.add("Brain", provider, INFO, status.detail or "not configured", self._brain_fix(provider))

        if real_routes:
            self.add("Brain", "Reasoning capability", PASS, f"usable routes: {', '.join(real_routes)}")
        else:
            self.add(
                "Brain", "Reasoning capability", FAIL,
                "no real model provider is available; only the mock fallback would answer",
                "Install and start Ollama and pull a model, authenticate the claude CLI, "
                "or set OPENAI_API_KEY / ANTHROPIC_API_KEY.",
            )

        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2.0) as response:
                models = [m.get("name") for m in json.loads(response.read().decode("utf-8")).get("models", [])]
            self.add(
                "Brain", "Ollama models",
                PASS if models else WARN,
                ", ".join(models[:10]) if models else "Ollama is running but no model is pulled",
                "ollama pull gemma3",
            )
        except Exception:
            pass

    @staticmethod
    def _brain_fix(provider: str) -> str:
        return {
            "ollama": "Install Ollama, start it, then: ollama pull gemma3",
            "claude_cli": "Install and authenticate the claude CLI.",
            "openai": "Set OPENAI_API_KEY in .env or the environment.",
            "anthropic": "Set ANTHROPIC_API_KEY in .env or the environment.",
        }.get(provider, "")

    def check_voice(self) -> None:
        if not self.kernel:
            return
        try:
            status = self.kernel.voice.status(refresh=True)
        except Exception as exc:
            self.add("Voice", "Voice engine", FAIL, f"{type(exc).__name__}: {exc}")
            return
        stt, tts = status.get("stt") or {}, status.get("tts") or {}
        self.add(
            "Voice", "Local speech-to-text",
            PASS if stt.get("available") else WARN,
            str(stt.get("detail") or stt.get("engine") or ("ready" if stt.get("available") else "not configured")),
            "pip install -r requirements-voice.txt && PYTHONPATH=. python scripts/setup_local_voice.py",
        )
        self.add(
            "Voice", "Text-to-speech",
            PASS if tts.get("available") else WARN,
            str(tts.get("detail") or tts.get("engine") or ("ready" if tts.get("available") else "not configured")),
            "Install a system voice (espeak-ng / say) or Piper.",
        )
        if not stt.get("available"):
            self.add(
                "Voice", "LOCAL-mode speech", INFO,
                "Without local STT, LOCAL operation mode has no speech input. "
                "It will degrade rather than silently use a hosted service.",
            )

        # Measured, not guessed: these appear only after real spoken turns.
        latency = self.kernel.latency.stats()
        if not latency.get("turns"):
            self.add(
                "Voice", "Measured turn latency", INFO,
                "no spoken turns recorded yet",
                "Hold a voice conversation, then re-run. The hear/think/speak breakdown "
                "is measured from live turns, never simulated.",
            )
        else:
            stages = latency.get("stages") or {}
            perceived = latency.get("perceived") or {}
            slowest = latency.get("slowest_stage")
            self.add(
                "Voice", "Measured turn latency",
                PASS if (perceived.get("median_ms") or 99999) < 1500 else WARN,
                f"perceived median {perceived.get('median_ms', '—')} ms over {latency['turns']} turn(s); "
                f"slowest stage: {slowest}",
                {
                    "transcribe": "Local STT is the bottleneck. Try a smaller whisper model or int8 quantization.",
                    "think": "The model is the bottleneck. Benchmark providers and point REALTIME at the fastest useful one.",
                    "speak": "Speech synthesis is the bottleneck. Try Piper locally, or shorten the first spoken sentence.",
                }.get(slowest, ""),
            )
            for name, data in stages.items():
                self.add("Voice", f"Stage: {name}", INFO,
                         f"median {data['median_ms']} ms · p95 {data['p95_ms']} ms")

    def check_memory(self) -> None:
        if not self.kernel:
            return
        try:
            status = self.kernel.status()
        except Exception as exc:
            self.add("Memory", "Status", FAIL, f"{type(exc).__name__}: {exc}")
            return
        memory = status.get("memory") or {}
        self.add(
            "Memory", "Database", PASS,
            f"{memory.get('messages', 0)} messages · {memory.get('structured_total', 0)} structured memories",
            db_path=self.kernel.config["memory"]["db_path"],
        )
        graph = status.get("knowledge_graph") or {}
        self.add("Memory", "Knowledge graph", PASS, f"{graph.get('nodes', 0)} nodes · {graph.get('edges', 0)} edges")
        sources = status.get("sources") or {}
        self.add("Memory", "Sources", PASS, f"{sources.get('sources', 0)} indexed sources")

        path = Path(self.kernel.config["memory"]["db_path"])
        if path.exists():
            size_mb = path.stat().st_size / (1024 * 1024)
            self.add(
                "Memory", "Database size",
                WARN if size_mb > 2048 else PASS, f"{size_mb:.1f} MB",
                "Consider the optional PostgreSQL/pgvector mirror for very large stores." if size_mb > 2048 else "",
            )

    def check_presence_and_events(self) -> None:
        if not self.kernel:
            return
        try:
            snapshot = self.kernel.presence.passive_snapshot(save=True)
        except Exception as exc:
            self.add("Presence", "Passive snapshot", FAIL, f"{type(exc).__name__}: {exc}")
            snapshot = {}
        if snapshot:
            system = snapshot.get("system") or {}
            self.add("Presence", "Host", INFO, f"{self.hide(system.get('hostname'))} ({system.get('platform')})")
            if system.get("battery_percent") is not None:
                self.add("Presence", "Battery", PASS, f"{system.get('battery_percent')}% (plugged: {system.get('power_plugged')})")
            network = (snapshot.get("network") or {}).get("wifi") or {}
            self.add(
                "Presence", "Network",
                PASS if network.get("connected") else INFO,
                f"wifi connected to {self.hide(network.get('connection'))}" if network.get("connected") else "no Wi-Fi connection reported",
            )
            self.add(
                "Presence", "Local sensors", INFO,
                f"{len((snapshot.get('sensors') or {}).get('serial_devices') or [])} serial · "
                f"{len((snapshot.get('sensors') or {}).get('video_devices') or [])} camera",
            )
            self.add("Presence", "Radio discovery", INFO, "not run; scanning is always explicit and user-triggered")

        events = self.kernel.events.stats()
        self.add(
            "Events", "Event bus",
            PASS if events["bus"]["enabled"] else WARN,
            f"{events['bus']['events']} events · {events['bus']['suppressed_repeats']} duplicates suppressed",
        )
        reactions = events["reactions"]
        self.add(
            "Events", "Reaction rules", PASS,
            f"{reactions['rules_enabled']}/{reactions['rules']} enabled · fired {reactions['fires']}× · "
            f"{reactions['pending_proposals']} proposal(s) awaiting approval",
        )
        if reactions["proposals_executed"]:  # Must always be zero by construction.
            self.add(
                "Events", "Reaction safety", FAIL,
                "a reaction reported executing an action directly",
                "This violates the v0.4.1 invariant that reactions only propose. Report it.",
            )
        else:
            self.add("Events", "Reaction safety", PASS, "no reaction has executed an action directly")
        self.add("Events", "Notifications", INFO, f"{events['notifications']['pending']} pending")

        for driver in self.kernel.drivers.statuses():
            if driver["usable"]:
                status_value, detail = PASS, f"ready for {len(driver['targets'])} approved target(s)"
            elif driver["enabled"]:
                status_value, detail = WARN, driver["detail"]
            else:
                status_value, detail = INFO, "disabled"
            self.add(
                "Drivers", driver["name"], status_value, detail,
                f"Configure drivers.{driver['name']} in config.yaml." if driver["enabled"] and not driver["usable"] else "",
                writes_allowed=driver["writes_allowed"],
            )

    def check_remote_access(self) -> None:
        """Report the remote-access posture honestly, including the gaps."""
        if not self.kernel:
            return
        identity = getattr(self.kernel, "identity", None)
        if identity is None:
            self.add("Remote access", "Device identity", WARN, "not available in this build")
            return
        stats = identity.stats()
        counts = stats.get("devices") or {}
        active = int(counts.get("active", 0))
        self.add(
            "Remote access", "Device identity",
            PASS if stats["enabled"] else FAIL,
            f"{active} active device(s), {counts.get('revoked', 0)} revoked, {stats['active_tokens']} live token(s)",
            "Set identity.enabled: true so remote clients must authenticate." if not stats["enabled"] else "",
        )
        self.add(
            "Remote access", "Remote authentication",
            PASS if stats["require_auth_for_remote"] else FAIL,
            "required for non-loopback callers" if stats["require_auth_for_remote"]
            else "NOT required - any host that can reach the port has full control",
            "Set identity.require_auth_for_remote: true.",
        )
        if stats.get("open_enrollment_codes"):
            self.add(
                "Remote access", "Enrollment codes", WARN,
                f"{stats['open_enrollment_codes']} unused code is still live",
                "Codes expire on their own; create a new one to supersede it if it was not used deliberately.",
            )

        gui = self.kernel.config.get("gui", {})
        host = str(gui.get("host", "127.0.0.1"))
        loopback_bound = host.startswith("127.") or host in {"localhost", "::1"}
        self.add(
            "Remote access", "GUI bind address",
            PASS if loopback_bound else WARN,
            f"{host}:{gui.get('port', 8765)}" + ("" if loopback_bound else " - reachable beyond this machine"),
            "Prefer the default loopback bind and reach DaQauntum over Tailscale rather than exposing the port.",
        )

        failures = [
            item for item in identity.recent_auth_events(limit=100)
            if item.get("event") in {"auth_failed", "enrollment_failed", "auth_throttled", "enrollment_throttled", "scope_denied"}
        ]
        if failures:
            self.add(
                "Remote access", "Recent auth failures",
                WARN if len(failures) > 5 else INFO,
                f"{len(failures)} in the last 100 auth events",
                "Check the Devices view. Repeated failures from an unknown address are worth investigating.",
            )
        else:
            self.add("Remote access", "Recent auth failures", PASS, "none recorded")

        push = (self.kernel.events.stats().get("push") or {})
        self.add(
            "Remote access", "Push notifications",
            PASS if push.get("available") else INFO,
            push.get("detail", "not configured"),
            "" if push.get("available") else "Optional. Point events.push.url at a webhook you control.",
        )

        bridge = self.kernel.config.get("device_bridge", {})
        self.add(
            "Remote access", "Device bridge",
            INFO if not bridge.get("enabled") else WARN,
            "disabled" if not bridge.get("enabled")
            else f"enabled on {bridge.get('host')}:{bridge.get('port')} - a separate, narrower ingestion surface",
            "The bridge has its own pairing and is intentionally narrower than the control API.",
        )

    def check_integrations(self) -> None:
        if not self.kernel:
            return
        try:
            statuses = self.kernel.integrations.statuses()
        except Exception as exc:
            self.add("Integrations", "Integration manager", FAIL, f"{type(exc).__name__}: {exc}")
            return
        for item in statuses:
            if item.get("healthy"):
                self.add("Integrations", item["name"], PASS, str(item.get("detail", "")))
            elif item.get("configured"):
                self.add("Integrations", item["name"], WARN, str(item.get("detail", "")))
            else:
                self.add("Integrations", item["name"], INFO, str(item.get("detail", "")))

    def check_service(self) -> None:
        if not shutil.which("systemctl"):
            self.add("Service", "systemd", INFO, "systemctl not available on this host")
            return
        for unit, installer in (
            ("daqauntum-server.service", "PYTHONPATH=. python scripts/install_server_service.py"),
            ("daqauntum-learn.timer", "PYTHONPATH=. python scripts/install_learning_timers.py"),
            ("daqauntum-report.timer", "PYTHONPATH=. python scripts/install_learning_timers.py"),
        ):
            code, output = self._run(["systemctl", "--user", "is-active", unit], 5)
            state = output.strip() or "unknown"
            if state == "active":
                self.add("Service", unit, PASS, "active")
            elif state in {"inactive", "failed"}:
                self.add("Service", unit, WARN, state, f"systemctl --user status {unit}")
            else:
                self.add("Service", unit, INFO, f"not installed ({state})", installer)

        code, output = self._run(["loginctl", "show-user", os.getenv("USER", ""), "-p", "Linger"], 5)
        if code == 0 and "Linger=" in output:
            lingering = output.strip().endswith("yes")
            self.add(
                "Service", "User lingering",
                PASS if lingering else WARN,
                "enabled; user services survive logout" if lingering else "disabled; user services stop at logout",
                f"sudo loginctl enable-linger {os.getenv('USER', '$USER')}",
            )

    def check_release(self) -> None:
        code, output = self._run([sys.executable, "scripts/verify_release.py"], 60)
        if code == 0:
            self.add("Release", "File manifest", PASS, output.splitlines()[-1] if output else "verified")
        else:
            self.add(
                "Release", "File manifest", WARN,
                (output.splitlines()[-1] if output else "verification failed"),
                "PYTHONPATH=. python scripts/build_release_manifest.py  # after intentional edits",
            )

    # Reporting ---------------------------------------------------------------
    def run(self) -> None:
        self.check_environment()
        self.check_kernel()
        self.check_brain()
        self.check_voice()
        self.check_memory()
        self.check_presence_and_events()
        self.check_remote_access()
        self.check_integrations()
        self.check_service()
        self.check_release()

    def counts(self) -> dict[str, int]:
        counts = {PASS: 0, WARN: 0, FAIL: 0, INFO: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts

    def verdict(self) -> str:
        counts = self.counts()
        if counts[FAIL]:
            return "NOT READY"
        if counts[WARN]:
            return "USABLE WITH GAPS"
        return "READY"

    def print_console(self) -> None:
        current = None
        for check in self.checks:
            if check.section != current:
                current = check.section
                print(f"\n{current.upper()}")
                print("-" * len(current))
            print(f"  {ICONS[check.status]} {check.name}: {check.detail}")
            if check.fix and check.status in {WARN, FAIL}:
                print(f"      fix: {check.fix}")
        counts = self.counts()
        print(
            f"\n{self.verdict()} — {counts[PASS]} pass · {counts[WARN]} warn · "
            f"{counts[FAIL]} fail · {counts[INFO]} informational"
        )

    def render_markdown(self) -> str:
        counts = self.counts()
        lines = [
            "# DaQauntum System Health",
            "",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Verdict: **{self.verdict()}**",
            f"- Results: {counts[PASS]} pass · {counts[WARN]} warn · {counts[FAIL]} fail · {counts[INFO]} informational",
            f"- Redacted: {'yes' if self.redact else 'no'}",
            "",
        ]
        if not self.redact:
            lines += [
                "> This file describes a real machine and may name its host, network and devices.",
                "> It lives under `data/`, which is excluded from Git and release archives. "
                "Re-run with `--redact` before sharing it.",
                "",
            ]
        blocking = [c for c in self.checks if c.status == FAIL]
        warnings = [c for c in self.checks if c.status == WARN]
        if blocking or warnings:
            lines += ["## What to fix first", ""]
            for check in blocking + warnings:
                marker = "**BLOCKING**" if check.status == FAIL else "Gap"
                lines.append(f"- {marker} — {check.section} / {check.name}: {check.detail}")
                if check.fix:
                    lines.append(f"  - `{check.fix}`")
            lines.append("")
        else:
            lines += ["## What to fix first", "", "Nothing. Every check passed.", ""]

        current = None
        for check in self.checks:
            if check.section != current:
                current = check.section
                lines += [f"## {current}", "", "| | Check | Detail |", "|---|---|---|"]
            lines.append(f"| {ICONS[check.status]} | {check.name} | {check.detail or '—'} |")
        lines += ["", "_Runtime artifact. Regenerate with `PYTHONPATH=. python scripts/doctor.py`._"]
        return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="DaQauntum consolidated host diagnostic")
    parser.add_argument("--redact", action="store_true", help="Omit host, network and device identifiers")
    parser.add_argument("--output", default="data/diagnostics/SYSTEM_HEALTH.md")
    parser.add_argument("--json", action="store_true", help="Print all checks as JSON")
    parser.add_argument("--quiet", action="store_true", help="Write the report without console output")
    args = parser.parse_args()

    print("DaQauntum host doctor")
    print("=" * 52)
    doctor = Doctor(redact=args.redact)
    doctor.run()
    if not args.quiet:
        doctor.print_console()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doctor.render_markdown(), encoding="utf-8")
    print(f"\nWrote {output}")

    if args.json:
        print(json.dumps([check.as_dict() for check in doctor.checks], indent=2, default=str))

    # A blocking failure exits non-zero so this can gate a setup script.
    raise SystemExit(1 if doctor.counts()[FAIL] else 0)


if __name__ == "__main__":
    main()
