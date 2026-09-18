"""Deterministic scientific analysis.

Every result carries the equation that produced it, the inputs it consumed and
its provenance, so a user can check the arithmetic rather than trust it. No
model is involved in any calculation here.
"""

from science.analysis.motion import (
    AnalysisError,
    AnalysisResult,
    MotionAnalysis,
    analyze_motion,
    average_velocity,
    interval_velocities,
)

__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "MotionAnalysis",
    "analyze_motion",
    "average_velocity",
    "interval_velocities",
]
