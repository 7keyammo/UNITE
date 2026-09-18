"""Regression guard for research and simulation backends.

The sharpest test here is the one on the unimplemented adapter. An integration
that has not been built must fail loudly: a lookup that quietly returns an
empty list reads as "nothing has been published", which is a false claim about
the state of the literature, made by code that did nothing at all.

The rest guards the epistemic labels on the way in - prior work arrives as
LITERATURE, simulator output as SIMULATION - and that a simulation states the
assumptions it made.
"""
from __future__ import annotations

import math
import os
import tempfile

from memory.store import MemoryStore
from science.backends import (
    BackendError,
    BackendUnavailable,
    KinematicSimulation,
    LocalResearchBackend,
    OpenScienceBackend,
    ResearchResult,
    SimulationResult,
)
from science.core import ScienceCore
from science.events import EventKind
from science.models import EvidenceKind, Provenance, ProvenanceKind
from events import EventSystem


def _core(with_events: bool = False) -> ScienceCore:
    tmp = tempfile.mkdtemp()
    memory = MemoryStore(os.path.join(tmp, "backends.db"))
    return ScienceCore(memory, events=EventSystem(memory) if with_events else None,
                       artifacts_dir=os.path.join(tmp, "artifacts"))


def test_unimplemented_backend_fails_loudly() -> None:
    """The most important test in this file."""
    backend = OpenScienceBackend()
    usable, reason = backend.available()
    assert usable is False
    assert "not implemented" in reason.lower()

    description = backend.describe()
    assert description["implemented"] is False
    assert description["network_calls"] is False
    # The stub says what has to be researched, so the next person does not have
    # to rediscover why it was left alone.
    assert len(description["research_needed"]) >= 5
    assert any("endpoint" in item.lower() or "docs" in item.lower()
               for item in description["research_needed"])

    try:
        backend.search("cart acceleration")
    except BackendUnavailable as exc:
        assert "not implemented" in str(exc).lower()
        assert "nothing has been" in str(exc).lower(), (
            "the refusal must say why an empty list would be wrong")
    else:
        raise AssertionError("An unimplemented backend returned results")

    # And it cannot be smuggled past the core either.
    core = _core()
    experiment = core.create_experiment("Lookup")
    try:
        core.research(backend, "cart", experiment_id=experiment.id, record=True)
    except BackendUnavailable:
        pass
    else:
        raise AssertionError("The core ran an unimplemented backend")
    assert core.list_evidence(experiment.id) == []


def test_failed_lookup_is_announced() -> None:
    core = _core(with_events=True)
    experiment = core.create_experiment("Lookup")
    try:
        core.research(OpenScienceBackend(), "cart", experiment_id=experiment.id)
    except BackendUnavailable:
        pass
    kinds = [e["kind"] for e in core.events.bus.recent(limit=50)]
    assert EventKind.RESEARCH_FAILED in kinds, kinds
    assert EventKind.RESEARCH_COMPLETED not in kinds, "a failure was reported as completion"


def test_local_research_finds_recorded_evidence() -> None:
    core = _core()
    experiment = core.create_experiment("Prior work")
    core.record_evidence(
        experiment.id, EvidenceKind.MEASUREMENT,
        "Cart reached 1.15 m/s average velocity on the level track",
        provenance=Provenance.sensor("lab.rangefinder"),
        quantity="average_velocity", value=1.15, unit="m/s",
    )
    interpretation = core.interpret(
        experiment.id, "The cart probably encountered little friction.",
        model="local/qwen3-4b")

    backend = LocalResearchBackend(core.store)
    assert backend.available()[0] is True

    found = backend.search("level track")
    assert len(found) == 1
    assert math.isclose(found[0].value, 1.15)
    assert found[0].source.startswith("local:")
    assert found[0].identifier

    # An AI interpretation is not returned as prior work by default: a past
    # model's reading of another experiment is a poor answer to "what is known".
    assert all(interpretation.id != r.identifier for r in backend.search("friction"))
    assert backend.search("friction") == []

    # Including it is possible, but has to be asked for.
    permissive = LocalResearchBackend(core.store,
                                      include_kinds=[EvidenceKind.AI_INTERPRETATION])
    assert len(permissive.search("friction")) == 1

    try:
        backend.search("   ")
    except BackendError as exc:
        assert "needs a query" in str(exc), exc
    else:
        raise AssertionError("Searched with an empty query")


def test_prior_work_is_literature_not_measurement() -> None:
    """Someone else measured it. That is a different position from measuring it."""
    result = ResearchResult(
        title="Uniform acceleration on an inclined plane",
        summary="Reported acceleration 0.102 m/s^2 for a comparable cart.",
        source="example-repository",
        identifier="10.0000/example",
        quantity="acceleration", value=0.102, unit="m/s^2", uncertainty=0.004,
        year=1998,
    )
    evidence = result.as_evidence("exp-1")
    assert evidence.kind is EvidenceKind.LITERATURE
    assert not evidence.is_empirical, "prior work is not this investigation's observation"
    assert evidence.provenance.kind is ProvenanceKind.IMPORTED
    assert "10.0000/example" in evidence.provenance.notes
    assert math.isclose(evidence.value, 0.102)

    # Every result has to say where it came from.
    try:
        ResearchResult(title="Anonymous", summary="From somewhere", source="")
    except BackendError as exc:
        assert "where it came from" in str(exc), exc
    else:
        raise AssertionError("A result with no source was accepted")


