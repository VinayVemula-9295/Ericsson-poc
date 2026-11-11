from __future__ import annotations

import hmac
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Docker supplies its writable /data volume. Windows local runs need a
# project-local directory instead of the protected C:\data path.
os.environ.setdefault(
    "DATA_DIR",
    "/data" if os.name != "nt" else str(Path(__file__).resolve().parent / ".local-data"),
)

from agent import invoke_agent, sanitize_agent_answer, explain_comparison_evidence
from aws_data import (
    clear_price_cache,
    discover_instance_type_options,
    get_metric_series,
    get_monthly_cost_summary,
    get_running_ec2_cost_estimate,
    get_target_instance_details,
    list_running_instances,
)
from cloud_comparison import get_provider_options
from recommendation_engine import compare_options
from instance_matching import exact_match_summary, rank_candidate_options
from cloud_adapters.azure import get_azure_vm_price
from cloud_adapters.gcp import get_gcp_vm_price
from infrastructure_request import parse_infrastructure_request
from trend import evaluate_trend, get_trend_diagnostics, start_post_change_cooldown
from provider_connections import list_connections, get_connection, configured_inventory, configured_aws_metrics
from provider_ui import render_provider_configuration
from workspace_ui import render_agent_intro, render_decision_sidebar, render_execution_methods

PRODUCT_NAME = "Intelligent Provisioning Platform"
st.set_page_config(page_title=PRODUCT_NAME, layout="wide", initial_sidebar_state="auto")

from shell_ui import require_demo_login, brand, sidebar_profile, header_profile
st.markdown('<style>' + (Path(__file__).parent / 'assets' / 'platform.css').read_text(encoding='utf-8') + '</style>', unsafe_allow_html=True)
require_demo_login()

REGION = os.getenv("AWS_REGION", "ap-south-1")
TARGET_INSTANCE_ID = os.getenv("TARGET_INSTANCE_ID", "")
DATA_DIR = Path(os.environ["DATA_DIR"])
DATA_DIR.mkdir(parents=True, exist_ok=True)
TREND_EVENT_PATH = DATA_DIR / "latest_cpu_trend_event.json"
LATEST_PLAN_PATH = DATA_DIR / "latest_infrastructure_plan.json"
APPROVAL_AUDIT_PATH = DATA_DIR / "approval_audit.jsonl"
TREND_STATE_PATH = DATA_DIR / "workload_trend_state.json"
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "innominds-logo.png"

st.markdown("""
<style>
[data-testid="stMainBlockContainer"] {
    padding: 5rem 2rem 3rem; max-width: 1320px; min-width: 0;
}
h1, h2, h3, h4 {letter-spacing: -.025em; font-weight: 600;}
div[data-testid="stMetric"] {border: 1px solid var(--secondary-background-color); padding: 12px 16px; border-radius: 6px;}
.small-note {color: var(--text-color); opacity: .75; font-size: .86rem;}
[data-testid="stSidebarUserContent"] {padding: 1.5rem 1rem;}
[data-testid="stSidebar"] [data-testid="stImage"] img {max-width: 100%; height: auto;}
.platform-title {font-size: 1.6rem; font-weight: 700; line-height: 1.4; overflow-wrap: anywhere;}
#MainMenu, [data-testid="stMainMenu"] {display: none;}
.st-key-iipp-global-topbar [data-testid="stCaptionContainer"] {overflow-wrap: anywhere;}
.st-key-iipp-global-topbar [data-testid="stColumn"] {min-width: 0;}

.st-key-iipp-global-topbar {border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px 22px; margin-bottom: 24px; background: #fff; color: #142d4e;}
.st-key-iipp-global-topbar .platform-title {border-left: 1px solid #dbe4ef; padding-left: 22px; font-size: 1.6rem; font-weight: 700; line-height: 1.4;}
.st-key-iipp-global-topbar [data-testid="stCaptionContainer"] {font-size: .7rem; letter-spacing: .1em; color: #64748b;}
.cloud-card-heading {display: flex; align-items: center; gap: 12px; margin-bottom: 18px; font-size: 1.15rem;}
.cloud-mark {display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 40px; border: 1px solid; border-radius: 10px; font-size: .8rem; font-weight: 700;}
.connection-pill {display: inline-block; border-radius: 20px; background: #e8f7ef; color: #237650; padding: 5px 12px; font-size: .8rem; margin-bottom: 16px;}

/* Scope the central form so account and approval forms retain their layout. */
div[data-testid="stForm"]:has([aria-label="Infrastructure requirements"]) {
    width: 100%; max-width: 760px; box-sizing: border-box; margin: .5rem auto 1.5rem;
    padding: 1.25rem; border: 1px solid var(--secondary-background-color); border-radius: 8px;
}
div[data-testid="stForm"]:has([aria-label="Infrastructure requirements"]) textarea {
    font-size: 1rem; line-height: 1.6;
}
@media (max-width: 1100px) {
    .st-key-iipp-global-topbar [data-testid="stHorizontalBlock"] {flex-direction: column; align-items: stretch;}
    .st-key-iipp-global-topbar [data-testid="stColumn"] {width: 100%; flex: 1 1 auto;}
}
@media (max-width: 640px) {
    [data-testid="stMainBlockContainer"] {padding: 4.5rem 1rem 2rem;}
    .platform-title, .st-key-iipp-global-topbar .platform-title {font-size: 1.25rem; font-weight: 700;}
    div[data-testid="stForm"]:has([aria-label="Infrastructure requirements"]) {padding: .75rem;}
}

/* Reference navigation, selectors and composer. Keep controls native and accessible. */
[data-testid="stSidebar"] {background: #fff; border-right: 1px solid #e2e8f0;}
.sidebar-product {color:#142d4e; font-size:1.6rem; font-weight:700; line-height:1.4; overflow-wrap:anywhere; margin:12px 0 26px;}
@media (max-width:640px) {.sidebar-product {font-size:1.25rem;}}
[data-testid="stSidebar"] [data-testid="stRadio"] label {padding:18px 16px; margin:4px 0; border-radius:14px; color:#142d4e; font-size:1.15rem; width:100%;}
[data-testid="stSidebar"] [data-testid="stRadio"] label:has(input:checked) {background:#e9f0f7; box-shadow:inset 4px 0 #345d80; font-weight:700; color:#345d80;}
[data-testid="stSidebar"] [data-testid="stRadio"] [role="radiogroup"] {width:100%; align-items:stretch;}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {display:flex; width:100%!important; box-sizing:border-box;}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child {display:none;}
[data-testid="stSidebar"] [data-testid="stRadioOption"] p {font-size:1.15rem; line-height:1.6;}
.st-key-iipp-global-topbar [data-testid="stWidgetLabel"] {color:#708299;}
.st-key-iipp-global-topbar [data-baseweb="select"] > div {background:#fff; border-color:#dbe4ef; border-radius:12px; min-height:56px;}
.st-key-iipp-global-topbar .react-aria-ComboBox > [role="group"] {background:#fff; border:1px solid #dbe4ef; border-radius:12px; min-height:56px; align-items:center;}
.st-key-iipp-global-topbar [role="combobox"] {background:transparent; border:0; font-size:1rem;}
.st-key-iipp-global-configure button, .st-key-agent-composer button, .st-key-apply-terraform-plan button {background:#345d80; border-color:#345d80; color:#fff;}
.st-key-iipp-global-configure button {min-height:56px; border-radius:12px;}
.st-key-agent-composer {background:#fff; border:1px solid #cbd5e1; border-radius:32px; padding:18px 24px; margin-top:4px; box-shadow:0 5px 20px #17344c05;}
.st-key-agent-composer textarea {background:#fff; border:0; box-shadow:none;}
.st-key-agent-composer button {border-radius:28px;}
</style>
""", unsafe_allow_html=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def _append_audit(record: dict[str, Any]) -> None:
    APPROVAL_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPROVAL_AUDIT_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, default=str) + "\n")


