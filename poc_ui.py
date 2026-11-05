"""Extended POC request, evidence, comparison and configuration workflow."""
from __future__ import annotations
import streamlit as st
from cloud_adapters.aws import discover_aws_vm_options
from cloud_adapters.azure import get_azure_vm_price
from cloud_adapters.gcp import get_gcp_vm_price
from provider_connections import get_aws_session
from poc_catalog import profiles

@st.cache_data(ttl=3600, show_spinner=False)
def catalogue_options(provider, region, zone, os_name, cpu, memory, connection_id):
    if provider == "AWS":
        rows = discover_aws_vm_options(region, cpu, memory,
            session=get_aws_session(connection_id, purpose="inventory") if connection_id else None, os_name=os_name)
        # Live discovery already checks architecture and VM compatibility. Keep
        # every standard CPU profile, including exact matches from new families.
        return [row for row in rows if not row.get("accelerated")]
    if connection_id:
        specs = profiles(provider, connection_id, region, zone)
    else:
        from cloud_comparison import AZURE_POC_PROFILES, GCP_POC_PROFILES
        mapping = AZURE_POC_PROFILES if provider == "Azure" else GCP_POC_PROFILES
        specs = [{"sku": sku, "vcpus": c, "memory_gib": m, "spec_source": "Curated POC reference; connect provider for live specifications"} for sku, c, m in mapping]
    from recommendation_engine import shortlist_with_upgrades
    specs = sorted(specs, key=lambda x: (x["vcpus"] < cpu or x["memory_gib"] < memory,
                    abs(x["vcpus"] - cpu)/cpu + abs(x["memory_gib"] - memory)/memory))
    # Only N2 pricing is supported; other families must not crowd out priced upgrades.
    if provider == "GCP":
        specs = [s for s in specs if s["sku"].startswith("n2-")]
    specs = shortlist_with_upgrades(specs, cpu, memory, 9)
    rows = []
    for spec in specs:
        if provider == "Azure":
            row = get_azure_vm_price(region, spec["sku"], os_name=os_name, profile=(spec["vcpus"], spec["memory_gib"]))
        elif spec["sku"].startswith("n2-"):
            row = get_gcp_vm_price(region, spec["sku"], os_name=os_name, profile=(spec["vcpus"], spec["memory_gib"]))
        else:
            row = {"available": False, "error": "Pricing for this machine family has not been validated"}
        row.update(spec, provider=provider, region=region, os=os_name)
        rows.append(row)
    return rows

def render():
    """Compatibility entry point for the simplified Agent workflow."""
    from agent_flow_ui import render as render_flow
    render_flow()
