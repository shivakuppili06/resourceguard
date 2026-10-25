# ResourceGuard — Build Roadmap

## Week 1–2: Stage 1 (Core K8s fundamentals)
- [ ] Write backend FastAPI service: `/healthz`, `/api/namespaces`
- [ ] Implement `k8s_client.py` against a local kind cluster
- [ ] Implement `scan.py` waste-score logic, test against a few dummy
      namespaces/pods with deliberately over-provisioned requests
- [ ] Build frontend dashboard: fetch `/api/waste`, render table + bar chart
- [ ] Dockerize both services; deploy with raw manifests (`k8s/base`) to kind
- [ ] Verify RBAC: service account can only read pods/nodes/metrics, nothing else

## Week 3–4: Stage 2 (Production-grade orchestration)
- [ ] Convert manifests to a parameterized Helm chart
- [ ] Add CronJob for nightly scans (`app.scan` as entrypoint)
- [ ] Add HPA on the backend; load-test with k6 to prove it scales
- [ ] Add Postgres (in-cluster StatefulSet or external) for scan history,
      wire up `_persist_to_db` in `scan.py`
- [ ] Add ConfigMap/Secret-driven config (Slack webhook, DB URL)
- [ ] Simulate 3–4 "team" namespaces with varied over/under-provisioning
      to get a realistic demo dataset

## Week 5–6: Stage 3 (Real cloud + governance)
- [ ] `terraform apply` the VPC + EKS modules (watch cost — tear down after demo)
- [ ] Push images to ECR, deploy via Helm to EKS
- [ ] Ingress + cert-manager for TLS on a real domain (or nip.io for demo)
- [ ] Install Prometheus + Grafana (kube-prometheus-stack), add a custom
      dashboard panel sourcing from ResourceGuard's own metrics
- [ ] Alertmanager rule → Slack webhook when a namespace exceeds waste threshold
- [ ] GitHub Actions: build → push → `helm upgrade` on merge to main

## Week 7 (stretch): Governance / differentiator
- [ ] OPA/Gatekeeper constraint (or a small custom admission webhook) that
      rejects any Pod spec missing resource requests/limits
- [ ] Record a demo: deploy a "bad" pod, show it being rejected; deploy a
      wasteful namespace, show it flagged in the dashboard and Slack

## Interview talking points this gives you
- Why RBAC is scoped read-only cluster-wide (principle of least privilege)
- Trade-off of CronJob-based batch scanning vs. a live-watching controller
- How the HPA target was chosen and validated under load
- Why Postgres instead of etcd/CRDs for historical scan data
- The admission webhook as a real policy-enforcement mechanism, not just
  a dashboard
