"""One dashboard interaction pattern for all provider-native hierarchies."""
from collections import Counter
from decimal import Decimal
from html import escape
import streamlit as st
from multicloud_dashboard import provider_snapshot, identity, query, TARGET_TYPES, account_summaries, connected_cost_summary
from multicloud_dashboard import billing_breakdown, connected_usage_summary


def card_billing(provider, snapshot):
    """Presentation-only samples; never inserted into billing results or accounts."""
    demo = provider in {'Azure', 'GCP'} and not any(snapshot.get(k) for k in ('connections', 'costs', 'errors', 'nodes'))
    if demo:
        gross, credit = {'Azure': ('64.20', '-20.00'), 'GCP': ('48.60', '-10.00')}[provider]
        gross, credit = Decimal(gross), Decimal(credit)
        return dict(gross_usage={'USD': gross}, credits={'USD': credit}, net={'USD': gross + credit},
                    adjustments={}, partial=False), True
    return snapshot.get('billing') or billing_breakdown(snapshot.get('costs', [])), False


def configure(provider, connection=None):
    st.session_state['iipp_active_provider'] = provider
    st.session_state['iipp_provider_select'] = provider
    st.session_state['dashboard-edit-connection'] = connection
    st.session_state['iipp_open_provider_config_dialog'] = True
    st.rerun()


def render(connections):
    from dashboard_ui import provider_logo
    from cost_dashboard_ui import money
    names = {'AWS': 'Amazon Web Services', 'Azure': 'Microsoft Azure', 'GCP': 'Google Cloud Platform'}
    st.title('Connected Clouds')
    refresh = False
    snapshots = {}
    cards = st.columns(3, gap='medium')
    for provider, card in zip(('AWS', 'Azure', 'GCP'), cards):
        with card, st.container(border=True, key='summary-' + provider):
            st.markdown(f'<div class="provider-title">{provider_logo(provider)}{names[provider]}</div>', unsafe_allow_html=True)
            with st.spinner('Loading ' + provider):
                snapshot = (dict(connections=[], nodes=[], costs=[], errors=[], partial_cost=False) if provider in {'Azure', 'GCP'}
                    else provider_snapshot(provider, refresh=refresh, connections=connections))
            snapshots[provider] = snapshot
            root_type = 'tenant' if provider == 'Azure' else 'organization'
            roots = [n for n in snapshot['nodes'] if n['type'] == root_type]
            accounts = account_summaries(provider, snapshot)
            connected = sum(row['online'] for row in accounts)
            unit = {'AWS': 'accounts', 'Azure': 'subscriptions', 'GCP': 'projects'}[provider]
            billing, demo = card_billing(provider, snapshot)
            count = '1' if demo else str(len(roots)) if roots else '—'
            targets = {'Azure': 4, 'GCP': 3}.get(provider, len(accounts))
            root_details = ''.join('<div class="root-detail"><strong>' + escape(n['name']) + '</strong><span>ID: ' + escape(n['id']) + '</span>' +
                ''.join('<span>' + escape(str(k).replace('_', ' ').title()) + ': ' + escape(str(v)) + '</span>' for k, v in n.get('metadata', {}).items()) + '</div>' for n in roots)
            label = 'Tenants' if provider == 'Azure' else 'Organizations'
            st.markdown('<div class="provider-counts"><div class="root-popover"><button type="button" class="root-trigger" aria-describedby="roots-' + provider + '"><span>' + label +
                ' ⓘ</span><strong>' + count + '</strong></button><div class="root-tooltip" role="tooltip" id="roots-' + provider + '">' +
                ('POC demo hierarchy' if demo else root_details or 'Organization discovery requires access.') +
                '</div></div><div class="target-count"><span>' + {'AWS': 'AWS Accounts', 'Azure': 'Subscriptions', 'GCP': 'Projects'}[provider] +
                '</span><strong>' + str(targets) + '</strong></div></div>', unsafe_allow_html=True)
            st.caption('POC demo' if demo else str(connected) + ' connected')
            st.markdown('<div class="provider-card-divider"></div>', unsafe_allow_html=True)
            spend = money(billing['gross_usage'])
            if not demo and (snapshot['partial_cost'] or billing['partial'] or not roots) and billing['gross_usage']:
                spend += ' (partial)'
            st.metric('Infrastructure Spend – MTD', spend)
            if st.button('View ' + unit + ' ↗', key=provider + '-accounts', use_container_width=True):
                st.session_state['overview-details-provider'] = provider
                st.session_state.pop('hierarchy-provider', None)
    provider = st.session_state.get('overview-details-provider')
    if provider in snapshots:
        render_accounts(provider, snapshots[provider])
    provider = st.session_state.get('hierarchy-provider')
    if provider in snapshots:
        explorer(provider, snapshots[provider])


