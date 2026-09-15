#!/usr/bin/env python3
from __future__ import annotations
import shutil, subprocess, sys

if not shutil.which("systemctl"):
    print("systemctl not found")
    raise SystemExit(2)
for name in ("daqauntum-connectors.timer", "daqauntum-learn.timer", "daqauntum-report.timer"):
    print(f"\n== {name} ==")
    subprocess.run(["systemctl", "--user", "status", name, "--no-pager"], check=False)
print("\n== next runs ==")
subprocess.run(["systemctl", "--user", "list-timers", "daqauntum-*.timer", "--all", "--no-pager"], check=False)
