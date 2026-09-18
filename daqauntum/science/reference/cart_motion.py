"""The cart motion reference experiment.

A cart travels along a track; position is recorded against time; the question
is how it moved. It is the smallest investigation that still needs every part
of the system - units, uncertainty, hypotheses, analysis, evidence, plots and
claims - and it is the worked example the documentation follows.

About the data
--------------
The default dataset is the illustrative one from the specification:

    t (s)  0.0  1.0  2.0  3.0  4.0
    x (m)  0.0  1.0  2.1  3.3  4.6

Nobody measured this. It is example data distributed with the software, so it
is recorded with IMPORTED provenance, which makes `evidence_kind` report
LITERATURE rather than MEASUREMENT, and every plot drawn from it says so on its
face. Presenting it as a reading would be exactly the failure this system
exists to prevent, and a demo is not an excuse.

`run_cart_motion(source="simulated")` uses `MockCartSensor` instead, which
labels its output SIMULATED. Both paths are honest about what they are; neither
produces a measurement, because no measurement was taken.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from science.analysis import MotionAnalysis
from science.core import ScienceCore
from science.models import (
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
from science.sensors import MockCartSensor

EXAMPLE_TIMES: tuple[float, ...] = (0.0, 1.0, 2.0, 3.0, 4.0)
EXAMPLE_POSITIONS: tuple[float, ...] = (0.0, 1.0, 2.1, 3.3, 4.6)

EXAMPLE_SOURCE = "daqauntum.reference.cart_motion"
EXAMPLE_NOTE = (
    "Example data distributed with DaQauntum. Not a reading anyone took; "
    "included so the pipeline can be demonstrated without hardware."
)

TIME_UNCERTAINTY = 0.01     # a stopwatch read to a hundredth of a second
POSITION_UNCERTAINTY = 0.01  # a tape measured to the nearest centimetre


@dataclass
class CartMotionExperiment:
    """The result of one reference run - every object it produced."""

    experiment: Experiment
    hypothesis: Hypothesis
    measurements: list[Measurement] = field(default_factory=list)
    analysis: MotionAnalysis | None = None
    evidence: list[Evidence] = field(default_factory=list)
    plots: list[Path] = field(default_factory=list)
    claim_id: str = ""
    source: str = "example"

    @property
    def experiment_id(self) -> str:
        return self.experiment.id

    def summary(self) -> dict[str, Any]:
        analysis = self.analysis
        return {
            "experiment_id": self.experiment.id,
            "title": self.experiment.title,
            "status": self.experiment.status.value,
            "source": self.source,
            "measurements": len(self.measurements),
            "displacement_m": analysis.displacement.value if analysis else None,
            "average_velocity_ms": analysis.average_velocity.value if analysis else None,
            "average_acceleration_ms2": (
                analysis.average_acceleration.value
                if analysis and analysis.average_acceleration else None
            ),
            "r_squared": (analysis.fit or {}).get("r_squared") if analysis else None,
            "evidence_kinds": sorted({e.kind.value for e in self.evidence}),
            "plots": [str(p) for p in self.plots],
            "warnings": list(analysis.warnings) if analysis else [],
            "hypothesis_status": self.hypothesis.status.value,
        }


def _example_measurements(
    core: ScienceCore,
    experiment_id: str,
    times: Sequence[float],
    positions: Sequence[float],
    *,
    correlation_id: str,
) -> list[Measurement]:
    """Record the example dataset as imported values, never as readings."""
    provenance = Provenance(ProvenanceKind.IMPORTED, agent=EXAMPLE_SOURCE, notes=EXAMPLE_NOTE)
    recorded = []
    for t, x in zip(times, positions):
        for quantity, value, unit, uncertainty in (
            ("time", t, "s", TIME_UNCERTAINTY),
            ("position", x, "m", POSITION_UNCERTAINTY),
        ):
            recorded.append(core.record_measurement(
                experiment_id, quantity, value, unit,
                uncertainty=uncertainty, timestamp=t, source=EXAMPLE_SOURCE,
                provenance=provenance,
                metadata={"example_data": True, "note": EXAMPLE_NOTE},
                correlation_id=correlation_id,
            ))
    return recorded


def run_cart_motion(
    core: ScienceCore,
    *,
    source: str = "example",
    times: Sequence[float] = EXAMPLE_TIMES,
    positions: Sequence[float] = EXAMPLE_POSITIONS,
    sensor: MockCartSensor | None = None,
    samples: int = 5,
    plot: bool = True,
    author: str = "user",
) -> CartMotionExperiment:
    """Run the whole investigation and return everything it produced.

    `source` is "example" (the documented dataset, recorded as imported) or
    "simulated" (generated by MockCartSensor, recorded as simulated). There is
    no "measured" option: this function has no instrument, and offering one
    would be an invitation to relabel generated data.
    """
    if source not in {"example", "simulated"}:
        raise ValueError(
            f"Unknown source {source!r}. Use 'example' or 'simulated'. Real readings "
            "are ingested with core.run_sensor and a backend for your instrument."
        )

    correlation_id = core.new_correlation_id()
    experiment = core.create_experiment(
        "Cart motion on a level track",
        research_question="How does the cart's position change with time, and is the motion uniform?",
        description=(
            "A cart travels along a level track. Position is recorded at one-second "
            "intervals and the position-time series is analysed for average velocity "
            "and for any change in velocity across the run."
        ),
        depth=Depth.INTRODUCTORY,
        independent_variables=["time"],
        dependent_variables=["position"],
        controlled_variables=["track angle", "cart mass", "starting point"],
        apparatus=["level track", "cart", "metre rule", "stopwatch"],
        procedure=[
            "Set the cart at the zero mark and hold it still.",
            "Release the cart and start the stopwatch together.",
            "Record the cart's position at each one-second mark.",
            "Repeat the run and keep every set of readings.",
        ],
        correlation_id=correlation_id,
    )

    hypothesis = core.propose_hypothesis(
        experiment.id,
        "The cart's velocity increases over the run rather than staying constant.",
        rationale=(
            "Successive one-second displacements grow, which a constant velocity "
            "would not produce."
        ),
        expected_relationship="position increases faster than linearly in time",
        correlation_id=correlation_id,
    )

    core.set_status(experiment.id, ExperimentStatus.ACTIVE, correlation_id=correlation_id)

    if source == "example":
        measurements = _example_measurements(
            core, experiment.id, times, positions, correlation_id=correlation_id)
    else:
        backend = sensor or MockCartSensor(noise=0.0)
        time_readings, position_readings = backend.sample(samples)
        measurements = core.ingest_readings(
            experiment.id, list(time_readings) + list(position_readings),
            correlation_id=correlation_id)

    core.set_status(experiment.id, ExperimentStatus.ANALYZING, correlation_id=correlation_id)
    analysis, evidence = core.analyse_motion(experiment.id, correlation_id=correlation_id)

    plots: list[Path] = []
    if plot:
        for path, plot_evidence in core.plot_motion(
            experiment.id, analysis, include_residuals=True, correlation_id=correlation_id
        ):
            plots.append(path)
            evidence.append(plot_evidence)

    # Link the calculated results to the hypothesis. Linking records the
    # relationship; it does not decide what it means.
    acceleration = analysis.average_acceleration
    supports = acceleration is not None and acceleration.value > 0
    for item in evidence:
        if item.quantity == "average_acceleration":
            core.link_evidence(hypothesis.id, item.id, supports=supports,
                               correlation_id=correlation_id)

    core.assess_hypothesis(
        hypothesis.id,
        HypothesisStatus.SUPPORTED if supports else HypothesisStatus.INCONCLUSIVE,
        assessor=author,
        reasoning=(
            f"Interval velocities rise across the run and the average acceleration is "
            f"{acceleration.quantity.format()}."
            if supports else
            "The data does not show a consistent change in velocity."
        ),
        correlation_id=correlation_id,
    )

    # The claim cites the calculated evidence. `require_empirical` is
    # deliberately not set: no measurement was taken, and a claim about the
    # physical world would be unsupportable from this data.
    claim = core.make_claim(
        experiment.id,
        (f"Over the recorded run the cart's average velocity was "
         f"{analysis.average_velocity.quantity.format()}"
         + (f" and its average acceleration was {acceleration.quantity.format()}."
            if acceleration else ".")),
        evidence_ids=[e.id for e in evidence if e.kind is EvidenceKind.CALCULATION],
        hypothesis_id=hypothesis.id,
        author=author,
        status=ClaimStatus.SUPPORTED if supports else ClaimStatus.UNRESOLVED,
        correlation_id=correlation_id,
    )

    completed = core.set_status(experiment.id, ExperimentStatus.COMPLETED,
                                correlation_id=correlation_id)
    return CartMotionExperiment(
        experiment=completed,
        hypothesis=core.store.get_hypothesis(hypothesis.id),
        measurements=measurements,
        analysis=analysis,
        evidence=evidence,
        plots=plots,
        claim_id=claim.id,
        source=source,
    )
