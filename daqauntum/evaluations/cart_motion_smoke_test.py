"""Regression guard for the cart motion reference experiment.

This is the worked example the documentation follows and the fixture that
exercises every layer at once: units, uncertainty, hypotheses, analysis,
evidence, plots, claims and persistence.

Its particular hazard is that a demo is the easiest place to cheat. The default
dataset is illustrative - nobody measured it - so the tests here check hardest
that nothing in the finished record calls it a measurement.
"""
from __future__ import annotations

import math
import os
import tempfile

from memory.store import MemoryStore
from science.core import ScienceCore
from science.models import (
    ClaimStatus,
    Depth,
    EvidenceKind,
    ExperimentStatus,
    HypothesisStatus,
    ProvenanceKind,
)
from science.reference import EXAMPLE_POSITIONS, EXAMPLE_TIMES, run_cart_motion
from science.sensors import MockCartSensor
from science.store import ScienceStore


def _core(tmp: str) -> ScienceCore:
    return ScienceCore(MemoryStore(os.path.join(tmp, "cart.db")),
                       artifacts_dir=os.path.join(tmp, "artifacts"))


def test_reference_run_produces_the_documented_numbers() -> None:
    """The values the documentation quotes, computed not copied."""
    with tempfile.TemporaryDirectory() as tmp:
        result = run_cart_motion(_core(tmp))
        analysis = result.analysis

        # Δx = 4.6 − 0.0 m; v_avg = 4.6 m / 4.0 s
        assert math.isclose(analysis.displacement.value, 4.6, rel_tol=1e-9)
        assert math.isclose(analysis.average_velocity.value, 1.15, rel_tol=1e-9)
        # a = (1.3 − 1.0) m/s / (3.5 − 0.5) s
        assert math.isclose(analysis.average_acceleration.value, 0.1, rel_tol=1e-9)
        assert [round(r.value, 10) for r in analysis.interval_velocities] == [1.0, 1.1, 1.2, 1.3]
        assert 0.997 < analysis.fit["r_squared"] < 0.998

        assert result.experiment.status is ExperimentStatus.COMPLETED
        assert result.experiment.depth is Depth.INTRODUCTORY
        assert len(result.measurements) == 2 * len(EXAMPLE_TIMES)
        assert len(result.plots) == 3
        assert all(p.exists() for p in result.plots)


def test_example_data_is_never_called_a_measurement() -> None:
    """The demo is the easiest place to cheat, so check every surface."""
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        result = run_cart_motion(core)

        for measurement in result.measurements:
            assert measurement.provenance.kind is ProvenanceKind.IMPORTED
            assert measurement.evidence_kind is EvidenceKind.LITERATURE
            assert not measurement.is_reading
            assert measurement.metadata["example_data"] is True
            assert "Not a reading anyone took" in measurement.metadata["note"]

        # Nothing in the record counts as a reading of the world.
        stats = core.stats()
        assert stats["raw_measurements"] == 0, "example data was counted as measured"

        # The analysis says so.
        assert any("not measured" in w for w in result.analysis.warnings), \
            result.analysis.warnings

        # Every plot says so on its face, where a screenshot carries it.
        for path in result.plots:
            text = path.read_text(encoding="utf-8")
            assert "not measured" in text.lower(), f"{path.name} has no data note"

        # No evidence claims to be empirical.
        for evidence in result.evidence:
            assert not evidence.is_empirical, f"{evidence.kind} presented as empirical"


def test_the_claim_rests_on_stated_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        result = run_cart_motion(core)
        claim = core.store.get_claim(result.claim_id)

        assert claim.evidence_ids, "a claim with no evidence"
        assert claim.hypothesis_id == result.hypothesis.id
        assert claim.status is ClaimStatus.SUPPORTED
        assert "1.15" in claim.statement and "m/s" in claim.statement
        # Every cited id resolves.
        for evidence_id in claim.evidence_ids:
            assert core.store.get_evidence(evidence_id) is not None
        assert claim.metadata["evidence_kinds"] == ["calculation"]


def test_the_hypothesis_is_assessed_not_proven() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        result = run_cart_motion(core)
        hypothesis = result.hypothesis

        assert hypothesis.status is HypothesisStatus.SUPPORTED
        assert hypothesis.status.value != "proven"
        assert hypothesis.supporting_evidence, "nothing was linked"
        assert not hypothesis.contradicting_evidence

        assessment = hypothesis.metadata["assessments"][-1]
        assert assessment["assessor"] == "user"
        assert "acceleration" in assessment["reasoning"]


