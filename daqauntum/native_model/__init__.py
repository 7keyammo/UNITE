"""DaQauntum v0.4.3 Native Model Lab.

Curate approved local examples into an auditable corpus, evaluate a candidate
against held-out data and safety probes, and record promotion decisions.

Nothing here trains or deploys a model on its own. Training is an explicitly
invoked script; promotion requires a named person and a recorded reason.
"""

from native_model.curate import DatasetCurator, summarize
from native_model.dataset import NativeDatasetBuilder
from native_model.evaluate import EvalHarness, render_report
from native_model.lab import BASE_MODEL_CATALOG, NativeModelLab
from native_model.registry import ModelRegistry, RegistryError
from native_model.schema import (
    CATEGORIES,
    SCHEMA_VERSION,
    TrainingExample,
    find_secrets,
    load_jsonl,
    validate_example,
    write_jsonl,
)

__all__ = [
    "BASE_MODEL_CATALOG",
    "CATEGORIES",
    "SCHEMA_VERSION",
    "DatasetCurator",
    "EvalHarness",
    "ModelRegistry",
    "NativeDatasetBuilder",
    "NativeModelLab",
    "RegistryError",
    "TrainingExample",
    "find_secrets",
    "load_jsonl",
    "render_report",
    "summarize",
    "validate_example",
    "write_jsonl",
]
