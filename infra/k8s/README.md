# Zivo production (K3s on single VPS)

Helm charts and prod values. Deploy model matches [zivo](https://github.com/BenitoJD/zivo).

## VPS

| | |
|---|---|
| IP | `103.194.228.47` |
| SSH | `ssh zivo-vps` |
| K3s | Installed |
| Runner | `self-hosted`, `zivo` |
| App namespace | `zivo` (recreated on deploy) |

## Charts

| Chart | Release | Purpose |
|-------|---------|---------|
| `charts/postgres` | `zivo-postgres` | pgvector Postgres |
| `charts/minio` | `zivo-minio` | artifact object storage |
| `charts/db-schema` | `auth-schema`, `storage-schema`, `practice-schema`, `content-schema`, `study-schema`, `library-schema`, `admin-schema`, `db-schema` | Alembic jobs |
| `charts/auth` | `zivo-auth` | identity FastAPI (`auth.zivo.fyi`) |
| `charts/storage` | `zivo-storage` | object FastAPI (`storage.zivo.fyi`) |
| `charts/practice` | `zivo-practice` | coding / system-design / newspaper practice (`practice.zivo.fyi`) |
| `charts/content` | `zivo-content` | SEO /learn + newspaper admin (`content.zivo.fyi`) |
| `charts/study` | `zivo-study` | learn/grade/chat (`study.zivo.fyi`) |
| `charts/library` | `zivo-library` | sources/documents (`library.zivo.fyi`) |
| `charts/admin` | `zivo-admin` | models/debug (`admin.zivo.fyi`) |
| `charts/worker` | `zivo-worker-io`, `zivo-worker-cpu` | slim `zivo-worker` image |
| `charts/web` | `zivo-web` | Next.js frontend |

Product Alembic image: `zivo-migrate` (backend Dockerfile), used by the `db-schema` Job. There is no `zivo-api` release.

Prod values: `environments/prod/*.yaml`

## DNS (required before TLS works)

| Host | Points to |
|------|-----------|
| `zivo.fyi` | `103.194.228.47` |
| `www.zivo.fyi` | `103.194.228.47` |
| `api.zivo.fyi` | `103.194.228.47` |
| `auth.zivo.fyi` | `103.194.228.47` |
| `storage.zivo.fyi` | `103.194.228.47` |
| `practice.zivo.fyi` | `103.194.228.47` |
| `content.zivo.fyi` | `103.194.228.47` |
| `study.zivo.fyi` | `103.194.228.47` |
| `library.zivo.fyi` | `103.194.228.47` |
| `admin.zivo.fyi` | `103.194.228.47` |
| `s3.zivo.fyi` | `103.194.228.47` |

## Deploy

**Normal path:** GitHub Actions → **Deploy Zivo** (builds API + auth + storage + practice + content + study + library + admin + worker + web images, Helm upgrade on VPS).

**Manual** (on VPS with repo checked out):

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
NS=zivo
TAG=Zivo_0.1.N

helm upgrade --install zivo-postgres ./infra/k8s/charts/postgres -n $NS --create-namespace \
  -f infra/k8s/environments/prod/postgres-values.yaml --wait
helm upgrade --install zivo-minio ./infra/k8s/charts/minio -n $NS \
  -f infra/k8s/environments/prod/minio-values.yaml --wait

helm upgrade --install auth-schema ./infra/k8s/charts/db-schema -n $NS \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.repository=ghcr.io/benitojd/zivo-auth \
  --set image.tag=$TAG --set jobName=auth-alembic-migrate --wait

helm upgrade --install storage-schema ./infra/k8s/charts/db-schema -n $NS \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.repository=ghcr.io/benitojd/zivo-storage \
  --set image.tag=$TAG --set jobName=storage-alembic-migrate --wait

helm upgrade --install db-schema ./infra/k8s/charts/db-schema -n $NS \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  --set image.repository=ghcr.io/benitojd/zivo-migrate \
  --set image.tag=$TAG --set jobName=alembic-migrate --wait

helm upgrade --install zivo-auth ./infra/k8s/charts/auth -n $NS \
  -f infra/k8s/environments/prod/auth-values.yaml --set image.tag=$TAG --wait
helm upgrade --install zivo-storage ./infra/k8s/charts/storage -n $NS \
  -f infra/k8s/environments/prod/storage-values.yaml --set image.tag=$TAG --wait
helm upgrade --install zivo-web ./infra/k8s/charts/web -n $NS \
  -f infra/k8s/environments/prod/web-values.yaml --set image.tag=$TAG --wait
```

## Secrets

Generated on bootstrap: `/root/.zivo/secrets.env`

Re-apply to cluster:

```bash
./scripts/apply-k8s-secrets.sh /root/.zivo/secrets.env
```

## TLS

cert-manager ClusterIssuer: `infra/k8s/cert-manager/cluster-issuer.yaml`  
Install: `scripts/install-cert-manager.sh`

`auth.zivo.fyi`, `storage.zivo.fyi`, `practice.zivo.fyi`, `content.zivo.fyi`,
and `study.zivo.fyi` need A records to `103.194.228.47` before Let's Encrypt
will issue. Until those records exist:

- Docker build must leave `NEXT_PUBLIC_AUTH_URL` and `NEXT_PUBLIC_STORAGE_URL`
  **empty** so the browser stays on `zivo.fyi` and Next rewrites `/api/auth`,
  `/api/storage`, practice, content, and study paths to in-cluster services.
- Those Ingresses stay HTTP-only (`tls: false`) so cert-manager does not park
  an ACME solver on a hostname that does not resolve.
- After DNS answers: set `tls: true` / `entrypoint: websecure` on the matching
  `*-values.yaml`, bake public URLs if you want them, and make new GHCR
  packages public if k3s should pull them without the deploy-job token
  (same footgun as `zivo-auth` / `zivo-storage`).
