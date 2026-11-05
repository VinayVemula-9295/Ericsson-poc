"""Normalized provider pricing/connectivity data models; unavailable values remain None."""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
import math
from typing import Any


def _number_or_none(value: Any, *, positive: bool = False) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number < 0):
        return None
    return number


@dataclass
class CloudOption:
    """Stable shape shared by AWS, Azure and GCP public-price adapters."""
    provider: str
    region: str
    service_type: str
    sku: str
    vcpus: int | None
    memory_gib: float | None
    os: str
    hourly_price: float | None
    price_currency: str | None
    monthly_compute_estimate: float | None
    monthly_storage_estimate: float | None = None
    monthly_network_estimate: float | None = None
    monthly_total_estimate: float | None = None
    price_type: str = "public on-demand consumption price"
    price_source: str | None = None
    price_retrieved_at: str | None = None
    telemetry_status: str = "unavailable"
    capacity_status: str = "not verified"
    provisioning_enabled: bool = False
    spec_source: str | None = None
    warnings: list[str] = field(default_factory=list)
    available: bool = False
    error: str | None = None
    retrieved_at: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "CloudOption":
        required_fields = {"provider", "region", "service_type", "sku", "vcpus", "memory_gib", "os",
                           "hourly_price", "price_currency", "monthly_compute_estimate", "monthly_storage_estimate",
                           "monthly_network_estimate", "monthly_total_estimate", "price_type", "price_source",
                           "price_retrieved_at", "telemetry_status", "capacity_status", "provisioning_enabled",
                           "spec_source", "warnings", "available", "error", "retrieved_at"}
        vcpus_raw = raw.get("vcpus")
        vcpus = int(vcpus_raw) if isinstance(vcpus_raw, (int, float)) and not isinstance(vcpus_raw, bool) and float(vcpus_raw).is_integer() and vcpus_raw >= 0 else None
        extras = {key: value for key, value in raw.items() if key not in required_fields}
        currency = raw.get("price_currency")
        normalized_currency = str(currency).upper() if currency else None
        raw_warnings = raw.get("warnings")
        if isinstance(raw_warnings, (list, tuple)):
            warnings = [str(item) for item in raw_warnings if str(item).strip()]
        elif isinstance(raw_warnings, str) and raw_warnings.strip():
            warnings = [raw_warnings.strip()]
        else:
            warnings = []
        return cls(
            provider=str(raw.get("provider") or "Unknown"), region=str(raw.get("region") or "unknown"),
            service_type=str(raw.get("service_type") or "Linux VM compute"), sku=str(raw.get("sku") or "unknown"),
            vcpus=vcpus, memory_gib=_number_or_none(raw.get("memory_gib"), positive=True),
            os=str(raw.get("os") or "unknown"), hourly_price=_number_or_none(raw.get("hourly_price"), positive=True),
            price_currency=normalized_currency,
            monthly_compute_estimate=_number_or_none(raw.get("monthly_compute_estimate"), positive=True),
            monthly_storage_estimate=_number_or_none(raw.get("monthly_storage_estimate"), positive=True),
            monthly_network_estimate=_number_or_none(raw.get("monthly_network_estimate"), positive=True),
            monthly_total_estimate=_number_or_none(raw.get("monthly_total_estimate"), positive=True),
            price_type=str(raw.get("price_type") or "public on-demand consumption price"),
            price_source=raw.get("price_source"), price_retrieved_at=raw.get("price_retrieved_at"),
            telemetry_status=str(raw.get("telemetry_status") or "unavailable"),
            capacity_status=str(raw.get("capacity_status") or "not verified"),
            provisioning_enabled=raw.get("provisioning_enabled") is True, spec_source=raw.get("spec_source"),
            warnings=warnings, available=raw.get("available") is True,
            error=str(raw["error"]) if raw.get("error") else None, retrieved_at=raw.get("retrieved_at"), extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        extras = result.pop("extras", {})
        # Preserve provider-specific diagnostic details without replacing normalized fields.
        for key, value in extras.items():
            result.setdefault(key, value)
        return result


def normalize_cloud_option(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a consistent serializable object; never turn missing cost/spec data into zero."""
    return CloudOption.from_mapping(raw).to_dict()
