#!/usr/bin/env python3
"""Inspect and decide on DaQauntum native-model candidates.

Promotion and rollback are human decisions and are recorded as such: every
promotion carries the name of the person deciding and their reason.

    PYTHONPATH=. python scripts/model_registry.py list
    PYTHONPATH=. python scripts/model_registry.py register --name daq-v1 --base qwen3-4b --manifest data/.../MANIFEST.json
    PYTHONPATH=. python scripts/model_registry.py promote --candidate cand_abc --by "Louis" --reason "best safety scores"
    PYTHONPATH=. python scripts/model_registry.py rollback --by "Louis" --reason "tool-use regression"
"""
from __future__ import annotations

import argparse
import json

from core.kernel import DaQauntumKernel
from native_model.registry import RegistryError


def main() -> None:
    parser = argparse.ArgumentParser(description="DaQauntum native model registry")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List candidates")
    sub.add_parser("history", help="Show the decision log")
    sub.add_parser("hardware", help="Recommend a base model for this machine")

    register = sub.add_parser("register", help="Register a trained candidate")
    register.add_argument("--name", required=True)
    register.add_argument("--base", required=True, help="Base model the adapter was trained on")
    register.add_argument("--manifest", default="", help="Path to the corpus MANIFEST.json")
    register.add_argument("--artifact", default="", help="Path to the adapter/weights")
    register.add_argument("--method", default="lora")
    register.add_argument("--notes", default="")

    promote = sub.add_parser("promote", help="Promote a candidate (human decision)")
    promote.add_argument("--candidate", required=True)
    promote.add_argument("--by", required=True, help="Who is deciding")
    promote.add_argument("--reason", required=True, help="Why")
    promote.add_argument("--force", action="store_true", help="Override a failing evaluation, recorded as an override")

    reject = sub.add_parser("reject", help="Reject a candidate")
    reject.add_argument("--candidate", required=True)
    reject.add_argument("--by", required=True)
    reject.add_argument("--reason", required=True)

    rollback = sub.add_parser("rollback", help="Take the live model out of service")
    rollback.add_argument("--by", required=True)
    rollback.add_argument("--reason", required=True)

    args = parser.parse_args()
    kernel = DaQauntumKernel()
    registry = kernel.model_lab.registry

    try:
        if args.command == "list":
            candidates = registry.list()
            if not candidates:
                print("No candidates registered yet.")
            for candidate in candidates:
                evaluation = candidate.get("evaluation") or {}
                marker = "*" if candidate["status"] == "promoted" else " "
                print(f"{marker} {candidate['candidate_id']}  {candidate['name']:<22} {candidate['status']:<12} "
                      f"base={candidate['base_model']:<16} score={evaluation.get('overall_score', '—')}")
            print(f"\nLive model: {registry.stats()['promoted'] or 'none'}")
            print("Autonomous promotion: disabled by design.")
        elif args.command == "history":
            for event in registry.history(limit=40):
                print(f"{event['event']:<14} {event['candidate_id']}  by {event['actor']}: {event['detail']}")
        elif args.command == "hardware":
            print(json.dumps(kernel.model_lab.recommend_base_model(), indent=2, default=str))
        elif args.command == "register":
            candidate = registry.register(
                name=args.name, base_model=args.base,
                dataset_manifest=args.manifest or None, artifact_path=args.artifact or None,
                method=args.method, notes=args.notes,
            )
            print(f"Registered {candidate['candidate_id']} ({candidate['status']}).")
            print("Evaluate it before promotion: PYTHONPATH=. python scripts/evaluate_model.py --candidate "
                  f"{candidate['candidate_id']}")
        elif args.command == "promote":
            candidate = registry.promote(args.candidate, decided_by=args.by, reason=args.reason, force=args.force)
            print(f"Promoted {candidate['candidate_id']} by {candidate['promoted_by']}: {candidate['decision_reason']}")
        elif args.command == "reject":
            candidate = registry.reject(args.candidate, decided_by=args.by, reason=args.reason)
            print(f"Rejected {candidate['candidate_id']}.")
        elif args.command == "rollback":
            candidate = registry.rollback(decided_by=args.by, reason=args.reason)
            print(f"Rolled back {candidate['candidate_id']}." if candidate else "No model is currently promoted.")
    except RegistryError as exc:
        raise SystemExit(f"Refused: {exc}")


if __name__ == "__main__":
    main()
