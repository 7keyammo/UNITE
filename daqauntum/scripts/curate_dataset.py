#!/usr/bin/env python3
"""Curate DaQauntum's approved training memories into an auditable corpus.

Applies quality gates (including a credential filter), removes exact and near
duplicates, balances categories, and writes deterministic train/validation/test
splits with a checksummed manifest.

No model weights are touched. Training is a separate, explicitly invoked step.

    PYTHONPATH=. python scripts/curate_dataset.py
    PYTHONPATH=. python scripts/curate_dataset.py --min-confidence 0.8 --max-per-category 2000
"""
from __future__ import annotations

import argparse
import json

from core.kernel import DaQauntumKernel


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate a DaQauntum native-model corpus")
    parser.add_argument("--limit", type=int, default=20000, help="Maximum candidate memories to read")
    parser.add_argument("--min-confidence", type=float, default=None)
    parser.add_argument("--max-per-category", type=int, default=None)
    parser.add_argument("--near-duplicate-threshold", type=float, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", default="", help="Directory for the corpus (default: a timestamped one)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kernel = DaQauntumKernel()
    curator = kernel.model_lab.curator
    if args.min_confidence is not None:
        curator.min_confidence = args.min_confidence
    if args.max_per_category is not None:
        curator.max_per_category = args.max_per_category
    if args.near_duplicate_threshold is not None:
        curator.near_duplicate_threshold = args.near_duplicate_threshold
    if args.seed is not None:
        curator.seed = args.seed

    print("DaQauntum dataset curation")
    print("=" * 52)
    manifest = kernel.model_lab.curate(limit=args.limit, output_dir=args.output or None)

    print(f"Received:  {manifest['received']}")
    print(f"Rejected:  {manifest['rejected']}  {manifest.get('rejection_reasons') or ''}")
    print(f"Duplicates removed: {manifest['duplicates_removed']}")
    print(f"Kept:      {manifest['kept']}")
    print(f"Splits:    {manifest['splits']}")
    print(f"Leakage:   {'clean' if manifest['leakage']['clean'] else 'COLLISIONS FOUND'}")
    print(f"\nWrote {manifest['manifest_path']}")

    if manifest["kept"] == 0:
        print(
            "\nNo examples survived curation. DaQauntum promotes a turn to a training memory "
            "only when it is confident and useful, so an empty corpus usually means the system "
            "has not been used enough yet, not that something is broken."
        )
    if args.json:
        print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
