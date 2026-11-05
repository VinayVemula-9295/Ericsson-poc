"""Read-only AWS telemetry, inventory, Cost Explorer, and EC2 pricing helpers."""
from __future__ import annotations

import calendar
import json
import os
from datetime import datetime, timedelta, timezone, date
import threading
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def _region() -> str:
    return os.getenv("AWS_REGION", "ap-south-1")


def _aws_api_config() -> Config:
    return Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 3, "mode": "standard"})


def _ec2_client():
    return boto3.client("ec2", region_name=_region(), config=_aws_api_config())


def _cw_client():
    return boto3.client("cloudwatch", region_name=_region(), config=_aws_api_config())


def _ce_client():
    # Cost Explorer's API endpoint is normally accessed in us-east-1.
    return boto3.client("ce", region_name=os.getenv("COST_EXPLORER_REGION", "us-east-1"), config=_aws_api_config())


def _pricing_client():
    # AWS Price List API is available in a limited set of regions.
    return boto3.client("pricing", region_name=os.getenv("AWS_PRICING_API_REGION", "us-east-1"), config=_aws_api_config())


def _error_text(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        detail = exc.response.get("Error", {})
        return f"{detail.get('Code', 'AWS error')}: {detail.get('Message', str(exc))}"
    return str(exc)


def list_running_instances() -> dict[str, Any]:
    """Return real running EC2 inventory for the configured region."""
    try:
        ec2 = _ec2_client()
        paginator = ec2.get_paginator("describe_instances")
        instances: list[dict[str, Any]] = []
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
            for reservation in page.get("Reservations", []):
                for item in reservation.get("Instances", []):
                    tags = {t.get("Key"): t.get("Value") for t in item.get("Tags", [])}
                    platform_details = item.get("PlatformDetails", "Linux/UNIX")
                    instances.append({
                        "name": tags.get("Name", "(untagged)"),
                        "instance_id": item.get("InstanceId", ""),
                        "instance_type": item.get("InstanceType", "unknown"),
                        "state": item.get("State", {}).get("Name", "unknown"),
                        "az": item.get("Placement", {}).get("AvailabilityZone", ""),
                        "private_ip": item.get("PrivateIpAddress", ""),
                        "public_ip": item.get("PublicIpAddress", ""),
                        "launch_time": item.get("LaunchTime").isoformat() if item.get("LaunchTime") else "",
                        "platform_details": platform_details,
                        "architecture": item.get("Architecture", "x86_64"),
                        "root_device_type": item.get("RootDeviceType", "unknown"),
                        "virtualization_type": item.get("VirtualizationType", "unknown"),
                        "tenancy": item.get("Placement", {}).get("Tenancy", "default"),
                        "image_id": item.get("ImageId", ""),
                    })
        return {"available": True, "region": _region(), "instances": instances, "count": len(instances),
                "checked_at": datetime.now(timezone.utc).isoformat(), "data_source": "EC2 DescribeInstances API"}
    except Exception as exc:
        return {"available": False, "region": _region(), "instances": [], "count": 0,
                "checked_at": datetime.now(timezone.utc).isoformat(), "data_source": "EC2 DescribeInstances API",
                "error": _error_text(exc)}



def discover_instance_type_options(instance_id: str) -> dict[str, Any]:
    """Discover same-family candidate sizes offered in the target's AZ and price them.

    EC2 instance-type offerings indicate regional/AZ support, not spare capacity or a
    guaranteed successful launch. No resources are launched by this function.
    """
    details = get_target_instance_details(instance_id)
    if not details.get("available"):
        return {"available": False, "instance_id": instance_id,
                "error": details.get("error", "Target instance details unavailable."), "options": []}

    current_type = str(details.get("instance_type") or "")
    family = current_type.split(".", 1)[0]
    family_options = {
        "t2": ["t2.micro", "t2.small", "t2.medium", "t2.large"],
        "t3": ["t3.micro", "t3.small", "t3.medium", "t3.large"],
        "t3a": ["t3a.micro", "t3a.small", "t3a.medium", "t3a.large"],
        "t4g": ["t4g.micro", "t4g.small", "t4g.medium", "t4g.large"],
    }
    candidates = family_options.get(family, [current_type] if current_type else [])
    configured = [item.strip() for item in os.getenv("CLOUDPILOT_ALLOWED_INSTANCE_TYPES", "").split(",") if item.strip()]
    if configured:
        candidates = [item for item in candidates if item in configured]
        if current_type and current_type not in candidates and current_type.startswith(family + "."):
            candidates.append(current_type)
    if not candidates:
        return {"available": False, "instance_id": instance_id,
                "error": f"No candidate types are configured for family {family}.", "options": []}

    region = _region()
    az = details.get("availability_zone") or ""
    ec2 = _ec2_client()
    try:
        filters = [{"Name": "instance-type", "Values": candidates}]
        if az:
            filters.append({"Name": "location", "Values": [az]})
            offering_response = ec2.describe_instance_type_offerings(
                LocationType="availability-zone", Filters=filters
            )
        else:
            filters.append({"Name": "location", "Values": [region]})
            offering_response = ec2.describe_instance_type_offerings(
                LocationType="region", Filters=filters
            )
        offered = {x.get("InstanceType") for x in offering_response.get("InstanceTypeOfferings", [])}
    except Exception as exc:
        return {"available": False, "instance_id": instance_id, "region": region, "availability_zone": az,
                "current_instance_type": current_type, "options": [],
                "error": f"Could not query EC2 instance-type offerings: {_error_text(exc)}"}

    specs_by_type: dict[str, dict[str, Any]] = {}
    if offered:
        try:
            response = ec2.describe_instance_types(InstanceTypes=sorted(offered))
            for spec in response.get("InstanceTypes", []):
                cpu = spec.get("VCpuInfo", {})
                mem = spec.get("MemoryInfo", {})
                specs_by_type[spec.get("InstanceType", "")] = {
                    "vcpus": cpu.get("DefaultVCpus"),
                    "memory_gib": round(float(mem.get("SizeInMiB", 0)) / 1024, 2) if mem.get("SizeInMiB") else None,
                    "network_performance": spec.get("NetworkInfo", {}).get("NetworkPerformance"),
                    "burstable": bool(spec.get("BurstablePerformanceSupported", False)),
                    "architectures": spec.get("ProcessorInfo", {}).get("SupportedArchitectures", []),
                }
        except Exception:
            # Offering information remains useful; details for a size may be unavailable.
            specs_by_type = {}

    # AWS publishes these baseline levels for the common T families. Include only where known;
    # all other properties above are read from EC2 APIs.
    baseline_cpu = {
        "t2.micro": 10, "t2.small": 20, "t2.medium": 20, "t2.large": 30,
        "t3.micro": 10, "t3.small": 20, "t3.medium": 20, "t3.large": 30,
        "t3a.micro": 10, "t3a.small": 20, "t3a.medium": 20, "t3a.large": 30,
        "t4g.micro": 10, "t4g.small": 20, "t4g.medium": 20, "t4g.large": 30,
    }
    current_price = get_ec2_instance_on_demand_price(
        current_type, details.get("platform_details", "Linux/UNIX"), details.get("tenancy", "default"), region
    )
    options: list[dict[str, Any]] = []
    for candidate in candidates:
        is_offered = candidate in offered
        spec = specs_by_type.get(candidate, {})
        price = get_ec2_instance_on_demand_price(
            candidate, details.get("platform_details", "Linux/UNIX"), details.get("tenancy", "default"), region
        ) if is_offered else {"available": False, "error": "Instance type is not listed as offered in target location."}
        hourly = price.get("hourly_usd") if price.get("available") else None
        current_hourly = current_price.get("hourly_usd") if current_price.get("available") else None
        options.append({
            "instance_type": candidate,
            "is_current": candidate == current_type,
            "offered_in_target_location": is_offered,
            "target_location_type": "availability-zone" if az else "region",
            "target_location": az or region,
            "vcpus": spec.get("vcpus"),
            "memory_gib": spec.get("memory_gib"),
            "network_performance": spec.get("network_performance"),
            "burstable": spec.get("burstable"),
            "baseline_cpu_percent_per_vcpu": baseline_cpu.get(candidate),
            "hourly_usd": hourly,
            "monthly_730h_usd": price.get("monthly_730h_usd") if price.get("available") else None,
            "monthly_delta_vs_current_usd": round((hourly - current_hourly) * 730, 4) if hourly is not None and current_hourly is not None else None,
            "price_status": "available" if price.get("available") else price.get("error", "price unavailable"),
        })

    offered_options = [x for x in options if x["offered_in_target_location"]]
    offered_options.sort(key=lambda x: (x.get("vcpus") or 999, x.get("memory_gib") or 999, x.get("monthly_730h_usd") or float("inf")))
    return {
        "available": True, "region": region, "availability_zone": az or None,
        "instance_id": instance_id, "current_instance_type": current_type,
        "offered_options": offered_options, "options": options,
        "capacity_note": "Offered in the target AZ means the instance type is supported there; it does not guarantee spare On-Demand capacity or that a resize/launch will succeed. No capacity was reserved and no resources were changed.",
    }

def _cwagent_dimensions(metric_name: str, instance_id: str) -> list[dict[str, str]]:
    """Discover the precise dimensions emitted by the CloudWatch Agent."""
    cw = _cw_client()
    response = cw.list_metrics(
        Namespace="CWAgent",
        MetricName=metric_name,
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
    )
    candidates = [
        metric.get("Dimensions", []) for metric in response.get("Metrics", [])
        if any(d.get("Name") == "InstanceId" and d.get("Value") == instance_id for d in metric.get("Dimensions", []))
    ]
    if not candidates:
        return [{"Name": "InstanceId", "Value": instance_id}]
    candidates.sort(key=lambda dims: (len(dims), str(dims)))
    return candidates[0]


def get_metric_series(instance_id: str, hours: int = 1) -> dict[str, Any]:
    """Fetch actual CPU/memory/disk utilization and network bytes per 60-second period."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=max(1, hours))
    cw = _cw_client()
    ec2_dims = [{"Name": "InstanceId", "Value": instance_id}]
    definitions = [
        ("CPU", "AWS/EC2", "CPUUtilization", ec2_dims, ["Average", "Maximum"], "Percent"),
        ("Memory", "CWAgent", "mem_used_percent", None, ["Average", "Maximum"], "Percent"),
        ("Disk", "CWAgent", "disk_used_percent", None, ["Average", "Maximum"], "Percent"),
        ("Network In", "AWS/EC2", "NetworkIn", ec2_dims, ["Sum"], "Bytes per 60-second period"),
        ("Network Out", "AWS/EC2", "NetworkOut", ec2_dims, ["Sum"], "Bytes per 60-second period"),
    ]
    result: dict[str, Any] = {"instance_id": instance_id, "region": _region(), "generated_at": now.isoformat(), "metrics": {}}
    for label, namespace, metric_name, dimensions, stats, unit in definitions:
        try:
            actual_dimensions = dimensions if dimensions is not None else _cwagent_dimensions(metric_name, instance_id)
            response = cw.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=actual_dimensions,
                StartTime=start,
                EndTime=now,
                Period=60,
                Statistics=stats,
            )
            points = sorted(response.get("Datapoints", []), key=lambda p: p["Timestamp"])
            stat_name = "Sum" if "Sum" in stats else "Average"
            cooked_points = []
            for point in points:
                raw_value = point.get(stat_name)
                if raw_value is None:
                    continue
                value = float(raw_value)
                cooked_points.append({
                    "timestamp": point["Timestamp"].astimezone(timezone.utc).isoformat(),
                    "value": value,
                    # Backward-compatible aliases used by CPU trend logic; network charts use `value`.
                    "average": value,
                    "maximum": float(point.get("Maximum", value)),
                    "statistic": stat_name,
                    "unit": unit,
                })
            result["metrics"][label] = {
                "available": bool(cooked_points), "namespace": namespace, "metric_name": metric_name,
                "dimensions": actual_dimensions, "period_seconds": 60, "statistic": stat_name,
                "unit": unit, "points": cooked_points,
                "latest": cooked_points[-1]["value"] if cooked_points else None,
                "latest_timestamp": cooked_points[-1]["timestamp"] if cooked_points else None, "error": None,
            }
        except Exception as exc:
            result["metrics"][label] = {
                "available": False, "namespace": namespace, "metric_name": metric_name,
                "dimensions": dimensions or [], "period_seconds": 60, "statistic": "Sum" if "Sum" in stats else "Average",
                "unit": unit, "points": [], "latest": None, "latest_timestamp": None,
                "error": _error_text(exc),
            }
    return result


def get_monthly_cost_summary(today: date | None = None) -> dict[str, Any]:
    """Read actual MTD costs and a projection; any extrapolation is not a guaranteed bill forecast."""
    if os.getenv("ENABLE_COST_EXPLORER", "true").lower() not in {"1", "true", "yes"}:
        return {"available": False, "error": "Cost Explorer is disabled. Set ENABLE_COST_EXPLORER=true and grant ce:GetCostAndUsage."}
    today = today or datetime.now(timezone.utc).date()
    start = today.replace(day=1)
    if today <= start:
        return {"available": False, "error": "Not enough completed days this month to estimate account spending."}
    try:
        ce = _ce_client()
        response = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": today.isoformat()},
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
        total = 0.0
        by_service: dict[str, float] = {}
        currency = "USD"
        for day in response.get("ResultsByTime", []):
            for group in day.get("Groups", []):
                service = group.get("Keys", ["Unknown"])[0]
                metric = group.get("Metrics", {}).get("UnblendedCost", {})
                currency = metric.get("Unit", currency) or currency
                amount = float(metric.get("Amount", "0"))
                total += amount
                by_service[service] = by_service.get(service, 0.0) + amount
        completed_days = max(1, (today - start).days)
        days_in_month = calendar.monthrange(today.year, today.month)[1]
        projection = total / completed_days * days_in_month
        forecast_value = None
        forecast_error = None
        forecast_method = None
        try:
            end_of_month = date(today.year, today.month, days_in_month)
            forecast_response = ce.get_cost_forecast(
                TimePeriod={"Start": today.isoformat(), "End": (end_of_month + timedelta(days=1)).isoformat()},
                Metric="UNBLENDED_COST", Granularity="MONTHLY", PredictionIntervalLevel=80,
            )
            result_rows = forecast_response.get("ForecastResultsByTime", [])
            forecast_numbers = [float(row.get("MeanValue")) for row in result_rows if row.get("MeanValue") is not None]
            if forecast_numbers:
                # This is forecast remaining spend; account total projected for the month is MTD actual + future forecast.
                forecast_value = total + sum(forecast_numbers)
                forecast_method = "Cost Explorer forecast for remaining month plus actual MTD spend; forecast is not a guaranteed bill."
        except Exception as forecast_exc:
            forecast_error = _error_text(forecast_exc)
        if forecast_value is not None:
            month_end_estimate = forecast_value
            estimate_method = forecast_method
        else:
            month_end_estimate = projection
            estimate_method = "Fallback linear extrapolation from actual spend through yesterday; not an AWS forecast or guaranteed bill." 
        sorted_services = sorted(by_service.items(), key=lambda x: x[1], reverse=True)
        return {
            "available": True,
            "currency": currency,
            "actual_mtd": round(total, 6),
            "completed_days": completed_days,
            "days_in_month": days_in_month,
            "estimated_month_end": round(month_end_estimate, 6) if total > 0 or forecast_value is not None else None,
            "month_end_estimate_source": "AWS Cost Explorer forecast" if forecast_value is not None else ("linear extrapolation" if total > 0 else "unavailable"),
            "forecast_error": forecast_error,
            "estimate_method": estimate_method,
            "service_costs": [{"service": k, "mtd": round(v, 6)} for k, v in sorted_services],
            "period_start": start.isoformat(),
            "period_end_exclusive": today.isoformat(),
            "returned_group_count": len(sorted_services),
            "zero_cost_data": abs(total) < 0.0000005 and forecast_value is None,
            "error": None,
        }
    except Exception as exc:
        return {"available": False, "error": _error_text(exc)}


_REGION_LOCATIONS = {
    "af-south-1": "Africa (Cape Town)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "ap-east-2": "Asia Pacific (Taipei)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-southeast-5": "Asia Pacific (Malaysia)",
    "ap-southeast-7": "Asia Pacific (Thailand)",
    "ca-central-1": "Canada (Central)",
    "ca-west-1": "Canada West (Calgary)",
    "eu-central-1": "Europe (Frankfurt)",
    "eu-central-2": "Europe (Zurich)",
    "eu-north-1": "Europe (Stockholm)",
    "eu-south-1": "Europe (Milan)",
    "eu-south-2": "Europe (Spain)",
    "eu-west-1": "Europe (Ireland)",
    "eu-west-2": "Europe (London)",
    "eu-west-3": "Europe (Paris)",
    "il-central-1": "Israel (Tel Aviv)",
    "me-central-1": "Middle East (UAE)",
    "me-south-1": "Middle East (Bahrain)",
    "sa-east-1": "South America (Sao Paulo)",
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
}

_PRICE_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_PRICE_CACHE_LOCK = threading.RLock()


def _price_cache_ttl_seconds() -> int:
    try:
        return max(30, min(3600, int(os.getenv("CLOUDPILOT_PRICE_CACHE_TTL_SECONDS", "300"))))
    except (TypeError, ValueError):
        return 300


def _parse_ondemand_hourly_products(products: list[str], region: str, instance_type: str,
                                    expected_location: str | None, os_name: str = "Linux") -> dict[str, Any] | None:
    """Extract one unambiguous Linux/shared On-Demand hourly rate from Price List products."""
    matches: list[dict[str, Any]] = []
    for raw in products:
        try:
            product = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        metadata = product.get("product", {})
        attrs = metadata.get("attributes", {})
        if attrs.get("instanceType") not in {None, instance_type}:
            continue
        if attrs.get("regionCode") not in {None, region}:
            continue
        if expected_location and attrs.get("location") not in {None, expected_location}:
            continue
        product_os = str(attrs.get("operatingSystem", "Linux")).strip().lower()
        if product_os not in ({"linux", "linux/unix"} if os_name == "Linux" else {"windows"}):
            continue
        if str(attrs.get("tenancy", "Shared")).strip().lower() not in {"shared", "default"}:
            continue
        if attrs.get("preInstalledSw") not in {None, "NA"}:
            continue
        if attrs.get("licenseModel") not in ({None, "No License required"} if os_name == "Linux" else {None, "License included"}):
            continue
        if attrs.get("capacitystatus") not in {None, "Used"}:
            continue
        if metadata.get("productFamily") not in {None, "Compute Instance"}:
            continue

        terms = product.get("terms", {}).get("OnDemand", {})
        for term in terms.values():
            term_attrs = term.get("termAttributes", {})
            # No purchase option attribute is required for standard On-Demand terms.
            for dimension in term.get("priceDimensions", {}).values():
                if dimension.get("unit") != "Hrs":
                    continue
                begin_range = str(dimension.get("beginRange", "0"))
                if begin_range not in {"0", "0.0"}:
                    continue
                raw_usd = dimension.get("pricePerUnit", {}).get("USD")
                try:
                    hourly = float(raw_usd)
                except (TypeError, ValueError):
                    continue
                if hourly < 0 or not (hourly < float("inf")):
                    continue
                matches.append({
                    "hourly_usd": hourly,
                    "effective_date": term.get("effectiveDate"),
                    "publication_date": product.get("publicationDate"),
                    "product_sku": metadata.get("sku"),
                    "location": attrs.get("location") or expected_location,
                    "region_code": attrs.get("regionCode") or region,
                    "product_family": metadata.get("productFamily"),
                })

    if not matches:
        return None
    # A single instance/region/OS/tenancy should have one standard On-Demand rate.
    distinct_prices = {round(item["hourly_usd"], 10) for item in matches}
    if len(distinct_prices) != 1:
        return {"ambiguous": True, "matched_rates": sorted(distinct_prices)}
    matches.sort(key=lambda item: (str(item.get("effective_date") or ""),
                                   str(item.get("publication_date") or "")), reverse=True)
    return matches[0]


def _fetch_aws_ec2_hourly_price(region: str, instance_type: str, tenancy: str, os_name: str = "Linux") -> dict[str, Any]:
    """Query AWS Price List Query API; never substitute fixture or hardcoded rates."""
    if tenancy not in {"default", "shared"}:
        return {"available": False, "error": f"Pricing estimate is not implemented for {tenancy} tenancy."}

    api_region = os.getenv("AWS_PRICING_API_REGION", "us-east-1").strip()
    supported_api_regions = {"us-east-1", "ap-south-1", "eu-central-1"}
    if api_region not in supported_api_regions:
        return {"available": False,
                "error": f"AWS Price List Query API endpoint region {api_region} is not configured as a supported endpoint. Use us-east-1, ap-south-1, or eu-central-1."}

    expected_location = _REGION_LOCATIONS.get(region)
    pricing = _pricing_client()
    base = [
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": os_name},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "licenseModel", "Value": "No License required" if os_name == "Linux" else "License included"},
    ]
    # regionCode is a product attribute and avoids guessing the marketing Location string.
    filter_attempts = [[
        *base,
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
        {"Type": "TERM_MATCH", "Field": "operation", "Value": "RunInstances" if os_name == "Linux" else "RunInstances:0002"},
    ], [
        *base,
        {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
    ]]
    if expected_location:
        filter_attempts.append([
            *base,
            {"Type": "TERM_MATCH", "Field": "location", "Value": expected_location},
            {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            {"Type": "TERM_MATCH", "Field": "operation", "Value": "RunInstances" if os_name == "Linux" else "RunInstances:0002"},
        ])
        filter_attempts.append([
            *base,
            {"Type": "TERM_MATCH", "Field": "location", "Value": expected_location},
        ])

    errors: list[str] = []
    fetched_at = datetime.now(timezone.utc).isoformat()
    for filters in filter_attempts:
        try:
            products: list[str] = []
            next_token = None
            page_count = 0
            while True:
                request: dict[str, Any] = {
                    "ServiceCode": "AmazonEC2", "Filters": filters,
                    "FormatVersion": "aws_v1", "MaxResults": 100,
                }
                if next_token:
                    request["NextToken"] = next_token
                response = pricing.get_products(**request)
                products.extend(response.get("PriceList", []))
                next_token = response.get("NextToken")
                page_count += 1
                # Exact instance + region filters should return very few rows. Guard API loops.
                if not next_token or page_count >= 5:
                    break
            parsed = _parse_ondemand_hourly_products(products, region, instance_type, expected_location, os_name) if os_name != "Linux" else _parse_ondemand_hourly_products(products, region, instance_type, expected_location)
            if parsed and parsed.get("ambiguous"):
                return {"available": False,
                        "error": f"AWS Price List returned multiple distinct Linux On-Demand hourly rates for {instance_type} in {region}; refusing to guess.",
                        "retrieved_at": fetched_at, "pricing_api_region": api_region}
            if parsed:
                return {
                    "available": True,
                    **parsed,
                    "retrieved_at": fetched_at,
                    "pricing_api_region": api_region,
                    "price_source": "AWS Price List Query API · AmazonEC2 GetProducts",
                    "price_model": f"On-Demand · {os_name} · Shared tenancy",
                }
        except Exception as exc:
            error_text = _error_text(exc)
            errors.append(error_text)
            # A permission failure is independent of filter shape; retries with
            # relaxed filters cannot fix it and only add noisy API calls.
            if "AccessDenied" in error_text or "Unauthorized" in error_text:
                break

    detail = next((value for value in errors if "AccessDenied" in value or "Unauthorized" in value), None)
    if not detail and errors:
        detail = errors[-1]
    return {
        "available": False,
        "error": detail or f"AWS Price List returned no Linux/shared On-Demand hourly rate for {instance_type} in {region}.",
        "retrieved_at": fetched_at,
        "pricing_api_region": api_region,
    }


def _hourly_linux_price_cached(region: str, instance_type: str, tenancy: str) -> dict[str, Any]:
    """Short TTL cache for real Price List API results; failed calls are never cached."""
    key = (region, instance_type, tenancy)
    now_monotonic = time.monotonic()
    with _PRICE_CACHE_LOCK:
        cached = _PRICE_CACHE.get(key)
        if cached and cached[0] > now_monotonic:
            result = dict(cached[1])
            result["cache_status"] = "HIT"
            result["cache_ttl_seconds"] = _price_cache_ttl_seconds()
            return result
        if cached:
            _PRICE_CACHE.pop(key, None)

    result = _fetch_aws_ec2_hourly_price(region, instance_type, tenancy)
    if result.get("available") is True:
        with _PRICE_CACHE_LOCK:
            _PRICE_CACHE[key] = (time.monotonic() + _price_cache_ttl_seconds(), dict(result))
        result["cache_status"] = "MISS"
        result["cache_ttl_seconds"] = _price_cache_ttl_seconds()
    else:
        result["cache_status"] = "NOT_CACHED"
        result["cache_ttl_seconds"] = _price_cache_ttl_seconds()
    return result


def get_ec2_instance_on_demand_price(
    instance_type: str,
    platform_details: str = "Linux/UNIX",
    tenancy: str = "default",
    region: str | None = None,
) -> dict[str, Any]:
    """Return current AWS Price List On-Demand hourly price (USD) for Linux EC2."""
    region = region or _region()
    platform = (platform_details or "Linux/UNIX").lower()
    if "linux" not in platform and "ubuntu" not in platform and platform != "windows":
        return {"available": False, "error": f"Live compute price lookup currently covers Linux/UNIX only; instance platform is {platform_details}."}
    try:
        price = (_fetch_aws_ec2_hourly_price(region, instance_type, tenancy, "Windows") if platform == "windows"
                 else _hourly_linux_price_cached(region, instance_type, tenancy))
        hourly = price.get("hourly_usd") if price.get("available") else None
        if hourly is None:
            return {"available": False, **price, "region": region, "instance_type": instance_type,
                    "error": price.get("error", "No AWS Price List rate returned.")}
        return {
            **price,
            "available": True,
            "region": region,
            "instance_type": instance_type,
            "hourly_usd": round(float(hourly), 8),
            "currency": "USD",
            "monthly_730h_usd": round(float(hourly) * 730, 4),
            "method": f"AWS Price List Query API On-Demand {platform_details}/shared-tenancy compute rate × 730 hours. Excludes EBS, data transfer, public IPv4, taxes, discounts, Savings Plans and support.",
        }
    except Exception as exc:
        return {"available": False, "region": region, "instance_type": instance_type, "error": _error_text(exc)}


def _clear_hourly_price_cache() -> None:
    with _PRICE_CACHE_LOCK:
        _PRICE_CACHE.clear()


# Compatibility with older call sites/tests that used the functools.lru_cache API.
_hourly_linux_price_cached.cache_clear = _clear_hourly_price_cache  # type: ignore[attr-defined]


def get_running_ec2_cost_estimate(instances: list[dict[str, Any]]) -> dict[str, Any]:
    """Estimate monthly 24x7 compute run-rate for currently running Linux EC2 instances."""
    if not instances:
        return {"available": True, "complete": True, "currency": "USD", "running_instance_count": 0,
                "hourly_total": 0.0, "monthly_run_rate": 0.0, "instances": [],
                "method": "No currently running EC2 instances were returned."}
    rows: list[dict[str, Any]] = []
    total_hourly = 0.0
    missing: list[str] = []
    for instance in instances:
        price = get_ec2_instance_on_demand_price(
            instance.get("instance_type", "unknown"),
            instance.get("platform_details", "Linux/UNIX"),
            instance.get("tenancy", "default"),
        )
        if price.get("available"):
            hourly = float(price["hourly_usd"])
            total_hourly += hourly
            rows.append({
                "name": instance.get("name", "(untagged)"),
                "instance_id": instance.get("instance_id", ""),
                "instance_type": instance.get("instance_type", "unknown"),
                "hourly_usd": hourly,
                "monthly_730h_usd": float(price["monthly_730h_usd"]),
                "price_status": "estimated",
            })
        else:
            missing.append(f"{instance.get('instance_id', 'unknown')}: {price.get('error', 'price unavailable')}")
            rows.append({
                "name": instance.get("name", "(untagged)"),
                "instance_id": instance.get("instance_id", ""),
                "instance_type": instance.get("instance_type", "unknown"),
                "hourly_usd": None,
                "monthly_730h_usd": None,
                "price_status": price.get("error", "price unavailable"),
            })
    complete = not missing
    has_any_price = any(row["monthly_730h_usd"] is not None for row in rows)
    return {
        "available": has_any_price,
        "complete": complete,
        "currency": "USD",
        "region": _region(),
        "running_instance_count": len(instances),
        "priced_instance_count": sum(1 for row in rows if row["monthly_730h_usd"] is not None),
        "hourly_total": round(total_hourly, 8),
        "monthly_run_rate": round(total_hourly * 730, 4),
        "instances": rows,
        "missing_prices": missing,
        "method": "AWS Price List on-demand Linux/shared-tenancy compute rates × 730 hours. Estimate assumes each currently running instance stays running 24×7; excludes EBS, data transfer, taxes, Savings Plans/Reserved discounts, and other AWS services.",
        "error": None if has_any_price else "Unable to price running instances. Check pricing:GetProducts permissions, region mapping, and instance OS/tenancy.",
    }


def get_target_instance_details(instance_id: str) -> dict[str, Any]:
    if not instance_id:
        return {"available": False, "error": "TARGET_INSTANCE_ID is not configured."}
    try:
        response = _ec2_client().describe_instances(InstanceIds=[instance_id])
        for reservation in response.get("Reservations", []):
            for item in reservation.get("Instances", []):
                tags = {t.get("Key"): t.get("Value") for t in item.get("Tags", [])}
                return {
                    "available": True,
                    "instance_id": item.get("InstanceId"),
                    "name": tags.get("Name", "(untagged)"),
                    "instance_type": item.get("InstanceType"),
                    "state": item.get("State", {}).get("Name"),
                    "private_ip": item.get("PrivateIpAddress"),
                    "availability_zone": item.get("Placement", {}).get("AvailabilityZone"),
                    "architecture": item.get("Architecture", "x86_64"),
                    "platform_details": item.get("PlatformDetails", "Linux/UNIX"),
                    "root_device_type": item.get("RootDeviceType", "unknown"),
                    "virtualization_type": item.get("VirtualizationType", "unknown"),
                    "tenancy": item.get("Placement", {}).get("Tenancy", "default"),
                    "launch_time": item.get("LaunchTime").isoformat() if item.get("LaunchTime") else "",
                    "image_id": item.get("ImageId", ""),
                    "tags": tags,
                }
        return {"available": False, "error": f"Instance {instance_id} was not returned by EC2."}
    except Exception as exc:
        return {"available": False, "error": _error_text(exc)}


def clear_price_cache() -> None:
    """Clear AWS Price List lookup cache after IAM permissions or configuration change."""
    try:
        _hourly_linux_price_cached.cache_clear()
    except Exception:
        pass
