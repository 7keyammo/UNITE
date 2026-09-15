# DaQauntum v0.4.0 — Presence + Guided Demo

DaQauntum v0.4.0 is the first release aimed at being **walk-up usable**, not only developer-testable.

> **Developer handoff edition:** This repository includes `CLAUDE.md`, `AGENTS.md`, `TASKS.md`, and `docs/HANDOFF.md` so Claude Code and Codex can continue development without the original chat history. Start there before editing code.
 It keeps the persistent server, realtime/full-duplex voice, structured memory, knowledge graph, source intelligence, connected folders/phone/web, autonomous learning, native-model seed corpus, workspaces/agents, integrations, screen perception, and permission-gated computer control from v0.3.x.

v0.4.0 adds:

- **Presence Layer** for passive local awareness of network state, battery, temperatures, local sensor buses, cameras, serial/USB devices, and paired Bluetooth metadata.
- Explicit nearby **Wi-Fi**, **Bluetooth**, and **mDNS/Bonjour** discovery controls.
- Permission-gated activation of saved Wi-Fi profiles and connection to already-paired Bluetooth devices.
- Optional **MQTT** bridge for Raspberry Pi / ESP32 / IoT messaging.
- Optional authenticated **Claude Code CLI brain** support in addition to OpenAI, Anthropic API, Ollama, and mock fallback.
- A **Guided Demo** that can speak and walk a new user through the live system.
- Main-screen shortcuts for **Talk / Demo / Sense / Capabilities**.
- A first-run readiness wizard and Presence diagnostics.

## Quick start

```bash
unzip daqauntum-alpha-v0.4.0.zip
cd daqauntum-alpha-v0.4.0
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml
```

Run the readiness wizard:

```bash
PYTHONPATH=. python scripts/first_run.py
```

Launch the GUI directly into the guided demo:

```bash
python daqauntum_gui.py --demo
```

Or launch normally:

```bash
python daqauntum_gui.py
```

Open: `http://127.0.0.1:8765/`

## Make sure DaQauntum has a real brain

v0.4.0 can use one of these routes:

1. **Ollama local model** — best privacy/locality path.
2. **Claude Code CLI** — if the `claude` CLI is already authenticated on the machine.
3. **OpenAI API** — set `OPENAI_API_KEY`.
4. **Anthropic API** — set `ANTHROPIC_API_KEY`.
5. Mock fallback — only for testing architecture.

If Ollama is installed but no model is pulled, you can explicitly pull one:

```bash
PYTHONPATH=. python scripts/first_run.py --pull-model YOUR_MODEL_NAME
```

DaQauntum classifies `claude_cli` as hosted inference for privacy policy purposes, even though the CLI process runs locally.

## Local voice

The browser can fall back to browser speech features, but the private/local path is:

```bash
pip install -r requirements-voice.txt
PYTHONPATH=. python scripts/setup_local_voice.py --download-whisper base.en --configure
```

Then start Call Mode. Local speech-to-text, local/system text-to-speech, streaming, hands-free turns, and barge-in remain available from v0.3.x.

## Presence Layer

Open **◎ Presence** in the GUI.

Passive awareness can observe:

- active network interfaces / current Wi-Fi connection;
- local IP address;
- battery level and power state;
- CPU/memory telemetry when available;
- thermal sensors;
- Linux IIO sensor devices;
- local camera devices;
- serial / USB device nodes;
- sound-device availability;
- Bluetooth adapter state and paired-device metadata;
- configured Home Assistant availability.

Passive awareness **does not** continuously scan nearby radios.

Explicit buttons are provided for:

- nearby Wi-Fi scan;
- nearby Bluetooth scan;
- local Bonjour/mDNS service discovery.

State-changing actions such as activating a saved Wi-Fi profile or connecting a paired Bluetooth device go through DaQauntum's normal L0–L4 permission gate.

### Diagnostics

```bash
PYTHONPATH=. python scripts/presence_doctor.py
```

## MQTT / IoT

v0.4.0 adds a generic MQTT bridge so DaQauntum can eventually work with Raspberry Pis, ESP32 devices, sensors, robotics and automations without creating a bespoke protocol for every device.

Install the Mosquitto client utilities on the host OS, then configure environment variables:

```bash
export MQTT_HOST="192.168.1.20"
export MQTT_PORT="1883"
export MQTT_USERNAME=""
export MQTT_PASSWORD=""
```

DaQauntum exposes:

- `mqtt_read` — read-only, one message from a topic;
- `mqtt_publish` — state-changing and permission-gated.

## Guided demo

GUI: click **▷ Guided demo**.

Terminal version:

```bash
PYTHONPATH=. python scripts/run_demo.py --speak
```

The walkthrough covers:

1. persistent DaQauntum identity;
2. local/hybrid/cloud brain routing;
3. two-way voice;
4. ambient Presence Layer;
5. screen/camera perception;
6. permission-gated computer control;
7. connected knowledge;
8. autonomous learning and native-model dataset preparation.

## Persistent server

Run DaQauntum as an always-on process:

```bash
python daqauntum_server.py
```

Or install the user service:

```bash
python scripts/install_server_service.py
```

Then reopen the GUI without restarting the brain:

```bash
python daqauntum_gui.py --client-only
```

## Phone / tablet ingestion

Start with the paired LAN ingestion bridge:

```bash
python daqauntum_gui.py --device-bridge
```

The main control GUI stays loopback-only. The separate bridge can pair a phone/tablet for file and note ingestion.

For secure remote use away from home, use the existing Tailscale Serve integration rather than exposing the GUI directly to the public internet.

## Safety model

DaQauntum deliberately separates:

- **sensing** from **acting**;
- **discovery** from **connection**;
- **reasoning strength** from **authority**;
- **local inference** from **hosted inference**;
- **learning experience** from **model-weight fine-tuning**.

Examples:

- reading battery temperature: L0/read-only;
- scanning Wi-Fi after you request it: explicit read-only discovery;
- activating a saved Wi-Fi profile: state-changing approval;
- connecting an already-paired Bluetooth device: state-changing approval;
- MQTT publish: state-changing approval;
- delete/send/purchase/publish/credential changes: always-confirm according to global policy.

## Tests

```bash
PYTHONPATH=. python scripts/verify_release.py
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
```

## What v0.4.0 is — and is not

v0.4.0 is a working local-first assistant/runtime that can chat through a configured real model, speak/listen, maintain memory, learn autonomously, observe approved local context, discover nearby radios on request, ingest external information, perceive screen/camera context, and prepare permission-gated actions.

It is **not yet** a universal autonomous ambient agent that can pair with arbitrary hardware, understand every BLE GATT profile, control unknown devices, or safely roam an open network without explicit boundaries. Those capabilities belong in later adapter/driver releases.
