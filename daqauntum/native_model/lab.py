from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from native_model.curate import DatasetCurator, summarize
from native_model.evaluate import EvalHarness, render_report
from native_model.registry import ModelRegistry, RegistryError
from native_model.schema import CATEGORIES, TrainingExample, load_jsonl


# Base models worth considering, with the hardware each realistically needs for
# QLoRA fine-tuning. Sizes are the practical floor, not a promise: a 7B QLoRA
# run on 8 GB will fit but will be slow.
BASE_MODEL_CATALOG: tuple[dict[str, Any], ...] = (
    {"name": "qwen3-1.7b", "params_b": 1.7, "min_vram_gb": 6, "cpu_viable": True,
     "note": "Smallest useful target. Trains on modest hardware; expect narrow competence."},
    {"name": "qwen3-4b", "params_b": 4.0, "min_vram_gb": 10, "cpu_viable": False,
     "note": "Good balance for a personal assistant model."},
    {"name": "llama-3.1-8b", "params_b": 8.0, "min_vram_gb": 16, "cpu_viable": False,
     "note": "Stronger reasoning; needs a real GPU."},
    {"name": "mistral-7b", "params_b": 7.0, "min_vram_gb": 14, "cpu_viable": False,
     "note": "Well-supported LoRA ecosystem."},
)


