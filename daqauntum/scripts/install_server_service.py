#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy/systemd/daqauntum-server.service.in"
USER_DIR = Path.home() / ".config/systemd/user"
SERVICE = USER_DIR / "daqauntum-server.service"


def main() -> None:
    if not shutil.which("systemctl"):
        raise SystemExit("systemctl is not available. On non-systemd systems, run python daqauntum_server.py with your preferred service manager.")
    python = Path(sys.executable).resolve()
    text = TEMPLATE.read_text(encoding="utf-8").replace("__PROJECT_ROOT__", str(ROOT)).replace("__PYTHON__", str(python))
    USER_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(text, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", "daqauntum-server.service"], check=True)
    print(f"Installed: {SERVICE}")
    print("DaQauntum will now start as your user service.")
    print("Status: systemctl --user status daqauntum-server.service")
    print("GUI:    python daqauntum_gui.py --client-only")
    print("For access while fully logged out, verify linger with: loginctl show-user $USER -p Linger")


if __name__ == "__main__":
    main()
