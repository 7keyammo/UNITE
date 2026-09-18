# DaQauntum Engineering Backlog

This backlog is ordered. Coding agents should prefer the highest unfinished priority unless the user explicitly redirects work.

# P0 - Stabilize the real DaQauntum host

> **Status:** the tooling P0 needs is now built and tested
> (`scripts/doctor.py`, `scripts/benchmark_latency.py`). The remaining P0 items
> are host operations that can only be performed on the actual Debian machine:
> run the doctor, configure voice, benchmark the real model routes, install the
> service, and set up Tailscale. Start there before extending v0.4.1.


Goal: prove v0.4.0 is an everyday usable service on the actual Debian machine, not only a regression-tested repository.

## P0.1 Upgrade and baseline

- [ ] Move the user's current v0.3.6 state into this v0.4.0 handoff repository using `scripts/import_previous_data.py`.
- [ ] Run `scripts/first_run.py`.
- [x] Run release/core/GUI/presence/integration diagnostics. (`scripts/doctor.py` runs them in one pass)
- [x] Write `data/diagnostics/SYSTEM_HEALTH.md` (runtime artifact, do not commit personal details). (`scripts/doctor.py`, with `--redact` for sharing)

Acceptance:

- GUI starts and reports v0.4.0.
- Existing messages/memory/projects survive migration.
- No startup exceptions.

## P0.2 Make two-way voice real

- [ ] Install/configure local STT (`faster-whisper` preferred initially).
- [ ] Verify local/system TTS.
- [ ] Verify duplex/barge-in.
- [ ] Measure STT time, TTFT and total latency for at least 10 short turns.
- [x] Add a latency benchmark/report script if needed. (`scripts/benchmark_latency.py`)

Acceptance:

- User speaks without typing and receives spoken answer.
- Median short-turn TTFT is recorded.
- LOCAL mode never uses browser/cloud STT fallback silently.

## P0.3 Optimize model routing for conversation

- [ ] Inventory host CPU/RAM/GPU and available local/CLI models.
- [ ] Benchmark Ollama/Claude CLI/other configured providers for short dialogue and deep tasks.
- [ ] Tune AUTO/REALTIME route for the machine.
- [ ] Preserve Deep mode for quality-sensitive tasks.

Acceptance:

- Normal conversational turns use the fastest acceptable route.
- Deep tasks still invoke planner/critic as intended.
- GUI displays route + latency accurately.

## P0.4 Persistent service

- [ ] Install/test `daqauntum-server.service`.
- [ ] Verify restart after process failure.
- [ ] Verify reconnecting GUI/client-only mode.
- [ ] Verify learning/connector timers survive logout as intended.

Acceptance:

- Closing browser does not stop DaQauntum.
- Reboot brings the service back without manual terminal launch (subject to configured user-service/linger policy).

## P0.5 Secure remote access

- [ ] Configure Tailscale.
- [ ] Use Tailscale Serve/private tailnet path for remote access.
- [ ] Do not expose the main control API publicly.
- [ ] Test from phone.

Acceptance:

- User can securely reach DaQauntum remotely from an enrolled personal device.
- Local GUI remains loopback by default.

# v0.4.1 - Device Drivers + Event Reactions

Goal: DaQauntum should notice approved environment changes, classify their importance, and create controlled reactions without polling every capability manually.

## 4.1.1 Event Bus

- [x] Add normalized `PresenceEvent` / `DeviceEvent` model.
- [x] Persist event audit trail.
- [x] Add deduplication/debounce/cooldown.
- [x] Allow source adapters to publish events without directly taking actions.

Acceptance:

- Simulated battery/network/device events enter one common pipeline.
- Duplicate noisy events do not spam the user or trigger repeated actions.

## 4.1.2 Reaction Engine

- [x] Deterministic rule conditions + scopes + expiry.
- [x] Actions may be notify / queue task / propose tool call.
- [x] Rules cannot bypass PermissionManager.
- [x] User can inspect/enable/disable/delete rules.

