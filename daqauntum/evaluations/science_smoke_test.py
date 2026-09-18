"""Regression guard for the v0.5 scientific domain, events and persistence.

The property this suite exists to protect: different kinds of knowledge stay
distinguishable. A measured value, a derived value, a simulated value and an AI
interpretation must never become interchangeable as they pass through models,
the event bus, storage and reload.
"""
from __future__ import annotations

import os
import tempfile

from events import EventSystem
from memory.store import MemoryStore
from science.events import EventKind, measurement_recorded, new_correlation_id
from science.models import (
    Claim,
    ClaimStatus,
    Evidence,
    EvidenceKind,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
    Measurement,
    Provenance,
    ProvenanceKind,
)
from science.store import ScienceStore
from science.units import UnitError


def _store(name: str = "science.db") -> ScienceStore:
    return ScienceStore(MemoryStore(os.path.join(tempfile.mkdtemp(), name)))


def test_knowledge_kinds_stay_distinct() -> None:
    """The distinction the whole project turns on."""
    measured = Evidence(kind=EvidenceKind.MEASUREMENT, statement="Position = 1.23 ± 0.01 m",
                        value=1.23, unit="m", uncertainty=0.01)
    calculated = Evidence(kind=EvidenceKind.CALCULATION, statement="Velocity = 1.15 m/s",
                          value=1.15, unit="m/s")
    simulated = Evidence(kind=EvidenceKind.SIMULATION, statement="Predicted a = 2.56 m/s²")
    literature = Evidence(kind=EvidenceKind.LITERATURE, statement="Model equation from Halliday")
    inferred = Evidence(kind=EvidenceKind.AI_INTERPRETATION, statement="Friction may explain the gap.")
    human = Evidence(kind=EvidenceKind.HUMAN_INTERPRETATION, statement="The track looked uneven.")

    # Only what came from the world is empirical.
    assert measured.is_empirical
    for other in (calculated, simulated, literature, inferred, human):
        assert not other.is_empirical, f"{other.kind.value} was treated as empirical"

    assert inferred.kind.is_interpretation and human.kind.is_interpretation
    assert not calculated.kind.is_interpretation

    # Evidence cannot exist without declaring its kind.
    try:
        Evidence(kind=None, statement="untyped")  # type: ignore[arg-type]
    except (ValueError, TypeError):
        pass
    else:
        raise AssertionError("evidence was created without a kind")

    try:
        Evidence(kind=EvidenceKind.MEASUREMENT, statement="   ")
    except ValueError:
        pass
    else:
        raise AssertionError("evidence was created with an empty statement")


def test_measurement_validation_and_derivation() -> None:
    raw = Measurement(quantity="position", value=1.23, unit="m", uncertainty=0.01, source="ruler")
    assert raw.evidence_kind is EvidenceKind.MEASUREMENT
    assert raw.as_quantity.format() == "1.23 ± 0.01 m", raw.as_quantity.format()

    # A derived value is a calculation and says so, even though it is stored
    # as a Measurement. Analyses rely on this to avoid consuming their own output.
    derived = Measurement(quantity="velocity", value=1.15, unit="m/s", derived=True)
    assert derived.evidence_kind is EvidenceKind.CALCULATION

    for bad, why in (
        (dict(quantity="", value=1.0, unit="m"), "unnamed quantity"),
        (dict(quantity="x", value=1.0, unit="furlongs"), "unknown unit"),
        (dict(quantity="x", value=1.0, unit="m", uncertainty=-0.5), "negative uncertainty"),
    ):
        try:
            Measurement(**bad)
        except (ValueError, UnitError):
            continue
        raise AssertionError(f"a measurement with {why} was accepted")


def test_provenance_records_origin() -> None:
    sensor = Provenance.sensor("mock_cart_encoder")
    calculated = Provenance.calculated(method="Δx/Δt", inputs=["meas_a", "meas_b"])
    ai = Provenance(ProvenanceKind.AI, agent="claude")

    assert sensor.kind is ProvenanceKind.SENSOR and not sensor.is_ai_generated
    assert calculated.inputs == ["meas_a", "meas_b"] and calculated.method == "Δx/Δt"
    assert ai.is_ai_generated, "AI provenance was not identifiable as AI"
    assert Provenance.from_dict(ai.as_dict()).kind is ProvenanceKind.AI


def test_hypothesis_vocabulary_avoids_proof() -> None:
    """A hypothesis accumulates support; it never becomes 'proven'."""
    values = {status.value for status in HypothesisStatus}
    for forbidden in ("proven", "proved", "true", "confirmed", "fact"):
        assert forbidden not in values, f"HypothesisStatus offers '{forbidden}'"
    assert {"supported", "contradicted", "inconclusive"} <= values

    hypothesis = Hypothesis(statement="The cart accelerates uniformly", experiment_id="exp_1")
    assert hypothesis.status is HypothesisStatus.PROPOSED
    hypothesis.supporting_evidence.append("ev_1")
    hypothesis.contradicting_evidence.append("ev_2")
    restored = Hypothesis.from_dict(hypothesis.as_dict())
    # Support and contradiction are kept apart, never netted into a score.
    assert restored.supporting_evidence == ["ev_1"]
    assert restored.contradicting_evidence == ["ev_2"]


