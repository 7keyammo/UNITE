# DaQauntum Reality Map

This file prevents architecture diagrams from being mistaken for configured real-world capability.

## Status vocabulary

- **IMPLEMENTED** - code path and regression coverage exist in this repository.
- **OPTIONAL DEPENDENCY** - implemented adapter requires another package/service/binary.
- **CONFIG REQUIRED** - credentials/model/path/service must be configured by the user.
- **HARDWARE REQUIRED** - feature needs physical hardware to validate.
- **PROTOTYPE** - useful architecture exists but is not production hardened.
- **PLANNED** - roadmap only; do not claim it works.

## Capability matrix

| Capability | Repo state | External requirement | Reality note |
|---|---|---|---|
| Text chat | IMPLEMENTED | Real model provider | Mock is only architecture fallback |
| Ollama local inference | IMPLEMENTED | Ollama + pulled model | Best local/privacy path |
| Claude CLI inference | IMPLEMENTED | Authenticated `claude` CLI | Treat as hosted inference |
| OpenAI/Anthropic APIs | IMPLEMENTED | API credentials | Hosted inference |
| Realtime/Balanced/Deep modes | IMPLEMENTED | Model route | Latency depends heavily on provider/hardware |
| Structured memory | IMPLEMENTED | none | SQLite default |
| Knowledge graph/provenance | IMPLEMENTED | none | Local SQLite graph tables |
| Local file/source indexing | IMPLEMENTED | optional `pypdf` for PDFs | Source similarity is not evidence |
| Connected folders | IMPLEMENTED | user grants explicit folder | Read scope is connector-specific |
| Phone upload bridge | IMPLEMENTED / PROTOTYPE | same LAN + explicit bridge start | Ingestion only, not full mobile client |
| Local STT | IMPLEMENTED | faster-whisper or whisper.cpp + model | User's photographed machine was not configured yet |
| TTS | IMPLEMENTED | system voice or Piper | Quality varies by host |
| Full-duplex transport | IMPLEMENTED / PROTOTYPE | local browser + voice stack | Not equivalent to telephony/PSTN; remote peers must authenticate |
| Four-stage voice latency measurement | IMPLEMENTED | a real spoken turn | Measured from live turns; timings only, never transcript |
| Daily / Lab interface modes | IMPLEMENTED | none | Daily hides developer surfaces; nothing is removed |
| Autonomous daily learning | IMPLEMENTED | persistent host/timers + usable model | Creates reports/traces; no weight updates |
| 7 AM / 7 PM jobs | IMPLEMENTED | systemd user timers installed | Must be installed on real host |
| Screen capture | IMPLEMENTED | browser/OS permission | Explicit opt-in |
| Camera capture | IMPLEMENTED | camera/browser permission | Explicit opt-in |
| Vision analysis | IMPLEMENTED | local multimodal Ollama or hosted provider | Local mode must not cloud-fallback |
| Computer control | IMPLEMENTED / PROTOTYPE | Open Interpreter/adapter | Must remain permission-gated |
| Visual post-action verification | IMPLEMENTED | vision route + a fresh capture | Verdict comes from an independent capture, not from the adapter that acted; fails closed to UNCERTAIN |
| Guided Eyes + Hands task | IMPLEMENTED / TESTED_WITH_MOCKS | shared screen + computer-use adapter | look → propose → approve → act → verify, fully audited; never validated on a real desktop |
| Passive presence sensing | IMPLEMENTED / PROTOTYPE | host OS support | Observes approved local telemetry only |
| Event bus + reaction rules | IMPLEMENTED | none | Deterministic matching; rules only notify, queue or propose |
| Notification queue | IMPLEMENTED | none | Local queue; mobile push adapter is PLANNED |
| Reaction tool proposals | IMPLEMENTED | none | Recorded only; execution always needs explicit user approval |
| Consolidated host doctor | IMPLEMENTED | none | Writes `data/diagnostics/SYSTEM_HEALTH.md` |
| Latency benchmark | IMPLEMENTED | a real model route | Mock provider is excluded from recommendations |
| Wi-Fi state/scan | IMPLEMENTED | NetworkManager/nmcli where applicable | Scan is explicit; joining is state-changing |
| Bluetooth metadata/scan/connect | IMPLEMENTED / PROTOTYPE | bluetoothctl/BlueZ | No automated pairing/PIN flows |
| BLE GATT generic drivers | IMPLEMENTED / OPTIONAL DEPENDENCY | `bleak` + allowlisted device + declared GATT profile | Reads declared characteristics only; no pairing flow |
| Serial sensor drivers | IMPLEMENTED / HARDWARE REQUIRED | `pyserial` + allowlisted port | Read-only unless `allow_writes` is set; not yet validated on real hardware |
| MQTT one-shot read/publish | IMPLEMENTED | mosquitto CLI + broker | Publish is state-changing |
| MQTT persistent subscription cache | IMPLEMENTED | mosquitto-clients + broker | Topic allowlist re-checked locally; publish stays permission-gated |
| Home Assistant bridge | IMPLEMENTED / OPTIONAL DEPENDENCY | HA URL/token | State ingestion for listed entities; service calls stay permission-gated |
| Tailscale Serve | IMPLEMENTED adapter | Tailscale installed/authenticated | Preferred remote transport |
| Obsidian export | IMPLEMENTED | Obsidian optional | Export is not the authoritative memory store |
| PostgreSQL/pgvector | OPTIONAL adapter | PostgreSQL + pgvector | SQLite remains default authority |
| Flux hardware design | MCP-ready / PROTOTYPE | Flux MCP setup | Do not claim autonomous PCB control |
| Native DaQauntum fine-tuned model | PLANNED | GPU/training pipeline/base model | Dataset preparation only today |
| Multi-atom network | PLANNED | identity/security/network protocol | No peer-memory sharing yet |
| Phone client (chat/inbox/approvals) | IMPLEMENTED / PROTOTYPE | enrolled device + private network | Served at `/m`; not an app-store app or a PWA |
| Device identity + enrollment | IMPLEMENTED | none | Single-use codes, hashed tokens, scopes, revocation |
| Remote API authentication | IMPLEMENTED | none | Loopback trusted by default; remote needs a device token |
| Remote approvals | IMPLEMENTED | device with `approve` scope | Approval never raises the permission level |
| Outbound push notifications | IMPLEMENTED / OPTIONAL | a webhook endpoint you control | Off by default; sends a narrow payload only |
| Production mobile client | PARTIAL | mobile/PWA work | Phone client exists; offline support, install prompt and capture are not done |

## v0.4.2 status note

Device identity, the API auth gate and the phone client are implemented and
covered by `evaluations/identity_smoke_test.py`. Remote access is still expected
to arrive over Tailscale or another authenticated private transport: the auth
layer is defence in depth, not a licence to expose the control API publicly.

## v0.4.1 status note

The v0.4.1 event, reaction and driver code is implemented and covered by
`evaluations/events_smoke_test.py` and `evaluations/drivers_smoke_test.py`,
which run entirely on mocks. No driver in this table has yet been validated
against real hardware on the target host, so the release carries a `-dev`
suffix. Describe these adapters as implemented and awaiting hardware
validation, not as working device control.

## Important rule

When presenting status to users or developers, prefer this sentence structure:

> "DaQauntum has an implemented adapter for X; it becomes usable when Y is installed/configured."

Do not say "DaQauntum can control X" solely because a future interface or adapter stub exists.
