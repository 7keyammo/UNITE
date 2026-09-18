"""Adapters to things outside the scientific core: literature and simulators.

Two interfaces live here.

`ResearchBackend` answers "has anyone measured this before?". The local
implementation searches what the user has already recorded. The Open Science
adapter is deliberately not implemented - see `OpenScienceBackend` for exactly
what is missing and why guessing would be worse than a stub.

`SimulationBackend` produces values from a model. Everything it returns is
marked SIMULATED and enters the record through `ScienceCore.record_simulation`,
so a simulated trajectory can never be mistaken for a measured one no matter
how good the model is.

Neither kind of backend writes to the record. They return values; the core
decides what to store and how to label it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from science.models import Evidence, EvidenceKind, Provenance, ProvenanceKind
from science.units import Quantity, parse_unit


class BackendError(RuntimeError):
    """Raised when a backend cannot do what was asked."""


class BackendUnavailable(BackendError):
    """Raised when a backend exists but is not usable in this build."""


# Research --------------------------------------------------------------------
@dataclass
class ResearchResult:
    """One prior finding, with enough to judge whether it is relevant."""

    title: str
    summary: str
    source: str                       # where this came from, always populated
    identifier: str = ""              # DOI, record id, local evidence id
    url: str = ""
    quantity: str = ""
    value: float | None = None
    unit: str = ""
    uncertainty: float | None = None
    year: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.source).strip():
            raise BackendError("A research result must say where it came from")
        if self.unit:
            parse_unit(self.unit)

    @property
    def as_quantity(self) -> Quantity | None:
        if self.value is None:
            return None
        return Quantity.of(self.value, self.unit, self.uncertainty)

    def as_evidence(self, experiment_id: str = "",
                    kind: EvidenceKind = EvidenceKind.LITERATURE) -> Evidence:
        """Prior work is LITERATURE evidence, never MEASUREMENT.

        Someone else measured it. That is a different epistemic position from
        having measured it here, and the distinction is the reason this system
        exists.
        """
        return Evidence(
            kind=kind,
            statement=self.summary or self.title,
            experiment_id=experiment_id,
            quantity=self.quantity,
            value=self.value,
            unit=self.unit,
            uncertainty=self.uncertainty,
            provenance=Provenance(
                ProvenanceKind.IMPORTED,
                agent=self.source,
                notes=f"{self.title} ({self.identifier or self.url or 'no identifier'})",
            ),
            metadata={"title": self.title, "url": self.url, "year": self.year,
                      "identifier": self.identifier, **self.metadata},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title, "summary": self.summary, "source": self.source,
            "identifier": self.identifier, "url": self.url, "quantity": self.quantity,
            "value": self.value, "unit": self.unit, "uncertainty": self.uncertainty,
            "year": self.year, "metadata": dict(self.metadata),
        }


class ResearchBackend:
    """Interface for looking up prior work."""

    name: str = "research"

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError

    def search(self, query: str, *, limit: int = 10) -> list[ResearchResult]:
        raise NotImplementedError

    def require_available(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise BackendUnavailable(f"{self.name} is not available: {reason}")


class LocalResearchBackend(ResearchBackend):
    """Searches what this installation has already recorded.

    Deliberately literal: substring matching over statements, titles and
    quantity names, with no ranking model and no embedding. Two reasons.

    First, a local corpus is small enough that literal matching finds what is
    there. Second, and the real one: a similarity score is not evidence that
    two results are about the same thing, and a retrieval layer that implies
    otherwise is exactly the confusion between retrieval and proof this project
    refuses to make. A user reads the matches and decides.
    """

    name = "local"

    def __init__(self, store, *, include_kinds: Sequence[EvidenceKind] | None = None):
        self.store = store
        # Interpretations are searchable but off by default: a past model's
        # reading of a different experiment is a poor answer to "what is known".
        self.include_kinds = tuple(include_kinds) if include_kinds else (
            EvidenceKind.MEASUREMENT,
            EvidenceKind.OBSERVATION,
            EvidenceKind.CALCULATION,
            EvidenceKind.LITERATURE,
        )

    def available(self) -> tuple[bool, str]:
        return True, "Searches locally recorded evidence."

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope": "this installation only",
            "kinds": [k.value for k in self.include_kinds],
            "matching": "case-insensitive substring; no ranking model",
            "note": ("Finding a past result does not make it relevant or correct. "
                     "Matches are shown for a person to judge."),
        }

    def search(self, query: str, *, limit: int = 10) -> list[ResearchResult]:
        needle = str(query or "").strip().lower()
        if not needle:
            raise BackendError("Search needs a query")
        wanted = {k.value for k in self.include_kinds}
        results: list[ResearchResult] = []
        for row in self.store.conn.execute(
            "SELECT document_json FROM evidence ORDER BY created_at DESC LIMIT 2000"
        ):
            import json

            data = json.loads(row["document_json"])
            if data.get("kind") not in wanted:
                continue
            haystack = " ".join(str(data.get(field, "")) for field in
                                ("statement", "quantity", "unit")).lower()
            if needle not in haystack:
                continue
            results.append(ResearchResult(
                title=data.get("statement", "")[:120],
                summary=data.get("statement", ""),
                source=f"local:{data.get('kind', 'evidence')}",
                identifier=data.get("id", ""),
                quantity=data.get("quantity", ""),
                value=data.get("value"),
                unit=data.get("unit", ""),
                uncertainty=data.get("uncertainty"),
                metadata={"experiment_id": data.get("experiment_id", ""),
                          "evidence_kind": data.get("kind", "")},
            ))
            if len(results) >= limit:
                break
        return results


class OpenScienceBackend(ResearchBackend):
    """Not implemented. Deliberately.

    DaQauntum should be able to check a result against published work, and an
    open-science repository is the obvious source. This class is a placeholder
    with no network code in it, because the alternative - writing an adapter
    against an API whose real behaviour has not been verified - produces
    something that looks finished, fails in a way nobody anticipated, and, in a
    system whose whole job is distinguishing what is known from what is
    guessed, would have been built by guessing.

    What has to be established before this is written, none of which can be
    settled from inside this repository:

    1. Which service. "Open science" covers several with incompatible APIs:
       OpenAlex, Crossref, OSF, Zenodo, Semantic Scholar, arXiv. They differ in
       coverage, identifiers and licensing, and the choice is a product
       decision, not an implementation detail.
    2. The real request and response shape, read from that service's current
       documentation: endpoint, query grammar, pagination, and the exact JSON
       fields carrying title, authors, identifiers, dates and any numeric
       values.
    3. Authentication: whether a key or a contact header is required, what the
       anonymous rate limit is, and how the service signals throttling.
    4. Terms of use: what may be cached locally, for how long, and what
       attribution the licence requires - this system stores what it retrieves,
       so caching rules are a correctness requirement, not a nicety.
    5. Failure semantics: how an empty result is distinguished from an error,
       so a lookup that failed never reads as "nothing has been published".
    6. How a record maps onto ResearchResult, and what to do with the fields
       that do not map rather than silently dropping them.

    Until those are answered from the service's own documentation, `available()`
    returns False with that list, and `search()` raises. A caller gets an
    honest "not built" instead of an empty result set that reads like "no prior
    work exists".
    """

    name = "open-science"

    RESEARCH_NEEDED: tuple[str, ...] = (
        "Choose the service (OpenAlex / Crossref / OSF / Zenodo / arXiv) - a product decision.",
        "Verify endpoint, query grammar, pagination and response fields from its live docs.",
        "Establish authentication, rate limits and throttling signals.",
        "Read the terms of use for caching, retention and attribution.",
        "Define how an empty result differs from a failed request.",
        "Map its record shape onto ResearchResult, including fields that do not map.",
    )

    def available(self) -> tuple[bool, str]:
        return False, (
            "Not implemented: no external API behaviour has been verified. "
            f"Outstanding: {len(self.RESEARCH_NEEDED)} items - see OpenScienceBackend.RESEARCH_NEEDED."
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": "unimplemented",
            "implemented": False,
            "network_calls": False,
            "research_needed": list(self.RESEARCH_NEEDED),
            "note": ("A placeholder on purpose. An adapter written against an unverified "
                     "API would look finished and fail unpredictably."),
        }

    def search(self, query: str, *, limit: int = 10) -> list[ResearchResult]:
        raise BackendUnavailable(
            "OpenScienceBackend is not implemented. It returns no results rather than an "
            "empty list, so a failed lookup is never mistaken for 'nothing has been "
            "published'. Outstanding research: "
            + "; ".join(self.RESEARCH_NEEDED)
        )


# Simulation ------------------------------------------------------------------
@dataclass
class SimulationResult:
    """A model's output, with the model and its parameters attached."""

    quantity: str
    values: list[float]
    unit: str
    times: list[float] = field(default_factory=list)
    model: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.times and len(self.times) != len(self.values):
            raise BackendError("One time per value, or none")
        if not str(self.model).strip():
            raise BackendError("A simulation result must name its model")
        parse_unit(self.unit)

    def as_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity, "values": list(self.values), "unit": self.unit,
            "times": list(self.times), "model": self.model,
            "parameters": dict(self.parameters), "assumptions": list(self.assumptions),
        }


