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
    from control_plane import enabled
    if enabled() and row['raw_status'] == 'AWAITING_APPROVAL':
        from control_plane_ui import submission
        submission(row['source'], row['deployment_id'])
    if row["provider"] == "AWS" and row["raw_status"] in {"SUCCEEDED", "DESTROY_FAILED", "DESTROY_PLAN_FAILED", "DESTROY_AWAITING_APPROVAL"}:
        if st.button("Destroy", key="destroy-" + row["key"]):
            st.session_state["open-destroy"] = row
            st.rerun()
    if row["raw_status"] in {"FAILED", "DESTROY_FAILED", "DESTROY_PLAN_FAILED"}:
        st.warning("The runner reported a failure. Review protected runner diagnostics and Terraform state before retrying.")
    if row["raw_status"] in {"PREPARING", "APPLYING", "DESTROYING"}:
        st.caption("Last saved runner status. A runner interruption can leave this status pending; verify the runner before retrying.")


def render():
    st.markdown('''<style>
    div[class*="st-key-successful-destroy-"] button {
        background: #fceaea; color: #af3648; border-color: #f3c4ca;
    }
    </style>''', unsafe_allow_html=True)
    pending = st.session_state.pop("open-destroy", None)
    if pending:
        _destroy(pending)
    st.subheader('Deployment history')
    rows, unreadable = load_history()
    if not rows:
        st.caption('Start in Provisioning Agent to configure a target, prepare a plan and request approval.')
        if st.button('Create deployment', key='start-deployment-workflow'):
            st.session_state['requested-page'] = 'Provisioning Agent'
            st.rerun()
    if any(r['raw_status'] in {'PREPARING', 'APPLYING', 'DESTROYING'} for r in rows):
        st_autorefresh(interval=10_000, key='deployment-history-refresh')
    if unreadable:
        st.warning(f'{unreadable} saved records could not be read.')
    search_col, status_col, cloud_col = st.columns([3, 2, 2])
    search = search_col.text_input('Search resource or account', key='history-search')
    status = status_col.selectbox('Status', ['All'] + sorted({r['status'] for r in rows}), key='history-status')
    provider = cloud_col.selectbox('Cloud provider', ['All', 'AWS', 'Azure', 'GCP'], key='history-provider')
    filtered = filter_records(rows, search, status, provider)
    filters = (search, status, provider)
    if st.session_state.get('history-filters') != filters:
        st.session_state['history-page'] = 1
        st.session_state['history-filters'] = filters
    pages = max(1, (len(filtered) + 9) // 10)
    if st.session_state.get('history-page', 1) > pages:
        st.session_state['history-page'] = 1
    page = max(1, st.session_state.get('history-page', 1))
    st.caption(f'{len(filtered)} deployments \u00b7 All times IST')
    _render_table(filtered[(page-1)*10:page*10], 'history-', 'No deployments match the current filters.')
    _, previous, indicator, following = st.columns([6, 1, 1.4, 1])
    if previous.button('Previous', disabled=page <= 1, key='history-previous'):
        st.session_state['history-page'] = page - 1
        st.rerun()
    indicator.caption(f'Page {page} of {pages}')
    if following.button('Next', disabled=page >= pages, key='history-next'):
        st.session_state['history-page'] = page + 1
        st.rerun()


def filter_records(rows, search='', status='All', provider='All'):
    return [r for r in rows if (status == 'All' or r['status'] == status)
            and (provider == 'All' or r['provider'] == provider)
            and search.casefold() in ' '.join(str(r.get(k, '')) for k in ('name', 'target', 'provider', 'deployment_id', 'customer', 'project')).casefold()]


def ist(value):
    from datetime import datetime, timezone, timedelta
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y \u00b7 %H:%M IST')
    except (ValueError, TypeError):
        return 'Unavailable'


def _render_table(rows, prefix, empty_message):
    if not rows:
        st.info(empty_message)
        return
    with st.container(border=True):
        widths = [0.8, 1.5, 1.4, 2, 1.7, 1.3, 1.1, 1.3]
        labels = ['CLOUD', 'SUBSCRIPTION / PROJECT', 'ACCOUNT', 'RESOURCE / REGION', 'PROVISIONED DATE', 'PROVISIONED BY', 'STATUS', 'ACTIONS']
        for col, label in zip(st.columns(widths), labels):
            col.caption(label)
        for row in rows:
            cols = st.columns(widths, vertical_alignment='center')
            for col, field in zip(cols[:3], ['provider', 'subscription', 'target']):
                col.write(row.get(field, 'Unavailable'))
            cols[3].write(row['name'])
            cols[3].caption(row.get('configuration', 'Unavailable') + ' \u00b7 ' + row['region'])
            cols[4].write(ist(row['created_at']))
            cols[5].write(row.get('initiated_by', 'Unavailable'))
            colors = {'Completed': ('#217b57', '#e6f6ee'), 'Failed': ('#af3648', '#fceaea'), 'Destroyed': ('#65758b', '#eff2f6')}
            color, bg = colors.get(row['status'], ('#6445bd', '#eee9fb'))
            cols[6].markdown(f'<span style="display:inline-block;border-radius:20px;padding:4px 10px;background:{bg};color:{color};font-size:13px">{escape(row["status"])}</span>', unsafe_allow_html=True)
            with cols[7]:
                if row['provider'] == 'AWS' and row['raw_status'] == 'SUCCEEDED':
                    reuse_action(row, prefix, 'Reprovision')
                    if st.button('Destroy', key=('' if prefix == 'history-' else prefix) + 'successful-destroy-' + row['key']):
                        st.session_state['open-destroy'] = row
                        st.session_state['requested-page'] = 'Deployment'
                        st.rerun()
                elif st.button('Details', key=prefix + 'details-' + row['key']):
                    _details(row)
            st.divider()


def reuse_action(row, prefix='', label='Reuse'):
    if row['raw_status'] != 'SUCCEEDED':
        return
    from reuse_artifacts import availability
    reason = availability(row)
    if st.button(label, key=prefix + 'reuse-' + row['key'], disabled=bool(reason), help=reason):
        try:
            st.session_state['reuse-pending'] = (row['source'], row['deployment_id'])
            if st.session_state.get('provisioning-mode') != 'Reuse previous deployment':
                st.session_state['provisioning-mode'] = 'Reuse previous deployment'
            st.session_state['requested-page'] = 'Provisioning Agent'
            st.rerun()
        except (ValueError, OSError):
            st.error('Saved configuration is incomplete or unavailable. Start a new request.')
