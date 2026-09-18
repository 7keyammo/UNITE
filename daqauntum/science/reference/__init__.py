"""Reference experiments: complete worked investigations, end to end.

Each one runs the whole pipeline on data whose origin is stated plainly, so a
new user can see what a finished record looks like and a developer has a
regression fixture that exercises every layer at once.
"""

from science.reference.cart_motion import (
    EXAMPLE_POSITIONS,
    EXAMPLE_TIMES,
    CartMotionExperiment,
    run_cart_motion,
)

__all__ = [
    "EXAMPLE_POSITIONS",
    "EXAMPLE_TIMES",
    "CartMotionExperiment",
    "run_cart_motion",
]
