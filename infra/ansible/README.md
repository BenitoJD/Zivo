# Ansible bootstrap: HA Kubernetes cluster (3 masters + 3 workers + 1 external LB)

Automates the HA setup guide ("HA Kubernetes Cluster Installation, 3 Master Nodes +
3 Worker Nodes + 1 External Load Balancer", Cilium revision) end to end:

7 VMs bootstrapped by one `ansible-playbook site.yml` run. HAProxy owns port 8443
on `master-lb` and forwards to kube-apiserver on 6443 on each master; `master-lb`
is never a Kubernetes node and never appears in `kubectl get nodes`.

| Component | Version | Source |
|-----------|---------|--------|
| Kubernetes (kubeadm / kubelet / kubectl) | v1.36.3 | pkgs.k8s.io `v1.36` apt channel |
| containerd | v2.3.3 | upstream binary tarball (LTS branch) |
| runc | v1.4.3 | upstream binary |
| CNI plugins | v1.9.1 | containernetworking tarball to `/opt/cni/bin` |
| crictl | v1.36.0 | cri-tools release |
| Cilium CNI + cilium-cli | v1.20.1 / latest stable.txt | cilium-cli binary |
| etcdctl (verification only) | v3.6.14 | etcd-io release |
| HAProxy | distro default | Ubuntu 24.04 apt |

The guide was written against Ubuntu 22.04 LTS; these VMs run **Ubuntu 24.04 LTS**.
Nothing changes component-wise: every pinned item is a distro-independent binary
tarball or external apt repo, noble ships Python 3.12 for Ansible and cgroup v2 by
default, its kernel (6.8) satisfies Cilium's minimum of kernel 5.10, and its HAProxy
(2.6) / netcat-openbsd support everything this playbook configures.

## Requirements

- Ansible 2.12+ on the controller with SSH access to all 7 VMs; built-in modules
  only (no extra collections needed).
- Ubuntu 24.04 LTS VMs with cgroup v2 (`stat -fc %T /sys/fs/cgroup/` must print
  `cgroup2fs`; the playbook also asserts this and fails fast).
- The inventory user has sudo on every VM (`become: true` is set in `site.yml`;
  add `--ask-become-pass` if not passwordless).
- Outbound internet from all nodes for binaries/repos; load balancer reachable
  from your workstation over SSH.

## Setup

1. Edit `inventory/hosts.ini`: replace the placeholder `ansible_host` IPs.

   ```ini
   [masters]
   master-1 ansible_host=10.0.0.11
   master-2 ansible_host=10.0.0.12
   master-3 ansible_host=10.0.0.13

   [workers]
   worker-1 ansible_host=10.0.0.21
   ...

   [load_balancer]
   master-lb ansible_host=10.0.0.10

   [all:vars]
   ansible_user=ubuntu
   ```

2. Review `inventory/group_vars/all/00-main.yml`. Before running, confirm the pod CIDR
   (`192.168.0.0/16`) and service CIDR (`10.96.0.0/12`) do NOT overlap your VM
   subnet. If they do, change `pod_network_cidr` there: it flows to both kubeadm
   init and Cilium's Cluster Pool IPAM so the two stay consistent.

3. Open the required ports between the VMs (firewall/cloud security groups are
   outside the playbook's scope):

   | Hosts | Ports |
   |-------|-------|
   | master-lb | 8443 inbound (kubectl / join clients); outbound 6443 to masters |
   | masters | 6443, 2379-2380, 10250, 10257, 10259 |
   | workers | 10250, 10256, 30000-32767 |
   | all 6 k8s nodes | UDP 8472 (Cilium VXLAN); optional TCP 4240 + ICMP echo for cilium-health |

## Run

```bash
cd infra/ansible
ansible-playbook site.yml                  # everything, in order
ansible-playbook site.yml --list-tasks     # preview
ansible-playbook site.yml --tags prereq    # OS/runtime prep only
ansible-playbook site.yml --tags lb        # HAProxy + kubectl client only
ansible-playbook site.yml --tags join      # re-run joins safely
ansible-playbook site.yml --tags verify    # final checks only
```

Plays execute in this order: common -> prerequisites -> containerd -> Kubernetes
packages -> haproxy -> control-plane init on master-1 -> kubeconfig on master-lb
-> Cilium -> join master-2/master-3 -> CoreDNS rebalance -> join workers ->
verification.

## Guide step mapping

| Guide step | Where it happens |
|------------|------------------|
| Prereq (cgroup v2), Steps 1-3 (swap, modules, sysctl) | `common`, `k8s_prereqs` |
| Step 4-6 (containerd 2.3.3, runc 1.4.3, CNI plugins 1.9.1) | `containerd` |
| Step 7 (apt repo, pinned 1.36.3 packages, hold) + Step 8 (crictl) | `kubernetes` |
| Steps 9-10 (HAProxy install/config, port checks from lb) | `haproxy` |
| Step 11 (kubeadm init at `<lb>:8443`, upload certs) | `control_plane_init` |
| Step 12 (kubectl on master-1), 12b (kubectl + admin.conf on lb) | `control_plane_init`, `kubernetes` (reused, kubectl only), `lb_kubeconfig` |
| Step 13 (cilium-cli + cilium install 1.20.1, status wait) | `cilium` |
| Step 14 (join masters 2-3, control-plane cert key) | `control_plane_join` |
| Step 14b (CoreDNS rebalance, optional via `rebalance_coredns`) | `coredns_rebalance` |
| Step 15 (worker joins) | `worker_join` |
| Final verification (nodes Ready, pods, etcd quorum, lb view) | `cluster_verify` |

## How joins stay reliable

Instead of parsing `kubeadm init` output, `control_plane_init` generates a random
certificate key (`kubeadm certs certificate-key`) up front, passes it to init
with `--upload-certs`, and mints a fresh token with
`kubeadm token create --print-join-command`. On every re-run of the playbook,
init skips itself (`/etc/kubernetes/admin.conf` exists), uploaded certs are
re-encrypted under the new key (`kubeadm init phase upload-certs --upload-certs
--certificate-key=...`), which defeats both failure modes from the guide: an
expired certificate key (2h TTL) and an expired join token (24h TTL). Joins skip
themselves when `/etc/kubernetes/kubelet.conf` already exists, so the whole
playbook is safe to run repeatedly.

## What the playbook gives you at the end

```
NAME       STATUS   ROLES           VERSION
master-1   Ready    control-plane   v1.36.3
master-2   Ready    control-plane   v1.36.3
master-3   Ready    control-plane   v1.36.3
worker-1   Ready    <none>          v1.36.3
worker-2   Ready    <none>          v1.36.3
worker-3   Ready    <none>          v1.36.3
```

Admin access points: `~/.kube/config` on master-1..3 and on master-lb (plus a copy
in `.artifacts/admin.conf` on the controller, gitignored). Point your laptop's
kubeconfig at `<LB_IP>:8443` too if you like; keep one break-glass copy in case
the load-balancer VM ever goes down.

## Caveats carried over from the guide

- **Single LB is a single point of failure for external access**: no Keepalived /
  floating IP here. Cluster workloads keep running if master-lb dies, but kubectl
  and future joins stop until it recovers.
- **admin.conf grants cluster-admin.** Restrict SSH on the masters and master-lb;
  rotate the cluster CA if any copy leaks.
- **Versions are pinned exactly** (`1.36.3-*` etc.) so every node reports the same
  patch; change them only in `inventory/group_vars/all/00-main.yml`, everywhere at once.
- **Optional end-to-end check**: set `cilium_connectivity_test: true` in
  `inventory/group_vars/all/00-main.yml` to have the final verification play run
  `cilium connectivity test` after all 6 nodes join (slower, creates temporary
  test pods across nodes). Off by default.
- **CoreDNS still colocated after the rebalance restart?** Its restart relies on
  soft anti-affinity, which usually succeeds but is not guaranteed. If both
  replicas stay on one node even after master-3 joins, patch in hard anti-affinity:

  ```bash
  kubectl -n kube-system patch deployment coredns --type=json -p='[{"op":"add","path":"/spec/template/spec/affinity/podAntiAffinity/requiredDuringSchedulingIgnoredDuringExecution","value":[{"labelSelector":{"matchLabels":{"k8s-app":"kube-dns"}},"topologyKey":"kubernetes.io/hostname"}]}]'
  ```
