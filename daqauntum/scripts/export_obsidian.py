#!/usr/bin/env python3
from __future__ import annotations

import argparse
from core.kernel import DaQauntumKernel


def main() -> None:
    ap = argparse.ArgumentParser(description="Export DaQauntum workspaces as an Obsidian-compatible Markdown vault")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    kernel = DaQauntumKernel(args.config)
    result = kernel.obsidian.export()
    print(f"Exported {result['workspaces']} workspace(s)")
    print(f"Open this folder as an Obsidian vault: {result['root']}")
    print(f"Home: {result['home']}")


if __name__ == "__main__":
    main()
