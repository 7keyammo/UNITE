#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "deploy" / "systemd"
USER_DIR = Path.home() / ".config" / "systemd" / "user"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), text=True, capture_output=True, check=check)


def render(src: Path, dst: Path, python: str) -> None:
    text = src.read_text(encoding="utf-8").replace("@ROOT@", str(ROOT)).replace("@PYTHON@", python)
    dst.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install DaQauntum connected-source sync, 7 AM learning, and 7 PM report timers for systemd --user.")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--no-enable", action="store_true")
    parser.add_argument("--render-only", default=None, help="Render units to this directory without calling systemctl")
    args = parser.parse_args()
    if args.render_only:
        target = Path(args.render_only).expanduser().resolve(); target.mkdir(parents=True, exist_ok=True)
        python = str((ROOT / ".venv" / "bin" / "python").resolve())
        if not Path(python).exists(): python = sys.executable
        render(TEMPLATES / "daqauntum-learn.service.in", target / "daqauntum-learn.service", python)
        shutil.copy2(TEMPLATES / "daqauntum-learn.timer", target / "daqauntum-learn.timer")
        render(TEMPLATES / "daqauntum-report.service.in", target / "daqauntum-report.service", python)
        shutil.copy2(TEMPLATES / "daqauntum-report.timer", target / "daqauntum-report.timer")
        render(TEMPLATES / "daqauntum-connectors.service.in", target / "daqauntum-connectors.service", python)
        shutil.copy2(TEMPLATES / "daqauntum-connectors.timer", target / "daqauntum-connectors.timer")
        print(f"Rendered DaQauntum timer units to {target}")
        return 0
    if not shutil.which("systemctl"):
        print("systemctl was not found. This installer targets Linux systemd user sessions.", file=sys.stderr)
        return 2
    USER_DIR.mkdir(parents=True, exist_ok=True)
    names = ["daqauntum-learn.service", "daqauntum-learn.timer", "daqauntum-report.service", "daqauntum-report.timer", "daqauntum-connectors.service", "daqauntum-connectors.timer"]
    if args.uninstall:
        run("systemctl", "--user", "disable", "--now", "daqauntum-learn.timer", "daqauntum-report.timer", "daqauntum-connectors.timer", check=False)
        for name in names:
            (USER_DIR / name).unlink(missing_ok=True)
        run("systemctl", "--user", "daemon-reload", check=False)
        print("Removed DaQauntum learning timers.")
        return 0

    python = str((ROOT / ".venv" / "bin" / "python").resolve())
    if not Path(python).exists():
        python = sys.executable
    render(TEMPLATES / "daqauntum-learn.service.in", USER_DIR / "daqauntum-learn.service", python)
    shutil.copy2(TEMPLATES / "daqauntum-learn.timer", USER_DIR / "daqauntum-learn.timer")
    render(TEMPLATES / "daqauntum-report.service.in", USER_DIR / "daqauntum-report.service", python)
    shutil.copy2(TEMPLATES / "daqauntum-report.timer", USER_DIR / "daqauntum-report.timer")
    render(TEMPLATES / "daqauntum-connectors.service.in", USER_DIR / "daqauntum-connectors.service", python)
    shutil.copy2(TEMPLATES / "daqauntum-connectors.timer", USER_DIR / "daqauntum-connectors.timer")
    run("systemctl", "--user", "daemon-reload")
    if not args.no_enable:
        run("systemctl", "--user", "enable", "--now", "daqauntum-connectors.timer", "daqauntum-learn.timer", "daqauntum-report.timer")
    print("Installed DaQauntum user timers:")
    print("  every 15m  daqauntum-connectors.timer")
    print("  07:00      daqauntum-learn.timer")
    print("  19:00      daqauntum-report.timer")
    print(f"  root   {ROOT}")
    print(f"  python {python}")
    try:
        cp = run("loginctl", "show-user", os.environ.get("USER", ""), "-p", "Linger", "--value", check=False)
        linger = cp.stdout.strip().lower()
        if linger == "yes":
            print("User linger: enabled (timers may run while logged out).")
        else:
            print("User linger: not confirmed. To allow user timers while logged out, an administrator may need: loginctl enable-linger $USER")
    except Exception:
        print("User linger: unable to check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
