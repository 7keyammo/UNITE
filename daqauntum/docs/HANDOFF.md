# DaQauntum v0.4.0 Developer Handoff

## Why this document exists

DaQauntum was prototyped iteratively through chat from v0.1 to v0.4.0. The project is now large enough that future work should be performed as normal software engineering in Claude Code, Codex, or another repository-aware coding environment.

This package is the **handoff source of truth**. A coding agent should not require the original conversation to continue development.

## Product thesis

DaQauntum is not intended to be a single chatbot model. It is an intelligence runtime composed of:

- swappable local/hosted language models;
- deterministic model/privacy policy;
- specialist agents and workspaces;
- structured long-term memory;
- a provenance-aware knowledge graph and source system;
- realtime/full-duplex voice;
- screen/camera perception;
- permission-gated computer/device action;
- connected folders, phone ingestion, web/feed connectors;
- autonomous daily learning and future native-model dataset preparation;
- ambient presence from approved local signals;
- optional mature integrations rather than rebuilding every ecosystem.

The governing principle is:

> **DaQauntum is the brain and policy layer; mature external systems may become organs.**

## Current release

Version: `0.4.1-dev`
Codename: **Device Drivers + Event Reactions**

Current primary entrypoints:

```bash
python daqauntum_gui.py
python daqauntum_gui.py --demo
python daqauntum_server.py
python daqauntum.py
```

Default local GUI: `http://127.0.0.1:8765/`
Default realtime WebSocket: port `8766`
Optional paired device bridge: port `8767`

## Current architecture at a glance

```text
                         DaQauntum Atom
                               |
                    identity + constitution
                               |
          +--------------------+--------------------+
          |                    |                    |
        Brain                Senses               Hands
          |                    |                    |
 planner/executor/       voice/screen/camera    tools/computer
 critic + model policy   presence/files/sensors integrations/MQTT
          |                    |                    |
          +--------------------+--------------------+
                               |
                       Permission Manager
                               |
                    execute / confirm / deny
                               |
                      verify + remember + learn
```

## What is genuinely implemented

See `docs/REALITY_MAP.md` for precise implementation/configuration status. Major code paths exist for:

- multi-provider model routing: Ollama, Claude CLI, OpenAI, Anthropic, mock;
- Realtime/Balanced/Deep cognition modes;
- deterministic privacy/locality policy;
- L0-L4 permissions and approval queue;
- SQLite persistent messages/events/structured memory;
- semantic-ish local memory retrieval, salience, aging, contradiction and consolidation;
- knowledge nodes/edges/provenance;
- local file/PDF/notebook/dataset source indexing;
- connected folder/phone/web/feed ingestion;
- call sessions, notes, action items and post-call task processing;
- local STT adapters and local/system TTS adapters;
- streamed model output, barge-in and persistent duplex session;
- autonomous morning learning/evening reports and training-trace export;
- portable workspaces, skills, agent definitions and workbench;
- persistent server/service installer;
- screen/camera frame intake + vision routing;
- computer-control adapter + post-action visual verification;
- Presence Layer for approved system/network/device telemetry;
- explicit Wi-Fi/Bluetooth/mDNS discovery paths;
- MQTT and optional external integration adapters;
- guided capability demo.

## What is not yet production-complete

Major gaps include:

- secure end-user authentication/device enrollment;
- robust remote mobile client with push notifications and approvals;
- standardized event bus + reaction engine;
- generic BLE GATT driver system;
- robust serial/GPIO hardware driver SDK;
- long-running Home Assistant/MQTT event subscriptions;
- hardened computer control across diverse applications;
- complete observability/metrics/tracing;
- packaging as a conventional installed desktop/server product;
- production database scaling/multi-user tenancy;
- evaluated native DaQauntum fine-tuned model;
- secure multi-atom federation.

## Real-machine context at handoff

The user has been running an older v0.3.6 build on a Debian/XFCE-style local machine. Screenshots showed:

- GUI successfully running on `127.0.0.1:8765`;
- real-model chat working;
- local STT not yet configured (browser fallback available);
- Device Bridge not running;
- high observed latency in at least one real-model turn.

Therefore **P0 is operational stabilization on the actual machine**, not adding endless new subsystems. See `TASKS.md`.

## Engineering strategy from here

Use three tracks in parallel:

### Track A - Product reliability

Make current features easy to install, diagnose and use every day.

### Track B - Embodiment/event loop

Turn Presence from sampled telemetry into a controlled event/reaction system with explicit device adapters.

### Track C - Native intelligence

Curate/evaluate training data and build a model lab only after the runtime has enough high-quality traces.

## How Claude Code and Codex should collaborate

Either tool may work independently, but avoid simultaneous edits to the same files without Git branches/worktrees.

Recommended split:

- **Claude Code:** architecture-heavy refactors, long-context repository review, UX/product flows, cross-module design.
- **Codex:** focused implementation, tests, migration utilities, integration adapters, bug fixing, release automation.

This is a suggested workflow, not a capability claim. The repo instructions in `CLAUDE.md` and `AGENTS.md` are authoritative.

## What changed after this handoff was written

A v0.4.1-dev development pass implemented the Device Drivers + Event Reactions
milestone: `events/` (bus, deterministic reaction rules, notifications) and
`drivers/` (BLE, serial, MQTT subscription, Home Assistant ingestion), plus the
Events GUI view, the `driver_*`/`events_*` tools, `scripts/doctor.py`,
`scripts/benchmark_latency.py`, and two new regression suites.

Two v0.4.1 acceptance criteria remain open and both require the physical
machine: validating one real sensor path, and retesting a freshly extracted
release archive. `VERSION` reads `0.4.1-dev` until then.

The P0 recommendation below is unchanged and still comes first, but step 2 is
now a single command: `PYTHONPATH=. python scripts/doctor.py`.

## First coding session recommendation

Do not begin v0.4.1 immediately. First complete `TASKS.md` **P0 Stabilize the Real Machine**:

1. upgrade the actual host to this handoff repo;
2. run `scripts/doctor.py` (it replaces running the separate doctors);
3. configure local STT/TTS;
4. benchmark model routes/TTFT with `scripts/benchmark_latency.py`;
5. install persistent server service;
6. verify 7 AM / 7 PM learning jobs;
7. verify Tailscale remote access;
8. produce one `SYSTEM_HEALTH.md` report from the real host (`scripts/doctor.py` writes it).

Once those pass, begin the v0.4.1 event/driver milestone.
