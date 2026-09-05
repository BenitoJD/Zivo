# Judge0 troubleshooting — how we got it working (2026-07-02)

Hard-won notes from standing up self-hosted Judge0 for the interview coding rounds. If code
execution breaks, **read this first** — most of the "obvious" causes turned out to be red herrings.

## The symptom
Every submission failed with:
```
{"status":{"id":13,"description":"Internal Error"},
 "message":"No such file or directory @ rb_sysopen - /box/script.py"}
```
and in the `server` container log: `chown: cannot access '/box': No such file or directory`.

## The actual root cause (the only thing that mattered)
**We submitted with `wait=true`.**

With `wait=true`, Judge0 runs the job **inline in the web `server` process**. That process is
**not** `privileged` and does **not** have `cgroup: host` — only the `workers` container does.
So `isolate --init` fails to create its sandbox, `@workdir` comes back empty, and Judge0 then
tries to write the source to `"" + "/box/script.py"` → `/box/script.py`, which doesn't exist.

Judge0's own code makes this concrete (`/api/app/jobs/isolate_job.rb`):
```ruby
@workdir = `isolate #{cgroups} -b #{box_id} --init`.chomp   # empty when isolate can't run here
@boxdir  = workdir + "/box"
@source_file = boxdir + "/" + source_file                    # => "/box/script.py" when workdir==""
```

### The fix
Submit **async** (`wait=false`) and poll the token. Async routes the job to the **privileged
`workers`** container — the only place `isolate` can run. Implemented in
`backend/app/services/code_execution.py` (`run_code` posts `wait=false`, then polls
`GET /submissions/{token}` until `status.id > 2`).

Proof it's the server vs worker split — same code, two outcomes:
```bash
# wait=true  -> runs in server -> FAILS (rb_sysopen /box/script.py)
curl -s -X POST 'http://localhost:2358/submissions?base64_encoded=false&wait=true' \
  -H 'Content-Type: application/json' -d '{"language_id":71,"source_code":"print(6*7)"}'

# wait=false -> runs in privileged workers -> "42", Accepted
T=$(curl -s -X POST 'http://localhost:2358/submissions?base64_encoded=false&wait=false' \
  -H 'Content-Type: application/json' -d '{"language_id":71,"source_code":"print(6*7)"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
sleep 3; curl -s "http://localhost:2358/submissions/$T?base64_encoded=false"
```
The server log will show `[IsolateJob] ... Performed IsolateJob` in `server-1` for the first and
in `workers-1` for the second — that single line is the whole diagnosis.

## Red herrings (don't repeat these — they cost days)
These looked like the cause but were **not**. They came from testing `isolate` by hand via
`docker exec` (which runs as root, in an ad-hoc shell) — a context that behaves differently from
the real resque worker, so it masked the real issue.

| Suspected cause | Why it was wrong |
|---|---|
| **Kernel 5.15+ segfaults isolate** (drove a 22.04→20.04 reinstall) | isolate runs fine; segfaults were a stack-limit artifact of manual testing, not the worker path. |
| **`isolate` stack too small** (`--stack` "fix") | Judge0 already passes `-k MAX_STACK_LIMIT`. Real submissions never hit this. |
| **Leftover / root-owned boxes** (`Box already exists`) | Real collisions, but only from *my* manual `docker exec` tests. Cleaning them didn't fix the API. |
| **cgroup v1 vs v2** | Genuinely required (isolate needs v1 + privileged) — but that's why Judge0 is on a **dedicated box**, and once there it was never the blocker. |
| **"Common KVM processor" CPU** | Pure coincidence. |

## Diagnostic playbook (fastest path next time)
1. Submit `wait=false` + poll. If that works but `wait=true` doesn't → **you're on the server/worker
   split**, use async (this is the resolved state; the client already does).
2. `docker compose logs --tail=20 workers` and `... server` around a submit. **Which container
   prints `Performed IsolateJob`?** It must be `workers`.
3. Confirm the workers container is privileged with host cgroups:
   `docker inspect judge0-workers-1 --format '{{.HostConfig.Privileged}} {{.HostConfig.CgroupnsMode}}'`
   → `true host`.
4. Sanity-run isolate as the worker user:
   ```bash
   docker exec -u judge0 judge0-workers-1 sh -c 'b=$(isolate --cg -b 55 --init); \
     echo "print(6*7)" > $b/box/script.py; \
     isolate --cg -b 55 --run -- /usr/bin/python3 script.py; isolate --cg -b 55 --cleanup'
   ```
   Prints `42` when the sandbox is healthy.

## Related gotchas
- **Firewall:** the box is public. `ufw allow OpenSSH` + `ufw allow from <PROD_EGRESS_IP> to any
  port 2358 proto tcp` + `ufw --force enable`. Prod egress IP: `ssh zivo-node5 "curl -s ifconfig.me"`.
- **Deploy Zivo Action helm step** can fail with `open /etc/rancher/k3s/k3s.yaml: permission denied`
  (runner user can't read the kubeconfig). Images still build+import fine; finish the rollout on the
  VPS with `sudo KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade ...` (see the deploy script).
</content>
