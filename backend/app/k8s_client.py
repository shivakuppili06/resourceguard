"""
Thin wrapper around the official `kubernetes` Python client.

Uses in-cluster config when running inside a Pod (ServiceAccount token
auto-mounted); falls back to local kubeconfig for `uvicorn app.main:app`
during local development against kind/Minikube.
"""
from kubernetes import client, config

_configured = False
_core_v1 = None
_custom_objects = None


def _ensure_configured():
    """
    Load kube config lazily, on first real use, rather than at module
    import time. Loading at import time meant importing this module at
    all — including just to run unit tests on the pure-Python parsing
    functions in scan.py, or to run `pytest` in CI with no cluster
    reachable — raised ConfigException and crashed before a single test
    could execute.
    """
    global _configured, _core_v1, _custom_objects
    if _configured:
        return
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    _core_v1 = client.CoreV1Api()
    _custom_objects = client.CustomObjectsApi()
    _configured = True


def _core() -> client.CoreV1Api:
    _ensure_configured()
    return _core_v1


def _custom() -> client.CustomObjectsApi:
    _ensure_configured()
    return _custom_objects


def get_namespaces() -> list[str]:
    ns_list = _core().list_namespace()
    return [ns.metadata.name for ns in ns_list.items]


def get_pod_requests(namespace: str) -> list[dict]:
    """Requested CPU/memory per pod/container in a namespace."""
    pods = _core().list_namespaced_pod(namespace)
    out = []
    for pod in pods.items:
        for c in pod.spec.containers:
            requests = (c.resources.requests or {}) if c.resources else {}
            out.append(
                {
                    "pod": pod.metadata.name,
                    "container": c.name,
                    "cpu_request": requests.get("cpu", "0"),
                    "memory_request": requests.get("memory", "0"),
                }
            )
    return out


def get_pod_usage(namespace: str) -> list[dict]:
    """
    Actual CPU/memory usage per pod via the Metrics API
    (metrics.k8s.io/v1beta1 — requires metrics-server installed).
    """
    data = _custom().list_namespaced_custom_object(
        group="metrics.k8s.io",
        version="v1beta1",
        namespace=namespace,
        plural="pods",
    )
    out = []
    for item in data.get("items", []):
        pod_name = item["metadata"]["name"]
        for c in item.get("containers", []):
            out.append(
                {
                    "pod": pod_name,
                    "container": c["name"],
                    "cpu_usage": c["usage"].get("cpu", "0"),
                    "memory_usage": c["usage"].get("memory", "0"),
                }
            )
    return out
