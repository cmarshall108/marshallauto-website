#!/usr/bin/env bash
# One-time installer for the auto-deploy stack on the Ubuntu VPS.
#
#   cd /opt/marshallauto-website && sudo deploy/install.sh
#
# Installs:
#   marshallauto.service              - the site, Restart=always
#   marshallauto-deploy.timer         - polls GitHub every minute, deploys + rolls back
#   marshallauto-healthcheck.timer    - restarts the site if /healthz stops answering
#   marshallauto-alerts.timer         - sends due price and arrival alerts
#   marshallauto-outreach.timer       - monthly new-car texts/emails to lead contacts
#   marshallauto-backup.timer         - daily encrypted data backup to a private GitHub repo
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

if ! bash "$ROOT/deploy/ensure_ffmpeg.sh"; then
  echo "WARNING: system ffmpeg unavailable; the imageio-ffmpeg Python dependency supplies a fallback on supported platforms." >&2
fi

chmod +x "$ROOT/start_ubuntu_prod.sh" "$ROOT/deploy/auto_deploy.sh" "$ROOT/deploy/healthcheck.sh" \
  "$ROOT/deploy/process_alerts.sh" "$ROOT/deploy/process_outreach.sh" \
  "$ROOT/deploy/sync_marketplace.sh" "$ROOT/deploy/run_backup.sh"

for unit in marshallauto.service marshallauto-deploy.service marshallauto-deploy.timer \
            marshallauto-healthcheck.service marshallauto-healthcheck.timer \
            marshallauto-marketplace-sync.service marshallauto-marketplace-sync.timer \
            marshallauto-alerts.service marshallauto-alerts.timer \
            marshallauto-outreach.service marshallauto-outreach.timer \
            marshallauto-backup.service marshallauto-backup.timer; do
  sed "s#/opt/marshallauto-website#$ROOT#g" "$ROOT/deploy/$unit" > "$UNIT_DIR/$unit"
done

touch /var/log/marshallauto.log /var/log/marshallauto-deploy.log /var/log/marshallauto-health.log \
  /var/log/marshallauto-backup.log
mkdir -p /var/backups/marshallauto /var/backups/marshallauto/encrypted
git config --global --add safe.directory "$ROOT"

systemctl daemon-reload
systemctl enable --now marshallauto.service
systemctl enable --now marshallauto-deploy.timer
systemctl enable --now marshallauto-healthcheck.timer
systemctl enable --now marshallauto-marketplace-sync.timer
systemctl enable --now marshallauto-alerts.timer
systemctl enable --now marshallauto-outreach.timer
systemctl enable --now marshallauto-backup.timer

systemctl --no-pager status marshallauto.service | head -n 12
systemctl list-timers --no-pager | grep marshallauto || true
echo
echo "Installed. Deploy log: /var/log/marshallauto-deploy.log"
echo "Backup log: /var/log/marshallauto-backup.log"
echo "Configure BACKUP_ENCRYPTION_KEY + BACKUP_GIT_URL (and token/deploy key) before the first backup."
echo "Monthly outreach uses TEXTBELT_API_KEY + SMTP; contacts sync from non-spam leads."
