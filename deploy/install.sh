#!/usr/bin/env bash
# One-time installer for the auto-deploy stack on the Ubuntu VPS.
#
#   cd /opt/marshallauto-website && sudo deploy/install.sh
#
# Installs:
#   marshallauto.service              - the site, Restart=always
#   marshallauto-deploy.timer         - polls GitHub every minute, deploys + rolls back
#   marshallauto-healthcheck.timer    - restarts the site if /healthz stops answering
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR=/etc/systemd/system

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }

command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }

chmod +x "$ROOT/start_ubuntu_prod.sh" "$ROOT/deploy/auto_deploy.sh" "$ROOT/deploy/healthcheck.sh"

for unit in marshallauto.service marshallauto-deploy.service marshallauto-deploy.timer \
            marshallauto-healthcheck.service marshallauto-healthcheck.timer; do
  sed "s#/opt/marshallauto-website#$ROOT#g" "$ROOT/deploy/$unit" > "$UNIT_DIR/$unit"
done

touch /var/log/marshallauto.log /var/log/marshallauto-deploy.log /var/log/marshallauto-health.log
mkdir -p /var/backups/marshallauto
git config --global --add safe.directory "$ROOT"

systemctl daemon-reload
systemctl enable --now marshallauto.service
systemctl enable --now marshallauto-deploy.timer
systemctl enable --now marshallauto-healthcheck.timer

systemctl --no-pager status marshallauto.service | head -n 12
systemctl list-timers --no-pager | grep marshallauto || true
echo
echo "Installed. Deploy log: /var/log/marshallauto-deploy.log"
