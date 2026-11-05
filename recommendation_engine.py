"""Deterministic multi-cloud eligibility and recommendation logic.

Hard CPU/RAM requirements and provider evidence are evaluated before ranking.
Prices are compute-only estimates. Latency is scored only when measured values are
provided by a connected workload probe; region names are never treated as latency.
"""
from __future__ import annotations

from typing import Any

STRATEGIES = {"Lowest Cost", "Lowest Latency", "Balanced", "Best Fit for Workload"}


def progressive_choices(options, min_vcpus, min_memory_gib, budget=None, strategy="Balanced"):
    """Closest fit, then two successive CPU/RAM upgrades from eligible evidence."""
    eligible = compare_options(options, min_vcpus, min_memory_gib, budget, strategy)["eligible_options"]
    choices, previous = [], None
    for tier in ("Selectable", "Recommended", "Optimal"):
        pool = eligible if previous is None and tier == "Selectable" else [
            row for row in eligible if previous is not None
            and row["vcpus"] >= previous["vcpus"] and row["memory_gib"] >= previous["memory_gib"]
            and (row["vcpus"] > previous["vcpus"] or row["memory_gib"] > previous["memory_gib"])
        ]
        base_cpu = previous["vcpus"] if previous else min_vcpus
        base_memory = previous["memory_gib"] if previous else min_memory_gib
        row = min(pool, key=lambda r: (
            (r["vcpus"] - base_cpu) / base_cpu + (r["memory_gib"] - base_memory) / base_memory,
            r["monthly_compute_estimate"], str(r["sku"]))) if pool else None
        choices.append((tier, row))
        previous = row
    return choices


def shortlist_with_upgrades(rows, cpu, memory, limit, specs=lambda row: (row["vcpus"], row["memory_gib"])):
    """Retain every exact profile and reserve room for two hardware upgrades.

    Input order supplies provider-specific tie breakers. Multiple alternatives at
    each upgrade reduce the chance that an unavailable price hides the whole tier.
    """
    exact = [r for r in rows if specs(r) == (cpu, memory)]
    selected = list(exact)
    previous = next((r for r in rows if specs(r)[0] >= cpu and specs(r)[1] >= memory), None)
    if previous is not None:
        if previous not in selected:
            selected.append(previous)
        for _ in range(2):
            bc, bm = specs(previous)
            pool = [r for r in rows if specs(r)[0] >= bc and specs(r)[1] >= bm and specs(r) != (bc, bm)]
            pool.sort(key=lambda r: (specs(r)[0] / bc + specs(r)[1] / bm))
            if not pool:
                break
            previous = pool[0]
            profile = specs(previous)
            alternatives = [r for r in pool if specs(r) == profile][:3]
            selected.extend(r for r in alternatives if r not in selected)
    for row in rows:
        if len(selected) >= max(limit, len(exact)):
            break
        if row not in selected:
            selected.append(row)
    return selected