Acceptance:

Example rule:

```text
IF battery < 15% AND not charging
THEN notify user
cooldown 60 min
```

fires once, is auditable, and cannot gain extra authority.

## 4.1.3 Notification queue

- [x] Persist notifications.
- [x] Severity/priority/status.
- [x] GUI surface.
- [x] Future mobile/push adapter interface.

## 4.1.4 BLE GATT driver SDK

- [x] Prefer `bleak` behind an optional adapter.
- [x] Generic device discovery metadata.
- [x] Explicit GATT profile definitions (read/write/notify characteristics).
- [x] Writes are permission-gated.
- [x] Never auto-pair unknown devices.

## 4.1.5 Serial sensor SDK

- [x] Optional `pyserial` adapter.
- [x] Explicit port allowlist/connector scope.
- [x] Pluggable parsers for line/JSON/CSV protocols.
- [x] Read-only sensor adapters first; write commands separate.

## 4.1.6 MQTT subscriptions

- [x] Persistent subscriber process/client.
- [x] Topic allowlists.
- [x] Retained/current state cache.
- [x] Map incoming topic updates to Event Bus.
- [x] Publish remains permission-gated.

## 4.1.7 Home Assistant event ingestion

- [x] Subscribe/poll HA state/event changes through a dedicated adapter.
- [x] Normalize entities/capabilities/events.
- [x] Feed Presence/Event Bus.
- [x] Service calls remain permission-gated.

## v0.4.1 Definition of done

- [x] Event + reaction architecture tested with hardware-independent mocks.
- [ ] At least one real sensor path validated on target hardware. **(outstanding — needs the physical machine; this is why VERSION is `0.4.1-dev`)**
- [x] GUI shows recent events/rules/notifications.
- [x] No passive event source can directly mutate external state.
- [x] Fresh standalone ZIP passes full regression matrix. (re-verified at v0.5.0: archive built from `RELEASE_MANIFEST.txt`, extracted to a clean directory with no git and no development tree, 29/29 suites pass and `verify_release.py` passes at 197 required files. Repeat on the target host before shipping - this container is not it.)

# v0.4.2 - Secure Mobile Client

> **Status:** identity, authentication, remote approvals, the phone client
> and push delivery are implemented and covered by
> `evaluations/identity_smoke_test.py`. What remains is phone-side capture
> and guided Tailscale setup.

- [x] Device identity + enrollment.
- [x] Session/auth tokens and revocation.
- [x] Phone-responsive full chat/call client.
- [x] Remote approvals.
- [x] Push notification adapter.
- [ ] Camera/file/screen context from phone. (ingestion still goes through the narrower device bridge)
- [ ] Tailscale-first network path. (adapter exists; setup is still a manual documented step)

# v0.4.3 - Native Model Lab

- [ ] Freeze schema for curated examples.
- [ ] Dataset quality gates/deduplication.
- [ ] Train/validation/test split.
- [ ] Pick open-weight base model based on target hardware.
- [ ] Reproducible LoRA/QLoRA training script.
- [ ] Teaching/physics/coding/reasoning/tool-use/safety eval harness.
- [ ] Model registry.
- [ ] Human-approved promotion/rollback.

# v0.5.x - Multi-Atom Universe

Only begin after secure device identity and remote auth are mature.

- [ ] Atom identity/keys.
- [ ] Explicit peer pairing.
- [ ] Shared molecule/workspace ACLs.
- [ ] Selective knowledge/capability exchange.
- [ ] No implicit private-memory synchronization.

# v1.0 acceptance target

A secure always-on service that the user can reach from phone/computer, talk to naturally, trust to remember/provenance-track work, receive useful ambient/event updates from explicitly connected devices/sources, and authorize to act on digital/physical systems with understandable permission boundaries.
