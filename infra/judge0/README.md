# Judge0 (dedicated box)

Self-hosted [Judge0](https://github.com/judge0/judge0) for the interview coding rounds. It runs
on **its own box**, not the app cluster — untrusted user code must be isolated away from prod data,
and isolate needs cgroup v1 + privileged (which our cgroup-v2 k3s node refuses).

The app reaches it via `JUDGE0_URL` (set in `infra/k8s/charts/api/values.yaml`). Client code:
`backend/app/services/code_execution.py`.

## ⚠️ Box requirements (learned the hard way)
- **Ubuntu 20.04 (kernel 5.4).** Judge0 1.13.1 bundles `isolate 1.8.1` (2019), whose sandbox
  **segfaults in glibc's loader on kernel 5.15+** (Ubuntu 22.04/24.04) — every sandboxed program
  crashes. Use 20.04, or the box is useless for Judge0.
- **cgroup v1.** If the OS boots cgroup v2, set `systemd.unified_cgroup_hierarchy=0` in
  `/etc/default/grub`, `update-grub`, reboot. Confirm `/sys/fs/cgroup/memory` exists.
- ~2 vCPU / 4 GB RAM / 30 GB disk. Note the Judge0 image is ~14 GB extracted.
- **Docker ≤ 24.** Docker 25+/29 changed cgroup-namespace handling and breaks isolate; install
  Docker 24 (`get.docker.com | VERSION=24.0 sh`).

## Setup
```bash
scp -r infra/judge0 root@<box>:/root/           # or git clone
cd /root/judge0
cp judge0.conf.example judge0.conf              # fill in random passwords
docker compose up -d db redis && sleep 10
docker compose up -d
```
Smoke test (should print `42`, status Accepted):
```bash
curl -s -X POST 'http://localhost:2358/submissions?base64_encoded=false&wait=true' \
  -H 'Content-Type: application/json' -d '{"language_id":71,"source_code":"print(6*7)"}'
```

## Lock it down (do this — the box is public)
1. **Firewall** so only the app can reach 2358:
   ```bash
   ufw allow OpenSSH
   ufw allow from <APP_BOX_IP> to any port 2358
   ufw --force enable
   ```
2. Optionally set `AUTHN_TOKEN` in `judge0.conf` and send it as `X-Auth-Token` from the app.

Then set `judge0Url` in the api chart to `http://<box-ip>:2358` and redeploy.
