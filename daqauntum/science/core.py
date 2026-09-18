"""ScienceCore: the composition root of the scientific layer.

Everything an investigation does passes through here - creating an experiment,
proposing a hypothesis, recording readings, running an analysis, turning a
result into evidence, making a claim. The facade exists so those steps happen
in one place with one set of rules, rather than each caller assembling models,
store writes and events for itself and getting a detail wrong.

The rules it enforces, all of which exist because the alternative is a record
that quietly overstates what is known:

* **A reading is recorded as a reading.** `record_measurement` refuses a
  measurement flagged derived; calculated values enter through
  `record_analysis`, which marks them and names their inputs.
* **An AI reading is labelled as one.** `interpret` always produces
  AI_INTERPRETATION evidence with AI provenance. The caller cannot choose a
  different kind, so a model's opinion cannot be filed as a measurement.
* **Evidence is linked, never scored.** Attaching evidence to a hypothesis
  records support or contradiction. It does not compute a truth value and it
  never sets a status by itself; a person decides what the evidence means.
* **Durable before announced.** Every mutation is persisted, then published.
  An event never reports a state the database does not hold.

The core does not talk to models, sensors, simulators or research platforms.
Those arrive through adapters the caller passes in.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from science import events as science_events
from science.analysis import AnalysisResult, MotionAnalysis, analyze_motion
from science.models import (
    Claim,
    ClaimStatus,
    Depth,
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


class ScienceError(ValueError):
    """Raised when an operation would record something misleading."""


class ScienceCore:
    """Experiment lifecycle, persistence and events in one place."""

    def __init__(self, memory, *, events=None, store: ScienceStore | None = None):
        self.memory = memory
        self.store = store or ScienceStore(memory)
        # Optional: the core is fully usable with no bus attached, which keeps
        # tests and scripts from needing the whole event pipeline.
        self.events = events

    # Event plumbing ----------------------------------------------------------
    def _publish(self, event) -> None:
        if self.events is None:
            return
        try:
            self.events.publish(event)
        except Exception as exc:  # pragma: no cover - defensive
            # A failed announcement must not roll back a stored fact, and must
            # not be swallowed silently either.
            self.memory.add_event("science_event_failed", {
                "kind": getattr(event, "kind", ""), "error": str(exc),
            })

    @staticmethod
    def new_correlation_id() -> str:
        """Group the events of one workflow so they can be replayed together."""
        return science_events.new_correlation_id()

    # Experiments -------------------------------------------------------------
    def create_experiment(
        self,
        title: str,
        *,
        research_question: str = "",
        description: str = "",
        depth: Depth | str = Depth.INTERMEDIATE,
        independent_variables: Sequence[str] = (),
        dependent_variables: Sequence[str] = (),
        controlled_variables: Sequence[str] = (),
        apparatus: Sequence[str] = (),
        procedure: Sequence[str] = (),
        provenance: Provenance | None = None,
        correlation_id: str | None = None,
        **extra: Any,
    ) -> Experiment:
        experiment = Experiment(
            title=title,
            research_question=research_question,
            description=description,
            depth=Depth(depth) if not isinstance(depth, Depth) else depth,
            independent_variables=list(independent_variables),
            dependent_variables=list(dependent_variables),
            controlled_variables=list(controlled_variables),
            apparatus=list(apparatus),
            procedure=list(procedure),
            provenance=provenance or Provenance.human(),
            **extra,
        )
        self.store.save_experiment(experiment)
        self._publish(science_events.experiment_created(experiment, correlation_id))
        return experiment

    def set_status(
        self,
        experiment_id: str,
        status: ExperimentStatus | str,
        *,
        correlation_id: str | None = None,
    ) -> Experiment:
        status = ExperimentStatus(status) if not isinstance(status, ExperimentStatus) else status
        experiment = self.store.set_experiment_status(experiment_id, status)
        if experiment is None:
            raise ScienceError(f"No experiment {experiment_id!r}")
        kind = {
            ExperimentStatus.ACTIVE: science_events.EventKind.EXPERIMENT_STARTED,
            ExperimentStatus.COMPLETED: science_events.EventKind.EXPERIMENT_COMPLETED,
        }.get(status)
        if kind:
            self._publish(science_events.experiment_status(experiment, kind, correlation_id))
        return experiment

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        return self.store.get_experiment(experiment_id)

    def list_experiments(self, **kwargs: Any) -> list[Experiment]:
        return self.store.list_experiments(**kwargs)

    # Hypotheses --------------------------------------------------------------
    def propose_hypothesis(
        self,
        experiment_id: str,
        statement: str,
        *,
        rationale: str = "",
        expected_relationship: str = "",
        provenance: Provenance | None = None,
        correlation_id: str | None = None,
    ) -> Hypothesis:
        hypothesis = Hypothesis(
            statement=statement,
            experiment_id=experiment_id,
            rationale=rationale,
            expected_relationship=expected_relationship,
            provenance=provenance or Provenance.human(),
        )
        self.store.save_hypothesis(hypothesis)
        self._publish(science_events.hypothesis_created(hypothesis, correlation_id))
        return hypothesis

    def link_evidence(
        self,
        hypothesis_id: str,
        evidence_id: str,
        *,
        supports: bool,
        correlation_id: str | None = None,
    ) -> Hypothesis:
        """Attach evidence to a hypothesis as supporting or contradicting.

        Linking does not change the hypothesis's status. Deciding that a body
        of evidence amounts to support is a judgment, and this layer does not
        make judgments - see `assess_hypothesis`, which requires a person.
        """
        hypothesis = self.store.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise ScienceError(f"No hypothesis {hypothesis_id!r}")
        if self.store.get_evidence(evidence_id) is None:
            raise ScienceError(f"No evidence {evidence_id!r} to link")
        target = hypothesis.supporting_evidence if supports else hypothesis.contradicting_evidence
        other = hypothesis.contradicting_evidence if supports else hypothesis.supporting_evidence
        if evidence_id in other:
            raise ScienceError(
                f"Evidence {evidence_id!r} is already linked to this hypothesis with the "
                "opposite sense. Unlink it first rather than recording both."
            )
        if evidence_id not in target:
            target.append(evidence_id)
        self.store.save_hypothesis(hypothesis)
        self._publish(science_events.hypothesis_updated(hypothesis, correlation_id))
        return hypothesis

    def assess_hypothesis(
        self,
        hypothesis_id: str,
        status: HypothesisStatus | str,
        *,
        assessor: str,
        reasoning: str = "",
        correlation_id: str | None = None,
    ) -> Hypothesis:
        """Record a person's assessment of where a hypothesis stands.

        `assessor` is required and recorded. The status vocabulary has no
        'proven' value, and this method will not invent one: a hypothesis
        accumulates support, it does not graduate to truth.
        """
        if not str(assessor).strip():
            raise ScienceError("An assessment must name who made it")
        hypothesis = self.store.get_hypothesis(hypothesis_id)
        if hypothesis is None:
            raise ScienceError(f"No hypothesis {hypothesis_id!r}")
        hypothesis.status = (
            HypothesisStatus(status) if not isinstance(status, HypothesisStatus) else status
        )
        hypothesis.metadata.setdefault("assessments", []).append({
            "assessor": assessor, "status": hypothesis.status.value, "reasoning": reasoning,
        })
        self.store.save_hypothesis(hypothesis)
        self._publish(science_events.hypothesis_updated(hypothesis, correlation_id))
        return hypothesis

    def list_hypotheses(self, experiment_id: str) -> list[Hypothesis]:
        return self.store.list_hypotheses(experiment_id)

    # Measurements ------------------------------------------------------------
    def record_measurement(
        self,
        experiment_id: str,
        quantity: str,
        value: float,
        unit: str = "",
        *,
        uncertainty: float | None = None,
        run_id: str = "",
        source: str = "",
        timestamp: float | None = None,
        provenance: Provenance | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> Measurement:
        """Record one reading of the world.

        Refuses anything already marked derived. A calculated value entering
        through this door would be indistinguishable from an instrument reading
        the moment it was stored.
        """
        measurement = Measurement(
            quantity=quantity,
            value=float(value),
            unit=unit,
            uncertainty=uncertainty,
            experiment_id=experiment_id,
            run_id=run_id,
            source=source,
            provenance=provenance or Provenance.human(),
            metadata=dict(metadata or {}),
            **({"timestamp": timestamp} if timestamp is not None else {}),
        )
        if measurement.derived:
            raise ScienceError("Use record_analysis for derived values")
        if measurement.provenance.kind in {ProvenanceKind.CALCULATED, ProvenanceKind.SIMULATED,
                                           ProvenanceKind.AI}:
            raise ScienceError(
                f"A reading cannot carry {measurement.provenance.kind.value} provenance. "
                "Calculated values go through record_analysis; simulated values through "
                "record_simulation."
            )
        self.store.save_measurement(measurement)
        self._publish(science_events.measurement_recorded(measurement, correlation_id))
        return measurement

    def record_series(
        self,
        experiment_id: str,
        quantity: str,
        values: Iterable[float],
        unit: str = "",
        *,
        uncertainty: float | None = None,
        timestamps: Sequence[float] | None = None,
        **kwargs: Any,
    ) -> list[Measurement]:
        """Record a run of readings, one measurement each.

        Identical values stay separate records and separate events. Collapsing
        repeats would change what the data says.
        """
        values = list(values)
        if timestamps is not None and len(timestamps) != len(values):
            raise ScienceError(
                f"Got {len(values)} values and {len(timestamps)} timestamps"
            )
        recorded = []
        for index, value in enumerate(values):
            recorded.append(self.record_measurement(
                experiment_id, quantity, value, unit,
                uncertainty=uncertainty,
                timestamp=timestamps[index] if timestamps is not None else None,
                **kwargs,
            ))
        return recorded

    def list_measurements(self, experiment_id: str, **kwargs: Any) -> list[Measurement]:
        return self.store.list_measurements(experiment_id, **kwargs)

    # Analysis ----------------------------------------------------------------
    def analyse_motion(
        self,
        experiment_id: str,
        *,
        time_quantity: str = "time",
        position_quantity: str = "position",
        run_id: str = "",
        correlation_id: str | None = None,
        store_results: bool = True,
    ) -> tuple[MotionAnalysis, list[Evidence]]:
        """Run the motion engine over an experiment's stored readings.

        Only raw readings are used. Feeding derived values back in would let a
        calculation compound on itself while still looking like data.
        """
        correlation_id = correlation_id or self.new_correlation_id()
        self._publish(science_events.job(
            science_events.EventKind.ANALYSIS_REQUESTED, "motion",
            experiment_id=experiment_id, correlation_id=correlation_id,
        ))
        times = self.store.list_measurements(
            experiment_id, quantity=time_quantity, run_id=run_id or None, include_derived=False
        )
        positions = self.store.list_measurements(
            experiment_id, quantity=position_quantity, run_id=run_id or None, include_derived=False
        )
        try:
            analysis = analyze_motion(times, positions, experiment_id=experiment_id)
        except Exception as exc:
            self._publish(science_events.job(
                science_events.EventKind.ANALYSIS_FAILED, "motion",
                experiment_id=experiment_id, correlation_id=correlation_id,
                severity="warning", error=str(exc),
            ))
            raise

        evidence: list[Evidence] = []
        if store_results:
            for result in (analysis.displacement, analysis.average_velocity,
                           analysis.average_acceleration):
                if result is not None:
                    evidence.append(self.record_analysis(
                        experiment_id, result, run_id=run_id, correlation_id=correlation_id,
                    ))
        self._publish(science_events.job(
            science_events.EventKind.ANALYSIS_COMPLETED, "motion",
            experiment_id=experiment_id, correlation_id=correlation_id,
            samples=analysis.samples,
            results=[r.name for r in analysis.results()],
            warnings=list(analysis.warnings),
        ))
        return analysis, evidence

    def record_analysis(
        self,
        experiment_id: str,
        result: AnalysisResult,
        *,
        run_id: str = "",
        correlation_id: str | None = None,
    ) -> Evidence:
        """Store a calculated result as both a derived measurement and evidence.

        Two records on purpose: the derived measurement makes the value
        queryable alongside the readings (flagged, never mixed in), and the
        evidence carries the equation and inputs for the argument.
        """
        derived = result.as_measurement(experiment_id, run_id=run_id)
        self.store.save_measurement(derived)
        self._publish(science_events.measurement_recorded(derived, correlation_id))

        evidence = result.as_evidence(experiment_id)
        evidence.metadata["derived_measurement_id"] = derived.id
        self.store.save_evidence(evidence)
        self._publish(science_events.evidence_created(evidence, correlation_id))
        return evidence

    # Evidence ----------------------------------------------------------------
    def record_evidence(
        self,
        experiment_id: str,
        kind: EvidenceKind | str,
        statement: str,
        *,
        provenance: Provenance,
        correlation_id: str | None = None,
        **fields: Any,
    ) -> Evidence:
        """Record evidence of an explicitly stated kind.

        `kind` and `provenance` are both required and neither defaults. The
        moment one of them has a default, the cheapest path through this
        function starts producing evidence whose origin is a guess.
        """
        evidence = Evidence(
            kind=EvidenceKind(kind) if not isinstance(kind, EvidenceKind) else kind,
            statement=statement,
            experiment_id=experiment_id,
            provenance=provenance,
            **fields,
        )
        self.store.save_evidence(evidence)
        self._publish(science_events.evidence_created(evidence, correlation_id))
        return evidence

    def observe(
        self,
        experiment_id: str,
        statement: str,
        *,
        observer: str = "user",
        correlation_id: str | None = None,
        **fields: Any,
    ) -> Evidence:
        """A qualitative human observation - 'the cart slowed near the end'."""
        return self.record_evidence(
            experiment_id, EvidenceKind.OBSERVATION, statement,
            provenance=Provenance.human(observer), correlation_id=correlation_id, **fields,
        )

    def interpret(
        self,
        experiment_id: str,
        statement: str,
        *,
        model: str,
        evidence_ids: Sequence[str] = (),
        confidence: float | None = None,
        correlation_id: str | None = None,
        **fields: Any,
    ) -> Evidence:
        """Record a model's reading of the data as AI_INTERPRETATION.

        The kind is fixed by this method and cannot be overridden. A model's
        reading is a hypothesis about the data, not a fact about the world,
        however fluent it sounds.

        `confidence`, if given, is stored as the model's self-report and
        labelled as such. It is not a probability that the statement is true,
        and nothing in this system treats it as one.
        """
        if not str(model).strip():
            raise ScienceError("An AI interpretation must name the model that produced it")
        metadata = dict(fields.pop("metadata", {}) or {})
        metadata["source_evidence_ids"] = list(evidence_ids)
        if confidence is not None:
            metadata["model_self_reported_confidence"] = float(confidence)
            metadata["confidence_note"] = (
                "Model self-report. Not a probability that this statement is correct."
            )
        return self.record_evidence(
            experiment_id, EvidenceKind.AI_INTERPRETATION, statement,
            provenance=Provenance(ProvenanceKind.AI, agent=model, inputs=list(evidence_ids)),
            metadata=metadata, correlation_id=correlation_id, **fields,
        )

    def list_evidence(self, experiment_id: str, **kwargs: Any) -> list[Evidence]:
        return self.store.list_evidence(experiment_id, **kwargs)

    # Claims ------------------------------------------------------------------
    def make_claim(
        self,
        experiment_id: str,
        statement: str,
        *,
        evidence_ids: Sequence[str] = (),
        hypothesis_id: str = "",
        author: str = "user",
        status: ClaimStatus | str = ClaimStatus.PROPOSED,
        correlation_id: str | None = None,
        require_empirical: bool = False,
    ) -> Claim:
        """State a conclusion, with the evidence it rests on.

        Every id must resolve: a claim citing evidence that is not in the
        record is unfalsifiable, which is the opposite of the point.

        With `require_empirical`, the claim must cite at least one measurement
        or observation. Callers that state conclusions about the physical world
        should set it, so a claim cannot rest entirely on interpretation.
        """
        resolved = []
        for evidence_id in evidence_ids:
            found = self.store.get_evidence(evidence_id)
            if found is None:
                raise ScienceError(f"Claim cites evidence {evidence_id!r}, which does not exist")
            resolved.append(found)
        if require_empirical and not any(e.kind.is_empirical for e in resolved):
            kinds = sorted({e.kind.value for e in resolved}) or ["none"]
            raise ScienceError(
                "This claim cites no measurement or observation "
                f"(cited: {', '.join(kinds)}). It cannot be stated as an empirical result."
            )
        if hypothesis_id and self.store.get_hypothesis(hypothesis_id) is None:
            raise ScienceError(f"No hypothesis {hypothesis_id!r}")
        claim = Claim(
            statement=statement,
            experiment_id=experiment_id,
            evidence_ids=[e.id for e in resolved],
            hypothesis_id=hypothesis_id,
            status=ClaimStatus(status) if not isinstance(status, ClaimStatus) else status,
            author=author,
            provenance=Provenance.human(author),
            metadata={"evidence_kinds": sorted({e.kind.value for e in resolved})},
        )
        self.store.save_claim(claim)
        self._publish(science_events.claim_created(claim, correlation_id))
        return claim

    def list_claims(self, experiment_id: str) -> list[Claim]:
        return self.store.list_claims(experiment_id)

    # Reporting ---------------------------------------------------------------
    def experiment_record(self, experiment_id: str) -> dict[str, Any] | None:
        return self.store.experiment_record(experiment_id)

    def stats(self) -> dict[str, Any]:
        return self.store.stats()
