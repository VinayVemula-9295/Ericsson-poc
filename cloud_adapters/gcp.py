"""Google Cloud Billing Catalog public price adapter (API key required; no GCP account connection implied)."""
from __future__ import annotations
import json
import os
import urllib.parse
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone
from typing import Any

BASE = "https://cloudbilling.googleapis.com/v1"

# Billing Catalog SKU descriptions sometimes expose both a broad regional rate
# (for example APAC) and a city-specific rate (Mumbai). Prefer the specific
# regional meter when both are returned.
_REGION_CITIES = {
    "asia-south1": ("mumbai",),
    "asia-south2": ("delhi",),
    "asia-east1": ("taiwan", "changhua"),
    "asia-east2": ("hong kong",),
    "asia-northeast1": ("tokyo",),
    "asia-northeast2": ("osaka",),
    "asia-northeast3": ("seoul",),
    "asia-southeast1": ("singapore",),
    "asia-southeast2": ("jakarta",),
    "australia-southeast1": ("sydney",),
    "australia-southeast2": ("melbourne",),
    "europe-west1": ("belgium",),
    "europe-west2": ("london",),
    "europe-west3": ("frankfurt",),
    "europe-west4": ("netherlands",),
    "europe-west6": ("zurich",),
    "europe-west8": ("milan",),
    "europe-west9": ("paris",),
    "northamerica-northeast1": ("montreal",),
    "northamerica-northeast2": ("toronto",),
    "southamerica-east1": ("sao paulo", "são paulo"),
    "us-central1": ("iowa",),
    "us-east1": ("south carolina",),
    "us-east4": ("northern virginia", "virginia"),
    "us-west1": ("oregon",),
}
_REGION_GROUPS = {
    "asia": "apac", "australia": "apac", "europe": "emea",
    "northamerica": "americas", "southamerica": "americas", "us-": "americas",
}

def _prefer_specific_region_rates(rates: list[tuple[float, dict[str, Any]]], region: str) -> list[tuple[float, dict[str, Any]]]:
    """Prefer city-local SKUs over broad APAC/EMEA/Americas rates for a region."""
    if not rates:
        return rates
    descriptions = [(rate, sku, str(sku.get("description", "")).lower()) for rate, sku in rates]
    cities = _REGION_CITIES.get(region.lower(), ())
    if cities:
        local = [(rate, sku) for rate, sku, desc in descriptions if any(city in desc for city in cities)]
        if local:
            return local
    group = None
    for prefix, label in _REGION_GROUPS.items():
        if region.lower().startswith(prefix):
            group = label
            break
    if group:
        broad = [(rate, sku) for rate, sku, desc in descriptions if f"running in {group}" in desc]
        if broad:
            return broad
    return rates

MACHINE_PROFILE = {
    "n2-standard-2": (2, 8.0),
    "n2-standard-4": (4, 16.0),
    "n2-standard-8": (8, 32.0),
    "n2-standard-16": (16, 64.0),
    "n2-standard-32": (32, 128.0),
}


