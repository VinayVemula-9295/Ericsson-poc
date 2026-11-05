"""Deterministic extraction of VM requirements from a natural-language IIPP prompt."""
from __future__ import annotations
import re
from typing import Any

_VCPU_PATTERNS = (
    # Accept standard spellings and common prompt typos such as "Vpcu".
    re.compile(r"\b(\d{1,3})\s*(?:v\s*-?\s*cpus?|v\s*-?\s*pcus?|virtual\s*cpus?|vpcs?)\b", re.I),
    re.compile(r"\b(?:cpu|cores?)\s*[:=]?\s*(\d{1,3})\b", re.I),
)
_MEMORY_PATTERNS = (
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:gib|gb|g)\s*(?:of\s*)?(?:ram|memory)\b", re.I),
    re.compile(r"\b(?:ram|memory)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:gib|gb|g)\b", re.I),
    # Natural requests such as "8 GiB server", "8GB VM", or "8 GB instance".
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:gib|gb)\s*(?:(?:linux|windows)\s+)?(?:server|instance|vm|machine|workload|node)\b", re.I),
    re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:gib|gb)\b(?!\s*(?:disk|storage|volume))", re.I),
)
_BUDGET_RE = re.compile(r"\b(?:budget|under|below|less than|max(?:imum)?)\s*(?:usd|\$)?\s*(\d+(?:\.\d+)?)\s*(?:usd|dollars?)?\b", re.I)


def parse_infrastructure_request(prompt: str) -> dict[str, Any]:
    """Parse common VM requests; always tell callers which values were inferred/defaulted."""
    text = (prompt or "").strip()
    vcpus = None
    for pattern in _VCPU_PATTERNS:
        match = pattern.search(text)
        if match:
            vcpus = int(match.group(1))
            break
    memory = None
    for pattern in _MEMORY_PATTERNS:
        match = pattern.search(text)
        if match:
            memory = float(match.group(1))
            break
    budget = None
    budget_match = _BUDGET_RE.search(text)
    if budget_match:
        try:
            budget = float(budget_match.group(1))
        except ValueError:
            budget = None
    os_name = "Windows" if re.search(r"\bwindows\b", text, re.I) else "Linux"
    lowered = text.lower()
    if any(term in lowered for term in ("lowest cost", "cheapest", "cost effective", "cost-effective")):
        strategy = "Lowest Cost"
    elif any(term in lowered for term in ("latency sensitive", "lowest latency", "low latency")):
        strategy = "Lowest Latency"
    elif any(term in lowered for term in ("best fit", "right size", "right-size")):
        strategy = "Best Fit for Workload"
    else:
        strategy = "Balanced"
    missing = []
    if vcpus is None:
        vcpus = 2
        missing.append("vCPU count (defaulted to 2)")
    if memory is None:
        memory = 8.0
        missing.append("RAM (defaulted to 8 GiB)")
    if not 1 <= vcpus <= 128:
        raise ValueError("Requested vCPU count must be between 1 and 128.")
    if not 0.5 <= memory <= 4096:
        raise ValueError("Requested RAM must be between 0.5 and 4096 GiB.")
    if budget is not None and budget <= 0:
        budget = None
    def workload_number(pattern, default, cast=float):
        found = re.search(pattern, text, re.I)
        return cast(found.group(1)) if found else default
    replicas = workload_number(r"\b(\d+)\s*(?:replicas?|pods?)\b", 3, int)
    workers = workload_number(r"\b(\d+)\s*(?:worker\s+nodes?|workers?)\b", None, int)
    if workers is not None and not 1 <= workers <= 1000:
        raise ValueError("Worker count must be between 1 and 1000.")
    pod_cpu = workload_number(r"\b(\d+(?:\.\d+)?)\s*(?:v?cpu|cores?)\s*(?:per|/)\s*pod\b", .5)
    pod_memory = workload_number(r"\b(\d+(?:\.\d+)?)\s*(?:gib|gb)\s*(?:ram\s*)?(?:per|/)\s*pod\b", 1.)
    if not 1 <= replicas <= 100000 or not .01 <= pod_cpu <= 128 or not .01 <= pod_memory <= 4096:
        raise ValueError("Pod sizing or replica count is outside the supported range.")
    return {
        "prompt": text, "min_vcpus": vcpus, "min_memory_gib": memory,
        "os": os_name, "max_monthly_budget_usd": budget, "strategy": strategy,
        "defaults_used": missing,
        "worker_count": workers,
        "replicas": replicas, "pod_cpu": pod_cpu, "pod_memory_gib": pod_memory,
        "tolerate_node_failure": not bool(re.search(r"\b(?:no spare|without spare)\b", text, re.I)),
        "private_network_verified": bool(re.search(r"\brequire\s+(?:verified\s+)?private\s+network", text, re.I)),
        "encryption_verified": bool(re.search(r"\brequire\s+(?:verified\s+)?encryption", text, re.I)),
        "require_quota": bool(re.search(r"\brequire\s+(?:verified\s+)?quota", text, re.I)),
        "kind": "Kubernetes" if workers is not None or re.search(r"\b(kubernetes|eks|aks|gke|k8s)\b", text, re.I) else "VM",
        "latency_target_ms": float(re.search(r"(\d+(?:\.\d+)?)\s*ms\b", text, re.I).group(1)) if re.search(r"(\d+(?:\.\d+)?)\s*ms\b", text, re.I) else None,
        "data_classification": "restricted" if re.search(r"\b(restricted|sovereign|sovereignty)\b", text, re.I) else "confidential" if re.search(r"\b(private|privacy|confidential|sensitive)\b", text, re.I) else "internal",
        "residency_regions": re.findall(r"\b(?:ap|us|eu|sa|ca|af|me)-(?:south|north|east|west|central|southeast|northeast)-\d\b|\basia-south\d\b|\bcentralindia\b", lowered),
        "requires_residency_confirmation": bool(re.search(r"\b(residen\w*|sovereign\w*|only in|must stay)\b", text, re.I)),
    }
