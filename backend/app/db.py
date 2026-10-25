"""
Durable storage for scan history.

Without this, scan results only ever lived in the process's memory
(`_latest_results` in scan.py), which meant: (1) a pod restart silently
lost all history, and (2) the CronJob's scan pod and the long-running API
pod are different processes and never shared that memory in the first
place, so /api/waste would 404 forever in any real multi-pod deployment.
This module makes results durable across both.

DATABASE_URL is a standard libpq connection string, e.g.:
  postgresql://resourceguard:password@resourceguard-postgres:5432/resourceguard
Set via the `secrets.databaseUrl` Helm value -> Kubernetes Secret (see
helm/resourceguard/templates/config.yaml) — never hardcoded here.
"""
import os

import psycopg2

# No custom JSONB registration here. The previous version registered
# `loads=lambda x: x`, which disabled psycopg2's normal JSON decoding and
# made every JSONB column come back as the raw JSON *string* instead of a
# dict. _load_latest_from_db() then returned that string straight to the
# FastAPI route, which the frontend expects to have `.scanned_at` and
# `.namespaces` keys — it doesn't, on a string. psycopg2's default JSONB
# adapter (registered automatically on `import psycopg2.extras` / on
# connect) already decodes to a dict correctly, so the fix is simply not
# overriding it.

_schema_ready = False


def get_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Durable storage requires Postgres; "
            "set secrets.databaseUrl in Helm values or DATABASE_URL in env "
            "for local runs."
        )
    return psycopg2.connect(database_url)


def init_schema(conn) -> None:
    """
    Idempotent, but only actually runs once per process: callers used to
    invoke this on every read and write, which meant a CREATE TABLE IF NOT
    EXISTS / CREATE INDEX IF NOT EXISTS round-trip on every single request
    to the database. The in-memory `_schema_ready` flag below skips that
    after the first successful call. For anything beyond this single
    table, switch to a real migration tool (Alembic) rather than growing
    this function — this flag-based approach does not handle schema
    *changes*, only first-time creation.
    """
    global _schema_ready
    if _schema_ready:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_results (
                id          SERIAL PRIMARY KEY,
                scanned_at  TIMESTAMPTZ NOT NULL,
                payload     JSONB NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scan_results_scanned_at
                ON scan_results (scanned_at DESC);
            """
        )
    conn.commit()
    _schema_ready = True


def check_connection() -> bool:
    """Used by the /healthz readiness check. Returns False rather than
    raising, so the caller can report a clean 'unhealthy' response instead
    of a 500."""
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
        return True
    except Exception:
        return False
