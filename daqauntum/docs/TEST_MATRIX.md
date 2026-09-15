# DaQauntum Test Matrix

## Base checks

```bash
PYTHONPATH=. python scripts/verify_release.py
PYTHONPATH=. python evaluations/smoke_test.py
```

## Subsystem tests

| Area changed | Required regression |
|---|---|
| Brain/model routing/policy/permissions/memory/tools | `evaluations/smoke_test.py` |
| STT/TTS | `evaluations/voice_smoke_test.py` |
| Streaming/cancellation | `evaluations/realtime_smoke_test.py` |
| WebSocket/full duplex | `evaluations/duplex_smoke_test.py` |
| Autonomous learning/native dataset | `evaluations/learning_smoke_test.py` |
| Folder/phone/web connectors | `evaluations/connectors_smoke_test.py` |
| Workspaces/agents/Obsidian | `evaluations/workspaces_smoke_test.py` |
| Third-party adapters/persistent runtime | `evaluations/integrations_smoke_test.py` |
| Screen/camera/computer action | `evaluations/perception_smoke_test.py` |
| Ambient Presence/network/device discovery | `evaluations/presence_smoke_test.py` |
| Guided demo/readiness | `evaluations/demo_smoke_test.py` |
| GUI/API changes | `evaluations/gui_smoke_test.py` |
| Event bus/reaction rules/notifications | `evaluations/events_smoke_test.py` |
| Device drivers/BLE/serial/MQTT/Home Assistant | `evaluations/drivers_smoke_test.py` |
| Device identity/auth gate/mobile client | `evaluations/identity_smoke_test.py` |

## Full matrix

```bash
PYTHONPATH=. python evaluations/smoke_test.py
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

## Missing test maturity

The current suite is smoke/integration-heavy. Future work should add:

- unit tests for policy/permissions (event-rule matching and condition
  operators are now covered by `evaluations/events_smoke_test.py`);
- migration fixtures covering multiple prior release schemas;
- deterministic API contract tests;
- browser automation/E2E tests for key UI workflows;
- fault injection (provider unavailable, DB lock, network loss, reconnect);
- latency regression benchmarks;
- security regression tests for path traversal, SSRF, credential leakage and permission bypass;
- hardware adapter mocks/simulators beyond the mock driver used in
  `evaluations/drivers_smoke_test.py`;
- a real-hardware sensor path validated on the target host, which the
  v0.4.1 definition of done still requires.
