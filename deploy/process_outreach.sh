#!/usr/bin/env bash
# Monthly new-car outreach run for the systemd timer. Safe to re-run; the
# worker enforces OUTREACH_MIN_DAYS spacing per contact.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Project Python virtual environment not found." >&2; exit 1; }
exec "$PYTHON" -m app.outreach_worker
