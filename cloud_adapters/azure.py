"""Azure Retail Prices API adapter; public pricing only, no Azure account required."""
from __future__ import annotations
import json
import urllib.parse
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone
from typing import Any

ENDPOINT = "https://prices.azure.com/api/retail/prices"
SKU_PROFILE = {
    "Standard_D2s_v5": (2, 8.0),
    "Standard_F4s_v2": (4, 8.0),
    "Standard_D4s_v5": (4, 16.0),
    "Standard_D8s_v5": (8, 32.0),
    "Standard_D16s_v5": (16, 64.0),
    "Standard_D32s_v5": (32, 128.0),
}


def _get_json(url: str, timeout: int = 15) -> dict[str, Any]:
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


def get_azure_vm_price(region: str = "centralindia", sku: str = "Standard_D2s_v5", currency: str = "USD", os_name: str = "Linux", profile: tuple | None = None) -> dict[str, Any]:
    """Retrieve a public Linux consumption hourly rate; returns unavailable on ambiguity/missing data."""
    now = datetime.now(timezone.utc).isoformat()
    vcpus, memory = profile or SKU_PROFILE.get(sku, (None, None))
    # Filter values are quoted OData literals. SKU/region are caller-controlled but restricted below.
    if not region.replace("-", "").isalnum() or not sku.replace("_", "").isalnum():
        return {"available": False, "provider": "Azure", "error": "Invalid Azure region or SKU identifier."}
    query = f"serviceName eq 'Virtual Machines' and armRegionName eq '{region}' and armSkuName eq '{sku}' and priceType eq 'Consumption'"
    url = ENDPOINT + "?api-version=2023-01-01-preview&" + urllib.parse.urlencode({"$filter": query, "currencyCode": f"'{currency}'"})
    matched: list[dict[str, Any]] = []
    try:
        next_url: str | None = url
        pages = 0
        while next_url and pages < 5:
            data = _get_json(next_url)
            pages += 1
            for item in data.get("Items", []):
                if item.get("serviceName") != "Virtual Machines":
                    continue
                if item.get("armRegionName", "").lower() != region.lower() or item.get("armSkuName") != sku:
                    continue
                if item.get("priceType", item.get("type", "Consumption")) not in {"Consumption", "consumption"}:
                    continue
                unit = str(item.get("unitOfMeasure", "")).lower()
                meter = str(item.get("meterName", "")).lower()
                product = str(item.get("productName", "")).lower()
                if "hour" not in unit or "spot" in meter or "low priority" in meter:
                    continue
                # The POC compares Linux consumption; Windows and licence-specific rows are not equivalent.
                if any(term in product or term in meter for term in (
                    "sql server", "red hat", "rhel", "suse", "rocky linux",
                    "ubuntu pro", "low priority", "spot", "reservation", "license"
                )):
                    continue
                if ("windows" in product) != (os_name == "Windows"):
                    continue
                rate_value = item.get("retailPrice", item.get("unitPrice"))
                if rate_value is None:
                    continue
                try:
                    rate_value = float(rate_value)
                except (TypeError, ValueError):
                    continue
                if rate_value < 0:
                    continue
                matched.append(item)
            next_url = data.get("NextPageLink")
        if not matched:
            return {"available": False, "provider": "Azure", "sku": sku, "region": region,
                    "error": f"No standard Linux on-demand hourly retail price found for {sku} in {region}.", "retrieved_at": now,
                "hourly_price": None, "monthly_compute_estimate": None, "telemetry_status": "unavailable (Azure account not connected)",
                "capacity_status": "not verified", "provisioning_enabled": False, "warnings": []}
        # Prefer the primary meter row. When equivalent duplicate meters remain, do not silently select a random one.
        matched.sort(key=lambda x: (not bool(x.get("isPrimaryMeterRegion", False)), float(x.get("retailPrice", float("inf")))))
        primary = [x for x in matched if x.get("isPrimaryMeterRegion") is True]
        candidate_rows = primary or matched
        unique = {(float(x.get("retailPrice", x.get("unitPrice"))), x.get("currencyCode", currency), x.get("unitOfMeasure", "")) for x in candidate_rows}
        if len(unique) > 1:
            return {"available": False, "provider": "Azure", "sku": sku, "region": region,
                    "error": "Azure returned multiple distinct hourly meters for the selected VM SKU; refusing to guess which is equivalent Linux compute.", "retrieved_at": now}
        row = candidate_rows[0]
        hourly = float(row.get("retailPrice", row.get("unitPrice")))
        currency_code = row.get("currencyCode", currency)
        return {
            "available": True, "provider": "Azure", "region": region, "sku": sku,
            "vcpus": vcpus, "memory_gib": memory, "os": os_name,
            "hourly_price": hourly if "hour" in str(row.get("unitOfMeasure", "")).lower() else None,
            "price_currency": currency_code,
            "monthly_compute_estimate": round(hourly * 730, 4) if "hour" in str(row.get("unitOfMeasure", "")).lower() else None,
            "monthly_storage_estimate": None, "monthly_network_estimate": None, "monthly_total_estimate": None,
            "price_type": "Azure public retail consumption rate",
            "price_source": ENDPOINT, "price_retrieved_at": now,
            "meter_name": row.get("meterName"), "product_name": row.get("productName"),
            "unit_of_measure": row.get("unitOfMeasure"),
            "telemetry_status": "unavailable (Azure account not connected)",
            "capacity_status": "regional SKU offering only; real-time capacity not verified",
            "provisioning_enabled": False,
            "spec_source": "Curated Azure VM SKU POC mapping; not live subscription SKU inventory",
            "warnings": ["Compute-only estimate; disk, bandwidth, IP, taxes and negotiated discounts excluded.",
                         "Public retail pricing does not mean an Azure subscription is connected."],
        }
    except Exception as exc:
        return {"available": False, "provider": "Azure", "sku": sku, "region": region,
                "error": f"Azure Retail Prices API request failed: {type(exc).__name__}: {exc}", "retrieved_at": now}
