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

## Local Development

### 1. Start PostgreSQL
```bash
docker compose up -d postgres
```

### 2. Configure Environment & Python Virtual Environment
```bash
cp backend/.env.example backend/.env
cd backend
python -m venv .venv
# On Windows:
.\.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Connect to a Local Kubernetes Cluster
```bash
kind create cluster --name resourceguard

# Install metrics-server for CPU/memory usage metrics
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type="json" -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

### 4. Run the Backend & Frontend
In terminal 1 (Backend API):
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
- Liveness check: `http://localhost:8000/healthz`
- Readiness check (includes DB status): `http://localhost:8000/readyz`
- Interactive API documentation: `http://localhost:8000/docs`

In terminal 2 (Frontend Dashboard):
```bash
cd frontend
npm install
npm run dev
```
Open `http://localhost:5173/` and click **"Run scan now"** to execute a live cluster scan.

### 5. Running Full In-Cluster Deployment (Stage 1 Manifests)
Alternatively, to run the entire stack inside the `kind` cluster with Kubernetes manifests:
```bash
# 1. Build local container images
docker build -t resourceguard-backend:local ./backend
docker build -t resourceguard-frontend:local ./frontend

# 2. Load images into the kind cluster
kind load docker-image resourceguard-backend:local --name resourceguard
kind load docker-image resourceguard-frontend:local --name resourceguard

# 3. Deploy manifests
kubectl apply -f k8s/base/

# 4. Port-forward frontend service
kubectl port-forward svc/resourceguard-frontend 5173:80 -n resourceguard
```

## License

MIT — portfolio project.