def render_accounts(provider, snapshot):
    from cost_dashboard_ui import money
    unit = {'AWS': 'accounts', 'Azure': 'subscriptions', 'GCP': 'projects'}[provider]
    with st.container(border=True, key='overview-account-details'):
        st.subheader(provider + ' ' + unit)
        rows = account_summaries(provider, snapshot)
        if not rows:
            st.info('No visible ' + unit + '. Configure a connection and approved discovery scope.')
        else:
            display = []
            for row in rows:
                state = 'Verified' if row['online'] else 'Permission / connection failed' if row['failed'] else 'Unverified' if row['configured'] else 'Not configured'
                display.append({'Account Name': row['name'], 'Account ID': row['id'],
                    'Account Type': 'Management' if row['management'] else 'Member',
                    'Configured Region': '' if row['region'] in {'Unavailable', 'N/A', 'Unknown'} else row['region'],
                    'MTD Cost': money(row['totals']) if row['totals'] else '',
                    'Connection Status': ('\U0001f7e2 ' if row['online'] else '\U0001f534 ' if row['failed'] else '\u26aa ') + state,
                    'Last Verified': row['last_verified']})
            event = st.dataframe(display, hide_index=True, use_container_width=True,
                on_select='rerun', selection_mode='single-row', key=provider + '-account-table')
            if event.selection.rows:
                account_dialog(provider, rows[event.selection.rows[0]], snapshot)


