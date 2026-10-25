"""
Core FinOps logic: compare requested resources against actual usage and
compute a waste score per namespace.

This module is intentionally dependency-light (no DB ORM) so it can also
be run standalone as the nightly CronJob entrypoint:

    python -m app.scan

In that mode it writes results to Postgres (via DATABASE_URL env var) and
exits; as a FastAPI-imported module it keeps the last result in memory and
returns it from get_latest_results().
"""
import json
import os
import re
from datetime import datetime, timezone

from app.k8s_client import get_namespaces, get_pod_requests, get_pod_usage
from app.db import get_connection, init_schema

_latest_results: dict | None = None


def _cpu_to_millicores(value: str) -> float:
    """
    Parse Kubernetes CPU quantities into millicores.

    Requests/limits (from pod spec) use 'm' (millicores) or bare cores,
    e.g. "100m", "1", "0.5".

    The Metrics API (metrics.k8s.io) reports *actual usage* in nanocores
    or microcores, e.g. "123456789n", "500u" — NOT millicores or bare
    cores. Treating "250000000n" as if it were "250000000" whole cores
    (the original bug) silently produced waste percentages that were off
    by six to nine orders of magnitude. All four unit forms must be
    handled explicitly.
    """
    value = value.strip()
    if not value:
        return 0.0
    try:
        if value.endswith("n"):      # nanocores: 1 core = 1e9 n = 1000 m
            return float(value[:-1]) / 1_000_000
        if value.endswith("u"):      # microcores: 1 core = 1e6 u = 1000 m
            return float(value[:-1]) / 1_000
        if value.endswith("m"):      # millicores
            return float(value[:-1])
        # bare cores
        return float(value) * 1000
    except ValueError:
        raise UnparsableQuantity(f"Cannot parse CPU quantity: {value!r}")


class UnparsableQuantity(ValueError):
    """Raised when a resource quantity string doesn't match any known
    Kubernetes unit suffix, instead of being silently misinterpreted."""


# Binary (power-of-1024) units, as Kubernetes uses for memory requests/
# limits and for what the Metrics API reports. Decimal (power-of-1000)
# units are also legal Kubernetes quantity suffixes and are included so a
# value like "500M" isn't rejected just because it's less common in
# practice.
_MEMORY_MULTIPLIERS_TO_MIB = {
    "Ki": 1 / 1024,
    "Mi": 1,
    "Gi": 1024,
    "Ti": 1024 * 1024,
    "K": 1000 / (1024 * 1024),
    "M": 1000 * 1000 / (1024 * 1024),
    "G": 1000 * 1000 * 1000 / (1024 * 1024),
    "T": 1000 * 1000 * 1000 * 1000 / (1024 * 1024),
    "": 1 / (1024 * 1024),  # bare bytes
}


def _mem_to_mib(value: str) -> float:
    """
    Parse a Kubernetes memory quantity (binary Ki/Mi/Gi/Ti, decimal
    K/M/G/T, or bare bytes) into MiB.

    Previously, any suffix not in {Ki, Mi, Gi, ""} silently fell back to
    a multiplier of 1 (i.e. was treated as if it were already MiB) via
    `multipliers.get(unit, 1)`. A value like "500000Ki" would then be
    read as 500000 MiB instead of ~488 MiB — wrong by three orders of
    magnitude, with no error raised. Unknown suffixes now raise
    UnparsableQuantity instead of guessing.
    """
    value = value.strip()
    match = re.match(r"^([0-9.]+)([A-Za-z]*)$", value)
    if not match:
        raise UnparsableQuantity(f"Cannot parse memory quantity: {value!r}")
    num_str, unit = match.group(1), match.group(2)
    if unit not in _MEMORY_MULTIPLIERS_TO_MIB:
        raise UnparsableQuantity(f"Unsupported memory unit {unit!r} in {value!r}")
    return float(num_str) * _MEMORY_MULTIPLIERS_TO_MIB[unit]


