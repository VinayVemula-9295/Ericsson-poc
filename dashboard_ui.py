"""Consolidated, read-only provider dashboard backed by the existing stores."""
from pathlib import Path
from html import escape
import base64
import streamlit as st
from provider_connections import list_connections
from deployment_history import load_history


def provider_logo(provider):
    path = Path(__file__).parent / 'assets' / (provider.lower() + '-logo.png')
    if not path.exists():
        return ''
    data = base64.b64encode(path.read_bytes()).decode()
    return f'<img class="provider-logo" alt="{escape(provider)}" src="data:image/png;base64,{data}">'


def logo_uri(provider):
    path = Path(__file__).parent / 'assets' / (provider.lower() + '-logo.png')
    return 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode() if path.exists() else None


def account_rows(connections):
    rows = []
    for provider in ('AWS', 'Azure', 'GCP'):
        for c in connections:
            if c.get('provider') != provider:
                continue
            meta = c.get('metadata') or {}
            rows.append({'Cloud Provider': provider,
                         'Subscription / Project': c.get('subscription_id') or meta.get('subscription_id') or c.get('project_id') or meta.get('project_id') or c.get('name', 'Unavailable'),
                         'Account': c.get('account_id') or c.get('name', 'Unavailable'),
                         'Region': meta.get('default_region') or meta.get('region') or 'Unavailable',
                         'Cost': 'Unavailable',
                         'Connected': ('🟢 Connected' if c.get('connection_status') == 'CONNECTED' else '🔴 Failed' if 'FAIL' in c.get('connection_status', '') else '⚪ Not verified'),
                         'Last verified': c.get('last_tested_at') or 'Not verified'})
    return rows


def history_frame(rows):
    return [{'Cloud': r['provider'], 'Subscription / Project': r.get('subscription', 'Unavailable'),
             'Account': r['target'], 'Resource': r['name'], 'Region': r['region'],
             'Provisioned Date (UTC)': r['created_at'], 'Provisioned By': r.get('initiated_by', 'Unavailable'),
             'Status': r['status']} for r in rows]


def render():
    from control_plane import enabled
    if enabled():
        from control_plane_dashboard import render as render_control_dashboard
        render_control_dashboard()
        return
    connections = list_connections()
    from hierarchy_ui import render as render_multicloud
    render_multicloud(connections)
    with st.container(border=True, key='recent-provisioning'):
        st.subheader('Recent provisioning')
        records, unreadable = load_history()
        if unreadable:
            st.warning(f'{unreadable} history records could not be read.')
        if records:
            from deployment_history_ui import _render_table
            _render_table(records[:5], 'dashboard-', '')
            st.caption('Showing the 5 most recent deployments \u00b7 All times IST')
        else:
            st.info('No provisioning activity recorded yet.')
        _, action = st.columns([5, 1])
        if action.button('View all', key='view-all-deployments', use_container_width=True, type='primary'):
            for key in ('history-search', 'history-status', 'history-provider', 'history-page'):
                st.session_state.pop(key, None)
            st.session_state['requested-page'] = 'Deployment'
            st.rerun()


@st.dialog('Cloud accounts', width='large')
def account_details(provider, connections):
    st.subheader(provider)
    rows = account_rows(connections)
    display = []
    for row, connection in zip(rows, connections):
        display.append({'Subscription / Project': row['Subscription / Project'], 'Account': row['Account'],
                        'Region': row['Region'], 'Status': connection.get('connection_status', 'Not verified').replace('_', ' ').title(),
                        'Cost \u00b7 MTD': 'Unavailable', 'Connected': 'Yes' if connection.get('connection_status') == 'CONNECTED' else 'No'})
    if display:
        st.dataframe(display, hide_index=True, use_container_width=True)
    else:
        st.info('No accounts configured for this provider.')
    st.caption('Connected reflects the last verification. Account billing is unavailable.')