def _display_value(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "N/A"


def _money(value: float | None, currency: str = "USD", decimals: int = 4) -> str:
    if value is None:
        return "N/A"
    return f"{currency} {value:,.{decimals}f}"


def _approval_token() -> str:
    """Prefer a read-only mounted secret file; environment is a backward-compatible fallback."""
    direct = os.getenv("APPROVAL_TOKEN", "")
    if direct:
        return direct
    for candidate in (Path("/run/secrets/cloudpilot-approval-token"), Path.home() / ".cloudpilot-approval-token"):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            pass
    return ""


def _render_trend_progress(instance_type: str | None = None) -> None:
    """Show the datapoint counters and explain min-size / freshness blockers."""
    status = get_trend_diagnostics(TREND_STATE_PATH)
    if instance_type and status.get("instance_type") and status.get("instance_type") != instance_type:
        st.caption(f"Threshold counters are being re-established for the current EC2 type ({instance_type}).")
        return
    left, right = st.columns(2)
    left.metric("Scale-up datapoints", f"{status.get('high_streak', 0)}/{status.get('high_required', 2)}")
    right.metric("Scale-down datapoints", f"{status.get('low_streak', 0)}/{status.get('low_required', 3)}")
    if status.get("scale_down_available") is False and instance_type:
        st.caption(f"Scale-down target unavailable: `{instance_type}` is the smallest allowlisted type in this POC. A scale-down can be considered only after the instance is running a larger type.")
    message = status.get("message")
    if message:
        st.caption(message)


@st.cache_data(ttl=60, show_spinner=False)
def _cached_inventory() -> dict[str, Any]:
    return list_running_instances()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_costs() -> dict[str, Any]:
    return get_monthly_cost_summary()


@st.cache_data(ttl=300, show_spinner=False)
def _cached_ec2_costs(instances: list[dict[str, Any]]) -> dict[str, Any]:
    return get_running_ec2_cost_estimate(instances)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_instance_type_options(instance_id: str) -> dict[str, Any]:
    return discover_instance_type_options(instance_id)


def _metric_dataframe(series: dict[str, Any]) -> pd.DataFrame:
    """Build a normalized dataframe while keeping percentage and byte metrics distinct."""
    rows = []
    for label, info in series.get("metrics", {}).items():
        unit = info.get("unit", "Percent")
        for point in info.get("points", []):
            value = point.get("value", point.get("average"))
            if value is None:
                continue
            if unit == "Bytes per 60-second period":
                value = float(value) / (1024 * 1024)
                display_unit = "MiB per 60-second period"
            else:
                display_unit = "Percent"
            rows.append({"Time (UTC)": pd.to_datetime(point["timestamp"]), "Value": value,
                         "Metric": label, "Unit": display_unit})
    return pd.DataFrame(rows)


def _retire_cleared_event(metrics: dict[str, Any]) -> bool:
    """Hide an unapproved plan after its CPU condition has clearly cleared.

    Scale-up plans retire after two distinct samples below 70%; scale-down plans
    retire after two samples above 25%. Approved actions remain in the audit/history.
    """
    event = _read_json(TREND_EVENT_PATH)
    if not event or event.get("event_type") != "sustained_cpu_trend":
        return False
    prior = _read_json(LATEST_PLAN_PATH) or {}
    if prior.get("approval", {}).get("status") == "APPLIED":
        return False

    points = sorted(metrics.get("metrics", {}).get("CPU", {}).get("points", []),
                    key=lambda point: point.get("timestamp", ""))
    if len(points) < 2:
        return False
    try:
        recent = [float(point.get("average")) for point in points[-2:]]
    except (TypeError, ValueError):
        return False
    signal = event.get("trend_signal")
    cleared = (signal == "scale_up" and all(value < 70 for value in recent)) or (
        signal == "scale_down" and all(value > 25 for value in recent)
    )
    if not cleared:
        return False

    TREND_EVENT_PATH.unlink(missing_ok=True)
    if prior.get("event_id") == event.get("event_id"):
        LATEST_PLAN_PATH.unlink(missing_ok=True)
    return True


def _last_successful_resize_audit() -> dict[str, Any] | None:
    """Return the most recent successful resize record from the private host audit log."""
    try:
        if not APPROVAL_AUDIT_PATH.exists():
            return None
        for line in reversed(APPROVAL_AUDIT_PATH.read_text(encoding="utf-8").splitlines()):
            try:
                record = json.loads(line)
            except Exception:
                continue
            if record.get("status") == "APPLIED" and record.get("new_instance_type"):
                return record
    except Exception:
        return None
    return None


def _clear_applied_change_state(current_instance_type: str | None = None) -> bool:
    """Clear stale post-approval UI state and recover a cooldown after older POC builds."""
    prior = _read_json(LATEST_PLAN_PATH) or {}
    event = _read_json(TREND_EVENT_PATH) or {}
    approval = prior.get("approval", {}) if isinstance(prior, dict) else {}
    plan_is_applied = approval.get("status") == "APPLIED"

    # v5 could write a new reverse-direction event over the prior APPLIED plan
    # before it was cleared. Recover from the audit record for a recent resize.
    audit = _last_successful_resize_audit()
    recent_resize = False
    if audit:
        try:
            completed = datetime.fromisoformat(str(audit.get("completed_at", "")).replace("Z", "+00:00"))
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - completed
            actual_type = current_instance_type or audit.get("new_instance_type")
            recent_resize = (
                timedelta(0) <= age <= timedelta(minutes=15)
                and actual_type == audit.get("new_instance_type")
                and bool(event)
            )
        except (TypeError, ValueError):
            recent_resize = False

    if not plan_is_applied and not recent_resize:
        return False

    new_type = current_instance_type or (audit or {}).get("new_instance_type") or approval.get("new_instance_type") or "unknown"
    start_post_change_cooldown(TREND_STATE_PATH, new_type)
    TREND_EVENT_PATH.unlink(missing_ok=True)
    LATEST_PLAN_PATH.unlink(missing_ok=True)
    return True


def _render_cloud_account_summary(inventory: dict[str, Any], ec2_costs: dict[str, Any]) -> None:
    """Render live AWS evidence beside presentation-only cloud demo accounts."""
    from telemetry_ui import render_comparison
    heading, refresh = st.columns([5, 1], vertical_alignment="center")
    heading.subheader("Connected accounts")
    if refresh.button("Refresh", key="refresh-cost-data", use_container_width=True):
        st.cache_data.clear()
        clear_price_cache()
        st.rerun()
    render_comparison(list_connections(), inventory, ec2_costs)


def _active_cloud_selection() -> tuple[str, str | None, dict[str, Any] | None]:
    """Return the provider/account chosen in the global top bar."""
    provider = str(st.session_state.get("iipp_active_provider", "AWS"))
    connection_id = st.session_state.get("iipp_active_connection_id")
    connection = get_connection(str(connection_id)) if connection_id else None
    if connection and connection.get("provider") != provider:
        connection = None
        connection_id = None
    return provider, str(connection_id) if connection_id else None, connection


def _render_global_topbar() -> None:
    """Shared cloud and account selectors for every workspace."""
    connected = list_connections()
    requested_provider = st.session_state.pop("iipp_provider_after_save", None)
    if requested_provider in {"AWS", "Azure", "GCP"}:
        st.session_state["iipp_active_provider"] = requested_provider
    requested_connection_id = st.session_state.pop("iipp_connection_after_save", None) or st.session_state.get('iipp_active_connection_id')

    with st.container(border=True, key="iipp-global-topbar"):
        provider_col, account_col, action_col = st.columns([3, 6, 1.2], vertical_alignment="bottom")
        with provider_col:
            providers = ['AWS', 'Azure', 'GCP']
            provider = st.selectbox("Cloud provider", providers,
                                    index=providers.index(st.session_state.get('iipp_active_provider', 'AWS')),
                                    key="cloud-provider-widget")
            st.session_state['iipp_active_provider'] = provider

        provider_rows = [row for row in connected if row.get("provider") == provider]
        provider_rows.sort(key=lambda row: (row.get("connection_status") != "CONNECTED", row.get("name", "")))
        account_labels: dict[str, str] = {}
        for row in provider_rows:
            meta = row.get("metadata", {})
            account_id = (row.get("account_id") or row.get("subscription_id") or meta.get("subscription_id")
                          or row.get("project_id") or meta.get("project_id") or "identity pending")
            label = f"{row.get('name')} · {account_id} · {row.get('connection_status', 'UNKNOWN')}"
            account_labels[label] = row.get("connection_id", "")
        account_options = list(account_labels) or [f"No {provider} connection · use Configure"]
        account_key = f"iipp_active_account_{provider.lower()}"
        if requested_connection_id:
            requested_label = next((label for label, cid in account_labels.items() if cid == requested_connection_id), None)
            if requested_label:
                st.session_state[account_key] = requested_label
        if st.session_state.get(account_key) not in account_options:
            connected_label = next((label for label in account_options if label.endswith("CONNECTED")), account_options[0])
            st.session_state[account_key] = connected_label

        with account_col:
            selected_label = st.selectbox(
                "Account / Subscription / Project", account_options, key=account_key,
                help="This selection is shared across workspaces.",
            )
        selected_id = account_labels.get(selected_label)
        selected_row = next((row for row in provider_rows if row.get("connection_id") == selected_id), None)
        st.session_state["iipp_active_connection_id"] = (
            selected_id if selected_row and selected_row.get("connection_status") == "CONNECTED" else None
        )
        if selected_row and selected_row.get("connection_status") != "CONNECTED":
            st.warning("This connection is saved but not verified. Test it in Configure before using live account data.")


@st.dialog("Control Center", width="large")
def _show_provider_config_dialog() -> None:
    render_provider_configuration()


@st.dialog("Cloud connection verified", width="small")
def _show_connection_success_dialog() -> None:
    message = st.session_state.get("iipp_connection_success_message", "Your cloud connection has been verified and saved.")
    st.success(message)
    st.caption("The selected account is now available in the shared Telemetry and Agent cloud selector.")
    if st.button("Continue", type="primary", use_container_width=True, key="iipp-success-dialog-close"):
        st.session_state.pop("iipp_connection_success_message", None)
        st.rerun()


def render_configured_provider_inventory(compact: bool = False) -> None:
    """Show live inventory for the account selected in the shared top bar."""
    provider, selected_id, connection = _active_cloud_selection()
    st.subheader(f"Live {provider} account inventory")
    if not selected_id or not connection:
        st.info(f"No verified {provider} account is selected. Use Configure to connect an account, then choose it in the top bar.")
        return
    selected_label = f"{connection.get('name')} · {connection.get('account_id') or connection.get('subscription_id') or connection.get('project_id') or 'connected'}"
    st.caption(f"Selected account: {selected_label}")
    cached = st.session_state.get("iipp-live-inventory-result") or {}
    refresh_clicked = st.button("Refresh selected account data", key=f"iipp-refresh-connected-inventory-{selected_id}")
    if refresh_clicked or cached.get("connection_id") != selected_id:
        with st.spinner("Querying the selected cloud account through its configured identity…"):
            result = configured_inventory(selected_id)
        result["connection_id"] = selected_id
        st.session_state["iipp-live-inventory-result"] = result
        st.session_state["iipp-live-inventory-connection-id"] = selected_id
    result = st.session_state.get("iipp-live-inventory-result")
    if result and st.session_state.get("iipp-live-inventory-connection-id") == selected_id:
        if result.get("ok"):
            c1, c2, c3 = st.columns(3)
            c1.metric("Provider", result.get("provider", "N/A"))
            c2.metric("Sampled resources", result.get("sampled_resources", result.get("sampled_instances", 0)))
            c3.metric("Region", result.get("region") or "Account-wide")
            st.caption(f"Identity: {result.get('identity') or result.get('subscription_name') or result.get('service_account_email') or 'verified'} · Source: {result.get('inventory_source')} · Checked: {result.get('checked_at')}")
            resources = result.get("resources", [])
            if resources:
                st.dataframe(pd.DataFrame(resources), hide_index=True, width="stretch")
            else:
                st.info("The live API call succeeded, but no resources were returned in the sampled result.")
            if result.get("provider") == "AWS" and not compact:
                ec2_rows = [item for item in resources if item.get("instance_id")]
                if ec2_rows:
                    options = {f"{item.get('name', '(untagged)')} · {item.get('instance_id')} · {item.get('type', 'type unknown')}": item.get("instance_id") for item in ec2_rows}
                    selected_instance_label = st.selectbox("EC2 instance for live telemetry", list(options.keys()), key=f"iipp-telemetry-instance-{selected_id}")
                    selected_instance_id = options[selected_instance_label]
                    if st.button("Fetch live CloudWatch telemetry", key=f"iipp-refresh-metrics-{selected_id}"):
                        with st.spinner("Querying the selected AWS account's CloudWatch metrics…"):
                            metric_result = configured_aws_metrics(selected_id, selected_instance_id, hours=1)
                        st.session_state["iipp-configured-aws-metrics"] = metric_result
                        st.session_state["iipp-configured-aws-metrics-key"] = f"{selected_id}:{selected_instance_id}"
                    metric_key = f"{selected_id}:{selected_instance_id}"
                    metric_result = st.session_state.get("iipp-configured-aws-metrics")
                    if metric_result and st.session_state.get("iipp-configured-aws-metrics-key") == metric_key:
                        st.markdown(f"**Live telemetry · {metric_result.get('instance_id')} · {metric_result.get('instance_type')} · {metric_result.get('instance_state')}**")
                        metric_rows = [{"Metric": label, "Latest": info.get("latest") if info.get("latest") is not None else "N/A",
                                        "Unit": info.get("unit"), "Latest datapoint (UTC)": info.get("latest_timestamp") or "N/A",
                                        "Status": "LIVE" if info.get("available") else "UNAVAILABLE",
                                        "Issue": info.get("error") or ""}
                                       for label, info in metric_result.get("metrics", {}).items()]
                        st.dataframe(pd.DataFrame(metric_rows), hide_index=True, width="stretch")
                        percent_points = []
                        network_points = []
                        for label, info in metric_result.get("metrics", {}).items():
                            destination = network_points if label.startswith("Network") else percent_points
                            for point in info.get("points", []):
                                destination.append({"Time (UTC)": pd.to_datetime(point["timestamp"]),
                                                    "Value": point.get("value"), "Metric": label})
                        if percent_points:
                            st.caption("CPU, memory and disk utilization · EC2 metrics use 5-minute periods; CloudWatch Agent metrics use 1-minute periods")
                            st.plotly_chart(px.line(pd.DataFrame(percent_points), x="Time (UTC)", y="Value", color="Metric", markers=True, height=250),
                                            width="stretch", config={"displayModeBar": False})
                        if network_points:
                            net_df = pd.DataFrame(network_points)
                            net_df["Value MiB"] = net_df["Value"].astype(float) / (1024 * 1024)
                            st.caption("Network traffic · MiB per 5-minute period")
                            st.plotly_chart(px.line(net_df, x="Time (UTC)", y="Value MiB", color="Metric", markers=True, height=220),
                                            width="stretch", config={"displayModeBar": False})
                        missing_metrics = [label for label, info in metric_result.get("metrics", {}).items() if not info.get("available")]
                        if missing_metrics:
                            st.warning("Some metrics are unavailable: " + ", ".join(missing_metrics) + ". CPU/network come from EC2; memory/disk need the CloudWatch Agent (CWAgent) on the target instance.")
                else:
                    st.info("No EC2 instance IDs were returned in this sample. Refresh or choose another AWS connection.")
            elif resources or result.get("provider") in {"Azure", "GCP"}:
                st.caption("Live provider inventory was queried. Telemetry details depend on the provider APIs and metrics configured for this connection.")
        else:
            st.error("Live inventory query failed: " + str(result.get("error", "Unknown provider error")))


def render_telemetry() -> None:
    st_autorefresh(interval=30_000, key="cloudpilot-work-watch")

    with st.spinner("Reading connected-account inventory and cost information…"):
        inventory = _cached_inventory()
        costs = _cached_costs()
        ec2_costs = _cached_ec2_costs(inventory.get("instances", [])) if inventory.get("available") else {"available": False, "error": inventory.get("error", "EC2 inventory is unavailable")}
    _render_cloud_account_summary(inventory, ec2_costs)
    # Deployment lifecycle controls live on the Deployment page.
    destroy_notice = st.session_state.pop("iipp_destroy_plan_ready_notice", None)
    if destroy_notice:
        st.success(destroy_notice)

    st.subheader("Controller account resources · estimated monthly compute cost")
    if inventory.get("available"):
        priced_by_id = {str(row.get("instance_id")): row for row in ec2_costs.get("instances", [])}
        resource_rows = []
        for item in inventory.get("instances", []):
            cost_item = priced_by_id.get(str(item.get("instance_id")), {})
            monthly = cost_item.get("monthly_730h_usd")
            resource_rows.append({
                "Cloud": "AWS",
                "Service": "Amazon EC2",
                "Resource name": item.get("name", "(untagged)"),
                "Resource ID": item.get("instance_id", ""),
                "Type": item.get("instance_type", "unknown"),
                "State": item.get("state", "unknown"),
                "Availability Zone": item.get("az", ""),
                "Estimated compute / month (USD)": f"{float(monthly):.2f}" if monthly is not None else "N/A",
                "Price status": "ESTIMATED · 730h on-demand" if monthly is not None else "UNAVAILABLE · " + str(cost_item.get("price_status", "price not returned")),
            })
        if resource_rows:
            st.dataframe(pd.DataFrame(resource_rows), hide_index=True, width="stretch")
        else:
            st.info(f"No running EC2 instances were returned in {REGION}. The zero resource count is from the live EC2 inventory query.")
    else:
        st.warning("Cannot list running resources until AWS EC2 inventory is available.")

    if not TARGET_INSTANCE_ID:
        return
    with st.spinner("Reading live AWS workload telemetry…"):
        metrics = get_metric_series(TARGET_INSTANCE_ID, hours=1)
        target_details = get_target_instance_details(TARGET_INSTANCE_ID)

    _clear_applied_change_state(target_details.get("instance_type"))
    # This is the only event source: a sustained CPU threshold transition from fresh,
    # distinct CloudWatch datapoints. HTTP requests-per-second do not trigger plans.
    trend_event = evaluate_trend(metrics, target_details, TREND_STATE_PATH)
    if trend_event:
        _write_json(TREND_EVENT_PATH, trend_event)
        with st.spinner(f"Sustained CPU {trend_event.get('trend_signal', '').replace('_', ' ')} signal detected. Agent is preparing an infrastructure recommendation…"):
            trend_plan = _analyze_event(trend_event)
            _write_json(LATEST_PLAN_PATH, trend_plan)
        st.success(f"CPU signal detected: {trend_event.get('current_instance_type')} → {trend_event.get('suggested_instance_type')}. No AWS change has been applied.")

    event_cleared = _retire_cleared_event(metrics)
    if event_cleared:
        st.info("The previous CPU spike has cleared. The unapproved recommendation was withdrawn; the agent is idle until another sustained CPU threshold event.")

    latest = {name: info.get("latest") for name, info in metrics.get("metrics", {}).items()}
    cpu = latest.get("CPU")
    saved_event = _read_json(TREND_EVENT_PATH)
    if trend_event:
        st.info(f"**New CPU signal: {trend_event.get('trend_signal', '').replace('_', ' ').title()}** · {trend_event.get('recommendation_reason', '')} · Reviewer approval is required.")
    elif saved_event:
        st.caption(f"Latest emitted CPU signal: {saved_event.get('trend_signal', '').replace('_', ' ').title()} at {saved_event.get('metric_timestamp', 'unknown')}. The workload monitor waits for a new sustained CPU transition.")
    else:
        st.info("**Agent idle — waiting for a sustained CPU threshold event.** No infrastructure plan or approval action is shown by default. Telemetry continues to monitor the live workload; Agent is reserved for prompt-based cloud comparison and provisioning.")

    st.subheader("Monitored workload telemetry")
    st.caption("This is the configured workload target, separate from the selected cloud account inventory above.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("CPU utilization", _display_value(latest.get("CPU")))
    c2.metric("Memory utilization", _display_value(latest.get("Memory")))
    c3.metric("Disk utilization", _display_value(latest.get("Disk")))
    cpu_status = "High · counting samples" if cpu is not None and cpu >= 70 else ("Low · counting samples" if cpu is not None and cpu <= 25 else "Normal")
    c4.metric("CPU trigger status", cpu_status)
    st.caption(f"Target: `{TARGET_INSTANCE_ID}` · Region: `{REGION}` · CloudWatch updated: {metrics.get('generated_at', 'unknown')}. Agent thresholds: CPU ≥ 70% for 2 distinct datapoints to suggest scale-up; CPU ≤ 25% for 3 distinct datapoints to consider scale-down.")
    _render_trend_progress(target_details.get("instance_type"))

    left, right = st.columns([1.55, 1])
    with left:
        st.subheader("Live telemetry")
        frame = _metric_dataframe(metrics)
        percent_frame = frame[frame["Unit"] == "Percent"] if not frame.empty else pd.DataFrame()
        network_frame = frame[frame["Unit"] == "MiB per 60-second period"] if not frame.empty else pd.DataFrame()
        if not percent_frame.empty:
            fig = px.line(percent_frame, x="Time (UTC)", y="Value", color="Metric", markers=True, height=320)
            fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="", hovermode="x unified")
            fig.update_yaxes(title="Utilization (%)", range=[0, 100])
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        else:
            st.info("No percentage CloudWatch datapoints were returned for the last hour. Check the metric namespace and instance dimensions.")
        if not network_frame.empty:
            st.caption("Network traffic · MiB summed in each 60-second CloudWatch period")
            network_fig = px.line(network_frame, x="Time (UTC)", y="Value", color="Metric", markers=True, height=230)
            network_fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), legend_title_text="", hovermode="x unified")
            network_fig.update_yaxes(title="MiB / 60 sec", rangemode="tozero")
            st.plotly_chart(network_fig, width="stretch", config={"displayModeBar": False})
        missing = [name for name, info in metrics.get("metrics", {}).items() if not info.get("available")]
        telemetry_status_rows = []
        for name, info in metrics.get("metrics", {}).items():
            telemetry_status_rows.append({
                "Metric": name,
                "Status": "LIVE datapoints returned" if info.get("available") else "UNAVAILABLE",
                "Latest value": info.get("latest") if info.get("latest") is not None else "N/A",
                "Unit": info.get("unit", "unknown"),
                "Latest datapoint (UTC)": info.get("latest_timestamp") or "N/A",
                "Source": f"{info.get('namespace', 'unknown')} / {info.get('metric_name', 'unknown')}",
                "Issue": info.get("error") or "",
            })
        with st.expander("Metric source and freshness", expanded=bool(missing)):
            st.caption(f"API query completed at {metrics.get('generated_at', 'unknown')} UTC timestamp. This is the query time, not necessarily the latest metric datapoint time.")
            st.dataframe(pd.DataFrame(telemetry_status_rows), hide_index=True, width="stretch")
        if missing:
            st.caption("Unavailable metrics: " + ", ".join(missing) + ". CPU and network use AWS/EC2; memory and disk require CloudWatch Agent CWAgent metrics with InstanceId dimensions. Only CPU triggers the agent in this POC.")

    with right:
        st.subheader("Currently running compute")
        if inventory.get("available"):
            if inventory["instances"]:
                priced_by_id = {str(row.get("instance_id")): row for row in ec2_costs.get("instances", [])}
                rows = [{
                    "Name": x["name"], "Instance ID": x["instance_id"], "Type": x["instance_type"],
                    "State": x["state"], "AZ": x["az"], "Private IP": x.get("private_ip", ""),
                    "Est. compute / month (USD)": (
                        f"{float(priced_by_id[str(x.get('instance_id'))]['monthly_730h_usd']):.2f}"
                        if priced_by_id.get(str(x.get("instance_id")), {}).get("monthly_730h_usd") is not None else "N/A"
                    ),
                } for x in inventory["instances"]]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=240)
                st.caption(f"{inventory['count']} running EC2 instance(s) detected in {REGION}.")
            else:
                st.info("No running EC2 instances found in this region.")
        else:
            st.warning("EC2 inventory unavailable: " + inventory.get("error", "unknown error"))

    st.subheader("Controller account cost visibility")
    col_a, col_b, col_c = st.columns(3)
    if costs.get("available"):
        currency = costs.get("currency", "USD")
        actual_mtd = float(costs.get("actual_mtd") or 0.0)
        if costs.get("zero_cost_data"):
            col_a.metric("Account actual MTD", "No posted spend")
        else:
            col_a.metric("Account actual MTD", _money(actual_mtd, currency, 4))
        if costs.get("estimated_month_end") is not None:
            col_b.metric("Account month-end estimate", _money(costs.get("estimated_month_end"), currency, 2))
            if costs.get("month_end_estimate_source") == "AWS Cost Explorer forecast":
                col_b.caption("AWS Cost Explorer forecast + actual MTD")
            else:
                col_b.caption("Linear projection from posted spend; not a bill forecast")
        elif ec2_costs.get("available"):
            col_b.metric("Known EC2 monthly floor", _money(ec2_costs.get("monthly_run_rate"), "USD", 2))
        else:
            col_b.metric("Account month-end estimate", "Unavailable")
        if ec2_costs.get("available"):
            col_c.metric("Running EC2 monthly run-rate", _money(ec2_costs.get("monthly_run_rate"), "USD", 2))
        else:
            col_c.metric("Running EC2 monthly run-rate", "N/A")
        st.caption(f"Cost Explorer period: {costs.get('period_start')} to {costs.get('period_end_exclusive')} (end date exclusive). {costs.get('estimate_method', '')}")
        if costs.get("zero_cost_data"):
            if ec2_costs.get("available"):
                st.warning("Cost Explorer returned no non-zero posted spend / usable forecast for this period. The EC2 figure is a separate 730-hour compute-only estimate for currently running Linux instances, not account-wide spend.")
            else:
                st.warning("Cost Explorer has no non-zero posted spend / usable forecast, and EC2 pricing is unavailable. Check ce:GetCostAndUsage, ce:GetCostForecast and pricing:GetProducts on the IIPP EC2 role.")
        if costs.get("service_costs"):
            st.markdown("**AWS service costs (actual MTD from Cost Explorer)**")
            service_df = pd.DataFrame(costs["service_costs"]).rename(columns={"service": "AWS service", "mtd": f"MTD ({currency})"})
            service_df[f"MTD ({currency})"] = service_df[f"MTD ({currency})"].map(lambda value: f"{value:,.4f}")
            st.dataframe(service_df, hide_index=True, width="stretch", height=210)
        else:
            st.info("Cost Explorer returned no service-cost rows. The EC2 compute estimate is calculated separately from AWS Price List.")
    else:
        col_a.metric("Account actual MTD", "N/A")
        if ec2_costs.get("available"):
            col_b.metric("Known EC2 monthly floor", _money(ec2_costs.get("monthly_run_rate"), "USD", 2))
        else:
            col_b.metric("Account month-end estimate", "N/A")
        col_c.metric("Running EC2 monthly run-rate", _money(ec2_costs.get("monthly_run_rate"), "USD", 2) if ec2_costs.get("available") else "N/A")
        st.warning("Cost Explorer unavailable: " + costs.get("error", "unknown error"))
        st.caption("Attach ce:GetCostAndUsage for account billing. AWS Price List pricing is separate and requires pricing:GetProducts.")

    if ec2_costs.get("available"):
        st.caption("EC2 monthly compute estimate assumes currently running Linux instances run 730 hours at on-demand rates. It excludes EBS, transfer, taxes, credits, savings plans and discounts.")
        with st.expander("EC2 cost estimate detail", expanded=False):
            detail = pd.DataFrame(ec2_costs.get("instances", []))
            if not detail.empty:
                st.dataframe(detail, hide_index=True, width="stretch")
            st.caption(ec2_costs.get("method", ""))
            if not ec2_costs.get("complete", True):
                st.warning("Some running instances could not be priced: " + "; ".join(ec2_costs.get("missing_prices", [])))
    elif ec2_costs.get("error"):
        st.error("EC2 monthly price lookup failed: " + ec2_costs.get("error", "unknown error"))
        st.caption("This typically requires pricing:GetProducts on the IIPP EC2 role. After attaching the policy, click Refresh or restart the container to clear cached errors.")


