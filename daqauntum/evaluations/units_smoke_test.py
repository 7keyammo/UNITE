"""Regression guard for the scientific units layer.

DaQauntum implements dimensional analysis natively so the core stays
dependency-free and the arithmetic stays inspectable. That choice is only
defensible if the implementation is actually right, so this suite cross-checks
every conversion against Pint whenever Pint is installed. The native code is
the implementation; Pint, when present, is the independent check on it.

Without Pint the suite still runs - it simply loses the external check and says
so, rather than passing quietly and implying a verification that did not happen.
"""
from __future__ import annotations

import math

from science.units import (
    DimensionalityError,
    Quantity,
    UnitError,
    compatible,
    dimension_name,
    known_units,
    parse_unit,
)


# (value, from_unit, to_unit) - the conversions Pint is asked to confirm.
CONVERSIONS: tuple[tuple[float, str, str], ...] = (
    (1.0, "m", "cm"), (2.5, "km", "m"), (36.0, "km/h", "m/s"), (1.0, "mi", "m"),
    (12.0, "in", "cm"), (500.0, "g", "kg"), (2.0, "h", "s"), (1.0, "N", "kg*m/s^2"),
    (1.0, "J", "N*m"), (1.0, "W", "J/s"), (9.81, "m/s^2", "m/s^2"), (1.5, "kW", "W"),
    (230.0, "V", "mV"), (1.0, "Hz", "s^-1"), (100.0, "degC", "K"), (212.0, "degF", "degC"),
    (1.0, "ohm", "V/A"), (45.0, "deg", "rad"), (1.0, "kg", "g"), (1000.0, "ms", "s"),
)

COMPATIBILITY: tuple[tuple[str, str, bool], ...] = (
    ("m", "cm", True), ("m", "s", False), ("N", "kg*m/s^2", True), ("J", "W", False),
    ("V", "W/A", True), ("Hz", "s^-1", True), ("m/s", "m/s^2", False), ("J", "N*m", True),
)


def test_dimensional_safety() -> None:
    """The headline behaviour: 3 m + 5 s is impossible, 3 m + 50 cm is not."""
    assert (Quantity.of(3, "m") + Quantity.of(50, "cm")).to("m") == 3.5
    try:
        Quantity.of(3, "m") + Quantity.of(5, "s")
    except DimensionalityError as exc:
        assert "length" in str(exc) and "time" in str(exc), exc
    else:
        raise AssertionError("adding a length to a time was permitted")

    try:
        Quantity.of(3, "m").to("s")
    except DimensionalityError:
        pass
    else:
        raise AssertionError("a length was expressed as a time")

    try:
        parse_unit("furlongs_per_fortnight")
    except UnitError:
        pass
    else:
        raise AssertionError("an unknown unit was accepted")


def test_derived_dimensions() -> None:
    velocity = Quantity.of(4.6, "m") / Quantity.of(4.0, "s")
    assert dimension_name(velocity.dimension) == "length·time⁻¹", dimension_name(velocity.dimension)
    assert math.isclose(velocity.to("m/s"), 1.15)

    acceleration = velocity / Quantity.of(2.0, "s")
    assert compatible_dimension(acceleration, "m/s^2")

    force = Quantity.of(2.0, "kg") * acceleration
    assert compatible_dimension(force, "N"), dimension_name(force.dimension)
    energy = force * Quantity.of(3.0, "m")
    assert compatible_dimension(energy, "J")


def compatible_dimension(quantity: Quantity, unit: str) -> bool:
    return quantity.dimension == parse_unit(unit).dimension


