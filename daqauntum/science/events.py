"""Typed scientific events on DaQauntum's existing bus.

Why these need their own constructors
-------------------------------------
The v0.4 bus deliberately suppresses repeats: identical readings from a noisy
sensor collapse into one stored event with a repeat counter. That is right for
telemetry and **wrong for science**. Two measurements of 1.0 m are two facts,
not one fact seen twice, and an analysis run on deduplicated data is an analysis
of data that was silently altered.

So every scientific event carries a dedupe key unique to the object it reports.
Suppression can therefore never discard a measurement, while the bus keeps its
useful behaviour for everything else. `evaluations/science_smoke_test.py`
asserts that N identical measurements produce N events.
"""
from __future__ import annotations

import uuid
from typing import Any

from events.models import Event
from science.models import Claim, Evidence, Experiment, Hypothesis, Measurement

# Scientific events share one source so a consumer can subscribe to the whole
# scientific workflow without enumerating every kind.
SOURCE = "science"


class EventKind:
    """The scientific vocabulary. Strings, so subscribers need no import."""

    EXPERIMENT_CREATED = "experiment.created"
    EXPERIMENT_STARTED = "experiment.started"
    EXPERIMENT_COMPLETED = "experiment.completed"

    HYPOTHESIS_CREATED = "hypothesis.created"
    HYPOTHESIS_UPDATED = "hypothesis.updated"

    MEASUREMENT_RECORDED = "measurement.recorded"

    ANALYSIS_REQUESTED = "analysis.requested"
    ANALYSIS_COMPLETED = "analysis.completed"
    ANALYSIS_FAILED = "analysis.failed"

    SIMULATION_REQUESTED = "simulation.requested"
    SIMULATION_COMPLETED = "simulation.completed"
    SIMULATION_FAILED = "simulation.failed"

    EVIDENCE_CREATED = "evidence.created"

    CLAIM_CREATED = "claim.created"
    CLAIM_UPDATED = "claim.updated"

    RESEARCH_REQUESTED = "research.requested"
    RESEARCH_COMPLETED = "research.completed"
    RESEARCH_FAILED = "research.failed"

    VISUALIZATION_REQUESTED = "visualization.requested"
    VISUALIZATION_COMPLETED = "visualization.completed"

    ALL = (
        EXPERIMENT_CREATED, EXPERIMENT_STARTED, EXPERIMENT_COMPLETED,
        HYPOTHESIS_CREATED, HYPOTHESIS_UPDATED,
        MEASUREMENT_RECORDED,
        ANALYSIS_REQUESTED, ANALYSIS_COMPLETED, ANALYSIS_FAILED,
        SIMULATION_REQUESTED, SIMULATION_COMPLETED, SIMULATION_FAILED,
        EVIDENCE_CREATED,
        CLAIM_CREATED, CLAIM_UPDATED,
        RESEARCH_REQUESTED, RESEARCH_COMPLETED, RESEARCH_FAILED,
        VISUALIZATION_REQUESTED, VISUALIZATION_COMPLETED,
    )


def new_correlation_id() -> str:
    """An id tying one workflow's events together."""
    return f"run_{uuid.uuid4().hex[:12]}"


def _event(
    kind: str,
    subject: str,
    *,
    message: str,
    attributes: dict[str, Any],
    unique: str,
    correlation_id: str | None = None,
    severity: str = "info",
    occurred_at: float | None = None,
) -> Event:
    """Build a scientific event whose dedupe key is unique to its object.

    `unique` is the id of the thing being reported. Including it means the bus
    can never treat two distinct scientific records as the same observation.
    """
    payload: dict[str, Any] = {"scientific": True, **attributes}
    return Event(
        source=SOURCE,
        kind=kind,
        subject=subject or "experiment",
        severity=severity,
        message=message,
        attributes=payload,
        dedupe_key=f"{SOURCE}:{kind}:{unique}",
        correlation_id=correlation_id,
        **({"occurred_at": occurred_at} if occurred_at is not None else {}),
    )


def experiment_created(experiment: Experiment, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.EXPERIMENT_CREATED, experiment.id,
        message=f"Experiment created: {experiment.title}",
        attributes={
            "experiment_id": experiment.id,
            "title": experiment.title,
            "research_question": experiment.research_question,
            "depth": experiment.depth.value,
        },
        unique=experiment.id, correlation_id=correlation_id,
    )


