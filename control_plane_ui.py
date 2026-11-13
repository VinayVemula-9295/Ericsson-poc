"""Customer ownership, request submission, approval inbox and schedule visibility."""
from datetime import datetime, timezone
import sqlite3
import streamlit as st
import control_plane as cp


def context_selector():
    from provider_connections import list_connections
    connections = list_connections()
    if not connections:
        st.info('No cloud connections assigned. Ask an administrator to configure customer/project ownership.')
        return
    columns = st.columns(5)
    selected = connections
    def choose(column, label, values):
        key = 'cp-context-' + label
        values = sorted(set(values))
        if st.session_state.get(key) not in values:
            st.session_state[key] = values[0]
        return column.selectbox(label, values, key=key)
    for column, label, field in zip(columns[:3], ['Customer', 'Organization', 'Project'], ['customer', 'organization', 'project']):
        value = choose(column, label, [(cp.scope(c['connection_id']) or {}).get(field, 'Unassigned') for c in selected])
        selected = [c for c in selected if (cp.scope(c['connection_id']) or {}).get(field, 'Unassigned') == value]
    provider = choose(columns[3], 'Cloud provider', [c['provider'] for c in selected])
    selected = [c for c in selected if c['provider'] == provider]
    labels = {c['connection_id']: c.get('name', c['connection_id']) for c in selected}
    key = 'cp-context-account'
    if st.session_state.get(key) not in labels:
        st.session_state[key] = next(iter(labels))
    connection = columns[4].selectbox('Account / Subscription / Project', list(labels), format_func=labels.get, key=key)
    st.session_state['iipp_active_provider'] = provider
    st.session_state['iipp_active_connection_id'] = connection


def login():
    from shell_ui import require_demo_login
    require_demo_login(browser_restore=False)


def submission(source, deployment_id):
    try:
        record, _, _, _ = cp.artifact(source, deployment_id)
        from cost_summary import render as render_cost_summary
        render_cost_summary(record)
    except (ValueError, OSError):
        st.warning('The saved cost and target summary could not be retrieved. Refresh the plan before submitting it.')
        return
    st.caption('Submit the reviewed plan for approval. A different authorized user must approve it.')
    mode = st.radio('Execution', ['Immediately after approval', 'Scheduled'], key='cp-execution-' + deployment_id)
    due = None
    if mode == 'Scheduled':
        date = st.date_input('Execution date (UTC)', key='cp-date-' + deployment_id)
        time = st.time_input('Execution time (UTC)', key='cp-time-' + deployment_id)
        due = datetime.combine(date, time, tzinfo=timezone.utc)
    st.caption('Plans remain subject to expiry: Agent plans 1 hour; legacy AWS plans 4 hours. Later execution requires a fresh plan and approval.')
    reviewed_cost = st.checkbox('I reviewed the target and estimated cost shown above (including any unavailable pricing notice).', key='cp-cost-reviewed-' + deployment_id)
    if st.button('Submit for approval', key='cp-submit-' + deployment_id, disabled=not reviewed_cost):
        try:
            request_id = cp.submit(source, deployment_id, due)
            st.success('Request submitted: ' + request_id)
        except (ValueError, OSError, sqlite3.IntegrityError) as exc:
            st.error('This plan already has a request; generate a fresh plan to resubmit.' if isinstance(exc, sqlite3.IntegrityError) else str(exc))


def render():
    user = cp.principal()
    st.subheader('Approvals & schedules')
    rows = cp.requests()
    for col, state in zip(st.columns(4), ['PENDING', 'APPROVED', 'RUNNING', 'SUCCEEDED']):
        col.metric(state.title(), sum(r['status'] == state for r in rows))
    customer = st.selectbox('Customer', ['All'] + sorted({r['customer'] for r in rows}))
    state = st.selectbox('Request status', ['All'] + sorted({r['status'] for r in rows}))
    visible = [r for r in rows if (customer == 'All' or r['customer'] == customer) and (state == 'All' or r['status'] == state)]
    st.dataframe([{k: r[k] for k in ('id', 'customer', 'project', 'deployment_id', 'requester', 'reviewer', 'due', 'expires', 'status', 'note')} for r in visible], hide_index=True)
    if visible:
        detail = st.selectbox('Request activity', visible, format_func=lambda r: r['deployment_id'] + ' / ' + r['status'])
        st.dataframe(cp.timeline(detail['id']), hide_index=True)
    pending = [r for r in visible if r['status'] == 'PENDING' and r['requester'] != user['username']]
    if pending and user['role'] in {'administrator', 'approver'}:
        selected = st.selectbox('Review request', pending, format_func=lambda r: r['customer'] + ' / ' + r['project'] + ' / ' + r['deployment_id'])
        try:
            record, _, _, _ = cp.artifact(selected['source'], selected['deployment_id'])
            st.code(record.get('plan_summary', 'Plan summary unavailable'))
            from cost_summary import render as render_cost_summary
            render_cost_summary(record)
        except (ValueError, OSError):
            st.warning('Saved plan is unavailable or changed. Reject and request a fresh plan.')
        note = st.text_input('Decision comment')
        ack = st.checkbox('I reviewed this exact plan, target, execution time and estimated cost')
        approve, reject = st.columns(2)
        for button, decision in ((approve, True), (reject, False)):
            if button.button('Approve' if decision else 'Reject', disabled=decision and not ack):
                try:
                    cp.decide(selected['id'], decision, note)
                    st.rerun()
                except (ValueError, OSError) as exc:
                    st.error(str(exc))
    st.caption('Approved requests execute on the background worker. Interrupted RUNNING requests require operator reconciliation; they are never automatically replayed.')
    if st.button('Refresh requests'):
        st.rerun()
    if user['role'] == 'administrator':
        ownership()


def ownership():
    from provider_connections import list_connections
    with st.expander('Customer / project ownership'):
        connections = list_connections()
        if not connections:
            st.info('Configure a cloud connection first.')
            return
        connection = st.selectbox('Connection', connections, format_func=lambda c: c.get('name', c['connection_id']))
        existing = cp.scope(connection['connection_id']) or {}
        with st.form('connection-ownership-' + connection['connection_id']):
            customer = st.text_input('Customer name', value=existing.get('customer', ''))
            project = st.text_input('Project', value=existing.get('project', ''))
            organization = st.text_input('Organization / management group / folder', value=existing.get('organization', ''))
            if st.form_submit_button('Save ownership'):
                try:
                    cp.assign_scope(connection['connection_id'], customer, project, organization)
                    st.success('Ownership saved')
                except ValueError as exc:
                    st.error(str(exc))
