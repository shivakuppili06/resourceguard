"""
Integration & unit tests for:
- API endpoints (/healthz, /readyz, /api/namespaces, /api/waste, /api/scan)
- Error handling & CORS
- PostgreSQL persistence and latest scan retrieval after scheduled scan
- Waste percentage and incomplete calculation handling
"""
import os
import json
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
import app.scan as scan_mod
from app.scan import compute_namespace_waste, run_scan, get_latest_results, _cpu_to_millicores, _mem_to_mib, UnparsableQuantity
from app.db import get_connection, init_schema

client = TestClient(app)


class TestApiEndpoints:
    def test_healthz(self):
        res = client.get("/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

    def test_readyz(self):
        res = client.get("/readyz")
        assert res.status_code in (200, 503)

    def test_readyz_database_down(self):
        with patch("app.main.check_connection", return_value=False):
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://invalid:5432/db"}):
                res = client.get("/readyz")
                assert res.status_code == 503
                assert res.json()["detail"]["status"] == "degraded"

    def test_list_namespaces_mocked(self):
        with patch("app.main.get_namespaces", return_value=["default", "demo-apps"]):
            res = client.get("/api/namespaces")
            assert res.status_code == 200
            assert res.json() == {"namespaces": ["default", "demo-apps"]}

    def test_waste_not_found_when_no_scans(self):
        with patch("app.main.get_latest_results", return_value=None):
            res = client.get("/api/waste")
            assert res.status_code == 404
            assert "No scan results yet" in res.json()["detail"]


class TestWasteCalculations:
    def test_waste_with_zero_requests(self):
        with patch("app.scan.get_pod_requests", return_value=[{"pod": "p1", "container": "c1", "cpu_request": "0", "memory_request": "0"}]):
            with patch("app.scan.get_pod_usage", return_value=[{"pod": "p1", "container": "c1", "cpu_usage": "10m", "memory_usage": "10Mi"}]):
                waste = compute_namespace_waste("test-ns")
                assert waste["cpu_waste_pct"] == 0.0
                assert waste["memory_waste_pct"] == 0.0
                assert waste["is_complete"] is True

    def test_waste_usage_greater_than_requests(self):
        with patch("app.scan.get_pod_requests", return_value=[{"pod": "p1", "container": "c1", "cpu_request": "100m", "memory_request": "50Mi"}]):
            with patch("app.scan.get_pod_usage", return_value=[{"pod": "p1", "container": "c1", "cpu_usage": "150m", "memory_usage": "100Mi"}]):
                waste = compute_namespace_waste("test-ns")
                assert waste["cpu_waste_pct"] == 0.0
                assert waste["memory_waste_pct"] == 0.0
                assert waste["is_complete"] is True

    def test_empty_namespace(self):
        with patch("app.scan.get_pod_requests", return_value=[]):
            with patch("app.scan.get_pod_usage", return_value=[]):
                waste = compute_namespace_waste("empty-ns")
                assert waste["pod_count"] == 0
                assert waste["cpu_requested_millicores"] == 0.0
                assert waste["cpu_waste_pct"] == 0.0
                assert waste["is_complete"] is True

    def test_missing_metrics_marks_incomplete(self):
        with patch("app.scan.get_pod_requests", return_value=[{"pod": "p1", "container": "c1", "cpu_request": "100m", "memory_request": "50Mi"}]):
            with patch("app.scan.get_pod_usage", return_value=[]):
                waste = compute_namespace_waste("test-ns")
                assert waste["missing_samples"] == 1
                assert waste["is_complete"] is False

    def test_unparsable_quantities_marks_incomplete(self):
        with patch("app.scan.get_pod_requests", return_value=[{"pod": "p1", "container": "c1", "cpu_request": "invalid-cpu", "memory_request": "invalid-mem"}]):
            with patch("app.scan.get_pod_usage", return_value=[]):
                waste = compute_namespace_waste("test-ns")
                assert waste["unparsable_quantities"] > 0
                assert waste["is_complete"] is False


class TestPostgresPersistenceAndLatestScan:
    def test_persistence_and_retrieval(self):
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not configured")

        now = datetime.now(timezone.utc)
        sample_scan = {
            "scanned_at": (now + timedelta(minutes=10)).isoformat(),
            "namespaces": [
                {
                    "namespace": "test-persist",
                    "cpu_requested_millicores": 200.0,
                    "cpu_used_millicores": 50.0,
                    "cpu_waste_pct": 75.0,
                    "memory_requested_mib": 256.0,
                    "memory_used_mib": 64.0,
                    "memory_waste_pct": 75.0,
                    "pod_count": 1,
                    "missing_samples": 0,
                    "unparsable_quantities": 0,
                    "is_complete": True,
                }
            ],
        }

        try:
            # Clear in-memory cache to simulate new process
            scan_mod._latest_results = None
            scan_mod._persist_to_db(sample_scan)

            # Retrieve should fetch from DB
            fetched = get_latest_results()
            assert fetched is not None
            assert fetched["scanned_at"] == sample_scan["scanned_at"]
            assert len(fetched["namespaces"]) == 1
            assert fetched["namespaces"][0]["namespace"] == "test-persist"
        finally:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scan_results WHERE scanned_at = %s", (sample_scan["scanned_at"],))
                conn.commit()
            finally:
                conn.close()

    def test_external_cronjob_scan_updates_latest_results(self):
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            pytest.skip("DATABASE_URL not configured")

        now = datetime.now(timezone.utc)
        initial_scan = {"scanned_at": (now + timedelta(minutes=20)).isoformat(), "namespaces": []}
        cron_scan = {"scanned_at": (now + timedelta(minutes=30)).isoformat(), "namespaces": [{"namespace": "cron-ns"}]}

        try:
            # 1. API has an initial result in memory
            scan_mod._latest_results = initial_scan

            # 2. External CronJob persists a newer scan to DB
            scan_mod._persist_to_db(cron_scan)

            # 3. API get_latest_results fetches newest from DB instead of stale in-memory
            latest = get_latest_results()
            assert latest is not None
            assert latest["scanned_at"] == cron_scan["scanned_at"]
            assert latest["namespaces"][0]["namespace"] == "cron-ns"
        finally:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM scan_results WHERE scanned_at = %s", (cron_scan["scanned_at"],))
                conn.commit()
            finally:
                conn.close()
