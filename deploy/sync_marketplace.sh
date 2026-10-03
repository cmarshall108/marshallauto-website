#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Project Python virtual environment not found." >&2; exit 1; }
exec "$PYTHON" -m flask --app run:app listing-sync