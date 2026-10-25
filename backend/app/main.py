"""
ResourceGuard backend — FastAPI service.

Responsibilities:
- Expose REST endpoints the dashboard consumes (namespaces, waste scores,
  recommendations, budget status).
- Provide a `/scan` endpoint that the nightly CronJob hits to trigger a
  cluster-wide resource usage scan (also runnable as a standalone script,
  see scan.py).
- Read-only access to the Kubernetes Metrics API — this service only
  needs a ServiceAccount with get/list on pods, nodes, and
  metrics.k8s.io resources (see k8s/base/rbac.yaml).
"""
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.scan import run_scan, get_latest_results
from app.k8s_client import get_namespaces
from app.db import check_connection

app = FastAPI(title="ResourceGuard API", version="0.1.0")

# Configure CORS: defaults to local dev origin http://localhost:5173, configurable via ALLOWED_ORIGINS
allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz")
def healthz():
    """
    Liveness only: is the process up and able to respond. Deliberately
    does NOT check the database — a liveness probe that depends on an
    external dependency causes Kubernetes to kill and restart a perfectly
    healthy pod whenever Postgres has a blip, which just compounds the
    outage. Use /readyz for the dependency check instead.
    """
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """
    Readiness: is this pod able to actually serve real traffic right now.
    Checks the database connection when DATABASE_URL is configured, since
    /api/waste depends on it. Wire this into the Deployment's
    readinessProbe (not livenessProbe) so Kubernetes stops routing
    traffic here during a DB outage without restarting the pod.
    """
    checks = {}
    if os.getenv("DATABASE_URL"):
        checks["database"] = check_connection()
    ok = all(checks.values()) if checks else True
    if not ok:
        raise HTTPException(status_code=503, detail={"status": "degraded", "checks": checks})
    return {"status": "ok", "checks": checks}


@app.get("/api/namespaces")
def list_namespaces():
    """List namespaces currently visible to the service account."""
    return {"namespaces": get_namespaces()}


@app.get("/api/waste")
def waste_report():
    """
    Returns the most recent scan results: per-namespace requested vs.
    actual usage, and a computed waste percentage.
    """
    results = get_latest_results()
    if results is None:
        raise HTTPException(status_code=404, detail="No scan results yet. POST /api/scan first.")
    return results


@app.post("/api/scan")
def trigger_scan():
    """Manually trigger a scan (the CronJob calls this on a schedule)."""
    results = run_scan()
    return {"status": "completed", "namespaces_scanned": len(results.get("namespaces", []))}
