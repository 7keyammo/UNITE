# Known Gaps / Technical Debt

## P0 product usability

- Real-host setup is still multi-step; installation needs consolidation.
- Voice depends on optional local model setup and host audio configuration.
- Real-model latency can be high. `scripts/benchmark_latency.py` now measures TTFT and total turn time, but the tuning decision still has to be made on the target hardware.
- ~~GUI/browser session is not yet an authenticated mobile client.~~ Resolved in v0.4.2-dev: an enrolled phone client is served at `/m`.
- ~~Diagnostics are fragmented across multiple doctor scripts.~~ Resolved in v0.4.1-dev by `scripts/doctor.py`; the focused doctors remain for single-subsystem checks.

## Runtime / concurrency

- SQLite + threaded/realtime workloads should receive sustained concurrency testing.
- Provider cancellation behavior varies and needs robust timeout/retry/backpressure policies.
- Long-running workflows need clearer job lifecycle/retry/checkpoint semantics.

## Presence / hardware

- ~~Presence is primarily sampled local telemetry, not a generalized event bus.~~ Resolved in v0.4.1-dev: presence snapshots now feed a normalized event bus.
- BLE GATT profiles are modeled through the v0.4.1 driver SDK, but no BLE device has been validated on real hardware yet.
- Serial sensors have an adapter with pluggable parsers; GPIO has none, and neither is hardware-validated yet.
- ~~MQTT support needs persistent subscription/state caching.~~ Resolved in v0.4.1-dev; still untested against a live broker.
- Home Assistant state ingestion polls `/api/states`. True event streaming over WebSocket and richer capability mapping remain open.

## Computer use

- Open Interpreter/computer control is adapter/prototype-level and requires host-specific validation.
- Visual verification is probabilistic; critical operations need stronger deterministic confirmations where possible.
- Application-specific skills need regression scenarios.

## Events and reactions

- Reaction conditions are single-level AND only; there is no OR/grouping yet.
- Notifications are local. A mobile push adapter interface exists but has no
  implementation, which is v0.4.2 work.
- The driver poll loop is a fixed interval; there is no adaptive backoff when a
  driver repeatedly fails.
- No driver has been validated against real hardware, so the v0.4.1 definition
  of done is not yet met.

## Security / identity

- ~~No mature device-enrollment/authentication layer for remote clients yet.~~ Resolved in v0.4.2-dev: enrollment codes, scoped devices, hashed session tokens and revocation.
- No dedicated encrypted secrets vault. Device tokens and enrollment codes are hashed at rest, but API keys still live in environment variables.
- Audit log tamper resistance is not production hardened.
- Public-internet exposure is intentionally not a supported default.

## Remote access

- The phone client is a served page, not an installable PWA: no offline cache,
  no install prompt, no background push registration.
- Camera, file and screen capture from the phone still go through the narrower
  device bridge rather than the authenticated client.
- Push delivery is outbound webhook only; there is no APNs/FCM adapter.
- Tailscale setup remains a manual documented step rather than guided setup.
- Token rotation is manual; there is no automatic refresh before expiry.

## Knowledge / memory

- Local semantic encoder is intentionally lightweight; scalable embeddings/backend strategy remains optional.
- Contradiction/consolidation algorithms are heuristic.
- Source-quality/reliability scoring needs development.

## Native model

- No native model weights exist yet.
- Current work only prepares candidate datasets.
- Need dataset curation, holdout evaluations, fine-tuning pipeline, registry and promotion policy.

## Packaging

- Project is a source repository, not yet a polished OS package/container/desktop installer.
- Python/environment dependency pinning and reproducibility should be strengthened.
