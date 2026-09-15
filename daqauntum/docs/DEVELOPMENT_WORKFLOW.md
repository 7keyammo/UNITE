# DaQauntum Development Workflow

## 1. Work from Git

The handoff archive may not contain Git history. On the development machine:

```bash
git init
git add .
git commit -m "baseline: DaQauntum v0.4.0 developer handoff"
```

If a remote repository already exists, prefer importing this tree into it rather than creating competing histories.

Use feature branches or worktrees when Claude Code and Codex are working simultaneously.

Example:

```bash
git switch -c feat/event-reaction-engine
```

## 2. Establish baseline before editing

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml
PYTHONPATH=. python scripts/verify_release.py
PYTHONPATH=. python evaluations/smoke_test.py
```

Run any subsystem test relevant to the task before modifying that subsystem. Record pre-existing failures rather than silently attributing them to new code.

## 3. Implement vertically

A feature is usually incomplete if it only touches one layer. Prefer a vertical slice:

```text
config/schema
   -> backend implementation
   -> permission/privacy handling
   -> API/tool exposure
   -> GUI/CLI (if user-facing)
   -> focused tests
   -> docs/doctor/readiness status
```

## 4. Keep external systems behind adapters

Do not vendor/fork mature ecosystems into the core unless there is a strong reason. Integrations should be independently installable/upgradable and must report `available/configured/healthy` rather than causing startup failure.

## 5. Testing discipline

- Pure deterministic logic: add unit tests where practical.
- New subsystem: add `evaluations/<subsystem>_smoke_test.py`.
- Cross-cutting change: run all affected smoke tests.
- Permission/privacy code: test both allowed and denied/fallback paths.
- Migrations: create an old-schema fixture and prove preservation.

See `docs/TEST_MATRIX.md`.

## 6. Release discipline

Do not modify the release manifest by hand.

Before creating a release:

1. update `VERSION` and version strings intentionally;
2. update README/ARCHITECTURE/CHANGELOG/MIGRATION/RELEASE/ROADMAP;
3. remove runtime/private artifacts;
4. run regression matrix;
5. rebuild `RELEASE_MANIFEST.txt`;
6. verify release;
7. zip complete repo;
8. extract final ZIP to a fresh directory;
9. rerun verifier + regression tests there.

See `docs/RELEASE_CHECKLIST.md`.

## 7. Session handoff

Each coding session should conclude with:

```text
Goal:
Completed:
Files changed:
Tests run:
Configuration changes:
Risks / known gaps:
Next exact task:
```
