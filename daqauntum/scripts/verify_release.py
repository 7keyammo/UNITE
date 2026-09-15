#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path


REQUIRED = [
    "VERSION",
    "CLAUDE.md",
    "AGENTS.md",
    "TASKS.md",
    "docs/HANDOFF.md",
    "docs/REALITY_MAP.md",
    "docs/SECURITY_INVARIANTS.md",
    "docs/CODEBASE_MAP.md",
    "docs/ARCHITECTURE_DECISIONS.md",
    "docs/PRODUCT_VISION.md",
    "docs/DEVELOPMENT_WORKFLOW.md",
    "docs/TEST_MATRIX.md",
    "docs/RELEASE_CHECKLIST.md",
    "docs/KNOWN_GAPS.md",
    "handoff/CLAUDE_CODE_START.md",
    "handoff/CODEX_START.md",
    "handoff/HANDOFF_CHECKLIST.md",
    "handoff/PROJECT_STATE.json",
    "README.md",
    "ARCHITECTURE.md",
    "MIGRATION.md",
    "CHANGELOG.md",
    "RELEASE.md",
    "RELEASE_MANIFEST.txt",
    "ROADMAP.md",
    "INTEGRATIONS.md",
    "constitution.md",
    "config.example.yaml",
    "requirements.txt",
    "requirements-voice.txt",
    "requirements-integrations.txt",
    "daqauntum_server.py",
    "runtime/__init__.py",
    "runtime/host.py",
    "integrations/__init__.py",
    "integrations/manager.py",
    "integrations/langgraph_runtime.py",
    "perception/__init__.py",
    "perception/manager.py",
    "computer/__init__.py",
    "computer/controller.py",
    "evaluations/perception_smoke_test.py",
    "scripts/perception_doctor.py",
    "evaluations/integrations_smoke_test.py",
    "scripts/install_server_service.py",
    "scripts/integration_doctor.py",
    "scripts/setup_tailscale_serve.py",
    "scripts/bootstrap_pgvector.py",
    "scripts/export_agent_portability.py",
    "deploy/systemd/daqauntum-server.service.in",
    "daqauntum.py",
    "daqauntum_gui.py",
    "core/kernel.py",
    "core/brain.py",
    "core/config.py",
    "core/critic.py",
    "core/permissions.py",
    "core/planner.py",
    "core/policy.py",
    "core/router.py",
    "core/runtime.py",
    "realtime/__init__.py",
    "realtime/session.py",
    "realtime/duplex.py",
    "agents/base.py",
    "agents/specialists.py",
    "memory/store.py",
    "memory/manager.py",
    "memory/intelligence.py",
    "tools/registry.py",
    "voice/call_session.py",
    "voice/local_voice.py",
    "interface/server.py",
    "interface/web/index.html",
    "interface/web/styles.css",
    "interface/web/app.js",
    "interface/web/universe.js",
    "knowledge/graph.py",
    "knowledge/sources.py",
    "connectors/__init__.py",
    "connectors/manager.py",
    "connectors/device_bridge.py",
    "workspaces/__init__.py",
    "workspaces/manager.py",
    "workspaces/workbench.py",
    "workspaces/obsidian.py",
    "universe/__init__.py",
    "universe/state.py",
    "evaluations/smoke_test.py",
    "evaluations/gui_smoke_test.py",
    "evaluations/voice_smoke_test.py",
    "evaluations/realtime_smoke_test.py",
    "evaluations/duplex_smoke_test.py",
    "evaluations/learning_smoke_test.py",
    "evaluations/connectors_smoke_test.py",
    "evaluations/workspaces_smoke_test.py",
    "learning/__init__.py",
    "learning/manager.py",
    "native_model/__init__.py",
    "native_model/dataset.py",
    "scripts/daily_learn.py",
    "scripts/evening_report.py",
    "scripts/evening_report.sh",
    "scripts/export_native_dataset.py",
    "scripts/install_learning_timers.py",
    "scripts/status_learning_timers.py",
    "scripts/sync_connected_sources.py",
    "scripts/build_os.py",
    "scripts/export_obsidian.py",
    "THIRD_PARTY_INSPIRATION.md",
    ".mcp.json",
    "deploy/systemd/daqauntum-learn.service.in",
    "deploy/systemd/daqauntum-learn.timer",
    "deploy/systemd/daqauntum-report.service.in",
    "deploy/systemd/daqauntum-report.timer",
    "deploy/systemd/daqauntum-connectors.service.in",
    "deploy/systemd/daqauntum-connectors.timer",
    "scripts/setup_local_voice.py",
    "scripts/run_gui.sh",
    "run_daqauntum.command",
    "scripts/import_previous_data.py",
    "scripts/build_release_manifest.py",
    "presence/__init__.py",
    "presence/manager.py",
    "presence/monitor.py",
    "demo/__init__.py",
    "demo/manager.py",
    "evaluations/presence_smoke_test.py",
    "evaluations/demo_smoke_test.py",
    "scripts/presence_doctor.py",
    "scripts/run_demo.py",
    "scripts/first_run.py",
    "scripts/doctor.py",
    "scripts/benchmark_latency.py",
    "events/manager.py",
    "events/bus.py",
    "events/rules.py",
    "events/notifications.py",
    "drivers/manager.py",
    "drivers/base.py",
    "evaluations/events_smoke_test.py",
    "evaluations/drivers_smoke_test.py",
    "evaluations/identity_smoke_test.py",
    "identity/manager.py",
    "identity/models.py",
    "identity/__init__.py",
    "events/push.py",
    "interface/web/mobile.html",
]

EXPECTED_VERSION = "0.4.2-dev"


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    missing = [item for item in REQUIRED if not (root / item).exists()]
    if missing:
        raise SystemExit("INCOMPLETE RELEASE - missing: " + ", ".join(missing))

    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if version != EXPECTED_VERSION:
        raise SystemExit(f"VERSION mismatch: expected {EXPECTED_VERSION}, got {version}")

    manifest_path = root / "RELEASE_MANIFEST.txt"
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        expected, relative = line.split("  ", 1)
        target = root / relative
        if not target.exists():
            raise SystemExit(f"Manifest file missing: {relative}")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Manifest hash mismatch: {relative}")

    # Verify both entrypoints can at least be loaded from the unpacked repository.
    for entry in ("daqauntum.py", "daqauntum_gui.py", "daqauntum_server.py"):
        spec = importlib.util.spec_from_file_location("daqauntum_entry_" + entry.replace(".", "_"), root / entry)
        if spec is None or spec.loader is None:
            raise SystemExit(f"Could not load {entry}")

    print(f"DaQauntum v{version} release verification: PASS ({len(REQUIRED)} required files present)")


if __name__ == "__main__":
    main()
