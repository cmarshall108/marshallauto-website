#!/usr/bin/env bash
# Independent watchdog: if the site stops answering, restart it. Runs even when
# no deploy is happening, so a crash loop or hung worker cannot take the site
# offline for more than a minute.
set -uo pipefail

SERVICE="${DEPLOY_SERVICE:-marshallauto}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1/healthz}"
LOG_FILE="${HEALTHCHECK_LOG:-/var/log/marshallauto-health.log}"
ATTEMPTS="${HEALTH_ATTEMPTS:-3}"
DELAY="${HEALTH_DELAY:-5}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >>"$LOG_FILE"; }

for ((i = 1; i <= ATTEMPTS; i++)); do
  if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
    exit 0
  fi
  sleep "$DELAY"
done

log "health check failed ${ATTEMPTS}x; restarting $SERVICE"
systemctl restart "$SERVICE"
sleep 10
if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
  log "service recovered after restart"
else
  log "CRITICAL: still unhealthy after restart"
fi
