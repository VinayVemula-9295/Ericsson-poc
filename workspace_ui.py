"""Agent workspace presentation. Reads existing evidence; never queries or mutates clouds."""
from __future__ import annotations

from html import escape
from typing import Any

import streamlit as st


def _text(value: Any) -> str:
    return escape(str(value), quote=True)


def _evidence_card(title: str, status: str, description: str, tone: str = "neutral") -> None:
    st.markdown(
        f'<article class="decision-card"><div class="decision-card-top"><h4>{_text(title)}</h4>'
        f'<span class="workspace-badge {tone}">{_text(status)}</span></div>'
        f'<p>{_text(description)}</p></article>', unsafe_allow_html=True,
    )


def render_agent_intro() -> None:
    st.markdown('<div class="workspace-kicker">AGENT WORKSPACE</div>', unsafe_allow_html=True)
    st.title("Describe the outcome. Review the plan.")
    st.caption("Your intent becomes a reviewable change, backed by policy and workload evidence.")


def render_execution_methods() -> None:
    st.markdown("#### Provisioning layer")
    st.markdown(
        '<div class="execution-methods"><article class="execution-method active">'
        '<span class="workspace-badge good">Supported · AWS</span>'
        '<h4>Infrastructure as Code</h4><p>Terraform saved plan → human review → approved execution</p></article>'
        '<article class="execution-method"><span class="workspace-badge neutral">Planned</span>'
        '<h4>Kubernetes</h4><p>Cluster workload provisioning is not implemented.</p></article></div>',
        unsafe_allow_html=True,
    )
    st.button("Kubernetes execution · planned", disabled=True, key="agent-kubernetes-planned")
    st.caption("Every infrastructure write requires policy validation and explicit human approval.")


def render_decision_sidebar(provider: str, connection_id: str | None, connection: dict | None,
                            comparison: dict | None, explanation: dict | None,
                            audit_records: list[dict]) -> None:
    with st.container(border=True, key="agent-evidence"):
        st.markdown("#### Decision evidence")
        st.caption("Policy first. AI second.")
        _evidence_card("Policy rules", "Required before plan",
                       "Account, region, instance family, environment, and TTL are validated when preparing a plan. A recommendation is not policy approval.")
        if not comparison:
            _evidence_card("AI decision engine", "Awaiting request",
                           "Submit requirements to compare options. AI explains the deterministic result and cannot authorize execution.", "purple")
        elif explanation and explanation.get("error"):
            _evidence_card("AI decision engine", "Fallback",
                           "AI explanation was unavailable. The deterministic comparison remains available.")
        elif explanation:
            _evidence_card("AI decision engine", "Advisory",
                           f"{explanation.get('provider', 'Configured model')} · Read-only explanation of hardware fit and ranking.", "purple")
        else:
            _evidence_card("AI decision engine", "Unavailable", "No AI explanation is available for this comparison.")

        # Only reuse inventory for the exact selected connection. Do not fetch on render.
        inventory = st.session_state.get("iipp-live-inventory-result") or {}
        same_connection = bool(connection_id and inventory.get("connection_id") == connection_id)
        if same_connection and inventory.get("ok"):
            _evidence_card("Real-time telemetry", "Last inventory read",
                           f"{provider} · Inventory checked {inventory.get('checked_at') or 'at an unavailable timestamp'}. Telemetry contains the workload metrics. Refresh in Telemetry for current evidence.")
        elif same_connection:
            _evidence_card("Real-time telemetry", "Unavailable",
                           "The latest selected-account inventory read failed. Refresh in Telemetry; no sample values are substituted.")
        else:
            _evidence_card("Real-time telemetry", "Not loaded",
                           "Open Telemetry to fetch selected-account inventory and available workload metrics. No live telemetry has been loaded for this context.")
        if connection:
            st.caption(f"Selected context: {provider} · {connection.get('name', 'Connected account')}")
        else:
            st.caption(f"{provider} is not connected. Use Configure to add a verified account.")
        if comparison:
            comparison_connection = st.session_state.get("agent-infra-aws-connection-id")
            if comparison_connection and comparison_connection != connection_id:
                st.caption("The comparison uses a different AWS connection from the current context. Verify the target account in the plan form.")
            rows = [row for row in comparison.get("source_options", [])
                    if row.get("provider") == "AWS" and row.get("available") is True
                    and row.get("price_retrieved_at")]
            if rows:
                st.caption("AWS price fetched (UTC): " + max(str(row["price_retrieved_at"]) for row in rows))
            else:
                st.caption("AWS price evidence unavailable. No sample prices are used.")
        with st.expander("How decisions are gated"):
            st.caption("A regional offering does not prove quota or spare capacity. The execution service validates policy and the exact saved plan. Only an authorized reviewer can approve apply or destroy.")

    with st.container(border=True, key="agent-cloud-coverage"):
        st.markdown("#### Cloud targets")
        _evidence_card("AWS", "Terraform supported", "Configuration and inventory · reviewed EC2 demo stack", "good")
        _evidence_card("Azure", "Inventory only", "Configuration and inventory · provisioning unavailable")
        _evidence_card("Google Cloud", "Inventory only", "Configuration and inventory · provisioning unavailable")

    with st.container(border=True, key="agent-audit"):
        st.markdown("#### Audit trail")
        if not audit_records:
            st.caption("No infrastructure activity recorded yet. Plan and approval events will appear here.")
        for record in audit_records:
            # Render a strict field selection, never raw approval records or credentials.
            timestamp = record.get("timestamp") or record.get("completed_at") or record.get("updated_at") or "Time unavailable"
            action = record.get("action") or "Infrastructure activity"
            status = record.get("status") or "Recorded"
            st.markdown(
                f'<div class="workspace-audit-row"><time>{_text(timestamp)}</time>'
                f'<strong>{_text(action)}</strong><span>{_text(status)}</span></div>', unsafe_allow_html=True,
            )
        st.caption("Recent platform activity · timestamps as recorded. Manage resource lifecycle and destroy reviews in Deployment.")
