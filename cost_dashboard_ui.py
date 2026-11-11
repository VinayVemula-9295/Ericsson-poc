"""Billing-only presentation; no catalogue estimates or currency conversion."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import streamlit as st


def summarize(snapshots):
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    totals, accounts = defaultdict(Decimal), {}
    for provider, snapshot in snapshots.items():
        for result in snapshot.get('costs', []):
            for row in result.get('data', []):
                if row['dimension'] != 'service':
                    continue
                period = 'MTD' if row['date'].startswith(current) else 'Previous month'
                key = (provider, result.get('owner', 'single'), row['target'], row['currency'])
                item = accounts.setdefault(key, {'Cloud': provider, 'Scope': result.get('owner', 'single'), 'Account / Subscription / Project': row['target'],
                    'Currency': row['currency'], 'MTD': None, 'Previous month': None, 'Provisional': False})
                item[period] = (item[period] or Decimal(0)) + Decimal(str(row['amount']))
                item['Provisional'] |= bool(row.get('estimated'))
                if period == 'MTD':
                    totals[row['currency']] += Decimal(str(row['amount']))
    return dict(totals), list(accounts.values())


def money(totals):
    def display(value):
        rounded = Decimal(str(value)).quantize(Decimal('0.01'))
        return f'{abs(rounded) if rounded == 0 else rounded:,.2f}'
    return ' / '.join(f'{c} {display(v)}' for c, v in sorted(totals.items())) or 'Unavailable'


def render(snapshots):
    totals, accounts = summarize(snapshots)
    with st.container(border=True, key='organization-costs'):
        st.subheader('Organization cloud expenditure')
        partial = any(not s.get('costs') or s.get('partial_cost') for s in snapshots.values())
        cols = st.columns(4)
        cols[0].metric('Combined month to date', money(totals))
        for column, provider in zip(cols[1:], ('AWS', 'Azure', 'GCP')):
            column.metric(provider + ' month to date', money(snapshots[provider]['totals']))
        st.caption('UTC calendar month · Provider billing data · ' + ('Partial coverage: some accounts or providers are unavailable.' if partial else 'All discovered targets covered.'))
        st.caption('AWS unblended usage, Azure actual cost, GCP exported usage including credits. Open-period costs may be provisional. Currencies are kept separate; compute estimates are excluded.')
        with st.expander('Account costs, service comparison and synchronization'):
            sync = []
            for provider, snapshot in snapshots.items():
                if not snapshot.get('costs'):
                    sync.append({'Cloud': provider, 'Connection': 'No billing data', 'Status': 'Unavailable', 'Last successful synchronization': 'Never'})
                for result in snapshot.get('costs', []):
                    sync.append({'Cloud': provider, 'Connection': result.get('connection_name', 'Configured target'),
                        'Status': result['status'], 'Last successful synchronization': result.get('last_successful_sync') or 'Never'})
            st.dataframe(sync, hide_index=True, use_container_width=True)
            if not accounts:
                st.info('Billing data is unavailable. Connect an account and grant the documented billing read permissions.')
                return
            display = []
            for item in accounts:
                row = dict(item)
                now, previous = row['MTD'], row['Previous month']
                row['Change vs full previous month'] = f'{(now - previous) / abs(previous) * 100:+.1f}%' if now is not None and previous else 'Unavailable'
                for field in ('MTD', 'Previous month'):
                    row[field] = f'{row[field]:,.2f}' if row[field] is not None else 'Unavailable'
                display.append(row)
            st.dataframe(display, hide_index=True, use_container_width=True)
            st.caption('Previous month is a full month, not a matched elapsed-day comparison. Missing export history is unavailable.')
            import pandas as pd
            current = datetime.now(timezone.utc).strftime('%Y-%m')
            for currency in totals:
                data = []
                for provider, snapshot in snapshots.items():
                    for result in snapshot.get('costs', []):
                        data.extend({'Cloud': provider, 'Service': r['category'], 'Cost': float(r['amount'])} for r in result['data']
                            if r['currency'] == currency and r['dimension'] == 'service' and r['date'].startswith(current))
                if data:
                    frame = pd.DataFrame(data)
                    st.caption(currency + ' · MTD cost by provider')
                    st.bar_chart(frame.groupby('Cloud')['Cost'].sum(), color='#7051c5')
                    st.dataframe(frame.groupby(['Cloud', 'Service'], as_index=False)['Cost'].sum(), hide_index=True, use_container_width=True)
