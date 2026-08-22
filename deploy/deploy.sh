#!/bin/bash
# Run this on the EC2 instance after ec2-userdata.sh has completed (check
# with `cloud-init status --wait`). Safe to re-run for every future update —
# it pulls latest code and restarts services, but never rotates secrets that
# already exist.
#
#   REPO_URL=git@github.com:you/repo.git sudo -E ./deploy.sh
#
set -euo pipefail

REPO_URL="${REPO_URL:?set REPO_URL to your git remote, e.g. git@github.com:you/repo.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
APP_DIR=/opt/sentinel-app

echo "==> syncing code"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u sentinel git -C "$APP_DIR" fetch origin "$REPO_BRANCH"
  sudo -u sentinel git -C "$APP_DIR" reset --hard "origin/$REPO_BRANCH"
else
  sudo -u sentinel git clone --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "==> platform: installing dependencies"
cd "$APP_DIR/platform"
sudo -u sentinel npm ci --omit=dev

if [ ! -f .env ]; then
  echo "==> platform: first deploy — generating secrets"
  JWT_SECRET=$(node -e "console.log(require('crypto').randomBytes(48).toString('hex'))")
  ADMIN_PASSWORD=$(node -e "console.log(require('crypto').randomBytes(12).toString('base64url'))")
  COMPLIANCE_PASSWORD=$(node -e "console.log(require('crypto').randomBytes(12).toString('base64url'))")
  sudo -u sentinel tee .env > /dev/null <<EOF
NODE_ENV=production
PORT=4000
SENTINEL_JWT_SECRET=$JWT_SECRET
SENTINEL_ADMIN_PASSWORD=$ADMIN_PASSWORD
SENTINEL_COMPLIANCE_PASSWORD=$COMPLIANCE_PASSWORD
EOF
  echo "############################################################"
  echo "# SAVE THESE NOW — printed only on first deploy:"
  echo "#   admin@sentinel.platform / $ADMIN_PASSWORD"
  echo "#   compliance@sentinel.platform / $COMPLIANCE_PASSWORD"
  echo "############################################################"
else
  echo "==> platform: .env already present, leaving secrets as-is"
fi

echo "==> platform: seeding (no-op if already seeded)"
set -a; source .env; set +a
sudo -u sentinel -E env PATH="$PATH" npm run seed

echo "==> installing systemd units"
cp "$APP_DIR/deploy/sentinel.service" /etc/systemd/system/sentinel.service
cp "$APP_DIR/deploy/flight-recorder.service" /etc/systemd/system/flight-recorder.service
systemctl daemon-reload
systemctl enable sentinel
systemctl restart sentinel
echo "==> waiting for Sentinel to come up"
for i in $(seq 1 30); do
  curl -sf http://localhost:4000/health > /dev/null && break
  sleep 1
done
curl -sf http://localhost:4000/health > /dev/null || { echo "Sentinel did not come up"; journalctl -u sentinel --no-pager -n 50; exit 1; }

echo "==> flight-recorder: setting up venv"
cd "$APP_DIR/flight-recorder"
[ -d .venv ] || sudo -u sentinel python3.11 -m venv .venv
sudo -u sentinel ./.venv/bin/pip install -q -r requirements.txt

if [ ! -f flightrecorder.db ]; then
  echo "==> flight-recorder: backfilling demo history"
  sudo -u sentinel ./.venv/bin/python harness/run.py --backfill 6h --backfill-count 900 --backfill-only
fi

if [ ! -f sentinel-config.json ]; then
  echo "==> flight-recorder: registering with Sentinel"
  sudo -u sentinel ./.venv/bin/python register_with_sentinel.py
fi

systemctl enable --now flight-recorder
systemctl restart flight-recorder

echo "==> pushing one batch of fresh traffic through both dashboards"
sudo -u sentinel ./.venv/bin/python harness/run.py --rate 5 --scenario demo --count 60 --sink sqlite,sentinel || true

PUBLIC_IP=$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 || echo "<instance-ip>")
echo "############################################################"
echo "Sentinel:         http://$PUBLIC_IP:4000"
echo "Flight Recorder:  http://$PUBLIC_IP:8000"
echo "############################################################"
