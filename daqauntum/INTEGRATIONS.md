# DaQauntum v0.4.0 Integration Matrix

DaQauntum remains the identity, policy, memory, provenance and orchestration layer. Mature external systems are used as replaceable modules rather than copied into the core.

| Integration | Role | Authority |
|---|---|---|
| Ollama | local model inference | reasoning only |
| Claude Code CLI | authenticated hosted brain via local CLI | reasoning; classified hosted |
| OpenAI API | hosted model inference | reasoning only |
| Anthropic API | hosted model inference | reasoning only |
| Open Interpreter | computer-use/coding arm | permission-gated |
| LangGraph | durable agent/workflow engine | inherits DaQauntum policy |
| Home Assistant | high-level physical-world / sensor bridge | commands permission-gated |
| MQTT | generic Raspberry Pi / ESP32 / IoT bus | read L0; publish gated |
| Flux MCP | hardware/PCB design specialist | adapter declaration; actions require mapping |
| PostgreSQL + pgvector | scalable memory/vector backend | no action authority |
| Tailscale Serve | secure remote transport | enabling is permission-gated |
| Obsidian | human-facing knowledge interface | file/vault boundary |
| NetworkManager (`nmcli`) | Wi-Fi observation/profile activation | scan explicit; activation gated |
| BlueZ (`bluetoothctl`) | Bluetooth observation/discovery/connect | scan explicit; connect gated |
| Avahi (`avahi-browse`) | local mDNS/Bonjour discovery | read-only explicit scan |

## Device philosophy

DaQauntum does not attempt to become the driver for every device. It discovers capabilities, routes them through adapters, and applies one permission model across them.

For unknown hardware, the preferred sequence is:

```text
discover
  ↓
identify protocol
  ↓
install/use adapter
  ↓
read-only observation
  ↓
permission-mapped actions
  ↓
verified automation
```