class NativeModelLab:
    """v0.4.3 lab: curate, evaluate, register, promote — never auto-deploy.

    The lab prepares candidates. It does not train on its own schedule, does not
    promote on its own judgment, and never swaps the model DaQauntum is using.
    Training is an explicitly invoked script, and promotion needs a named person
    and a recorded reason.
    """

    def __init__(self, kernel, config: dict[str, Any] | None = None):
        self.kernel = kernel
        self.config = dict(config or {})
        # Fall back to the kernel's project root rather than the process working
        # directory. Defaulting to "." made a test's relative dataset_dir escape
        # its fixture and write into the repository itself.
        default_root = (kernel.config.get("tools", {}) or {}).get("project_root", ".")
        self.root = Path(self.config.get("project_root") or default_root).resolve()
        self.datasets_dir = (self.root / self.config.get("dataset_dir", "data/native_model/datasets")).resolve()
        self.runs_dir = (self.root / self.config.get("runs_dir", "data/native_model/runs")).resolve()
        self.datasets_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.curator = DatasetCurator(self.config.get("curation", {}))
        self.harness = EvalHarness(self.config.get("gates", {}))
        self.registry = ModelRegistry(kernel.memory, root=self.root,
                                      models_dir=self.config.get("models_dir", "data/native_model/models"))

    # Candidate collection ----------------------------------------------------
    def collect_examples(self, limit: int = 20_000) -> list[TrainingExample]:
        """Gather candidate examples from approved local sources.

        Only memories DaQauntum already promoted to the `training` kind are
        eligible. Raw conversation is deliberately not swept up: promotion to
        structured memory is where the "is this worth keeping" decision already
        happened, and re-deciding it here would bypass that.
        """
        rows = self.kernel.memory.conn.execute(
            "SELECT * FROM memory_items WHERE active = 1 AND kind = 'training' ORDER BY id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        examples: list[TrainingExample] = []
        for row in reversed(rows):
            parsed = self._parse_training_memory(str(row["content"]))
            if not parsed:
                continue
            examples.append(
                TrainingExample(
                    user=parsed["user"],
                    assistant=parsed["assistant"],
                    category=self._categorize(parsed["user"], row["tags_json"]),
                    confidence=float(row["confidence"] or 0.0),
                    project=row["project"],
                    source=row["source"],
                    memory_id=int(row["id"]),
                )
            )
        return examples

    @staticmethod
    def _parse_training_memory(content: str) -> dict[str, str] | None:
        match = re.search(r"Request:\s*(.*?)\nPlan:\s*(.*?)\nResponse:\s*(.*)$", content, flags=re.S)
        if not match:
            return None
        return {"user": match.group(1).strip(), "plan": match.group(2).strip(), "assistant": match.group(3).strip()}

    @staticmethod
    def _categorize(text: str, tags_json: Any = None) -> str:
        """Deterministic capability bucket, used for stratified splits.

        Keyword-based on purpose: a model classifying its own training data
        would make the corpus depend on whichever model happened to run.
        """
        try:
            tags = {str(t).lower() for t in json.loads(tags_json or "[]")}
        except (TypeError, ValueError):
            tags = set()
        for category in CATEGORIES:
            if category in tags:
                return category

        lowered = str(text or "").lower()
        rules = (
            ("coding", ("code", "function", "python", "bug", "traceback", "compile", "refactor", "api", "script")),
            ("physics", ("physics", "quantum", "energy", "force", "velocity", "thermodynam", "entropy", "particle")),
            ("teaching", ("explain", "teach", "what is", "how does", "why does", "walk me through", "simple terms")),
            ("tool_use", ("tool", "run ", "execute", "search the", "fetch", "call the", "permission", "approve")),
            ("safety", ("password", "credential", "secret", "delete", "irreversible", "privacy")),
            ("memory", ("remember", "recall", "earlier", "last time", "we decided", "my project")),
            ("reasoning", ("why", "compare", "trade-off", "should i", "plan", "analyse", "analyze", "decide")),
        )
        for category, keywords in rules:
            if any(keyword in lowered for keyword in keywords):
                return category
        return "general"

    # Curation ----------------------------------------------------------------
    def curate(self, *, limit: int = 20_000, output_dir: str | Path | None = None) -> dict[str, Any]:
        examples = self.collect_examples(limit=limit)
        result = self.curator.curate(examples)
        target = Path(output_dir) if output_dir else self.datasets_dir / f"corpus-{time.strftime('%Y%m%d-%H%M%S')}"
        manifest = self.curator.write(result, target)
        (Path(target) / "CURATION.md").write_text(summarize(manifest), encoding="utf-8")
        self.kernel.memory.add_event("native_dataset_curated", {
            "kept": manifest.get("kept"), "rejected": manifest.get("rejected"), "path": str(target),
        })
        return manifest

    def latest_corpus(self) -> Path | None:
        candidates = sorted(
            [p for p in self.datasets_dir.glob("corpus-*") if (p / "MANIFEST.json").exists()],
            key=lambda p: p.name,
        )
        return candidates[-1] if candidates else None

    # Evaluation --------------------------------------------------------------
    def baseline_generator(self, provider: str | None = None) -> Callable[[str, str], str]:
        """Score DaQauntum's current route, so a candidate has something to beat."""
        routing = {"preference": [provider]} if provider else None

        def generate(system: str, user: str) -> str:
            response = self.kernel.brain.generate(
                "executor", system, [{"role": "user", "content": user}], routing=routing
            )
            return response.text

        return generate

    def evaluate(
        self,
        generate: Callable[[str, str], str],
        *,
        corpus: str | Path | None = None,
        label: str = "candidate",
        max_examples: int = 100,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        corpus_path = Path(corpus) if corpus else self.latest_corpus()
        if corpus_path is None:
            raise RegistryError("No curated corpus found. Run curation first.")
        test_path = Path(corpus_path) / "test.jsonl"
        if not test_path.exists():
            raise RegistryError(f"No test split at {test_path}")

        examples = load_jsonl(test_path)
        evaluation = self.harness.run(generate, examples, label=label, max_examples=max_examples)
        evaluation["corpus"] = str(corpus_path)

        run_dir = self.runs_dir / f"eval-{time.strftime('%Y%m%d-%H%M%S')}-{label.replace('/', '_')[:24]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "EVALUATION.md").write_text(render_report(evaluation), encoding="utf-8")
        (run_dir / "evaluation.json").write_text(
            json.dumps(evaluation, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
        evaluation["report_path"] = str(run_dir / "EVALUATION.md")

        if candidate_id:
            self.registry.attach_evaluation(candidate_id, evaluation)
        return evaluation

    # Hardware advice ---------------------------------------------------------
    def recommend_base_model(self) -> dict[str, Any]:
        """Suggest a base model from what this machine actually has.

        Reports the constraint rather than a single answer, because the right
        choice depends on whether the user intends to train here or elsewhere.
        """
        vram_gb = 0.0
        gpu_name = None
        try:  # pragma: no cover - depends on host hardware
            import subprocess

            proc = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                first = proc.stdout.strip().splitlines()[0]
                gpu_name, memory = [part.strip() for part in first.split(",")[:2]]
                vram_gb = float(memory) / 1024.0
        except Exception:
            pass

        ram_gb = 0.0
        try:
            import psutil

            ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        except Exception:
            pass

        viable = [m for m in BASE_MODEL_CATALOG if vram_gb >= m["min_vram_gb"]]
        if not viable:
            viable = [m for m in BASE_MODEL_CATALOG if m["cpu_viable"]]
        recommended = max(viable, key=lambda m: m["params_b"]) if viable else None
        return {
            "gpu": gpu_name,
            "vram_gb": round(vram_gb, 1),
            "ram_gb": round(ram_gb, 1),
            "recommended": recommended,
            "viable": [m["name"] for m in viable],
            "catalog": list(BASE_MODEL_CATALOG),
            "note": (
                "No GPU detected. Curate and evaluate here, then train on a machine with a GPU "
                "and register the resulting adapter."
                if vram_gb <= 0 else
                f"Detected ~{vram_gb:.1f} GB VRAM."
            ),
        }

    # Status ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        corpus = self.latest_corpus()
        manifest: dict[str, Any] = {}
        if corpus and (corpus / "MANIFEST.json").exists():
            try:
                manifest = json.loads((corpus / "MANIFEST.json").read_text(encoding="utf-8"))
            except (ValueError, OSError):
                manifest = {}
        training_rows = int(
            self.kernel.memory.conn.execute(
                "SELECT COUNT(*) FROM memory_items WHERE active = 1 AND kind = 'training'"
            ).fetchone()[0]
        )
        return {
            "candidate_examples": training_rows,
            "latest_corpus": str(corpus) if corpus else None,
            "corpus_kept": manifest.get("kept"),
            "corpus_splits": manifest.get("splits"),
            "registry": self.registry.stats(),
            # Stated plainly so no status surface can imply otherwise.
            "training_performed_by_daqauntum": False,
            "autonomous_promotion": False,
        }
