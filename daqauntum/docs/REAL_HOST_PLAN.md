# DaQauntum Real-Host Convergence Plan

## What this document is

A maturity audit of every DaQauntum capability, separating what is written in
code from what actually runs on a real machine. `docs/REALITY_MAP.md` answers
"does this exist in the repository?". This answers "does this work on the
computer in front of me?", which is a different and harder question.

Regenerate the runtime half with `PYTHONPATH=. python scripts/doctor.py`.

## Maturity states

| State | Meaning |
|---|---|
| `IMPLEMENTED` | Code path exists in this repository |
| `INSTALLED` | Its dependency or binary is present on the host |
| `CONFIGURED` | The user has supplied credentials, paths or an allowlist |
| `WORKING` | Verified to actually run on this host |
| `HARDWARE_TESTED` | Verified against real physical hardware |
| `BLOCKED` | Cannot progress here; names what is missing |
| `NOT_IMPLEMENTED` | Does not exist yet |

A capability is only as mature as its weakest link. An implemented adapter with
no dependency installed is `IMPLEMENTED`, not `WORKING`.

## Which host this audit describes

**This audit was run on an ephemeral cloud build container, not on the Debian
machine DaQauntum is meant to live on.** That distinction matters more than any
individual row below, so it is stated first rather than buried.

| Property | This build host |
|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 6.18 |
| CPU | 4 × Intel Xeon @ 2.10GHz |
| RAM | 15 GiB |
| Storage | 30 GiB available |
| GPU | none (`nvidia-smi` absent, no `/dev/dri`) |
| Audio | none (`/dev/snd` absent) |
| Camera | none (no `/dev/video*`) |
| Sensors | none (no IIO, no battery, no serial) |
| systemd | offline (`systemctl --user` unavailable) |
| Network tooling | no `nmcli`, no `bluetoothctl`, no `avahi-browse` |

The consequence is concrete and unavoidable: **the entire voice conversation
loop, the whole Eyes + Hands demo, every device driver and the persistent
service cannot be validated here.** Anything claiming otherwise would be
guessing. Those rows are marked `BLOCKED — needs your Debian host`, and the
first job on that machine is `scripts/doctor.py`.

## Capability matrix

### Brain / inference

| Capability | State | Evidence |
|---|---|---|
| Multi-provider routing (Ollama / Claude CLI / OpenAI / Anthropic / mock) | `WORKING` | Routing exercised by the regression suite |
| Claude CLI provider | `WORKING` | `claude 2.1.272` present and reported available |
| Ollama local inference | `IMPLEMENTED` | Ollama not installed here; the preferred local/private route on your host |
| OpenAI provider | `IMPLEMENTED` | `OPENAI_API_KEY` not set |
| Anthropic provider | `IMPLEMENTED` | `ANTHROPIC_API_KEY` not set |
| Deterministic privacy/locality policy | `WORKING` | Covered by `evaluations/smoke_test.py` |
| Realtime / Balanced / Deep modes | `WORKING` | Route selection tested; **latency untuned for real hardware** |
| Mock fallback | `WORKING` | Architecture fallback only — never a usable route |

### Voice — the critical gap

| Capability | State | Evidence |
|---|---|---|
| Local STT adapters (faster-whisper / whisper.cpp) | `IMPLEMENTED` | `BLOCKED` — neither package installed, and no microphone exists here |
| TTS (system voice / Piper) | `IMPLEMENTED` | `BLOCKED` — no `/dev/snd`, no `espeak-ng`/`say`/`piper` |
| Full-duplex WebSocket transport | `WORKING` | `evaluations/duplex_smoke_test.py`; remote peers must authenticate |
| Barge-in / interruption | `IMPLEMENTED` | `BLOCKED` — needs real audio to validate perceptually |
| Partial transcription while speaking | `IMPLEMENTED` | `BLOCKED` — same |
| Four-stage latency measurement | `NOT_IMPLEMENTED` | `scripts/benchmark_latency.py` measures TTFT and total, not the STT and first-audio stages |

**This is the single largest gap between DaQauntum as built and DaQauntum as
intended.** Every step 2–10 of the target experience depends on it.

### Memory / knowledge

| Capability | State | Evidence |
|---|---|---|
| SQLite persistence, structured memory | `WORKING` | Core suite |
| Memory intelligence (salience, aging, contradiction) | `WORKING` | Core suite |
| Knowledge graph + provenance | `WORKING` | Core suite |
| Source indexing (files, PDF, notebooks) | `WORKING` | `pypdf` installed |
| Connected folders / web / feeds | `WORKING` | `evaluations/connectors_smoke_test.py` |
| Obsidian two-way long-term memory | `WORKING` | `evaluations/obsidian_smoke_test.py`; vault not yet created here |
| PostgreSQL/pgvector mirror | `IMPLEMENTED` | `psql` present, `psycopg` not installed, no DSN set |

### Senses / events

