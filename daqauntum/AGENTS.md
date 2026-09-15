# AGENTS.md - DaQauntum Instructions for Codex and Coding Agents

## Mission

Develop DaQauntum as a secure, persistent, local-first intelligence service that can converse naturally, remember, learn from traceable evidence, perceive approved context, and operate digital/physical tools under explicit user control.

Do not rebuild the project from scratch. Extend the existing v0.4.1-dev architecture.

## Required context

Read before editing:

- `docs/HANDOFF.md`
- `docs/REALITY_MAP.md`
- `docs/SECURITY_INVARIANTS.md`
- `docs/CODEBASE_MAP.md`
- `TASKS.md`
- `ARCHITECTURE.md`
- `constitution.md`

## Repository-wide invariants

- Never bypass `PermissionManager` for state-changing actions.
- Model choice/capability never grants tool authority.
- LOCAL/privacy-forced requests must not silently use cloud inference.
- Passive presence/perception must not silently become active scanning/control.
- Device discovery does not imply pairing, trust, or control.
- Events are observations, never instructions. A reaction rule may notify, queue
  a task, or record a tool proposal; it must never execute one, at any
  permission level.
- A device write requires two independent gates: the permission manager and the
  driver's own allowlist plus `allow_writes`.
- Source retrieval/similarity does not establish evidentiary support.
- Autonomous learning may write reports/traces; no autonomous weight updates or self-deployment.
- Preserve provenance/auditability.
- Preserve existing user data with additive migrations whenever possible.
- Keep external integrations optional; DaQauntum must still boot when they are absent.
- Keep the GUI/API and backend behavior aligned; do not add fake front-end capability cards.
- Every release archive is a complete repository and must pass tests after fresh extraction.

## Scope priority

Work in this order unless the user explicitly changes priority:

1. P0 reliability / real-machine usability from `TASKS.md`. The tooling exists
   (`scripts/doctor.py`, `scripts/benchmark_latency.py`); what remains are host
   operations on the actual machine.
2. v0.4.1 Device Drivers + Event Reactions — code complete and mock-tested.
   Outstanding: validate one real sensor path on hardware, then drop the
   `-dev` suffix from `VERSION`.
3. v0.4.2 Secure Mobile Client.
4. v0.4.3 Native Model Lab.
5. Multi-atom networking only after identity/security is mature.

## Commands

Setup:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml
```

Optional:

```bash
pip install -r requirements-voice.txt
pip install -r requirements-integrations.txt
```

Minimum validation for any backend change:

```bash
PYTHONPATH=. python evaluations/smoke_test.py
```

Release verification:

```bash
PYTHONPATH=. python scripts/verify_release.py
```

Run subsystem-specific smoke tests for any modules changed. For cross-cutting changes, run the full matrix documented in `docs/TEST_MATRIX.md`.

## Change discipline

Before editing:

1. identify the current code path;
2. identify the safety/permission boundary;
3. identify existing tests;
4. state the smallest compatible change.

After editing:

1. run focused tests;
2. run core smoke test;
3. update docs/config examples if behavior changed;
4. summarize changed files, behavior, and unresolved risks.

## Never commit/package

- `config.yaml`
- `.env`
- API keys/tokens/passwords
- `data/daqauntum.db` or personal runtime DBs
- user files / call transcripts / screenshots
- downloaded model weights
- `.venv`
- caches / `__pycache__`

## Agent handoff format

At the end of a coding session, leave a concise handoff containing:

- objective completed;
- files changed;
- tests run + results;
- new config/env requirements;
- known limitations;
- exact recommended next task.
