"""A registry of physical quantities and the relations between them.

Each concept records its SI dimension, its usual symbol and unit, and a plain
explanation. Each relation records an equation, the quantities it connects and
the conditions under which it holds.

Two things this is not.

It is not a formula solver. The equations are stored as text, checked for
dimensional consistency, and shown to a person. Nothing here rearranges an
equation and substitutes values, because a relation that holds only under
stated conditions cannot be applied safely by a system that does not know
whether those conditions were met.

It is not a curriculum. `Depth` exists on the explanations so an education
layer can be built later, but v0.5 only carries the field.

The conditions on each relation are the part most worth reading. "v = Δx/Δt"
is not always true; it is true for average velocity over an interval, and
saying so is the difference between a reference and a source of quiet errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from science.units import DimensionalityError, dimension_name, parse_unit


class ConceptError(ValueError):
    """Raised when a concept or relation is inconsistent."""


@dataclass(frozen=True)
class Concept:
    """One physical quantity."""

    key: str
    name: str
    symbol: str
    si_unit: str
    summary: str
    aliases: tuple[str, ...] = ()
    common_units: tuple[str, ...] = ()
    notes: str = ""
    depth: str = "introductory"

    def __post_init__(self) -> None:
        parse_unit(self.si_unit)
        for unit in self.common_units:
            if not self.compatible_with_unit(unit):
                raise ConceptError(
                    f"{self.key}: {unit!r} is not a unit of {self.dimension_name}"
                )

    @property
    def dimension(self) -> tuple:
        return parse_unit(self.si_unit).dimension

    @property
    def dimension_name(self) -> str:
        return dimension_name(self.dimension)

    def compatible_with_unit(self, unit: str) -> bool:
        try:
            return parse_unit(unit).dimension == self.dimension
        except Exception:
            return False

    def matches(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        return lowered in {self.key, self.name.lower(), self.symbol.lower(),
                           *(a.lower() for a in self.aliases)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "name": self.name, "symbol": self.symbol,
            "si_unit": self.si_unit, "dimension": self.dimension_name,
            "summary": self.summary, "aliases": list(self.aliases),
            "common_units": list(self.common_units), "notes": self.notes,
            "depth": self.depth,
        }


@dataclass(frozen=True)
class Relation:
    """An equation, and the conditions under which it is true."""

    key: str
    equation: str
    result: str                       # concept key the equation yields
    inputs: tuple[str, ...] = ()      # concept keys it consumes
    conditions: tuple[str, ...] = ()  # when it holds - never empty in practice
    summary: str = ""
    depth: str = "introductory"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key, "equation": self.equation, "result": self.result,
            "inputs": list(self.inputs), "conditions": list(self.conditions),
            "summary": self.summary, "depth": self.depth,
        }


CONCEPTS: dict[str, Concept] = {}
RELATIONS: dict[str, Relation] = {}


def _register(concept_obj: Concept) -> Concept:
    if concept_obj.key in CONCEPTS:
        raise ConceptError(f"Duplicate concept {concept_obj.key!r}")
    CONCEPTS[concept_obj.key] = concept_obj
    return concept_obj


def _relate(relation: Relation) -> Relation:
    if relation.key in RELATIONS:
        raise ConceptError(f"Duplicate relation {relation.key!r}")
    for key in (relation.result, *relation.inputs):
        if key not in CONCEPTS:
            raise ConceptError(f"{relation.key!r} references unknown concept {key!r}")
    if not relation.conditions:
        raise ConceptError(
            f"{relation.key!r} states no conditions. Every relation holds only "
            "sometimes; recording when is the point of this registry."
        )
    RELATIONS[relation.key] = relation
    return relation


# Kinematics ------------------------------------------------------------------
_register(Concept(
    key="time", name="Time", symbol="t", si_unit="s",
    summary="How long something takes, or when it happened.",
    aliases=("duration", "elapsed time"),
    common_units=("s", "ms", "min", "h"),
))
_register(Concept(
    key="position", name="Position", symbol="x", si_unit="m",
    summary="Where an object is, measured from a stated origin.",
    aliases=("displacement from origin", "distance from origin"),
    common_units=("m", "cm", "mm", "km"),
    notes="Position is relative to an origin. A position with no stated origin is incomplete.",
))
_register(Concept(
    key="displacement", name="Displacement", symbol="Δx", si_unit="m",
    summary="The change in position: where it ended minus where it started.",
    common_units=("m", "cm", "km"),
    notes=("Not the same as distance travelled. A cart that returns to its start has "
           "zero displacement and a non-zero path length."),
))
_register(Concept(
    key="velocity", name="Velocity", symbol="v", si_unit="m/s",
    summary="How fast position changes, and in which direction.",
    aliases=("speed",),
    common_units=("m/s", "km/h"),
    notes=("Speed is the magnitude of velocity. They are used interchangeably in "
           "casual speech and are not the same quantity."),
))
_register(Concept(
    key="acceleration", name="Acceleration", symbol="a", si_unit="m/s^2",
    summary="How fast velocity changes.",
    common_units=("m/s^2",),
    notes="An object can accelerate while slowing down; the sign carries the direction.",
))
_register(Concept(
    key="mass", name="Mass", symbol="m", si_unit="kg",
    summary="How much matter an object contains.",
    common_units=("kg", "g"),
    notes="Mass is not weight. Weight is a force and depends on local gravity.",
))
_register(Concept(
    key="force", name="Force", symbol="F", si_unit="N",
    summary="A push or pull that changes an object's motion.",
    common_units=("N",),
    depth="introductory",
))
_register(Concept(
    key="momentum", name="Momentum", symbol="p", si_unit="kg*m/s",
    summary="Mass in motion: how hard it is to stop something.",
    depth="intermediate",
))
_register(Concept(
    key="energy", name="Energy", symbol="E", si_unit="J",
    summary="The capacity to do work.",
    aliases=("work", "kinetic energy", "potential energy"),
    common_units=("J",),
))
_register(Concept(
    key="power", name="Power", symbol="P", si_unit="W",
    summary="How fast energy is transferred.",
    common_units=("W",),
))
_register(Concept(
    key="temperature", name="Temperature", symbol="T", si_unit="K",
    summary="How hot something is, on an absolute scale.",
    common_units=("K", "degC", "degF"),
    notes=("Celsius and Fahrenheit have offset zeros, so a temperature converts "
           "differently from a temperature difference."),
))


_relate(Relation(
    key="average_velocity",
    equation="v_avg = Δx / Δt",
    result="velocity", inputs=("displacement", "time"),
    conditions=(
        "Gives the average over the interval, not the velocity at any instant.",
        "Δt must be non-zero.",
        "Uses displacement, not path length, so it is zero for a round trip.",
    ),
    summary="Average velocity over an interval.",
))
_relate(Relation(
    key="average_acceleration",
    equation="a_avg = Δv / Δt",
    result="acceleration", inputs=("velocity", "time"),
    conditions=(
        "Gives the average over the interval.",
        "Δt must be non-zero.",
    ),
    summary="Average acceleration over an interval.",
))
_relate(Relation(
    key="uniform_acceleration_position",
    equation="x(t) = x0 + v0*t + 0.5*a*t^2",
    result="position", inputs=("position", "velocity", "acceleration", "time"),
    conditions=(
        "Acceleration must be constant over the whole interval.",
        "Motion is treated as one-dimensional.",
        "Does not hold where drag or friction varies with speed.",
    ),
    summary="Position under constant acceleration.",
    depth="intermediate",
))
_relate(Relation(
    key="newton_second_law",
    equation="F = m*a",
    result="force", inputs=("mass", "acceleration"),
    conditions=(
        "F is the net force - the sum of every force acting.",
        "Mass must be constant; it does not hold for a rocket losing fuel.",
        "Speeds must be well below the speed of light.",
    ),
    summary="Net force equals mass times acceleration.",
))
_relate(Relation(
    key="momentum_definition",
    equation="p = m*v",
    result="momentum", inputs=("mass", "velocity"),
    conditions=("Non-relativistic speeds.",),
    summary="Momentum is mass times velocity.",
    depth="intermediate",
))
_relate(Relation(
    key="kinetic_energy",
    equation="E_k = 0.5*m*v^2",
    result="energy", inputs=("mass", "velocity"),
    conditions=(
        "Non-relativistic speeds.",
        "Measured in the frame in which v was measured; kinetic energy is frame-dependent.",
    ),
    summary="Kinetic energy of a moving mass.",
))
_relate(Relation(
    key="power_definition",
    equation="P = E / t",
    result="power", inputs=("energy", "time"),
    conditions=("Gives average power over the interval.", "t must be non-zero."),
    summary="Power is energy transferred per unit time.",
))


# Lookup ----------------------------------------------------------------------
def concept(key: str) -> Concept:
    """Look a concept up by key, raising rather than returning None."""
    found = CONCEPTS.get(str(key).strip().lower())
    if found is None:
        raise ConceptError(f"Unknown concept {key!r}")
    return found


def find_concept(text: str) -> Concept | None:
    """Find a concept by key, name, symbol or alias. Returns None if unknown.

    Deliberately exact rather than fuzzy. A near-match that silently picks the
    wrong quantity would attach the wrong dimension to a measurement, which is
    worse than not finding one at all.
    """
    for candidate in CONCEPTS.values():
        if candidate.matches(text):
            return candidate
    return None


def concepts_with_dimension(unit: str) -> list[Concept]:
    """Every concept measured in a unit of this dimension."""
    try:
        target = parse_unit(unit).dimension
    except Exception as exc:
        raise ConceptError(f"Cannot parse {unit!r}") from exc
    return [c for c in CONCEPTS.values() if c.dimension == target]


def relations_for(concept_key: str) -> list[Relation]:
    """Every relation that produces or consumes this concept."""
    key = str(concept_key).strip().lower()
    return [r for r in RELATIONS.values() if key == r.result or key in r.inputs]


def registry_summary() -> dict[str, Any]:
    return {
        "concepts": len(CONCEPTS),
        "relations": len(RELATIONS),
        "dimensions": sorted({c.dimension_name for c in CONCEPTS.values()}),
        "note": ("A reference, not a solver. Equations are stored as text with their "
                 "conditions and shown to a person; nothing here substitutes values."),
    }
