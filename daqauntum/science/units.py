"""Dimensional analysis and quantities for the DaQauntum scientific core.

Why this is implemented here rather than delegated to Pint
----------------------------------------------------------
DaQauntum ships with six runtime dependencies and boots on a machine with
nothing else installed. Dimensional analysis over the seven SI base dimensions
is a small, closed, textbook problem: a rational exponent vector plus a scale
factor. Implementing it costs a few hundred lines that stay inspectable, which
matters because a scientific result the user cannot audit is not much of a
result.

That reasoning only holds if the implementation is actually correct, so
`evaluations/units_smoke_test.py` cross-checks these conversions against Pint
whenever Pint happens to be installed. The native code is the implementation;
Pint, when present, is the independent check on it.

Everything is stored internally in SI base units. A Quantity remembers the unit
it was written in so a result can be shown the way the user thinks about it.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Iterable


# The seven SI base dimensions, in a fixed order. A dimension vector is an
# exponent per entry, so metres are (1,0,0,0,0,0,0) and newtons are
# (1,1,-2,0,0,0,0) for length·mass·time⁻².
BASE_DIMENSIONS: tuple[str, ...] = ("length", "mass", "time", "current", "temperature", "amount", "luminosity")

_ZERO = (Fraction(0),) * len(BASE_DIMENSIONS)


class UnitError(ValueError):
    """Raised for an unknown unit or an invalid unit expression."""


class DimensionalityError(UnitError):
    """Raised when an operation would combine incompatible dimensions.

    This is the error that makes `3 metres + 5 seconds` impossible while
    `3 metres + 50 centimetres` stays fine.
    """


def _dim(**exponents: int | Fraction) -> tuple[Fraction, ...]:
    values = []
    for name in BASE_DIMENSIONS:
        values.append(Fraction(exponents.get(name, 0)))
    return tuple(values)


DIMENSIONLESS = _ZERO


@dataclass(frozen=True)
class Unit:
    """A named unit: a dimension vector, a scale to SI, and an offset.

    `offset` exists only for the absolute temperature scales; everything else
    is purely multiplicative. Keeping it on the Unit rather than special-casing
    temperature elsewhere means `degC` converts correctly without the rest of
    the system knowing temperature is unusual.
    """

    symbol: str
    dimension: tuple[Fraction, ...]
    scale: float = 1.0
    offset: float = 0.0
    name: str = ""

    def to_si(self, value: float) -> float:
        return value * self.scale + self.offset

    def from_si(self, value: float) -> float:
        return (value - self.offset) / self.scale

    @property
    def dimensionless(self) -> bool:
        return self.dimension == DIMENSIONLESS


# Base and derived units. Deliberately a curated set covering the quantities
# v0.5 names, rather than an attempt at every unit in existence.
_L = _dim(length=1)
_M = _dim(mass=1)
_T = _dim(time=1)
_I = _dim(current=1)
_K = _dim(temperature=1)
_N = _dim(amount=1)
_J = _dim(luminosity=1)

_VELOCITY = _dim(length=1, time=-1)
_ACCELERATION = _dim(length=1, time=-2)
_FORCE = _dim(length=1, mass=1, time=-2)
_ENERGY = _dim(length=2, mass=1, time=-2)
_POWER = _dim(length=2, mass=1, time=-3)
_CHARGE = _dim(current=1, time=1)
_VOLTAGE = _dim(length=2, mass=1, time=-3, current=-1)
_RESISTANCE = _dim(length=2, mass=1, time=-3, current=-2)
_FREQUENCY = _dim(time=-1)
_AREA = _dim(length=2)
_VOLUME = _dim(length=3)
_MOMENTUM = _dim(length=1, mass=1, time=-1)
_DENSITY = _dim(length=-3, mass=1)
_PRESSURE = _dim(length=-1, mass=1, time=-2)


_UNITS: dict[str, Unit] = {}


def _register(symbol: str, dimension: tuple[Fraction, ...], scale: float = 1.0,
              offset: float = 0.0, name: str = "", aliases: Iterable[str] = ()) -> None:
    unit = Unit(symbol, dimension, scale, offset, name or symbol)
    for key in (symbol, *aliases):
        _UNITS[key] = unit


# Base
_register("m", _L, name="metre", aliases=("meter", "metre", "meters", "metres"))
_register("kg", _M, name="kilogram", aliases=("kilogram", "kilograms"))
_register("s", _T, name="second", aliases=("sec", "secs", "second", "seconds"))
_register("A", _I, name="ampere", aliases=("amp", "amps", "ampere", "amperes"))
_register("K", _K, name="kelvin", aliases=("kelvin",))
_register("mol", _N, name="mole", aliases=("mole", "moles"))
_register("cd", _J, name="candela", aliases=("candela",))

# Length
_register("km", _L, 1e3, name="kilometre", aliases=("kilometre", "kilometer", "kilometres", "kilometers"))
_register("cm", _L, 1e-2, name="centimetre", aliases=("centimetre", "centimeter", "centimetres", "centimeters"))
_register("mm", _L, 1e-3, name="millimetre", aliases=("millimetre", "millimeter"))
_register("um", _L, 1e-6, name="micrometre", aliases=("µm", "micrometre", "micrometer"))
_register("nm", _L, 1e-9, name="nanometre", aliases=("nanometre", "nanometer"))
_register("in", _L, 0.0254, name="inch", aliases=("inch", "inches"))
_register("ft", _L, 0.3048, name="foot", aliases=("foot", "feet"))
_register("mi", _L, 1609.344, name="mile", aliases=("mile", "miles"))

# Mass
_register("g", _M, 1e-3, name="gram", aliases=("gram", "grams"))
_register("mg", _M, 1e-6, name="milligram", aliases=("milligram",))
_register("t", _M, 1e3, name="tonne", aliases=("tonne", "tonnes"))

# Time
_register("ms", _T, 1e-3, name="millisecond", aliases=("millisecond", "milliseconds"))
_register("us", _T, 1e-6, name="microsecond", aliases=("µs", "microsecond"))
_register("ns", _T, 1e-9, name="nanosecond", aliases=("nanosecond",))
_register("min", _T, 60.0, name="minute", aliases=("minute", "minutes"))
_register("h", _T, 3600.0, name="hour", aliases=("hr", "hour", "hours"))
_register("day", _T, 86400.0, name="day", aliases=("days",))

# Derived
_register("N", _FORCE, name="newton", aliases=("newton", "newtons"))
_register("J", _ENERGY, name="joule", aliases=("joule", "joules"))
_register("W", _POWER, name="watt", aliases=("watt", "watts"))
_register("Hz", _FREQUENCY, name="hertz", aliases=("hertz",))
_register("C", _CHARGE, name="coulomb", aliases=("coulomb", "coulombs"))
_register("V", _VOLTAGE, name="volt", aliases=("volt", "volts"))
_register("ohm", _RESISTANCE, name="ohm", aliases=("Ω", "ohms"))
_register("Pa", _PRESSURE, name="pascal", aliases=("pascal",))
_register("kJ", _ENERGY, 1e3, name="kilojoule")
_register("kW", _POWER, 1e3, name="kilowatt")
_register("mV", _VOLTAGE, 1e-3, name="millivolt")
_register("mA", _I, 1e-3, name="milliampere")
_register("kHz", _FREQUENCY, 1e3, name="kilohertz")
_register("MHz", _FREQUENCY, 1e6, name="megahertz")
_register("kN", _FORCE, 1e3, name="kilonewton")

# Temperature. Celsius shares kelvin's dimension but is offset, which is why
# Unit carries an offset at all.
_register("degC", _K, 1.0, 273.15, name="degree Celsius", aliases=("°C", "celsius", "C_deg"))
_register("degF", _K, 5.0 / 9.0, 255.3722222222222, name="degree Fahrenheit", aliases=("°F", "fahrenheit"))

# Dimensionless
_register("1", DIMENSIONLESS, name="dimensionless", aliases=("", "dimensionless", "count", "unitless"))
_register("%", DIMENSIONLESS, 0.01, name="percent", aliases=("percent",))
_register("rad", DIMENSIONLESS, name="radian", aliases=("radian", "radians"))
_register("deg", DIMENSIONLESS, math.pi / 180.0, name="degree", aliases=("degree", "degrees"))


# Unit expressions: "m/s", "m s^-2", "kg*m/s^2". Deliberately a small grammar -
# a product of factors, optionally divided by a product of factors.
_FACTOR_RE = re.compile(r"^\s*([A-Za-zΩ°%µ_]+[A-Za-z0-9Ω°%µ_]*|1)\s*(?:\^|\*\*)?\s*(-?\d+(?:/\d+)?)?\s*$")


def _parse_factor(token: str) -> tuple[Unit, Fraction]:
    match = _FACTOR_RE.match(token)
    if not match:
        raise UnitError(f"Could not parse unit factor: {token!r}")
    symbol, exponent = match.group(1), match.group(2)
    unit = _UNITS.get(symbol)
    if unit is None:
        raise UnitError(f"Unknown unit: {symbol!r}")
    power = Fraction(exponent) if exponent else Fraction(1)
    if unit.offset and power != 1:
        # Celsius squared is not a thing anyone means.
        raise UnitError(f"Offset unit {symbol!r} cannot be raised to a power")
    return unit, power


def parse_unit(expression: str) -> Unit:
    """Resolve a unit expression into a single Unit.

    Composite expressions lose the offset by construction: only a bare offset
    unit such as "degC" keeps one, because "degC/s" has no meaningful zero.
    """
    text = str(expression if expression is not None else "").strip()
    if text in _UNITS:
        return _UNITS[text]
    if not text:
        return _UNITS["1"]

    numerator, _, denominator = text.partition("/")
    if "/" in denominator:
        raise UnitError(f"Unit expression has more than one division: {expression!r}")

    dimension = list(DIMENSIONLESS)
    scale = 1.0

    def apply(part: str, sign: int) -> None:
        nonlocal scale
        for token in re.split(r"[*·\s]+", part.strip()):
            if not token:
                continue
            unit, power = _parse_factor(token)
            if unit.offset:
                raise UnitError(f"Offset unit {unit.symbol!r} cannot appear in a composite unit")
            for index, exponent in enumerate(unit.dimension):
                dimension[index] += sign * exponent * power
            scale *= unit.scale ** (sign * float(power))

    apply(numerator, 1)
    if denominator.strip():
        apply(denominator, -1)

    return Unit(symbol=text, dimension=tuple(dimension), scale=scale, offset=0.0, name=text)


def dimension_name(dimension: tuple[Fraction, ...]) -> str:
    """Human-readable dimensionality, e.g. 'length/time²'."""
    if dimension == DIMENSIONLESS:
        return "dimensionless"
    superscript = {"-": "⁻", "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
                   "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "/": "ᐟ"}
    parts = []
    for name, exponent in zip(BASE_DIMENSIONS, dimension):
        if exponent == 0:
            continue
        if exponent == 1:
            parts.append(name)
        else:
            parts.append(name + "".join(superscript.get(ch, ch) for ch in str(exponent)))
    return "·".join(parts)


@dataclass(frozen=True)
class Quantity:
    """A number with a unit, and optionally an absolute uncertainty.

    Stored in SI internally so comparison and arithmetic never depend on how a
    value happened to be written down. `display_unit` remembers the original so
    results can be shown the way the user thinks about them.

    Uncertainty propagates through arithmetic using the standard first-order
    rules, treating inputs as independent. That assumption is stated here
    because it is wrong for correlated inputs, and a reader deserves to know
    which one they are getting.
    """

    si_value: float
    dimension: tuple[Fraction, ...]
    display_unit: str = ""
    si_uncertainty: float | None = None

    # Construction ------------------------------------------------------------
    @classmethod
    def of(cls, value: float, unit: str = "", uncertainty: float | None = None) -> "Quantity":
        resolved = parse_unit(unit)
        si_value = resolved.to_si(float(value))
        si_uncertainty = None
        if uncertainty is not None:
            if float(uncertainty) < 0:
                raise UnitError("Uncertainty cannot be negative")
            # An offset does not shift an interval width, only its centre.
            si_uncertainty = abs(float(uncertainty) * resolved.scale)
        return cls(si_value, resolved.dimension, unit or resolved.symbol, si_uncertainty)

    # Reads -------------------------------------------------------------------
    def to(self, unit: str) -> float:
        """Value expressed in another unit of the same dimension."""
        target = parse_unit(unit)
        if target.dimension != self.dimension:
            raise DimensionalityError(
                f"Cannot express {dimension_name(self.dimension)} as {unit!r} "
                f"({dimension_name(target.dimension)})"
            )
        return target.from_si(self.si_value)

    def uncertainty_in(self, unit: str) -> float | None:
        if self.si_uncertainty is None:
            return None
        target = parse_unit(unit)
        if target.dimension != self.dimension:
            raise DimensionalityError(f"Cannot express uncertainty as {unit!r}")
        return abs(self.si_uncertainty / target.scale)

    @property
    def value(self) -> float:
        """Value in the unit it was written in."""
        return self.to(self.display_unit) if self.display_unit else self.si_value

    @property
    def uncertainty(self) -> float | None:
        return self.uncertainty_in(self.display_unit) if self.display_unit else self.si_uncertainty

    @property
    def relative_uncertainty(self) -> float | None:
        if self.si_uncertainty is None or self.si_value == 0:
            return None
        return abs(self.si_uncertainty / self.si_value)

    def compatible_with(self, other: "Quantity") -> bool:
        return self.dimension == other.dimension

    # Arithmetic --------------------------------------------------------------
    def _require_compatible(self, other: "Quantity", operation: str) -> None:
        if self.dimension != other.dimension:
            raise DimensionalityError(
                f"Cannot {operation} {dimension_name(self.dimension)} and "
                f"{dimension_name(other.dimension)}"
            )

    @staticmethod
    def _combine_absolute(a: float | None, b: float | None) -> float | None:
        if a is None and b is None:
            return None
        return math.hypot(a or 0.0, b or 0.0)

    def __add__(self, other: "Quantity") -> "Quantity":
        self._require_compatible(other, "add")
        return Quantity(self.si_value + other.si_value, self.dimension, self.display_unit,
                        self._combine_absolute(self.si_uncertainty, other.si_uncertainty))

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._require_compatible(other, "subtract")
        return Quantity(self.si_value - other.si_value, self.dimension, self.display_unit,
                        self._combine_absolute(self.si_uncertainty, other.si_uncertainty))

    def __mul__(self, other: "Quantity | float | int") -> "Quantity":
        if isinstance(other, (int, float)):
            scaled = None if self.si_uncertainty is None else abs(self.si_uncertainty * float(other))
            return Quantity(self.si_value * float(other), self.dimension, self.display_unit, scaled)
        dimension = tuple(a + b for a, b in zip(self.dimension, other.dimension))
        value = self.si_value * other.si_value
        return Quantity(value, dimension, "", self._relative_propagated(other, value))

    def __truediv__(self, other: "Quantity | float | int") -> "Quantity":
        if isinstance(other, (int, float)):
            if float(other) == 0:
                raise ZeroDivisionError("Cannot divide a quantity by zero")
            scaled = None if self.si_uncertainty is None else abs(self.si_uncertainty / float(other))
            return Quantity(self.si_value / float(other), self.dimension, self.display_unit, scaled)
        if other.si_value == 0:
            raise ZeroDivisionError("Cannot divide by a zero quantity")
        dimension = tuple(a - b for a, b in zip(self.dimension, other.dimension))
        value = self.si_value / other.si_value
        return Quantity(value, dimension, "", self._relative_propagated(other, value))

    def _relative_propagated(self, other: "Quantity", result: float) -> float | None:
        """Relative uncertainties add in quadrature for products and quotients."""
        left, right = self.relative_uncertainty, other.relative_uncertainty
        if left is None and right is None:
            return None
        return abs(result) * math.hypot(left or 0.0, right or 0.0)

    __rmul__ = __mul__

    # Presentation ------------------------------------------------------------
    def format(self, unit: str | None = None, *, significant: int = 4) -> str:
        unit = unit or self.display_unit or "1"
        value = self.to(unit)
        uncertainty = self.uncertainty_in(unit)
        label = "" if unit in {"1", ""} else f" {unit}"
        if uncertainty is None:
            return f"{value:.{significant}g}{label}"
        return f"{value:.{significant}g} ± {uncertainty:.{max(1, significant - 2)}g}{label}"

    def as_dict(self) -> dict[str, Any]:
        unit = self.display_unit or "1"
        return {
            "value": self.to(unit),
            "unit": unit,
            "uncertainty": self.uncertainty_in(unit),
            "si_value": self.si_value,
            "si_uncertainty": self.si_uncertainty,
            "dimension": dimension_name(self.dimension),
            "dimension_vector": [str(x) for x in self.dimension],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Quantity":
        return cls.of(float(data["value"]), str(data.get("unit") or ""), data.get("uncertainty"))

    def __str__(self) -> str:
        return self.format()


def known_units() -> list[str]:
    return sorted(_UNITS)


def compatible(unit_a: str, unit_b: str) -> bool:
    """Whether two unit expressions describe the same physical dimension."""
    try:
        return parse_unit(unit_a).dimension == parse_unit(unit_b).dimension
    except UnitError:
        return False