def test_recorded_research_enters_as_literature() -> None:
    core = _core()
    experiment = core.create_experiment("With prior work")
    core.record_evidence(
        experiment.id, EvidenceKind.MEASUREMENT, "Cart average velocity 1.15 m/s",
        provenance=Provenance.sensor("lab.rangefinder"),
        quantity="average_velocity", value=1.15, unit="m/s")

    other = core.create_experiment("Lookup target")
    results = core.research(LocalResearchBackend(core.store), "cart average",
                            experiment_id=other.id, record=True)
    assert results
    recorded = core.list_evidence(other.id)
    assert recorded and all(e.kind is EvidenceKind.LITERATURE for e in recorded)
    assert all(not e.is_empirical for e in recorded)


def test_simulation_output_is_labelled_and_explained() -> None:
    core = _core()
    experiment = core.create_experiment("Kinematics")
    backend = KinematicSimulation()

    result, stored = core.simulate(
        experiment.id, backend,
        initial_position=0.0, initial_velocity=1.0, acceleration=0.1,
        duration=4.0, steps=5,
    )
    # x(t) = t + 0.05t^2
    assert [round(v, 10) for v in result.values] == [0.0, 1.05, 2.2, 3.45, 4.8]
    assert result.times == [0.0, 1.0, 2.0, 3.0, 4.0]

    # A model that does not say what it ignored invites its output to be read
    # as the behaviour of the real system.
    assert result.assumptions
    assert any("friction" in a.lower() for a in result.assumptions)

    assert len(stored) == 10          # a time and a position per step
    for measurement in stored:
        assert measurement.is_simulated
        assert not measurement.is_reading
        assert measurement.evidence_kind is EvidenceKind.SIMULATION
        assert measurement.metadata["model"] == backend.name
        assert measurement.metadata["assumptions"]

    assert core.stats()["raw_measurements"] == 0
    assert core.stats()["simulated_measurements"] == 10


def test_simulated_data_analyses_with_a_warning() -> None:
    core = _core()
    experiment = core.create_experiment("Simulated then analysed")
    core.simulate(experiment.id, KinematicSimulation(),
                  initial_velocity=1.0, acceleration=0.1, duration=4.0, steps=5)
    analysis, evidence = core.analyse_motion(experiment.id)

    assert math.isclose(analysis.average_velocity.value, 1.2, rel_tol=1e-9)
    assert any("were simulated" in w for w in analysis.warnings), analysis.warnings
    assert all(e.metadata.get("simulated_inputs") == 10 for e in evidence)


def test_simulation_refuses_impossible_parameters() -> None:
    backend = KinematicSimulation()
    for kwargs, expected in (
        ({"steps": 1}, "at least two steps"),
        ({"duration": 0.0}, "must be positive"),
        ({"duration": -1.0}, "must be positive"),
    ):
        try:
            backend.run(**kwargs)
        except BackendError as exc:
            assert expected in str(exc), exc
        else:
            raise AssertionError(f"Accepted {kwargs}")

    try:
        SimulationResult(quantity="x", values=[1.0], unit="m", model="")
    except BackendError as exc:
        assert "name its model" in str(exc), exc
    else:
        raise AssertionError("A simulation result with no model was accepted")

    try:
        SimulationResult(quantity="x", values=[1.0, 2.0], unit="m",
                         times=[0.0], model="test")
    except BackendError as exc:
        assert "One time per value" in str(exc), exc
    else:
        raise AssertionError("Accepted mismatched times and values")


def test_velocity_simulation_carries_the_right_unit() -> None:
    result = KinematicSimulation().run(
        initial_velocity=1.0, acceleration=0.1, duration=4.0, steps=5,
        quantity="velocity")
    assert result.unit == "m/s"
    assert [round(v, 10) for v in result.values] == [1.0, 1.1, 1.2, 1.3, 1.4]


def main() -> None:
    test_unimplemented_backend_fails_loudly()
    test_failed_lookup_is_announced()
    test_local_research_finds_recorded_evidence()
    test_prior_work_is_literature_not_measurement()
    test_recorded_research_enters_as_literature()
    test_simulation_output_is_labelled_and_explained()
    test_simulated_data_analyses_with_a_warning()
    test_simulation_refuses_impossible_parameters()
    test_velocity_simulation_carries_the_right_unit()
    print("DaQauntum v0.5 backends smoke test: PASS")


if __name__ == "__main__":
    main()
