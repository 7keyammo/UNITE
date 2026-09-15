#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from core.kernel import DaQauntumKernel


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize DaQauntum connected knowledge sources.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--learning-only", action="store_true", help="Sync only connectors marked Learn from this source")
    args = parser.parse_args()
    kernel = DaQauntumKernel(args.config)
    result = kernel.connectors.sync_all(learning_only=args.learning_only)
    print(json.dumps(result, indent=2, default=str))
    return 0 if not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
