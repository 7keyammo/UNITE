# Human Handoff Checklist

Before giving this repository to Claude Code or Codex on the real machine:

- [ ] Make a backup of the currently working DaQauntum directory and `data/daqauntum.db`.
- [ ] Extract this handoff repository into its own directory.
- [ ] Do not copy old source-code folders over it.
- [ ] Import only prior runtime state with `scripts/import_previous_data.py`.
- [ ] Create a Git baseline commit before autonomous coding work.
- [ ] Keep secrets in environment variables/local config, never in prompts or committed files.
- [ ] Give coding agents repository access, not unrestricted root/system access unless a specific task requires it.
- [ ] Review/approve any command that modifies system services, networking, Bluetooth, credentials, or hardware.
- [ ] Require tests before accepting a coding-agent change.
- [ ] Use branches/worktrees if Claude Code and Codex will edit simultaneously.