def test_uncertainty_propagation() -> None:
    """Standard first-order rules, treating inputs as independent."""
    a = Quantity.of(3.0, "m", 0.04)
    b = Quantity.of(4.0, "m", 0.03)
    # Absolute uncertainties add in quadrature for sums.
    assert math.isclose((a + b).uncertainty_in("m"), math.hypot(0.04, 0.03)), (a + b).uncertainty_in("m")
    assert math.isclose((a - b).uncertainty_in("m"), math.hypot(0.04, 0.03))

    # Relative uncertainties add in quadrature for quotients.
    distance = Quantity.of(4.6, "m", 0.01)
    duration = Quantity.of(4.0, "s", 0.05)
    velocity = distance / duration
    expected = abs(velocity.si_value) * math.hypot(0.01 / 4.6, 0.05 / 4.0)
    assert math.isclose(velocity.si_uncertainty, expected, rel_tol=1e-12), velocity.si_uncertainty

    # A quantity without uncertainty stays without one.
    assert (Quantity.of(1.0, "m") + Quantity.of(1.0, "m")).uncertainty is None
    # Scaling by a constant scales the uncertainty.
    assert math.isclose((a * 2).uncertainty_in("m"), 0.08)

    try:
        Quantity.of(1.0, "m", -0.1)
    except UnitError:
        pass
    else:
        raise AssertionError("a negative uncertainty was accepted")


def test_offset_units() -> None:
    """Celsius and Fahrenheit are offset scales, not just scaled ones."""
    assert math.isclose(Quantity.of(25, "degC").to("K"), 298.15)
    assert math.isclose(Quantity.of(298.15, "K").to("degC"), 25.0, abs_tol=1e-9)
    assert math.isclose(Quantity.of(212, "degF").to("degC"), 100.0, abs_tol=1e-9)
    # An offset shifts a centre, not an interval width.
    assert math.isclose(Quantity.of(25, "degC", 0.5).uncertainty_in("K"), 0.5)
    # Offset units cannot appear in composites, where they have no meaningful zero.
    for bad in ("degC/s", "degC^2"):
        try:
            parse_unit(bad)
        except UnitError:
            continue
        raise AssertionError(f"{bad} was accepted as a unit")


def test_round_trip_serialization() -> None:
    original = Quantity.of(1.23, "m", 0.01)
    restored = Quantity.from_dict(original.as_dict())
    assert math.isclose(restored.si_value, original.si_value)
    assert math.isclose(restored.si_uncertainty, original.si_uncertainty)
    assert restored.dimension == original.dimension
    assert "±" in original.format()
    assert "m" in original.format()


def test_against_pint() -> int:
    """Cross-check every conversion against Pint, if it is installed."""
    try:
        import pint
    except ImportError:
        print("  note: Pint is not installed, so conversions were NOT independently verified.")
        print("        Install it with `pip install -r requirements-science.txt` to enable the check.")
        return 0

    registry = pint.UnitRegistry()
    worst = 0.0
    for value, source, target in CONVERSIONS:
        mine = Quantity.of(value, source).to(target)
        theirs = registry.Quantity(value, source.replace("^", "**")).to(target.replace("^", "**")).magnitude
        relative = abs(mine - theirs) / max(abs(theirs), 1e-12)
        worst = max(worst, relative)
        assert relative < 1e-9, f"{value} {source} -> {target}: native {mine} vs Pint {theirs}"

    for left, right, expected in COMPATIBILITY:
        mine = compatible(left, right)
        theirs = (registry.Quantity(1, left.replace("^", "**")).dimensionality
                  == registry.Quantity(1, right.replace("^", "**")).dimensionality)
        assert mine == theirs == expected, f"{left} ~ {right}: native {mine}, Pint {theirs}, expected {expected}"

    print(f"  verified {len(CONVERSIONS)} conversions against Pint {pint.__version__} "
          f"(worst relative error {worst:.1e})")
    return len(CONVERSIONS)


def main() -> None:
    test_dimensional_safety()
    test_derived_dimensions()
    test_uncertainty_propagation()
    test_offset_units()
    test_round_trip_serialization()
    checked = test_against_pint()
    assert len(known_units()) > 50, "the unit registry looks suspiciously small"
    suffix = f", {checked} cross-checked against Pint" if checked else ", Pint check skipped"
    print(f"DaQauntum units smoke test: PASS{suffix}")


if __name__ == "__main__":
    main()
