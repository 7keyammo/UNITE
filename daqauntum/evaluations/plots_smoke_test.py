"""Regression guard for the plotting layer.

A chart is the part of a scientific record most likely to be screenshotted and
pasted where its provenance does not follow. So the property these tests
defend hardest is that the data note is rendered into the image itself, and
that a plot of simulated values is never recorded as stronger evidence than
the values it draws.

The rest is arithmetic and output hygiene: valid XML, no unescaped user text,
axes that survive degenerate input, and no crash on a flat series.
"""
from __future__ import annotations

import math
import os
import tempfile
import xml.etree.ElementTree as ET

from memory.store import MemoryStore
from science.analysis import analyze_motion
from science.core import ScienceCore
from science.models import EvidenceKind, Measurement, Provenance, ProvenanceKind
from science.plots import (
    Plot,
    PlotError,
    Series,
    _bounds,
    _nice_step,
    position_time_plot,
    render_svg,
    residual_plot,
    velocity_time_plot,
    write_svg,
)
from science.sensors import MockCartSensor

CART_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]
CART_POSITIONS = [0.0, 1.0, 2.1, 3.3, 4.6]


def _measurements(simulated: bool = False) -> tuple[list[Measurement], list[Measurement]]:
    provenance = (Provenance(ProvenanceKind.SIMULATED, agent="mock")
                  if simulated else Provenance.sensor("lab.rangefinder"))
    times = [Measurement(quantity="time", value=t, unit="s", uncertainty=0.01,
                         timestamp=t, provenance=provenance) for t in CART_TIMES]
    positions = [Measurement(quantity="position", value=x, unit="m", uncertainty=0.01,
                             timestamp=t, provenance=provenance)
                 for t, x in zip(CART_TIMES, CART_POSITIONS)]
    return times, positions


def _core(tmp: str) -> ScienceCore:
    return ScienceCore(MemoryStore(os.path.join(tmp, "plots.db")),
                       artifacts_dir=os.path.join(tmp, "artifacts"))


def test_svg_is_valid_and_self_contained() -> None:
    times, positions = _measurements()
    svg = render_svg(position_time_plot(times, positions))
    root = ET.fromstring(svg)          # raises if malformed
    assert root.tag.endswith("svg")
    # No external references: the file has to render with no network and no fonts.
    assert "http://" not in svg.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "<image" not in svg and "@import" not in svg
    assert "position (m)" in svg and "time (s)" in svg


def test_simulated_data_is_labelled_on_the_image() -> None:
    """Metadata does not survive a screenshot. The pixels have to say it."""
    times, positions = _measurements(simulated=True)
    svg = render_svg(position_time_plot(times, positions))
    assert "SIMULATED DATA" in svg
    assert "not measured" in svg

    real_svg = render_svg(position_time_plot(*_measurements(simulated=False)))
    assert "SIMULATED" not in real_svg

    # A mixed set says so rather than rounding to either answer.
    times, positions = _measurements(simulated=False)
    positions[0].provenance = Provenance(ProvenanceKind.SIMULATED, agent="mock")
    mixed = render_svg(position_time_plot(times, positions))
    assert "MIXED DATA" in mixed and "1 of 10" in mixed


def test_a_plot_is_never_stronger_evidence_than_its_data() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        core = _core(tmp)
        experiment = core.create_experiment("Simulated cart")
        t, x = MockCartSensor(noise=0.0).sample(5)
        core.ingest_readings(experiment.id, t)
        core.ingest_readings(experiment.id, x)
        analysis, _ = core.analyse_motion(experiment.id)

        saved = core.plot_motion(experiment.id, analysis, include_residuals=True)
        assert len(saved) == 3
        for path, evidence in saved:
            assert path.exists() and path.suffix == ".svg"
            # Simulated input, so the plot is SIMULATION evidence - not
            # MEASUREMENT, and never empirical.
            assert evidence.kind is EvidenceKind.SIMULATION
            assert not evidence.is_empirical
            assert evidence.artifact_path == str(path)
            assert "SIMULATED DATA" in path.read_text(encoding="utf-8")

        # Real readings produce a calculation-grade plot instead.
        real = core.create_experiment("Real cart")
        times, positions = _measurements(simulated=False)
        for m in times + positions:
            m.experiment_id = real.id
            core.store.save_measurement(m)
        real_analysis, _ = core.analyse_motion(real.id)
        (_, real_evidence), *_ = core.plot_motion(real.id, real_analysis)
        assert real_evidence.kind is EvidenceKind.CALCULATION


