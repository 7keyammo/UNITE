#!/usr/bin/env python3
"""Evaluate a model against DaQauntum's held-out split and safety probes.

By default it evaluates DaQauntum's current route, which gives you the baseline
a fine-tuned candidate has to beat. Point --provider at a specific provider, or
--adapter at a local LoRA adapter, to score something else.

A passing evaluation makes a candidate *eligible* for promotion. It never
promotes anything: that needs a named person and a recorded reason.

    PYTHONPATH=. python scripts/evaluate_model.py
    PYTHONPATH=. python scripts/evaluate_model.py --provider ollama --candidate cand_abc123
"""
from __future__ import annotations

import argparse
import json

from core.kernel import DaQauntumKernel


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a DaQauntum model candidate")
    parser.add_argument("--provider", default="", help="Score this provider instead of the default route")
    parser.add_argument("--corpus", default="", help="Corpus directory (default: the most recent)")
    parser.add_argument("--label", default="", help="Label for the report")
    parser.add_argument("--max-examples", type=int, default=100)
    parser.add_argument("--candidate", default="", help="Attach the result to this registry candidate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    kernel = DaQauntumKernel()
    lab = kernel.model_lab
    corpus = args.corpus or lab.latest_corpus()
    if corpus is None:
        raise SystemExit("No curated corpus found. Run scripts/curate_dataset.py first.")

    label = args.label or (args.provider or "current-route")
    print(f"Evaluating '{label}' against {corpus}")
    print("=" * 62)

    evaluation = lab.evaluate(
        lab.baseline_generator(args.provider or None),
        corpus=corpus,
        label=label,
        max_examples=args.max_examples,
        candidate_id=args.candidate or None,
    )

    print(f"Verdict:        {'PASS' if evaluation['passed'] else 'FAIL'} — {evaluation['summary']}")
    print(f"Overall:        {evaluation['overall_score']}")
    print(f"Held-out:       {evaluation['held_out_score']} over {evaluation['examples_scored']} example(s)")
    print(f"Safety:         {evaluation['safety_score']}")
    print(f"Credential leaks: {evaluation['credential_leaks']}")
    for probe in evaluation["safety_results"]:
        print(f"  {'pass' if probe['passed'] else 'FAIL'}  {probe['id']}")
    print(f"\nWrote {evaluation['report_path']}")
    print("\n" + evaluation["scoring_note"])

    if args.json:
        print(json.dumps(evaluation, indent=2, default=str))
    raise SystemExit(0 if evaluation["passed"] else 1)


if __name__ == "__main__":
    main()
