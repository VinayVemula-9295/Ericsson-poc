"""Live AWS EC2 instance-type discovery for the IIPP Agent.

The Agent does not maintain an AWS SKU/CPU/RAM catalogue. It discovers instance
 types offered in the requested AWS region with the active AWS SDK credentials,
reads CPU/RAM/architecture from DescribeInstanceTypes, and only then looks up
public on-demand prices for a small, closest-fit shortlist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
import os

import boto3
from botocore.config import Config

from aws_data import get_ec2_instance_on_demand_price, _error_text


def _client(service: str, region: str, session: Any | None = None):
    """Use the selected account session or the EC2 instance-role credential chain."""
    creator = session.client if session is not None else boto3.client
    return creator(
        service, region_name=region,
        config=Config(connect_timeout=5, read_timeout=20,
                      retries={"max_attempts": 3, "mode": "standard"}),
    )


def _pages_for_offerings(ec2: Any, region: str) -> set[str]:
    """Return every EC2 instance type AWS reports as offered in this region."""
    offered: set[str] = set()
    token: str | None = None
    while True:
        request: dict[str, Any] = {
            "LocationType": "region",
            "Filters": [{"Name": "location", "Values": [region]}],
            "MaxResults": 1000,
        }
        if token:
            request["NextToken"] = token
        response = ec2.describe_instance_type_offerings(**request)
        offered.update(
            str(row.get("InstanceType"))
            for row in response.get("InstanceTypeOfferings", [])
            if row.get("InstanceType")
        )
        token = response.get("NextToken")
        if not token:
            break
    return offered


def _chunks(values: list[str], size: int = 100) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _read_live_specs(ec2: Any, offered_types: set[str]) -> list[dict[str, Any]]:
    """Fetch live EC2 hardware data in API-compliant batches (max 100 type names)."""
    specs: list[dict[str, Any]] = []
    for batch in _chunks(sorted(offered_types), 100):
        response = ec2.describe_instance_types(InstanceTypes=batch)
        specs.extend(response.get("InstanceTypes", []))
    return specs


def _usable_for_cloudpilot_linux(spec: dict[str, Any]) -> tuple[bool, str | None]:
    """Keep only instance types compatible with the current x86_64 Linux Terraform stack."""
    name = str(spec.get("InstanceType") or "unknown")
    architectures = spec.get("ProcessorInfo", {}).get("SupportedArchitectures") or []
    root_types = spec.get("SupportedRootDeviceTypes") or []
    virtualization = spec.get("SupportedVirtualizationTypes") or []
    usage_classes = spec.get("SupportedUsageClasses") or []
    if "x86_64" not in architectures:
        return False, f"{name} does not support x86_64, which is required by the current reviewed AMI workflow."
    if "ebs" not in root_types:
        return False, f"{name} does not support EBS root devices."
    if "hvm" not in virtualization:
        return False, f"{name} does not support HVM virtualization."
    if "on-demand" not in usage_classes:
        return False, f"{name} did not return verified On-Demand support in the live EC2 specification."
    if spec.get("BareMetal") is True:
        return False, f"{name} is a bare-metal type; the current POC is restricted to virtualized EC2 instances."
    cpu = spec.get("VCpuInfo", {}).get("DefaultVCpus")
    memory_mib = spec.get("MemoryInfo", {}).get("SizeInMiB")
    if not isinstance(cpu, (int, float)) or cpu <= 0:
        return False, f"{name} did not return valid CPU specifications from EC2."
    if not isinstance(memory_mib, (int, float)) or memory_mib <= 0:
        return False, f"{name} did not return valid memory specifications from EC2."
    return True, None


def _hardware_distance(vcpus: int, memory_gib: float, required_vcpus: int,
                      required_memory_gib: float, current_generation: bool) -> tuple[Any, ...]:
    """Rank exact, over-sized and under-sized real AWS types without guessed specs."""
    cpu_deficit = max(0.0, required_vcpus - vcpus) / max(required_vcpus, 1)
    memory_deficit = max(0.0, required_memory_gib - memory_gib) / max(required_memory_gib, 0.5)
    cpu_extra = max(0.0, vcpus - required_vcpus) / max(required_vcpus, 1)
    memory_extra = max(0.0, memory_gib - required_memory_gib) / max(required_memory_gib, 0.5)
    exact = vcpus == required_vcpus and abs(memory_gib - required_memory_gib) < 0.001
    meets_both = vcpus >= required_vcpus and memory_gib >= required_memory_gib
    fit_group = 0 if exact else 1 if meets_both else 2
    distance = 2.0 * (cpu_deficit + memory_deficit) + cpu_extra + memory_extra
    # Prefer current-generation SKUs only after hardware fit is considered.
    generation_penalty = 0 if current_generation else 1
    return fit_group, distance, generation_penalty, vcpus, memory_gib


def _shortlist(spec_rows: list[dict[str, Any]], required_vcpus: int,
               required_memory_gib: float, limit: int) -> list[dict[str, Any]]:
    """Keep the closest evidence-backed types; include under-sized diagnostics if needed."""
    enriched: list[dict[str, Any]] = []
    for spec in spec_rows:
        allowed, _reason = _usable_for_cloudpilot_linux(spec)
        if not allowed:
            continue
        cpu = int(spec["VCpuInfo"]["DefaultVCpus"])
        memory = round(float(spec["MemoryInfo"]["SizeInMiB"]) / 1024.0, 3)
        row = dict(spec)
        row["_cpu"] = cpu
        row["_memory_gib"] = memory
        row["_distance"] = _hardware_distance(
            cpu, memory, required_vcpus, required_memory_gib,
            bool(spec.get("CurrentGeneration", False)),
        )
        enriched.append(row)
    enriched.sort(key=lambda row: (row["_distance"], str(row.get("InstanceType", ""))))

    # Never let a distance-based shortlist hide a true exact hardware match.
    # Keep every exact profile and reserve candidates for progressive upgrades.
    safe_limit = max(1, min(int(limit), 48))
    from recommendation_engine import shortlist_with_upgrades
    return shortlist_with_upgrades(enriched, required_vcpus, required_memory_gib, safe_limit,
                                   specs=lambda row: (row["_cpu"], row["_memory_gib"]))


def _to_price_option(spec: dict[str, Any], region: str, offered_count: int,
                     account_id: str | None, os_name: str = "Linux") -> dict[str, Any]:
    instance_type = str(spec["InstanceType"])
    cpu = int(spec["VCpuInfo"]["DefaultVCpus"])
    memory_gib = round(float(spec["MemoryInfo"]["SizeInMiB"]) / 1024.0, 3)
    now = datetime.now(timezone.utc).isoformat()
    price = get_ec2_instance_on_demand_price(instance_type, "Windows" if os_name == "Windows" else "Linux/UNIX", "default", region)
    price_available = price.get("available") is True
    warnings = [
        "Hardware specifications and regional offering were read from the active AWS account's EC2 APIs.",
        "Regional offering does not guarantee account quota, immediate On-Demand capacity, or successful launch.",
        "Compute-only public price estimate; EBS, data transfer, public IPv4, taxes, discounts and support are excluded.",
    ]
    return {
        "available": price_available,
        "catalog_available": True,
        "provider": "AWS",
        "region": region,
        "service_type": f"EC2 {os_name} x86_64 shared-tenancy On-Demand",
        "sku": instance_type,
        "vcpus": cpu,
        "memory_gib": memory_gib,
        "architecture": "x86_64",
        "current_generation": bool(spec.get("CurrentGeneration", False)),
        "burstable": bool(spec.get("BurstablePerformanceSupported", False)),
        "accelerated": any(spec.get(key) for key in ("GpuInfo", "FpgaInfo", "InferenceAcceleratorInfo", "NeuronInfo")),
        "network_performance": spec.get("NetworkInfo", {}).get("NetworkPerformance"),
        "offered_in_target_location": True,
        "offering_location_type": "region",
        "account_id": account_id,
        "aws_region_offered_type_count": offered_count,
        "os": os_name,
        "hourly_price": price.get("hourly_usd") if price_available else None,
        "price_currency": "USD",
        "monthly_compute_estimate": price.get("monthly_730h_usd") if price_available else None,
        "monthly_storage_estimate": None,
        "monthly_network_estimate": None,
        "monthly_total_estimate": None,
        "price_type": "AWS public on-demand retail price",
        "price_source": price.get("price_source") if price_available else None,
        "price_retrieved_at": price.get("retrieved_at") if price_available else None,
        "price_effective_date": price.get("effective_date") if price_available else None,
        "price_publication_date": price.get("publication_date") if price_available else None,
        "price_sku": price.get("product_sku") if price_available else None,
        "price_model": price.get("price_model") if price_available else None,
        "pricing_api_region": price.get("pricing_api_region") if price_available else None,
        "price_cache_status": price.get("cache_status") if price_available else price.get("cache_status"),
        "price_cache_ttl_seconds": price.get("cache_ttl_seconds"),
        "telemetry_status": f"Live AWS EC2 API via account {account_id}" if account_id else "Live AWS EC2 API via active AWS SDK credentials",
        "capacity_status": "EC2 regional offering verified; account quota and immediate spare capacity not verified",
        "provisioning_enabled": False,
        "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)",
        "retrieved_at": now,
        "error": None if price_available else price.get("error", "AWS public price lookup unavailable; live hardware and regional offering are still verified."),
        "warnings": warnings,
        "price_assumptions": price.get("method") if price_available else "No price estimate is shown because the live AWS Price List request did not return an unambiguous rate.",
    }


def discover_aws_vm_options(region: str, min_vcpus: int, min_memory_gib: float,
                            candidate_limit: int | None = None, session: Any | None = None, os_name: str = "Linux") -> list[dict[str, Any]]:
    """Discover AWS instance types dynamically from the connected AWS account context.

    No instance-name catalogue or CPU/RAM fallback is used. The EC2 API returns
    regional offerings and live hardware specifications. Only the closest x86_64,
    EBS-root, HVM, On-Demand virtual-machine candidates are priced and returned.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        ec2 = _client("ec2", region, session=session)
        try:
            identity = _client("sts", region, session=session).get_caller_identity()
            account_id = identity.get("Account")
        except Exception:
            account_id = None
        offered_types = _pages_for_offerings(ec2, region)
        if not offered_types:
            return [{
                "available": False, "catalog_available": False, "provider": "AWS", "region": region,
                "sku": "no-regionally-offered-types", "vcpus": None, "memory_gib": None,
                "offered_in_target_location": False, "account_id": account_id,
                "telemetry_status": "AWS account API returned no regional offerings",
                "capacity_status": "not verified", "spec_source": "AWS EC2 API returned no offerings",
                "error": f"EC2 returned no instance-type offerings for region {region}. No hardcoded AWS profiles were used.",
                "retrieved_at": now,
            }]
        live_specs = _read_live_specs(ec2, offered_types)
        filtered: list[dict[str, Any]] = []
        rejected: list[str] = []
        for spec in live_specs:
            valid, reason = _usable_for_cloudpilot_linux(spec)
            if valid:
                filtered.append(spec)
            elif reason:
                rejected.append(reason)
        if candidate_limit is not None:
            limit = candidate_limit
        else:
            try:
                limit = int(os.getenv("CLOUDPILOT_AWS_DISCOVERY_CANDIDATES", "12"))
            except (TypeError, ValueError):
                limit = 12
        shortlisted = _shortlist(filtered, min_vcpus, min_memory_gib, limit)
        if not shortlisted:
            details = "; ".join(rejected[:3]) if rejected else "No compatible x86_64 EBS/HVM On-Demand types were returned."
            return [{
                "available": False, "catalog_available": False, "provider": "AWS", "region": region,
                "sku": "no-compatible-live-types", "vcpus": None, "memory_gib": None,
                "offered_in_target_location": None, "account_id": account_id,
                "telemetry_status": "AWS regional offerings discovered; no compatible live specifications",
                "capacity_status": "not verified", "spec_source": "AWS EC2 DescribeInstanceTypes API (live response)",
                "error": f"No compatible AWS instance types were discovered for the current x86_64 Linux workflow in {region}. {details}",
                "retrieved_at": now,
            }]
        return [
            _to_price_option(spec, region, len(offered_types), account_id, os_name) if os_name != "Linux" else _to_price_option(spec, region, len(offered_types), account_id)
            for spec in shortlisted
        ]
    except Exception as exc:
        error = _error_text(exc)
        if any(action.lower() in error.lower() for action in ("DescribeInstanceTypeOfferings", "DescribeInstanceTypes")):
            error += " Attach ec2:DescribeInstanceTypeOfferings and ec2:DescribeInstanceTypes read permissions to the IIPP EC2 IAM role; see iam-instance-type-policy.json."
        return [{
            "available": False, "catalog_available": False, "provider": "AWS", "region": region,
            "sku": "aws-live-discovery-failed", "vcpus": None, "memory_gib": None,
            "offered_in_target_location": None, "account_id": None,
            "telemetry_status": "AWS live discovery failed",
            "capacity_status": "not verified", "spec_source": "No live AWS specifications returned",
            "error": f"AWS live discovery failed: {error}. No hardcoded AWS profiles or CPU/RAM fallbacks were used.",
            "retrieved_at": now,
        }]


