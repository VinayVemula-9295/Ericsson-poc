"""Derive progress from persisted execution states, never inferred cloud success."""
STEPS = ('Configure target', 'Validate and plan', 'Review and approve', 'Execute deployment', 'Verify outcome')
POSITIONS = {'DRAFT': 0, 'PREPARING': 1, 'VALIDATED': 1, 'AWAITING_APPROVAL': 2,
             'PLAN_READY_EXECUTION_UNVALIDATED': 2, 'REVIEWED_EXECUTION_DISABLED': 2,
             'REJECTED': 2, 'CANCELLED': 2, 'APPLYING': 3, 'SUCCEEDED': 4}


def progress(status, history=()):
    failed = status in {'FAILED', 'DESTROY_FAILED', 'DESTROY_PLAN_FAILED'}
    prior = [item.get('status') for item in history if item.get('status') in POSITIONS]
    position = POSITIONS.get(status, POSITIONS.get(prior[-1], 1) if prior else 1)
    destroyed = status.startswith('DESTROY')
    if destroyed:
        position = 4
    complete = status == 'SUCCEEDED' or status == 'DESTROYED'
    return [dict(label=label, state=('completed' if index < position or complete else
        'failed' if failed and index == position else 'cancelled' if status in {'CANCELLED', 'REJECTED'} and index == position else
        'current' if index == position else 'pending')) for index, label in enumerate(STEPS)]


def render(row):
    from html import escape
    import streamlit as st
    steps = progress(row['raw_status'], row.get('execution_events', []))
    st.markdown('<nav class="deployment-steps" aria-label="Deployment progress">' + ''.join(
        f'<div class="deployment-step {s["state"]}"><span>{"&#10003;" if s["state"] == "completed" else i + 1}</span><strong>{escape(s["label"])}</strong><small>{s["state"].title()}</small></div>'
        for i, s in enumerate(steps)) + '</nav>', unsafe_allow_html=True)
    selected = st.radio('Inspect deployment step', [s['label'] for s in steps], horizontal=True,
                        index=next((i for i, s in enumerate(steps) if s['state'] in {'current', 'failed', 'cancelled'}), 4),
                        key='workflow-step-' + row['key'])
    index = [s['label'] for s in steps].index(selected)
    if steps[index]['state'] == 'pending':
        st.info('This step has not been reached. Complete the preceding steps through the reviewed provisioning workflow.')
    elif index == 0:
        st.write({'Cloud': row['provider'], 'Account / Subscription / Project': row['target'], 'Region': row['region'], 'Configuration': row['configuration']})
    elif index == 1:
        st.caption('Saved Terraform planning runs policy, target and input checks. The exact saved plan requires approval before execution.')
        st.write('Current recorded status: ' + row['status'])
    elif index == 2:
        st.write('Reviewer: ' + row.get('reviewer', 'Unavailable'))
        if row['raw_status'] == 'AWAITING_APPROVAL':
            from control_plane import enabled
            if enabled():
                from control_plane_ui import submission
                submission(row['source'], row['deployment_id'])
            else:
                st.caption('Open the saved plan in Provisioning Agent to review and approve execution.')
        else:
            st.caption('Approval controls are available only for a saved plan awaiting approval.')
    elif index == 3:
        st.write('Execution status: ' + row['status'])
        st.caption('Execution follows the existing provider support and approval gates. Azure/GCP plans retain their execution availability limits.')
    else:
        if row['raw_status'] == 'SUCCEEDED':
            st.success('The deployment runner recorded successful execution.')
            if row.get('resource_identifiers'):
                st.write(row['resource_identifiers'])
        else:
            st.write('Latest outcome: ' + row['status'])
    with st.expander('Execution event log', expanded=True):
        if row.get('state_history'):
            st.dataframe(row['state_history'], hide_index=True, use_container_width=True)
        else:
            st.caption('This older deployment has no recorded transition log.')
        st.caption('Only saved execution events are displayed. Raw runner output remains in protected diagnostics to avoid exposing secrets.')
