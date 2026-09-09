#!/usr/bin/env bash
# Pull-based auto deploy with health check and automatic rollback.
#
# Run from a systemd timer. If the new commit fails to install, migrate, or
# serve traffic, the previous commit (and its database snapshot) is restored so
# the site is never left down.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-main}"
SERVICE="${DEPLOY_SERVICE:-marshallauto}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1/healthz?deep=1}"
HEALTH_RETRIES="${HEALTH_RETRIES:-20}"
HEALTH_DELAY="${HEALTH_DELAY:-3}"
LOG_FILE="${DEPLOY_LOG:-/var/log/marshallauto-deploy.log}"
LOCK_FILE="${DEPLOY_LOCK:-/var/lock/marshallauto-deploy.lock}"
BACKUP_ROOT="${DEPLOY_BACKUP_DIR:-/var/backups/marshallauto}"

mkdir -p "$(dirname "$LOG_FILE")" "$BACKUP_ROOT" 2>/dev/null || true
exec >>"$LOG_FILE" 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Never run two deploys at once (timer fires while a deploy is still going).
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "another deploy is running; skipping"
  exit 0
fi

cd "$ROOT" || { log "FATAL: $ROOT missing"; exit 1; }

PYTHON="$ROOT/venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

git config --global --add safe.directory "$ROOT" 2>/dev/null || true

if ! git fetch --quiet origin "$BRANCH"; then
  log "git fetch failed; will retry on the next timer tick"
  exit 0
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"
if [[ "$LOCAL" == "$REMOTE" && "${FORCE_DEPLOY:-0}" != "1" ]]; then
  exit 0
fi

log "deploying $LOCAL -> $REMOTE (branch $BRANCH)"

# Snapshot the database/instance folder so a rollback also rolls back migrations.
SNAPSHOT="$BACKUP_ROOT/instance-$LOCAL-$(date +%s)"
if [[ -d "$ROOT/instance" ]]; then
  cp -a "$ROOT/instance" "$SNAPSHOT" && log "instance snapshot: $SNAPSHOT"
fi

wait_healthy() {
  local i
  for ((i = 1; i <= HEALTH_RETRIES; i++)); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$HEALTH_DELAY"
  done
  return 1
}

restart_service() {
  systemctl restart "$SERVICE" || return 1
  return 0
}

rollback() {
  log "ROLLBACK: restoring $LOCAL"
  git reset --hard "$LOCAL" --quiet || log "WARNING: git reset failed"
  if [[ -d "$SNAPSHOT" ]]; then
    rm -rf "$ROOT/instance"
    cp -a "$SNAPSHOT" "$ROOT/instance" || log "WARNING: instance restore failed"
  fi
  "$PYTHON" -m pip install --quiet -r requirements.txt || log "WARNING: rollback pip install failed"
  restart_service || log "WARNING: rollback restart failed"
  if wait_healthy; then
    log "ROLLBACK OK: site healthy on $LOCAL"
  else
    log "CRITICAL: site unhealthy after rollback — manual intervention required"
  fi
}

if ! git reset --hard "$REMOTE" --quiet; then
  log "FAILED: could not check out $REMOTE"
  rollback
  exit 1
fi

if ! "$PYTHON" -m pip install --quiet -r requirements.txt; then
  log "FAILED: pip install"
  rollback
  exit 1
fi

if ! FLASK_APP=run.py "$PYTHON" -m flask db upgrade; then
  log "FAILED: database migration"
  rollback
  exit 1
fi

if ! restart_service; then
  log "FAILED: systemctl restart $SERVICE"
  rollback
  exit 1
fi

if ! wait_healthy; then
  log "FAILED: health check ($HEALTH_URL) after restart"
  rollback
  exit 1
fi

log "DEPLOYED $(git rev-parse --short HEAD) — healthy"

# Keep the 10 most recent snapshots.
ls -1dt "$BACKUP_ROOT"/instance-* 2>/dev/null | tail -n +11 | xargs -r rm -rf
