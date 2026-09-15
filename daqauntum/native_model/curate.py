from __future__ import annotations

import hashlib
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from native_model.schema import (
    CATEGORIES,
    SCHEMA_VERSION,
    TrainingExample,
    jaccard,
    shingles,
    validate_example,
    write_jsonl,
)


class DatasetCurator:
    """Turn raw candidate examples into an auditable, split corpus.

    The pipeline is deterministic given the same input and seed, so two runs
    produce the same splits and a training result can be traced back to the
    exact corpus that produced it.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = dict(config or {})
        self.min_confidence = float(self.config.get("min_confidence", 0.6))
        self.min_user_chars = int(self.config.get("min_user_chars", 8))
        self.min_assistant_chars = int(self.config.get("min_assistant_chars", 16))
        self.max_chars = int(self.config.get("max_chars", 24_000))
        self.near_duplicate_threshold = float(self.config.get("near_duplicate_threshold", 0.85))
        self.max_per_category = int(self.config.get("max_per_category", 0))  # 0 = unlimited
        self.seed = int(self.config.get("seed", 20260915))
        self.splits = dict(self.config.get("splits", {"train": 0.8, "validation": 0.1, "test": 0.1}))

    # Gates -------------------------------------------------------------------
    def apply_gates(self, examples: list[TrainingExample]) -> tuple[list[TrainingExample], list[dict[str, Any]]]:
        kept: list[TrainingExample] = []
        rejected: list[dict[str, Any]] = []
        for example in examples:
            reasons = validate_example(
                example,
                min_user_chars=self.min_user_chars,
                min_assistant_chars=self.min_assistant_chars,
                max_chars=self.max_chars,
                min_confidence=self.min_confidence,
            )
            if reasons:
                rejected.append({
                    "example_id": example.exact_key[:16],
                    "category": example.category,
                    "reasons": [r.as_dict() for r in reasons],
                })
                continue
            kept.append(example)
        return kept, rejected

    # Deduplication -----------------------------------------------------------
    def deduplicate(self, examples: list[TrainingExample]) -> tuple[list[TrainingExample], list[dict[str, Any]]]:
        """Collapse exact duplicates, then near-duplicates within a category.

        Near-duplicate comparison is bucketed by category and by the first
        shingle, so this stays close to linear instead of comparing every pair;
        a corpus of a few tens of thousands of examples is the target size.
        """
        kept: list[TrainingExample] = []
        dropped: list[dict[str, Any]] = []
        seen_exact: dict[str, str] = {}

        for example in examples:
            key = example.exact_key
            if key in seen_exact:
                dropped.append({"example_id": key[:16], "reason": "exact_duplicate", "of": seen_exact[key][:16]})
                continue
            seen_exact[key] = key
            kept.append(example)

        if self.near_duplicate_threshold >= 1.0:
            return kept, dropped

        buckets: dict[tuple[str, str], list[tuple[TrainingExample, set[str]]]] = defaultdict(list)
        survivors: list[TrainingExample] = []
        for example in kept:
            grams = shingles(example.user + " " + example.assistant)
            bucket_key = (example.category, next(iter(sorted(grams))) if grams else "")
            duplicate_of = None
            for other, other_grams in buckets[bucket_key]:
                if jaccard(grams, other_grams) >= self.near_duplicate_threshold:
                    duplicate_of = other
                    break
            if duplicate_of is not None:
                dropped.append({
                    "example_id": example.exact_key[:16],
                    "reason": "near_duplicate",
                    "of": duplicate_of.exact_key[:16],
                })
                continue
            buckets[bucket_key].append((example, grams))
            survivors.append(example)
        return survivors, dropped

    # Balancing ---------------------------------------------------------------
    def balance(self, examples: list[TrainingExample]) -> tuple[list[TrainingExample], list[dict[str, Any]]]:
        """Cap any category that would otherwise dominate the corpus."""
        if self.max_per_category <= 0:
            return examples, []
        rng = random.Random(self.seed)
        by_category: dict[str, list[TrainingExample]] = defaultdict(list)
        for example in examples:
            by_category[example.category].append(example)
        kept: list[TrainingExample] = []
        dropped: list[dict[str, Any]] = []
        for category, items in by_category.items():
            if len(items) <= self.max_per_category:
                kept.extend(items)
                continue
            # Keep the highest-confidence examples, breaking ties deterministically.
            ordered = sorted(items, key=lambda e: (-e.confidence, e.exact_key))
            kept.extend(ordered[: self.max_per_category])
            for example in ordered[self.max_per_category :]:
                dropped.append({"example_id": example.exact_key[:16], "reason": "category_cap", "category": category})
        rng.shuffle(kept)
        return kept, dropped

    # Splits ------------------------------------------------------------------
    def split(self, examples: list[TrainingExample]) -> dict[str, list[TrainingExample]]:
        """Stratified, deterministic train/validation/test split.

        The split is keyed off a hash of the example rather than a shuffle
        position, so adding new examples does not reshuffle existing ones
        between train and test - which would silently leak test data into
        training on the next curation run.
        """
        weights = {k: max(0.0, float(v)) for k, v in self.splits.items()}
        total = sum(weights.values()) or 1.0
        fractions = {k: v / total for k, v in weights.items()}
        names = list(fractions)

        buckets: dict[str, list[TrainingExample]] = {name: [] for name in names}
        by_category: dict[str, list[TrainingExample]] = defaultdict(list)
        for example in examples:
            by_category[example.category].append(example)

        for category, items in by_category.items():
            ordered = sorted(items, key=lambda e: self._split_hash(e))
            count = len(ordered)
            # Cumulative boundaries rather than per-split rounding, so rounding
            # error does not accumulate and starve the last split.
            cumulative = 0.0
            start = 0
            for index, name in enumerate(names):
                cumulative += fractions[name]
                end = count if index == len(names) - 1 else int(round(count * cumulative))
                buckets[name].extend(ordered[start:end])
                start = end
        return buckets

    def _split_hash(self, example: TrainingExample) -> str:
        return hashlib.sha256(f"{self.seed}:{example.exact_key}".encode("utf-8")).hexdigest()

    # Pipeline ----------------------------------------------------------------
    def curate(self, examples: list[TrainingExample]) -> dict[str, Any]:
        started = time.perf_counter()
        received = len(examples)
        gated, rejected = self.apply_gates(examples)
        deduped, duplicates = self.deduplicate(gated)
        balanced, capped = self.balance(deduped)
        splits = self.split(balanced)

        leaked = self._check_leakage(splits)
        return {
            "schema": SCHEMA_VERSION,
            "seed": self.seed,
            "received": received,
            "rejected": len(rejected),
            "duplicates_removed": len(duplicates),
            "capped": len(capped),
            "kept": len(balanced),
            "splits": {name: len(items) for name, items in splits.items()},
            "category_counts": dict(Counter(e.category for e in balanced)),
            "split_examples": splits,
            "rejection_reasons": dict(Counter(r["reasons"][0]["code"] for r in rejected if r["reasons"])),
            "rejections": rejected[:200],
            "duplicate_samples": duplicates[:50],
            "leakage": leaked,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    @staticmethod
    def _check_leakage(splits: dict[str, list[TrainingExample]]) -> dict[str, Any]:
        """Prove no example appears in more than one split.

        Reported rather than assumed: a silent leak makes every evaluation
        number meaningless, and it is cheap to check.
        """
        seen: dict[str, str] = {}
        collisions: list[dict[str, str]] = []
        for name, items in splits.items():
            for example in items:
                key = example.exact_key
                if key in seen and seen[key] != name:
                    collisions.append({"example_id": key[:16], "in": seen[key], "also_in": name})
                seen[key] = name
        return {"clean": not collisions, "collisions": collisions[:20]}

    def write(self, result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        """Write splits and an auditable manifest."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        checksums: dict[str, str] = {}
        for name, items in (result.get("split_examples") or {}).items():
            path = output_dir / f"{name}.jsonl"
            write_jsonl(path, items)
            files[name] = str(path)
            checksums[name] = hashlib.sha256(path.read_bytes()).hexdigest()

        manifest = {key: value for key, value in result.items() if key != "split_examples"}
        manifest["files"] = files
        manifest["checksums"] = checksums
        manifest["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        manifest_path = output_dir / "MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        manifest["manifest_path"] = str(manifest_path)
        return manifest


def summarize(manifest: dict[str, Any]) -> str:
    """Human-readable curation report."""
    lines = [
        "# DaQauntum Dataset Curation",
        "",
        f"- Schema: `{manifest.get('schema')}`",
        f"- Seed: {manifest.get('seed')} (splits are deterministic)",
        f"- Received: {manifest.get('received')}",
        f"- Rejected by quality gates: {manifest.get('rejected')}",
        f"- Duplicates removed: {manifest.get('duplicates_removed')}",
        f"- Capped for balance: {manifest.get('capped')}",
        f"- Kept: {manifest.get('kept')}",
        "",
        "## Splits",
        "",
    ]
    for name, count in (manifest.get("splits") or {}).items():
        lines.append(f"- **{name}**: {count}")
    leakage = manifest.get("leakage") or {}
    lines += ["", f"- Split leakage check: {'clean' if leakage.get('clean') else 'COLLISIONS FOUND'}", ""]
    if manifest.get("category_counts"):
        lines += ["## Categories", ""]
        for category in CATEGORIES:
            count = (manifest["category_counts"] or {}).get(category)
            if count:
                lines.append(f"- {category}: {count}")
        lines.append("")
    if manifest.get("rejection_reasons"):
        lines += ["## Why examples were rejected", ""]
        for code, count in sorted((manifest["rejection_reasons"] or {}).items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{code}`: {count}")
        lines.append("")
    lines += [
        "_No model weights were changed by curation. Training is a separate, "
        "explicitly invoked step, and promotion requires a human decision._",
        "",
    ]
    return "\n".join(lines)
