# DaQauntum Release Checklist

## Before freeze

- [ ] Scope/acceptance criteria complete.
- [ ] `VERSION` intentionally updated.
- [ ] User-facing version strings updated.
- [ ] README / ARCHITECTURE / CHANGELOG / MIGRATION / RELEASE / ROADMAP updated.
- [ ] `CLAUDE.md`, `AGENTS.md`, `TASKS.md` still reflect reality.
- [ ] `docs/REALITY_MAP.md` updated for new capability status.
- [ ] Config examples/environment examples updated.
- [ ] New state-changing actions have permission tests.
- [ ] Local/privacy fallback behavior tested.
- [ ] Existing data migration tested where schema changed.

## Hygiene

Release must not contain:

- [ ] `.venv/`
- [ ] `config.yaml`
- [ ] `.env` or credentials
- [ ] runtime DBs
- [ ] user call transcripts
- [ ] screenshots/camera frames
- [ ] connected-source data
- [ ] downloaded model weights
- [ ] test temp data
- [ ] `__pycache__`

## Regression

- [ ] Run focused tests.
- [ ] Run core smoke test.
- [ ] Run all affected cross-subsystem tests.
- [ ] For a milestone release, run full matrix.

## Package

```bash
PYTHONPATH=. python scripts/build_release_manifest.py
PYTHONPATH=. python scripts/verify_release.py
```

Then zip the **complete repository**.

## Fresh-extraction gate

Extract the final archive into a new temporary directory and run:

```bash
PYTHONPATH=. python scripts/verify_release.py
PYTHONPATH=. python evaluations/smoke_test.py
```

For milestone releases, rerun the full matrix from the extracted package.

A release is not complete until the packaged artifact, not only the source tree, passes.