def test_identical_measurements_are_not_suppressed() -> None:
    """The event bus must never collapse two readings into one.

    The v0.4 bus deduplicates repeats, which is right for a noisy sensor and
    wrong for science: five readings of 1.0 m are five facts.
    """
    events = EventSystem(MemoryStore(os.path.join(tempfile.mkdtemp(), "events.db")))
    run = new_correlation_id()
    accepted = 0
    for _ in range(5):
        measurement = Measurement(quantity="position", value=1.0, unit="m",
                                  experiment_id="exp_1", run_id=run)
        accepted += events.publish(measurement_recorded(measurement, run)).accepted

    assert accepted == 5, f"only {accepted} of 5 identical measurements were accepted"
    stats = events.stats()["bus"]
    assert stats["events"] == 5, stats
    assert stats["suppressed_repeats"] == 0, f"measurements were suppressed: {stats}"

    recorded = events.recent_events(limit=5)
    assert all(e["correlation_id"] == run for e in recorded), "correlation id was lost"
    assert all(e["kind"] == EventKind.MEASUREMENT_RECORDED for e in recorded)
    # A subscriber must be able to tell what kind of knowledge arrived without
    # fetching the record.
    assert recorded[0]["attributes"]["evidence_kind"] == "measurement"


def test_persistence_round_trip() -> None:
    path = os.path.join(tempfile.mkdtemp(), "persist.db")
    store = ScienceStore(MemoryStore(path))

    experiment = store.save_experiment(Experiment(
        title="Cart motion", research_question="How does the motion of a cart change over time?",
        independent_variables=["time"], dependent_variables=["position"],
    ))
    hypothesis = store.save_hypothesis(Hypothesis(
        statement="The cart accelerates uniformly", experiment_id=experiment.id))
    for index, (t, x) in enumerate([(0, 0.0), (1, 1.0), (2, 2.1), (3, 3.3), (4, 4.6)]):
        store.save_measurement(Measurement(quantity="time", value=t, unit="s",
                                           experiment_id=experiment.id, timestamp=float(index)))
        store.save_measurement(Measurement(quantity="position", value=x, unit="m", uncertainty=0.01,
                                           experiment_id=experiment.id, timestamp=float(index)))
    store.save_measurement(Measurement(quantity="velocity", value=1.15, unit="m/s",
                                       experiment_id=experiment.id, derived=True))
    store.save_evidence(Evidence(kind=EvidenceKind.CALCULATION, statement="Average velocity 1.15 m/s",
                                 experiment_id=experiment.id, value=1.15, unit="m/s"))
    store.save_evidence(Evidence(kind=EvidenceKind.AI_INTERPRETATION,
                                 statement="Friction may explain the residual.",
                                 experiment_id=experiment.id,
                                 provenance=Provenance(ProvenanceKind.AI, agent="test-model")))
    store.save_claim(Claim(statement="The cart accelerated", experiment_id=experiment.id,
                           hypothesis_id=hypothesis.id))

    # Reopen the database from scratch - this is the part that matters.
    reloaded = ScienceStore(MemoryStore(path))
    restored = reloaded.get_experiment(experiment.id)
    assert restored is not None and restored.title == "Cart motion"
    assert restored.independent_variables == ["time"]

    positions = reloaded.list_measurements(experiment.id, quantity="position")
    assert [m.value for m in positions] == [0.0, 1.0, 2.1, 3.3, 4.6], "measurement order was lost"
    assert positions[0].uncertainty == 0.01, "uncertainty did not survive"

    raw = reloaded.list_measurements(experiment.id, include_derived=False)
    assert len(raw) == 10 and all(not m.derived for m in raw), "derived values leaked into raw"

    kinds = {e.kind for e in reloaded.list_evidence(experiment.id)}
    assert kinds == {EvidenceKind.CALCULATION, EvidenceKind.AI_INTERPRETATION}, kinds
    ai = reloaded.list_evidence(experiment.id, kind=EvidenceKind.AI_INTERPRETATION)[0]
    # The crucial reload property: an AI interpretation is still an AI
    # interpretation, and still carries AI provenance.
    assert not ai.is_empirical
    assert ai.provenance.is_ai_generated, "AI provenance was lost on reload"

    record = reloaded.experiment_record(experiment.id)
    assert record["evidence_by_kind"] == {"calculation": 1, "ai_interpretation": 1}
    assert len(record["claims"]) == 1

    stats = reloaded.stats()
    assert stats["raw_measurements"] == 10 and stats["derived_measurements"] == 1, stats


def test_time_zero_survives_a_round_trip() -> None:
    """t = 0.0 is a real timestamp, not a missing one.

    Regression: from_dict used `data.get("timestamp") or now()`, so the first
    sample of a run - almost always t = 0 - came back from storage stamped with
    the wall clock. The series then re-sorted with its first sample last, and
    the displacement came back negative. Nothing about the result looked wrong,
    which is what made it worth a test of its own.
    """
    store = _store("epoch.db")
    reading = Measurement(quantity="time", value=0.0, unit="s",
                          experiment_id="exp-epoch", timestamp=0.0)
    assert reading.timestamp == 0.0
    store.save_measurement(reading)

    reloaded = store.get_measurement(reading.id)
    assert reloaded.timestamp == 0.0, f"t=0 became {reloaded.timestamp}"

    # Same rule for every other stored time.
    experiment = Experiment(title="Epoch", id="exp-epoch")
    experiment.created_at = 0.0
    store.save_experiment(experiment)
    assert store.get_experiment("exp-epoch").created_at == 0.0

    # An absent or unreadable value still falls back rather than crashing.
    assert Measurement.from_dict({"quantity": "t", "value": 1.0}).timestamp > 0
    assert Measurement.from_dict(
        {"quantity": "t", "value": 1.0, "timestamp": "not-a-time"}).timestamp > 0

def main() -> None:
    test_knowledge_kinds_stay_distinct()
    test_measurement_validation_and_derivation()
    test_provenance_records_origin()
    test_hypothesis_vocabulary_avoids_proof()
    test_identical_measurements_are_not_suppressed()
    test_persistence_round_trip()
    test_time_zero_survives_a_round_trip()
    print("DaQauntum v0.5 scientific core smoke test: PASS")


if __name__ == "__main__":
    main()
