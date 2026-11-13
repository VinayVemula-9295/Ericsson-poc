"""Display an evidence-based, read-only cost and target summary for a saved plan."""
from __future__ import annotations

import math


def summary(record: dict) -> dict[str, object]:
    candidate = record.get("candidate") or {}
    request = record.get("requirements") or {}
    config = record.get("requested_configuration") or {}
    provider = str(candidate.get("provider") or record.get("provider") or "AWS")
    connection_id = record.get("connection_id") or record.get("aws_connection_id") or config.get("aws_connection_id")
    connection = None
    if connection_id:
        try:
            from provider_connections import get_connection
            connection = get_connection(str(connection_id))
        except Exception:
            connection = None
    metadata = (connection or {}).get("metadata") or {}
    identity = (record.get("target_identity") or record.get("target_account_id") or
                metadata.get("subscription_id") or metadata.get("project_id") or
                (connection or {}).get("account_id") or "Unavailable")
    identity_label = {"AWS": "Account", "Azure": "Subscription", "GCP": "Project"}.get(provider, "Identity")
    actual_identity = ((connection or {}).get("account_id") or metadata.get("account_id") if provider == "AWS" else
                       metadata.get("subscription_id") or (connection or {}).get("subscription_id") if provider == "Azure" else
                       metadata.get("project_id") or (connection or {}).get("project_id"))
    monthly = candidate.get("monthly_compute_estimate", config.get("monthly_compute_estimate"))
    if monthly is None and provider == "AWS":
        instance_type = candidate.get("sku") or config.get("instance_type")
        region = candidate.get("region") or config.get("region") or record.get("region")
        if instance_type and region:
            try:
                from aws_data import get_ec2_instance_on_demand_price
                platform = "Windows" if str(candidate.get("os") or config.get("os") or request.get("os") or "Linux").lower() == "windows" else "Linux/UNIX"
                price = get_ec2_instance_on_demand_price(str(instance_type), platform, "default", str(region))
                monthly = price.get("monthly_730h_usd") if price.get("available") else None
            except Exception:
                monthly = None
    try:
        monthly = float(monthly)
        if not math.isfinite(monthly) or monthly < 0:
            monthly = None
    except (TypeError, ValueError, OverflowError):
        monthly = None
    vcpus = candidate.get("vcpus", request.get("min_vcpus", config.get("min_vcpus")))
    memory = candidate.get("memory_gib", request.get("min_memory_gib", config.get("min_memory_gib")))
    specs = " / ".join((f"{vcpus} vCPU" if vcpus is not None else "vCPU unavailable",
                        f"{memory} GiB RAM" if memory is not None else "RAM unavailable"))
    name = (connection or {}).get("name") or metadata.get("subscription_name") or metadata.get("project_name")
    if not actual_identity or str(actual_identity) != str(identity):
        name = None
    return {
        "provider": provider,
        "identity_label": identity_label,
        "identity_name": str(name or "Name not recorded"),
        "identity": str(identity),
        "region": str(candidate.get("region") or config.get("region") or record.get("region") or "Unavailable"),
        "configuration_type": str(request.get("kind") or "VM"),
        "configuration": str(candidate.get("sku") or config.get("instance_type") or "Unavailable"),
        "specifications": specs,
        "monthly_compute_estimate": f"${monthly:,.2f} USD / month" if monthly is not None else "Unavailable",
        "price_available": monthly is not None,
    }


def render(record: dict) -> None:
    import streamlit as st
    details = summary(record)
    st.markdown("#### Estimated infrastructure cost")
    st.write({
        "Cloud provider": details["provider"],
        details["identity_label"]: details["identity_name"] + " · " + details["identity"],
        "Region": details["region"],
        "Configuration type": details["configuration_type"],
        "Configuration": details["configuration"],
        "Resource specifications": details["specifications"],
    })
    st.metric("Estimated monthly compute cost", details["monthly_compute_estimate"])
    st.caption("Estimate only. The available price covers compute; storage, networking, taxes, discounts, and other charges may not be included.")
    if not details["price_available"]:
        st.warning("Pricing is unavailable for this configuration. Review actual provider pricing before approving Apply.")
