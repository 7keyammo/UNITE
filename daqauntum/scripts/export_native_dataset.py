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
    parser = argparse.ArgumentParser(description="Export DaQauntum's audited native-model seed dataset. Does not fine-tune weights.")
    parser.add_argument("--config", default=os.getenv("DAQAUNTUM_CONFIG", "config.yaml"))
    parser.add_argument("--output", default=None)
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    kernel = DaQauntumKernel(args.config if Path(args.config).exists() else None)
    result = kernel.native_model.export(args.output, limit=args.limit)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
