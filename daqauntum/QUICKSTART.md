# DaQauntum — Run it on your machine

```bash
unzip daqauntum-0.5.0.zip
cd daqauntum-0.5.0

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml
```

## 1. Find out what actually works here

```bash
PYTHONPATH=. python scripts/doctor.py
```

One pass over environment, model routes, voice, memory, presence, events,
drivers, remote access, integrations, systemd and release integrity. Every
result says PASS, WARN or FAIL with the exact command that fixes it, and it
writes `data/diagnostics/SYSTEM_HEALTH.md`.

Add `--redact` to omit hostnames, addresses and device names before sharing it.

**Expect warnings on a fresh install.** That is the point — it tells you what to
set up rather than pretending everything works.

## 2. Give it a real brain

DaQauntum ships with a mock provider so it boots with nothing configured. The
mock is an architecture fallback, not a usable model. Pick at least one:

```bash
# Local and private — the preferred route
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma3

# or hosted
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...

# or an authenticated Claude CLI, if you already have one
```

## 3. Start it

```bash
python daqauntum_gui.py          # local GUI at http://127.0.0.1:8765/
python daqauntum_gui.py --demo   # guided tour of what this machine can do
python daqauntum_server.py       # persistent server
python daqauntum.py              # terminal
```

The GUI opens in **Daily** mode: talk, chat, demo, notifications. Switch to
**Lab** in the top bar for memory, knowledge, events, drivers, devices,
workspaces, learning and the native-model lab.

## 4. Voice

```bash
pip install -r requirements-voice.txt
PYTHONPATH=. python scripts/setup_local_voice.py
```

Then hold a conversation and check where the time goes:

```bash
PYTHONPATH=. python scripts/benchmark_latency.py
```

It reports four stages separately — **hear** (speech end to transcript),
**think** (transcript to first token), **speak** (first token to first audible
reply) and total — and names the slowest one. A single number could not tell you
whether to fix your STT model or your LLM route.

## 5. Reach it from your phone

```bash
PYTHONPATH=. python scripts/setup_remote_access.py --enrol "My phone"
```

It checks each step in order and stops at the first thing not ready. Enter the
code at `https://<your-host>.<tailnet>.ts.net/m`.

Use Tailscale. The device token is defence in depth, not a reason to expose the
control API to the internet.

## 6. Run it as a service

```bash
PYTHONPATH=. python scripts/install_server_service.py
PYTHONPATH=. python scripts/install_learning_timers.py
```

## What to expect

DaQauntum's reasoning, memory, knowledge, event, identity and native-model
layers are covered by 18 regression suites and work out of the box once a model
is configured.

Its voice, vision, device-driver and service layers are real code that has never
run on real hardware — see `docs/REAL_HOST_PLAN.md`, which marks exactly which
is which. Your machine is where that gets settled.

```bash
PYTHONPATH=. python evaluations/smoke_test.py    # or any suite in evaluations/
```
