"""Regression guard for the physics registry and scientific validation.

The guarantee this suite exists to protect is fail-closed: anything the
validator does not understand resolves to UNCERTAIN, never to PASS. A validator
that passes what it cannot check certifies whatever it fails to understand,
which is worse than having no validator - users trust it, and it is blind
exactly where the novel work is.

The second guarantee is that PASS stays narrow. It means specific checks found
no contradiction. It is not a truth score, and the report says so in the words
it hands back.
"""
from __future__ import annotations

import os
import tempfile

from memory.store import MemoryStore
from physics import CONCEPTS, RELATIONS, Concept, ConceptError, find_concept
from physics.concepts import (
    concept,
    concepts_with_dimension,
    registry_summary,
    relations_for,
)
from science.core import ScienceCore
from science.models import (
    Claim,
    Evidence,
    EvidenceKind,
    Experiment,
    Hypothesis,
    Measurement,
    Provenance,
    ProvenanceKind,
)
from science.reference import run_cart_motion
from science.validation import (
    ValidationReport,
    Verdict,
    check_relation_dimensions,
    validate_claim,
    validate_evidence,
    validate_experiment,
    validate_hypothesis,
    validate_measurement,
    validate_series,
)


def test_unknown_things_are_uncertain_never_passed() -> None:
    """The guarantee the whole module turns on."""
    unknown = Measurement(
        quantity="phlogiston density", value=1.0, unit="kg", uncertainty=0.1,
        provenance=Provenance.sensor("lab.probe"))
    report = validate_measurement(unknown)

    assert report.verdict is Verdict.UNCERTAIN, report.summary()
    assert not report.failures, "an unknown quantity is not a contradiction"
    check = next(f for f in report.findings if f.check == "unit_matches_quantity")
    assert check.verdict is Verdict.UNCERTAIN
    assert "not in the physics registry" in check.message
    # The report says plainly that it could not establish this.
    assert "could not be established" in report.summary()["meaning"].lower()


def test_one_unknown_makes_the_whole_report_uncertain() -> None:
    """A check that could not run leaves its problem unexamined."""
    report = ValidationReport()
    known = Measurement(quantity="velocity", value=1.15, unit="m/s", uncertainty=0.01,
                        provenance=Provenance.sensor("lab"))
    report.findings.extend(validate_measurement(known).findings)
    assert report.verdict is Verdict.PASS

    report.findings.extend(validate_measurement(Measurement(
        quantity="unregistered quantity", value=1.0, unit="m", uncertainty=0.01,
        provenance=Provenance.sensor("lab"))).findings)
    assert report.verdict is Verdict.UNCERTAIN, "an unknown was absorbed into a pass"

    # An empty report is uncertain too: no checks ran.
    assert ValidationReport().verdict is Verdict.UNCERTAIN


def test_a_pass_stays_narrow() -> None:
    good = Measurement(quantity="velocity", value=1.15, unit="m/s", uncertainty=0.003,
                       provenance=Provenance.sensor("lab.rangefinder"))
    report = validate_measurement(good)
    assert report.verdict is Verdict.PASS
    meaning = report.summary()["meaning"]
    assert "found no contradiction" in meaning
    assert "Not a statement that the result is correct" in meaning
    # No score, no confidence, no probability anywhere in the output.
    text = str(report.summary()).lower()
    for forbidden in ("score", "confidence", "probability", "likelihood"):
        assert forbidden not in text, f"validation emitted a {forbidden}"


def test_real_contradictions_fail() -> None:
    cases = (
        (Measurement(quantity="velocity", value=1.0, unit="kg",
                     provenance=Provenance.sensor("lab")),
         "unit_matches_quantity"),
        (Measurement(quantity="position", value=1.0, unit="m", uncertainty=5.0,
                     provenance=Provenance.sensor("lab")),
         "uncertainty_present"),
        (Measurement(quantity="position", value=1.0, unit="m", uncertainty=0.01,
                     provenance=Provenance(ProvenanceKind.SENSOR, agent="")),
         "provenance_named"),
    )
    for measurement, expected_check in cases:
        report = validate_measurement(measurement)
        assert report.verdict is Verdict.FAIL, (expected_check, report.summary())
        assert any(f.check == expected_check for f in report.failures), \
            f"{expected_check} did not fail: {[f.check for f in report.failures]}"

    # An unparseable unit stops the run rather than carrying on with a guess.
    broken = Measurement.from_dict(
        {"quantity": "position", "value": 1.0, "unit": "m", "uncertainty": 0.01})
    object.__setattr__(broken, "unit", "furlongs-per-fortnight")
    report = validate_measurement(broken)
    assert report.verdict is Verdict.FAIL
    assert len(report.findings) == 1, "checks kept running after the unit failed to parse"