def experiment_status(experiment: Experiment, kind: str, correlation_id: str | None = None) -> Event:
    return _event(
        kind, experiment.id,
        message=f"Experiment {experiment.status.value}: {experiment.title}",
        attributes={"experiment_id": experiment.id, "status": experiment.status.value},
        # Status transitions are distinct events, so the status joins the key.
        unique=f"{experiment.id}:{experiment.status.value}", correlation_id=correlation_id,
    )


def hypothesis_created(hypothesis: Hypothesis, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.HYPOTHESIS_CREATED, hypothesis.id,
        message=f"Hypothesis: {hypothesis.statement}",
        attributes={
            "hypothesis_id": hypothesis.id,
            "experiment_id": hypothesis.experiment_id,
            "statement": hypothesis.statement,
            "status": hypothesis.status.value,
        },
        unique=hypothesis.id, correlation_id=correlation_id,
    )


def hypothesis_updated(hypothesis: Hypothesis, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.HYPOTHESIS_UPDATED, hypothesis.id,
        message=f"Hypothesis is now {hypothesis.status.value}: {hypothesis.statement}",
        attributes={
            "hypothesis_id": hypothesis.id,
            "experiment_id": hypothesis.experiment_id,
            "status": hypothesis.status.value,
            "supporting": len(hypothesis.supporting_evidence),
            "contradicting": len(hypothesis.contradicting_evidence),
        },
        unique=f"{hypothesis.id}:{hypothesis.updated_at}", correlation_id=correlation_id,
    )


def measurement_recorded(measurement: Measurement, correlation_id: str | None = None) -> Event:
    """One measurement, one event - always.

    The dedupe key is the measurement id, which is unique per reading, so two
    identical values can never collapse into one recorded observation.
    """
    return _event(
        EventKind.MEASUREMENT_RECORDED, measurement.quantity,
        message=f"{measurement.quantity} = {measurement.as_quantity.format()}",
        attributes={
            "measurement_id": measurement.id,
            "experiment_id": measurement.experiment_id,
            "run_id": measurement.run_id,
            "quantity": measurement.quantity,
            "value": measurement.value,
            "unit": measurement.unit,
            "uncertainty": measurement.uncertainty,
            "derived": measurement.derived,
            "source": measurement.source,
            "evidence_kind": measurement.evidence_kind.value,
        },
        unique=measurement.id, correlation_id=correlation_id,
        occurred_at=measurement.timestamp,
    )


def evidence_created(evidence: Evidence, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.EVIDENCE_CREATED, evidence.kind.value,
        message=f"[{evidence.kind.value}] {evidence.statement}",
        attributes={
            "evidence_id": evidence.id,
            "experiment_id": evidence.experiment_id,
            # Carried explicitly: a subscriber must be able to tell a
            # measurement from an AI interpretation without fetching the record.
            "evidence_kind": evidence.kind.value,
            "empirical": evidence.is_empirical,
            "quantity": evidence.quantity,
            "value": evidence.value,
            "unit": evidence.unit,
            "provenance": evidence.provenance.kind.value,
        },
        unique=evidence.id, correlation_id=correlation_id,
    )


def claim_created(claim: Claim, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.CLAIM_CREATED, claim.id,
        message=f"Claim: {claim.statement}",
        attributes={
            "claim_id": claim.id,
            "experiment_id": claim.experiment_id,
            "status": claim.status.value,
            "evidence_count": len(claim.evidence_ids),
            "author": claim.author,
        },
        unique=claim.id, correlation_id=correlation_id,
    )


def claim_updated(claim: Claim, correlation_id: str | None = None) -> Event:
    return _event(
        EventKind.CLAIM_UPDATED, claim.id,
        message=f"Claim is now {claim.status.value}: {claim.statement}",
        attributes={"claim_id": claim.id, "experiment_id": claim.experiment_id,
                    "status": claim.status.value},
        unique=f"{claim.id}:{claim.status.value}", correlation_id=correlation_id,
    )


def job(kind: str, subject: str, *, experiment_id: str = "", correlation_id: str | None = None,
        detail: str = "", severity: str = "info", **attributes: Any) -> Event:
    """Generic lifecycle event for analysis, simulation, research, visualization.

    The correlation id doubles as the uniqueness key so a requested/completed
    pair stays two events while repeated runs stay distinct.
    """
    marker = correlation_id or new_correlation_id()
    return _event(
        kind, subject,
        message=detail or f"{kind} ({subject})",
        attributes={"experiment_id": experiment_id, **attributes},
        unique=f"{marker}:{kind}:{subject}", correlation_id=marker, severity=severity,
    )
