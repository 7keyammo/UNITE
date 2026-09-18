"""Plotting: position against time, velocity against time, residuals.

SVG is written directly, with no plotting library. Three reasons, in order of
weight:

1. A plot is evidence in this system. An artifact that renders identically on
   any machine, with no backend to configure and no headless-display problem,
   is one less way for a record to become unreproducible.
2. SVG is text, so a plot diffs, greps and survives in git like the rest of
   the record.
3. matplotlib is a heavy dependency for a core that is otherwise dependency
   free. Where it is installed, `render_matplotlib` uses it - but nothing here
   needs it.

Every plot states in its own caption where its data came from. A chart of
simulated values is labelled on the face of the image, not only in metadata,
because a plot is the part of a record most likely to be screenshotted and
pasted somewhere the metadata does not follow.
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from science.analysis import MotionAnalysis
from science.models import (
    Evidence,
    EvidenceKind,
    Measurement,
    Provenance,
    ProvenanceKind,
)


class PlotError(ValueError):
    """Raised when data cannot be plotted honestly."""


@dataclass
class Series:
    """One line or scatter on a plot."""

    label: str
    xs: list[float]
    ys: list[float]
    colour: str = "#2b6cb0"
    style: str = "line"          # line | scatter | both
    dashed: bool = False
    y_errors: list[float] | None = None

    def __post_init__(self) -> None:
        if len(self.xs) != len(self.ys):
            raise PlotError(
                f"{self.label!r}: {len(self.xs)} x values and {len(self.ys)} y values"
            )
        if self.y_errors is not None and len(self.y_errors) != len(self.ys):
            raise PlotError(f"{self.label!r}: one error bar per point, or none")


@dataclass
class Plot:
    """A finished chart plus the statements that keep it honest."""

    title: str
    x_label: str
    y_label: str
    series: list[Series] = field(default_factory=list)
    caption: str = ""
    data_note: str = ""          # rendered on the image itself
    width: int = 720
    height: int = 420
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_evidence(self, experiment_id: str, artifact_path: str,
                    kind: EvidenceKind = EvidenceKind.CALCULATION) -> Evidence:
        """A plot is evidence of the kind its data was, never stronger."""
        return Evidence(
            kind=kind,
            statement=f"Plot: {self.title}",
            experiment_id=experiment_id,
            artifact_path=str(artifact_path),
            provenance=Provenance(
                ProvenanceKind.CALCULATED,
                agent="daqauntum.science.plots",
                method="SVG rendering of recorded values",
                inputs=list(self.metadata.get("measurement_ids", [])),
            ),
            metadata={"caption": self.caption, "data_note": self.data_note, **self.metadata},
        )


# Geometry --------------------------------------------------------------------
def _nice_step(span: float, target_ticks: int = 5) -> float:
    """A round tick interval near span/target_ticks - 1, 2 or 5 times a power of ten."""
    if span <= 0:
        return 1.0
    raw = span / max(1, target_ticks)
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 5, 10):
        if raw <= multiple * magnitude:
            return multiple * magnitude
    return 10 * magnitude


def _bounds(values: Sequence[float], errors: Sequence[float] | None = None) -> tuple[float, float]:
    if not values:
        raise PlotError("Cannot compute bounds of an empty series")
    if errors:
        low = min(v - abs(e) for v, e in zip(values, errors))
        high = max(v + abs(e) for v, e in zip(values, errors))
    else:
        low, high = min(values), max(values)
    if low == high:
        # A flat series still deserves a readable axis rather than a
        # zero-height box. Pad around the value instead of stretching it.
        padding = abs(low) * 0.1 or 1.0
        return low - padding, high + padding
    margin = (high - low) * 0.08
    return low - margin, high + margin


def _format_tick(value: float) -> str:
    if value == 0:
        return "0"
    if abs(value) >= 10_000 or abs(value) < 0.001:
        return f"{value:.2e}"
    text = f"{value:.4g}"
    return text


# Rendering -------------------------------------------------------------------
def render_svg(plot: Plot) -> str:
    """Render to a standalone SVG string."""
    if not plot.series:
        raise PlotError("A plot needs at least one series")

    left, right, top, bottom = 72, 24, 56, 92
    inner_w = plot.width - left - right
    inner_h = plot.height - top - bottom
    if inner_w <= 0 or inner_h <= 0:
        raise PlotError("Plot area is too small for its margins")

    all_x = [x for s in plot.series for x in s.xs]
    all_y = [y for s in plot.series for y in s.ys]
    all_err = [e for s in plot.series if s.y_errors for e in s.y_errors] or None
    x_min, x_max = _bounds(all_x)
    y_min, y_max = _bounds(all_y, all_err if all_err else None)

    def px(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * inner_w

    def py(value: float) -> float:
        return top + inner_h - (value - y_min) / (y_max - y_min) * inner_h

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{plot.width}" '
        f'height="{plot.height}" viewBox="0 0 {plot.width} {plot.height}" '
        f'role="img" aria-label="{html.escape(plot.title)}">',
        '<style>'
        'text{font-family:system-ui,-apple-system,Segoe UI,sans-serif;fill:#1a202c}'
        '.title{font-size:16px;font-weight:600}'
        '.axis{font-size:11px;fill:#4a5568}'
        '.axis-label{font-size:12px;fill:#2d3748}'
        '.caption{font-size:11px;fill:#4a5568}'
        '.note{font-size:11px;font-weight:600;fill:#975a16}'
        '.grid{stroke:#e2e8f0;stroke-width:1}'
        '.frame{stroke:#a0aec0;stroke-width:1;fill:none}'
        '</style>',
        f'<rect width="{plot.width}" height="{plot.height}" fill="#ffffff"/>',
        f'<text class="title" x="{left}" y="26">{html.escape(plot.title)}</text>',
    ]

    # Grid and ticks.
    x_step = _nice_step(x_max - x_min)
    y_step = _nice_step(y_max - y_min)
    tick = math.ceil(x_min / x_step) * x_step
    while tick <= x_max + x_step * 1e-9:
        x = px(tick)
        parts.append(f'<line class="grid" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + inner_h}"/>')
        parts.append(f'<text class="axis" x="{x:.2f}" y="{top + inner_h + 16}" '
                     f'text-anchor="middle">{_format_tick(tick)}</text>')
        tick += x_step
    tick = math.ceil(y_min / y_step) * y_step
    while tick <= y_max + y_step * 1e-9:
        y = py(tick)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{left + inner_w}" y2="{y:.2f}"/>')
        parts.append(f'<text class="axis" x="{left - 8}" y="{y + 4:.2f}" '
                     f'text-anchor="end">{_format_tick(tick)}</text>')
        tick += y_step

    parts.append(f'<rect class="frame" x="{left}" y="{top}" width="{inner_w}" height="{inner_h}"/>')

    # Series.
    for series in plot.series:
        points = [(px(x), py(y)) for x, y in zip(series.xs, series.ys)]
        if series.y_errors:
            for (x, _), y_value, error in zip(points, series.ys, series.y_errors):
                if not error:
                    continue
                top_y, bottom_y = py(y_value + abs(error)), py(y_value - abs(error))
                parts.append(
                    f'<line x1="{x:.2f}" y1="{top_y:.2f}" x2="{x:.2f}" y2="{bottom_y:.2f}" '
                    f'stroke="{series.colour}" stroke-width="1" opacity="0.65"/>'
                    f'<line x1="{x - 3:.2f}" y1="{top_y:.2f}" x2="{x + 3:.2f}" y2="{top_y:.2f}" '
                    f'stroke="{series.colour}" stroke-width="1" opacity="0.65"/>'
                    f'<line x1="{x - 3:.2f}" y1="{bottom_y:.2f}" x2="{x + 3:.2f}" y2="{bottom_y:.2f}" '
                    f'stroke="{series.colour}" stroke-width="1" opacity="0.65"/>'
                )
        if series.style in {"line", "both"} and len(points) > 1:
            path = " ".join(f"{'M' if i == 0 else 'L'}{x:.2f},{y:.2f}"
                            for i, (x, y) in enumerate(points))
            dash = ' stroke-dasharray="6 4"' if series.dashed else ""
            parts.append(f'<path d="{path}" fill="none" stroke="{series.colour}" '
                         f'stroke-width="2"{dash}/>')
        if series.style in {"scatter", "both"}:
            for x, y in points:
                parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="3.5" '
                             f'fill="{series.colour}"/>')

    # Axis labels.
    parts.append(f'<text class="axis-label" x="{left + inner_w / 2:.0f}" '
                 f'y="{top + inner_h + 36}" text-anchor="middle">{html.escape(plot.x_label)}</text>')
    parts.append(f'<text class="axis-label" transform="translate(18,{top + inner_h / 2:.0f}) '
                 f'rotate(-90)" text-anchor="middle">{html.escape(plot.y_label)}</text>')

    # Legend.
    legend_x = left
    legend_y = top + inner_h + 54
    for series in plot.series:
        dash = ' stroke-dasharray="6 4"' if series.dashed else ""
        parts.append(f'<line x1="{legend_x}" y1="{legend_y - 4}" x2="{legend_x + 18}" '
                     f'y2="{legend_y - 4}" stroke="{series.colour}" stroke-width="2"{dash}/>')
        parts.append(f'<text class="caption" x="{legend_x + 24}" y="{legend_y}">'
                     f'{html.escape(series.label)}</text>')
        legend_x += 28 + 7 * len(series.label)

    # The data note goes on the image itself, where a screenshot carries it.
    if plot.data_note:
        parts.append(f'<text class="note" x="{left}" y="{plot.height - 10}">'
                     f'{html.escape(plot.data_note)}</text>')
    elif plot.caption:
        parts.append(f'<text class="caption" x="{left}" y="{plot.height - 10}">'
                     f'{html.escape(plot.caption)}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def write_svg(plot: Plot, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_svg(plot), encoding="utf-8")
    return target


def render_matplotlib(plot: Plot, path: str | Path) -> Path:
    """Render with matplotlib when it is installed.

    Optional on purpose. The SVG renderer is the reference output; this exists
    for users who want a PNG for a document.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise PlotError(
            "matplotlib is not installed. Use write_svg, or "
            "pip install -r requirements-science.txt"
        ) from exc

    figure, axes = plt.subplots(figsize=(plot.width / 100, plot.height / 100), dpi=100)
    for series in plot.series:
        if series.y_errors:
            axes.errorbar(series.xs, series.ys, yerr=series.y_errors, label=series.label,
                          color=series.colour, capsize=3,
                          fmt="o-" if series.style == "both" else "o"
                          if series.style == "scatter" else "-")
        else:
            axes.plot(series.xs, series.ys, label=series.label, color=series.colour,
                      linestyle="--" if series.dashed else "-",
                      marker="o" if series.style in {"scatter", "both"} else None)
    axes.set_title(plot.title)
    axes.set_xlabel(plot.x_label)
    axes.set_ylabel(plot.y_label)
    axes.grid(True, alpha=0.3)
    axes.legend()
    if plot.data_note:
        figure.text(0.01, 0.01, plot.data_note, fontsize=8, color="#975a16")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, bbox_inches="tight")
    plt.close(figure)
    return target


