"""Presentation-only connected cloud comparison; demo data never enters live state."""
from html import escape

import streamlit as st


def comparison_rows(connections, inventory, ec2_costs):
    aws = [row for row in connections
           if row.get("provider") == "AWS" and row.get("connection_status") == "CONNECTED"]
    available = inventory.get("available", False)
    cost = ec2_costs.get("monthly_run_rate") if ec2_costs.get("available") else None
    return [
        {"Cloud": "AWS", "Status": "Connected" if aws else ("Controller live" if available else "Unavailable"),
         "Account": ", ".join(str(row.get("name") or row.get("account_id") or "Verified account") for row in aws) or "Controller account",
         "Region": inventory.get("region") or "Unavailable",
         "Running compute": int(inventory.get("count") or 0) if available else None,
         "Monthly compute (USD)": float(cost) if cost is not None else None,
         "Evidence": "Live inventory · estimated compute" if available else "Unavailable",
         "Checked": inventory.get("checked_at") or "Unavailable"},
        {"Cloud": "Microsoft Azure", "Status": "Connected · Demo", "Account": "Azure Demo Subscription",
         "Region": "centralindia", "Running compute": 8, "Monthly compute (USD)": 286.40,
         "Evidence": "Sample data", "Checked": "Demo snapshot"},
        {"Cloud": "Google Cloud", "Status": "Connected · Demo", "Account": "GCP Demo Project",
         "Region": "asia-south1", "Running compute": 6, "Monthly compute (USD)": 214.80,
         "Evidence": "Sample data", "Checked": "Demo snapshot"},
    ]


def render_comparison(connections, inventory, ec2_costs):
    rows = comparison_rows(connections, inventory, ec2_costs)
    st.caption("MULTI-CLOUD OVERVIEW")
    st.caption("AWS uses current controller evidence. Azure and Google Cloud are illustrative connected accounts with sample values.")
    for col, row, color, mark in zip(st.columns(3), rows,
                                     ["#c77813", "#0078d4", "#4285f4"], ["AWS", "AZ", "GC"]):
        with col, st.container(border=True):
            status = escape(row["Status"])
            st.markdown(
                f'<div class="cloud-card-heading"><span class="cloud-mark" style="color:{color};border-color:{color}">{mark}</span>'
                f'<strong>{escape(row["Cloud"])}</strong></div>'
                f'<span class="connection-pill">{status}</span>', unsafe_allow_html=True)
            st.markdown(f'**{row["Account"]}**')
            st.caption(row["Region"])
            st.metric("Running compute resources", row["Running compute"] if row["Running compute"] is not None else "N/A")
            monthly = row["Monthly compute (USD)"]
            st.metric("Estimated compute / month", f"USD {monthly:,.2f}" if monthly is not None else "N/A")
            st.caption(row["Evidence"])
            st.caption(row["Checked"])
    st.markdown("#### Cloud account comparison")
    st.dataframe(rows, hide_index=True, width="stretch")
    st.caption("Monthly compute excludes storage, networking, tax and discounts. AWS uses a 730-hour estimate; Azure/GCP amounts are samples.")
    if not inventory.get("available"):
        st.warning("AWS inventory unavailable: " + str(inventory.get("error") or "The API did not return inventory."))
    elif not ec2_costs.get("available"):
        st.caption("AWS compute estimate unavailable: " + str(ec2_costs.get("error") or "No price was returned."))
