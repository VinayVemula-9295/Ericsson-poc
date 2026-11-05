"""Live AWS account discovery plus Azure/GCP POC public-price comparisons."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from cloud_adapters.aws import discover_aws_vm_options
from cloud_adapters.azure import get_azure_vm_price
from cloud_adapters.gcp import get_gcp_vm_price
from cloud_adapters.normalization import normalize_cloud_option
from recommendation_engine import compare_options
from provider_connections import get_aws_session

# These two shortlists are intentionally retained only for the POC's unconnected
# Azure/GCP public-price comparisons. AWS has no hardcoded VM name/specification list.
AZURE_POC_PROFILES = [
    ("Standard_D2s_v5", 2, 8.0),
    ("Standard_F4s_v2", 4, 8.0),
    ("Standard_D4s_v5", 4, 16.0),
    ("Standard_D8s_v5", 8, 32.0),
    ("Standard_D16s_v5", 16, 64.0),
    ("Standard_D32s_v5", 32, 128.0),
]
GCP_POC_PROFILES = [
    ("n2-standard-2", 2, 8.0),
    ("n2-standard-4", 4, 16.0),
    ("n2-standard-8", 8, 32.0),
    ("n2-standard-16", 16, 64.0),
    ("n2-standard-32", 32, 128.0),
]


def _profile_distance(profile: tuple[str, int, float], min_vcpus: int,
                      min_memory_gib: float) -> tuple[float, float, str]:
    """Distance key for the intentionally curated Azure/GCP POC reference profiles."""
    sku, cpu, memory = profile
    cpu_deficit = max(0.0, min_vcpus - cpu) / max(min_vcpus, 1)
    mem_deficit = max(0.0, min_memory_gib - memory) / max(min_memory_gib, 0.5)
    cpu_extra = max(0.0, cpu - min_vcpus) / max(min_vcpus, 1)
    mem_extra = max(0.0, memory - min_memory_gib) / max(min_memory_gib, 0.5)
    deficit = cpu_deficit + mem_deficit
    excess = cpu_extra + mem_extra
    category = 0.0 if deficit == 0 and excess == 0 else 1.0 if deficit == 0 else 2.0
    return category, 2.0 * deficit + excess, sku


def _select_poc_profiles(profiles: list[tuple[str, int, float]], min_vcpus: int,
                         min_memory_gib: float, limit: int = 4) -> list[tuple[str, int, float]]:
    ordered = sorted(profiles, key=lambda profile: _profile_distance(profile, min_vcpus, min_memory_gib))
    return ordered[:limit]


def _fetch_poc_provider(provider: str, profiles: list[tuple[str, int, float]], region: str,
                        getter: Any, min_vcpus: int, min_memory_gib: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = _select_poc_profiles(profiles, min_vcpus, min_memory_gib)
    for sku, cpu, memory in candidates:
        try:
            row = getter(region, sku)
            row.setdefault("vcpus", cpu)
            row.setdefault("memory_gib", memory)
            row.setdefault("spec_source", f"Curated {provider} POC profile mapping (not live account inventory)")
            row.setdefault("poc_profile_mapping", True)
            rows.append(row)
        except Exception as exc:
            rows.append({
                "available": False, "provider": provider, "region": region, "sku": sku,
                "vcpus": cpu, "memory_gib": memory,
                "spec_source": f"Curated {provider} POC profile mapping (not live account inventory)",
                "poc_profile_mapping": True,
                "error": f"{type(exc).__name__}: {exc}",
                "telemetry_status": f"{provider} account not connected",
                "capacity_status": "not verified", "provisioning_enabled": False,
            })
    return rows


def get_provider_options(aws_region: str = "ap-south-1", azure_region: str = "centralindia",
                         gcp_region: str = "asia-south1", min_vcpus: int = 2,
                         min_memory_gib: float = 8.0, aws_connection_id: str | None = None) -> list[dict[str, Any]]:
    """Discover AWS types live; retain only documented POC profile maps for Azure/GCP.

    AWS candidates/specifications are sourced from the authenticated EC2 API context.
    Azure/GCP remain unconnected public-price comparisons using explicit POC mappings.
    """
    rows: list[dict[str, Any]] = []
    try:
        if aws_connection_id:
            aws_session = get_aws_session(aws_connection_id, purpose="inventory")
            rows.extend(discover_aws_vm_options(aws_region, min_vcpus, min_memory_gib, session=aws_session))
        else:
            rows.extend(discover_aws_vm_options(aws_region, min_vcpus, min_memory_gib))
    except Exception as exc:
        rows.append({
            "available": False, "catalog_available": False, "provider": "AWS", "region": aws_region,
            "sku": "aws-live-discovery-failed", "vcpus": None, "memory_gib": None,
            "offered_in_target_location": None, "spec_source": "No live AWS specification fallback used",
            "telemetry_status": "AWS live discovery failed", "capacity_status": "not verified",
            "error": f"AWS live discovery failed: {type(exc).__name__}: {exc}. No hardcoded AWS profiles were used.",
        })
    rows.extend(_fetch_poc_provider("Azure", AZURE_POC_PROFILES, azure_region,
                                    get_azure_vm_price, min_vcpus, min_memory_gib))
    rows.extend(_fetch_poc_provider("GCP", GCP_POC_PROFILES, gcp_region,
                                    get_gcp_vm_price, min_vcpus, min_memory_gib))
    return [normalize_cloud_option(row) for row in rows]


def run_comparison(aws_region: str = "ap-south-1", azure_region: str = "centralindia",
                   gcp_region: str = "asia-south1", min_vcpus: int = 2, min_memory_gib: float = 8.0,
                   max_monthly_budget: float | None = None, strategy: str = "Balanced",
                   aws_connection_id: str | None = None) -> dict[str, Any]:
    options = get_provider_options(aws_region, azure_region, gcp_region, min_vcpus, min_memory_gib, aws_connection_id)
    result = compare_options(options, min_vcpus, min_memory_gib, max_monthly_budget, strategy)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["source_options"] = options
    return result