def test_missing_uncertainty_is_uncertain_not_failed() -> None:
    """Absent is not wrong. The validator must not invent a defect."""
    measurement = Measurement(quantity="position", value=1.0, unit="m",
                              provenance=Provenance.sensor("lab"))
    report = validate_measurement(measurement)
    assert report.verdict is Verdict.UNCERTAIN
    finding = next(f for f in report.findings if f.check == "uncertainty_present")
    assert finding.verdict is Verdict.UNCERTAIN
    assert "cannot tell which" in finding.message


def test_series_checks_catch_mixed_data() -> None:
    sensor = Provenance.sensor("lab")
    simulated = Provenance(ProvenanceKind.SIMULATED, agent="sim")

    consistent = [Measurement(quantity="position", value=v, unit="m", uncertainty=0.01,
                              timestamp=float(i), provenance=sensor)
                  for i, v in enumerate([0.0, 1.0, 2.0])]
    assert validate_series(consistent).verdict is Verdict.PASS

    # Mixed dimensions are a contradiction.
    mixed_units = consistent + [Measurement(quantity="position", value=1.0, unit="kg",
                                            timestamp=9.0, provenance=sensor)]
    report = validate_series(mixed_units)
    assert report.verdict is Verdict.FAIL
    assert any("not readings of the same thing" in f.message for f in report.failures)

    # Mixed origins are allowed, and must be visible.
    mixed_origin = consistent + [Measurement(quantity="position", value=3.0, unit="m",
                                             uncertainty=0.01, timestamp=3.0,
                                             provenance=simulated)]
    report = validate_series(mixed_origin)
    assert report.verdict is Verdict.UNCERTAIN
    finding = next(f for f in report.findings if f.check == "series_one_origin")
    assert "describes the mixture" in finding.message

    assert validate_series([]).verdict is Verdict.UNCERTAIN


def test_calculations_must_state_their_method() -> None:
    no_method = Evidence(kind=EvidenceKind.CALCULATION, statement="v = 1.15 m/s",
                         provenance=Provenance(ProvenanceKind.CALCULATED, agent="x"))
    report = validate_evidence(no_method)
    assert report.verdict is Verdict.FAIL
    assert any("cannot be checked or reproduced" in f.message for f in report.failures)

    with_method = Evidence(
        kind=EvidenceKind.CALCULATION, statement="v = 1.15 m/s",
        provenance=Provenance.calculated("v = dx/dt", ["m1", "m2"]))
    assert validate_evidence(with_method).verdict is Verdict.PASS


def test_ai_evidence_is_checked_for_its_label_not_its_content() -> None:
    """The validator confirms the labelling. It does not fact-check the model."""
    reading = Evidence(
        kind=EvidenceKind.AI_INTERPRETATION,
        statement="Friction was probably negligible.",
        provenance=Provenance(ProvenanceKind.AI, agent="local/qwen3-4b"))
    report = validate_evidence(reading)
    finding = next(f for f in report.findings if f.check == "ai_is_labelled")
    assert finding.verdict is Verdict.PASS
    assert "not the content" in finding.message
    assert "no part of this system evaluates whether a model's reading is correct" \
        in finding.message


def test_claims_are_checked_against_what_they_cite() -> None:
    evidence = {
        "ev1": Evidence(kind=EvidenceKind.MEASUREMENT, statement="x = 1.0 m",
                        measurement_ids=["m1"]),
        "ev2": Evidence(kind=EvidenceKind.AI_INTERPRETATION, statement="Probably smooth.",
                        provenance=Provenance(ProvenanceKind.AI, agent="model")),
    }
    resolve = evidence.get

    good = Claim(statement="The cart moved 1 m.", evidence_ids=["ev1"])
    assert validate_claim(good, resolve).verdict is Verdict.PASS

    missing = Claim(statement="Unfalsifiable.", evidence_ids=["ev1", "ev-nope"])
    report = validate_claim(missing, resolve)
    assert report.verdict is Verdict.FAIL
    assert "do not exist" in report.failures[0].message

    none_at_all = Claim(statement="Trust me.", evidence_ids=[])
    assert validate_claim(none_at_all, resolve).verdict is Verdict.FAIL

    # Interpretation-only is uncertain, not failed: it may still be right.
    interpretation_only = Claim(statement="The track was smooth.", evidence_ids=["ev2"])
    report = validate_claim(interpretation_only, resolve)
    assert report.verdict is Verdict.UNCERTAIN
    assert "may still be right" in report.uncertain[0].message