def _fallback_plan(event: dict[str, Any], error: str) -> dict[str, Any]:
    instance = get_target_instance_details(TARGET_INSTANCE_ID)
    metrics = get_metric_series(TARGET_INSTANCE_ID) if TARGET_INSTANCE_ID else {"metrics": {}}
    latest = {k: v.get("latest") for k, v in metrics.get("metrics", {}).items()}
    cpu, mem = latest.get("CPU"), latest.get("Memory")
    if event.get("trend_signal") == "scale_up" and event.get("suggested_instance_type"):
        recommendation = (f"Sustained high CloudWatch load indicates a capacity-pressure signal. A Terraform-managed capacity change may be worth evaluating from "
                          f"{event.get('current_instance_type', instance.get('instance_type', 'current type'))} "
                          f"toward {event.get('suggested_instance_type')}. Validate workload behavior, account policy, subnet and cost before creating a Terraform plan.")
    elif event.get("trend_signal") == "scale_down" and event.get("suggested_instance_type"):
        recommendation = (f"Sustained low CloudWatch load indicates possible over-provisioning. A Terraform-managed capacity change may be worth evaluating from "
                          f"{event.get('current_instance_type', instance.get('instance_type', 'current type'))} "
                          f"toward {event.get('suggested_instance_type')}. Validate headroom, application health and cost before creating a Terraform plan.")
    else:
        recommendation = "The sustained CPU threshold evidence is not enough to justify a capacity change. Continue monitoring and do not prepare a Terraform change without a clear policy-compliant request."
    answer = f"""## Infrastructure plan (deterministic fallback; LLM unavailable)

**Status:** NOT APPLIED — manual review required.

### CPU threshold evidence
- Signal: {event.get('trend_signal', 'unknown')} from {event.get('consecutive_datapoints', 'N/A')} distinct CloudWatch CPU datapoints
- Trigger threshold: {event.get('threshold_percent', 'N/A')}% CPU
- Current CPU: {cpu if cpu is not None else 'not available'}%
- Current memory: {mem if mem is not None else 'not available'}%
- Current target: {instance.get('instance_type', 'unknown')} / `{TARGET_INSTANCE_ID or 'not configured'}`

### Recommendation
{recommendation}

### Placement and client constraints
- Connected environment: AWS `{REGION}` only. Azure and Google Cloud are not connected in this POC.
- Data-sovereignty/privacy constraints require customer policy validation. Current workload region is `{REGION}`; strict India-only LLM processing is not verified for the APAC inference profile.
- No numeric latency SLO was supplied. End-to-end network latency is not measured, so latency compliance is unverified.
- Account cost appears in Telemetry; the separately listed EC2 run-rate uses AWS Price List compute rates and excludes storage/network/tax/discounts.

### Proposed execution
1. Review telemetry and application logs.
2. If capacity pressure is demonstrated, choose an approved scaling mechanism appropriate for how the workload is managed.
3. Review the selected infrastructure change and impact before approving it.
4. Validate health after the change and retain a rollback path.

**Execution gate:** This analysis is read-only. All infrastructure changes in Intelligent Provisioning Platform must use Terraform, a saved plan, policy validation and explicit human approval. This release's AWS Terraform stack creates a new demo EC2 instance; it does not resize or import the existing QuickCart workload.

**LLM status:** {error}
"""
    return {"status": "fallback", "provider": "deterministic rules", "answer": answer,
            "event_id": event.get("event_id"), "execution_status": "NOT_APPLIED", "llm_error": error,
            "metrics_snapshot": latest, "created_at": datetime.now(timezone.utc).isoformat()}


