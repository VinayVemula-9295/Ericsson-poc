"""Evidence-based POC placement. Unknown mandatory evidence fails closed."""
from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any

POLICY_VERSION = "poc-placement-v1"

def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Evidence timestamps require a timezone")
    return parsed

def finite(value: Any, name: str, minimum: float = 0) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"Invalid {name}")
    return result

def validate_latency(records: list[dict], now: datetime | None = None) -> list[dict]:
    if not isinstance(records, list) or len(records) > 1000:
        raise ValueError("Latency upload must be a JSON list of at most 1000 records")
    now = now or datetime.now(timezone.utc)
    result = []
    for raw in records:
        row = dict(raw)
        for key in ("origin", "provider", "region", "metric", "method", "window", "measured_at"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"Latency record requires {key}")
        row["value_ms"] = finite(row.get("value_ms"), "latency")
        age = (now - timestamp(row["measured_at"])).total_seconds()
        row["fresh"] = 0 <= age <= 900
        result.append(row)
    return result

def explicit_cluster(node: dict, count: int) -> dict:
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1000:
        raise ValueError("Worker count must be between 1 and 1000")
    price = node.get("monthly_compute_estimate")
    return {"node_count": count, "worker_monthly_usd": price * count if price is not None else None,
            "pods_per_node": None, "version_compatibility": "unknown", "os": "Linux",
            "assumptions": ["Explicit worker count; hardware requirements apply per worker",
                            "Pod scheduling and IP capacity require validation"]}


def size_cluster(node: dict, pod_cpu: float, pod_memory: float, replicas: int,
                 tolerate_node_failure: bool = True, overhead: float = .2) -> dict:
    cpu = finite(pod_cpu, "pod CPU", .001)
    memory = finite(pod_memory, "pod memory", .001)
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 1:
        raise ValueError("Replicas must be a positive integer")
    if not 0 <= overhead < 1:
        raise ValueError("System overhead must be between zero and one")
    slots = min(math.floor(finite(node["vcpus"], "node CPU", .001) * (1-overhead)/cpu),
                math.floor(finite(node["memory_gib"], "node memory", .001) * (1-overhead)/memory))
    if slots < 1:
        raise ValueError("A pod cannot fit on this node after system overhead")
    count = math.ceil(replicas / slots) + int(tolerate_node_failure)
    price = node.get("monthly_compute_estimate")
    return {"node_count": count, "pods_per_node": slots, "replicas": replicas,
            "system_overhead": overhead, "worker_monthly_usd": price * count if price is not None else None,
            "control_plane_monthly_usd": None, "storage_monthly_usd": None,
            "network_monthly_usd": None, "total_monthly_usd": None,
            "version_compatibility": "unknown", "os": "Linux",
            "assumptions": ["Identical pod requests; scheduling constraints and provider pod/IP limits need validation",
                            "One spare node" if tolerate_node_failure else "No spare node"]}

def evaluate(candidate: dict, request: dict, latency: list[dict] = (), now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    # Only the current validated upload can supply a ranked measurement.
    candidate.pop("measured_latency_ms", None)
    candidate.pop("latency_ms", None)
    checks = []
    def check(name, state, detail):
        checks.append({"control": name, "status": state, "detail": detail})
    for field in ("residency_regions", "processing_regions"):
        allowed = request.get(field, [])
        actual = candidate.get("region") if field == "residency_regions" else request.get("ai_processing_region")
        check(field, "pass" if not allowed or actual in allowed else "unknown" if not actual else "fail", str(actual or "unverified"))
    check("os", "pass" if candidate.get("os") == request.get("os", "Linux") else "fail", str(candidate.get("os")))
    target = request.get("latency_target_ms")
    matching = [r for r in validate_latency(list(latency), now) if r["fresh"] and
                r["provider"] == candidate.get("provider") and r["region"] == candidate.get("region") and
                r["origin"] == request.get("latency_origin") and r["metric"] == request.get("latency_metric", "p95") and
                r["method"] == request.get("latency_method", "https") and r["window"] == request.get("latency_window", "5m")]
    if matching:
        measured = max(matching, key=lambda r: timestamp(r["measured_at"]))
        candidate["measured_latency_ms"] = measured["value_ms"]
    if target is not None:
        check("latency", "unknown" if not matching else "pass" if measured["value_ms"] <= target else "fail", "Comparable fresh probe required")
    for name in ("encryption_verified", "private_network_verified"):
        if request.get(name):
            value = candidate.get(name)
            check(name, "unknown" if value is None else "pass" if value else "fail", "Provider configuration evidence required")
    if request.get("require_quota"):
        headroom = candidate.get("quota_headroom_vcpus")
        check("quota", "unknown" if headroom is None else "pass" if headroom >= candidate.get("vcpus", float("inf")) else "fail", "Quota is not spare capacity")
    state = "fail" if any(c["status"] == "fail" for c in checks) else "unknown" if any(c["status"] == "unknown" for c in checks) else "pass"
    return {"policy_version": POLICY_VERSION, "status": state, "checks": checks}

def apply_constraints(options: list[dict], request: dict, latency: list[dict] = ()) -> list[dict]:
    result = []
    for raw in options:
        row = dict(raw)
        row["policy_result"] = evaluate(row, request, latency)
        if row["policy_result"]["status"] != "pass":
            row.update(available=False, error="Mandatory policy failed or lacks verified evidence")
        result.append(row)
    return result
