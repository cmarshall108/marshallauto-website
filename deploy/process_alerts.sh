#!/usr/bin/env bash
# One-shot inventory alert run for the systemd timer. The production launcher
# also runs this worker continuously; repeating the check is safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Project Python virtual environment not found." >&2; exit 1; }
exec "$PYTHON" -m app.alert_worker
