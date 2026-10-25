# ResourceGuard

Kubernetes-native cost & resource governance platform. Tracks real pod
resource usage vs. requested resources per namespace/team, computes waste
scores, surfaces recommendations, and (optionally) enforces resource-limit
policy via an admission webhook.

## Why

Product companies running Kubernetes routinely over-provision workloads.
ResourceGuard gives platform/FinOps teams per-namespace visibility into
waste, nightly reports, and budget alerts — a lightweight, self-hosted
alternative to Kubecost.

## Architecture

```
            ┌─────────────┐
            │   React UI   │  (dashboard: waste %, cost per namespace)
            └──────┬───────┘
                   │ REST
            ┌──────▼───────┐        ┌────────────────┐
            │  Backend API  │◄──────┤  Metrics Server  │ (CPU/mem usage)
            │ (FastAPI/Go)  │        └────────────────┘
            └──────┬───────┘
                   │
       ┌───────────┼─────────────┐
       │           │             │
┌──────▼─────┐ ┌───▼────┐  ┌─────▼──────┐
│  CronJob    │ │Postgres │  │ Prometheus │
│ nightly scan│ │ (results)│  │  /Grafana  │
└─────────────┘ └─────────┘  └────────────┘
```

## Repo layout

- `backend/` — FastAPI service: polls Kubernetes Metrics API, computes
  waste scores, exposes REST endpoints, nightly scan job entrypoint.
- `frontend/` — React dashboard (Vite).
- `k8s/` — raw Kubernetes manifests (Stage 1, Minikube/kind).
- `helm/resourceguard/` — Helm chart (Stage 2, parameterized deploy).
- `terraform/` — EKS cluster + VPC provisioning (Stage 3, real cloud).
- `.github/workflows/` — CI/CD: build, test, push image, deploy.
- `docs/` — architecture notes, runbook, roadmap.

## Build stages

| Stage | What you do |
|---|---|
| 1 | `docker build` backend+frontend, `kubectl apply -f k8s/base` on kind/Minikube |
| 2 | `helm install` the chart; CronJob + HPA + ConfigMaps/Secrets |
| 3 | `terraform apply` → real EKS cluster; Ingress+TLS; Prometheus/Grafana; Alertmanager→Slack; OPA/Gatekeeper policy |

See `docs/ROADMAP.md` for the week-by-week plan.

## Quickstart (local)

```bash
# 1. Build images
docker build -t resourceguard-backend:local ./backend
docker build -t resourceguard-frontend:local ./frontend

# 2. Start a local cluster
kind create cluster --name resourceguard

# 3. Load images into kind
kind load docker-image resourceguard-backend:local --name resourceguard
kind load docker-image resourceguard-frontend:local --name resourceguard

# 4. Deploy
kubectl apply -f k8s/base/

# 5. Port-forward and open the dashboard
kubectl port-forward svc/resourceguard-frontend 5173:80
```

## License

MIT — portfolio project.
