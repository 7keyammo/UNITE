#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then PY="${PYTHON:-python3}"; fi
cd "$ROOT"
exec "$PY" scripts/evening_report.py --notify "$@"
