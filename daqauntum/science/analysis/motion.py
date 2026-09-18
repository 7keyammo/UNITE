"""Motion analysis: position and time in, velocity and acceleration out.

Written in plain Python with the equations visible. NumPy would be faster on
large series and is not used, because the point of this module is that a
student or a reviewer can read `(x2 - x1) / (t2 - t1)` and check it. Every
result carries `method`, `inputs` and `provenance` so the arithmetic can be
reconstructed from the record alone.

Nothing here decides what a result *means*. Interpretation is a separate kind
of knowledge and is never produced by a calculation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from science.models import (
    Evidence,
    EvidenceKind,
    Measurement,
    Provenance,
    ProvenanceKind,
)
from science.units import DimensionalityError, Quantity, parse_unit


class AnalysisError(ValueError):
    """Raised when data cannot support the requested calculation."""


@dataclass
class AnalysisResult:
    """One calculated value, with everything needed to audit it."""

    name: str
    quantity: Quantity
    method: str                                   # the equation, written out
    inputs: list[str] = field(default_factory=list)   # measurement ids consumed
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def value(self) -> float:
        return self.quantity.value

    @property
    def unit(self) -> str:
        return self.quantity.display_unit

    def as_evidence(self, experiment_id: str = "") -> Evidence:
        """Turn the result into CALCULATION evidence.

        Calculation, never measurement: this value was derived, and the record
        has to keep saying so however far it travels.
        """
        return Evidence(
            kind=EvidenceKind.CALCULATION,
            statement=f"{self.name} = {self.quantity.format()}",
            experiment_id=experiment_id,
            quantity=self.name,
            value=self.quantity.value,
            unit=self.quantity.display_unit,
            uncertainty=self.quantity.uncertainty,
            measurement_ids=list(self.inputs),
            provenance=Provenance(
                ProvenanceKind.CALCULATED,
                agent="daqauntum.analysis.motion",
                inputs=list(self.inputs),
                method=self.method,
            ),
            metadata={"detail": self.detail, **self.metadata},
        )

    def as_measurement(self, experiment_id: str = "", run_id: str = "") -> Measurement:
        """Store the result alongside the readings, flagged as derived."""
        return Measurement(
            quantity=self.name,
            value=self.quantity.value,
            unit=self.quantity.display_unit,
            uncertainty=self.quantity.uncertainty,
            experiment_id=experiment_id,
            run_id=run_id,
            source="daqauntum.analysis.motion",
            derived=True,
            provenance=Provenance(
                ProvenanceKind.CALCULATED,
                agent="daqauntum.analysis.motion",
                inputs=list(self.inputs),
                method=self.method,
            ),
            metadata={"detail": self.detail, **self.metadata},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            **self.quantity.as_dict(),
            "method": self.method,
            "inputs": list(self.inputs),
            "detail": self.detail,
            "metadata": dict(self.metadata),
        }


@dataclass
class MotionAnalysis:
    """The full result of analysing one position-time series."""

    samples: int
    displacement: AnalysisResult | None = None
    average_velocity: AnalysisResult | None = None
    interval_velocities: list[AnalysisResult] = field(default_factory=list)
    average_acceleration: AnalysisResult | None = None
    fit: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def results(self) -> list[AnalysisResult]:
        found = [r for r in (self.displacement, self.average_velocity, self.average_acceleration) if r]
        return found + list(self.interval_velocities)

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "displacement": self.displacement.as_dict() if self.displacement else None,
            "average_velocity": self.average_velocity.as_dict() if self.average_velocity else None,
            "average_acceleration": self.average_acceleration.as_dict() if self.average_acceleration else None,
            "interval_velocities": [r.as_dict() for r in self.interval_velocities],
            "fit": dict(self.fit),
            "warnings": list(self.warnings),
        }


def _pair_series(
    times: list[Measurement], positions: list[Measurement]
) -> list[tuple[Measurement, Measurement]]:
    """Match time and position readings into samples, in time order.

    Pairing is positional after sorting by timestamp: a time reading and a
    position reading taken together are one sample. Mismatched counts are an
    error rather than something to silently truncate, because quietly dropping
    the tail of a dataset changes the answer without telling anyone.
    """
    if len(times) != len(positions):
        raise AnalysisError(
            f"Got {len(times)} time readings and {len(positions)} position readings. "
            "Each position needs a matching time."
        )
    ordered = sorted(zip(times, positions), key=lambda pair: (pair[0].timestamp, pair[0].id))
    return list(ordered)


def _validate(samples: list[tuple[Measurement, Measurement]]) -> None:
    if len(samples) < 2:
        raise AnalysisError("At least two samples are needed to describe motion")
    for time_reading, position_reading in samples:
        if not parse_unit(time_reading.unit).dimension == parse_unit("s").dimension:
            raise DimensionalityError(
                f"Time readings must be a duration, got {time_reading.unit!r}"
            )
        if not parse_unit(position_reading.unit).dimension == parse_unit("m").dimension:
            raise DimensionalityError(
                f"Position readings must be a length, got {position_reading.unit!r}"
            )


def average_velocity(
    start: tuple[Measurement, Measurement], end: tuple[Measurement, Measurement]
) -> AnalysisResult:
    """v_avg = (x₂ − x₁) / (t₂ − t₁)."""
    (t1, x1), (t2, x2) = start, end
    duration = t2.as_quantity - t1.as_quantity
    if duration.si_value == 0:
        raise AnalysisError(
            "Cannot compute velocity across a zero time interval - two readings share a timestamp"
        )
    displacement = x2.as_quantity - x1.as_quantity
    velocity = displacement / duration
    return AnalysisResult(
        name="velocity",
        quantity=Quantity.of(velocity.to("m/s"), "m/s", velocity.uncertainty_in("m/s")),
        method="v = Δx / Δt = (x₂ − x₁) / (t₂ − t₁)",
        inputs=[x1.id, x2.id, t1.id, t2.id],
        detail=(f"({x2.value} − {x1.value}) {x2.unit} / "
                f"({t2.value} − {t1.value}) {t2.unit}"),
        metadata={"t_start": t1.as_quantity.to("s"), "t_end": t2.as_quantity.to("s")},
    )


def interval_velocities(samples: list[tuple[Measurement, Measurement]]) -> list[AnalysisResult]:
    """Velocity across each consecutive pair - the shape of the motion."""
    return [average_velocity(samples[i], samples[i + 1]) for i in range(len(samples) - 1)]


def _linear_fit(xs: list[float], ys: list[float]) -> dict[str, Any]:
    """Ordinary least squares, written out.

    Returns slope, intercept, r² and residuals. This is the standard closed
    form; it is here rather than in SciPy so the numbers can be checked by hand
    against the formula printed in `method`.
    """
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        raise AnalysisError("Cannot fit a line: every sample shares the same time")
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    predicted = [slope * x + intercept for x in xs]
    residuals = [y - p for y, p in zip(ys, predicted)]
    ss_res = sum(r * r for r in residuals)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot else 1.0
    # Standard error of the slope, for n > 2.
    slope_error = math.sqrt(ss_res / ((n - 2) * sxx)) if n > 2 and ss_res > 0 else None
    return {
        "slope": slope,
        "intercept": intercept,
        "r_squared": r_squared,
        "residuals": residuals,
        "slope_standard_error": slope_error,
        "method": "ordinary least squares: slope = Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²",
    }


def analyze_motion(
    times: list[Measurement],
    positions: list[Measurement],
    *,
    experiment_id: str = "",
) -> MotionAnalysis:
    """Analyse a position-time series.

    Produces displacement, average velocity, per-interval velocities and, when
    the data supports it, an average acceleration. Every result records the
    equation and the measurement ids it consumed.
    """
    samples = _pair_series(times, positions)
    _validate(samples)

    warnings: list[str] = []
    first, last = samples[0], samples[-1]

    displacement_quantity = last[1].as_quantity - first[1].as_quantity
    displacement = AnalysisResult(
        name="displacement",
        quantity=Quantity.of(displacement_quantity.to("m"), "m",
                             displacement_quantity.uncertainty_in("m")),
        method="Δx = x_final − x_initial",
        inputs=[first[1].id, last[1].id],
        detail=f"{last[1].value} {last[1].unit} − {first[1].value} {first[1].unit}",
    )

    mean_velocity = average_velocity(first, last)
    mean_velocity.name = "average_velocity"
    mean_velocity.method = "v_avg = Δx / Δt over the whole run"

    intervals = interval_velocities(samples)

    # Acceleration only if the velocity actually changed. Reporting a
    # near-zero acceleration from noise would invent a finding.
    acceleration: AnalysisResult | None = None
    if len(intervals) >= 2:
        v_first, v_last = intervals[0], intervals[-1]
        t_first = (v_first.metadata["t_start"] + v_first.metadata["t_end"]) / 2
        t_last = (v_last.metadata["t_start"] + v_last.metadata["t_end"]) / 2
        span = t_last - t_first
        if span <= 0:
            warnings.append("Interval midpoints do not advance in time; acceleration not computed.")
        else:
            delta_v = v_last.quantity - v_first.quantity
            acceleration_quantity = delta_v / Quantity.of(span, "s")
            acceleration = AnalysisResult(
                name="average_acceleration",
                quantity=Quantity.of(acceleration_quantity.to("m/s^2"), "m/s^2",
                                     acceleration_quantity.uncertainty_in("m/s^2")),
                method="a_avg = Δv / Δt, using interval-midpoint times",
                inputs=sorted({*v_first.inputs, *v_last.inputs}),
                detail=(f"({v_last.value:.4g} − {v_first.value:.4g}) m/s / {span:.4g} s"),
                metadata={"t_start": t_first, "t_end": t_last},
            )
    else:
        warnings.append("Fewer than three samples: acceleration needs at least two intervals.")

    fit: dict[str, Any] = {}
    try:
        seconds = [t.as_quantity.to("s") for t, _ in samples]
        metres = [x.as_quantity.to("m") for _, x in samples]
        fit = _linear_fit(seconds, metres)
        fit["interpretation_note"] = (
            "r² measures how well a straight line fits position against time. "
            "A low value means the motion was not uniform; it does not by itself "
            "say why."
        )
        if fit["r_squared"] < 0.99 and acceleration is None:
            warnings.append("Position is not linear in time, so average velocity hides the detail.")
    except AnalysisError as exc:
        warnings.append(str(exc))

    return MotionAnalysis(
        samples=len(samples),
        displacement=displacement,
        average_velocity=mean_velocity,
        interval_velocities=intervals,
        average_acceleration=acceleration,
        fit=fit,
        warnings=warnings,
    )
