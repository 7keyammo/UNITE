# Changelog

## v0.4.1-dev — Device Drivers + Event Reactions

Version carries a `-dev` suffix deliberately: the code milestone is complete and
regression-tested with hardware-independent mocks, but the v0.4.1 definition of
done also requires at least one real sensor path validated on the target host.
That step needs the physical machine. Drop the suffix once it passes there.

### Added

- normalized event bus with a persisted audit trail (`device_events`);
- deterministic suppression: deduplication, debounce, per-kind cooldown and
  unchanged-state detection, with repeats counted rather than stored;
- deterministic reaction rules with match filters, conditions, scopes, expiry
  and cooldown, persisted in `reaction_rules`;
- reaction actions limited to notify, queue task and propose tool call;
- durable notification queue with severity-derived priority and a delivery
  adapter interface for a future mobile push client;
- queued reaction tasks and tool proposals with explicit approval/rejection;
- presence bridge turning existing snapshots into normalized events;
- device driver SDK with declared capabilities and four-part readiness;
- optional BLE GATT driver (`bleak`) with explicit per-device profiles;
- optional serial sensor driver (`pyserial`) with a port allowlist;
- persistent MQTT subscription with a retained-state cache and topic allowlist;
- Home Assistant state ingestion for explicitly listed entities;
- `driver_status`, `driver_discover`, `driver_read`, `events_recent` and
  `notifications_pending` tools (L0), plus `driver_write` (L3);
- Events & Reactions GUI view and `/api/events`, `/api/drivers` endpoints;
- `scripts/doctor.py`, a single host diagnostic that writes
  `data/diagnostics/SYSTEM_HEALTH.md`;
- `scripts/benchmark_latency.py` for measured TTFT and total turn latency;
- `evaluations/events_smoke_test.py` and `evaluations/drivers_smoke_test.py`.

### Security properties

- A reaction never executes a tool. Verified at L0, L2 and L4: even where the
  permission gate would allow the call outright, a rule may only record a
  proposal, because an observation is not an instruction.
- A proposal the gate refuses is stored as denied and can never be approved.
- Device writes require two independent gates: the user's permission level via
  the tool registry, and the driver's own allowlist plus `allow_writes`.
- Discovery output marks what is approved; detecting a device authorizes
  nothing, and there is no pairing flow.
- MQTT topic filters are re-checked locally, so a message on an unapproved
  topic is dropped even if the broker delivers it.

### Fixed

- MQTT `poll()` discarded messages already received when the subscription could
  not be restarted; buffered readings are now always drained.

### Preserved

All v0.4.0 behavior remains available. Configuration is additive and deep-merged,
so existing `config.yaml` files inherit the new sections unchanged, and all
twelve prior regression suites still pass.

## v0.4.0 — Presence + Guided Demo

### Added

- local Presence Layer and background passive awareness thread;
- passive network, battery, thermal, IIO, camera, serial/USB, sound and paired-Bluetooth observation;
- local alerts for low battery, high temperature, Wi-Fi changes and serial-device attach/remove;
- explicit Wi-Fi scan;
- explicit Bluetooth scan;
- explicit mDNS/Bonjour discovery;
- permission-gated saved Wi-Fi profile activation;
- permission-gated connection to already-paired Bluetooth devices;
- optional MQTT read/publish IoT bridge;
- `claude_cli` cognitive provider;
- guided spoken system demo;
- Presence and Demo GUI views;
- Chat quick actions for Talk / Demo / Sense / Capabilities;
- `scripts/first_run.py`;
- `scripts/presence_doctor.py`;
- `scripts/run_demo.py`;
- presence and demo regression suites.

### Preserved

All v0.3.x persistent server, voice, realtime, duplex, memory, knowledge, source, connector, learning, workspace, integration, perception and computer-control behavior remains available.
