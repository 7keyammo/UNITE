# DaQauntum Architecture

## v0.5 — the scientific layer

The scientific core sits beside the existing subsystems, not above them. It
shares the kernel's one SQLite connection and its one event bus, and owns no
transport, model or sensor of its own.

```text
                        SCIENTIFIC CORE
                              │
        Experiment → Hypothesis → Measurement → Analysis
                              │
                    Evidence (of a stated kind)
                              │
                           Claim
                              │
   ┌──────────────┬───────────┴───────────┬──────────────┐
   │              │                       │              │
 units        validation              backends        plots
 (SI, ±)   (fail-closed)   sensors / research / sim    (SVG)
```

Three rules shape every arrow in that diagram.

**Kinds of knowledge do not blur.** `EvidenceKind` separates measurement,
observation, calculation, simulation, literature and interpretation.
`Measurement.evidence_kind` is computed from the record rather than stored
beside it, so a value cannot carry a label contradicting its own provenance.

**Suppression is for telemetry, not for data.** The event bus deduplicates by
design. Scientific events carry a dedupe key unique to the object they report,
so two identical readings are always two events. An analysis over deduplicated
data is an analysis of data that was silently altered.

**Refusals over approximations.** Mismatched series raise rather than
truncating; an unavailable sensor raises rather than returning a plausible
number; an unimplemented adapter raises rather than returning an empty list.
Where the system cannot do something correctly it says so, because every one
of those alternatives produces a wrong answer that looks right.

See `docs/SCIENTIFIC_CORE.md`, `docs/EVENTS.md` and `docs/INTEGRATIONS.md`.

## Goal (v0.4)

v0.4.0 turns the v0.3.x architecture into a coherent live assistant: a persistent brain with voice, memory, perception, external information, ambient awareness, and permission-gated action.

```text
                         DAQAUNTUM ATOM
                               │
             identity / memory / provenance
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
        BRAIN               SENSES               HANDS
          │                    │                    │
  local / CLI / cloud     voice / screen      computer use
  planner/executor/       camera / presence   Home Assistant
  critic                  files / sensors     MQTT / network
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                      PERMISSION MANAGER
                               │
                  execute / confirm / deny
                               │
                              ACT
                               │
                         verify / learn
```

## Presence Layer

`presence/manager.py` provides local-first awareness. Passive sampling is intentionally different from active radio discovery.

### Passive sampling

The persistent host runs `PresenceMonitorThread` at the configured interval. It can observe available local signals such as:

- system/battery/thermal metrics;
- current network interfaces and active Wi-Fi connection;
- local IP address;
- Bluetooth adapter state and already-paired devices;
- Linux IIO sensor nodes;
- cameras, serial/USB and sound-device presence;
- Home Assistant integration health.

No Wi-Fi or Bluetooth discovery scan occurs from the passive thread.

### Explicit discovery

User-triggered tools/endpoints can request:

- `wifi_scan`;
- `bluetooth_scan`;
- `service_discovery` for mDNS/Bonjour.

These operations are read-only discovery, but the GUI labels them explicitly so the user knows a radio/network scan is occurring.

### State-changing connection actions

- `wifi_activate_profile` activates an OS-saved NetworkManager profile.
- `bluetooth_connect` connects an already-paired device.
- `mqtt_publish` can affect IoT state.

They require DaQauntum tool permission. v0.4.0 intentionally does not accept or store Wi-Fi passwords and does not automate Bluetooth pairing/PIN flows.

## Ambient event awareness

Each presence sample is stored in `presence_samples`. DaQauntum derives local alerts such as low battery, high temperature, Wi-Fi connection changes, or serial-device attach/remove events. Important alerts may enter reasoning context even when the user did not explicitly ask for device status.

This is the first step toward ambient awareness without granting open-ended autonomy.

## Brain providers

v0.4.0 supports:

- `ollama` — local model server;
- `claude_cli` — authenticated Claude Code CLI in one-turn plan mode;
- `openai` — hosted API;
- `anthropic` — hosted API;
- `mock` — deterministic architecture fallback.

Privacy policy treats Claude CLI as hosted inference. LOCAL operation mode restricts routing to local providers.

## IoT message bus

The optional MQTT adapter uses `mosquitto_sub` / `mosquitto_pub` if installed.

- read one topic message: read-only;
- publish: state-changing permission-gated action.

This provides a generic bridge for Raspberry Pi, ESP32, microcontrollers, lab sensors and robotics while Home Assistant remains the preferred high-level physical-world integration when available.

## Guided Demo

`demo/manager.py` builds a live walkthrough from actual runtime capability status. It does not claim unavailable features are configured.

The demo can speak each step through DaQauntum's voice engine and is available through:

- GUI **▷ Guided demo**;
- `python daqauntum_gui.py --demo`;
- `python scripts/run_demo.py --speak`.

## Human-facing optimization

The Chat view now exposes four immediate actions:

- Talk;
- Guided demo;
- Sense environment;
- What can you do?

The intent is to make the default interaction useful before a user understands the deeper developer tabs.

## Security boundaries

1. Main GUI defaults to loopback.
2. Phone ingestion is a separate paired bridge.
3. Tailscale is preferred for remote access.
4. Passive presence never joins networks or pairs devices.
5. Sensitive inference can be forced local.
6. State-changing tools still use L0-L4 permissions.
7. Computer autonomy does not override tool permissions.
8. Autonomous learning does not silently fine-tune model weights.