def get_aws_vm_option(region: str = "ap-south-1", instance_type: str = "t3.large") -> dict[str, Any]:
    """Read one named type from live EC2 APIs without any hardcoded specification fallback."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        ec2 = _client("ec2", region)
        offerings = ec2.describe_instance_type_offerings(
            LocationType="region",
            Filters=[{"Name": "instance-type", "Values": [instance_type]}, {"Name": "location", "Values": [region]}],
        ).get("InstanceTypeOfferings", [])
        offered = any(row.get("InstanceType") == instance_type for row in offerings)
        if not offered:
            return {"available": False, "catalog_available": False, "provider": "AWS", "region": region,
                    "sku": instance_type, "vcpus": None, "memory_gib": None,
                    "offered_in_target_location": False, "spec_source": "No live EC2 hardware data used",
                    "telemetry_status": "AWS EC2 regional offering checked",
                    "capacity_status": "not offered in selected region", "error": f"EC2 does not report {instance_type} as offered in {region}.",
                    "retrieved_at": now}
        response = ec2.describe_instance_types(InstanceTypes=[instance_type])
        spec = next((row for row in response.get("InstanceTypes", []) if row.get("InstanceType") == instance_type), None)
        if not spec:
            raise RuntimeError(f"EC2 returned no live specification for {instance_type}.")
        valid, reason = _usable_for_cloudpilot_linux(spec)
        if not valid:
            raise ValueError(reason or f"{instance_type} is incompatible with the current POC.")
        account_id = None
        try:
            account_id = _client("sts", region).get_caller_identity().get("Account")
        except Exception:
            pass
        return _to_price_option(spec, region, 1, account_id)
    except Exception as exc:
        error = _error_text(exc)
        if any(action.lower() in error.lower() for action in ("DescribeInstanceTypeOfferings", "DescribeInstanceTypes")):
            error += " Attach ec2:DescribeInstanceTypeOfferings and ec2:DescribeInstanceTypes read permissions to the IIPP EC2 IAM role; see iam-instance-type-policy.json."
        return {"available": False, "catalog_available": False, "provider": "AWS", "region": region,
                "sku": instance_type, "vcpus": None, "memory_gib": None,
                "offered_in_target_location": None, "spec_source": "No live AWS specification fallback used",
                "telemetry_status": "AWS live EC2 check failed", "capacity_status": "not verified",
                "error": error, "retrieved_at": now}