class SimulationBackend:
    """Interface for anything that computes a trajectory from a model."""

    name: str = "simulation"

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        raise NotImplementedError

    def run(self, **parameters: Any) -> SimulationResult:
        raise NotImplementedError

    def require_available(self) -> None:
        usable, reason = self.available()
        if not usable:
            raise BackendUnavailable(f"{self.name} is not available: {reason}")


class KinematicSimulation(SimulationBackend):
    """Constant-acceleration kinematics, x(t) = x0 + v0·t + ½·a·t².

    Every result lists the assumptions the model makes. A simulation that does
    not state what it ignored invites the reader to treat its output as the
    behaviour of the real system rather than of the model.
    """

    name = "kinematics.constant-acceleration"

    ASSUMPTIONS: tuple[str, ...] = (
        "Acceleration is constant over the whole interval.",
        "Motion is one-dimensional.",
        "No friction, drag or other resistive force is modelled.",
        "The object is treated as a point mass.",
    )

    def available(self) -> tuple[bool, str]:
        return True, "Closed-form; no solver required."

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "model": "x(t) = x0 + v0*t + 0.5*a*t^2",
            "outputs": ["position", "velocity"],
            "assumptions": list(self.ASSUMPTIONS),
            "note": "Output describes the model, not any physical object.",
        }

    def run(
        self,
        *,
        initial_position: float = 0.0,
        initial_velocity: float = 0.0,
        acceleration: float = 0.0,
        duration: float = 1.0,
        steps: int = 10,
        unit: str = "m",
        quantity: str = "position",
    ) -> SimulationResult:
        if steps < 2:
            raise BackendError("A trajectory needs at least two steps")
        if duration <= 0:
            raise BackendError("Duration must be positive")
        dt = duration / (steps - 1)
        times = [i * dt for i in range(steps)]
        if quantity == "velocity":
            values = [initial_velocity + acceleration * t for t in times]
            unit = f"{unit}/s"
        else:
            values = [initial_position + initial_velocity * t + 0.5 * acceleration * t * t
                      for t in times]
        return SimulationResult(
            quantity=quantity,
            values=values,
            unit=unit,
            times=times,
            model=self.name,
            parameters={"x0": initial_position, "v0": initial_velocity,
                        "a": acceleration, "duration_s": duration, "steps": steps},
            assumptions=list(self.ASSUMPTIONS),
        )