def test_plots_are_drawn_from_the_analysed_values() -> None:
    """The chart and the numbers beside it must come from one set of readings."""
    times, positions = _measurements()
    analysis = analyze_motion(times, positions)
    plot = position_time_plot(times, positions, analysis=analysis)

    assert plot.series[0].xs == CART_TIMES
    assert plot.series[0].ys == CART_POSITIONS
    assert set(plot.metadata["measurement_ids"]) == {m.id for m in times + positions}

    # The fit line is the analysis's own slope and intercept, not a refit.
    fit_series = plot.series[1]
    slope, intercept = analysis.fit["slope"], analysis.fit["intercept"]
    assert all(math.isclose(y, slope * x + intercept, rel_tol=1e-12)
               for x, y in zip(fit_series.xs, fit_series.ys))
    assert f"{analysis.fit['r_squared']:.4f}" in fit_series.label


def test_interval_velocities_are_drawn_at_their_midpoints() -> None:
    """An interval velocity is an average over a span, not a value at an instant."""
    times, positions = _measurements()
    analysis = analyze_motion(times, positions)
    plot = velocity_time_plot(analysis)

    assert plot.series[0].xs == [0.5, 1.5, 2.5, 3.5]
    assert [round(y, 10) for y in plot.series[0].ys] == [1.0, 1.1, 1.2, 1.3]
    assert "midpoint" in plot.caption

    # The average line spans the data and is labelled with its value.
    average = plot.series[1]
    assert average.ys == [analysis.average_velocity.value] * 2
    assert "1.15" in average.label


def test_degenerate_input_does_not_crash_or_mislead() -> None:
    # A flat series gets a readable axis rather than a zero-height box.
    low, high = _bounds([2.0, 2.0, 2.0])
    assert low < 2.0 < high

    # A flat series at zero still produces a finite range.
    low, high = _bounds([0.0, 0.0])
    assert low < high and math.isfinite(low) and math.isfinite(high)

    flat = Plot(title="Flat", x_label="t (s)", y_label="x (m)",
                series=[Series("x", [0.0, 1.0, 2.0], [1.0, 1.0, 1.0])])
    ET.fromstring(render_svg(flat))

    assert _nice_step(0.0) == 1.0
    assert _nice_step(10.0) in (1.0, 2.0, 5.0, 10.0)

    # Mismatched series lengths are caught at construction.
    try:
        Series("s", [0.0, 1.0], [1.0])
    except PlotError as exc:
        assert "1 y values" in str(exc), exc
    else:
        raise AssertionError("Accepted a series with mismatched lengths")

    # An empty plot is refused rather than rendered as blank axes, which would
    # look like a result showing nothing rather than no result at all.
    try:
        render_svg(Plot(title="Empty", x_label="t", y_label="x"))
    except PlotError as exc:
        assert "at least one series" in str(exc), exc
    else:
        raise AssertionError("Rendered a plot with no data")

    try:
        position_time_plot(_measurements()[0], _measurements()[1][:-1])
    except PlotError as exc:
        assert "5 time values and 4 position values" in str(exc), exc
    else:
        raise AssertionError("Plotted mismatched series")


def test_titles_are_escaped_not_injected() -> None:
    """A title is text. It must not become markup in the output file."""
    plot = Plot(
        title='Run <script>alert("x")</script> & more',
        x_label="t & u", y_label="x < y",
        series=[Series("a & b", [0.0, 1.0], [0.0, 1.0])],
        data_note="note & <b>bold</b>",
    )
    svg = render_svg(plot)
    ET.fromstring(svg)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg and "&amp;" in svg


def test_written_file_matches_the_rendered_string() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        times, positions = _measurements()
        plot = position_time_plot(times, positions)
        target = write_svg(plot, os.path.join(tmp, "nested", "dir", "plot.svg"))
        assert target.exists()
        assert target.read_text(encoding="utf-8") == render_svg(plot)


def test_residual_plot_explains_what_it_shows() -> None:
    times, positions = _measurements()
    analysis = analyze_motion(times, positions)
    plot = residual_plot(analysis, times)
    assert plot.series[0].ys == analysis.fit["residuals"]
    assert plot.series[1].ys == [0.0, 0.0], "zero line missing"
    assert "wrong model" in plot.caption

    empty = analyze_motion(*_measurements())
    empty.fit = {}
    try:
        residual_plot(empty, times)
    except PlotError as exc:
        assert "residuals" in str(exc), exc
    else:
        raise AssertionError("Plotted residuals that do not exist")


def main() -> None:
    test_svg_is_valid_and_self_contained()
    test_simulated_data_is_labelled_on_the_image()
    test_a_plot_is_never_stronger_evidence_than_its_data()
    test_plots_are_drawn_from_the_analysed_values()
    test_interval_velocities_are_drawn_at_their_midpoints()
    test_degenerate_input_does_not_crash_or_mislead()
    test_titles_are_escaped_not_injected()
    test_written_file_matches_the_rendered_string()
    test_residual_plot_explains_what_it_shows()
    print("DaQauntum v0.5 plotting smoke test: PASS")


if __name__ == "__main__":
    main()
