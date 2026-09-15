# DaQauntum v0.4.0 Release Contract

A v0.4.0 release is valid only when:

1. `VERSION` is exactly `0.4.0`.
2. The archive contains the complete repository, not a patch.
3. The SHA-256 release manifest validates.
4. The core brain/memory/permission regression suite passes.
5. Voice, realtime, full-duplex, autonomous learning, connector, workspace, integration and perception suites pass.
6. Presence Layer tests pass without requiring real radio hardware.
7. Guided Demo tests pass without requiring a hosted model.
8. GUI smoke tests pass from a clean temporary database.
9. Runtime data, credentials, `config.yaml`, `.venv`, caches and test databases are absent from the release archive.
10. A fresh extraction of the final ZIP passes verification and regression tests again.

v0.4.0 safety invariants:

- passive awareness does not initiate nearby-radio scans;
- radio discovery is explicit;
- Wi-Fi passwords are not stored by DaQauntum;
- Bluetooth pairing is not automated;
- network/device state changes remain permission-gated;
- MQTT publish remains permission-gated;
- sensitive model routing can force local inference;
- autonomous learning does not modify neural weights.


## Developer handoff files

This handoff edition additionally requires `CLAUDE.md`, `AGENTS.md`, `TASKS.md`, `docs/HANDOFF.md`, security/reality/test/release documentation, and copy-paste starter prompts under `handoff/`. These files are part of the release manifest and must travel with the complete repository.
