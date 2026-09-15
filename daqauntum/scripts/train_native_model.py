#!/usr/bin/env python3
"""Reproducible LoRA/QLoRA fine-tune of a DaQauntum native model candidate.

This script is the only place in DaQauntum that can change model weights, and
it never runs on its own. Nothing schedules it, no reaction can trigger it, and
the autonomous learning loop cannot reach it. Invariant 12: learning may prepare
a candidate, never deploy one.

It also does not promote what it trains. Training produces an adapter and a
registry entry in `registered` state; it must then pass evaluation, and a person
must promote it by name with a reason.

Requires the optional training stack:

    pip install -r requirements-training.txt

Usage:

    PYTHONPATH=. python scripts/train_native_model.py --plan            # show what would run
    PYTHONPATH=. python scripts/train_native_model.py --confirm         # actually train
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

from core.kernel import DaQauntumKernel


def dependency_report() -> dict[str, object]:
    """Which parts of the training stack are present on this machine."""
    report: dict[str, object] = {}
    for module, label in (
        ("torch", "PyTorch"),
        ("transformers", "Transformers"),
        ("peft", "PEFT (LoRA)"),
        ("datasets", "Datasets"),
        ("bitsandbytes", "bitsandbytes (4-bit QLoRA)"),
        ("trl", "TRL (SFTTrainer)"),
    ):
        try:
            imported = __import__(module)
            report[label] = getattr(imported, "__version__", "installed")
        except Exception:
            report[label] = None
    try:
        import torch

        report["CUDA available"] = bool(torch.cuda.is_available())
        report["GPU"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    except Exception:
        report["CUDA available"] = False
        report["GPU"] = None
    return report


def git_revision(root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return proc.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def build_plan(args, corpus: Path, root: Path) -> dict[str, object]:
    """Everything needed to reproduce this run, recorded before it starts."""
    manifest_path = corpus / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    return {
        "base_model": args.base_model,
        "method": "qlora" if args.four_bit else "lora",
        "corpus": str(corpus),
        "corpus_checksums": manifest.get("checksums"),
        "corpus_kept": manifest.get("kept"),
        "corpus_splits": manifest.get("splits"),
        "hyperparameters": {
            "epochs": args.epochs,
            "learning_rate": args.learning_rate,
            "batch_size": args.batch_size,
            "gradient_accumulation_steps": args.grad_accum,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "max_seq_length": args.max_seq_length,
            "seed": args.seed,
            "four_bit": args.four_bit,
        },
        "environment": {
            "python": platform.python_version(),
            "platform": f"{platform.system()} {platform.release()}",
            "daqauntum_revision": git_revision(root),
        },
        "dependencies": dependency_report(),
    }


def train(plan: dict, corpus: Path, output_dir: Path) -> dict:  # pragma: no cover - needs a GPU stack
    """Run the fine-tune. Only reached after --confirm and a dependency check."""
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    hyper = plan["hyperparameters"]
    set_seed(int(hyper["seed"]))

    tokenizer = AutoTokenizer.from_pretrained(plan["base_model"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def load_split(name: str) -> Dataset:
        rows = []
        path = corpus / f"{name}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            text = tokenizer.apply_chat_template(record["messages"], tokenize=False)
            rows.append({"text": text})
        return Dataset.from_list(rows)

    train_ds, eval_ds = load_split("train"), load_split("validation")

    model_kwargs: dict = {"torch_dtype": torch.bfloat16 if torch.cuda.is_available() else torch.float32}
    if hyper["four_bit"]:
        from transformers import BitsAndBytesConfig

        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
    model = AutoModelForCausalLM.from_pretrained(plan["base_model"], **model_kwargs)
    if hyper["four_bit"]:
        model = prepare_model_for_kbit_training(model)

    model = get_peft_model(model, LoraConfig(
        r=int(hyper["lora_r"]),
        lora_alpha=int(hyper["lora_alpha"]),
        lora_dropout=float(hyper["lora_dropout"]),
        bias="none",
        task_type="CAUSAL_LM",
    ))

    def tokenize(batch):
        return tokenizer(batch["text"], truncation=True, max_length=int(hyper["max_seq_length"]))

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output_dir),
            num_train_epochs=float(hyper["epochs"]),
            learning_rate=float(hyper["learning_rate"]),
            per_device_train_batch_size=int(hyper["batch_size"]),
            gradient_accumulation_steps=int(hyper["gradient_accumulation_steps"]),
            eval_strategy="epoch",
            save_strategy="epoch",
            logging_steps=10,
            seed=int(hyper["seed"]),
            report_to=[],
        ),
        train_dataset=train_ds.map(tokenize, batched=True, remove_columns=["text"]),
        eval_dataset=eval_ds.map(tokenize, batched=True, remove_columns=["text"]),
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )
    result = trainer.train()
    model.save_pretrained(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    return {"train_loss": float(result.training_loss), "steps": int(result.global_step)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a DaQauntum native model candidate")
    parser.add_argument("--base-model", default="Qwen/Qwen3-1.7B")
    parser.add_argument("--corpus", default="", help="Corpus directory (default: the most recent)")
    parser.add_argument("--name", default="", help="Candidate name for the registry")
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--max-seq-length", type=int, default=2048)
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--four-bit", action="store_true", help="QLoRA 4-bit quantized base")
    parser.add_argument("--plan", action="store_true", help="Show the plan and exit without training")
    parser.add_argument("--confirm", action="store_true", help="Required to actually train")
    args = parser.parse_args()

    kernel = DaQauntumKernel()
    lab = kernel.model_lab
    corpus = Path(args.corpus) if args.corpus else lab.latest_corpus()
    if corpus is None:
        raise SystemExit("No curated corpus found. Run scripts/curate_dataset.py first.")
    if not (corpus / "train.jsonl").exists():
        raise SystemExit(f"No train split in {corpus}")

    root = Path(lab.root)
    plan = build_plan(args, Path(corpus), root)

    print("DaQauntum native model training plan")
    print("=" * 56)
    print(json.dumps(plan, indent=2, default=str))

    missing = [name for name, version in plan["dependencies"].items()
               if version is None and name not in {"GPU", "bitsandbytes (4-bit QLoRA)"}]
    if missing:
        print("\nMissing training dependencies: " + ", ".join(missing))
        print("Install them with: pip install -r requirements-training.txt")
    if not plan["dependencies"].get("CUDA available"):
        print("\nNo CUDA GPU detected. A LoRA run on CPU is possible for the smallest base "
              "model but will be very slow.")

    if args.plan or not args.confirm:
        print("\nNothing was trained. This script never runs on its own; re-run with --confirm to train.")
        return
    if missing:
        raise SystemExit("\nRefusing to train with missing dependencies.")

    run_dir = Path(lab.runs_dir) / f"train-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "PLAN.json").write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

    print(f"\nTraining. Artifacts and the plan land in {run_dir}")
    started = time.time()
    try:
        outcome = train(plan, Path(corpus), run_dir)
    except Exception as exc:
        (run_dir / "FAILED.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        raise SystemExit(f"Training failed: {type(exc).__name__}: {exc}")

    plan["result"] = {**outcome, "elapsed_seconds": round(time.time() - started, 1)}
    (run_dir / "PLAN.json").write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

    candidate = lab.registry.register(
        name=args.name or f"daq-{time.strftime('%Y%m%d-%H%M')}",
        base_model=args.base_model,
        dataset_manifest=str(Path(corpus) / "MANIFEST.json"),
        method=plan["method"],
        hyperparameters=plan["hyperparameters"],
        artifact_path=str(run_dir),
        notes=f"Trained by scripts/train_native_model.py at {plan['environment']['daqauntum_revision']}",
    )
    print(f"\nRegistered candidate {candidate['candidate_id']} in state '{candidate['status']}'.")
    print("It is NOT in use. Next:")
    print(f"  PYTHONPATH=. python scripts/evaluate_model.py --candidate {candidate['candidate_id']}")
    print(f"  PYTHONPATH=. python scripts/model_registry.py promote --candidate {candidate['candidate_id']} "
          "--by \"<your name>\" --reason \"<why>\"")


if __name__ == "__main__":
    main()
