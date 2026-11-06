#!/usr/bin/env bash
# Daily encrypted data backup → private GitHub repository (ciphertext only).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
LOG_FILE="${BACKUP_LOG:-/var/log/marshallauto-backup.log}"
LOCK_FILE="${BACKUP_LOCK:-/var/lock/marshallauto-backup.lock}"

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another backup is running; skipping"
  exit 0
fi

PYTHON="$ROOT/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || { log "FATAL: project virtualenv python not found"; exit 1; }

export FLASK_APP="${FLASK_APP:-run.py}"
# Flask loads project-root .env (BACKUP_ENCRYPTION_KEY, BACKUP_GIT_URL, …).
log "starting encrypted backup"
if ! "$PYTHON" -m flask backup-data; then
  log "FAILED: backup-data"
  exit 1
fi
log "backup completed successfully"
