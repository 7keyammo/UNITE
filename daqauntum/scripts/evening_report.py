#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from core.kernel import DaQauntumKernel


def main() -> int:
    parser = argparse.ArgumentParser(description="Build DaQauntum's evening learning digest.")
    parser.add_argument("--config", default=os.getenv("DAQAUNTUM_CONFIG", "config.yaml"))
    parser.add_argument("--date", default=None)
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args()
    kernel = DaQauntumKernel(args.config if Path(args.config).exists() else None)
    result = kernel.learning.build_evening_digest(args.date)
    if args.notify:
        kernel.learning.notify("DaQauntum Evening Report", f"Report ready\n{result['report_path']}")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