@st.dialog('Account resources and billing', width='large')
def account_dialog(provider, account, snapshot):
    from cost_dashboard_ui import money
    st.subheader(account['name'])
    st.write(('AWS Account ID: ' if provider == 'AWS' else 'Target ID: ') + account['id'])
    st.caption('Region: ' + account['region'] + ' · Connection Status: ' + ('Verified' if account['online'] else 'Unverified'))
    refresh = st.button('Refresh', key='account-dialog-refresh')
    costs = [dict(result, data=[r for r in result.get('data', []) if r['target'] == account['id']])
             for result in snapshot.get('costs', []) if result.get('owner', 'single') == account['owner']]
    if refresh:
        updated = provider_snapshot(provider, refresh=True, connections=snapshot['connections'])
        costs = [dict(result, data=[r for r in result.get('data', []) if r['target'] == account['id']])
                 for result in updated.get('costs', []) if result.get('owner', 'single') == account['owner']]
    for result in costs:
        st.caption('Billing: ' + result['status'] + ' · Last billing synchronization: ' + str(result.get('last_successful_sync') or 'Never'))
        for error in result.get('errors', []):
            st.warning(error)
    billing = [r for result in costs for r in result['data']]
    from multicloud_dashboard import cost_totals
    st.metric('Account net expenditure · MTD', money(cost_totals(costs)))
    if billing:
        with st.expander('Service costs, daily trend and adjustments', expanded=True):
            render_cost(billing)
    else:
        st.info('Billing unavailable. Configure Cost Explorer access for this account or the management billing role.')
    if not account['connection_ids']:
        st.info('Resource discovery is not configured. Configure a matching account connection with a read-only inventory role and the External ID required by its trust policy.')
        if st.button('Configure account access'):
            configure(provider)
        return
    connection_id = st.selectbox('Inventory connection', account['connection_ids'])
    result = query(connection_id, 'resources', account['id'], refresh=refresh)
    resources = result['data']
    if result.get('errors'):
        st.info('Some resources could not be discovered. Contact your administrator for access review.')
    search = st.text_input('Search resources').casefold()
    a, b, c = st.columns(3)
    service = a.selectbox('Service', ['All'] + sorted({r['service'] for r in resources}))
    region = b.selectbox('Region', ['All'] + sorted({r.get('region') or 'Unavailable' for r in resources}))
    status = c.selectbox('Status', ['All'] + sorted({str(r.get('status') or 'Unavailable') for r in resources}))
    resources = [r for r in resources if (not search or search in (r['name'] + ' ' + r['id']).casefold())
                 and (service == 'All' or r['service'] == service) and (region == 'All' or r.get('region') == region)
                 and (status == 'All' or r.get('status') == status)]
    count = max(1, (len(resources) + 24) // 25)
    page = int(st.number_input('Page', min_value=1, max_value=count, value=1))
    st.dataframe(resources[(page - 1) * 25:page * 25], hide_index=True, use_container_width=True)
    st.caption(f'{len(resources)} matching resources. Resource cost attribution is unavailable; service billing is shown separately.')



def explorer(provider, snapshot):
    st.subheader(provider + ' hierarchy')
    if st.button('Refresh hierarchy and billing', key='hierarchy-refresh'):
        snapshot = provider_snapshot(provider, refresh=True, connections=snapshot['connections'])
    for error in snapshot['errors']:
        st.warning(error)
    nodes = snapshot['nodes']
    connections = snapshot['connections']
    if not nodes:
        st.info('No visible hierarchy. Configure a connection and approved discovery scope.')
        return
    tree, panel = st.columns([2, 3], gap='large')
    with tree, st.container(border=True, key='hierarchy-tree'):
        st.markdown('#### Organization hierarchy')
        search = st.text_input('Search name or ID', key='hierarchy-search').casefold()
        view = st.selectbox('Targets', ['all', 'configured', 'connected'], key='hierarchy-filter')
        matches = set()
        def linked(n):
            return [c for c in connections if identity(c) == n['id'] and c['connection_id'] in n['connection_ids']]
        for n in nodes:
            links = linked(n)
            eligible = (view == 'all' or bool(links)) if view != 'connected' else any(c.get('connection_status') == 'CONNECTED' for c in links)
            if eligible and (not search or search in (n['name'] + ' ' + n['id']).casefold()):
                matches.add(n['uid'])
        # Include ancestor paths without exposing additional sibling targets.
        for _ in range(len(nodes)):
            before = len(matches)
            parents = {n['parent_id'] for n in nodes if n['uid'] in matches}
            matches.update(n['uid'] for n in nodes if n['id'] in parents)
            if before == len(matches):
                break
        by_id = {n['id'] for n in nodes}
        visited = set()
        def draw(n):
            if n['uid'] in visited or n['uid'] not in matches:
                return
            visited.add(n['uid'])
            children = [child for child in nodes if child['parent_id'] == n['id'] and set(child['connection_ids']) & set(n['connection_ids'])]
            label = n['name'] + ' · ' + n['id']
            if n['metadata'].get('management'):
                label += ' · Management account'
            if children:
                with st.expander(label, expanded=True):
                    if st.button('Inspect ' + n['type'], key='node-' + n['uid']):
                        st.session_state['hierarchy-selected'] = n['uid']
                    for child in children:
                        draw(child)
            else:
                links = linked(n)
                state = 'Connected' if any(c.get('connection_status') == 'CONNECTED' for c in links) else 'Configured' if links else 'Disconnected'
                if st.button(label + ' · ' + state, key='node-' + n['uid'], use_container_width=True):
                    st.session_state['hierarchy-selected'] = n['uid']
        for n in nodes:
            if not n['parent_id'] or n['parent_id'] not in by_id:
                draw(n)
        if not matches:
            st.info('No matching nodes.')
    with panel, st.container(border=True, key='hierarchy-details'):
        selected = next((n for n in nodes if n['uid'] == st.session_state.get('hierarchy-selected')), None)
        if selected is None:
            selected = next((n for n in nodes if n['type'] == TARGET_TYPES[provider]), nodes[0])
        st.subheader(selected['name'])
        tab = st.radio('Details', ['Overview', 'Cost', 'Resources', 'Regions', 'Connection'], horizontal=True, key='hierarchy-tab')
        links = [c for c in connections if identity(c) == selected['id'] and c['connection_id'] in selected['connection_ids']]
        if tab == 'Overview':
            st.write({'Name': selected['name'], 'Unique ID': selected['id'], 'Provider': provider,
                      'Type': selected['type'], 'Parent': selected['parent_id'], **selected['metadata']})
            children = [n for n in nodes if n['parent_id'] == selected['id']]
            if children:
                st.write('Immediate children: ' + str(len(children)))
            for c in links:
                st.caption(c['name'] + ' · ' + c.get('connection_status', 'Configured') + ' · Last verified: ' + str(c.get('last_tested_at') or 'Never'))
                result = query(c['connection_id'], 'resources', selected['id'])
                st.caption('Last successful inventory synchronization: ' + str(result['last_successful_sync'] or 'Never'))
                for error in result['errors']:
                    st.warning(error)
                resources = result['data']
                a, b, d, expenditure = st.columns(4)
                a.metric('Resources observed', len(resources) if result['status'] in {'Available', 'Partial'} else 'Unavailable')
                b.metric('Services observed', len({r['service'] for r in resources}) if resources else 'Unavailable')
                d.metric('Inventory coverage', result['status'])
                from multicloud_dashboard import cost_totals
                from cost_dashboard_ui import money
                cost_rows = [r for result in snapshot.get('costs', []) for r in result['data'] if r['target'] == selected['id']]
                expenditure.metric('Monthly spend · MTD', money(cost_totals([{'data': cost_rows}])))
                counts = Counter(r['service'] for r in resources)
                if counts:
                    st.dataframe([{'Service': name, 'Resource count': count,
                        'Observed statuses': ', '.join(sorted({r['status'] for r in resources if r['service'] == name}))} for name, count in sorted(counts.items())],
                        hide_index=True, use_container_width=True)
                st.write('Regions: ' + (', '.join(sorted({r.get('region') or 'Unavailable' for r in result['data']})) or 'Unavailable'))
        elif tab == 'Cost':
            descendants = {selected['id']}
            for _ in nodes:
                before = len(descendants)
                descendants.update(n['id'] for n in nodes if n['parent_id'] in descendants and set(n['connection_ids']) & set(selected['connection_ids']))
                if len(descendants) == before:
                    break
            rows = [r for result in snapshot.get('costs', []) for r in result['data'] if r['target'] in descendants]
            for result in snapshot.get('costs', []):
                st.caption(result.get('connection_name', '') + ' · ' + result['status'] + ' · Last successful synchronization: ' + str(result.get('last_successful_sync') or 'Never'))
            if rows:
                render_cost(rows)
            else:
                st.info('No billing data is available for this scope. Configure billing read access and synchronize the overview.')
        elif tab == 'Connection':
            if not links:
                st.info('Discovered node. A matching target connection is required for resource and cost inspection.')
            for c in links:
                st.write({'Connection': c['name'], 'Status': c.get('connection_status'), 'Last verified': c.get('last_tested_at')})
                if st.button('Use in provisioning', key='activate-' + c['connection_id'], disabled=c.get('connection_status') != 'CONNECTED'):
                    st.session_state['iipp_active_provider'] = provider
                    st.session_state['iipp_active_connection_id'] = c['connection_id']
                    st.session_state['requested-page'] = 'Provisioning Agent'
                    st.rerun()
                if st.button('Reconnect', key='reconnect-' + c['connection_id']):
                    configure(provider, c['connection_id'])
        elif not links:
            st.info('Connect this account, subscription, or project to inspect its resources and billing. Container nodes provide hierarchy context.')
        else:
            c = st.selectbox('Connection', links, format_func=lambda c: c['name'], key='detail-connection-' + selected['uid'])
            section = 'cost' if tab == 'Cost' else 'resources'
            refresh = st.button('Synchronize ' + section, key='sync-' + selected['uid'])
            with st.spinner('Loading ' + section):
                result = query(c['connection_id'], section, selected['id'], refresh)
            st.caption(result['status'] + ' · Last successful synchronization: ' + str(result['last_successful_sync'] or 'Never'))
            for error in result['errors']:
                st.warning(error)
            rows = result['data']
            if not rows:
                st.info('No data returned. Availability and collection coverage are shown above.')
            elif tab == 'Cost':
                render_cost(rows)
            elif tab == 'Regions':
                st.dataframe([{'Region': region, 'Resources': count} for region, count in Counter(r.get('region') or 'Unavailable' for r in rows).items()], hide_index=True)
            else:
                st.caption('Observed resources include running, stopped and persistent services. Unknown runtime states are shown as unavailable. Per-resource costs require resource-level billing and are not allocated from service totals.')
                state = st.selectbox('Resource state', ['All resources', 'Running / active', 'Stopped / inactive', 'Status unavailable'], key='resource-state-' + selected['uid'])
                def matches(row):
                    status = str(row.get('status') or 'Unavailable').casefold()
                    if state == 'Running / active':
                        return status in {'running', 'active', 'available', 'vm running', 'powerstate/running'}
                    if state == 'Stopped / inactive':
                        return status in {'stopped', 'stopping', 'terminated', 'inactive', 'deallocated', 'vm stopped', 'vm deallocated'}
                    return status == 'unavailable' if state == 'Status unavailable' else True
                rows = [row for row in rows if matches(row)]
                if not rows:
                    st.info('No observed resources match this state.')
                for service in sorted({r['service'] for r in rows}):
                    items = [r for r in rows if r['service'] == service]
                    with st.expander(f'{service} ({len(items)})'):
                        st.dataframe(items, hide_index=True, use_container_width=True)


def render_cost(rows):
    import pandas as pd
    from datetime import datetime, timezone
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    service_rows = [r for r in rows if r['dimension'] == 'service']
    for currency in sorted({r['currency'] for r in rows}):
        selected = [r for r in service_rows if r['currency'] == currency]
        month = sum((Decimal(str(r['amount'])) for r in selected if r['date'].startswith(current)), Decimal(0))
        previous = sum((Decimal(str(r['amount'])) for r in selected if not r['date'].startswith(current)), Decimal(0))
        month_text = f'{month:,.2f}' if any(r['date'].startswith(current) for r in selected) else 'Unavailable'
        previous_text = f'{previous:,.2f}' if any(not r['date'].startswith(current) for r in selected) else 'Unavailable'
        st.write(f'{currency} · Current month: {month_text} · Previous month: {previous_text}')
        frame = pd.DataFrame(selected)
        frame['amount'] = frame['amount'].astype(float)
        st.line_chart(frame.groupby('date')['amount'].sum())
        for dimension in ('service', 'region', 'record_type'):
            data = pd.DataFrame([r for r in rows if r['currency'] == currency and r['dimension'] == dimension and r['date'].startswith(current)])
            if not data.empty:
                data['amount'] = data['amount'].astype(float)
                st.caption('MTD by ' + dimension)
                summary = data.groupby('category', as_index=False)['amount'].sum().sort_values('amount', ascending=False)
                st.dataframe(summary, hide_index=True)
                if dimension == 'service':
                    st.caption('Top five services · ' + currency)
                    st.bar_chart(summary.head(5).set_index('category'))
        st.caption('Account / subscription / project: ' + ', '.join(sorted({r['target'] for r in selected})))
    st.caption('Provider-reported usage costs; current-period values may be provisional. AWS: unblended. Azure: actual. GCP: exported cost including credits. Missing export history is unavailable.')