def _get_json(url: str, timeout: int = 20) -> dict[str, Any]:
    """Bounded read-only GET with small retries for throttling/transient server errors."""
    req = urllib.request.Request(url, headers={"User-Agent": "Innominds-IIPP/1.0", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Pricing API returned non-object JSON.")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        except urllib.error.URLError:
            if attempt == 2:
                raise
        time.sleep(0.25 * (2 ** attempt))
    raise RuntimeError("Pricing API request failed after bounded retries.")


def _unit_price_usd(sku: dict[str, Any]) -> float | None:
    """Extract the current first tier only when its unit can be normalized to USD/hour."""
    from datetime import datetime
    infos = sku.get("pricingInfo") or []
    now = datetime.now(timezone.utc)
    eligible_infos = []
    for info in infos:
        try:
            effective = datetime.fromisoformat(str(info.get("effectiveTime", "")).replace("Z", "+00:00"))
            if effective.tzinfo is None:
                effective = effective.replace(tzinfo=timezone.utc)
            if effective <= now:
                eligible_infos.append((effective, info))
        except (TypeError, ValueError):
            continue
    if not eligible_infos:
        return None
    expr = max(eligible_infos, key=lambda x: x[0])[1].get("pricingExpression", {})
    tiers = expr.get("tieredRates") or []
    if not tiers:
        return None
    # A simple VM unit estimate uses a zero-start tier. A free tier or thresholded
    # pricing rule must not be mistaken for an unconditional hourly rate.
    zero_tiers = []
    for tier in tiers:
        try:
            start_amount = float(tier.get("startUsageAmount", 0))
        except (TypeError, ValueError):
            continue
        if start_amount == 0:
            zero_tiers.append(tier)
    if not zero_tiers:
        return None
    unit = zero_tiers[0].get("unitPrice", {})
    rate = float(unit.get("units", 0)) + float(unit.get("nanos", 0)) / 1_000_000_000
    usage_unit = str(expr.get("usageUnit", "")).lower()
    base_unit = str(expr.get("baseUnit", "")).lower()
    conversion = float(expr.get("baseUnitConversionFactor") or 1)
    if "hour" in usage_unit or usage_unit in {"h", "hr"} or usage_unit.endswith(".h"):
        return rate
    if "second" in usage_unit or usage_unit in {"s", "sec"} or usage_unit.endswith(".s"):
        return rate * 3600
    if "second" in base_unit and conversion >= 3500:
        return rate
    if "second" in base_unit and conversion <= 1:
        return rate * 3600
    return None


def _catalog_api_key() -> str:
    value = os.getenv("GOOGLE_CLOUD_BILLING_API_KEY", "").strip()
    if value:
        return value
    secret_path = "/run/secrets/google-cloud-billing-api-key"
    try:
        with open(secret_path, encoding="utf-8") as stream:
            return stream.read().strip()
    except OSError:
        return ""


def get_gcp_vm_price(region: str = "asia-south1", machine_type: str = "n2-standard-2", api_key: str | None = None, os_name: str = "Linux", profile: tuple | None = None) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    api_key = (api_key or _catalog_api_key()).strip()
    vcpus, memory = profile or MACHINE_PROFILE.get(machine_type, (None, None))
    if not api_key:
        return {"available": False, "provider": "GCP", "region": region, "sku": machine_type,
                "vcpus": vcpus, "memory_gib": memory, "os": os_name,
                "spec_source": "Curated GCP machine profile; live catalog pricing unavailable",
                "telemetry_status": "unavailable (GCP project is not connected)",
                "capacity_status": "not verified",
                "error": "GOOGLE_CLOUD_BILLING_API_KEY is not configured. Enable the Google Cloud Billing Catalog API and set the key before rebuilding/restarting IIPP. This public-pricing key does not connect a GCP account.", "retrieved_at": now}
    try:
        # Discover the Compute Engine service id from the catalog rather than assuming one.
        # Current catalog API: service discovery is paginated separately from SKU listing.
        token = None
        service_name = None
        services_scanned = 0
        # The catalog has many services. A small fixed page cap can miss Compute Engine
        # even with a valid API key, so request a large documented page and still follow
        # pagination tokens in case the service list is paginated.
        for _ in range(20):
            params = {"key": api_key, "pageSize": 5000}
            if token:
                params["pageToken"] = token
            url = BASE + "/services?" + urllib.parse.urlencode(params)
            data = _get_json(url)
            services = data.get("services", [])
            services_scanned += len(services)
            for service in services:
                display_name = str(service.get("displayName", "")).strip().casefold()
                service_id = str(service.get("serviceId", "")).strip()
                if display_name == "compute engine" or service_id == "6F81-5844-456A":
                    service_name = service.get("name") or ("services/" + service_id if service_id else None)
                    break
            if service_name:
                break
            token = data.get("nextPageToken")
            if not token:
                break
        if not service_name:
            return {"available": False, "provider": "GCP", "region": region, "sku": machine_type,
                    "vcpus": vcpus, "memory_gib": memory, "os": os_name,
                    "spec_source": "Curated GCP machine profile; live catalog specs/prices not verified",
                    "telemetry_status": "unavailable (GCP project is not connected)", "capacity_status": "not verified",
                    "error": f"Compute Engine service was not found after scanning {services_scanned} catalog service entries. Check that the Cloud Billing Catalog API is enabled, the API key is valid/restricted to cloudbilling.googleapis.com, and the deployment is using the latest code.", "retrieved_at": now}
        token = None
        windows_rates: list[tuple[float, dict[str, Any]]] = []
        cpu_rates: list[tuple[float, dict[str, Any]]] = []
        ram_rates: list[tuple[float, dict[str, Any]]] = []
        pages = 0
        while pages < 5:
            params = {"key": api_key, "pageSize": 5000, "currencyCode": "USD"}
            if token:
                params["pageToken"] = token
            # Use the documented v1 child-resource route: /v1/services/{serviceId}/skus.
            # The API returns both pricing and the regions to which each SKU applies.
            sku_endpoint = BASE + "/" + service_name.strip("/") + "/skus?"
            data = _get_json(sku_endpoint + urllib.parse.urlencode(params))
            pages += 1
            for sku in data.get("skus", []):
                regions = sku.get("serviceRegions", [])
                if region not in regions:
                    continue
                description = str(sku.get("description", "")).lower()
                category = sku.get("category") or {}
                usage_type = str(category.get("usageType", "")).lower()
                resource_family = str(category.get("resourceFamily", "")).lower()
                if usage_type not in {"ondemand", "on-demand"} or resource_family not in {"compute", "license", ""}:
                    continue
                if any(term in description for term in ("spot", "preemptible", "committed use", "commitment")):
                    continue
                if "windows server" in description and "sql" not in description:
                    rate = _unit_price_usd(sku)
                    if rate is not None and "core" in description:
                        windows_rates.append((rate, sku))
                if "n2 instance core" in description:
                    rate = _unit_price_usd(sku)
                    if rate is not None:
                        cpu_rates.append((rate, sku))
                elif "n2 instance ram" in description:
                    rate = _unit_price_usd(sku)
                    if rate is not None:
                        ram_rates.append((rate, sku))
            token = data.get("nextPageToken")
            if cpu_rates and ram_rates and (os_name != "Windows" or windows_rates):
                break
            if not token:
                break
        if not cpu_rates or not ram_rates:
            return {"available": False, "provider": "GCP", "region": region, "sku": machine_type,
                    "vcpus": vcpus, "memory_gib": memory, "os": os_name,
                    "spec_source": "Curated GCP machine profile; live price unavailable",
                    "telemetry_status": "unavailable (GCP project is not connected)", "capacity_status": "not verified",
                    "error": f"Could not find both regional N2 core and RAM hourly SKUs for {region}; missing pricing is not replaced with zero. Check the selected region and Cloud Billing Catalog API response.", "retrieved_at": now}
        # Some catalog pages include a broad APAC/EMEA/Americas SKU alongside a
        # localized city SKU. Prefer the city-specific price if present; otherwise
        # use a matching broad-region price. Remaining distinct rates stay ambiguous.
        cpu_rates = _prefer_specific_region_rates(cpu_rates, region)
        ram_rates = _prefer_specific_region_rates(ram_rates, region)
        cpu_unique = {round(rate, 12) for rate, _ in cpu_rates}
        ram_unique = {round(rate, 12) for rate, _ in ram_rates}
        if len(cpu_unique) != 1 or len(ram_unique) != 1:
            return {"available": False, "provider": "GCP", "region": region, "sku": machine_type,
                    "vcpus": vcpus, "memory_gib": memory, "os": os_name,
                    "spec_source": "Curated GCP machine profile; live price ambiguous",
                    "telemetry_status": "unavailable (GCP project is not connected)", "capacity_status": "not verified",
                    "error": "Multiple distinct on-demand N2 CPU/RAM rates matched; pricing is ambiguous and was not guessed.", "retrieved_at": now}
        cpu_rate, cpu_sku = cpu_rates[0]
        ram_rate, ram_sku = ram_rates[0]
        hourly = cpu_rate * int(vcpus or 2) + ram_rate * float(memory or 8.0)
        if os_name == "Windows":
            rates = _prefer_specific_region_rates(windows_rates, region)
            if len({rate for rate, _ in rates}) != 1:
                raise ValueError("Windows licence pricing unavailable or ambiguous")
            hourly += rates[0][0] * max(4, int(vcpus or 0))
        return {
            "available": True, "provider": "GCP", "region": region, "sku": machine_type,
            "vcpus": vcpus, "memory_gib": memory, "os": os_name,
            "hourly_price": round(hourly, 8), "price_currency": "USD",
            "monthly_compute_estimate": round(hourly * 730, 4),
            "monthly_storage_estimate": None, "monthly_network_estimate": None, "monthly_total_estimate": None,
            "price_type": "Google Cloud public catalog on-demand CPU + RAM estimate",
            "price_source": f"{BASE}/{service_name.strip('/')}/skus", "price_retrieved_at": now,
            "component_skus": {"cpu": cpu_sku.get("skuId"), "ram": ram_sku.get("skuId")},
            "telemetry_status": "unavailable (GCP project is not connected)",
            "capacity_status": "public regional SKUs found; regional machine availability and capacity not verified",
            "provisioning_enabled": False,
            "spec_source": "Curated N2 machine profile mapping; verify current machine type details before deployment",
            "warnings": ["Compute-only estimate combines CPU and RAM catalog rates × 730 hours.",
                         "Storage, network, OS licensing, taxes, committed-use discounts and negotiated prices excluded.",
                         "Catalog API access does not prove a GCP account/project is connected."],
        }
    except Exception as exc:
        # The API key is a query parameter for this public catalog. Keep request URLs
        # and low-level errors out of UI messages so keys cannot leak through logs.
        return {"available": False, "provider": "GCP", "region": region, "sku": machine_type,
                "vcpus": vcpus, "memory_gib": memory, "os": os_name,
                "spec_source": "Curated GCP machine profile; live catalog request failed",
                "telemetry_status": "unavailable (GCP project is not connected)", "capacity_status": "not verified",
                "error": f"Google Cloud Billing Catalog API request failed ({type(exc).__name__}); verify API enablement, key restrictions, network access and quota.", "retrieved_at": now}