def _num(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 and result < float("inf") else None


def _hardware_tier(item: dict[str, Any], min_vcpus: int, min_memory_gib: float) -> int:
    """0 exact, 1 meets/exceeds, 2 below/unknown."""
    cpu, ram = _num(item.get("vcpus")), _num(item.get("memory_gib"))
    if cpu is None or ram is None:
        return 2
    if int(cpu) == min_vcpus and abs(ram - min_memory_gib) < 0.001:
        return 0
    if cpu >= min_vcpus and ram >= min_memory_gib:
        return 1
    return 2


def _availability_score(item: dict[str, Any]) -> tuple[float, str]:
    if item.get("live_capacity_verified") is True:
        return 100.0, "Live capacity verified"
    if str(item.get("provider", "")).upper() == "AWS":
        if item.get("offered_in_target_location") is True:
            return 80.0, "EC2 regional offering verified; account quota and spare capacity not verified"
        return 0.0, "AWS regional offering not verified"
    if item.get("account_connected") is True and item.get("subscription_inventory_verified") is True:
        return 70.0, "Account inventory verified; launch capacity not verified"
    return 30.0, "Public price catalogue only; account/zone capacity not verified"


def compare_options(options: list[dict[str, Any]], min_vcpus: int = 2, min_memory_gib: float = 8.0,
                    max_monthly_budget: float | None = None, strategy: str = "Balanced") -> dict[str, Any]:
    """Filter hard requirements, then score exact/feasible candidates with known evidence.

    Recommendation policy:
      * Exact hardware matches form the preferred tier whenever any are eligible.
      * Otherwise choose among candidates meeting both minimum CPU and RAM.
      * Lowest Cost picks the cheapest candidate in that tier.
      * Balanced weights cost 65%, hardware fit 20%, and availability evidence 15%.
      * Latency is included only where a measured latency_ms is supplied.
      * Without measured latency, a Lowest Latency request has no latency winner;
        the independently calculated cost-optimal choice is still returned.
    """
    if not isinstance(min_vcpus, int) or isinstance(min_vcpus, bool) or min_vcpus < 1:
        raise ValueError("min_vcpus must be an integer of at least 1.")
    if not isinstance(min_memory_gib, (int, float)) or isinstance(min_memory_gib, bool) or min_memory_gib <= 0:
        raise ValueError("min_memory_gib must be greater than zero.")
    if max_monthly_budget is not None and (not isinstance(max_monthly_budget, (int, float)) or isinstance(max_monthly_budget, bool) or max_monthly_budget <= 0):
        raise ValueError("max_monthly_budget must be a positive amount or None.")
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown comparison strategy. Choose one of: {', '.join(sorted(STRATEGIES))}.")

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for raw in options:
        item = dict(raw)
        provider = str(item.get("provider", "")).upper()
        reasons: list[str] = []
        if not item.get("available"):
            reasons.append(item.get("error") or "Public compute price is unavailable")
        if _hardware_tier(item, min_vcpus, float(min_memory_gib)) == 2:
            cpu, ram = item.get("vcpus"), item.get("memory_gib")
            if cpu is None or ram is None:
                reasons.append("CPU/RAM specifications are missing or unverified")
            else:
                if float(cpu) < min_vcpus:
                    reasons.append(f"Below minimum vCPU requirement ({min_vcpus})")
                if float(ram) < float(min_memory_gib):
                    reasons.append(f"Below minimum RAM requirement ({min_memory_gib:g} GiB)")
        if provider == "AWS" and "offered_in_target_location" in item and item.get("offered_in_target_location") is not True:
            reasons.append("AWS EC2 regional offering is not verified")
        monthly = _num(item.get("monthly_compute_estimate"))
        if monthly is None:
            reasons.append("Comparable monthly compute price is unavailable")
        elif max_monthly_budget is not None and monthly > float(max_monthly_budget):
            reasons.append(f"Compute estimate exceeds budget of {max_monthly_budget:g} {item.get('price_currency', 'USD')}/month")
        if item.get("price_currency", "USD") != "USD":
            reasons.append("Price currency is not USD; cross-currency ranking requires a verified FX rate")
        if reasons:
            item["eligibility"] = "ineligible / not scoreable"
            item["eligibility_reason"] = "; ".join(str(x) for x in reasons)
            item["comparison_selectable"] = False
            excluded.append(item)
        else:
            item["hardware_tier"] = _hardware_tier(item, min_vcpus, float(min_memory_gib))
            item["availability_score"], item["availability_summary"] = _availability_score(item)
            item["eligibility"] = "eligible for price/spec comparison"
            item["eligibility_reason"] = (
                "Exact CPU/RAM match with a usable USD compute price" if item["hardware_tier"] == 0
                else "Meets minimum CPU/RAM with a usable USD compute price"
            )
            item["comparison_selectable"] = True
            item["provisioning_selectable"] = bool(
                provider == "AWS" and item.get("offered_in_target_location") is True
                and item["hardware_tier"] in {0, 1}
            )
            eligible.append(item)

    # Scores are normalized over the current eligible comparison set only.
    prices = [float(x["monthly_compute_estimate"]) for x in eligible]
    min_price, max_price = (min(prices), max(prices)) if prices else (None, None)
    latency_values = [_num(x.get("measured_latency_ms", x.get("latency_ms"))) for x in eligible]
    measured_latency = [x for x in latency_values if x is not None]
    min_latency, max_latency = (min(measured_latency), max(measured_latency)) if measured_latency else (None, None)

    for item in eligible:
        price = float(item["monthly_compute_estimate"])
        cost_score = 100.0 if max_price == min_price else 100.0 * (max_price - price) / (max_price - min_price)
        cpu = float(item["vcpus"])
        ram = float(item["memory_gib"])
        cpu_excess = max(0.0, cpu - min_vcpus) / max(1, min_vcpus)
        ram_excess = max(0.0, ram - float(min_memory_gib)) / max(float(min_memory_gib), 0.5)
        fit_score = max(0.0, 100.0 - 25.0 * cpu_excess - 20.0 * ram_excess)
        item["cost_score"] = round(cost_score, 2)
        item["workload_fit_score"] = round(fit_score, 2)
        if min_latency is not None:
            latency = _num(item.get("measured_latency_ms", item.get("latency_ms")))
            latency_score = None if latency is None else (100.0 if max_latency == min_latency else 100.0 * (max_latency - latency) / (max_latency - min_latency))
        else:
            latency_score = None
        item["latency_score"] = round(latency_score, 2) if latency_score is not None else None
        item["score"] = None
        if strategy == "Balanced":
            if latency_score is None:
                score = 0.65 * cost_score + 0.20 * fit_score + 0.15 * item["availability_score"]
            else:
                score = 0.50 * cost_score + 0.20 * fit_score + 0.15 * item["availability_score"] + 0.15 * latency_score
            item["score"] = round(score, 2)
        elif strategy == "Best Fit for Workload":
            item["score"] = round(0.75 * fit_score + 0.25 * cost_score, 2)
        elif strategy == "Lowest Cost":
            item["score"] = round(cost_score, 2)
        elif strategy == "Lowest Latency" and latency_score is not None:
            item["score"] = round(latency_score, 2)
        item["recommendation_note"] = (
            "Measured latency included in score." if latency_score is not None
            else "No comparable measured end-to-end latency; latency is not scored."
        )

    # Hardware fit is the first ordering key. Cost/strategy only chooses within
    # exact or meets-minimum groups; an undersized SKU can never win.
    exact_pool = [x for x in eligible if x["hardware_tier"] == 0]
    meets_pool = [x for x in eligible if x["hardware_tier"] == 1]
    preferred_pool = exact_pool or meets_pool

    def _cost_key(x: dict[str, Any]) -> tuple[float, float, str, str]:
        return (float(x["monthly_compute_estimate"]), -float(x["availability_score"]),
                str(x.get("provider", "")), str(x.get("sku", "")))

    optimal = min(preferred_pool, key=_cost_key) if preferred_pool else None
    recommendation = None
    reason = "No candidate meets the requested CPU/RAM with a valid USD price and required regional evidence."
    if preferred_pool:
        if strategy == "Lowest Latency":
            if measured_latency:
                latency_pool = [x for x in preferred_pool if _num(x.get("measured_latency_ms", x.get("latency_ms"))) is not None]
                if latency_pool:
                    recommendation = min(latency_pool, key=lambda x: (
                        float(x.get("measured_latency_ms", x.get("latency_ms"))),
                        float(x["monthly_compute_estimate"]), -float(x["availability_score"])))
                    reason = "Selected the lowest measured end-to-end latency within the best hardware-fit tier; price and availability are tie-breakers."
            if recommendation is None:
                reason = "No latency winner is claimed because comparable measured end-to-end latency is unavailable. The cost-optimal option is shown separately."
        elif strategy == "Lowest Cost":
            recommendation = optimal
            reason = "Selected the lowest compute-only price within the exact-match tier, or the meets-minimum tier when no exact match exists."
        elif strategy == "Best Fit for Workload":
            recommendation = max(preferred_pool, key=lambda x: (float(x["score"] or 0), -float(x["monthly_compute_estimate"])))
            reason = "Selected the strongest hardware-fit score within the exact-match tier, or the meets-minimum tier when no exact match exists."
        else:
            recommendation = max(preferred_pool, key=lambda x: (float(x["score"] or 0), -float(x["monthly_compute_estimate"])))
            reason = ("Balanced ranking uses cost 65%, CPU/RAM fit 20%, and availability evidence 15%; comparable latency is not measured."
                      if min_latency is None else
                      "Balanced ranking uses cost, CPU/RAM fit, account/region evidence, and measured latency.")

    return {
        "requirements": {"min_vcpus": min_vcpus, "min_memory_gib": float(min_memory_gib), "max_monthly_budget_usd": max_monthly_budget},
        "strategy": strategy,
        "eligible_options": eligible,
        "excluded_options": excluded,
        "recommended_provider": recommendation.get("provider") if recommendation else None,
        "recommended_sku": recommendation.get("sku") if recommendation else None,
        "optimal_provider": optimal.get("provider") if optimal else None,
        "optimal_sku": optimal.get("sku") if optimal else None,
        "optimal_monthly_compute_estimate": optimal.get("monthly_compute_estimate") if optimal else None,
        "latency_measured": bool(measured_latency),
        "recommendation_reason": reason,
        "generated_at": None,
        "notices": [
            "Prices are public retail/on-demand compute estimates, not account-specific bills.",
            "Monthly compute uses 730 hours; storage, network transfer, public IPs, taxes, discounts and support are excluded unless explicitly priced.",
            "A public price catalogue is not live subscription/project inventory. Azure/GCP provisioning remains disabled until their reviewed Terraform adapters are enabled.",
            "An AWS regional offering does not confirm account quota or immediate spare launch capacity.",
            "Latency is ranked only when comparable measured end-to-end latency evidence is available.",
        ],
    }
