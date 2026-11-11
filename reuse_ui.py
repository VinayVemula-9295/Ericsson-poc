"""Search and review saved infrastructure without another Agent conversation."""
import copy
import json
import streamlit as st
from deployment_history import load_reusable_history, load_reuse
from deployment_history_ui import filter_records, reuse_action, ist
from reuse_artifacts import load, availability


@st.dialog('Previous deployment configuration', width='large')
def configuration_details(row):
    st.subheader(row['name'])
    from control_plane import enabled
    if enabled():
        if st.button('Edit requirements and evaluate new options', key='edit-requirements-' + row['key']):
            try:
                saved = load_reuse(row['source'], row['deployment_id'])
                st.session_state['poc-prompt'] = saved['prompt']
                st.session_state['cp-edit-requirements'] = True
                st.rerun()
            except (ValueError, OSError):
                st.error('Saved requirements unavailable; start a new request.')
    st.write({'Deployment ID': row['deployment_id'], 'Cloud': row['provider'],
              'Account': row['target'], 'Region': row['region'],
              'Configuration': row['configuration'], 'Status': row['status'],
              'Created': ist(row['created_at']), 'Completed': ist(row.get('completed_at'))})
    try:
        saved = load_reuse(row['source'], row['deployment_id'])
        st.write('Saved input values')
        st.json(saved['inputs'])
    except (ValueError, OSError, KeyError, TypeError):
        st.info('Saved input values are unavailable.')


def render():
    st.caption('Reuse saved Terraform to create a separate deployment. Review a fresh plan before applying.')
    rows, unreadable = load_reusable_history()
    rows = [r for r in rows if r['raw_status'] == 'SUCCEEDED']
    if unreadable:
        st.warning(f'{unreadable} saved records could not be read.')
    if not rows:
        st.info('No verified successful deployments are available for reuse. Complete a deployment first.')
        return
    search = st.text_input('Search deployments', placeholder='Resource, deployment ID or account', key='reuse-search')
    with st.expander('Advanced search'):
        a, b, c = st.columns(3)
        provider = a.selectbox('Cloud provider', ['All', 'AWS', 'Azure', 'GCP'], key='reuse-cloud')
        account = b.selectbox('Account', ['All'] + sorted({r['target'] for r in rows}), key='reuse-account')
        region = c.selectbox('Region', ['All'] + sorted({r['region'] for r in rows}), key='reuse-region')
        status = 'All'
        start = b.date_input('From date', value=None, key='reuse-start')
        end = c.date_input('To date', value=None, key='reuse-end')
        if st.button('Clear filters'):
            for key in ('reuse-cloud', 'reuse-account', 'reuse-region', 'reuse-status', 'reuse-start', 'reuse-end', 'reuse-search'):
                st.session_state.pop(key, None)
            st.rerun()
    if start and end and start > end:
        st.error('From date must precede To date.')
        return
    from datetime import datetime, timedelta, timezone
    def in_range(row):
        if not start and not end:
            return True
        try:
            dt = datetime.fromisoformat(row['created_at'].replace('Z', '+00:00'))
            date = dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30))).date()
            return (not start or date >= start) and (not end or date <= end)
        except ValueError:
            return False
    filtered = [r for r in filter_records(rows, search, status, provider)
                if (account == 'All' or account == r['target']) and (region == 'All' or region == r['region']) and in_range(r)]
    st.caption(f'{len(filtered)} deployments found')
    if not filtered:
        st.info('No deployments match your search.')
    else:
        headers = st.columns([2, 2, 2, 2, 1, 1])
        for column, label in zip(headers, ['Cloud / Account', 'Resource / Region', 'Previous configuration', 'Configuration details', 'Details', 'Reuse']):
            column.markdown('**' + label + '**')
    for row in filtered:
        with st.container(border=True):
            cloud, resource, config, sizing, details, action = st.columns([2, 2, 2, 2, 1, 1])
            cloud.write(row['provider'] + ' · ' + row['target'])
            resource.write(row['name'])
            resource.caption(row['kind'] + ' · ' + row['region'])
            config.write(row['configuration'])
            sizing.write(row.get('configuration_details', 'Sizing unavailable'))
            if details.button('Details', key='reuse-details-' + row['key']):
                configuration_details(row)
            with action:
                reason = availability(row)
                if reason:
                    st.caption(reason)
                reuse_action(row, 'agent-')
    source = st.session_state.get('reuse-source')
    if not source:
        return
    try:
        directory, record, checksum = load(*source)
        candidate, request, inputs = reuse_values(source, directory, record)
        key = (source, checksum)
        if st.session_state.get('reuse-loaded') != key:
            from agent_flow_ui import clear_result
            clear_result()
            st.session_state['reuse-loaded'] = key
            st.session_state['reuse-draft'] = dict(inputs=inputs, connection_id=candidate.get('connection_id'), region=candidate['region'])
        import cloud_regions
        catalogue = cloud_regions.load_catalogue(candidate['provider'])
        regions = [r['value'] for r in catalogue['regions']]
        region = st.selectbox('Deployment region', regions, index=regions.index(candidate['region']) if candidate['region'] in regions else 0, key='reuse-target-region-' + source[1])
        candidate['region'] = region
        st.subheader('Reuse ' + record.get('resource_name', source[1]))
        st.caption('Review the previous configuration and edit the supported inputs. Submit prepares a fresh plan for approval.')
        if candidate['provider'] != 'AWS':
            st.info('Saved script execution is currently available for AWS only.')
            return
        from agent_flow_ui import render_inputs
        render_inputs(candidate, request, source)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        st.error('Saved configuration cannot be reused: ' + str(exc))


