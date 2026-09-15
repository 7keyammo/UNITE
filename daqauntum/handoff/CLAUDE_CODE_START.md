# Suggested first prompt for Claude Code

Open this repository as the DaQauntum v0.4.0 engineering baseline.

Read `CLAUDE.md`, then `docs/HANDOFF.md`, `docs/REALITY_MAP.md`, `docs/SECURITY_INVARIANTS.md`, `TASKS.md`, and `ARCHITECTURE.md` before editing anything.

Your first job is **P0 Stabilize the real DaQauntum host**, not a rewrite and not speculative new features. Audit the existing code against the P0 acceptance criteria, identify the smallest changes needed to make setup/diagnostics/voice latency/persistent-service operation reliable on a Debian host, and produce a short implementation plan before changing code.

Preserve every security invariant. Run the relevant smoke tests after each vertical slice. Do not claim an external integration works merely because an adapter exists.
