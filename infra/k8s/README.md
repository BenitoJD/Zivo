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
| `charts/db-schema` | `db-schema` | one-off intel schema Job |
| `charts/api` | `zivo-api` | FastAPI |
| `charts/web` | `zivo-web` | Next.js frontend |

Prod values: `environments/prod/*.yaml`

## DNS (required before TLS works)

| Host | Points to |
|------|-----------|
| `zivo.fyi` | `103.194.228.47` |
| `www.zivo.fyi` | `103.194.228.47` |
| `api.zivo.fyi` | `103.194.228.47` |
| `s3.zivo.fyi` | `103.194.228.47` |

## Deploy

**Normal path:** GitHub Actions → **Deploy Zivo** (builds API + web images, Helm upgrade on VPS).

**Manual** (on VPS with repo checked out):

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
NS=zivo
TAG=Zivo_0.1.N

helm upgrade --install zivo-postgres ./infra/k8s/charts/postgres -n $NS --create-namespace \
  -f infra/k8s/environments/prod/postgres-values.yaml --wait
helm upgrade --install zivo-minio ./infra/k8s/charts/minio -n $NS \
  -f infra/k8s/environments/prod/minio-values.yaml --wait

IMAGE_REPOSITORY=ghcr.io/benitojd/zivo-api IMAGE_TAG=$TAG \
  ./scripts/run-k8s-schema-migrate.sh

helm upgrade --install zivo-api ./infra/k8s/charts/api -n $NS \
  -f infra/k8s/environments/prod/backend-release-values.yaml \
  -f infra/k8s/environments/prod/api-values.yaml --set image.tag=$TAG --wait
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
