"""Typed scientific domain objects for DaQauntum.

The defining idea of this layer is that different kinds of knowledge stay
distinguishable. A number someone measured, a number derived from it, a number
a simulation predicted, a sentence from a paper, and a sentence an AI wrote are
all useful - and they are not interchangeable. Collapsing them is how a system
ends up quietly presenting a model's guess as an observation.

So `EvidenceKind` is not decoration. It is the field that keeps that
distinction alive through analysis, storage and presentation, and nothing in
this module lets a value change kind on its way through.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from science.units import Quantity, UnitError, parse_unit


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def _time(data: dict[str, Any], key: str) -> float:
    """Read a stored time, falling back to now() only when it is absent.

    Deliberately not `data.get(key) or now()`. A measurement taken at t = 0.0
    is the first sample of almost every experiment, and 0.0 is falsy, so that
    idiom replaced the start of each run with the wall clock. The series then
    re-sorted with its first sample last and the displacement came back
    negative - a wrong answer with nothing about it that looked wrong.
    """
    value = data.get(key)
    if value is None:
        return now()
    try:
        return float(value)
    except (TypeError, ValueError):
        return now()


class EvidenceKind(str, Enum):
    """Where a piece of knowledge came from.

    Ordered loosely from most directly tied to the physical world to most
    interpretive. That ordering is not a ranking of importance - an AI
    interpretation can be the most valuable thing in an experiment - but it is
    a reminder that these are different epistemic categories.
    """

    MEASUREMENT = "measurement"            # a sensor or a person read an instrument
    OBSERVATION = "observation"            # a qualitative human observation
    CALCULATION = "calculation"            # derived deterministically from other evidence
    SIMULATION = "simulation"              # produced by a model, not by the world
    LITERATURE = "literature"              # sourced from published work
    AI_INTERPRETATION = "ai_interpretation"        # a language model's reading
    HUMAN_INTERPRETATION = "human_interpretation"  # the investigator's reading

    @property
    def is_empirical(self) -> bool:
        """Whether this came from the world rather than from reasoning about it."""
        return self in {EvidenceKind.MEASUREMENT, EvidenceKind.OBSERVATION}

    @property
    def is_interpretation(self) -> bool:
        return self in {EvidenceKind.AI_INTERPRETATION, EvidenceKind.HUMAN_INTERPRETATION}


class ProvenanceKind(str, Enum):
    """How an object came to exist."""

    HUMAN = "human"
    SENSOR = "sensor"
    CALCULATED = "calculated"
    SIMULATED = "simulated"
    AI = "ai"
    IMPORTED = "imported"


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class HypothesisStatus(str, Enum):
    """Deliberately not 'proven'.

    A hypothesis accumulates support or contradiction; it does not graduate to
    truth. The vocabulary here is chosen so the UI cannot casually say a
    hypothesis was proved.
    """

    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INCONCLUSIVE = "inconclusive"
    WITHDRAWN = "withdrawn"


class ClaimStatus(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class Depth(str, Enum):
    """Explanation depth, for the education layer to build on later.

    v0.5 only carries the field; no curriculum content is encoded yet.
    """

    INTRODUCTORY = "introductory"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    RESEARCH = "research"


@dataclass
class Provenance:
    """Who or what produced something, when, and from what.

    Kept small on purpose. The value is in it being present on every object and
    always answering the same four questions, not in modelling every possible
    detail of a workflow.
    """

    kind: ProvenanceKind
    agent: str = ""                       # person, sensor id, model name, or tool
    inputs: list[str] = field(default_factory=list)   # ids of objects this derives from
    method: str = ""                      # equation, procedure or adapter used
    created_at: float = field(default_factory=now)
    notes: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProvenanceKind):
            self.kind = ProvenanceKind(str(self.kind))

    @property
    def is_ai_generated(self) -> bool:
        return self.kind is ProvenanceKind.AI

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "agent": self.agent,
            "inputs": list(self.inputs),
            "method": self.method,
            "created_at": self.created_at,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Provenance":
        return cls(
            kind=ProvenanceKind(data.get("kind", "human")),
            agent=data.get("agent", ""),
            inputs=list(data.get("inputs") or []),
            method=data.get("method", ""),
            created_at=_time(data, "created_at"),
            notes=data.get("notes", ""),
        )

    @classmethod
    def human(cls, agent: str = "user", **kwargs: Any) -> "Provenance":
        return cls(ProvenanceKind.HUMAN, agent, **kwargs)

    @classmethod
    def sensor(cls, agent: str, **kwargs: Any) -> "Provenance":
        return cls(ProvenanceKind.SENSOR, agent, **kwargs)

    @classmethod
    def calculated(cls, method: str, inputs: list[str], agent: str = "daqauntum", **kwargs: Any) -> "Provenance":
        return cls(ProvenanceKind.CALCULATED, agent, inputs=inputs, method=method, **kwargs)


@dataclass
class Measurement:
    """One reading of one quantity.

    `derived` separates a raw reading from a computed one. Both are legitimate
    measurements of the experiment's state, but only one of them was read off
    an instrument, and an analysis that cannot tell them apart will happily
    compute a velocity from velocities.
    """

    quantity: str
    value: float
    unit: str = ""
    uncertainty: float | None = None
    experiment_id: str = ""
    run_id: str = ""
    source: str = ""
    derived: bool = False
    timestamp: float = field(default_factory=now)
    id: str = field(default_factory=lambda: new_id("meas"))
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceKind.HUMAN))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.quantity = str(self.quantity or "").strip()
        if not self.quantity:
            raise ValueError("A measurement must name the quantity it measures")
        # Validate the unit at construction, so an unparseable unit cannot sit
        # in the database waiting to fail during analysis.
        parse_unit(self.unit)
        if self.uncertainty is not None and float(self.uncertainty) < 0:
            raise UnitError("Measurement uncertainty cannot be negative")

    @property
    def as_quantity(self) -> Quantity:
        return Quantity.of(self.value, self.unit, self.uncertainty)

    @property
    def is_simulated(self) -> bool:
        """Whether this value came from a model rather than from the world."""
        return self.provenance.kind is ProvenanceKind.SIMULATED

    @property
    def is_reading(self) -> bool:
        """Whether somebody actually read this value off an instrument.

        The single test for "did this come from the world". Everything else -
        calculated, simulated, imported from a dataset or a textbook - is a
        value this investigation did not measure, and callers that need to say
        so should ask this rather than checking one origin at a time and
        missing the others.
        """
        return not self.derived and self.provenance.kind in {
            ProvenanceKind.HUMAN, ProvenanceKind.SENSOR,
        }

    @property
    def evidence_kind(self) -> EvidenceKind:
        """What kind of knowledge this value actually is.

        Read from what the record already says rather than stored separately,
        so the two can never disagree. A derived value is a calculation; a
        value a simulator produced is a simulation however realistic it looks;
        only a reading of the world is a measurement.
        """
        if self.derived:
            return EvidenceKind.CALCULATION
        if self.is_simulated:
            return EvidenceKind.SIMULATION
        if self.provenance.kind is ProvenanceKind.IMPORTED:
            # A value that arrived from somewhere else - a textbook, a paper, a
            # dataset - is not a reading this investigation took, however
            # trustworthy its source. LITERATURE keeps that visible.
            return EvidenceKind.LITERATURE
        return EvidenceKind.MEASUREMENT

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "quantity": self.quantity,
            "value": self.value,
            "unit": self.unit,
            "uncertainty": self.uncertainty,
            "experiment_id": self.experiment_id,
            "run_id": self.run_id,
            "source": self.source,
            "derived": self.derived,
            "timestamp": self.timestamp,
            "provenance": self.provenance.as_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Measurement":
        return cls(
            quantity=data["quantity"],
            value=float(data["value"]),
            unit=data.get("unit", ""),
            uncertainty=data.get("uncertainty"),
            experiment_id=data.get("experiment_id", ""),
            run_id=data.get("run_id", ""),
            source=data.get("source", ""),
            derived=bool(data.get("derived", False)),
            timestamp=_time(data, "timestamp"),
            id=data.get("id") or new_id("meas"),
            provenance=Provenance.from_dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Evidence:
    """A piece of knowledge, tagged with what kind of knowledge it is.

    `kind` is required and has no default. An untyped piece of evidence would
    defeat the purpose of the class: the whole point is that a reader can tell
    a measurement from an inference without having to trace its history.
    """

    kind: EvidenceKind
    statement: str
    experiment_id: str = ""
    quantity: str = ""
    value: float | None = None
    unit: str = ""
    uncertainty: float | None = None
    measurement_ids: list[str] = field(default_factory=list)
    artifact_path: str = ""
    id: str = field(default_factory=lambda: new_id("ev"))
    created_at: float = field(default_factory=now)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceKind.HUMAN))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            self.kind = EvidenceKind(str(self.kind))
        self.statement = str(self.statement or "").strip()
        if not self.statement:
            raise ValueError("Evidence must state something")
        if self.unit:
            parse_unit(self.unit)

    @property
    def is_empirical(self) -> bool:
        return self.kind.is_empirical

    @property
    def quantity_value(self) -> Quantity | None:
        if self.value is None:
            return None
        return Quantity.of(self.value, self.unit, self.uncertainty)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "statement": self.statement,
            "experiment_id": self.experiment_id,
            "quantity": self.quantity,
            "value": self.value,
            "unit": self.unit,
            "uncertainty": self.uncertainty,
            "measurement_ids": list(self.measurement_ids),
            "artifact_path": self.artifact_path,
            "created_at": self.created_at,
            "provenance": self.provenance.as_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
        return cls(
            kind=EvidenceKind(data["kind"]),
            statement=data["statement"],
            experiment_id=data.get("experiment_id", ""),
            quantity=data.get("quantity", ""),
            value=data.get("value"),
            unit=data.get("unit", ""),
            uncertainty=data.get("uncertainty"),
            measurement_ids=list(data.get("measurement_ids") or []),
            artifact_path=data.get("artifact_path", ""),
            id=data.get("id") or new_id("ev"),
            created_at=_time(data, "created_at"),
            provenance=Provenance.from_dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Hypothesis:
    """A testable statement, with the evidence for and against kept apart."""

    statement: str
    experiment_id: str = ""
    rationale: str = ""
    expected_relationship: str = ""
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("hyp"))
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceKind.HUMAN))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, HypothesisStatus):
            self.status = HypothesisStatus(str(self.status))
        self.statement = str(self.statement or "").strip()
        if not self.statement:
            raise ValueError("A hypothesis must state something testable")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "experiment_id": self.experiment_id,
            "rationale": self.rationale,
            "expected_relationship": self.expected_relationship,
            "status": self.status.value,
            "supporting_evidence": list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": self.provenance.as_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Hypothesis":
        return cls(
            statement=data["statement"],
            experiment_id=data.get("experiment_id", ""),
            rationale=data.get("rationale", ""),
            expected_relationship=data.get("expected_relationship", ""),
            status=HypothesisStatus(data.get("status", "proposed")),
            supporting_evidence=list(data.get("supporting_evidence") or []),
            contradicting_evidence=list(data.get("contradicting_evidence") or []),
            id=data.get("id") or new_id("hyp"),
            created_at=_time(data, "created_at"),
            updated_at=_time(data, "updated_at"),
            provenance=Provenance.from_dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Claim:
    """A statement someone is willing to stand behind, and its evidence.

    There is deliberately no automated truth score. Status is set by a person
    or left unresolved; DaQauntum's job is to make the supporting evidence
    visible, not to grade the claim.
    """

    statement: str
    experiment_id: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    hypothesis_id: str = ""
    status: ClaimStatus = ClaimStatus.PROPOSED
    author: str = "user"
    id: str = field(default_factory=lambda: new_id("claim"))
    created_at: float = field(default_factory=now)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceKind.HUMAN))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ClaimStatus):
            self.status = ClaimStatus(str(self.status))
        self.statement = str(self.statement or "").strip()
        if not self.statement:
            raise ValueError("A claim must state something")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "experiment_id": self.experiment_id,
            "evidence_ids": list(self.evidence_ids),
            "hypothesis_id": self.hypothesis_id,
            "status": self.status.value,
            "author": self.author,
            "created_at": self.created_at,
            "provenance": self.provenance.as_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Claim":
        return cls(
            statement=data["statement"],
            experiment_id=data.get("experiment_id", ""),
            evidence_ids=list(data.get("evidence_ids") or []),
            hypothesis_id=data.get("hypothesis_id", ""),
            status=ClaimStatus(data.get("status", "proposed")),
            author=data.get("author", "user"),
            id=data.get("id") or new_id("claim"),
            created_at=_time(data, "created_at"),
            provenance=Provenance.from_dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Experiment:
    """The container for one investigation.

    Holds identity and design; the measurements, evidence and claims that
    accumulate under it live in the store and are fetched by id, so an
    experiment object stays small however much data it gathers.
    """

    title: str
    research_question: str = ""
    description: str = ""
    status: ExperimentStatus = ExperimentStatus.DRAFT
    depth: Depth = Depth.INTERMEDIATE
    independent_variables: list[str] = field(default_factory=list)
    dependent_variables: list[str] = field(default_factory=list)
    controlled_variables: list[str] = field(default_factory=list)
    apparatus: list[str] = field(default_factory=list)
    procedure: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("exp"))
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    provenance: Provenance = field(default_factory=lambda: Provenance(ProvenanceKind.HUMAN))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExperimentStatus):
            self.status = ExperimentStatus(str(self.status))
        if not isinstance(self.depth, Depth):
            self.depth = Depth(str(self.depth))
        self.title = str(self.title or "").strip()
        if not self.title:
            raise ValueError("An experiment needs a title")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "research_question": self.research_question,
            "description": self.description,
            "status": self.status.value,
            "depth": self.depth.value,
            "independent_variables": list(self.independent_variables),
            "dependent_variables": list(self.dependent_variables),
            "controlled_variables": list(self.controlled_variables),
            "apparatus": list(self.apparatus),
            "procedure": list(self.procedure),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": self.provenance.as_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Experiment":
        return cls(
            title=data["title"],
            research_question=data.get("research_question", ""),
            description=data.get("description", ""),
            status=ExperimentStatus(data.get("status", "draft")),
            depth=Depth(data.get("depth", "intermediate")),
            independent_variables=list(data.get("independent_variables") or []),
            dependent_variables=list(data.get("dependent_variables") or []),
            controlled_variables=list(data.get("controlled_variables") or []),
            apparatus=list(data.get("apparatus") or []),
            procedure=list(data.get("procedure") or []),
            id=data.get("id") or new_id("exp"),
            created_at=_time(data, "created_at"),
            updated_at=_time(data, "updated_at"),
            provenance=Provenance.from_dict(data.get("provenance") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
