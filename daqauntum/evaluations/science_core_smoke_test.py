"""Regression guard for the ScienceCore facade.

The facade is where a careless caller would do the most damage, because it is
the one path every part of the system uses. These tests are mostly about what
it refuses: a calculated value entering as a reading, a model's opinion filed
as a measurement, a claim citing evidence that does not exist, a hypothesis
quietly promoted to true.

They also check the ordinary happy path, because a core that only says no is
not a core.
"""
from __future__ import annotations

import math
import os
import tempfile

from events import EventSystem
from memory.store import MemoryStore
from science.core import ScienceCore, ScienceError
from science.events import EventKind
from science.models import (
    ClaimStatus,
    Depth,
    EvidenceKind,
    ExperimentStatus,
    HypothesisStatus,
    Provenance,
    ProvenanceKind,
)

CART_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]
CART_POSITIONS = [0.0, 1.0, 2.1, 3.3, 4.6]


def _core(*, with_events: bool = False) -> tuple[ScienceCore, EventSystem | None, MemoryStore]:
    memory = MemoryStore(os.path.join(tempfile.mkdtemp(), "core.db"))
    events = EventSystem(memory) if with_events else None
    return ScienceCore(memory, events=events), events, memory


def _cart_experiment(core: ScienceCore, **kwargs):
    experiment = core.create_experiment(
        "Cart on a track",
        research_question="How does the cart's position change with time?",
        depth=Depth.INTRODUCTORY,
        independent_variables=["time"],
        dependent_variables=["position"],
        **kwargs,
    )
    core.record_series(experiment.id, "time", CART_TIMES, "s",
                       uncertainty=0.01, source="mock.cart",
                       timestamps=[1000.0 + i for i in range(5)],
                       provenance=Provenance.sensor("mock.cart"))
    core.record_series(experiment.id, "position", CART_POSITIONS, "m",
                       uncertainty=0.01, source="mock.cart",
                       timestamps=[1000.0 + i for i in range(5)],
                       provenance=Provenance.sensor("mock.cart"))
    return experiment


def test_full_workflow_runs_through_the_facade() -> None:
    core, _, _ = _core()
    experiment = _cart_experiment(core)
    assert core.get_experiment(experiment.id).title == "Cart on a track"

    hypothesis = core.propose_hypothesis(
        experiment.id, "The cart accelerates uniformly",
        expected_relationship="x increases faster than linearly in t",
    )
    assert hypothesis.status is HypothesisStatus.PROPOSED

    core.set_status(experiment.id, ExperimentStatus.ACTIVE)
    analysis, evidence = core.analyse_motion(experiment.id)

    assert analysis.samples == 5
    assert math.isclose(analysis.average_velocity.value, 1.15, rel_tol=1e-9)
    assert math.isclose(analysis.average_acceleration.value, 0.1, rel_tol=1e-9)

    # displacement, average velocity, average acceleration
    assert len(evidence) == 3
    assert all(e.kind is EvidenceKind.CALCULATION for e in evidence)

    for item in evidence:
        core.link_evidence(hypothesis.id, item.id, supports=True)
    reloaded = core.store.get_hypothesis(hypothesis.id)
    assert len(reloaded.supporting_evidence) == 3
    # Linking evidence does not move the status. A person has to say so.
    assert reloaded.status is HypothesisStatus.PROPOSED

    assessed = core.assess_hypothesis(
        hypothesis.id, HypothesisStatus.SUPPORTED,
        assessor="user", reasoning="Velocity rises across every interval.",
    )
    assert assessed.status is HypothesisStatus.SUPPORTED
    assert assessed.metadata["assessments"][0]["assessor"] == "user"

    observation = core.observe(experiment.id, "The cart ran straight and did not wobble.")
    claim = core.make_claim(
        experiment.id, "The cart accelerated at about 0.1 m/s^2.",
        evidence_ids=[e.id for e in evidence] + [observation.id],
        hypothesis_id=hypothesis.id, require_empirical=True,
    )
    assert claim.status is ClaimStatus.PROPOSED
    assert set(claim.metadata["evidence_kinds"]) == {"calculation", "observation"}

    core.set_status(experiment.id, ExperimentStatus.COMPLETED)
    record = core.experiment_record(experiment.id)
    assert record["experiment"]["status"] == "completed"
    assert record["evidence_by_kind"]["calculation"] == 3
    assert len(record["claims"]) == 1