def reuse_values(source, directory, record):
    inputs = json.loads((directory / 'terraform.tfvars.json').read_text(encoding='utf-8'))
    inputs = {k: v for k, v in inputs.items() if k not in {'admin_password', 'external_id'}}
    candidate = copy.deepcopy(record.get('candidate') or {})
    request = copy.deepcopy(record.get('requirements') or {})
    if source[0] == 'terraform_deployments':
        cfg = record['requested_configuration']
        candidate = dict(provider='AWS', region=cfg['region'], sku=cfg['instance_type'], os=cfg.get('os', 'Linux'),
                         vcpus=cfg.get('min_vcpus', 1), memory_gib=cfg.get('min_memory_gib', 1),
                         connection_id=cfg['aws_connection_id'], available=True, offered_in_target_location=True)
        request = dict(kind='VM', os=candidate['os'], min_vcpus=candidate['vcpus'], min_memory_gib=candidate['memory_gib'],
                       environment=cfg.get('environment', 'poc'), ttl_days=cfg.get('ttl_days', 2))
        for old, new in [('instance_name', 'name'), ('ami_id', 'image_id')]:
            inputs[new] = inputs.get(old, '')
    candidate['connection_id'] = record.get('connection_id') or candidate.get('connection_id')
    request.setdefault('environment', 'poc')
    request.setdefault('ttl_days', 2)
    inputs['name'] = ''
    return candidate, request, inputs


def plan_controls(candidate, request, inputs, source):
    from agent_flow_ui import fingerprint, plan_dialog, approval_dialog, success_dialog
    initiator = st.text_input('Initiated by', key='reuse-initiator')
    payload_hash = fingerprint((candidate, request, inputs, initiator, source))
    if st.session_state.get('poc-input-fingerprint') != payload_hash:
        for key in ('poc-saved-plan', 'poc-open-approval', 'poc-open-plan'):
            st.session_state.pop(key, None)
    st.session_state['poc-input-fingerprint'] = payload_hash
    if st.button('Submit', type='primary', disabled=not inputs.get('name') or not initiator.strip()):
        try:
            from reuse_service import prepare_reuse
            with st.spinner('Validating saved Terraform and preparing a fresh plan…'):
                record = prepare_reuse(source, candidate, dict(request, initiated_by=initiator.strip()), inputs)
            st.session_state['poc-saved-plan'] = record
            st.session_state['poc-open-plan'] = record['status'] == 'AWAITING_APPROVAL'
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, ValueError) else 'Planning failed. Check protected runner diagnostics. No infrastructure was applied.')
    record = st.session_state.get('poc-saved-plan')
    if not record:
        return
    st.caption(record['deployment_id'] + ' · ' + record['status'])
    if record['status'] == 'AWAITING_APPROVAL':
        if st.button('Review Terraform plan'):
            st.session_state['poc-open-plan'] = True
        if st.button('Terraform apply') or st.session_state.get('poc-open-approval'):
            approval_dialog(record['deployment_id'], payload_hash)
    if st.session_state.pop('poc-open-plan', False):
        plan_dialog(record)
    elif st.session_state.pop('poc-open-success', False):
        success_dialog(record)
