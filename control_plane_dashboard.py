"""Scoped account health and explicitly refreshed live inventory."""
import streamlit as st
import control_plane as cp


def render():
    from provider_connections import list_connections, configured_inventory
    connections = list_connections()
    customers = sorted({s['customer'] for c in connections if (s := cp.scope(c['connection_id']))})
    customer = st.selectbox('Customer', ['All'] + customers)
    connections = [c for c in connections if customer == 'All' or (cp.scope(c['connection_id']) or {}).get('customer') == customer]
    projects = sorted({s['project'] for c in connections if (s := cp.scope(c['connection_id']))})
    project = st.selectbox('Project', ['All'] + projects)
    connections = [c for c in connections if project == 'All' or (cp.scope(c['connection_id']) or {}).get('project') == project]
    requests = [r for r in cp.requests() if customer == 'All' or r['customer'] == customer]
    requests = [r for r in requests if project == 'All' or r['project'] == project]
    a, b, c, d = st.columns(4)
    a.metric('Cloud connections', len(connections))
    b.metric('Verified connections', sum(x.get('connection_status') == 'CONNECTED' for x in connections))
    c.metric('Pending approval', sum(r['status'] == 'PENDING' for r in requests))
    d.metric('Approved / scheduled', sum(r['status'] == 'APPROVED' for r in requests))
    from hierarchy_ui import render as render_multicloud
    render_multicloud(connections)
    from deployment_history import load_history
    history, unreadable = load_history()
    history = [h for h in history if (customer == 'All' or h['customer'] == customer) and (project == 'All' or h['project'] == project)]
    st.subheader('Recent provisioning')
    st.dataframe([{k: h[k] for k in ('name', 'provider', 'target', 'region', 'status', 'created_at')} for h in history[:10]], hide_index=True)
    if unreadable:
        st.warning(f'{unreadable} history records could not be read.')
