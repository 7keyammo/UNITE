"""Regression guard for sensor backends and the ingestion path.

The risk this suite exists for: a mock sensor produces numbers that look
exactly like real ones. Realistic generated data flowing into the record as a
measurement would break the single distinction the whole system is built on,
and it would break it invisibly - nothing downstream would look wrong.

So most of these tests are about the label surviving: backend -> reading ->
measurement -> evidence_kind -> storage -> reload -> analysis warnings.
"""
from __future__ import annotations

import math
import os
import tempfile

from memory.store import MemoryStore
from science.core import ScienceCore, ScienceError
from science.models import EvidenceKind, ProvenanceKind
from science.sensors import (
    MockCartSensor,
    SensorBackend,
    SensorError,
    SensorReading,
    StaticSensor,
    rms_error,
)
from science.store import ScienceStore


def _core() -> tuple[ScienceCore, str]:
    path = os.path.join(tempfile.mkdtemp(), "sensors.db")
    return ScienceCore(MemoryStore(path)), path


def test_mock_sensor_follows_its_own_model() -> None:
    """x(t) = x0 + v0*t + a*t^2/2, checked against the arithmetic."""
    sensor = MockCartSensor(initial_position=0.0, initial_velocity=1.0,
                            acceleration=0.1, interval=1.0, noise=0.0)
    times, positions = sensor.sample(5)

    assert [r.value for r in times] == [0.0, 1.0, 2.0, 3.0, 4.0]
    expected = [0.0 + 1.0 * t + 0.05 * t * t for t in (0.0, 1.0, 2.0, 3.0, 4.0)]
    assert [round(r.value, 10) for r in positions] == [round(v, 10) for v in expected]

    # Reset replays the same run; the generator is not stateful across users.
    sensor.reset()
    again, _ = sensor.sample(5)
    assert [r.value for r in again] == [r.value for r in times]


def test_mock_sensor_cannot_claim_to_be_real() -> None:
    """The flag is fixed by the class and has no off switch."""
    sensor = MockCartSensor()
    assert sensor.simulated is True
    assert MockCartSensor.simulated is True

    reading = sensor.read()
    assert reading.simulated is True
    assert reading.provenance.kind is ProvenanceKind.SIMULATED
    assert "not measured" in reading.provenance.notes

    description = sensor.describe()
    assert description["simulated"] is True
    assert "must never be reported as one" in description["warning"]

    # Even constructing a reading by hand and lying about it does not help:
    # provenance is derived from the flag, so the two cannot disagree.
    honest = SensorReading(quantity="position", value=1.0, unit="m",
                           sensor_id="real.cart", simulated=False)
    assert honest.provenance.kind is ProvenanceKind.SENSOR


def test_simulated_data_stays_labelled_through_storage() -> None:
    core, path = _core()
    experiment = core.create_experiment("Mock cart run")
    sensor = MockCartSensor(noise=0.0)
    times, positions = sensor.sample(5)

    stored = core.ingest_readings(experiment.id, times) + \
        core.ingest_readings(experiment.id, positions)
    assert len(stored) == 10
    for measurement in stored:
        assert measurement.is_simulated
        assert measurement.provenance.kind is ProvenanceKind.SIMULATED
        assert measurement.evidence_kind is EvidenceKind.SIMULATION
        assert not measurement.derived

    # A query for observations of the world finds none of them.
    assert core.list_measurements(experiment.id, include_simulated=False) == []
    assert len(core.list_measurements(experiment.id)) == 10
    stats = core.stats()
    assert stats["simulated_measurements"] == 10 and stats["raw_measurements"] == 0

    # And it survives a reopen, which is where a metadata-only label would rot.
    reopened = ScienceStore(MemoryStore(path))
    for measurement in reopened.list_measurements(experiment.id):
        assert measurement.evidence_kind is EvidenceKind.SIMULATION
    assert reopened.list_measurements(experiment.id, include_simulated=False) == []


def test_analysis_of_simulated_data_says_so() -> None:
    core, _ = _core()
    experiment = core.create_experiment("Mock cart analysis")
    sensor = MockCartSensor(noise=0.0)
    times, positions = sensor.sample(5)
    core.ingest_readings(experiment.id, times)
    core.ingest_readings(experiment.id, positions)

    analysis, evidence = core.analyse_motion(experiment.id)
    # x(4) = 0 + 1.0*4 + 0.5*0.1*16 = 4.8 m, so v_avg = 4.8 m / 4 s = 1.2 m/s.
    assert math.isclose(analysis.displacement.value, 4.8, rel_tol=1e-9)
    assert math.isclose(analysis.average_velocity.value, 1.2, rel_tol=1e-9)
    # Under uniform acceleration the interval velocity equals the instantaneous
    # velocity at the midpoint: v(0.5) = 1.0 + 0.1*0.5 = 1.05, and so on.
    assert [round(r.value, 10) for r in analysis.interval_velocities] == [1.05, 1.15, 1.25, 1.35]
    assert math.isclose(analysis.average_acceleration.value, 0.1, rel_tol=1e-9)

    assert any("were simulated" in w for w in analysis.warnings), analysis.warnings
    assert any("not a measured system" in w for w in analysis.warnings)
    for item in evidence:
        assert item.metadata.get("simulated_inputs") == 10

    # Asking for readings of the world only leaves nothing to analyse, and the
    # engine refuses rather than returning an empty result that looks valid.
    try:
        core.analyse_motion(experiment.id, include_simulated=False)
    except Exception as exc:
        assert "two samples" in str(exc) or "matching time" in str(exc), exc
    else:
        raise AssertionError("Analysed an empty series")


