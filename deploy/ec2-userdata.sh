#!/bin/bash
# EC2 user-data: OS-level bootstrap only. Runs once, as root, at first boot
# (Amazon Linux 2023). Does NOT clone the app or start services — that's
# deploy.sh, run afterwards (and re-run on every future update).
set -euxo pipefail

dnf update -y
dnf install -y git python3.11 python3.11-pip

# Node 22+ is required (platform/src/db.js uses node:sqlite, stable from
# 22.5+) — AL2023's bundled nodejs module may lag behind that, so use
# NodeSource directly rather than relying on `dnf install nodejs`.
curl -fsSL https://rpm.nodesource.com/setup_22.x | bash -
dnf install -y nodejs

id -u sentinel &>/dev/null || useradd --system --create-home --shell /bin/bash sentinel
mkdir -p /opt/sentinel-app
chown sentinel:sentinel /opt/sentinel-app

node -v
python3.11 -V
echo "bootstrap complete — run deploy.sh next"
