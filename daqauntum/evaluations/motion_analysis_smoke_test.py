"""Regression guard for the motion analysis engine.

Two properties matter here and they pull in opposite directions.

The first is arithmetic: the numbers have to be right, and checkable against
the equations printed in `method`. Every expected value in this file was
computed by hand from the cart dataset, not copied from a previous run of the
code, so a silent change in the maths fails the suite instead of updating it.

The second is epistemic: a calculated value must never be able to pass as a
reading. `analyze_motion` consumes measurements and produces derived results,
and the tests assert that the derived flag and the CALCULATION evidence kind
survive that transition.
"""
from __future__ import annotations

import math

from science.analysis import AnalysisError, AnalysisResult, MotionAnalysis, analyze_motion
from science.analysis.motion import _linear_fit, average_velocity, interval_velocities
from science.models import EvidenceKind, Measurement, Provenance, ProvenanceKind
from science.units import DimensionalityError

# The v0.5 reference dataset: a cart on a track, accelerating gently.
CART_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]
CART_POSITIONS = [0.0, 1.0, 2.1, 3.3, 4.6]


def _series(
    times: list[float] = CART_TIMES,
    positions: list[float] = CART_POSITIONS,
    *,
    time_unit: str = "s",
    position_unit: str = "m",
    time_uncertainty: float | None = 0.01,
    position_uncertainty: float | None = 0.01,
) -> tuple[list[Measurement], list[Measurement]]:
    """Build a paired time/position series as a sensor would have recorded it."""
    provenance = Provenance(ProvenanceKind.SENSOR, agent="mock.cart.position")
    time_readings = [
        Measurement(quantity="time", value=t, unit=time_unit, uncertainty=time_uncertainty,
                    timestamp=1000.0 + index, source="mock.cart", provenance=provenance)
        for index, t in enumerate(times)
    ]
    position_readings = [
        Measurement(quantity="position", value=x, unit=position_unit,
                    uncertainty=position_uncertainty,
                    timestamp=1000.0 + index, source="mock.cart", provenance=provenance)
        for index, x in enumerate(positions)
    ]
    return time_readings, position_readings


def _close(actual: float, expected: float, tolerance: float = 1e-9) -> bool:
    return math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance)


def test_cart_arithmetic_matches_hand_calculation() -> None:
    """Displacement, velocity and acceleration, checked against the equations."""
    analysis = analyze_motion(*_series())
    assert analysis.samples == 5

    # Δx = 4.6 − 0.0 = 4.6 m
    assert _close(analysis.displacement.value, 4.6)
    assert analysis.displacement.unit == "m"

    # v_avg = 4.6 m / 4.0 s = 1.15 m/s
    assert _close(analysis.average_velocity.value, 1.15)
    assert analysis.average_velocity.unit == "m/s"

    # Interval velocities: each Δt is 1 s, so these are the position deltas.
    observed = [round(r.value, 10) for r in analysis.interval_velocities]
    assert observed == [1.0, 1.1, 1.2, 1.3], observed

    # a_avg = (1.3 − 1.0) m/s / (3.5 − 0.5) s = 0.3 / 3 = 0.1 m/s²
    assert _close(analysis.average_acceleration.value, 0.1)
    assert analysis.average_acceleration.unit == "m/s^2"
    assert analysis.warnings == [], analysis.warnings


def test_uncertainty_propagates_rather_than_vanishing() -> None:
    """A derived value with no uncertainty would overstate what we know."""
    analysis = analyze_motion(*_series())

    # Δx = x₂ − x₁, absolute uncertainties in quadrature: √(0.01² + 0.01²).
    expected_displacement = math.sqrt(0.01 ** 2 + 0.01 ** 2)
    assert _close(analysis.displacement.quantity.uncertainty, expected_displacement)

    # v = Δx / Δt, relative uncertainties in quadrature.
    relative = math.sqrt((expected_displacement / 4.6) ** 2 + (expected_displacement / 4.0) ** 2)
    assert _close(analysis.average_velocity.quantity.uncertainty, 1.15 * relative)

    # Every reported result carries one; none of them silently drop to None.
    for result in analysis.results():
        assert result.quantity.uncertainty is not None, f"{result.name} lost its uncertainty"
        assert result.quantity.uncertainty > 0


def test_least_squares_fit_matches_closed_form() -> None:
    """OLS on the cart data, computed independently in the test."""
    analysis = analyze_motion(*_series())
    fit = analysis.fit

    mean_t = sum(CART_TIMES) / len(CART_TIMES)
    mean_x = sum(CART_POSITIONS) / len(CART_POSITIONS)
    sxx = sum((t - mean_t) ** 2 for t in CART_TIMES)
    sxy = sum((t - mean_t) * (x - mean_x) for t, x in zip(CART_TIMES, CART_POSITIONS))
    slope = sxy / sxx
    intercept = mean_x - slope * mean_t

    assert _close(fit["slope"], slope)
    assert _close(fit["intercept"], intercept)
    assert 0.0 <= fit["r_squared"] <= 1.0
    assert fit["r_squared"] > 0.99
    assert len(fit["residuals"]) == 5
    assert fit["slope_standard_error"] is not None and fit["slope_standard_error"] > 0
    # The fit reports what r² does and does not say. Removing that note would
    # let a number stand in for an explanation.
    assert "does not by itself" in fit["interpretation_note"]

    # A perfectly linear series has zero residual, and no slope error to report.
    perfect = _linear_fit([0.0, 1.0, 2.0, 3.0], [0.0, 2.0, 4.0, 6.0])
    assert _close(perfect["slope"], 2.0) and _close(perfect["intercept"], 0.0)
    assert _close(perfect["r_squared"], 1.0)
    assert perfect["slope_standard_error"] is None


