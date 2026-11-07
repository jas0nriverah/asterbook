#!/bin/sh
# This causes service downtime. Install only after configuring backup storage.
set -eu
if systemctl is-active --quiet asterbook.service; then
  restart() { systemctl start asterbook.service; }
  trap 'restart' EXIT
  systemctl stop asterbook.service
fi
python3 /opt/asterbook/current/tools/recovery.py backup --data-dir /var/lib/asterbook --root /var/backups/asterbook --keep 7 --verifier /opt/asterbook/current/bin/recovery_verify
