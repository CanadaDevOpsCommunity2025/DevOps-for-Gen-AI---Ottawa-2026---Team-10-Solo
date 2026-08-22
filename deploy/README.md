# Deploying to AWS

One EC2 instance runs both services — Sentinel (Node, port 4000) and the
flight-recorder console (Python/FastAPI, port 8000) — behind their own ports,
no domain/TLS (plain HTTP over the instance's public IP). SQLite for both
lives on the instance's own EBS volume, so it survives reboots as long as
the instance itself isn't terminated.

**Known limitation, by design for this deployment:** flight-recorder's
console trusts a client-supplied `X-FR-Role` header for its RBAC demo (its
own docs are explicit that in production this should come from an API key —
see `flight-recorder/START.md`). Since only synthetic/fake data ever flows
through it, that's an acceptable tradeoff for a public demo link — just
don't point real traffic or real PII at it as deployed here.

## 1. Authenticate (you do this, not me)

```bash
aws configure       # or: aws sso login
aws sts get-caller-identity   # confirms it worked
```

## 2. Provision the instance

```bash
cd deploy
REGION=us-east-1 ./provision-aws.sh
```

Creates (or reuses, if run again): an SSH key pair (`sentinel-platform-key.pem`,
saved next to this script — **don't commit it**, `.gitignore` already
excludes `deploy/*.pem`), a security group (22/tcp from your current IP only,
4000/tcp and 8000/tcp from anywhere), a `t3.small` Amazon Linux 2023
instance running `ec2-userdata.sh` at boot, and an Elastic IP so the address
is stable across stop/start.

Prints the instance's public IP and the exact next commands.

## 3. Deploy the code

```bash
ssh -i sentinel-platform-key.pem ec2-user@<public-ip> 'cloud-init status --wait'
scp -i sentinel-platform-key.pem -r . ec2-user@<public-ip>:~/deploy
ssh -i sentinel-platform-key.pem ec2-user@<public-ip> \
  'REPO_URL=<your-git-remote-url> sudo -E bash ~/deploy/deploy.sh'
```

`deploy.sh` clones the repo to `/opt/sentinel-app`, installs both apps'
dependencies, generates and saves a JWT secret plus admin/compliance
passwords **on first run only** (printed once — save them), seeds the
platform DB, registers flight-recorder's three RAG apps with Sentinel,
backfills demo history, and starts both services under systemd
(`sentinel.service`, `flight-recorder.service` — auto-restart on crash or
reboot).

## 4. Verify

```bash
curl http://<public-ip>:4000/health
curl http://<public-ip>:8000/api/tenants
```

Open `http://<public-ip>:4000` (Sentinel) and `http://<public-ip>:8000`
(flight-recorder console) in a browser.

## Redeploying after future changes

```bash
ssh -i sentinel-platform-key.pem ec2-user@<public-ip> \
  'REPO_URL=<your-git-remote-url> sudo -E bash /opt/sentinel-app/deploy/deploy.sh'
```

Safe to re-run: pulls the latest commit on `main`, reinstalls dependencies,
and restarts both services — but never overwrites `platform/.env` (so
existing sessions/secrets survive) or re-seeds a database that already has
tenants in it.

## Tearing down

```bash
aws ec2 terminate-instances --region us-east-1 --instance-ids <instance-id>
aws ec2 release-address --region us-east-1 --allocation-id <allocation-id>
```
