"""End-to-end test of the v0.5 scientific core over the real HTTP API.

One investigation, start to finish, through the running server: create the
experiment, propose a hypothesis, record readings, analyse, plot, claim,
persist, reload in a fresh process, and verify. Nothing is stubbed and nothing
short-circuits the transport.

What this suite adds over the unit suites is the boundaries between them. Each
layer is careful about epistemic labels on its own; this checks that the labels
survive JSON serialisation, HTTP, the scope gate, SQLite and a process restart -
which is where a distinction that is only held in memory quietly disappears.
"""
from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

CART_TIMES = [0.0, 1.0, 2.0, 3.0, 4.0]
CART_POSITIONS = [0.0, 1.0, 2.1, 3.3, 4.6]


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(base: str, path: str, payload: dict | None = None) -> dict:
    if payload is None:
        request = urllib.request.Request(base + path, method="GET")
    else:
        request = urllib.request.Request(
            base + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_until_ready(base: str, process: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with code {process.returncode}")
        try:
            if request_json(base, "/api/status").get("ok"):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.15)
    raise RuntimeError("server did not become ready")


def _config(tmp_root: Path, project: Path, port: int, artifacts: Path) -> dict:
    return {
        "version": "0.5.0-e2e-test",
        "permission_level": 2,
        "models": {
            "fallback_to_mock": True,
            "providers": {"mock": {"model": "daqauntum-mock"}},
            "roles": {
                "planner": {"provider": "mock", "preference": ["mock"]},
                "executor": {"provider": "mock", "preference": ["mock"]},
                "critic": {"provider": "mock", "preference": ["mock"]},
            },
        },
        "memory": {"db_path": str(tmp_root / "e2e.db"), "structured_enabled": True,
                   "intelligence_enabled": False, "auto_maintain_every": 0},
        "knowledge_graph": {"enabled": False},
        "sources": {"enabled": False, "project_root": str(project)},
        "connectors": {"enabled": False},
        "device_bridge": {"enabled": False},
        "presence": {"enabled": False},
        "perception": {"enabled": False},
        "learning": {"enabled": False, "project_root": str(project)},
        "voice": {"enabled": False},
        "realtime": {"enabled": False},
        "duplex": {"enabled": False},
        "demo": {"enabled": False},
        "obsidian": {"enabled": False},
        "science": {"enabled": True, "artifacts_dir": str(artifacts)},
        "tools": {"project_root": str(project), "notes_dir": "notes"},
        "gui": {"host": "127.0.0.1", "port": port, "websocket_port": free_port()},
    }


def run_investigation(base: str) -> dict:
    """Create, measure, analyse, claim - the whole workflow over HTTP."""
    created = request_json(base, "/api/science/experiment", {
        "title": "Cart on a level track",
        "research_question": "How does the cart's position change with time?",
        "depth": "introductory",
        "independent_variables": ["time"],
        "dependent_variables": ["position"],
    })
    assert created["ok"], created
    experiment_id = created["experiment"]["id"]
    assert created["experiment"]["status"] == "draft"

    hypothesis = request_json(base, "/api/science/hypothesis", {
        "experiment_id": experiment_id,
        "statement": "The cart's velocity increases over the run.",
        "expected_relationship": "position increases faster than linearly in time",
    })
    assert hypothesis["ok"] and hypothesis["hypothesis"]["status"] == "proposed"

    for t, x in zip(CART_TIMES, CART_POSITIONS):
        for quantity, value, unit in (("time", t, "s"), ("position", x, "m")):
            recorded = request_json(base, "/api/science/measurement", {
                "experiment_id": experiment_id, "quantity": quantity,
                "value": value, "unit": unit, "uncertainty": 0.01,
                "observer": "test-operator",
            })
            assert recorded["ok"], recorded
            assert recorded["measurement"]["derived"] is False

    analysed = request_json(base, "/api/science/analyse",
                            {"experiment_id": experiment_id, "plot": True})
    assert analysed["ok"], analysed
    return {"experiment_id": experiment_id,
            "hypothesis_id": hypothesis["hypothesis"]["id"],
            "analysis": analysed}


def check_analysis(analysed: dict) -> None:
    """The numbers, computed by the server, checked against the equations here."""
    analysis = analysed["analysis"]
    assert analysis["samples"] == 5
    assert math.isclose(analysis["displacement"]["value"], 4.6, rel_tol=1e-9)
    assert math.isclose(analysis["average_velocity"]["value"], 1.15, rel_tol=1e-9)
    assert math.isclose(analysis["average_acceleration"]["value"], 0.1, rel_tol=1e-9)
    assert [round(r["value"], 10) for r in analysis["interval_velocities"]] == \
        [1.0, 1.1, 1.2, 1.3]
    assert 0.997 < analysis["fit"]["r_squared"] < 0.998

    # Human-entered readings are measurements, so no "not measured" warning.
    assert analysis["warnings"] == [], analysis["warnings"]

    # Every calculated result arrives with its equation and its inputs.
    for key in ("displacement", "average_velocity", "average_acceleration"):
        result = analysis[key]
        assert result["method"], f"{key} crossed the API without its equation"
        assert result["inputs"], f"{key} crossed the API without its inputs"
        assert result["uncertainty"] is not None, f"{key} lost its uncertainty"

    # Analysis results are CALCULATION evidence on the far side of the wire.
    assert analysed["evidence"], "no evidence returned"
    for item in analysed["evidence"]:
        assert item["kind"] == "calculation", item["kind"]
        assert item["provenance"]["kind"] == "calculated"

    assert len(analysed["plots"]) == 2, analysed["plots"]
    for path in analysed["plots"]:
        artifact = Path(path)
        assert artifact.exists(), f"{path} was reported but not written"
        assert artifact.read_text(encoding="utf-8").startswith("<svg")


def check_record(base: str, experiment_id: str) -> dict:
    record = request_json(base, f"/api/science/experiment?id={experiment_id}")
    assert record["ok"], record
    body = record["record"]
    assert body["experiment"]["id"] == experiment_id
    assert len(body["hypotheses"]) == 1
    # 3 calculated results + 2 plots
    assert body["evidence_by_kind"]["calculation"] == 5, body["evidence_by_kind"]

    readings = request_json(
        base, f"/api/science/measurements?id={experiment_id}&readings_only=1")
    assert len(readings["measurements"]) == 10, "human readings were not stored as readings"
    assert all(not m["derived"] for m in readings["measurements"])

    everything = request_json(base, f"/api/science/measurements?id={experiment_id}")
    assert len(everything["measurements"]) > 10, "derived results were not stored"

    stats = request_json(base, "/api/science")["science"]
    assert stats["raw_measurements"] == 10
    assert stats["derived_measurements"] == 3
    assert stats["simulated_measurements"] == 0
    return body


def check_claim(base: str, experiment_id: str, hypothesis_id: str,
                evidence: list[dict]) -> str:
    calculated = [e["id"] for e in evidence if e["kind"] == "calculation"]
    claim = request_json(base, "/api/science/claim", {
        "experiment_id": experiment_id,
        "statement": "The cart accelerated at about 0.1 m/s^2 over the run.",
        "evidence_ids": calculated,
        "hypothesis_id": hypothesis_id,
        "author": "test-operator",
    })
    assert claim["ok"], claim
    assert claim["claim"]["evidence_ids"] == calculated

    # A claim citing evidence that does not exist is refused across the wire too.
    try:
        request_json(base, "/api/science/claim", {
            "experiment_id": experiment_id,
            "statement": "Unfalsifiable.",
            "evidence_ids": ["ev_does_not_exist"],
        })
    except urllib.error.HTTPError as exc:
        assert exc.code >= 400, exc.code
    else:
        raise AssertionError("The API accepted a claim citing nonexistent evidence")
    return claim["claim"]["id"]


def check_backends(base: str) -> None:
    """An unimplemented adapter must announce itself as unimplemented."""
    backends = request_json(base, "/api/science/backends")["backends"]
    research = {b["name"]: b for b in backends["research"]}
    assert research["local"]["scope"] == "this installation only"

    open_science = research["open-science"]
    assert open_science["status"] == "unimplemented"
    assert open_science["implemented"] is False
    assert open_science["network_calls"] is False
    assert open_science["research_needed"], "no list of what is missing"

    simulation = backends["simulation"][0]
    assert any("friction" in a.lower() for a in simulation["assumptions"])
    sensor = backends["sensors"][0]
    assert sensor["simulated"] is True
    assert "never be reported as one" in sensor["warning"]


def check_reference_run(base: str) -> None:
    """The reference experiment over HTTP, still labelled as example data."""
    result = request_json(base, "/api/science/reference-run", {"source": "example"})
    assert result["ok"], result
    summary = result["result"]
    assert summary["source"] == "example"
    assert math.isclose(summary["average_velocity_ms"], 1.15, rel_tol=1e-9)
    assert summary["warnings"], "example data crossed the API with no warning"
    assert any("not measured" in w for w in summary["warnings"])
    assert "measurement" not in summary["evidence_kinds"]
    for path in summary["plots"]:
        assert "not measured" in Path(path).read_text(encoding="utf-8").lower()


def check_reload(db_path: Path, experiment_id: str, claim_id: str) -> None:
    """A fresh process reads the same record, with the same labels."""
    script = f"""
import json, sys
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from memory.store import MemoryStore
from science.store import ScienceStore
from science.models import EvidenceKind

store = ScienceStore(MemoryStore({str(db_path)!r}))
record = store.experiment_record({experiment_id!r})
claim = store.get_claim({claim_id!r})
readings = store.list_measurements({experiment_id!r}, readings_only=True)
derived = [m for m in store.list_measurements({experiment_id!r}) if m.derived]
print(json.dumps({{
    "status": record["experiment"]["status"],
    "hypotheses": len(record["hypotheses"]),
    "evidence_by_kind": record["evidence_by_kind"],
    "claims": len(record["claims"]),
    "claim_evidence": len(claim.evidence_ids),
    "readings": len(readings),
    "reading_kinds": sorted({{m.evidence_kind.value for m in readings}}),
    "derived": len(derived),
    "derived_kinds": sorted({{m.evidence_kind.value for m in derived}}),
    "stats": store.stats(),
}}))
"""
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    reloaded = json.loads(proc.stdout)

    assert reloaded["hypotheses"] == 1
    assert reloaded["claims"] == 1
    assert reloaded["claim_evidence"] == 3
    assert reloaded["readings"] == 10
    # The distinction that matters, after a full restart.
    assert reloaded["reading_kinds"] == ["measurement"], reloaded["reading_kinds"]
    assert reloaded["derived"] == 3
    assert reloaded["derived_kinds"] == ["calculation"], reloaded["derived_kinds"]
    # Global counts now also include the reference run, which used example
    # data. That makes this the sharper assertion: the only readings in the
    # whole database are the ten a person entered. The reference run added
    # imported and derived values and not one measurement.
    stats = reloaded["stats"]
    assert stats["raw_measurements"] == 10, (
        f"{stats['raw_measurements']} readings in the database; the reference run's "
        "example data was counted as measured")
    assert stats["imported_measurements"] == 10, stats
    assert stats["derived_measurements"] >= 3, stats
    assert stats["simulated_measurements"] == 0, stats


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="daqauntum-science-e2e-") as tmp:
        tmp_root = Path(tmp)
        project = tmp_root / "project"
        project.mkdir()
        artifacts = tmp_root / "artifacts"
        port = free_port()
        config_path = tmp_root / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(_config(tmp_root, project, port, artifacts)), encoding="utf-8")

        env = {**os.environ, "PYTHONPATH": str(root), "DAQAUNTUM_CONFIG": str(config_path)}
        process = subprocess.Popen(
            [sys.executable, "daqauntum_gui.py", "--no-browser",
             "--config", str(config_path), "--port", str(port)],
            cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{port}"
        try:
            wait_until_ready(base, process)

            investigation = run_investigation(base)
            check_analysis(investigation["analysis"])
            record = check_record(base, investigation["experiment_id"])
            claim_id = check_claim(base, investigation["experiment_id"],
                                   investigation["hypothesis_id"],
                                   investigation["analysis"]["evidence"])
            check_backends(base)
            check_reference_run(base)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()

        # Only now, with the server stopped, read the database from scratch.
        check_reload(tmp_root / "e2e.db", investigation["experiment_id"], claim_id)

    print("DaQauntum v0.5 scientific core end-to-end smoke test: PASS")


if __name__ == "__main__":
    main()