# Builders --------------------------------------------------------------------
def _data_note(measurements: Sequence[Measurement]) -> str:
    """The sentence printed on the plot describing where the data came from."""
    simulated = sum(1 for m in measurements if m.is_simulated)
    derived = sum(1 for m in measurements if m.derived)
    if simulated and simulated == len(measurements):
        return "SIMULATED DATA - generated from a model, not measured."
    if simulated:
        return f"MIXED DATA - {simulated} of {len(measurements)} values were simulated."
    if derived:
        return f"Includes {derived} calculated values."
    return ""


def position_time_plot(
    times: Sequence[Measurement],
    positions: Sequence[Measurement],
    *,
    analysis: MotionAnalysis | None = None,
    title: str = "Position against time",
) -> Plot:
    """Position-time scatter, with the least-squares line when one was fitted."""
    if len(times) != len(positions):
        raise PlotError(
            f"{len(times)} time values and {len(positions)} position values"
        )
    if not times:
        raise PlotError("Nothing to plot")

    xs = [m.as_quantity.to("s") for m in times]
    ys = [m.as_quantity.to("m") for m in positions]
    errors = [m.as_quantity.uncertainty_in("m") or 0.0 for m in positions]

    series = [Series("measured position", xs, ys, style="scatter",
                     y_errors=errors if any(errors) else None)]
    if analysis and analysis.fit:
        slope = analysis.fit["slope"]
        intercept = analysis.fit["intercept"]
        fitted = [slope * x + intercept for x in xs]
        r_squared = analysis.fit.get("r_squared")
        label = f"least-squares fit (r² = {r_squared:.4f})" if r_squared is not None else "fit"
        series.append(Series(label, xs, fitted, colour="#c05621", style="line", dashed=True))

    return Plot(
        title=title,
        x_label="time (s)",
        y_label="position (m)",
        series=series,
        caption="Error bars show the recorded measurement uncertainty.",
        data_note=_data_note(list(times) + list(positions)),
        metadata={"measurement_ids": [m.id for m in times] + [m.id for m in positions]},
    )


