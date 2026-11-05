"""Deterministic exact/nearest VM profile matching for IIPP.

This module classifies hardware fit only. Provider/account availability and launch
capacity must continue to come from each provider adapter or an actual launch check.
"""
from __future__ import annotations

from typing import Any


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def annotate_candidate(option: dict[str, Any], min_vcpus: int, min_memory_gib: float) -> dict[str, Any]:
    """Return a copy with explicit match, shortfall, and evidence labels."""
    item = dict(option)
    cpu = _safe_number(item.get("vcpus"))
    memory = _safe_number(item.get("memory_gib"))
    requested_cpu = max(1, int(min_vcpus))
    requested_memory = max(0.5, float(min_memory_gib))
    provider = str(item.get("provider", "")).upper()

    item["requested_vcpus"] = requested_cpu
    item["requested_memory_gib"] = requested_memory
    item["cpu_shortfall"] = max(0.0, requested_cpu - (cpu or 0.0)) if cpu is not None else None
    item["memory_shortfall_gib"] = max(0.0, requested_memory - (memory or 0.0)) if memory is not None else None
    item["cpu_surplus"] = max(0.0, (cpu or 0.0) - requested_cpu) if cpu is not None else None
    item["memory_surplus_gib"] = max(0.0, (memory or 0.0) - requested_memory) if memory is not None else None

    if cpu is None or memory is None:
        item["hardware_match"] = "Unknown specifications"
        item["hardware_fit"] = "unknown"
        item["match_distance"] = float("inf")
    else:
        cpu_equal = int(cpu) == requested_cpu
        memory_equal = abs(memory - requested_memory) < 0.001
        meets_both = cpu >= requested_cpu and memory >= requested_memory
        if cpu_equal and memory_equal:
            item["hardware_match"] = "Exact CPU/RAM match"
            item["hardware_fit"] = "exact"
        elif meets_both:
            extras = []
            if cpu > requested_cpu:
                extras.append(f"{int(cpu - requested_cpu)} extra vCPU")
            if memory > requested_memory:
                extras.append(f"{memory - requested_memory:g} GiB extra RAM")
            item["hardware_match"] = "Meets request; " + " and ".join(extras)
            item["hardware_fit"] = "meets"
        else:
            deficits = []
            if cpu < requested_cpu:
                deficits.append(f"{int(requested_cpu - cpu)} fewer vCPU")
            if memory < requested_memory:
                deficits.append(f"{requested_memory - memory:g} GiB less RAM")
            item["hardware_match"] = "Below request: " + " and ".join(deficits)
            item["hardware_fit"] = "below"
        # Normalized distance keeps CPU and RAM dimensions comparable. A deficit
        # is weighted more heavily than an excess, since it misses a hard requirement.
        cpu_delta = abs(cpu - requested_cpu) / requested_cpu
        memory_delta = abs(memory - requested_memory) / requested_memory
        deficit = (max(0.0, requested_cpu - cpu) / requested_cpu
                   + max(0.0, requested_memory - memory) / requested_memory)
        excess = (max(0.0, cpu - requested_cpu) / requested_cpu
                  + max(0.0, memory - requested_memory) / requested_memory)
        item["match_distance"] = round((2.0 * deficit) + excess + 0.05 * (cpu_delta + memory_delta), 6)

    if provider == "AWS":
        offered = item.get("offered_in_target_location")
        if offered is True:
            item["availability_evidence"] = "EC2 regional offering verified; account quota and spare capacity not verified"
        elif offered is False:
            item["availability_evidence"] = "EC2 reports this type is not offered in the selected region"
        else:
            item["availability_evidence"] = "EC2 regional offering could not be verified (check API/IAM error)"
        item["selectable_for_aws_plan"] = bool(offered is True and item.get("hardware_fit") in {"exact", "meets"})
    elif provider == "AZURE":
        item["availability_evidence"] = "Public retail catalogue only; Azure subscription, quota, zone and live capacity are not connected"
        item["selectable_for_aws_plan"] = False
    elif provider == "GCP":
        item["availability_evidence"] = "Public billing catalogue only; GCP project, quota, zone and live capacity are not connected"
        item["selectable_for_aws_plan"] = False
    else:
        item["availability_evidence"] = "Provider availability evidence unavailable"
        item["selectable_for_aws_plan"] = False
    return item


def rank_candidate_options(options: list[dict[str, Any]], min_vcpus: int,
                           min_memory_gib: float) -> list[dict[str, Any]]:
    """Rank exact, safe over-provisioned, then undersized candidate profiles."""
    annotated = [annotate_candidate(x, min_vcpus, min_memory_gib) for x in options]

    def key(item: dict[str, Any]) -> tuple[Any, ...]:
        fit = item.get("hardware_fit")
        fit_rank = {"exact": 0, "meets": 1, "below": 2, "unknown": 3}.get(fit, 4)
        # Exact hardware is preferred. Within the same safe tier, optimize cost
        # first; proximity is a tie-breaker, not a reason to select an expensive SKU.
        price = item.get("monthly_compute_estimate")
        try:
            price_key = float(price) if price is not None and item.get("available") is True else float("inf")
        except (TypeError, ValueError):
            price_key = float("inf")
        availability_rank = 0 if item.get("offered_in_target_location") is True else 1
        return (fit_rank, price_key, availability_rank, item.get("match_distance", float("inf")),
                str(item.get("provider", "")), str(item.get("sku", "")))

    return sorted(annotated, key=key)


def exact_match_summary(options: list[dict[str, Any]], min_vcpus: int,
                        min_memory_gib: float) -> dict[str, Any]:
    """Summarize exact profiles without overstating public catalogue data as account inventory."""
    ranked = rank_candidate_options(options, min_vcpus, min_memory_gib)
    result: dict[str, Any] = {
        "aws_verified_exact": [],
        "aws_unverified_exact": [],
        "azure_catalog_exact": [],
        "gcp_catalog_exact": [],
    }
    for item in ranked:
        if item.get("hardware_fit") != "exact":
            continue
        provider = str(item.get("provider", "")).upper()
        if provider == "AWS":
            key = "aws_verified_exact" if item.get("offered_in_target_location") is True else "aws_unverified_exact"
        elif provider == "AZURE":
            key = "azure_catalog_exact"
        elif provider == "GCP":
            key = "gcp_catalog_exact"
        else:
            continue
        result[key].append(item)
    return result