| Capability | State | Evidence |
|---|---|---|
| Event bus, dedupe/debounce/cooldown | `WORKING` | `evaluations/events_smoke_test.py` |
| Deterministic reaction rules | `WORKING` | Verified to execute nothing at L0/L2/L4 |
| Notification queue | `WORKING` | Same suite |
| Outbound webhook push | `WORKING` | Tested against a local HTTP endpoint |
| Presence layer (passive telemetry) | `IMPLEMENTED` | Runs, but reports nothing here: no battery, thermal or radio |
| Wi-Fi / Bluetooth / mDNS discovery | `IMPLEMENTED` | `BLOCKED` — no `nmcli`, `bluetoothctl` or `avahi-browse` |
| BLE GATT driver | `TESTED_WITH_MOCKS` | `bleak` not installed; no BLE hardware |
| Serial sensor driver | `TESTED_WITH_MOCKS` | `pyserial` not installed; no serial devices |
| MQTT persistent subscription | `TESTED_WITH_MOCKS` | `mosquitto-clients` not installed; no broker |
| Home Assistant ingestion | `TESTED_WITH_MOCKS` | No URL or token configured |

### Eyes + Hands

| Capability | State | Evidence |
|---|---|---|
| Screen / camera frame intake | `WORKING` | `evaluations/perception_smoke_test.py` |
| Vision routing (local or hosted) | `IMPLEMENTED` | No multimodal model configured here |
| Computer-use adapter | `IMPLEMENTED` | `BLOCKED` — Open Interpreter absent, no desktop session |
| Post-action visual verification | `TESTED_WITH_MOCKS` | PASS/FAIL/UNCERTAIN logic tested; never run against a real desktop |
| End-to-end look → propose → approve → act → verify | `TESTED_WITH_MOCKS` | `evaluations/computer_task_smoke_test.py`; needs a real desktop to reach `HARDWARE_TESTED` |

### Identity / remote access

| Capability | State | Evidence |
|---|---|---|
| Device enrollment, scoped tokens, revocation | `WORKING` | `evaluations/identity_smoke_test.py` |
| Control-API auth gate | `WORKING` | Driven over real HTTP as a remote caller |
| Realtime WebSocket auth | `WORKING` | Same suite |
| Phone client at `/m` | `WORKING` | Served and driven end to end; **never opened on a real phone** |
| Phone capture (photo / file) | `WORKING` | Endpoints tested, including path-traversal rejection |
| Tailscale transport | `IMPLEMENTED` | `BLOCKED` — `tailscale` not installed |

### Autonomy / learning / native model

| Capability | State | Evidence |
|---|---|---|
| Autonomous daily learning | `WORKING` | `evaluations/learning_smoke_test.py` |
| 7 AM / 7 PM timers | `IMPLEMENTED` | `BLOCKED` — systemd offline here |
| Persistent server service | `IMPLEMENTED` | `BLOCKED` — same |
| Dataset curation, gates, splits | `WORKING` | `evaluations/native_model_smoke_test.py` |
| Evaluation harness + safety probes | `WORKING` | Same suite |
| Model registry, human promotion, rollback | `WORKING` | Same suite |
| LoRA/QLoRA training | `IMPLEMENTED` | No GPU, no training stack. Correct: DaQauntum must never train itself |

### Interface

| Capability | State | Evidence |
|---|---|---|
| Local GUI + API | `WORKING` | `evaluations/gui_smoke_test.py` |
| Events / Devices / Universe views | `WORKING` | Exercised over HTTP |
| Atom brain structure visualization | `WORKING` | Renders live subsystem state, degraded states included |
| Guided demo | `WORKING` | `evaluations/demo_smoke_test.py` |
| Daily vs Lab mode split | `NOT_IMPLEMENTED` | Every subsystem is currently surfaced at equal weight |

## Ordered plan

### On your Debian host — you, not me

1. `PYTHONPATH=. python scripts/doctor.py` — writes `data/diagnostics/SYSTEM_HEALTH.md`.
2. Install and start Ollama, pull a small model. This is the local/private route
   and the only way DaQauntum answers without a hosted provider.
3. `pip install -r requirements-voice.txt && python scripts/setup_local_voice.py`.
4. `PYTHONPATH=. python scripts/benchmark_latency.py` against real models.
5. `PYTHONPATH=. python scripts/install_server_service.py`.
6. `PYTHONPATH=. python scripts/setup_remote_access.py --enrol "your phone"`.

### In the repository — me

1. **Four-stage latency instrumentation.** Separate speech-end → STT,
   STT → first token, first token → first audio, and total. Without the
   breakdown, "it feels slow" cannot be turned into a fix.
2. **Daily mode / Lab mode.** Talking to DaQauntum should not require
   understanding its architecture.
3. ~~One reliable Eyes + Hands slice~~ — built and mock-tested. It now needs a
   real desktop with Open Interpreter and a vision model to reach
   `HARDWARE_TESTED`.
4. Real-hardware validation of one driver path, which is the last open v0.4.1
   acceptance criterion.

## Honest summary

DaQauntum is a well-tested **runtime** and an unvalidated **appliance**. Its
reasoning, memory, knowledge, event, identity and native-model layers are real
and covered by 17 regression suites. Its voice, vision, device and service
layers are real code that has never met real hardware.

The gap is not missing features. It is that nothing has yet been proven on the
machine it is supposed to live on.
