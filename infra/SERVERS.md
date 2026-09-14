# Server inventory: Web Eye Soft fleet

# Server inventory: Web Eye Soft fleet

> **STATUS 2026-09-05:** nodes 1-8 were **reinstalled from fresh images** (fleet-wide TCP
> blackout #1; recovered after ~2h). **Blackout #2 on 2026-09-06 ~01:12 IST**: total TCP loss
> on all 10 servers again (ping fine) — recurred mid-rebuild. Log incidents with Web Eye Soft
> every time; if blackouts continue, the provider is not production-grade.
> blackout, then all SSH keys rejected).
> The cluster, platform and app are rebuilt entirely from this repo's automation
> (`infra/ansible/site.yml` + `scripts/install-ha-platform.sh` + the deploy release
> list) once key access is re-injected via the panel console. Two new LV 10 servers
> (zivo-lb1/lb2) were bought to act as the HAProxy application load balancers.
>
> **Key status (SSH verified):** zivo-lb1, zivo-lb2, node1, node2, node3 have the
> fleet key. **Nodes 4, 5, 6, 7, 8 reject their panel passwords** (the reinstall
> regenerated them; the panel still shows the originals) — use the panel's
> CHANGE PASSWORD on each, then the key gets installed over SSH.

Scope: the **8 working VPS** from the September 2026 client-area screenshot, surveyed live over
SSH on 2026-09-01. Seven of them form the HA Kubernetes cluster that [infra/ansible](./ansible/)
bootstraps (PR #27); one is the CI runner. SSH key access is installed and verified on all 8.

**TL;DR for teammates:** `ssh zivo-runner-1`, `ssh zivo-db-1`, `ssh zivo-master-1` … (role-based
names). The older `zivo-node1`…`zivo-node8` aliases still work.

## Role-based naming (2026-09-14)

The old aliases numbered servers by purchase order, so "worker-4" was the CI runner and
"worker-2" was the database. Use the role name; the number keys stay as legacy aliases.

| Role name (new) | Legacy alias | Kubernetes node | Job |
|-----------------|--------------|-----------------|-----|
| `zivo-ingress-1` | zivo-node1 | worker-1 | k8s worker + Traefik ingress |
| `zivo-db-1` | zivo-node2 | worker-2 | **dedicated data node**: Postgres + MinIO (tainted, no other workloads) |
| `zivo-ingress-2` | zivo-node3 | worker-3 | k8s worker + Traefik ingress |
| `zivo-runner-1` | zivo-node4 | (left the cluster 2026-09-14) | **dedicated GitHub Actions CI runner** — nothing else |
| `zivo-spare-1` | zivo-node5 | — | API LB host, Judge0 sandbox candidate |
| `zivo-master-1` | zivo-node6 | master-1 | k8s control plane + etcd |
| `zivo-master-2` | zivo-node7 | master-2 | k8s control plane + etcd |
| `zivo-master-3` | zivo-node8 | master-3 | k8s control plane + etcd |

Kubernetes node names (`worker-1`, `worker-2`, …, `master-3`) are **not renamed**: kubeadm certs
and etcd embed the control-plane hostnames, and worker-2's local-path PVs pin Postgres data to
that node name. Renaming them means drain + rejoin per node and is not worth the risk for
cosmetics — the role names above are what humans and SSH use.

## Fleet at a glance

| Alias | Role (planned) | Vendor hostname | IP | OS | vCPU / RAM / disk | Plan | Renews |
|-----------|--------------------------|--------------------------|-----------------|------------------|-------------------|-------|------------|
| zivo-lb1 | **app load balancer** (HAProxy) — EMPTY, SSH key verified working | vm464833534.manageserver.in | 45.196.196.98 | Ubuntu 24.04.4 | 1 / 0.9 Gi / 19 G | LV 10 | 2026-10-05 |
| zivo-lb2 | **K8s API load balancer** (HAProxy 8443 -> masters 6443) | vm418674611.manageserver.in | 45.196.196.233 | Ubuntu 24.04.4 | 1 / 0.9 Gi / 19 G | LV 10 | 2026-10-05 |
| zivo-node6 | k8s control plane + etcd snapshot cron | vm759659741.manageserver.in | 203.57.85.250 | Ubuntu 22.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node7 | k8s control plane + etcd | vm997512676.manageserver.in | 203.57.85.224 | Ubuntu 22.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node8 | k8s control plane + etcd | vm130951261.manageserver.in | 203.57.85.157 | Ubuntu 22.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node5 | **Judge0 server** (docker compose, :2358, cgroup v1 — outside k8s) | vm723139291.manageserver.in | 45.196.196.22 | Ubuntu 24.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-20 |
| zivo-node1 | k8s worker + Traefik ingress | vm148786062.manageserver.in | 45.196.196.52 | Ubuntu 24.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-24 |
| zivo-node2 | **dedicated data node** (Postgres + MinIO, tainted) | vm326035110.manageserver.in | 45.196.196.115 | Ubuntu 24.04 | 4 / 7.6 Gi / 96 G | LV 13 | 2026-09-24 |
| zivo-node3 | k8s worker + Traefik ingress | vm127192563.manageserver.in | 45.196.196.191 | Ubuntu 24.04 | 4 / 7.6 Gi / 96 G | LV 13 | 2026-09-24 |
| zivo-node4 | **dedicated GitHub Actions CI runner** (removed from k8s 2026-09-14; kubelet/kubeadm/kubectl purged, `kubeadm reset` done; only Docker + the runner service remain) | vm501272425.manageserver.in | 203.57.85.251 | Ubuntu 22.04 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-20 |

Notes:

- The vendor hostnames (`vm*.manageserver.in`) **do not resolve in public DNS**; always connect by IP.
  (Inside the cluster the k8s node names are exactly these vendor hostnames.)
- zivo-node2 is tainted (`node.zivo/dedicated=data:NoSchedule`) and runs only Postgres and MinIO;
  the chart pins in `environments/prod/{postgres,minio}-values.yaml` hold it there. Traefik runs on
  the two ingress workers (`node.zivo/ingress=true`), so the data node serves no public traffic.
- Nodes span two provider subnets / host systems (`SER17111`, `SER17108`), so a single host failure
  cannot take the etcd quorum.
- The client-area power status can be stale; `zivo-node8` showed "stopped" there while it was Ready
  in the cluster.

## The HA Kubernetes cluster

Kubernetes **v1.36.3** via kubeadm, **Cilium 1.20.1** CNI + CoreDNS (system namespaces only, no app
workloads deployed yet). Built from [infra/ansible](./ansible/) (PR #27); the live topology is now
reflected in `infra/ansible/inventory/hosts.ini`.

| Piece | Server | Detail |
|------------------|-------------------------|----------------------------------------------|
| Control planes (etcd quorum) | zivo-node6, node7, node8 | kube-apiserver on `6443`, etcd on `2379/2380` |
| Workers | zivo-node1, node2, node3 | kubelet + kube-proxy + Cilium |
| API load balancer | zivo-node5 | HAProxy `8443` → apiserver `6443`, `/readyz` HTTPS checks |
| Cluster endpoint | `45.196.196.22:8443` | what kubectl / kubeadm join talk to |
| CI runner (outside cluster) | zivo-node4 | GitHub Actions runner + Docker only; `kubeadm reset` 2026-09-14 — do not join to the cluster |

kubectl access: a working admin kubeconfig lives on **zivo-node5** (`/root/.kube/config`) and
`/etc/kubernetes/admin.conf` on each control-plane node. `kubectl get nodes` from zivo-node5 shows
all 6 nodes Ready.

### Watch items (checked 2026-09-01)

- The control planes are small (2 vCPU) and run hot under etcd + apiserver + Cilium duty
  (1-minute load 2 to 8). Healthy, but do not co-locate extra workloads on nodes 6-8.
- `cloud-final.service` shows failed on zivo-node7: a benign provider-image cloud-init quirk, no impact.
- Ubuntu 24.04 nodes (1-3) use systemd socket activation for SSH (`ssh.socket`), so there is no
  always-on `sshd` listener; SSH works as normal.

## SSH access

Set up 2026-09-01: a dedicated key was generated and installed on every server via the panel
passwords; `~/.ssh/config` carries the aliases below. Password auth still works as fallback
(root password per server is visible in the client area under *Manage → VPS Information*).

```sshconfig
# ~/.ssh/config: Web Eye Soft fleet — role names (legacy zivo-node* also work)
Host zivo-ingress-1
    HostName 45.196.196.52
Host zivo-db-1
    HostName 45.196.196.115
Host zivo-ingress-2
    HostName 45.196.196.191
Host zivo-runner-1
    HostName 203.57.85.251
Host zivo-spare-1
    HostName 45.196.196.22
Host zivo-master-1
    HostName 203.57.85.250
Host zivo-master-2
    HostName 203.57.85.224
Host zivo-master-3
    HostName 203.57.85.157

Host zivo-node* zivo-ingress-* zivo-db-* zivo-runner-* zivo-spare-* zivo-master-*
    User root
    IdentityFile ~/.ssh/zivo_fleet_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

- Fleet public key (installed in `/root/.ssh/authorized_keys` on all servers):
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPOsMijLpcSD7zpzQrbpq6Gpg7fvO6uQkJK+sEkg1Pur zivo-fleet-20260901`
- **No passwords are committed to this repo.** They live in the Web Eye Soft client area;
  locally they are kept in `~/.ssh/zivo-fleet-creds` (chmod 600) on Benito's machine.
- To add a teammate: have them generate a key and append the public key to
  `/root/.ssh/authorized_keys` on the servers they need (or re-run `ssh-copy-id` with the panel
  password).

## zivo app platform on the HA cluster (September 2026)

The HA cluster now runs the full zivo app stack (Zivo_0.1.364): Traefik ingress
(hostNetwork DaemonSet on the three workers, ports 80/443, real client IPs preserved),
cert-manager with the letsencrypt-prod ClusterIssuer, local-path default storage class,
pgbouncer, Postgres (pgvector) and MinIO. Rebuild/re-run with
`scripts/install-ha-platform.sh`; app releases replay from git via ArgoCD sync
(see the Argo CD section below).

- kubectl from a workstation: `KUBECONFIG=~/.kube/zivo-ha.conf` (admin.conf fetched from
  master-1; its server line already targets the LB at 45.196.196.22:8443).
- Ingress entry points: any worker IP on 80/443 (45.196.196.52 / .115 / .191).
- zivo-secrets: applied to the cluster; the secrets.env copy lives on master-1 at
  `/root/.zivo/secrets.env` (chmod 600). Regenerated fresh on 2026-09-04 — external API
  keys came from Benito's `backend/.env.local`, DB/MinIO/session secrets are new randoms.
- TLS is intentionally OFF on all ingresses until the zivo.fyi DNS records move from the
  old VPS IP to a worker IP; then set `tls: true` / `entrypoint: websecure` per values.

## Client area & renewals

- Portal: <https://www.webeyesoft.com/client-area/accounts/services> ; the per-service *Manage* page
  shows IP, root password, OS, power state, and has START / SHUTDOWN / REBOOT / REINSTALL /
  CHANGE PASSWORD / VNC / SSH-console actions.
- **Renewal watch:** all 8 servers renew between **2026-09-12 and 2026-09-24** (₹399-₹699 each).
  Letting the control planes (nodes 6-8) lapse takes out the etcd quorum and the whole cluster.

## Related

- [infra/ansible/](./ansible/): the bootstrap playbook; `inventory/hosts.ini` now holds the real
  IPs and live roles (masters / workers / load_balancer), `ansible_user=root` matching the key above.

## Argo CD (GitOps) — installed 2026-09-06

Argo CD runs in the `argocd` namespace, watching **main**. 13 Applications
(postgres, minio, pgbouncer, auth, storage, practice, content, study, library,
admin, web, worker-io, worker-cpu) auto-sync from
`infra/k8s/charts/*` + `infra/k8s/environments/prod/*`.

- **UI:** `http://argocd.zivo.fyi` (after DNS) — admin / see `~/.ssh/zivo-fleet-creds`
- **CLI-less access:** `kubectl -n argocd get applications` (kubeconfig: `~/.kube/zivo-ha.conf`)
- **Schema migrations are manual**: run `scripts/run-k8s-schema-migrate.sh` from a
  workstation with the admin kubeconfig. The `cicd` pipeline only builds and
  promotes images; Argo CD manages the long-running workloads only.
- **Known cosmetic:** ingress-backed apps show *Degraded* in Argo CD — the
  hostNetwork Traefik has no `loadBalancer` IP for Argo's ingress health rule;
  routing is verified working. Health customization applied via `argocd-cm`.
