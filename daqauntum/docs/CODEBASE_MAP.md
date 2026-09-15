# DaQauntum Codebase Map

## Entrypoints

- `daqauntum.py` - terminal/CLI interaction surface.
- `daqauntum_gui.py` - local web GUI launcher/client path.
- `daqauntum_server.py` - persistent host entrypoint.

## Cognitive core

- `core/kernel.py` - central turn lifecycle and subsystem orchestration.
- `core/brain.py` - provider implementations/model invocation/streaming.
- `core/policy.py` - deterministic task/privacy/model policy.
- `core/planner.py` - deterministic/model planning and tool intent generation.
- `core/critic.py` - answer critique/revision support.
- `core/permissions.py` - L0-L4 authority decisions.
- `core/runtime.py` - runtime/cognition/module mode state.
- `core/config.py` - YAML/default configuration loading and compatibility.
- `core/router.py` - specialist routing helper.

## Tools and action

- `tools/registry.py` - registered tools and permission enforcement boundary.
- `computer/controller.py` - computer autonomy/action/verification orchestration.
- `integrations/manager.py` - optional external integration registry/adapters.
- `integrations/langgraph_runtime.py` - optional durable workflow bridge.

## Agents and workspaces

- `agents/` - built-in specialist agents.
- `workspaces/manager.py` - FRAME-style portable workspaces.
- `workspaces/workbench.py` - parallel advisory agent jobs.
- `workspaces/obsidian.py` - portable Obsidian export.

## Memory / knowledge

- `memory/store.py` - SQLite persistence and base schema.
- `memory/manager.py` - structured memory lifecycle/retrieval.
- `memory/intelligence.py` - local semantic vectorization, salience, aging, contradiction, consolidation.
- `knowledge/graph.py` - graph nodes/edges/provenance.
- `knowledge/sources.py` - files/PDFs/notebooks/datasets/URLs/chunks/evidence sources.

## Voice / realtime

- `voice/local_voice.py` - STT/TTS adapters.
- `voice/call_session.py` - call sessions, notes, tasks and reports.
- `realtime/session.py` - realtime turn/cancellation bookkeeping.
- `realtime/duplex.py` - persistent WebSocket audio/text/control channel.

## Perception / presence

- `perception/manager.py` - explicit visual frame storage and vision routing.
- `presence/manager.py` - system/network/device telemetry and explicit discovery actions.
- `presence/monitor.py` - background passive sampling thread.

## Events, reactions and devices

- `events/models.py` - normalized `Event`, severities and publish results.
- `events/bus.py` - persistence, deterministic suppression, subscriber dispatch.
- `events/conditions.py` - deterministic rule operators that fail closed.
- `events/rules.py` - reaction rules, queued tasks and tool proposals.
- `events/notifications.py` - durable notification queue and delivery adapters.
- `events/manager.py` - `EventSystem` facade and the presence bridge.
- `drivers/base.py` - driver contract, capabilities and four-part readiness.
- `drivers/parsers.py` - line/json/csv/keyvalue sensor payload parsers.
- `drivers/serial_sensor.py` / `drivers/ble.py` - optional hardware adapters.
- `drivers/mqtt_subscriber.py` - persistent subscription and state cache.
- `drivers/homeassistant.py` - entity state ingestion.
- `drivers/manager.py` - driver registry, poll loop and the single write relay.

Reactions never execute. A rule may notify, queue a task, or record a tool
proposal that the user approves through `kernel.approve_proposal`, which runs
it through the normal `tools/registry.py` permission boundary.

## Connected information

- `connectors/manager.py` - folder/feed/web/other source synchronization.
- `connectors/device_bridge.py` - narrow paired LAN ingestion surface.

## Learning / native model preparation

- `learning/manager.py` - autonomous daily learning/report lifecycle.
- `native_model/dataset.py` - curated seed dataset export. No training weights yet.

## Interface

- `interface/server.py` - HTTP/API backend used by the local GUI.
- `interface/web/` - browser GUI and quantum-universe visualization.
- `demo/manager.py` - live guided capability walkthrough.
- `universe/state.py` - DaQauntum atom/project visual evolution state.

## Deployment / scripts

- `deploy/systemd/` - persistent server + learning/report/connector timers.
- `scripts/first_run.py` - readiness setup/diagnostics.
- `scripts/doctor.py` - consolidated host diagnostic; writes `SYSTEM_HEALTH.md`.
- `scripts/benchmark_latency.py` - measured TTFT/total latency per model route.
- `scripts/*_doctor.py` - targeted capability diagnostics.
- `scripts/install_server_service.py` - systemd user service installer.
- `scripts/install_learning_timers.py` - scheduled autonomous jobs.
- `scripts/import_previous_data.py` - safe prior-version state import.
- `scripts/build_release_manifest.py` / `verify_release.py` - release integrity.

## Tests

Each major subsystem has a smoke test in `evaluations/`. These are integration-style regression guards, not a complete unit-test suite. New subsystems should normally receive both focused unit tests and a smoke/integration test.
