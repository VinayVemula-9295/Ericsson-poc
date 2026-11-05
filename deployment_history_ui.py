"""Deployment history and explicitly reviewed lifecycle operations."""
from html import escape

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from deployment_history import load_history, load_destroy_review


def _service(row):
    if row["source"] == "poc_plans":
        import poc_planner
        return poc_planner
    import terraform_service
    return terraform_service


@st.dialog("Destroy deployment", width="large")
def _destroy(row):
    st.write(f"{row['name']} · {row['deployment_id']} · {row['target']}")
    state_key = "destroy-review-" + row["key"]
    record = st.session_state.get(state_key)
    service = _service(row)
    if record is None:
        try:
            with st.spinner("Preparing destroy plan…"):
                record = (load_destroy_review(row["source"], row["deployment_id"])
                          if row["raw_status"] == "DESTROY_AWAITING_APPROVAL" and not st.session_state.get(state_key + "-regenerate")
                          else service.prepare_destroy_plan(row["deployment_id"]))
            if record["status"] != "DESTROY_AWAITING_APPROVAL":
                raise ValueError("Destroy planning failed. Review protected runner diagnostics.")
            st.session_state[state_key] = record
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, ValueError) else "Destroy planning failed. Review protected runner diagnostics.")
            return
    st.code(record.get("destroy_plan_summary", "Summary unavailable"))
    if st.button("Regenerate plan", key=state_key + "-new"):
        st.session_state.pop(state_key, None)
        st.session_state.pop(state_key + "-submitted", None)
        st.session_state[state_key + "-regenerate"] = True
        st.session_state["open-destroy"] = row
        st.rerun()
    if not st.session_state.get(state_key + "-submitted"):
        if st.button("Submit", key=state_key + "-submit"):
            st.session_state[state_key + "-submitted"] = True
            st.session_state["open-destroy"] = row
            st.rerun()
        return
    reviewer = st.text_input("Reviewer name", key=state_key + "-reviewer")
    token = st.text_input("Approval token", type="password", key=state_key + "-token")
    ack = st.checkbox("I reviewed this exact plan and approve deletion.", key=state_key + "-ack")
    if st.button("Validate and destroy", disabled=not (reviewer.strip() and token and ack), key=state_key + "-approve"):
        try:
            with st.spinner("Applying reviewed destroy plan…"):
                result = service.apply_destroy_plan(row["deployment_id"], reviewer, token)
            st.session_state.pop(state_key, None)
            st.session_state.pop(state_key + "-submitted", None)
            st.session_state.pop(state_key + "-token", None)
            if result["status"] == "DESTROYED":
                st.success(f"Successfully destroyed {row['name']} · {row['deployment_id']}")
                if result.get("resource_identifiers"):
                    st.write(result["resource_identifiers"])
                elif result.get("outputs"):
                    st.write({k: v for k, v in result["outputs"].items() if k in {"instance_id", "instance_name"}})
            else:
                st.error("Destroy failed. State and history are retained; refresh before retrying.")
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, ValueError) else "Destroy could not complete. Review protected runner diagnostics.")
    if st.button("Close and refresh", key=state_key + "-close"):
        st.rerun()


@st.dialog("Deployment details", width="large")
def _details(row):
    st.write({
        "Deployment": row["name"], "Deployment ID": row["deployment_id"],
        "Cloud": row["provider"], "Account / Subscription / Project": row["target"],
        "Region": row["region"], "Status": row["status"],
        "Configuration": row["configuration"], "Type": row["kind"],
        "Created (UTC)": row["created_at"], "Updated (UTC)": row["updated_at"],
        "Completed (UTC)": row["completed_at"], "Reviewer": row["reviewer"],
    })
    if row["state_history"]:
        st.dataframe(row["state_history"], hide_index=True, width="stretch")
    else:
        st.caption("This older record has no saved status timeline.")
    if row.get("resource_identifiers"):
        st.write(row["resource_identifiers"])
    if row["provider"] == "AWS" and row["raw_status"] in {"SUCCEEDED", "DESTROY_FAILED", "DESTROY_PLAN_FAILED", "DESTROY_AWAITING_APPROVAL"}:
        if st.button("Destroy", key="destroy-" + row["key"]):
            st.session_state["open-destroy"] = row
            st.rerun()
    if row["raw_status"] in {"FAILED", "DESTROY_FAILED", "DESTROY_PLAN_FAILED"}:
        st.warning("The runner reported a failure. Review protected runner diagnostics and Terraform state before retrying.")
    if row["raw_status"] in {"PREPARING", "APPLYING", "DESTROYING"}:
        st.caption("Last saved runner status. A runner interruption can leave this status pending; verify the runner before retrying.")


def render():
    pending = st.session_state.pop("open-destroy", None)
    if pending:
        _destroy(pending)
    st.title("Deployment")
    rows, unreadable = load_history()
    if any(row["raw_status"] in {"PREPARING", "APPLYING", "DESTROYING"} for row in rows):
        st_autorefresh(interval=10_000, key="deployment-history-refresh")
    title, refresh = st.columns([5, 1], vertical_alignment="center")
    title.caption("DEPLOYMENT RECORDS")
    if refresh.button("Refresh", key="refresh-deployment-history", use_container_width=True):
        st.rerun()
    if unreadable:
        st.warning(f"{unreadable} saved deployment record(s) could not be read. Other records are shown.")
    with st.container(border=True):
        st.subheader("Successfully deployed")
        successful = [row for row in rows if row["raw_status"] == "SUCCEEDED"]
        st.caption(f"{len(successful)} successful deployment(s)")
        _render_table(successful, "successful-", "No successful deployments have been recorded yet.")
    with st.container(border=True):
        st.subheader("History")
        _render_table(rows, "", "No deployments have been recorded yet. Generate a reviewed Terraform plan in Agent to begin.")


def _render_table(rows, prefix, empty_message):
    if not rows:
        st.info(empty_message)
        return
    widths = [1.6, .8, 2.4, 1.2, 1.5, 1.8, .8]
    for col, label in zip(st.columns(widths), ["DEPLOYMENT", "CLOUD", "ACCOUNT / SUBSCRIPTION / PROJECT", "REGION", "STATUS", "CREATED (UTC)", ""]):
        col.caption(label)
    for row in rows:
        cols = st.columns(widths, vertical_alignment="center")
        for col, field in zip(cols[:4], ["name", "provider", "target", "region"]):
            col.text(row[field])
        color, background = ("#217b57", "#e6f6ee") if row["raw_status"] == "SUCCEEDED" else (
            ("#af3648", "#fceaea") if "FAILED" in row["raw_status"] else ("#6445bd", "#eee9fb"))
        cols[4].markdown(f'<span style="display:inline-block;border-radius:20px;padding:4px 10px;background:{background};color:{color};font-size:13px;font-weight:600">{escape(row["status"])}</span>', unsafe_allow_html=True)
        cols[5].text(row["created_at"])
        if cols[6].button("Details", key=prefix + "deployment-details-" + row["key"]):
            _details(row)
        st.divider()