def test_calculated_values_cannot_enter_as_readings() -> None:
    """The facade's most important refusal."""
    core, _, _ = _core()
    experiment = core.create_experiment("Refusals")

    for kind in (ProvenanceKind.CALCULATED, ProvenanceKind.SIMULATED, ProvenanceKind.AI):
        try:
            core.record_measurement(experiment.id, "velocity", 1.15, "m/s",
                                    provenance=Provenance(kind, agent="whatever"))
        except ScienceError as exc:
            assert kind.value in str(exc), exc
        else:
            raise AssertionError(f"{kind.value} provenance was accepted as a reading")

    # A sensor reading is fine, and lands in the raw set.
    core.record_measurement(experiment.id, "position", 1.0, "m",
                            provenance=Provenance.sensor("mock.cart"))
    raw = core.list_measurements(experiment.id, include_derived=False)
    assert len(raw) == 1 and raw[0].derived is False


def test_analysis_never_consumes_its_own_output() -> None:
    """A second pass must see the same readings, not its own results."""
    core, _, _ = _core()
    experiment = _cart_experiment(core)

    first, _ = core.analyse_motion(experiment.id)
    second, _ = core.analyse_motion(experiment.id)
    assert first.samples == second.samples == 5
    assert math.isclose(first.average_velocity.value, second.average_velocity.value)

    everything = core.list_measurements(experiment.id)
    raw = core.list_measurements(experiment.id, include_derived=False)
    assert len(raw) == 10, "readings changed"
    assert len(everything) > len(raw), "derived results were not stored"
    raw_ids = {m.id for m in raw}
    assert all(m.derived for m in everything if m.id not in raw_ids)


def test_ai_interpretation_cannot_pass_as_evidence_of_the_world() -> None:
    core, _, _ = _core()
    experiment = _cart_experiment(core)
    _, evidence = core.analyse_motion(experiment.id)

    reading = core.interpret(
        experiment.id,
        "The near-constant interval velocity suggests friction was negligible.",
        model="local/qwen3-4b",
        evidence_ids=[e.id for e in evidence],
        confidence=0.82,
    )
    assert reading.kind is EvidenceKind.AI_INTERPRETATION
    assert not reading.is_empirical
    assert reading.provenance.is_ai_generated
    assert reading.provenance.agent == "local/qwen3-4b"
    # Confidence is stored as a self-report and labelled, never as a probability.
    assert reading.metadata["model_self_reported_confidence"] == 0.82
    assert "Not a probability" in reading.metadata["confidence_note"]

    # The model has to be named.
    try:
        core.interpret(experiment.id, "Something happened.", model="  ")
    except ScienceError as exc:
        assert "name the model" in str(exc), exc
    else:
        raise AssertionError("An unattributed AI interpretation was accepted")

    # An empirical claim cannot rest on interpretation alone.
    try:
        core.make_claim(experiment.id, "Friction was negligible.",
                        evidence_ids=[reading.id], require_empirical=True)
    except ScienceError as exc:
        assert "no measurement or observation" in str(exc), exc
    else:
        raise AssertionError("An interpretation-only empirical claim was accepted")

    # Stated as a proposal rather than an empirical result, it is allowed -
    # the record still shows exactly what it rests on.
    proposal = core.make_claim(experiment.id, "Friction may have been negligible.",
                               evidence_ids=[reading.id])
    assert proposal.metadata["evidence_kinds"] == ["ai_interpretation"]


def test_claims_cannot_cite_evidence_that_does_not_exist() -> None:
    core, _, _ = _core()
    experiment = core.create_experiment("Citations")
    try:
        core.make_claim(experiment.id, "Something is true.", evidence_ids=["ev-does-not-exist"])
    except ScienceError as exc:
        assert "does not exist" in str(exc), exc
    else:
        raise AssertionError("A claim cited evidence that was never recorded")

    try:
        core.propose_hypothesis(experiment.id, "A guess")
        core.link_evidence("hyp-missing", "ev-missing", supports=True)
    except ScienceError as exc:
        assert "No hypothesis" in str(exc), exc
    else:
        raise AssertionError("Evidence was linked to a hypothesis that does not exist")


