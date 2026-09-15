# Migrating to DaQauntum v0.4.0

v0.4.0 can import state from any recent v0.3.x release. The screenshots supplied during development show a v0.3.6 instance; you do not need to install every intermediate release first.

## 1. Keep your working folder

Do not overwrite the current DaQauntum directory until v0.4.0 passes verification.

## 2. Install v0.4.0 beside it

```bash
unzip daqauntum-alpha-v0.4.0.zip
cd daqauntum-alpha-v0.4.0
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Verify the package

```bash
PYTHONPATH=. python scripts/verify_release.py
PYTHONPATH=. python evaluations/presence_smoke_test.py
PYTHONPATH=. python evaluations/demo_smoke_test.py
PYTHONPATH=. python evaluations/gui_smoke_test.py
```

## 4. Import the old state

Example if the current folder is v0.3.6:

```bash
python scripts/import_previous_data.py ../daqauntum-alpha-v0.3.6
```

For v0.3.9:

```bash
python scripts/import_previous_data.py ../daqauntum-alpha-v0.3.9
```

The import script carries forward the database, notes and config rather than mixing old source-code modules into the new release.

## 5. Run first-run diagnostics

```bash
PYTHONPATH=. python scripts/first_run.py
```

This is particularly useful if the old machine already has Claude Code CLI, Ollama, or local voice installed.

## 6. Launch the spoken demo

```bash
python daqauntum_gui.py --demo
```

## Database migration

v0.4.0 additively creates `presence_samples` if it does not already exist. Existing messages, memories, graph nodes, sources, calls, learning records, connectors, workspaces, visual frames and computer-action history remain intact.

## Existing Claude CLI setups

v0.4.0 includes a first-class `claude_cli` brain adapter. If your old config references Claude CLI, use the example v0.4.0 provider configuration if the imported settings need adjustment.

## Reinstall persistent services

After validating v0.4.0, reinstall user services/timers from the new repository so service paths point at the new version:

```bash
python scripts/install_server_service.py
python scripts/install_learning_timers.py
```