def test_real_readings_are_recorded_as_measurements() -> None:
    """The other half: a genuine sensor is not demoted to simulation."""
    core, _ = _core()
    experiment = core.create_experiment("Real run")
    readings = [
        SensorReading(quantity="position", value=v, unit="m", uncertainty=0.01,
                      timestamp=float(i), sensor_id="lab.rangefinder", simulated=False)
        for i, v in enumerate([0.0, 1.0, 2.1])
    ]
    stored = core.ingest_readings(experiment.id, readings)
    for measurement in stored:
        assert measurement.evidence_kind is EvidenceKind.MEASUREMENT
        assert measurement.provenance.kind is ProvenanceKind.SENSOR
        assert not measurement.is_simulated
    assert core.stats()["raw_measurements"] == 3
    assert len(core.list_measurements(experiment.id, include_simulated=False)) == 3


def test_static_sensor_defaults_to_the_cautious_answer() -> None:
    reading = SensorReading(quantity="position", value=1.0, unit="m")
    assert StaticSensor([reading]).simulated is True, "forgetting should not imply real data"
    assert StaticSensor([reading], simulated=False).simulated is False

    sensor = StaticSensor([reading], name="replay")
    assert sensor.available()[0] is True
    assert sensor.read().value == 1.0
    assert sensor.available()[0] is False
    try:
        sensor.read()
    except SensorError as exc:
        assert "no readings left" in str(exc), exc
    else:
        raise AssertionError("Read past the end of a replay")


def test_unavailable_backend_contributes_nothing() -> None:
    """A backend that cannot reach its instrument must not invent a number."""

    class BrokenSensor(SensorBackend):
        name = "broken.probe"

        def available(self) -> tuple[bool, str]:
            return False, "device /dev/ttyUSB0 not present"

        def describe(self) -> dict:
            return {"name": self.name}

        def read(self) -> SensorReading:  # pragma: no cover - must never run
            raise AssertionError("read() was called on an unavailable backend")

    core, _ = _core()
    experiment = core.create_experiment("Broken probe")
    try:
        core.run_sensor(experiment.id, BrokenSensor(), 5)
    except SensorError as exc:
        assert "not present" in str(exc), exc
    else:
        raise AssertionError("An unavailable backend was allowed to contribute readings")
    assert core.list_measurements(experiment.id) == []


def test_bad_readings_are_refused_at_construction() -> None:
    for kwargs, expected in (
        ({"quantity": "", "value": 1.0, "unit": "m"}, "name the quantity"),
        ({"quantity": "position", "value": 1.0, "unit": "m", "uncertainty": -1.0}, "negative"),
    ):
        try:
            SensorReading(**kwargs)
        except SensorError as exc:
            assert expected in str(exc), exc
        else:
            raise AssertionError(f"Accepted a bad reading: {kwargs}")

    # An unparseable unit fails here rather than during analysis.
    try:
        SensorReading(quantity="position", value=1.0, unit="furlongs-per-fortnight")
    except Exception as exc:
        assert "furlongs" in str(exc) or "unit" in str(exc).lower(), exc
    else:
        raise AssertionError("Accepted an unparseable unit")

    try:
        MockCartSensor(interval=0.0)
    except SensorError as exc:
        assert "positive" in str(exc), exc
    else:
        raise AssertionError("Accepted a zero sampling interval")

    core, _ = _core()
    experiment = core.create_experiment("Attribution")
    try:
        core.record_simulation(experiment.id, "position", 1.0, "m", simulator="")
    except ScienceError as exc:
        assert "name the simulator" in str(exc), exc
    else:
        raise AssertionError("An unattributed simulated value was accepted")


def test_noise_is_reproducible_and_bounded() -> None:
    """Seeded scatter: realistic-looking, and still exactly assertable."""
    first = MockCartSensor(noise=0.02, seed=7).read_many(20)
    second = MockCartSensor(noise=0.02, seed=7).read_many(20)
    assert [r.value for r in first] == [r.value for r in second]

    different = MockCartSensor(noise=0.02, seed=8).read_many(20)
    assert [r.value for r in first] != [r.value for r in different]

    model = MockCartSensor(noise=0.0)
    expected = [model.position_at(r.metadata["model_time_s"]) for r in first]
    # Scatter of sigma 0.02 should stay well inside 5 sigma RMS.
    assert rms_error(first, expected) < 0.1
    # The noiseless value is kept alongside, so the generated scatter can be
    # separated from the model afterwards.
    assert all("noiseless_value" in r.metadata for r in first)


def main() -> None:
    test_mock_sensor_follows_its_own_model()
    test_mock_sensor_cannot_claim_to_be_real()
    test_simulated_data_stays_labelled_through_storage()
    test_analysis_of_simulated_data_says_so()
    test_real_readings_are_recorded_as_measurements()
    test_static_sensor_defaults_to_the_cautious_answer()
    test_unavailable_backend_contributes_nothing()
    test_bad_readings_are_refused_at_construction()
    test_noise_is_reproducible_and_bounded()
    print("DaQauntum v0.5 sensor backend smoke test: PASS")


if __name__ == "__main__":
    main()