def _analyze_event(event: dict[str, Any]) -> dict[str, Any]:
    try:
        plan = invoke_agent(event)
        plan["created_at"] = datetime.now(timezone.utc).isoformat()
        return plan
    except Exception as exc:
        return _fallback_plan(event, str(exc))


def render_agent() -> None:
    """Prompt-driven cloud comparison and provisioning; workload telemetry lives in Home."""
    if st.session_state.get("iipp_pending_destroy_deployment_id"):
        return
    st.markdown(f"<style>{(ASSETS_DIR / 'agent-workspace.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    st.markdown(f"<style>{(ASSETS_DIR / 'platform.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    with st.container(key="agent-workspace"):
        with st.container(key="agent-conversation"):
            _render_infrastructure_request_flow()


PRICE_CACHE_TTL_SECONDS = max(60, min(3600, int(os.getenv("CLOUDPILOT_PRICE_CACHE_TTL_SECONDS", "300"))))


@st.cache_data(ttl=PRICE_CACHE_TTL_SECONDS, show_spinner=False)
def _cached_public_provider_options(aws_region: str, azure_region: str, gcp_region: str,
                                    min_vcpus: int, min_memory_gib: float,
                                    aws_connection_id: str | None = None) -> list[dict[str, Any]]:
    """Cache provider rows by region, request profile and selected AWS account connection."""
    return get_provider_options(aws_region, azure_region, gcp_region, min_vcpus, min_memory_gib,
                               aws_connection_id=aws_connection_id)


def _cached_cloud_comparison(aws_region: str, azure_region: str, gcp_region: str,
                             min_vcpus: int, min_memory_gib: float,
                             budget: float | None, strategy: str,
                             aws_connection_id: str | None = None) -> dict[str, Any]:
    options = _cached_public_provider_options(aws_region, azure_region, gcp_region, min_vcpus,
                                              min_memory_gib, aws_connection_id)
    result = compare_options(options, min_vcpus, min_memory_gib, budget, strategy)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    result["source_options"] = options
    result["price_cache_ttl_seconds"] = PRICE_CACHE_TTL_SECONDS
    return result



def _option_matches_request(option: dict[str, Any], requirements: dict[str, Any]) -> bool:
    """True only when verified specs meet the user's hard CPU/RAM requirements."""
    try:
        return (option.get("vcpus") is not None and option.get("memory_gib") is not None
                and int(option["vcpus"]) >= int(requirements["min_vcpus"])
                and float(option["memory_gib"]) >= float(requirements["min_memory_gib"]))
    except (KeyError, TypeError, ValueError):
        return False


def _aws_option_can_be_selected(option: dict[str, Any], requirements: dict[str, Any]) -> bool:
    """Allow choosing AWS only if the exact type/region offering and compatible specs were verified.

    A missing price can be disclosed while still allowing the user to prepare a plan;
    a failed or negative EC2 offering check must never be overridden by price availability.
    """
    return bool(
        str(option.get("provider", "")).upper() == "AWS"
        and option.get("sku")
        and option.get("sku") != "no-matching-profile"
        and option.get("offered_in_target_location") is True
        and _option_matches_request(option, requirements)
    )


def _render_infrastructure_request_flow() -> None:
    """Use the current prompt workflow, including for sessions with old toggle state."""
    from agent_flow_ui import render
    render()


def _render_legacy_infrastructure_request_flow() -> None:
    """Natural-language requirements -> live three-cloud price comparison -> AWS plan workflow."""
    with st.form("agent-infrastructure-request-form", clear_on_submit=False):
        st.markdown('<div class="workspace-kicker">PROVISIONING REQUEST</div>', unsafe_allow_html=True)
        st.markdown("### Provision with the Agent")
        prompt = st.text_area(
            "Infrastructure requirements", key="agent-infrastructure-prompt", height=110,
            placeholder="Describe your infrastructure requirements…",
        )
        st.caption("Example: 4 vCPU, 16 GiB RAM, Linux in India. Prefer lowest cost.")
        with st.expander("Advanced options", expanded=False):
            r1, r2, r3 = st.columns(3)
            aws_region = r1.text_input("AWS region", value=REGION, key="agent-infra-aws-region")
            azure_region = r2.text_input("Azure region", value=os.getenv("AZURE_PRICING_REGION", "centralindia"), key="agent-infra-azure-region")
            gcp_region = r3.text_input("GCP region", value=os.getenv("GCP_PRICING_REGION", "asia-south1"), key="agent-infra-gcp-region")
            active_provider, active_connection_id, active_connection = _active_cloud_selection()
            aws_connections = [x for x in list_connections() if x.get("provider") == "AWS" and x.get("connection_status") == "CONNECTED"]
            active_aws_id = active_connection_id if active_provider == "AWS" else None
            if not active_aws_id and active_provider != "AWS":
                # Preserve the last verified AWS target for the three-cloud comparison while
                # the global selected provider drives the live inventory context above.
                active_aws_id = next((x.get("connection_id") for x in aws_connections), None)
            aws_connection_id = active_aws_id or None
            if active_connection:
                st.caption(f"Active context: {active_provider} · {active_connection.get('name')} · {active_connection.get('account_id') or active_connection.get('subscription_id') or active_connection.get('project_id')}")
            else:
                st.caption(f"Active context: {active_provider} · not connected. Configure and test an account to query its live inventory.")
        compare_clicked = st.form_submit_button("Find options", type="primary")

    if compare_clicked:
        try:
            # Clear the previous result immediately so an invalid prompt or API error
            # cannot leave an old recommendation visible beneath the new request.
            for state_key in ("agent-infra-comparison", "agent-infra-requirements",
                              "agent-infra-llm-explanation"):
                st.session_state.pop(state_key, None)
            if not prompt.strip():
                st.warning("Describe your infrastructure requirements first.")
                return
            requirements = parse_infrastructure_request(prompt)
            if requirements.get("os") != "Linux":
                st.error("Windows was detected, but this POC's AWS/Azure/GCP comparison profiles and AWS Terraform template currently use Linux pricing/AMI validation. No Windows price or deployment recommendation was generated. Use a Linux request or extend the reviewed Windows pricing and image workflow first.")
                return
            # Strategy and any budget are inferred only from the user's natural-language prompt.
            budget = requirements.get("max_monthly_budget_usd")
            # A new user comparison is an explicit request for fresh market data.
            # Clear both the Streamlit result cache and the underlying AWS Price List TTL cache
            # before retrieving regional EC2 specs, offerings and public On-Demand prices.
            _cached_public_provider_options.clear()
            clear_price_cache()
            with st.spinner("Refreshing live AWS EC2 availability and AWS Price List On-Demand rates; checking Azure/GCP catalogues…"):
                comparison = _cached_cloud_comparison(
                    aws_region.strip(), azure_region.strip(), gcp_region.strip(),
                    int(requirements["min_vcpus"]), float(requirements["min_memory_gib"]),
                    budget, str(requirements["strategy"]), aws_connection_id,
                )
            comparison["requested_os"] = requirements.get("os", "Linux")
            comparison["requested_prompt"] = prompt.strip()
            st.session_state["agent-infra-requirements"] = requirements
            st.session_state["agent-infra-comparison"] = comparison
            st.session_state.pop("agent-infra-llm-explanation", None)
            st.session_state["agent-infra-selected-provider"] = None
            st.session_state.pop("agent-infra-selected-sku", None)
            st.session_state["agent-infra-selected-region"] = aws_region.strip()
            st.session_state["agent-infra-aws-connection-id"] = aws_connection_id
            try:
                with st.spinner("Evaluating infrastructure options…"):
                    st.session_state["agent-infra-llm-explanation"] = explain_comparison_evidence(prompt.strip(), comparison)
            except Exception as llm_exc:
                if comparison.get("recommended_sku"):
                    lead = f"**Recommended: {comparison.get('recommended_provider')} · {comparison.get('recommended_sku')}**"
                elif comparison.get("optimal_sku"):
                    lead = f"**Cost-optimal: {comparison.get('optimal_provider')} · {comparison.get('optimal_sku')}**"
                else:
                    lead = "**Recommendation: no verified eligible configuration**"
                aws_price_rows = [x for x in comparison.get("source_options", [])
                                  if x.get("provider") == "AWS" and x.get("available") is True
                                  and x.get("price_retrieved_at")]
                if aws_price_rows:
                    freshest = max(aws_price_rows, key=lambda x: str(x.get("price_retrieved_at")))
                    price_line = (f"• AWS live price: AWS Price List Query API; fetched {freshest.get('price_retrieved_at')} UTC."
                                  )
                else:
                    price_line = "• AWS live price: unavailable; no sample or hardcoded AWS rate was used."
                st.session_state["agent-infra-llm-explanation"] = {
                    "answer": "\n".join([
                        lead,
                        f"• Request: {requirements['min_vcpus']} vCPU and {requirements['min_memory_gib']:g} GiB RAM.",
                        f"• Strategy: {requirements.get('strategy', 'Balanced')}; exact hardware matches are preferred.",
                        f"• Cost-optimal option: {comparison.get('optimal_provider') or 'none'} · {comparison.get('optimal_sku') or 'not available'}.",
                        price_line,
                        "• Latency: no comparable live end-to-end measurement is available; no latency winner is claimed.",
                        "• AWS regional offering is checked; account quota and immediate spare capacity are not guaranteed; Azure/GCP public prices are catalogues only.",
                        "• No infrastructure was changed; Terraform plan and reviewer approval are required.",
                    ]),
                    "provider": "Deterministic fallback", "model_id": "LLM unavailable",
                    "error": f"{type(llm_exc).__name__}: {llm_exc}",
                    "execution_status": "READ_ONLY_COMPARISON_EXPLANATION",
                }
        except Exception as exc:
            st.error(f"Could not compare cloud options: {type(exc).__name__}: {exc}")
            return

    comparison = st.session_state.get("agent-infra-comparison")
    requirements = st.session_state.get("agent-infra-requirements")
    if not comparison or not requirements:
        with st.container(border=True, key="agent-empty-state"):
            st.markdown("### Your next change starts with a request.")
            st.caption("Describe your workload above. The Agent compares hardware fit and cost, explains the evidence, and helps prepare a change for human review.")
            render_execution_methods()
        return

    st.markdown("#### Parsed workload requirements")
    req_cols = st.columns(3)
    req_cols[0].metric("Minimum vCPU", requirements.get("min_vcpus", "N/A"))
    req_cols[1].metric("Minimum RAM", f"{requirements.get('min_memory_gib', 'N/A')} GiB")
    req_cols[2].metric("OS", comparison.get("requested_os", "Linux"))
    if requirements.get("defaults_used"):
        st.warning("Some hardware requirements were not stated: " + ", ".join(requirements["defaults_used"]) + ". Please confirm the parsed defaults before provisioning.")

    explanation = st.session_state.get("agent-infra-llm-explanation")
    if explanation:
        with st.container(border=True, key="agent-recommendation"):
            st.markdown("#### Recommendation")
            st.markdown(sanitize_agent_answer(explanation.get("answer", "No explanation returned.")))
            with st.expander("Recommendation details", expanded=False):
                st.caption(f"{explanation.get('provider', 'unknown')} · {explanation.get('model_id', 'unknown')} · Read-only recommendation")
            if explanation.get("error"):
                st.caption("The deterministic comparison remains available; the LLM explanation was unavailable for this request.")

    with st.expander("Compare configurations and choose a deployment target", expanded=False):
        _render_agent_comparison_options(comparison, requirements, aws_region)

    with st.container(border=True, key="agent-execution"):
        render_execution_methods()

    if st.session_state.get("agent-infra-selected-provider") == "AWS":
        selected_type = st.session_state.get("agent-infra-selected-sku")
        selected_region = st.session_state.get("agent-infra-selected-region", REGION)
        st.divider()
        st.subheader(f"Review proposed change · {selected_type}")
        st.info("Prepare a saved Terraform plan for a NEW EC2 instance, then review its exact changes and approve execution. Azure/GCP provisioning stacks are not implemented in this release.")
        if st.button("Cancel AWS creation", key="agent-cancel-aws-create"):
            st.session_state["agent-infra-selected-provider"] = None
            st.session_state.pop("agent-infra-selected-sku", None)
            st.rerun()
        render_deployments(
            embedded=True,
            suggested_instance_type=selected_type,
            default_region=selected_region,
            required_vcpus=int(requirements.get("min_vcpus", 2)),
            required_memory_gib=float(requirements.get("min_memory_gib", 8.0)),
        )


def _render_agent_comparison_options(comparison: dict[str, Any], requirements: dict[str, Any], aws_region: str) -> None:
    """Keep detailed selection available without overwhelming the conversation."""
    st.markdown("#### Three-cloud comparison")
    if st.button("Refresh live cloud discovery & pricing", key="agent-refresh-provider-pricing"):
        st.cache_data.clear()
        clear_price_cache()
        st.rerun()
    options = comparison.get("source_options", [])
    ranked_candidates = rank_candidate_options(options, int(requirements["min_vcpus"]), float(requirements["min_memory_gib"]))
    exact_summary = exact_match_summary(options, int(requirements["min_vcpus"]), float(requirements["min_memory_gib"]))
    aws_live_rows = [x for x in options if str(x.get("provider", "")).upper() == "AWS"]
    safe_aws = [x for x in ranked_candidates if str(x.get("provider", "")).upper() == "AWS"
                and x.get("offered_in_target_location") is True and x.get("hardware_fit") in {"exact", "meets"}]
    nearest_aws = next((x for x in ranked_candidates if str(x.get("provider", "")).upper() == "AWS"), None)
    if safe_aws:
        chosen = safe_aws[0]
        match_word = "Exact match" if chosen.get("hardware_fit") == "exact" else "Closest safe match · meets or exceeds request"
        with st.container(border=True):
            st.caption("RECOMMENDED AWS CONFIGURATION")
            st.markdown(f"### {chosen.get('sku')}")
            fit_cols = st.columns(3)
            fit_cols[0].metric("vCPU", chosen.get("vcpus", "N/A"))
            fit_cols[1].metric("RAM", f"{chosen.get('memory_gib', 'N/A')} GiB")
            price_value = chosen.get("monthly_compute_estimate")
            fit_cols[2].metric("Est. compute / month", f"USD {float(price_value):,.2f}" if price_value is not None else "N/A")
            st.caption(f"{match_word}. Regional EC2 offering verified. Account quota and spare launch capacity are separate checks.")
    elif nearest_aws:
        with st.container(border=True):
            st.caption("CLOSEST AWS CONFIGURATION · NOT ELIGIBLE FOR PROVISIONING")
            st.markdown(f"### {nearest_aws.get('sku')}")
            fit_cols = st.columns(2)
            fit_cols[0].metric("vCPU", nearest_aws.get("vcpus", "N/A"))
            fit_cols[1].metric("RAM", f"{nearest_aws.get('memory_gib', 'N/A')} GiB")
            st.warning("No verified AWS option currently meets both requested CPU and RAM requirements or regional offering checks.")
    else:
        st.warning("No live AWS instance profile was returned for this request. No hardcoded AWS fallback was used.")
    aws_account_id = next((x.get("account_id") for x in aws_live_rows if x.get("account_id")), None)
    aws_discovery_errors = [str(x.get("error")) for x in aws_live_rows
                            if x.get("catalog_available") is False and x.get("error")]
    recommended_provider = comparison.get("recommended_provider")
    priced_provider_names = {
        str(option.get("provider", "")).upper()
        for option in options
        if option.get("available") is True
        and option.get("vcpus") is not None and int(option.get("vcpus")) >= int(requirements["min_vcpus"])
        and option.get("memory_gib") is not None and float(option.get("memory_gib")) >= float(requirements["min_memory_gib"])
        and option.get("hourly_price") is not None
        and option.get("price_currency", "USD") == "USD"
    }
    unavailable_providers = [p for p in ("AWS", "Azure", "GCP") if p not in priced_provider_names]
    if recommended_provider:
        confidence_text = "all three public price catalogues" if len(priced_provider_names) == 3 else f"{len(priced_provider_names)}/3 providers with eligible price/spec data"
        st.caption(f"Comparison covers {confidence_text}.")
    elif comparison.get("optimal_sku"):
        st.info(f"No latency winner is verified. Cost-optimal eligible option: **{comparison.get('optimal_provider')} · {comparison.get('optimal_sku')}**.")
    else:
        st.warning(comparison.get("recommendation_reason", "No provider can currently be ranked from verified prices."))
    if requirements.get("strategy") == "Lowest Latency" and not comparison.get("latency_measured"):
        st.caption("Latency measurements are unavailable. Cost and hardware fit are compared below.")

    recommended_key = (str(recommended_provider or "").upper(), str(comparison.get("recommended_sku") or ""))
    optimal_key = (str(comparison.get("optimal_provider") or "").upper(), str(comparison.get("optimal_sku") or ""))
    eligible_key = {(x.get("provider"), x.get("sku")): x for x in comparison.get("eligible_options", [])}
    with st.expander("Exact match check and closest configurations", expanded=False):
        requested_cpu = int(requirements["min_vcpus"])
        requested_ram = float(requirements["min_memory_gib"])
        if exact_summary["aws_verified_exact"]:
            exact_names = ", ".join(f"{x.get('sku')} ({x.get('vcpus')} vCPU / {x.get('memory_gib')} GiB)" for x in exact_summary["aws_verified_exact"])
            st.success(f"EC2 reports an exact hardware profile offered in AWS region {aws_region}: {exact_names}. This does not prove your account has enough vCPU quota or that spare capacity is available at launch time.")
        elif exact_summary["aws_unverified_exact"]:
            reasons = []
            for item in exact_summary["aws_unverified_exact"]:
                evidence = item.get("availability_evidence", "Offering not verified")
                reasons.append(f"{item.get('sku')}: {evidence}")
            st.warning(f"No exact AWS {requested_cpu} vCPU / {requested_ram:g} GiB option is currently verified as offered in {aws_region}. " + " | ".join(reasons))
        elif aws_discovery_errors:
            st.error("AWS live account discovery did not complete. No hardcoded AWS SKU or CPU/RAM fallback was used. " + " | ".join(aws_discovery_errors[:2]))
        else:
            st.warning(f"No exact AWS {requested_cpu} vCPU / {requested_ram:g} GiB configuration was returned by live EC2 discovery in {aws_region}. The closest live configurations are listed below.")
        if exact_summary["azure_catalog_exact"]:
            if any(x.get("available") is True for x in exact_summary["azure_catalog_exact"]):
                st.info("Azure public retail pricing returned an exact hardware profile, but Azure account connectivity, subscription quota and zone capacity are not configured in this POC.")
            else:
                st.caption("Azure has an exact profile in the local candidate map, but the public price lookup did not verify a current regional price.")
        if exact_summary["gcp_catalog_exact"]:
            if any(x.get("available") is True for x in exact_summary["gcp_catalog_exact"]):
                st.info("GCP public billing pricing returned an exact hardware profile, but GCP project connectivity, quota and zone capacity are not configured in this POC.")
            else:
                st.caption("GCP has an exact profile in the local candidate map, but the public price lookup did not verify a current regional price.")
        if not exact_summary["azure_catalog_exact"] and not exact_summary["gcp_catalog_exact"]:
            st.caption("AWS discovery is live for the selected account. Azure/GCP Configure connections can also query live inventories; unconnected comparison rows remain labelled as public catalogue estimates. This POC's reviewed Terraform apply stack currently provisions AWS EC2 only.")

        candidate_rows = []
        for item in ranked_candidates:
            candidate_key = (str(item.get("provider", "")).upper(), str(item.get("sku", "")))
            badges = []
            if candidate_key == recommended_key and recommended_key[1]:
                badges.append("Recommended")
            if candidate_key == optimal_key and optimal_key[1]:
                badges.append("Optimal · lowest cost")
            candidate_rows.append({
                "Selection": " · ".join(badges),
                "Cloud": item.get("provider"), "SKU / profile": item.get("sku"),
                "vCPU": item.get("vcpus"), "RAM GiB": item.get("memory_gib"),
                "Hardware fit": item.get("hardware_match"),
                "Price / month (USD)": item.get("monthly_compute_estimate"),
                "Price data": "LIVE · AWS Price List" if item.get("provider") == "AWS" and item.get("available") is True else ("Available" if item.get("available") is True else "Unavailable"),
                "Price checked (UTC)": item.get("price_retrieved_at") or "Not returned",
                "Price source": item.get("price_source") or item.get("error") or "Not available",
                "Regional / account evidence": item.get("availability_evidence"),
            })
        if candidate_rows:
            st.dataframe(pd.DataFrame(candidate_rows), hide_index=True, width="stretch")
        else:
            st.info("No candidate profile data was returned. Check provider API permissions/catalogue configuration and refresh.")
    aws_priced = [x for x in aws_live_rows if x.get("available") is True and x.get("price_retrieved_at")]
    if aws_priced:
        checked_values = sorted({str(x.get("price_retrieved_at")) for x in aws_priced})
        st.caption(f"AWS live pricing: AWS Price List Query API · Linux/Shared On-Demand · fetched {checked_values[-1]} · {PRICE_CACHE_TTL_SECONDS // 60} min cache TTL. A new Compare request forces a fresh price lookup. Monthly estimate uses 730 hours and excludes EBS, data transfer, public IPv4, taxes and account discounts.")
    else:
        price_errors = [str(x.get("error")) for x in aws_live_rows if x.get("error")]
        st.warning("AWS live price data is unavailable. Intelligent Provisioning Platform will not use sample or hardcoded AWS prices. " + ("; ".join(price_errors[:2]) if price_errors else "Check pricing:GetProducts on the controller IAM role and the Price List API endpoint region."))
    st.caption("AWS CPU/RAM values and regional offerings come from live EC2 APIs using the selected account role. AWS public On-Demand prices come from the Price List Query API using the controller role. Only exact matches or configurations meeting/exceeding both requested CPU and RAM are eligible for AWS plan generation. Undersized alternatives are shown for comparison but cannot be provisioned against the current request. Azure/GCP results use the POC's curated reference profiles and public pricing catalogues; these are not proof of connected account capacity.")

    with st.expander("Detailed comparison and exclusions", expanded=False):
        rows = []
        for item in options:
            rows.append({
                "Provider": item.get("provider"), "SKU": item.get("sku"), "Region": item.get("region"),
                "Status": "PRICE AVAILABLE" if item.get("available") else "UNAVAILABLE / PARTIAL",
                "vCPU": item.get("vcpus") if item.get("vcpus") is not None else "N/A",
                "RAM GiB": item.get("memory_gib") if item.get("memory_gib") is not None else "N/A",
                "Hourly price": f"{item.get('price_currency') or 'USD'} {item.get('hourly_price'):.5f}" if item.get("hourly_price") is not None else "N/A",
                "Monthly compute": f"{item.get('price_currency') or 'USD'} {item.get('monthly_compute_estimate'):.2f}" if item.get("monthly_compute_estimate") is not None else "N/A",
                "Telemetry": item.get("telemetry_status", "unavailable"),
                "Provisioning": "AWS approval-gated only" if item.get("provider") == "AWS" else "Disabled (no account connection)",
                "Issue": item.get("error") or item.get("eligibility_reason") or "No additional issue reported",
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        for notice in comparison.get("notices", []):
            st.caption("• " + notice)



    st.markdown("#### Selectable · Recommended · Optimal by cloud")
    st.caption(
        "Selectable prioritizes an exact hardware match (or the closest eligible fit if no exact match exists). "
        "Recommended follows the requested ranking strategy. Optimal is the lowest-priced eligible option."
    )
    provider_tabs = st.tabs(["AWS", "Azure", "GCP"])
    eligible_by_key = {
        (str(x.get("provider", "")).upper(), str(x.get("sku", ""))): x
        for x in comparison.get("eligible_options", [])
    }

    for tab, provider in zip(provider_tabs, ("AWS", "Azure", "GCP")):
        with tab:
            provider_rows = [
                x for x in ranked_candidates
                if str(x.get("provider", "")).upper() == provider
                and (provider, str(x.get("sku", ""))) in eligible_by_key
            ]
            if provider == "AWS":
                st.caption(
                    f"Live EC2 specifications and regional offerings · account {aws_account_id or 'verified connection'} · {aws_region}"
                )
            else:
                st.caption(
                    f"Public catalogue comparison · {provider} account inventory / launch capacity is not verified in this POC."
                )

            exact_rows = [x for x in provider_rows if x.get("hardware_fit") == "exact"]
            feasible_rows = [x for x in provider_rows if x.get("hardware_fit") in {"exact", "meets"}]
            selectable_pool = exact_rows or feasible_rows
            selectable_option = min(
                selectable_pool,
                key=lambda x: (
                    float(x.get("match_distance", float("inf"))),
                    float(eligible_by_key[(provider, str(x.get("sku", "")))].get("monthly_compute_estimate", float("inf"))),
                    str(x.get("sku", "")),
                ),
            ) if selectable_pool else None

            # Preserve the same hard-fit hierarchy as the global engine:
            # exact profiles outrank oversized profiles; only when no exact
            # profile exists do we rank the meets-minimum pool.
            preferred_rows = exact_rows or feasible_rows
            scored_rows = sorted(
                preferred_rows,
                key=lambda x: (
                    -(float(eligible_by_key[(provider, str(x.get("sku", "")))].get("score") or 0)),
                    float(eligible_by_key[(provider, str(x.get("sku", "")))].get("monthly_compute_estimate", float("inf"))),
                    str(x.get("sku", "")),
                ),
            )
            recommended_option = next(
                (x for x in scored_rows if not selectable_option or x.get("sku") != selectable_option.get("sku")),
                scored_rows[0] if scored_rows else selectable_option,
            )
            optimal_option = min(
                preferred_rows,
                key=lambda x: (
                    float(eligible_by_key[(provider, str(x.get("sku", "")))].get("monthly_compute_estimate", float("inf"))),
                    str(x.get("sku", "")),
                ),
            ) if preferred_rows else None

            tier_specs = [
                ("Selectable", selectable_option, "Exact match first; otherwise closest eligible hardware fit."),
                ("Recommended", recommended_option, f"Best {requirements.get('strategy', 'Balanced')} strategy result for this cloud."),
                ("Optimal", optimal_option, "Lowest verified USD compute price among eligible profiles."),
            ]
            tier_cols = st.columns(3)
            for tier_col, (tier_name, option, description) in zip(tier_cols, tier_specs):
                with tier_col:
                    with st.container(border=True):
                        st.markdown(f"**{tier_name}**")
                        st.caption(description)
                        if option is None:
                            st.info("No eligible, priced configuration returned.")
                            continue
                        evidence = eligible_by_key[(provider, str(option.get("sku", "")))]
                        st.markdown(f"### `{option.get('sku')}`")
                        m1, m2 = st.columns(2)
                        m1.metric("vCPU", option.get("vcpus", "N/A"))
                        m2.metric("RAM", f"{option.get('memory_gib', 'N/A')} GiB")
                        monthly = evidence.get("monthly_compute_estimate")
                        st.metric(
                            "Compute / month",
                            f"USD {float(monthly):,.2f}" if monthly is not None else "Unavailable",
                        )
                        st.caption(str(option.get("hardware_match", "Hardware fit not verified")))
                        st.caption(
                            str(option.get("availability_evidence") or option.get("availability_summary")
                                or "Public catalogue evidence only")
                        )
                        if tier_name == "Selectable" and option.get("hardware_fit") == "exact":
                            st.success("Exact CPU/RAM match")
                        elif tier_name == "Selectable":
                            st.info("No exact match returned; showing closest eligible fit.")
                        same_as = []
                        if tier_name == "Recommended" and selectable_option and option.get("sku") == selectable_option.get("sku"):
                            same_as.append("Selectable")
                        if tier_name == "Optimal" and option.get("sku") in {
                            selectable_option.get("sku") if selectable_option else None,
                            recommended_option.get("sku") if recommended_option else None,
                        }:
                            same_as.append("another tier")
                        if same_as:
                            st.caption("Same profile as " + " / ".join(same_as) + "; no distinct alternative met the evidence requirements.")

                        can_plan = (
                            provider == "AWS"
                            and _aws_option_can_be_selected(option, requirements)
                        )
                        button_label = "Use this configuration" if provider == "AWS" else "Comparison only · provisioning unavailable"
                        if st.button(
                            button_label,
                            key=f"agent-tier-select-{provider.lower()}-{tier_name.lower()}",
                            type="primary" if tier_name == "Recommended" else "secondary",
                            disabled=not can_plan,
                            use_container_width=True,
                        ):
                            st.session_state["agent-infra-selected-provider"] = "AWS"
                            st.session_state["agent-infra-selected-sku"] = option.get("sku")
                            st.session_state["agent-infra-selected-region"] = option.get("region") or aws_region
                            st.rerun()
                        if provider != "AWS":
                            st.caption("Azure/GCP Terraform provisioning is not enabled in this release.")
                        elif not can_plan:
                            st.caption("Cannot proceed until live AWS offering, price, policy and connection checks pass.")

    selectable_comparison_options = [x for x in ranked_candidates
        if x.get("hardware_fit") in {"exact", "meets"}
        and x.get("available") is True
        and x.get("monthly_compute_estimate") is not None
        and x.get("price_currency", "USD") == "USD"]
    if selectable_comparison_options:
        with st.expander("Choose from all returned priced configurations that meet the request", expanded=True):
            option_labels: list[str] = []
            option_by_label: dict[str, dict[str, Any]] = {}
            for item in selectable_comparison_options:
                role_labels = []
                candidate_key = (str(item.get("provider", "")).upper(), str(item.get("sku", "")))
                if candidate_key == recommended_key and recommended_key[1]:
                    role_labels.append("Recommended")
                if candidate_key == optimal_key and optimal_key[1]:
                    role_labels.append("Optimal · lowest cost")
                suffix = f" · {' / '.join(role_labels)}" if role_labels else ""
                label = (f"{item.get('provider')} · {item.get('sku')} · {item.get('vcpus')} vCPU / "
                         f"{item.get('memory_gib')} GiB · USD {float(item.get('monthly_compute_estimate')):,.2f}/month{suffix}")
                option_labels.append(label)
                option_by_label[label] = item
            default_label = next((label for label, item in option_by_label.items()
                                  if (str(item.get("provider", "")).upper(), str(item.get("sku", ""))) == recommended_key),
                                 next((label for label, item in option_by_label.items()
                                       if (str(item.get("provider", "")).upper(), str(item.get("sku", ""))) == optimal_key),
                                      option_labels[0]))
            selected_label = st.selectbox("Select a configuration", option_labels,
                                          index=option_labels.index(default_label), key="agent-multicloud-candidate-choice")
            selected_option = option_by_label[selected_label]
            availability_summary = selected_option.get("availability_evidence") or selected_option.get("availability_summary") or "Catalogue evidence only"
            st.caption(f"Hardware fit: {selected_option.get('hardware_match')} · Availability: {availability_summary} · Capacity: {selected_option.get('capacity_status', 'not verified')}")
            selected_provider = str(selected_option.get("provider", "")).upper()
            selected_is_plan_eligible = selected_provider == "AWS" and _aws_option_can_be_selected(selected_option, requirements)
            if st.button("Use selected configuration", key="agent-use-selected-multicloud-option", type="primary", disabled=not selected_is_plan_eligible):
                st.session_state["agent-infra-selected-provider"] = "AWS"
                st.session_state["agent-infra-selected-sku"] = selected_option.get("sku")
                st.session_state["agent-infra-selected-region"] = selected_option.get("region") or aws_region
                st.rerun()
            if not selected_is_plan_eligible:
                st.caption("This option is selectable for comparison, but this release prepares Terraform plans only for AWS profiles with verified regional offerings.")

    st.caption("Estimates cover compute only. Creating resources requires a reviewed Terraform plan and approval.")

def render_cloud_comparison() -> None:
    st.caption("Public retail compute pricing and deterministic eligibility checks. AWS account telemetry is live; Azure/GCP account telemetry and provisioning are not connected.")
    with st.form("cloud-comparison-form"):
        left, right = st.columns(2)
        min_vcpus = left.number_input("Minimum vCPUs", min_value=1, max_value=128, value=2, step=1)
        min_memory = right.number_input("Minimum memory (GiB)", min_value=0.5, max_value=4096.0, value=8.0, step=0.5)
        c1, c2, c3 = st.columns(3)
        aws_region = c1.text_input("AWS region", value=REGION)
        azure_region = c2.text_input("Azure region", value=os.getenv("AZURE_PRICING_REGION", "centralindia"))
        gcp_region = c3.text_input("GCP region", value=os.getenv("GCP_PRICING_REGION", "asia-south1"))
        submitted = st.form_submit_button("Compare cloud options", type="primary")
    if submitted:
        try:
            with st.spinner("Querying AWS Price List, Azure Retail Prices and Google Cloud Billing Catalog…"):
                comparison = _cached_cloud_comparison(aws_region.strip(), azure_region.strip(), gcp_region.strip(),
                                                      int(min_vcpus), float(min_memory), None, "Balanced")
            st.session_state["cloudpilot-comparison"] = comparison
        except Exception as exc:
            st.error(f"Comparison failed: {type(exc).__name__}: {exc}")
    if st.button("Refresh public pricing", key="refresh-cloud-prices"):
        st.cache_data.clear()
        st.rerun()
    comparison = st.session_state.get("cloudpilot-comparison")
    if not comparison:
        st.info("Choose workload requirements and select Compare cloud options. The comparison does not create resources.")
        st.markdown("**Default profile:** AWS `t3.large` · Azure `Standard_D2s_v5` · GCP `n2-standard-2` (Linux, target 2 vCPU / 8 GiB). Public prices and machine profile specs are not a substitute for workload benchmarking.")
        return
    st.caption(f"Compared at {comparison.get('generated_at', 'unknown')} · Default approach: Balanced, using evidence available from the selected cloud catalogs")
    if comparison.get("recommended_provider"):
        st.success(f"Best eligible option under the selected strategy: {comparison['recommended_provider']} · {comparison.get('recommended_sku')}")
    else:
        st.warning(comparison.get("recommendation_reason", "No recommendation could be ranked from available evidence."))
    st.write(comparison.get("recommendation_reason", ""))
    rows = []
    for item in comparison.get("source_options", []):
        rows.append({
            "Provider": item.get("provider"), "SKU": item.get("sku"), "Region": item.get("region"),
            "Status": "PRICE AVAILABLE" if item.get("available") else "UNAVAILABLE",
            "vCPUs": item.get("vcpus") if item.get("vcpus") is not None else "N/A",
            "Memory GiB": item.get("memory_gib") if item.get("memory_gib") is not None else "N/A",
            "Hourly compute": f"{item.get('price_currency', 'USD')} {item['hourly_price']:.6f}" if item.get("hourly_price") is not None else "N/A",
            "Monthly compute (730h)": f"{item.get('price_currency', 'USD')} {item['monthly_compute_estimate']:.2f}" if item.get("monthly_compute_estimate") is not None else "N/A",
            "Telemetry": item.get("telemetry_status", "unavailable"),
            "Capacity": item.get("capacity_status", "not verified"),
            "Provisioning": "Enabled" if item.get("provisioning_enabled") else "Disabled",
            "Price retrieved (UTC)": item.get("price_retrieved_at") or item.get("retrieved_at") or "N/A",
            "Price source / issue": item.get("price_source") or item.get("error", "unavailable"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if comparison.get("eligible_options"):
        st.subheader("Eligible options and deterministic scores")
        score_rows = [{"Provider": x.get("provider"), "SKU": x.get("sku"), "Eligibility": x.get("eligibility"),
                       "Score": x.get("score") if x.get("score") is not None else "Not ranked",
                       "Compute / month": f"{x.get('price_currency', 'USD')} {x.get('monthly_compute_estimate'):.2f}",
                       "Reason": x.get("eligibility_reason")} for x in comparison["eligible_options"]]
        st.dataframe(pd.DataFrame(score_rows), hide_index=True, width="stretch")
    if comparison.get("excluded_options"):
        with st.expander("Unavailable or excluded options", expanded=False):
            st.dataframe(pd.DataFrame([{"Provider": x.get("provider"), "SKU": x.get("sku"),
                                        "Status": x.get("eligibility"), "Reason": x.get("eligibility_reason")} for x in comparison["excluded_options"]]),
                         hide_index=True, width="stretch")
    for notice in comparison.get("notices", []): st.caption("• " + notice)


def render_cloud_connections() -> None:
    st.caption("Connection status is based on API capability and configuration; public pricing access is not an account connection.")
    inventory = list_running_instances()
    details = get_metric_series(TARGET_INSTANCE_ID) if TARGET_INSTANCE_ID else {"metrics": {}, "generated_at": None}
    aws_ok = bool(inventory.get("available"))
    connected_count = 1 if aws_ok else 0
    st_cols = st.columns(3)
    st_cols[0].metric("Connected providers", f"{connected_count}/3")
    st_cols[1].metric("Verified running EC2 instances", inventory.get("count", 0) if aws_ok else "N/A")
    st_cols[2].metric("Public pricing cache TTL", f"{PRICE_CACHE_TTL_SECONDS // 60} min")
    cards = st.columns(3)
    with cards[0]:
        st.subheader("AWS")
        st.success("LIVE ACCOUNT API" if aws_ok else "UNAVAILABLE")
        if aws_ok:
            st.metric("Running EC2 instances", inventory.get("count", 0))
            st.caption(f"Region: {REGION} · Checked: {inventory.get('checked_at', datetime.now(timezone.utc).isoformat())}")
            cpu = details.get("metrics", {}).get("CPU", {})
            st.caption(f"Target CPU: {cpu.get('latest')}%" if cpu.get("latest") is not None else "Target CPU metric unavailable")
        else:
            st.error(inventory.get("error", "AWS inventory query failed."))
        st.caption("Telemetry is read-only. All infrastructure provisioning is performed through the Terraform saved-plan and human-approval workflow.")
    with cards[1]:
        st.subheader("Microsoft Azure")
        st.info("PUBLIC PRICING ONLY")
        st.caption("Azure account credentials are not connected. Live Azure telemetry and provisioning are disabled.")
        if st.button("Test Azure public pricing", key="test-azure-pricing"):
            with st.spinner("Querying Azure Retail Prices API…"):
                status = get_azure_vm_price(os.getenv("AZURE_PRICING_REGION", "centralindia"), "Standard_D2s_v5")
            if status.get("available"):
                st.success(f"Public price lookup succeeded: {status.get('sku')} · {status.get('price_currency')} {status.get('hourly_price')}/hour")
                st.caption(f"Retrieved: {status.get('price_retrieved_at')} · Source: {status.get('price_source')}")
            else:
                st.warning(status.get("error", "Pricing source unavailable."))
    with cards[2]:
        st.subheader("Google Cloud")
        gcp_key_configured = bool(os.getenv("GOOGLE_CLOUD_BILLING_API_KEY", "").strip()) or Path("/run/secrets/google-cloud-billing-api-key").is_file()
        if gcp_key_configured:
            st.info("PUBLIC CATALOG KEY CONFIGURED")
            if st.button("Test GCP public pricing", key="test-gcp-pricing"):
                with st.spinner("Querying Google Cloud Billing Catalog API…"):
                    status = get_gcp_vm_price(os.getenv("GCP_PRICING_REGION", "asia-south1"), "n2-standard-2")
                if status.get("available"):
                    st.success(f"Public price lookup succeeded: {status.get('sku')} · USD {status.get('hourly_price')}/hour")
                    st.caption(f"Retrieved: {status.get('price_retrieved_at')} · Source: {status.get('price_source')}")
                else:
                    st.warning(status.get("error", "Pricing source unavailable."))
        else:
            st.warning("PUBLIC PRICING KEY NOT CONFIGURED")
            st.caption("Set GOOGLE_CLOUD_BILLING_API_KEY to query public catalog prices. GCP project telemetry and provisioning remain disconnected.")

    st.subheader("Resource inventory (verified AWS API data)")
    if aws_ok and inventory.get("instances"):
        st.dataframe(pd.DataFrame([{"Name": x.get("name"), "Instance ID": x.get("instance_id"),
                                    "Type": x.get("instance_type"), "State": x.get("state"),
                                    "Availability Zone": x.get("az"), "Private IP": x.get("private_ip")}
                                   for x in inventory["instances"]]), hide_index=True, width="stretch")
    elif aws_ok:
        st.info("The AWS API succeeded but returned no running EC2 instances in the configured region.")


def _recent_application_audit(limit: int = 30) -> list[dict[str, Any]]:
    """Read recent approval and change audit records; malformed lines are skipped."""
    if not APPROVAL_AUDIT_PATH.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = APPROVAL_AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines[-max(limit, 1):]):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    except OSError:
        return []
    return records


def render_deployments(*, embedded: bool = False, suggested_instance_type: str | None = None, default_region: str | None = None, required_vcpus: int = 2, required_memory_gib: float = 8.0) -> None:
    from terraform_service import (apply_destroy_plan, apply_saved_plan, is_enabled,
                                   list_deployments, prepare_destroy_plan, prepare_plan)
    if not embedded:
        st.caption("Terraform deployment lifecycle and reviewer approval history. Nothing is applied on page load or refresh.")
    enabled, reason = is_enabled()
    if enabled: st.success(reason)
    else: st.warning(reason)
    if not enabled:
        st.markdown("Enable only after the versioned S3 state bucket and controller IAM access are ready, target-account roles are configured, Terraform is installed, and dashboard access is restricted.")
    if embedded:
        st.markdown("**AWS deployment configuration**")
        st.caption(f"Requested minimum: {required_vcpus} vCPU · {required_memory_gib:g} GiB RAM. The plan is validated against actual EC2 specifications and the selected region.")
    else:
        st.subheader("Prepare a new AWS EC2 deployment plan")
    st.info("This workflow creates a new demo EC2 instance in the selected AWS-POC or AWS-TEST account, not a resize of QuickCart. Provide an approved Linux AMI, an existing subnet, and an existing security group in the same VPC. The reviewed stack does not assign a public IP. The saved Terraform plan must be explicitly approved before AWS creates anything.")
    initial_type = suggested_instance_type or ""
    with st.form("terraform-plan-form-agent" if embedded else "terraform-plan-form"):
        aws_connections = [x for x in list_connections() if x.get("provider") == "AWS" and x.get("connection_status") == "CONNECTED"]
        aws_connections = [x for x in aws_connections if (x.get("metadata") or {}).get("terraform_role_arn")]
        aws_connection_labels = {}
        for conn in aws_connections:
            aws_connection_labels[f"{conn.get('name')} · {conn.get('account_id') or 'connected'}"] = conn["connection_id"]
        default_connection_id = st.session_state.get("agent-infra-aws-connection-id", "")
        connection_options = ["Select a verified AWS-POC or AWS-TEST connection"] + list(aws_connection_labels.keys())
        default_connection_index = next((i for i, label in enumerate(connection_options)
                                         if aws_connection_labels.get(label) == default_connection_id and default_connection_id), 0)
        selected_connection_label = st.selectbox("AWS target account", connection_options,
                                                 index=default_connection_index,
                                                 key="terraform-aws-target-connection")
        aws_connection_id = aws_connection_labels.get(selected_connection_label, "")
        if not aws_connections:
            st.warning("No verified AWS connection with a Terraform Execution Role is saved. Use Configure to connect AWS-POC and AWS-TEST first.")
        c1, c2, c3 = st.columns(3)
        region = c1.text_input("AWS region", value=default_region or REGION)
        instance_type = c2.text_input(
            "EC2 instance type", value=initial_type, disabled=bool(suggested_instance_type),
            placeholder="Select a live AWS option above",
            help="Intelligent Provisioning Platform discovers supported AWS types dynamically. The selected type is revalidated against live EC2 APIs during plan preparation.",
        )
        instance_name = c3.text_input("Name tag", value="iipp-demo-vm")
        c4, c5, c6 = st.columns(3)
        ami_id = c4.text_input("Reviewed Linux AMI ID (required)", placeholder="ami-0123456789abcdef0", help="AMI must exist in the selected AWS region and be an available x86_64 Linux image.")
        subnet_id = c5.text_input("Existing subnet ID (required)", placeholder="subnet-0123456789abcdef0", help="Use a subnet in the intended VPC. The stack does not assign a public IP.")
        security_group_id = c6.text_input("Existing security group ID (required)", placeholder="sg-0123456789abcdef0", help="Security group must be in the same VPC as the subnet. Restrict ingress to required ports/sources.")
        owner = st.text_input("Owner tag", value="Innominds-IIPP")
        environment = "test" if "test" in selected_connection_label.lower() else "poc"
        st.caption(f"Environment tag: {environment.upper()} (derived from the selected connection name)")
        ttl = st.number_input("TTL days (tag only; deletion is not automatic)", min_value=1, max_value=30, value=2)
        instance_profile = ""
        st.caption("No workload instance role is attached by this POC template. Add IAM PassRole only after a dedicated workload role is reviewed.")
        # Keep the action visible even while disabled configuration prevents execution;
        # clicking it surfaces the exact setup blocker instead of a mysteriously disabled button.
        plan_submitted = st.form_submit_button("Create AWS infrastructure — prepare saved plan", type="primary")
    if plan_submitted:
        if not aws_connection_id:
            st.error("Select a verified AWS-POC or AWS-TEST connection with a Terraform Execution Role. The controller account is not an eligible provisioning target.")
        elif not enabled:
            st.error(f"Cannot prepare a Terraform plan yet: {reason}")
            st.info("After you restrict dashboard access, review the AWS IAM policy and configure the environment with ENABLE_TERRAFORM_WORKFLOW=true, redeploy the container, and try again. The next step prepares a saved plan only; resource creation still requires explicit reviewer approval.")
        else:
            payload = {"region": region, "instance_type": instance_type, "instance_name": instance_name,
                       "ami_id": ami_id, "subnet_id": subnet_id, "security_group_id": security_group_id,
                       "owner": owner, "ttl_days": ttl, "instance_profile_name": instance_profile,
                       "min_vcpus": required_vcpus, "min_memory_gib": required_memory_gib,
                       "aws_connection_id": aws_connection_id or None, "environment": environment}
            try:
                with st.spinner("Initializing Terraform, validating configuration and generating a saved plan. No resource will be created yet…"):
                    record = prepare_plan(payload)
                _append_audit({"timestamp": datetime.now(timezone.utc).isoformat(), "deployment_id": record.get("deployment_id"),
                               "status": record.get("status"), "action": "terraform-plan", "region": region,
                               "instance_type": instance_type})
                if record.get("status") == "AWAITING_APPROVAL": st.success(f"Plan is ready for review: {record.get('deployment_id')}. No infrastructure was created.")
                else: st.error(record.get("error", "Terraform plan did not complete."))
                if record.get("log"): st.code(record["log"][-12000:])
            except Exception as exc:
                st.error(f"Plan generation failed: {type(exc).__name__}: {exc}")

    records = list_deployments()
    if embedded:
        records = [row for row in records if row.get("status") == "AWAITING_APPROVAL"]
    st.subheader("Deployment and approval history" if not embedded else "Terraform creation plans awaiting approval")
    if not records:
        st.info("No creation plans are awaiting approval. Provisioned resources and destroy lifecycle are managed in Deployment." if embedded else "No Terraform deployment records exist yet.")
    for record in records[:20]:
        did = record.get("deployment_id", "unknown")
        with st.expander(f"{did} · {record.get('status', 'UNKNOWN')} · {record.get('requested_configuration', {}).get('instance_name', '')}", expanded=record.get("status") in {"AWAITING_APPROVAL", "DESTROY_AWAITING_APPROVAL"}):
            st.write({"Status": record.get("status"), "Created": record.get("created_at"), "Updated": record.get("updated_at"),
                      "Region": record.get("region"), "Configuration": record.get("requested_configuration"),
                      "Outputs": record.get("outputs"), "Outputs collection issue": record.get("outputs_error")})
            if record.get("state_history"):
                st.caption("Workflow state history")
                st.dataframe(pd.DataFrame(record["state_history"]), hide_index=True, width="stretch")
            if record.get("error"): st.error(record["error"])
            if record.get("log"): st.code(record["log"][-12000:])
            if record.get("status") == "AWAITING_APPROVAL":
                st.markdown("**Saved plan summary — review before approving**")
                st.code(record.get("plan_summary", "No plan summary."))
                reviewer = st.text_input("Reviewer name", key=f"tf-reviewer-{did}")
                token = st.text_input("Reviewer approval token", type="password", key=f"tf-token-{did}")
                ack = st.checkbox("I reviewed this exact saved plan and authorize applying it to AWS.", key=f"tf-ack-{did}")
                if st.button("Approve and apply saved Terraform plan", key=f"tf-apply-{did}", type="primary", disabled=not (enabled and reviewer.strip() and token and ack)):
                    expected = _approval_token()
                    if not expected or not hmac.compare_digest(token, expected):
                        st.error("Approval token did not match; plan was not applied.")
                    else:
                        with st.spinner("Applying the exact checksummed saved plan…"):
                            applied = apply_saved_plan(did, reviewer)
                        _append_audit({"timestamp": datetime.now(timezone.utc).isoformat(), "deployment_id": did,
                                       "status": applied.get("status"), "action": "terraform-apply", "reviewer": reviewer})
                        if applied.get("status") == "SUCCEEDED": st.success("Terraform apply succeeded and outputs were retrieved.")
                        else: st.error(applied.get("error", "Terraform apply failed."))
                        st.rerun()
            if not embedded and record.get("status") in {"SUCCEEDED", "DESTROY_PLAN_FAILED", "DESTROY_FAILED"}:
                if record.get("status") != "SUCCEEDED":
                    st.caption("A previous destroy-plan/apply attempt failed. Review the error above before preparing a fresh plan.")
                if st.button("Prepare destroy plan (review required)", key=f"tf-destroy-plan-{did}", disabled=not enabled):
                    with st.spinner("Preparing a destroy plan for this Intelligent Provisioning Platform-managed deployment; no resource is deleted yet…"):
                        destroyed = prepare_destroy_plan(did)
                    _append_audit({"timestamp": datetime.now(timezone.utc).isoformat(), "deployment_id": did,
                                   "status": destroyed.get("status"), "action": "terraform-destroy-plan"})
                    st.rerun()
            if not embedded and record.get("status") == "DESTROY_AWAITING_APPROVAL":
                st.warning("Destroy plan will delete resources managed by this deployment. Verify the plan below carefully.")
                st.code(record.get("destroy_plan_summary", "No destroy-plan summary."))
                reviewer = st.text_input("Destroy reviewer name", key=f"tf-destroy-reviewer-{did}")
                token = st.text_input("Destroy approval token", type="password", key=f"tf-destroy-token-{did}")
                ack = st.checkbox("I reviewed and explicitly approve destroying this Intelligent Provisioning Platform-managed deployment.", key=f"tf-destroy-ack-{did}")
                if st.button("Approve and apply destroy plan", key=f"tf-destroy-apply-{did}", type="primary", disabled=not (enabled and reviewer.strip() and token and ack)):
                    expected = _approval_token()
                    if not expected or not hmac.compare_digest(token, expected):
                        st.error("Approval token did not match; destroy was not applied.")
                    else:
                        with st.spinner("Applying the exact checksummed destroy plan…"):
                            destroyed = apply_destroy_plan(did, reviewer, token)
                        _append_audit({"timestamp": datetime.now(timezone.utc).isoformat(), "deployment_id": did,
                                       "status": destroyed.get("status"), "action": "terraform-destroy-apply", "reviewer": reviewer})
                        st.rerun()


    st.subheader("Recent reviewer and infrastructure activity")
    audit_records = _recent_application_audit()
    if audit_records:
        safe_rows = []
        for record in audit_records:
            safe_rows.append({
                "Time": record.get("completed_at") or record.get("timestamp") or record.get("updated_at", "unknown"),
                "Action": record.get("action") or record.get("status") or "activity",
                "Status": record.get("status", "unknown"),
                "Resource": record.get("instance_id") or record.get("deployment_id") or "N/A",
                "Change": f"{record.get('previous_instance_type', '')} → {record.get('new_instance_type', '')}".strip(" →"),
                "Reviewer": record.get("reviewer", record.get("destroy_reviewer", "N/A")),
            })
        st.dataframe(pd.DataFrame(safe_rows), hide_index=True, width="stretch")
    else:
        st.info("No approved Terraform deployment activity has been recorded yet.")


def render_settings() -> None:
    st.caption("Read-only configuration overview. Runtime configuration comes from environment variables and EC2 IAM roles.")
    st.subheader("Runtime configuration")
    st.dataframe(pd.DataFrame([
        {"Setting": "AWS_REGION", "Current value": REGION, "Purpose": "AWS live telemetry, inventory and resource region"},
        {"Setting": "TARGET_INSTANCE_ID", "Current value": TARGET_INSTANCE_ID or "not configured", "Purpose": "QuickCart workload instance monitored by the threshold agent"},
        {"Setting": "LLM_PROVIDER", "Current value": os.getenv("LLM_PROVIDER", "bedrock"), "Purpose": "bedrock, openai, anthropic, gemini, or local OpenAI-compatible endpoint"},
        {"Setting": "BEDROCK_MODEL_ID", "Current value": os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-micro-v1:0"), "Purpose": "Configured Bedrock Converse model / inference profile"},
        {"Setting": "OPENAI_MODEL", "Current value": os.getenv("OPENAI_MODEL", "gpt-4o-mini") if os.getenv("LLM_PROVIDER", "bedrock").lower() == "openai" else "not selected", "Purpose": "Optional OpenAI-compatible chat model; secret key is never displayed"},
        {"Setting": "ANTHROPIC_MODEL", "Current value": os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001") if os.getenv("LLM_PROVIDER", "bedrock").lower() == "anthropic" else "not selected", "Purpose": "Optional Anthropic Claude tool-using model; may be overridden for account access"},
        {"Setting": "GEMINI_MODEL", "Current value": os.getenv("GEMINI_MODEL", "gemini-3.8-flash") if os.getenv("LLM_PROVIDER", "bedrock").lower() in {"gemini", "google"} else "not selected", "Purpose": "Optional Google Gemini tool-using model; may be overridden for account access"},
        {"Setting": "GOOGLE_CLOUD_BILLING_API_KEY", "Current value": "configured" if os.getenv("GOOGLE_CLOUD_BILLING_API_KEY") else ("mounted secret available" if Path("/run/secrets/google-cloud-billing-api-key").exists() else "not configured"), "Purpose": "GCP public billing catalog lookup only; not a GCP account connection"},
        {"Setting": "ENABLE_TERRAFORM_WORKFLOW", "Current value": os.getenv("ENABLE_TERRAFORM_WORKFLOW", "false"), "Purpose": "Gates new-instance Terraform plan/apply workflow"},
    ]), hide_index=True, width="stretch")
    st.caption("Optional LLM provider setup is done through deploy-time environment variables and mounted secret files; API keys are never printed here. The default is the existing Bedrock Nova Lite configuration.")
    st.subheader("Data source labels")
    st.markdown("- **LIVE:** returned by an authorized cloud account/API for this session.\n- **ESTIMATED:** computed from published price catalogs and explicit assumptions.\n- **UNAVAILABLE:** no valid value was returned; the platform does not replace it with zero.\n- **Not connected:** telemetry and provisioning are disabled for that provider.")
    st.warning("Do not enable Terraform/apply unless the dashboard security-group ingress is restricted to authorized reviewer IPs and IAM permissions are reviewed. All provisioning must pass through Terraform. Azure/GCP Terraform apply stacks are not included in this AWS-first POC release.")


from dashboard_ui import render as render_dashboard
st.markdown("<style>" + (ASSETS_DIR / 'platform.css').read_text(encoding='utf-8') + "</style>", unsafe_allow_html=True)
aliases = {'Work': 'Dashboard', 'Home': 'Dashboard', 'Agent': 'Provisioning Agent', 'Deployments': 'Deployment', 'Telemetry': 'Dashboard'}
old_page = st.session_state.get('navigation', 'Dashboard')
st.session_state['navigation'] = aliases.get(old_page, old_page)
if st.session_state.pop('iipp_navigate_to_agent', False):
    st.session_state['requested-page'] = 'Provisioning Agent'
if st.session_state.pop('iipp_navigate_to_work', False):
    st.session_state['requested-page'] = 'Dashboard'
requested = st.session_state.pop('requested-page', None)
if requested:
    st.session_state['navigation'] = requested
    st.session_state['operational-screen'] = False
with st.sidebar:
    from control_plane import enabled as control_plane_enabled
    pages = ['Dashboard', 'Provisioning Agent', 'Deployment']
    if control_plane_enabled():
        pages.append('Approvals & schedules')
    page = st.radio('Workspace', pages, key='navigation',
                    label_visibility='collapsed', on_change=lambda: st.session_state.update({'operational-screen': False}))
    sidebar_profile()
# Provider/account identity is stored independently of transient dialog widgets.
saved_provider = st.session_state.get('iipp_provider_after_save')
saved_connection = st.session_state.get('iipp_connection_after_save')
if saved_provider in {'AWS', 'Azure', 'GCP'}:
    st.session_state['iipp_active_provider'] = saved_provider
    if saved_connection:
        st.session_state['iipp_active_connection_id'] = saved_connection
with st.container(key='platform-header'):
    heading, control, profile_col = st.columns([7, 2, 1], vertical_alignment='center')
    with heading:
        brand()
    with control:
        if st.button('Configure Cloud', key='control-configure', type='primary', use_container_width=True):
            st.session_state['iipp_open_provider_config_dialog'] = True
    with profile_col, st.container(key='header-profile'):
        header_profile()
from control_plane import enabled as governed_control_plane
if governed_control_plane():
    from control_plane_ui import context_selector
    context_selector()
if st.session_state.pop('iipp_open_provider_config_dialog', False):
    _show_provider_config_dialog()
if st.session_state.pop('iipp_open_connection_success_dialog', False):
    _show_connection_success_dialog()
if st.session_state.get('operational-screen'):
    if st.button('Back to dashboard', key='close-workload-telemetry'):
        st.session_state['operational-screen'] = False
        st.rerun()
    from control_plane import enabled, require
    if enabled():
        try:
            require({'administrator'})
        except ValueError:
            st.info('Controller monitoring is available to administrators.')
        else:
            render_telemetry()
    else:
        st.subheader('Workload telemetry')
        render_telemetry()
elif page == 'Approvals & schedules':
    from control_plane_ui import render as render_control_plane
    render_control_plane()
elif page == 'Dashboard':
    render_dashboard()
elif page == 'Deployment':
    from deployment_history_ui import render as render_deployment_history
    render_deployment_history()
else:
    render_agent()
