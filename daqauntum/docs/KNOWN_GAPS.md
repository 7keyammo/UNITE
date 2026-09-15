# Known Gaps / Technical Debt

## P0 product usability

- Real-host setup is still multi-step; installation needs consolidation.
- Voice depends on optional local model setup and host audio configuration.
- Real-model latency can be high; routing/TTFT needs measured optimization on target hardware.
- GUI/browser session is not yet an authenticated mobile client.
- Diagnostics are fragmented across multiple doctor scripts.

## Runtime / concurrency

- SQLite + threaded/realtime workloads should receive sustained concurrency testing.
- Provider cancellation behavior varies and needs robust timeout/retry/backpressure policies.
- Long-running workflows need clearer job lifecycle/retry/checkpoint semantics.

## Presence / hardware

- Presence is primarily sampled local telemetry, not a generalized event bus.
- BLE GATT profiles are not yet modeled through a generic driver SDK.
- Serial/GPIO sensor protocols need adapters/schemas.
- MQTT support needs persistent subscription/state caching.
- Home Assistant integration needs event streaming and capability mapping.

## Computer use

- Open Interpreter/computer control is adapter/prototype-level and requires host-specific validation.
- Visual verification is probabilistic; critical operations need stronger deterministic confirmations where possible.
- Application-specific skills need regression scenarios.

## Security / identity

- No mature device-enrollment/authentication layer for remote clients yet.
- No dedicated encrypted secrets vault.
- Audit log tamper resistance is not production hardened.
- Public-internet exposure is intentionally not a supported default.

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
