# Changelog

## v0.4.2-dev — Secure Mobile Client

Closes the largest item in the security gaps list: remote clients had no
device-enrollment or authentication layer at all.

### Added

- persistent device identity with enrollment, scopes and revocation;
- single-use, short-lived enrollment codes minted on the trusted host;
- session tokens with TTL, rotation and immediate cascade revocation;
- an authentication gate on the control API with per-surface scopes;
- a phone-responsive client at `/m` covering pairing, chat, the notification
  inbox, remote approvals, sending notes and token rotation;
- a Devices & Remote Access GUI view with an authentication log;
- an optional outbound webhook push adapter (ntfy, Gotify, Pushover, Home
  Assistant webhooks, or a personal relay);
- `evaluations/identity_smoke_test.py`.

### Security properties

- **Identity is not authority.** Scopes say which surfaces a device may reach;
  the PermissionManager still decides what DaQauntum may do. A device holding
  every scope cannot make a forbidden action permissible: at L0 a state-changing
  tool is refused with and without approval, and a device can only approve an
  action DaQauntum already prepared, never invent one.
- Session tokens are stored as SHA-256 digests; the short human-typed enrollment
  code additionally uses PBKDF2, because it is guessable in a way a token is not.
  Reading the database yields no working credential.
- The enrollment code alphabet excludes every ambiguous character pair, so a
  misread code cannot become a different valid code.
- Minting a new enrollment code supersedes any unused one, so only the code
  currently on screen can enrol a device.
- Reads and writes map through separate scope tables. A shared table would let a
  GET mapping authorize the POST on the same prefix; unmapped endpoints require
  admin, so a new endpoint is closed to remote devices until opened on purpose.
- Loopback remains the trusted control surface by default. The check reads the
  socket's real peer address, never a forwarded header.
- Authentication failures are indistinguishable to the caller; the reason stays
  in the audit log. Failed attempts are rate-limited per address.
- Push delivery is disabled by default and sends only title, body, severity,
  kind and source — never the originating event's raw attributes. Credentials
  are referenced from the environment as `${VAR}`, never stored in config.

### Fixed

- The realtime duplex WebSocket accepted any peer. With the HTTP API now
  authenticated, that left an unlocked door beside a locked one whenever the
  host was started with `--allow-lan`, where both bind to a non-loopback
  address. Remote duplex connections now present a token as their first
  message - a browser cannot set headers on a WebSocket, and a token in a URL
  leaks into logs - and need the `chat` scope. Audio sent before authentication
  is refused outright, so nothing is buffered, transcribed or answered for an
  unauthenticated peer.

### Still open for v0.4.2

- camera/file/screen capture from the phone (the narrow device bridge covers
  ingestion today);
- Tailscale-first setup remains a documented manual step.

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