def test_calculated_results_never_pass_as_readings() -> None:
    """The distinction the product depends on, at the analysis boundary."""
    analysis = analyze_motion(*_series(), experiment_id="exp-cart")
    velocity = analysis.average_velocity

    evidence = velocity.as_evidence("exp-cart")
    assert evidence.kind is EvidenceKind.CALCULATION
    assert not evidence.is_empirical, "a computed velocity is not an observation"
    assert evidence.provenance.kind is ProvenanceKind.CALCULATED
    assert not evidence.provenance.is_ai_generated
    # The inputs are named, so the arithmetic can be re-run from the record.
    assert len(evidence.measurement_ids) == 4
    assert evidence.provenance.method == velocity.method
    assert evidence.unit == "m/s" and _close(evidence.value, 1.15)

    measurement = velocity.as_measurement("exp-cart", run_id="run-1")
    assert measurement.derived is True
    assert measurement.evidence_kind is EvidenceKind.CALCULATION
    assert measurement.provenance.kind is ProvenanceKind.CALCULATED
    assert measurement.experiment_id == "exp-cart" and measurement.run_id == "run-1"


def test_every_result_can_be_audited() -> None:
    """Result, units, method, inputs, provenance - all six, on every result."""
    analysis = analyze_motion(*_series())
    for result in analysis.results():
        assert isinstance(result, AnalysisResult)
        assert result.name and result.unit, result
        assert result.method, f"{result.name} has no equation recorded"
        assert result.inputs, f"{result.name} does not say what it consumed"
        evidence = result.as_evidence()
        assert evidence.provenance.inputs == result.inputs
    assert analysis.as_dict()["samples"] == 5


def test_zero_time_interval_is_refused() -> None:
    """Dividing by a zero interval would produce infinity, not a velocity."""
    times, positions = _series([0.0, 0.0, 1.0], [0.0, 0.5, 1.0])
    try:
        analyze_motion(times, positions)
    except AnalysisError as exc:
        assert "zero time interval" in str(exc), exc
    else:
        raise AssertionError("A repeated timestamp should stop the calculation")


def test_wrong_dimensions_are_refused() -> None:
    """Seconds and metres, not whatever the caller happened to pass."""
    for kwargs in ({"time_unit": "m"}, {"position_unit": "s"}, {"position_unit": "kg"}):
        times, positions = _series(**kwargs)
        try:
            analyze_motion(times, positions)
        except DimensionalityError:
            pass
        else:
            raise AssertionError(f"Accepted the wrong dimension for {kwargs}")

    # Correct dimension in a different unit is fine, and converts.
    times, positions = _series(time_unit="ms", position_unit="cm",
                               time_uncertainty=None, position_uncertainty=None)
    analysis = analyze_motion(times, positions)
    # 4.6 cm over 4.0 ms = 0.046 m / 0.004 s = 11.5 m/s
    assert _close(analysis.displacement.value, 0.046)
    assert _close(analysis.average_velocity.value, 11.5)


def test_mismatched_series_is_rejected_not_truncated() -> None:
    """Silently dropping the tail of a dataset changes the answer."""
    times, positions = _series()
    try:
        analyze_motion(times, positions[:-1])
    except AnalysisError as exc:
        assert "5 time readings and 4 position readings" in str(exc), exc
    else:
        raise AssertionError("Unequal series lengths should be an error")

    try:
        analyze_motion(times[:1], positions[:1])
    except AnalysisError as exc:
        assert "two samples" in str(exc), exc
    else:
        raise AssertionError("One sample does not describe motion")


def test_thin_data_reports_what_it_cannot_say() -> None:
    """With two samples there is no acceleration - say so, do not invent one."""
    times, positions = _series([0.0, 1.0], [0.0, 1.0])
    analysis = analyze_motion(times, positions)
    assert analysis.average_acceleration is None
    assert any("at least two intervals" in w for w in analysis.warnings), analysis.warnings
    assert _close(analysis.average_velocity.value, 1.0)


def test_readings_are_ordered_before_pairing() -> None:
    """Out-of-order arrival must not invert the sign of the velocity."""
    times, positions = _series()
    shuffled_t = [times[2], times[0], times[4], times[1], times[3]]
    shuffled_x = [positions[2], positions[0], positions[4], positions[1], positions[3]]
    analysis = analyze_motion(shuffled_t, shuffled_x)
    assert _close(analysis.displacement.value, 4.6)
    assert _close(analysis.average_velocity.value, 1.15)
    assert [round(r.value, 10) for r in analysis.interval_velocities] == [1.0, 1.1, 1.2, 1.3]


def test_helpers_are_usable_on_their_own() -> None:
    """The building blocks work without the full analysis wrapper."""
    times, positions = _series()
    samples = list(zip(times, positions))
    single = average_velocity(samples[0], samples[1])
    assert _close(single.value, 1.0) and single.name == "velocity"
    assert len(interval_velocities(samples)) == 4
    assert isinstance(analyze_motion(times, positions), MotionAnalysis)


def main() -> None:
    test_cart_arithmetic_matches_hand_calculation()
    test_uncertainty_propagates_rather_than_vanishing()
    test_least_squares_fit_matches_closed_form()
    test_calculated_results_never_pass_as_readings()
    test_every_result_can_be_audited()
    test_zero_time_interval_is_refused()
    test_wrong_dimensions_are_refused()
    test_mismatched_series_is_rejected_not_truncated()
    test_thin_data_reports_what_it_cannot_say()
    test_readings_are_ordered_before_pairing()
    test_helpers_are_usable_on_their_own()
    print("DaQauntum v0.5 motion analysis smoke test: PASS")


if __name__ == "__main__":
    main()
