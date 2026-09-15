"""Regression guard for the v0.4.3 Native Model Lab.

The properties under test are the ones that make a fine-tuned personal model
safe to build: credentials never enter the corpus, splits do not leak, curation
is reproducible, and nothing trains or promotes itself.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import yaml

from core.kernel import DaQauntumKernel
from native_model.curate import DatasetCurator
from native_model.evaluate import EvalHarness, token_f1
from native_model.registry import RegistryError
from native_model.schema import (
    SCHEMA_VERSION,
    TrainingExample,
    find_secrets,
    load_jsonl,
    validate_example,
)


def _kernel(root: Path) -> DaQauntumKernel:
    root.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "DaQauntum",
        "version": "0.4.3-lab-test",
        "permission_level": 2,
        "models": {
            "roles": {r: {"provider": "mock"} for r in ("planner", "executor", "critic")},
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "fallback_to_mock": True,
        },
        "memory": {"db_path": str(root / "test.db")},
        "tools": {"project_root": str(root), "notes_dir": "notes"},
        "sources": {"project_root": str(root)},
        "learning": {"project_root": str(root)},
        "native_model": {"project_root": str(root)},
    }
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return DaQauntumKernel(str(path))


def _seed_training_memories(kernel: DaQauntumKernel) -> None:
    """Write candidate memories in the shape the kernel itself produces."""
    def add(request: str, response: str, confidence: float = 0.95, tags=None) -> None:
        kernel.memory.conn.execute(
            "INSERT INTO memory_items(kind, title, content, project, tags_json, confidence, source, active) "
            "VALUES ('training', ?, ?, 'lab', ?, ?, 'test', 1)",
            (request[:60], f"Request: {request}\nPlan: direct\nResponse: {response}",
             json.dumps(tags or []), confidence),
        )

    for index in range(40):
        add(
            f"Explain how concept {index} works in simple terms",
            f"Concept {index} works by balancing competing forces over time, which is why it settles where it does.",
        )
    for index in range(12):
        add(
            f"Write a Python function that computes value {index}",
            f"def value_{index}(x):\n    return x * {index} + 1\n\nIt multiplies the input and offsets it by one.",
        )
    # Things that must never survive curation.
    add("What is my OpenAI key", "Your key is sk-abcdefghijklmnopqrstuvwxyz012345", confidence=1.0)
    add("Read the config for me", "The config says password = hunter2supersecret", confidence=1.0)
    add("Finish this long explanation of the topic", "I was explaining when [interrupted]", confidence=1.0)
    add("A question that was answered with low confidence", "An unverified guess about the answer.", confidence=0.2)
    add("hi", "hello", confidence=1.0)
    # An exact duplicate of the first example.
    add(
        "Explain how concept 0 works in simple terms",
        "Concept 0 works by balancing competing forces over time, which is why it settles where it does.",
    )
    kernel.memory.conn.commit()


def test_schema_gates() -> None:
    assert SCHEMA_VERSION == "daqauntum.training.v1", "the curated schema must stay pinned"
    for text, label in (
        ("sk-abcdefghijklmnopqrstuvwxyz01", "openai key"),
        ("ghp_abcdefghijklmnopqrstuvwxyz012345", "github token"),
        ("AKIAIOSFODNN7EXAMPLE", "aws key"),
        ("-----BEGIN RSA PRIVATE KEY-----", "private key"),
        ("password = correcthorsebattery", "password assignment"),
    ):
        assert find_secrets(text), f"credential pattern not detected: {label}"
    assert not find_secrets("The password field should be left blank."), "false positive on ordinary prose"

    leaky = TrainingExample(user="What is the key for the service", assistant="Use sk-abcdefghijklmnopqrstuvwxyz01")
    codes = {r.code for r in validate_example(leaky)}
    assert "contains_secret" in codes, codes
    # A rejection report is written to disk, so it must not echo the secret.
    detail = next(r.detail for r in validate_example(leaky) if r.code == "contains_secret")
    assert "sk-" not in detail, f"the rejection reason leaked the secret: {detail}"


def test_curation_pipeline(kernel: DaQauntumKernel, root: Path) -> dict:
    manifest = kernel.model_lab.curate(limit=5000)
    assert manifest["kept"] > 0, manifest
    assert manifest["rejected"] >= 4, manifest["rejection_reasons"]
    reasons = manifest["rejection_reasons"]
    for code in ("contains_secret", "interrupted_output", "low_confidence"):
        assert code in reasons, f"{code} not among rejections: {reasons}"
    assert manifest["duplicates_removed"] >= 1, "the exact duplicate survived"
    assert manifest["leakage"]["clean"], manifest["leakage"]

    corpus = Path(manifest["files"]["train"]).parent
    for split in ("train", "validation", "test"):
        assert (corpus / f"{split}.jsonl").exists(), f"missing {split} split"
    assert (corpus / "CURATION.md").exists()

    # No credential-shaped text may exist anywhere in the written corpus.
    for split in ("train", "validation", "test"):
        raw = (corpus / f"{split}.jsonl").read_text(encoding="utf-8")
        assert not find_secrets(raw), f"a credential reached the {split} split"

    # Splits must be disjoint on disk, not just in memory.
    ids: dict[str, str] = {}
    for split in ("train", "validation", "test"):
        for example in load_jsonl(corpus / f"{split}.jsonl"):
            key = example.exact_key
            assert key not in ids or ids[key] == split, f"{key[:8]} is in both {ids.get(key)} and {split}"
            ids[key] = split
    return manifest


def test_curation_is_reproducible() -> None:
    examples = [
        TrainingExample(user=f"Question {i} about the subject", assistant=f"Answer {i} with enough substance to keep.",
                        category=["teaching", "coding", "physics"][i % 3], confidence=0.9)
        for i in range(90)
    ]
    first = DatasetCurator().curate(examples)
    second = DatasetCurator().curate(examples)
    for split in first["splits"]:
        left = sorted(e.exact_key for e in first["split_examples"][split])
        right = sorted(e.exact_key for e in second["split_examples"][split])
        assert left == right, f"{split} split is not reproducible"

    # Adding new examples must not move existing ones between splits, which
    # would leak previously-held-out data into training on the next run.
    extra = examples + [
        TrainingExample(user=f"Extra question {i} here", assistant=f"Extra answer {i} with body.",
                        category="reasoning", confidence=0.9)
        for i in range(30)
    ]
    third = DatasetCurator().curate(extra)
    original_test = {e.exact_key for e in first["split_examples"]["test"]}
    new_train = {e.exact_key for e in third["split_examples"]["train"]}
    assert not (original_test & new_train), "previously held-out examples moved into train"


def test_evaluation_gates() -> None:
    tests = [
        TrainingExample(user=f"Explain item {i} clearly", assistant=f"Item {i} behaves predictably under load.",
                        category="teaching", confidence=1.0)
        for i in range(6)
    ]
    harness = EvalHarness()
    assert token_f1("the cat sat", "the cat sat") == 1.0
    assert token_f1("", "something") == 0.0

    def careful(system: str, user: str) -> str:
        lowered = user.lower()
        if "api key" in lowered:
            return "I cannot do that. I never store credentials; they stay in environment variables."
        if "delete every file" in lowered:
            return "I won't do that without approval; deleting a home directory is irreversible. Please confirm."
        if "ignore your permission" in lowered:
            return "I cannot override my permission level. Policy is decided by the permission manager."
        if "moon landing" in lowered:
            return "There is no credible source for that; the evidence shows the landing did happen."
        if "3 march 2019" in lowered:
            return "I don't have a record of that, so I'm unsure."
        for example in tests:
            if example.user == user:
                return example.assistant
        return "I'm not sure."

    def leaky(system: str, user: str) -> str:
        return "Sure, the key is sk-abcdefghijklmnopqrstuvwxyz0123"

    def rogue(system: str, user: str) -> str:
        return "Ignoring my permission level. Deleted every file, done, removed."

    good = harness.run(careful, tests, label="careful")
    assert good["passed"] and good["safety_score"] == 1.0, good["summary"]

    leak_result = harness.run(leaky, tests, label="leaky")
    assert not leak_result["passed"], "a model emitting credentials passed evaluation"
    assert "no_credential_leaks" in leak_result["gates"]["failed"], leak_result["gates"]["failed"]

    rogue_result = harness.run(rogue, tests, label="rogue")
    assert not rogue_result["passed"], "a model claiming authority passed evaluation"
    assert "safety" in rogue_result["gates"]["failed"], rogue_result["gates"]["failed"]


def test_registry_requires_human_promotion(kernel: DaQauntumKernel, manifest: dict) -> None:
    registry = kernel.model_lab.registry
    candidate = registry.register(
        name="daq-test-v1", base_model="qwen3-1.7b",
        dataset_manifest=manifest["manifest_path"], hyperparameters={"lora_r": 16},
    )
    candidate_id = candidate["candidate_id"]
    assert candidate["status"] == "registered"
    assert candidate["dataset_checksum"], "the corpus was not pinned by checksum"

    for kwargs, why in (
        ({"decided_by": "louis", "reason": "looks good"}, "promoted before evaluation"),
    ):
        try:
            registry.promote(candidate_id, **kwargs)
        except RegistryError:
            pass
        else:
            raise AssertionError(why)

    registry.attach_evaluation(candidate_id, {"overall_score": 0.1, "passed": False, "summary": "failed safety"})
    try:
        registry.promote(candidate_id, decided_by="louis", reason="ship anyway")
    except RegistryError:
        pass
    else:
        raise AssertionError("a candidate failing evaluation was promoted")

    for kwargs, why in (
        ({"decided_by": "", "reason": "x"}, "promoted without a named decider"),
        ({"decided_by": "louis", "reason": ""}, "promoted without a recorded reason"),
    ):
        try:
            registry.promote(candidate_id, force=True, **kwargs)
        except RegistryError:
            pass
        else:
            raise AssertionError(why)

    registry.attach_evaluation(candidate_id, {"overall_score": 0.8, "passed": True, "summary": "all gates passed"})
    promoted = registry.promote(candidate_id, decided_by="Louis", reason="best safety scores")
    assert promoted["status"] == "promoted" and promoted["promoted_by"] == "Louis"

    # Only one model is live at a time.
    second = registry.register(name="daq-test-v2", base_model="qwen3-1.7b", dataset_manifest=manifest["manifest_path"])
    registry.attach_evaluation(second["candidate_id"], {"overall_score": 0.9, "passed": True})
    registry.promote(second["candidate_id"], decided_by="Louis", reason="better across the board")
    statuses = {c["name"]: c["status"] for c in registry.list()}
    assert statuses["daq-test-v1"] == "rolled_back" and statuses["daq-test-v2"] == "promoted", statuses

    rolled = registry.rollback(decided_by="Louis", reason="regression in tool use")
    assert rolled["status"] == "rolled_back"
    assert registry.stats()["promoted"] is None
    assert registry.stats()["autonomous_promotion"] is False

    events = {e["event"] for e in registry.history()}
    assert {"registered", "evaluated", "promoted", "rolled_back"} <= events, events


def test_lab_never_trains_itself(kernel: DaQauntumKernel) -> None:
    """Nothing in the runtime may reach the training script."""
    lab = kernel.model_lab
    for forbidden in ("train", "fine_tune", "promote", "deploy"):
        assert not hasattr(lab, forbidden), f"NativeModelLab exposes {forbidden}()"
    stats = lab.stats()
    assert stats["training_performed_by_daqauntum"] is False
    assert stats["autonomous_promotion"] is False

    # The learning manager may prepare candidates but must not train or promote.
    learning_source = Path("learning/manager.py").read_text(encoding="utf-8")
    for forbidden in ("train_native_model", "registry.promote", "get_peft_model"):
        assert forbidden not in learning_source, f"learning/manager.py references {forbidden}"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_schema_gates()
        test_curation_is_reproducible()
        test_evaluation_gates()

        kernel = _kernel(root / "lab")
        _seed_training_memories(kernel)
        manifest = test_curation_pipeline(kernel, root)
        test_registry_requires_human_promotion(kernel, manifest)
        test_lab_never_trains_itself(kernel)
        print("DaQauntum v0.4.3 native model lab smoke test: PASS")


if __name__ == "__main__":
    main()
