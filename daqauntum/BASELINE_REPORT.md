# DaQauntum Baseline Report — before v0.5 Scientific Core

Audited 2026-09-18 at commit `b153f87`, on a cloud build container.

## 1. Current version and state

| | |
|---|---|
| VERSION | `0.4.2-dev` |
| Branch | `claude/fervent-knuth-q9br3z` |
| Working tree | clean |
| Python | 3.11.15 |
| Runtime dependencies | 6 (PyYAML, rich, openai, pypdf, websockets, psutil) |

## 2. Baseline test results — verified, not assumed

```
19 / 19 suites pass
scripts/verify_release.py: PASS (165 required files)
```

`evaluations/smoke_test.py` plus 18 `*_smoke_test.py` files. Note for future
agents: the glob `evaluations/*_smoke_test.py` returns 18 and silently omits the
core `smoke_test.py`, whose name has no underscore prefix. Enumerate the suite
explicitly or use `evaluations/*smoke_test.py`.

## 3. Current architecture

Flat top-level Python packages, orchestrated by one kernel. There is no
`src/` layout, no framework and no ORM.

```
daqauntum_gui.py / daqauntum_server.py / daqauntum.py   entrypoints
        |
runtime/host.py            process host: HTTP server, duplex WS, timers, bridge
        |
core/kernel.py             DaQauntumKernel — constructs and owns every subsystem
        |
        +-- core/policy.py, core/planner.py, core/critic.py, core/brain.py
        +-- core/permissions.py    L0-L4 authority gate, outside the model
        +-- tools/registry.py      the ONLY execution boundary
        |
        +-- memory/       SQLite store + structured memory + intelligence
        +-- knowledge/    graph, provenance, source indexing
        +-- events/       event bus, deterministic reaction rules, notifications
        +-- drivers/      optional BLE / serial / MQTT / Home Assistant adapters
        +-- identity/     device enrollment, scoped tokens, revocation
        +-- perception/   screen and camera frames, vision routing
        +-- computer/     computer-use controller + guided task flow
        +-- presence/     passive local telemetry
        +-- native_model/ dataset curation, evaluation, model registry
        +-- obsidian/     two-way vault memory bridge
        +-- realtime/     duplex transport, turn timeline
        +-- universe/     atom/brain state for the GUI
        |
interface/server.py        stdlib HTTP API + static GUI, scope-gated
interface/web/             browser GUI (index.html, app.js, universe.js) + /m phone client
```

### Entry point
`daqauntum_gui.py` → `runtime/host.py` → `core/kernel.py`. The kernel is the
composition root; every subsystem is constructed there and reachable as an
attribute.

### Event architecture
`events/` is already a real bus, not a stub:

- `events/models.py` — frozen `Event(source, kind, subject, severity, message, attributes, occurred_at, dedupe_key)` with a stable state fingerprint.
- `events/bus.py` — persistence to `device_events`, deterministic suppression (dedupe, debounce, per-kind cooldown, unchanged-state), subscriber dispatch.
- `events/rules.py` — deterministic reaction rules. Actions are limited to notify / queue task / propose tool call; **a rule never executes anything.**
- `events/notifications.py` — durable notification queue.

**Critical constraint for v0.5:** the bus deliberately suppresses repeats. Two
identical readings collapse into one stored event with a repeat counter. That is
correct for a noisy sensor and *wrong* for scientific measurements, where two
equal readings are two distinct facts. Scientific events must carry a unique
dedupe key so suppression can never discard data.

### Persistence
One SQLite database (`data/daqauntum.db`) opened once by `memory/store.py`, with
every subsystem calling `CREATE TABLE IF NOT EXISTS` on the shared connection.
19 modules follow this pattern. There is no migration framework; schema changes
are additive by convention.

### API / UI boundary
`interface/server.py` is a stdlib `ThreadingHTTPServer`. Business rules live in
kernel subsystems; the server marshals JSON. Authorization uses two separate
prefix tables (`READ_SCOPES`, `WRITE_SCOPES`) and defaults unmapped endpoints to
`admin`, so a new endpoint is closed to remote devices until opened on purpose.

### Configuration and secrets
`core/config.py` holds a `DEFAULT_CONFIG` dict deep-merged with `config.yaml`,
then overridden by environment variables. Secrets are referenced by env var name
only. `config.yaml`, `.env`, `data/` and `*.db` are gitignored and excluded from
release archives.

## 4. What is genuinely solid

- The permission gate, and the single execution boundary through `tools/registry.py`.
- The event bus, reaction engine and notification queue.
- Structured memory, the knowledge graph and source provenance.
- Device identity, the API auth gate and the phone client.
- The native-model lab: curation gates, split leakage checks, human-gated promotion.
- 19 regression suites, several of them negative-tested.

## 5. Technical debt and risks

| Item | Severity | Detail |
|---|---|---|
| **Test pollution** | medium | `evaluations/gui_smoke_test.py` and `learning_smoke_test.py` set `dataset_dir: "native/datasets"`, a *relative* path that escapes their temp directory and writes into the repository root. `native/` exists on disk now, untracked and not ignored. Tests must not write outside their fixture. |
| No migration framework | medium | Additive `CREATE TABLE IF NOT EXISTS` only. Adequate so far; a column rename would have no path. |
| Event suppression vs science | **high for v0.5** | See above — identical measurements must not be deduped. |
| Nothing hardware-validated | high | Voice, drivers, computer-use and the service layer are code that has never met real hardware. `docs/REAL_HOST_PLAN.md` marks each. |
| Kernel breadth | low-medium | `DaQauntumKernel.__init__` now constructs ~25 subsystems. Readable, but it is the file that grows with every milestone. |
| `models/` package is empty | trivial | Only `__init__.py`. A name v0.5 might want; worth noting before reusing it. |
| No typed domain layer | — | Everything is `dict[str, Any]` over SQLite rows. This is exactly what v0.5 changes. |

## 6. Dependency situation for v0.5

Checked on this host: **NumPy, SciPy, matplotlib and Pint are all absent.** PyPI
is reachable through the proxy, so any of them *could* be added.

The repository's stated philosophy is "keep dependencies optional where
practical" and "DaQauntum must still boot when they are absent". Adding four
heavy scientific dependencies to a six-dependency project is a real change in
character and is the main architectural decision v0.5 has to make consciously
rather than by habit.

## 7. Recommendations before implementation

1. Fix the test pollution first — it is small, and a baseline that writes into
   its own repository is not a trustworthy baseline.
2. Give scientific events unique dedupe keys before any measurement flows
   through the bus.
3. Build the scientific layer as new top-level packages alongside the existing
   ones, following the established SQLite-on-shared-connection convention, and
   change no existing subsystem behaviour.
4. Keep the core dependency-free; make heavy scientific libraries optional and,
   where they exist, use them to *verify* the native implementation rather than
   to provide it.