def test_hypotheses_cannot_double_count_evidence() -> None:
    conflicted = Hypothesis(statement="A guess", supporting_evidence=["ev1"],
                            contradicting_evidence=["ev1"])
    report = validate_hypothesis(conflicted)
    assert report.verdict is Verdict.FAIL
    assert "both supporting and contradicting" in report.failures[0].message

    untested = Hypothesis(statement="A guess")
    assert validate_hypothesis(untested).verdict is Verdict.UNCERTAIN


def test_concept_registry_is_dimensionally_consistent() -> None:
    summary = registry_summary()
    assert summary["concepts"] >= 10 and summary["relations"] >= 6
    assert "not a solver" in summary["note"]

    # Every declared common unit really is a unit of that concept's dimension.
    for item in CONCEPTS.values():
        for unit in item.common_units:
            assert item.compatible_with_unit(unit), f"{item.key}: {unit}"

    assert find_concept("speed").key == "velocity"
    assert find_concept("Δx").key == "displacement"
    # Exact matching: a near-miss returns nothing rather than the wrong quantity.
    assert find_concept("velocit") is None
    assert find_concept("") is None

    assert {c.key for c in concepts_with_dimension("km/h")} == {"velocity"}
    assert "average_velocity" in {r.key for r in relations_for("velocity")}

    try:
        concept("not-a-quantity")
    except ConceptError as exc:
        assert "Unknown concept" in str(exc), exc
    else:
        raise AssertionError("An unknown concept resolved to something")


def test_every_relation_states_its_conditions() -> None:
    """An equation with no stated conditions is a source of quiet errors."""
    for relation in RELATIONS.values():
        assert relation.conditions, f"{relation.key} states no conditions"
        assert relation.result in CONCEPTS
        assert all(key in CONCEPTS for key in relation.inputs)

    finding = check_relation_dimensions("newton_second_law")
    assert finding.verdict is Verdict.PASS
    assert "net force" in finding.message.lower()
    assert "Not a check that the equation applies" in finding.detail["note"]

    # v = dx/dt carries the warning that it is an average.
    average = check_relation_dimensions("average_velocity")
    assert "average over the interval" in average.message

    assert check_relation_dimensions("no_such_relation").verdict is Verdict.UNCERTAIN


def test_a_real_experiment_validates_cleanly_but_not_blindly() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = ScienceCore(MemoryStore(os.path.join(tmp, "v.db")),
                           artifacts_dir=os.path.join(tmp, "art"))
        result = run_cart_motion(core, plot=False)
        record_id = result.experiment.id

        report = validate_experiment(
            result.experiment,
            measurements=core.list_measurements(record_id, include_derived=False),
            evidence=core.list_evidence(record_id),
            hypotheses=core.list_hypotheses(record_id),
            claims=core.list_claims(record_id),
            resolve_evidence=core.store.get_evidence,
        )
        # No contradictions: units match quantities, methods are recorded,
        # claims resolve.
        assert not report.failures, [f.as_dict() for f in report.failures]
        assert report.verdict in {Verdict.PASS, Verdict.UNCERTAIN}
        assert len(report.findings) > 20, "too few checks ran to mean anything"


def main() -> None:
    test_unknown_things_are_uncertain_never_passed()
    test_one_unknown_makes_the_whole_report_uncertain()
    test_a_pass_stays_narrow()
    test_real_contradictions_fail()
    test_missing_uncertainty_is_uncertain_not_failed()
    test_series_checks_catch_mixed_data()
    test_calculations_must_state_their_method()
    test_ai_evidence_is_checked_for_its_label_not_its_content()
    test_claims_are_checked_against_what_they_cite()
    test_hypotheses_cannot_double_count_evidence()
    test_concept_registry_is_dimensionally_consistent()
    test_every_relation_states_its_conditions()
    test_a_real_experiment_validates_cleanly_but_not_blindly()
    print("DaQauntum v0.5 validation smoke test: PASS")


if __name__ == "__main__":
    main()