def test_simulated_source_is_labelled_differently() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        result = run_cart_motion(core, source="simulated",
                                 sensor=MockCartSensor(noise=0.0))
        assert result.source == "simulated"
        for measurement in result.measurements:
            assert measurement.evidence_kind is EvidenceKind.SIMULATION
        assert any("were simulated" in w for w in result.analysis.warnings)
        for path in result.plots:
            assert "SIMULATED" in path.read_text(encoding="utf-8") or \
                   "NOT MEASURED" in path.read_text(encoding="utf-8")

        # v0 = 1.0, a = 0.1 over 4 s: x(4) = 4.8, so v_avg = 1.2 m/s.
        assert math.isclose(result.analysis.average_velocity.value, 1.2, rel_tol=1e-9)


def test_there_is_no_way_to_relabel_the_data_as_measured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        try:
            run_cart_motion(core, source="measured")
        except ValueError as exc:
            assert "Unknown source" in str(exc), exc
            assert "run_sensor" in str(exc), "the error should point at the real path"
        else:
            raise AssertionError("A generated run was allowed to call itself measured")


def test_the_whole_record_survives_a_reload() -> None:
    """Persistence: the labels have to still be there in a fresh process."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cart.db")
        core = ScienceCore(MemoryStore(path), artifacts_dir=os.path.join(tmp, "artifacts"))
        result = run_cart_motion(core)
        experiment_id = result.experiment.id

        reopened = ScienceStore(MemoryStore(path))
        record = reopened.experiment_record(experiment_id)
        assert record["experiment"]["status"] == "completed"
        assert len(record["claims"]) == 1
        assert len(record["hypotheses"]) == 1
        assert record["hypotheses"][0]["status"] == "supported"

        for measurement in reopened.list_measurements(experiment_id, include_derived=False):
            assert measurement.evidence_kind is EvidenceKind.LITERATURE, \
                "the example-data label did not survive reload"
        assert reopened.stats()["raw_measurements"] == 0

        # The plots are still on disk and still say what they are.
        for plot_path in result.plots:
            assert plot_path.exists()
            assert "not measured" in plot_path.read_text(encoding="utf-8").lower()


def test_custom_data_runs_through_the_same_path() -> None:
    """A user's own numbers work, and are labelled the same way."""
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        result = run_cart_motion(
            core,
            times=[0.0, 2.0, 4.0, 6.0],
            positions=[0.0, 2.0, 4.0, 6.0],   # exactly uniform motion
            plot=False,
        )
        assert math.isclose(result.analysis.average_velocity.value, 1.0, rel_tol=1e-9)
        assert math.isclose(result.analysis.average_acceleration.value, 0.0, abs_tol=1e-12)
        assert math.isclose(result.analysis.fit["r_squared"], 1.0, rel_tol=1e-12)
        # No acceleration, so the hypothesis is not supported - and the run
        # says inconclusive rather than contradicted, because zero measured
        # acceleration on four points is not a refutation.
        assert result.hypothesis.status is HypothesisStatus.INCONCLUSIVE
        assert core.store.get_claim(result.claim_id).status is ClaimStatus.UNRESOLVED


def test_summary_is_complete_enough_to_read_alone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_cart_motion(_core(tmp)).summary()
        for key in ("experiment_id", "source", "measurements", "displacement_m",
                    "average_velocity_ms", "average_acceleration_ms2", "r_squared",
                    "evidence_kinds", "plots", "warnings", "hypothesis_status"):
            assert key in summary, f"summary is missing {key}"
        assert summary["source"] == "example"
        assert summary["warnings"], "a summary of unmeasured data with no warning"
        assert "measurement" not in summary["evidence_kinds"]


def main() -> None:
    test_reference_run_produces_the_documented_numbers()
    test_example_data_is_never_called_a_measurement()
    test_the_claim_rests_on_stated_evidence()
    test_the_hypothesis_is_assessed_not_proven()
    test_simulated_source_is_labelled_differently()
    test_there_is_no_way_to_relabel_the_data_as_measured()
    test_the_whole_record_survives_a_reload()
    test_custom_data_runs_through_the_same_path()
    test_summary_is_complete_enough_to_read_alone()
    print("DaQauntum v0.5 cart motion reference smoke test: PASS")


if __name__ == "__main__":
    main()
