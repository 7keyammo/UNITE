"""DaQauntum v0.5 Scientific Core.

The scientific layer owns the workflow of investigation: questions, hypotheses,
measurements, analysis, evidence and claims. Its defining job is to keep those
kinds of knowledge distinguishable, so a user can always tell what was measured
from what was calculated, simulated, read in the literature, or inferred by an
AI.

External systems - language models, simulators, sensors, research platforms -
sit behind adapters. The core depends on none of them.
"""

from science.core import ScienceCore, ScienceError
from science.units import (
    BASE_DIMENSIONS,
    DimensionalityError,
    Quantity,
    Unit,
    UnitError,
    compatible,
    dimension_name,
    known_units,
    parse_unit,
)

__all__ = [
    "BASE_DIMENSIONS",
    "ScienceCore",
    "ScienceError",
    "DimensionalityError",
    "Quantity",
    "Unit",
    "UnitError",
    "compatible",
    "dimension_name",
    "known_units",
    "parse_unit",
]