def compute_namespace_waste(namespace: str) -> dict:
    """
    Compare requested vs. actual resource usage for a namespace.

    A container can have a resource *request* with no matching entry in
    `usage` — e.g. the Metrics API hasn't scraped it yet (container just
    started), or the metrics-server had a transient failure for that pod.
    Previously that case was indistinguishable from "measured usage of
    zero": `u = usage_by_key.get(key)` returned None, the `if u:` guard
    skipped adding anything to total_cpu_used/total_mem_used, and the
    container's full request was counted as 100% waste for that resource
    — exactly what a genuinely idle container would also produce. A
    namespace where the metrics-server simply hadn't reported yet for 3 of
    4 pods would be flagged as extreme waste, when the truth is "we don't
    know yet."

    This version counts missing samples explicitly via `missing_samples`
    and reports `is_complete: False` on the namespace when any occurred,
    so the caller (API/dashboard) can distinguish "measured and genuinely
    idle" from "incomplete scan, don't trust this number."
    """
    requests = get_pod_requests(namespace)
    usage = get_pod_usage(namespace)
    usage_by_key = {(u["pod"], u["container"]): u for u in usage}

    total_cpu_requested = total_cpu_used = 0.0
    total_mem_requested = total_mem_used = 0.0
    missing_samples = 0
    unparsable_quantities = 0

    for r in requests:
        key = (r["pod"], r["container"])
        u = usage_by_key.get(key)

        try:
            total_cpu_requested += _cpu_to_millicores(r["cpu_request"])
        except UnparsableQuantity:
            unparsable_quantities += 1
        try:
            total_mem_requested += _mem_to_mib(r["memory_request"])
        except UnparsableQuantity:
            unparsable_quantities += 1

        if u is None:
            missing_samples += 1
            continue  # don't count this container's request as "waste" below

        try:
            total_cpu_used += _cpu_to_millicores(u["cpu_usage"])
        except UnparsableQuantity:
            unparsable_quantities += 1
        try:
            total_mem_used += _mem_to_mib(u["memory_usage"])
        except UnparsableQuantity:
            unparsable_quantities += 1

    cpu_waste_pct = (
        round(100 * (1 - total_cpu_used / total_cpu_requested), 1)
        if total_cpu_requested > 0
        else 0.0
    )
    mem_waste_pct = (
        round(100 * (1 - total_mem_used / total_mem_requested), 1)
        if total_mem_requested > 0
        else 0.0
    )

    return {
        "namespace": namespace,
        "cpu_requested_millicores": total_cpu_requested,
        "cpu_used_millicores": total_cpu_used,
        "cpu_waste_pct": max(cpu_waste_pct, 0.0),
        "memory_requested_mib": round(total_mem_requested, 1),
        "memory_used_mib": round(total_mem_used, 1),
        "memory_waste_pct": max(mem_waste_pct, 0.0),
        "pod_count": len({r["pod"] for r in requests}),
        "missing_samples": missing_samples,
        "unparsable_quantities": unparsable_quantities,
        "is_complete": missing_samples == 0 and unparsable_quantities == 0,
    }


def run_scan() -> dict:
    global _latest_results
    namespaces = get_namespaces()
    # Skip system namespaces — not relevant to team-level cost governance.
    skip = {"kube-system", "kube-public", "kube-node-lease"}
    results = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "namespaces": [
            compute_namespace_waste(ns) for ns in namespaces if ns not in skip
        ],
    }
    _latest_results = results
    if os.getenv("DATABASE_URL"):
        _persist_to_db(results)
    return results


def get_latest_results() -> dict | None:
    """
    Return the most recent scan. Checks the database first when DATABASE_URL
    is configured so that scans executed by external processes (e.g. the nightly
    CronJob or manual CLI scans) are immediately reflected without serving
    stale in-memory data. If DATABASE_URL is not set or returns nothing,
    falls back to the in-memory cache.
    """
    global _latest_results
    if os.getenv("DATABASE_URL"):
        db_results = _load_latest_from_db()
        if db_results is not None:
            _latest_results = db_results
            return db_results
    return _latest_results


def _persist_to_db(results: dict) -> None:
    """Write scan results to Postgres, one row per scan with the full
    namespace payload as JSONB, so history is queryable later without a
    schema migration for each new field we compute."""
    conn = get_connection()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO scan_results (scanned_at, payload) VALUES (%s, %s)",
                (results["scanned_at"], json.dumps(results)),
            )
        conn.commit()
    finally:
        conn.close()


def _load_latest_from_db() -> dict | None:
    conn = get_connection()
    try:
        init_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM scan_results ORDER BY scanned_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    out = run_scan()
    print(f"Scanned {len(out['namespaces'])} namespaces at {out['scanned_at']}")
