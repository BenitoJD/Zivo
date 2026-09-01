# Server inventory: Web Eye Soft fleet

All 9 VPS rented from **Web Eye Soft** ([client area](https://www.webeyesoft.com/client-area/accounts/services)),
surveyed live over SSH on **2026-09-01**. Seven of them form the HA Kubernetes cluster that
[infra/ansible](./ansible/) bootstraps (PR #27); one is idle; one runs unrelated standalone apps.

**TL;DR for teammates:** `ssh zivo-node1` … `ssh zivo-node9` (key auth, root). See [SSH access](#ssh-access).

## Fleet at a glance

| Alias | Role | Vendor hostname | IP | OS | vCPU / RAM / disk | Plan | Renews |
|-----------|--------------------------|--------------------------|-----------------|------------------|-------------------|-------|------------|
| zivo-node1 | k8s worker | vm148786062.manageserver.in | 45.196.196.52 | Ubuntu 24.04.4 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-24 |
| zivo-node2 | k8s worker | vm326035110.manageserver.in | 45.196.196.115 | Ubuntu 24.04.4 | 4 / 7.6 Gi / 96 G | LV 13 | 2026-09-24 |
| zivo-node3 | k8s worker | vm127192563.manageserver.in | 45.196.196.191 | Ubuntu 24.04.4 | 4 / 7.6 Gi / 96 G | LV 13 | 2026-09-24 |
| zivo-node4 | **idle / spare** | vm501272425.manageserver.in | 203.57.85.251 | Ubuntu 22.04.5 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-20 |
| zivo-node5 | k8s API load balancer | vm723139291.manageserver.in | 45.196.196.22 | Ubuntu 22.04.5 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-20 |
| zivo-node6 | k8s control plane | vm759659741.manageserver.in | 203.57.85.250 | Ubuntu 22.04.5 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node7 | k8s control plane | vm997512676.manageserver.in | 203.57.85.224 | Ubuntu 22.04.5 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node8 | k8s control plane | vm130951261.manageserver.in | 203.57.85.157 | Ubuntu 22.04.5 | 2 / 3.7 Gi / 48 G | LV 12 | 2026-09-12 |
| zivo-node9 | standalone apps box | vm627572835.manageserver.in | 203.57.85.94 | Ubuntu 22.04.5 | 6 / 11 Gi / 140 G | LV 8 | 2026-10-01 |

Notes:

- The vendor hostnames (`vm*.manageserver.in`) **do not resolve in public DNS**; always connect by IP.
  (Inside the cluster the k8s node names are exactly these vendor hostnames.)
- Nodes span two provider subnets / host systems (`SER17111`, `SER17108`), so a single host failure
  cannot take the etcd quorum.
- The client-area power status can be stale; `zivo-node8` showed "stopped" there while it was Ready
  in the cluster.

## The HA Kubernetes cluster

Kubernetes **v1.36.3** via kubeadm, **Cilium 1.20.1** CNI + CoreDNS (system namespaces only, no app
workloads deployed yet). Built from [infra/ansible](./ansible/) (PR #27).

| Piece | Server | Detail |
|------------------|-------------------------|----------------------------------------------|
| Control planes (etcd quorum) | zivo-node6, node7, node8 | kube-apiserver on `6443`, etcd on `2379/2380` |
| Workers | zivo-node1, node2, node3 | kubelet + kube-proxy + Cilium |
| API load balancer | zivo-node5 | HAProxy `8443` → apiserver `6443`, `/readyz` HTTPS checks |
| Cluster endpoint | `45.196.196.22:8443` | what kubectl / kubeadm join talk to |
| Spare | zivo-node4 | bare Ubuntu 22.04, nothing installed |

kubectl access: a working admin kubeconfig lives on **zivo-node5** (`/root/.kube/config`) and
`/etc/kubernetes/admin.conf` on each control-plane node. `kubectl get nodes` from zivo-node5 shows
all 6 nodes Ready.

## zivo-node9: standalone apps box (not part of the Zivo cluster)

14-week uptime, Docker + nginx + helm. Runs two throwaway-in-docker clusters and a few apps:

- `k3d-prod-cluster` and `k3d-nonprod-cluster` (k3s v1.31.5 containers, apiserver on `6443`/`6444`)
- `assetlink-app` (`:3010`) with `assetlink-minio` (`:9100/9101`), a second MinIO (`:9000/9001`), ChartDB (`:8080`)
- PostgreSQL listening on `:5432`, nginx on `:80/:443`

## SSH access

Set up 2026-09-01: a dedicated key was generated and installed on every server via the panel
passwords; `~/.ssh/config` carries the aliases below. Password auth still works as fallback
(root password per server is visible in the client area under *Manage → VPS Information*).

```sshconfig
# ~/.ssh/config: Web Eye Soft fleet
Host zivo-node1
    HostName 45.196.196.52
Host zivo-node2
    HostName 45.196.196.115
Host zivo-node3
    HostName 45.196.196.191
Host zivo-node4
    HostName 203.57.85.251
Host zivo-node5
    HostName 45.196.196.22
Host zivo-node6
    HostName 203.57.85.250
Host zivo-node7
    HostName 203.57.85.224
Host zivo-node8
    HostName 203.57.85.157
Host zivo-node9
    HostName 203.57.85.94

Host zivo-node*
    User root
    IdentityFile ~/.ssh/zivo_fleet_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

- Fleet public key (installed in `/root/.ssh/authorized_keys` on all 9):
  `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPOsMijLpcSD7zpzQrbpq6Gpg7fvO6uQkJK+sEkg1Pur zivo-fleet-20260901`
- **No passwords are committed to this repo.** They live in the Web Eye Soft client area;
  locally they are kept in `~/.ssh/zivo-fleet-creds` (chmod 600) on Benito's machine.
- To add a teammate: have them generate a key and append the public key to
  `/root/.ssh/authorized_keys` on the servers they need (or re-run `ssh-copy-id` with the panel
  password).

## Client area & renewals

- Portal: <https://www.webeyesoft.com/client-area/accounts/services> ; the per-service *Manage* page
  shows IP, root password, OS, power state, and has START / SHUTDOWN / REBOOT / REINSTALL /
  CHANGE PASSWORD / VNC / SSH-console actions.
- **Renewal watch:** 8 of the 9 servers renew between **2026-09-12 and 2026-09-24** (₹399–₹699
  each; zivo-node9 ₹999 on 2026-10-01). Let them lapse and the cluster loses its etcd quorum.

## Related

- [infra/ansible/](./ansible/): the bootstrap playbook for the HA cluster (masters / workers /
  load_balancer groups in `inventory/hosts.ini` still hold placeholder IPs; fill them from the
  table above when re-provisioning).
- Production zivo app stack: `103.194.228.47` (`ssh zivo-vps`, separate provider); see
  [AGENTS.md](../AGENTS.md#production-vps).