def velocity_time_plot(
    analysis: MotionAnalysis,
    *,
    title: str = "Velocity against time",
) -> Plot:
    """Interval velocities at their midpoint times, with the average line.

    Plotted at the midpoint because an interval velocity is an average over a
    span, not a value at an instant, and placing it at either end would imply
    a precision the calculation does not have.
    """
    if not analysis.interval_velocities:
        raise PlotError("No interval velocities to plot")

    xs = [(r.metadata["t_start"] + r.metadata["t_end"]) / 2 for r in analysis.interval_velocities]
    ys = [r.value for r in analysis.interval_velocities]
    errors = [r.quantity.uncertainty or 0.0 for r in analysis.interval_velocities]

    series = [Series("interval velocity", xs, ys, colour="#2f855a", style="both",
                     y_errors=errors if any(errors) else None)]
    if analysis.average_velocity is not None:
        mean = analysis.average_velocity.value
        series.append(Series(f"average ({mean:.4g} m/s)", [xs[0], xs[-1]], [mean, mean],
                             colour="#718096", style="line", dashed=True))

    note = ""
    for warning in analysis.warnings:
        if "simulated" in warning.lower():
            note = "SIMULATED DATA - generated from a model, not measured."
            break
    return Plot(
        title=title,
        x_label="time (s)",
        y_label="velocity (m/s)",
        series=series,
        caption=("Each point is an average over its interval, drawn at the interval "
                 "midpoint."),
        data_note=note,
        metadata={"measurement_ids": sorted({i for r in analysis.interval_velocities
                                             for i in r.inputs})},
    )


def residual_plot(analysis: MotionAnalysis, times: Sequence[Measurement],
                  *, title: str = "Fit residuals") -> Plot:
    """Residuals against time - where the straight line fails to describe the motion."""
    residuals = (analysis.fit or {}).get("residuals")
    if not residuals:
        raise PlotError("No fit residuals available")
    xs = [m.as_quantity.to("s") for m in times][:len(residuals)]
    return Plot(
        title=title,
        x_label="time (s)",
        y_label="residual (m)",
        series=[
            Series("residual", xs, list(residuals), colour="#9b2c2c", style="both"),
            Series("zero", [xs[0], xs[-1]], [0.0, 0.0], colour="#a0aec0", dashed=True),
        ],
        caption=("Structure in the residuals means a straight line is the wrong model; "
                 "scatter with no pattern means it is adequate."),
        data_note=_data_note(list(times)),
        metadata={"measurement_ids": [m.id for m in times]},
    )
