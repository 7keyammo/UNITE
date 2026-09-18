"""Scientific validation: finding contradictions, never awarding truth.

Every check here can say one of three things about what it examined:

    FAIL        this contradicts something definite - a unit that does not
                match its quantity's dimension, a claim citing evidence that
                does not exist, a negative uncertainty
    UNCERTAIN   this could not be established either way
    PASS        this specific check found no contradiction

A PASS is the narrowest statement in the system. It means one check found one
kind of problem absent. It does not mean the value is correct, the experiment
was sound, or the conclusion follows - and no combination of passes adds up to
any of those.

Unknowns therefore resolve to UNCERTAIN, never to PASS. A quantity this system
has never heard of is not thereby validated; a check that cannot run has not
been passed. The failure mode of a validator that reports PASS when it does not
know is a system that certifies whatever it fails to understand, which is worse
than no validator at all.

There is deliberately no automated truth score, no confidence number and no AI
fact-checker. The checks are mechanical and dimensional. Whether a result is
*right* is a judgment this module does not make and cannot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from physics.concepts import CONCEPTS, ConceptError, find_concept, relations_for
from science.models import (
    Claim,
    Evidence,
    EvidenceKind,
    Experiment,
    Hypothesis,
    Measurement,
)
from science.units import DimensionalityError, UnitError, parse_unit


class Verdict(str, Enum):
    """What a check concluded. Ordered by how much it lets through."""

    FAIL = "fail"
    UNCERTAIN = "uncertain"
    PASS = "pass"

    @property
    def is_blocking(self) -> bool:
        return self is Verdict.FAIL


@dataclass
class Finding:
    """One check, its verdict and why."""

    check: str
    verdict: Verdict
    message: str
    subject: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.check, "verdict": self.verdict.value,
                "message": self.message, "subject": self.subject,
                "detail": dict(self.detail)}


@dataclass
class ValidationReport:
    """The findings from one validation run, and what they add up to."""

    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: Finding) -> Finding:
        self.findings.append(finding)
        return finding

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.FAIL]

    @property
    def uncertain(self) -> list[Finding]:
        return [f for f in self.findings if f.verdict is Verdict.UNCERTAIN]

    @property
    def verdict(self) -> Verdict:
        """The overall result: any failure fails; any unknown is uncertain.

        A report is only PASS when every check ran and every check found no
        contradiction. One check that could not run makes the whole report
        UNCERTAIN, because the thing it would have caught is still unexamined.
        """
        if self.failures:
            return Verdict.FAIL
        if self.uncertain:
            return Verdict.UNCERTAIN
        return Verdict.PASS if self.findings else Verdict.UNCERTAIN

    def summary(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "checks": len(self.findings),
            "failures": len(self.failures),
            "uncertain": len(self.uncertain),
            "findings": [f.as_dict() for f in self.findings],
            "meaning": {
                "fail": "Contradicts something definite. Look at it.",
                "uncertain": "Could not be established either way.",
                "pass": ("These specific checks found no contradiction. Not a "
                         "statement that the result is correct."),
            }[self.verdict.value],
        }


# Measurements ----------------------------------------------------------------
def validate_measurement(measurement: Measurement) -> ValidationReport:
    """Check one measurement for internal contradictions."""
    report = ValidationReport()

    # 1. Does the unit parse at all?
    try:
        unit = parse_unit(measurement.unit)
    except UnitError as exc:
        report.add(Finding("unit_parses", Verdict.FAIL, str(exc), measurement.id))
        return report
    report.add(Finding("unit_parses", Verdict.PASS,
                       f"{measurement.unit or 'dimensionless'} is a unit this system understands",
                       measurement.id))

    # 2. Does the unit match the quantity it claims to measure?
    concept = find_concept(measurement.quantity)
    if concept is None:
        # The crucial branch. An unrecognised quantity is not thereby correct.
        report.add(Finding(
            "unit_matches_quantity", Verdict.UNCERTAIN,
            f"{measurement.quantity!r} is not in the physics registry, so its unit "
            "could not be checked against it",
            measurement.id,
            {"quantity": measurement.quantity, "unit": measurement.unit,
             "known_quantities": len(CONCEPTS)},
        ))
    elif unit.dimension != concept.dimension:
        from science.units import dimension_name as _dimension_name

        report.add(Finding(
            "unit_matches_quantity", Verdict.FAIL,
            f"{concept.name} is measured in {concept.dimension_name} (SI: "
            f"{concept.si_unit}), but {measurement.unit!r} is a unit of "
            f"{_dimension_name(unit.dimension)}",
            measurement.id,
            {"expected_dimension": concept.dimension_name,
             "expected_si_unit": concept.si_unit, "got_unit": measurement.unit,
             "got_dimension": _dimension_name(unit.dimension)},
        ))
    else:
        report.add(Finding(
            "unit_matches_quantity", Verdict.PASS,
            f"{measurement.unit!r} is a unit of {concept.dimension_name}, as "
            f"{concept.name} should be",
            measurement.id))

    # 3. Uncertainty sanity.
    if measurement.uncertainty is None:
        report.add(Finding(
            "uncertainty_present", Verdict.UNCERTAIN,
            "No uncertainty recorded. The value may be exact or the uncertainty may "
            "simply not have been noted; this check cannot tell which.",
            measurement.id))
    elif measurement.uncertainty < 0:
        report.add(Finding("uncertainty_present", Verdict.FAIL,
                           "Uncertainty is negative", measurement.id))
    elif measurement.value and abs(measurement.uncertainty) > abs(measurement.value):
        report.add(Finding(
            "uncertainty_present", Verdict.FAIL,
            f"Uncertainty ({measurement.uncertainty}) exceeds the value "
            f"({measurement.value}); the measurement does not constrain anything",
            measurement.id))
    else:
        report.add(Finding("uncertainty_present", Verdict.PASS,
                           "Uncertainty recorded and smaller than the value",
                           measurement.id))

    # 4. Does the record say where it came from?
    if not measurement.provenance.agent:
        report.add(Finding("provenance_named", Verdict.FAIL,
                           "No agent recorded: nothing says who or what produced this",
                           measurement.id))
    else:
        report.add(Finding(
            "provenance_named", Verdict.PASS,
            f"Recorded as {measurement.evidence_kind.value} from "
            f"{measurement.provenance.agent}", measurement.id))

    return report


def validate_series(measurements: Sequence[Measurement]) -> ValidationReport:
    """Check a set of readings for consistency with each other."""
    report = ValidationReport()
    if not measurements:
        report.add(Finding("series_not_empty", Verdict.UNCERTAIN,
                           "No measurements to check"))
        return report

    units = {m.unit for m in measurements}
    dimensions = set()
    for unit in units:
        try:
            dimensions.add(parse_unit(unit).dimension)
        except UnitError:
            report.add(Finding("series_units_parse", Verdict.FAIL,
                               f"{unit!r} is not a unit this system understands"))
            return report

    if len(dimensions) > 1:
        report.add(Finding(
            "series_one_dimension", Verdict.FAIL,
            f"The series mixes {len(dimensions)} dimensions across units {sorted(units)}; "
            "these are not readings of the same thing",
            detail={"units": sorted(units)}))
    else:
        report.add(Finding("series_one_dimension", Verdict.PASS,
                           f"All {len(measurements)} readings share one dimension"))

    kinds = {m.evidence_kind for m in measurements}
    if len(kinds) > 1:
        # Not a failure: mixing is legitimate and common. It has to be visible.
        report.add(Finding(
            "series_one_origin", Verdict.UNCERTAIN,
            f"The series mixes {', '.join(sorted(k.value for k in kinds))}. That is "
            "allowed, but any result drawn from it describes the mixture.",
            detail={"kinds": sorted(k.value for k in kinds)}))
    else:
        report.add(Finding("series_one_origin", Verdict.PASS,
                           f"All readings are {next(iter(kinds)).value}"))

    timestamps = [m.timestamp for m in measurements]
    if len(set(timestamps)) != len(timestamps):
        report.add(Finding(
            "series_timestamps_distinct", Verdict.UNCERTAIN,
            "Two or more readings share a timestamp. That is fine for simultaneous "
            "quantities and a problem for a time series; this check cannot tell which."))
    else:
        report.add(Finding("series_timestamps_distinct", Verdict.PASS,
                           "Every reading has a distinct timestamp"))
    return report


# Evidence and claims ---------------------------------------------------------
def validate_evidence(evidence: Evidence) -> ValidationReport:
    report = ValidationReport()

    if evidence.value is not None and evidence.unit:
        try:
            parse_unit(evidence.unit)
            report.add(Finding("unit_parses", Verdict.PASS,
                               f"{evidence.unit!r} parses", evidence.id))
        except UnitError as exc:
            report.add(Finding("unit_parses", Verdict.FAIL, str(exc), evidence.id))

    if evidence.kind.is_empirical and not evidence.measurement_ids:
        report.add(Finding(
            "empirical_cites_readings", Verdict.UNCERTAIN,
            f"Recorded as {evidence.kind.value} but cites no measurements. It may be a "
            "qualitative observation, or a reading may have gone unrecorded.",
            evidence.id))
    elif evidence.kind.is_empirical:
        report.add(Finding("empirical_cites_readings", Verdict.PASS,
                           f"Cites {len(evidence.measurement_ids)} measurements",
                           evidence.id))

    if evidence.kind is EvidenceKind.CALCULATION and not evidence.provenance.method:
        report.add(Finding(
            "calculation_states_method", Verdict.FAIL,
            "A calculation with no method recorded cannot be checked or reproduced",
            evidence.id))
    elif evidence.kind is EvidenceKind.CALCULATION:
        report.add(Finding("calculation_states_method", Verdict.PASS,
                           f"Method recorded: {evidence.provenance.method}", evidence.id))

    if evidence.provenance.is_ai_generated:
        report.add(Finding(
            "ai_is_labelled", Verdict.PASS,
            f"AI-generated and labelled as {evidence.kind.value}. This check confirms "
            "the label, not the content - no part of this system evaluates whether a "
            "model's reading is correct.",
            evidence.id))
    return report


def validate_claim(claim: Claim, resolve_evidence) -> ValidationReport:
    """Check a claim against the evidence it cites.

    `resolve_evidence` takes an id and returns Evidence or None - normally
    `store.get_evidence`.
    """
    report = ValidationReport()

    if not claim.evidence_ids:
        report.add(Finding("claim_cites_evidence", Verdict.FAIL,
                           "The claim cites no evidence at all", claim.id))
        return report

    resolved: list[Evidence] = []
    missing: list[str] = []
    for evidence_id in claim.evidence_ids:
        found = resolve_evidence(evidence_id)
        (resolved if found is not None else missing).append(found or evidence_id)

    if missing:
        report.add(Finding(
            "claim_cites_evidence", Verdict.FAIL,
            f"Cites {len(missing)} evidence records that do not exist: "
            f"{', '.join(str(m) for m in missing[:5])}",
            claim.id, {"missing": [str(m) for m in missing]}))
    else:
        report.add(Finding("claim_cites_evidence", Verdict.PASS,
                           f"All {len(resolved)} cited records resolve", claim.id))

    kinds = {e.kind for e in resolved}
    if kinds and all(k.is_interpretation for k in kinds):
        report.add(Finding(
            "claim_rests_on_more_than_interpretation", Verdict.UNCERTAIN,
            "Every cited record is an interpretation. The claim may still be right, "
            "but nothing in the record connects it to an observation.",
            claim.id, {"kinds": sorted(k.value for k in kinds)}))
    elif kinds:
        report.add(Finding(
            "claim_rests_on_more_than_interpretation", Verdict.PASS,
            f"Cites {', '.join(sorted(k.value for k in kinds))}", claim.id))
    return report


def validate_hypothesis(hypothesis: Hypothesis) -> ValidationReport:
    report = ValidationReport()
    overlap = set(hypothesis.supporting_evidence) & set(hypothesis.contradicting_evidence)
    if overlap:
        report.add(Finding(
            "evidence_not_double_counted", Verdict.FAIL,
            f"{len(overlap)} records are listed as both supporting and contradicting",
            hypothesis.id, {"overlap": sorted(overlap)}))
    else:
        report.add(Finding("evidence_not_double_counted", Verdict.PASS,
                           "No record is counted both ways", hypothesis.id))

    if not (hypothesis.supporting_evidence or hypothesis.contradicting_evidence):
        report.add(Finding(
            "hypothesis_has_evidence", Verdict.UNCERTAIN,
            "No evidence linked yet. The hypothesis is proposed but untested.",
            hypothesis.id))
    else:
        report.add(Finding(
            "hypothesis_has_evidence", Verdict.PASS,
            f"{len(hypothesis.supporting_evidence)} supporting, "
            f"{len(hypothesis.contradicting_evidence)} contradicting",
            hypothesis.id))
    return report


# Relations -------------------------------------------------------------------
def check_relation_dimensions(relation_key: str) -> Finding:
    """Confirm a registered relation is dimensionally consistent.

    Checks the dimensions of the quantities a relation connects - not the
    algebra. An equation can be dimensionally sound and still wrong.
    """
    from physics.concepts import RELATIONS, concept as get_concept

    relation = RELATIONS.get(relation_key)
    if relation is None:
        return Finding("relation_known", Verdict.UNCERTAIN,
                       f"No relation {relation_key!r} in the registry", relation_key)
    try:
        result = get_concept(relation.result)
        inputs = [get_concept(key) for key in relation.inputs]
    except ConceptError as exc:
        return Finding("relation_concepts_known", Verdict.FAIL, str(exc), relation_key)

    return Finding(
        "relation_registered", Verdict.PASS,
        f"{relation.equation} relates {', '.join(c.name for c in inputs)} to "
        f"{result.name}. Holds when: {'; '.join(relation.conditions)}",
        relation_key,
        {"equation": relation.equation, "conditions": list(relation.conditions),
         "note": "Dimensional and structural check only. Not a check that the "
                 "equation applies to your situation."},
    )


def validate_experiment(
    experiment: Experiment,
    *,
    measurements: Sequence[Measurement] = (),
    evidence: Sequence[Evidence] = (),
    claims: Sequence[Claim] = (),
    hypotheses: Sequence[Hypothesis] = (),
    resolve_evidence=None,
) -> ValidationReport:
    """Run every applicable check over a whole experiment record."""
    report = ValidationReport()

    if not experiment.research_question.strip():
        report.add(Finding("experiment_states_a_question", Verdict.UNCERTAIN,
                           "No research question recorded", experiment.id))
    else:
        report.add(Finding("experiment_states_a_question", Verdict.PASS,
                           "A research question is recorded", experiment.id))

    for measurement in measurements:
        report.findings.extend(validate_measurement(measurement).findings)
    if measurements:
        by_quantity: dict[str, list[Measurement]] = {}
        for measurement in measurements:
            by_quantity.setdefault(measurement.quantity, []).append(measurement)
        for series in by_quantity.values():
            report.findings.extend(validate_series(series).findings)
    for item in evidence:
        report.findings.extend(validate_evidence(item).findings)
    for hypothesis in hypotheses:
        report.findings.extend(validate_hypothesis(hypothesis).findings)
    if resolve_evidence is not None:
        for claim in claims:
            report.findings.extend(validate_claim(claim, resolve_evidence).findings)
    elif claims:
        report.add(Finding(
            "claims_checked", Verdict.UNCERTAIN,
            f"{len(claims)} claims were not checked: no way to resolve evidence ids "
            "was provided"))
    return report
