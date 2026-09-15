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
| Full-duplex transport | IMPLEMENTED / PROTOTYPE | local browser + voice stack | Not equivalent to telephony/PSTN |
| Autonomous daily learning | IMPLEMENTED | persistent host/timers + usable model | Creates reports/traces; no weight updates |
| 7 AM / 7 PM jobs | IMPLEMENTED | systemd user timers installed | Must be installed on real host |
| Screen capture | IMPLEMENTED | browser/OS permission | Explicit opt-in |
| Camera capture | IMPLEMENTED | camera/browser permission | Explicit opt-in |
| Vision analysis | IMPLEMENTED | local multimodal Ollama or hosted provider | Local mode must not cloud-fallback |
| Computer control | IMPLEMENTED / PROTOTYPE | Open Interpreter/adapter | Must remain permission-gated |
| Visual post-action verification | IMPLEMENTED / PROTOTYPE | vision route | PASS/FAIL/UNCERTAIN, not infallible |
| Passive presence sensing | IMPLEMENTED / PROTOTYPE | host OS support | Observes approved local telemetry only |
| Wi-Fi state/scan | IMPLEMENTED | NetworkManager/nmcli where applicable | Scan is explicit; joining is state-changing |
| Bluetooth metadata/scan/connect | IMPLEMENTED / PROTOTYPE | bluetoothctl/BlueZ | No automated pairing/PIN flows |
| BLE GATT generic drivers | PLANNED | Bleak or platform backend | v0.4.1 target |
| Serial sensor drivers | PLANNED | pyserial + hardware | v0.4.1 target |
| MQTT one-shot read/publish | IMPLEMENTED | mosquitto CLI + broker | Publish is state-changing |
| MQTT persistent subscription cache | PLANNED | MQTT client | v0.4.1 target |
| Home Assistant bridge | IMPLEMENTED / OPTIONAL DEPENDENCY | HA URL/token | Deeper event ingestion planned |
| Tailscale Serve | IMPLEMENTED adapter | Tailscale installed/authenticated | Preferred remote transport |
| Obsidian export | IMPLEMENTED | Obsidian optional | Export is not the authoritative memory store |
| PostgreSQL/pgvector | OPTIONAL adapter | PostgreSQL + pgvector | SQLite remains default authority |
| Flux hardware design | MCP-ready / PROTOTYPE | Flux MCP setup | Do not claim autonomous PCB control |
| Native DaQauntum fine-tuned model | PLANNED | GPU/training pipeline/base model | Dataset preparation only today |
| Multi-atom network | PLANNED | identity/security/network protocol | No peer-memory sharing yet |
| Production mobile client | PLANNED | mobile/PWA work | Phone bridge is not this |

## Important rule

When presenting status to users or developers, prefer this sentence structure:

> "DaQauntum has an implemented adapter for X; it becomes usable when Y is installed/configured."

Do not say "DaQauntum can control X" solely because a future interface or adapter stub exists.