def test_evidence_cannot_both_support_and_contradict() -> None:
    core, _, _ = _core()
    experiment = _cart_experiment(core)
    hypothesis = core.propose_hypothesis(experiment.id, "The cart moved")
    _, evidence = core.analyse_motion(experiment.id)

    core.link_evidence(hypothesis.id, evidence[0].id, supports=True)
    # Idempotent: linking the same way twice does not duplicate.
    core.link_evidence(hypothesis.id, evidence[0].id, supports=True)
    assert core.store.get_hypothesis(hypothesis.id).supporting_evidence == [evidence[0].id]

    try:
        core.link_evidence(hypothesis.id, evidence[0].id, supports=False)
    except ScienceError as exc:
        assert "opposite sense" in str(exc), exc
    else:
        raise AssertionError("The same evidence was recorded as both support and contradiction")


def test_assessment_requires_a_named_assessor() -> None:
    core, _, _ = _core()
    experiment = core.create_experiment("Attribution")
    hypothesis = core.propose_hypothesis(experiment.id, "A guess")
    try:
        core.assess_hypothesis(hypothesis.id, HypothesisStatus.SUPPORTED, assessor="")
    except ScienceError as exc:
        assert "who made it" in str(exc), exc
    else:
        raise AssertionError("An anonymous assessment was accepted")

    # And there is no way to say 'proven'.
    assert "proven" not in {s.value for s in HypothesisStatus}
    try:
        core.assess_hypothesis(hypothesis.id, "proven", assessor="user")
    except ValueError:
        pass
    else:
        raise AssertionError("A hypothesis was marked proven")


def test_events_carry_the_workflow_and_survive_suppression() -> None:
    """Every step announces itself, and repeats are never collapsed."""
    core, events, _ = _core(with_events=True)
    correlation = core.new_correlation_id()
    experiment = core.create_experiment("Events", correlation_id=correlation)

    # Five identical readings: five events, not one with a repeat counter.
    core.record_series(experiment.id, "time", [0.0, 1.0, 2.0, 3.0, 4.0], "s",
                       correlation_id=correlation)
    core.record_series(experiment.id, "position", [1.0] * 5, "m", correlation_id=correlation)

    recent = events.bus.recent(limit=200)
    kinds = [e["kind"] for e in recent]
    assert kinds.count(EventKind.MEASUREMENT_RECORDED) == 10, kinds
    assert EventKind.EXPERIMENT_CREATED in kinds

    analysis, _ = core.analyse_motion(experiment.id, correlation_id=correlation)
    # A stationary cart: displacement zero, and the engine says the fit is flat
    # rather than inventing motion.
    assert math.isclose(analysis.displacement.value, 0.0, abs_tol=1e-12)

    kinds = [e["kind"] for e in events.bus.recent(limit=400)]
    assert EventKind.ANALYSIS_REQUESTED in kinds and EventKind.ANALYSIS_COMPLETED in kinds

    tagged = [e for e in events.bus.recent(limit=400)
              if e.get("correlation_id") == correlation]
    assert len(tagged) >= 12, f"correlation id did not thread through: {len(tagged)}"


def test_analysis_failure_is_announced_not_swallowed() -> None:
    core, events, _ = _core(with_events=True)
    experiment = core.create_experiment("Bad data")
    core.record_measurement(experiment.id, "time", 0.0, "s")
    core.record_measurement(experiment.id, "position", 0.0, "m")
    core.record_measurement(experiment.id, "position", 1.0, "m")

    try:
        core.analyse_motion(experiment.id)
    except Exception:
        pass
    else:
        raise AssertionError("Mismatched series should not analyse")

    kinds = [e["kind"] for e in events.bus.recent(limit=100)]
    assert EventKind.ANALYSIS_FAILED in kinds, kinds


def test_core_works_without_an_event_bus() -> None:
    """Scripts and tests must not need the whole pipeline to record science."""
    core, events, _ = _core()
    assert events is None
    experiment = _cart_experiment(core)
    analysis, evidence = core.analyse_motion(experiment.id)
    assert analysis.samples == 5 and len(evidence) == 3
    assert core.stats()["raw_measurements"] == 10


def main() -> None:
    test_full_workflow_runs_through_the_facade()
    test_calculated_values_cannot_enter_as_readings()
    test_analysis_never_consumes_its_own_output()
    test_ai_interpretation_cannot_pass_as_evidence_of_the_world()
    test_claims_cannot_cite_evidence_that_does_not_exist()
    test_evidence_cannot_both_support_and_contradict()
    test_assessment_requires_a_named_assessor()
    test_events_carry_the_workflow_and_survive_suppression()
    test_analysis_failure_is_announced_not_swallowed()
    test_core_works_without_an_event_bus()
    print("DaQauntum v0.5 science core facade smoke test: PASS")


if __name__ == "__main__":
    main()
