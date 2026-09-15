# DaQauntum - Claude Code Engineering Contract

You are working on **DaQauntum v0.4.2-dev**, a local-first persistent AI operating system / agent runtime. This repository is a working prototype with real subsystems for multimodel reasoning, memory, knowledge/provenance, voice, perception, permission-gated computer/device actions, autonomous learning, connected sources, workspaces, integrations, presence sensing, and a local GUI.

Your job is to **improve the existing system**, not rewrite it from scratch.

## Read first

Before changing code, read these in order:

1. `docs/HANDOFF.md`
2. `docs/REALITY_MAP.md`
3. `docs/SECURITY_INVARIANTS.md`
4. `docs/CODEBASE_MAP.md`
5. `TASKS.md`
6. `ARCHITECTURE.md`
7. `constitution.md`

If a task touches a subsystem, read its implementation and matching smoke test before editing it.

## Non-negotiable architecture rules

1. **Model intelligence is not authority.** A stronger model never gets more tool permission.
2. **Identity is not authority.** An enrolled device proves which client is calling and which API surfaces it may reach. It never raises the permission level.
3. **All state-changing actions go through the permission system.** Do not create a backdoor execution path.
4. **Local means local.** If runtime/privacy policy requires local inference, do not silently fall back to hosted providers.
5. **Sensing is not acting.** Presence/perception may observe approved signals; connection/control is separately permission-gated. An event never triggers an action on its own: reaction rules may only notify, queue a task, or record a proposal the user approves.
6. **Discovery is not trust.** Detecting a network/device/source does not authorize connection or control.
7. **Similarity is not evidence.** Knowledge/source provenance must remain explicit.
8. **Learning is not self-retraining.** Autonomous learning may create reports/training traces; it must not change model weights without an explicit evaluated training pipeline and human promotion decision.
9. **Interrupted output is not durable truth.** Partial/interrupted replies must not become structured long-term knowledge.
10. **Every release is a complete standalone repository.** Never ship patch-only archives.
11. **Backward compatibility is additive by default.** Database/schema migrations should preserve existing data unless a migration plan explicitly says otherwise.

See `docs/SECURITY_INVARIANTS.md` for the complete list.

## Current product target

The immediate goal is not more speculative features. It is to make DaQauntum a dependable everyday service:

- one-command / service startup;
- low-latency natural voice conversation;
- persistent server and reconnecting clients;
- explicit device identity and remote approvals;
- reliable local/hosted model routing;
- event-driven awareness from approved sensors/devices;
- stable Eyes + Hands execution with verification;
- strong observability and failure reporting.

The v0.4.1 Device Drivers + Event Reactions code is implemented and covered by
mock-based regression tests. Two acceptance criteria remain open and both need
the user's actual machine: validating one real sensor path, and retesting a
freshly extracted release archive. That is why `VERSION` reads `0.4.1-dev`.

Remaining P0 host operations (run the doctor, configure voice, benchmark the
real model routes, install the service, set up Tailscale) take priority over
new features. Acceptance criteria are in `TASKS.md`.

## Development workflow

Create a virtual environment and install base dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml
```

Optional voice/integration dependencies:

```bash
pip install -r requirements-voice.txt
pip install -r requirements-integrations.txt
```

Run a focused test for the subsystem you changed, then run the full relevant suite before declaring success.

Core verification:

```bash
PYTHONPATH=. python scripts/verify_release.py
PYTHONPATH=. python evaluations/smoke_test.py
```

Full regression matrix:

```bash
PYTHONPATH=. python evaluations/voice_smoke_test.py
PYTHONPATH=. python evaluations/realtime_smoke_test.py
PYTHONPATH=. python evaluations/duplex_smoke_test.py
PYTHONPATH=. python evaluations/learning_smoke_test.py
PYTHONPATH=. python evaluations/connectors_smoke_test.py
PYTHONPATH=. python evaluations/workspaces_smoke_test.py
PYTHONPATH=. python evaluations/integrations_smoke_test.py
PYTHONPATH=. python evaluations/perception_smoke_test.py
PYTHONPATH=. python evaluations/presence_smoke_test.py
PYTHONPATH=. python evaluations/demo_smoke_test.py
PYTHONPATH=. python evaluations/gui_smoke_test.py
PYTHONPATH=. python evaluations/events_smoke_test.py
PYTHONPATH=. python evaluations/drivers_smoke_test.py
PYTHONPATH=. python evaluations/identity_smoke_test.py
```

## Definition of done for code changes

A change is not done until:

- implementation is real, not a UI-only stub;
- permission/privacy behavior is preserved;
- failure modes are explicit and safe;
- tests are added/updated;
- docs/config examples are updated if behavior changed;
- no credentials, runtime DBs, model files, caches, `.venv`, or personal data are committed;
- the relevant smoke tests pass.

For release work, follow `docs/RELEASE_CHECKLIST.md` and test the **freshly extracted final ZIP**, not only the development tree.

## Coding style

- Python first; keep dependencies optional where practical.
- Prefer small adapters/interfaces over hard-coupling external products into the kernel.
- Use deterministic logic for permissions, policy, migration, and provenance decisions.
- Keep the GUI thin: business rules belong in backend modules.
- Keep local/offline fallback paths working.
- Do not silently swallow failures; return/report degraded capability state.
- Do not change public behavior merely to make a test pass.

## Important files

- `core/kernel.py` - main cognitive lifecycle
- `core/brain.py` - model providers/routing integration
- `core/policy.py` - model/privacy routing policy
- `core/permissions.py` - authority gate
- `tools/registry.py` - tool registration/execution boundary
- `runtime/host.py` - persistent runtime host
- `interface/server.py` - local HTTP/API GUI backend
- `realtime/duplex.py` - persistent realtime conversation transport
- `memory/` - durable + intelligent memory
- `knowledge/` - graph, sources, provenance
- `presence/` - ambient approved local sensing
- `perception/` - screen/camera frame intake + vision routing
- `computer/` - computer action controller/verification
- `connectors/` - external information/device ingestion
- `integrations/` - optional third-party modules
- `learning/` and `native_model/` - autonomous learning + future-model dataset preparation
- `events/` - event bus, deterministic reaction rules, notifications
- `drivers/` - optional BLE/serial/MQTT/Home Assistant device adapters
- `identity/` - device enrollment, scoped session tokens, revocation
- `evaluations/` - regression tests

## Before proposing a rewrite

Assume the current architecture is intentional until you can show:

1. the concrete limitation;
2. a migration path;
3. compatibility impact;
4. tests proving the replacement is safer/better.

DaQauntum has evolved incrementally from v0.1 to v0.4.0. Preserve that accumulated behavior.
