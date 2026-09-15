# Suggested first prompt for Codex

Work from `AGENTS.md` and treat this repository as the full DaQauntum v0.4.0 baseline.

Start with `TASKS.md` P0. Do not rewrite the architecture. First run/inspect the existing smoke tests and readiness/doctor scripts. Then implement one focused P0 task at a time with tests.

For every task:

1. identify existing implementation path;
2. preserve permission/privacy invariants;
3. add/update focused tests;
4. run `evaluations/smoke_test.py` plus subsystem tests;
5. report changed files, commands run, and next task.

Do not package runtime data, credentials, model weights, `.venv`, or user files.
