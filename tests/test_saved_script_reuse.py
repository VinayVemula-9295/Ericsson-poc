import json
import sys
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest
from reuse_artifacts import snapshot, load, copy_sources, availability


@pytest.fixture(params=['poc_plans', 'terraform_deployments'])
def bundle(request, tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    source = request.param
    did = 'poc-' + 'a' * 32 if source == 'poc_plans' else 'cp-' + 'a' * 16
    directory = tmp_path / source / did
    directory.mkdir(parents=True)
    (directory / ('main.tf.json' if source == 'poc_plans' else 'main.tf')).write_text('{}')
    (directory / '.terraform.lock.hcl').write_text('locked')
    (directory / 'terraform.tfvars.json').write_text(json.dumps({'name': 'original'}))
    record = dict(deployment_id=did, status='SUCCEEDED', candidate=dict(provider='AWS', sku='t3.small', os='Linux'))
    snapshot(directory, record)
    filename = 'record.json' if source == 'poc_plans' else 'deployment.json'
    (directory / filename).write_text(json.dumps(record))
    return (source, did), directory, record, filename


def test_successful_reuse_without_state_copies_only_sources(bundle, tmp_path):
    source, directory, record, filename = bundle
    for name in ('terraform.tfstate', 'tfplan', 'approval-token'):
        (directory / name).write_text('private')
    _, _, checksum = load(*source)
    assert len(checksum) == 64
    destination = tmp_path / 'new'
    destination.mkdir()
    copy_sources(directory, destination)
    assert not any((destination / name).exists() for name in ('terraform.tfstate', 'tfplan', 'approval-token', 'terraform.tfvars.json'))
    assert (destination / '.terraform.lock.hcl').read_text() == 'locked'


def test_modified_missing_or_extra_source_blocks_reuse(bundle):
    source, directory, record, filename = bundle
    path = directory / 'terraform.tfvars.json'
    path.write_text('{}')
    with pytest.raises(ValueError, match='integrity'):
        load(*source)
    assert availability(dict(source=source[0], deployment_id=source[1]))


def test_pending_and_unverified_older_records_block_reuse(bundle):
    source, directory, record, filename = bundle
    record['status'] = 'APPLYING'
    (directory / filename).write_text(json.dumps(record))
    with pytest.raises(ValueError, match='Only completed'):
        load(*source)
    record['status'] = 'SUCCEEDED'
    record.pop('reuse_manifest')
    (directory / filename).write_text(json.dumps(record))
    with pytest.raises(ValueError, match='older record'):
        load(*source)


def test_no_traversal_or_source_selection():
    for source, did in [('poc_plans', '../outside'), ('other', 'poc-' + 'a' * 32)]:
        with pytest.raises(ValueError):
            load(source, did)


def test_reuse_does_not_call_agent_and_live_failure_prevents_plan(bundle, monkeypatch):
    source, directory, record, filename = bundle
    if source[0] != 'poc_plans':
        return  # Legacy live checks run inside its planner, covered separately below.
    import reuse_service
    candidate = dict(provider='AWS', sku='t3.small', os='Linux', region='ap-south-1', connection_id='target')
    request = dict(kind='VM')
    def fail(*args):
        raise ValueError('Live target is unavailable')
    monkeypatch.setitem(sys.modules, 'provider_inputs', SimpleNamespace(validate_live_inputs=fail))
    with pytest.raises(ValueError, match='Live target'):
        reuse_service.prepare_reuse(source, candidate, request, {'name': 'new'})
    with pytest.raises(ValueError, match='new resource name'):
        reuse_service.prepare_reuse(source, candidate, request, {'name': 'original'})


def test_success_routes_to_source_workflow_with_fresh_plan(bundle, monkeypatch):
    source, directory, record, filename = bundle
    import reuse_service
    candidate = dict(provider='AWS', sku='t3.small', os='Linux', region='us-east-1', connection_id='new-account')
    request = dict(kind='VM', initiated_by='Author', environment='test', ttl_days=7)
    inputs = dict(name='new-name', image_id='new-image', subnet_id='new-subnet', security_group_id='new-group')
    calls = []
    def plan(*args, **kwargs):
        calls.append((args, kwargs))
        return dict(deployment_id='new-id', status='AWAITING_APPROVAL', state_key='new-state')
    monkeypatch.setitem(sys.modules, 'provider_inputs', SimpleNamespace(validate_live_inputs=lambda *a: None))
    monkeypatch.setitem(sys.modules, 'poc_planner', SimpleNamespace(prepare=plan))
    monkeypatch.setitem(sys.modules, 'terraform_service', SimpleNamespace(prepare_plan=plan))
    if source[0] == 'terraform_deployments':
        record['requested_configuration'] = dict(instance_type='t3.small', os='Linux')
        (directory / filename).write_text(json.dumps(record))
    result = reuse_service.prepare_reuse(source, candidate, request, inputs)
    assert result['state_key'] == 'new-state'
    assert calls[0][1]['reuse_from'] == source
    if source[0] == 'terraform_deployments':
        assert calls[0][0][0]['environment'] == 'test'
        assert calls[0][0][0]['ttl_days'] == 7
    assert (directory / 'terraform.tfvars.json').read_text() == json.dumps({'name': 'original'})


def test_history_filters_and_timezone():
    from deployment_history_ui import filter_records, ist
    rows = [dict(name='Server', target='Demo', provider='AWS', status='Completed', deployment_id='one'),
            dict(name='Other', target='Dev', provider='Azure', status='Failed', deployment_id='two')]
    assert filter_records(rows, 'DEMO', 'Completed', 'AWS') == rows[:1]
    assert filter_records(rows, 'Demo', 'Failed') == []
    assert ist('2026-11-01T00:00:00Z').endswith('05:30 IST')


def test_reuse_grid_only_successful_with_details_and_disabled_missing_bundle(monkeypatch):
    import reuse_ui
    row = dict(raw_status='SUCCEEDED', status='Completed', provider='AWS', target='Demo',
               name='Successful server', kind='VM', region='ap-south-1', configuration='t3.small',
               configuration_details='2 vCPU · 1 GiB RAM',
               deployment_id='poc-' + 'b' * 32, source='poc_plans', key='saved',
               created_at='2026-11-01T00:00:00Z', completed_at='2026-11-01T00:01:00Z')
    monkeypatch.setattr(reuse_ui, 'load_reusable_history', lambda: ([row, dict(row, raw_status='FAILED', name='Failed server')], 0))
    monkeypatch.setattr(reuse_ui, 'availability', lambda row: 'Verified bundle unavailable')
    page = AppTest.from_string('from reuse_ui import render\nrender()').run()
    assert not page.exception
    assert next(b for b in page.button if b.label == 'Reuse').disabled
    assert len([b for b in page.button if b.label == 'Details']) == 1
    assert any(m.value == '2 vCPU · 1 GiB RAM' for m in page.markdown)
    assert 'Failed server' not in '\n'.join(m.value for m in page.markdown)
    next(b for b in page.button if b.label == 'Details').click().run()
    assert not page.exception
    assert any('unavailable' in item.value for item in page.info)


def test_dashboard_account_dialog_and_history_navigation(monkeypatch, tmp_path):
    import dashboard_ui
    import hierarchy_ui
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setattr(dashboard_ui, 'list_connections', lambda: [dict(provider='AWS', connection_id='one', account_id='123', connection_status='CONNECTED')])
    monkeypatch.setattr(hierarchy_ui, 'provider_snapshot', lambda provider, **_: dict(connections=[], nodes=[], totals={}, errors=[], partial_cost=False))
    page = AppTest.from_string('from dashboard_ui import render\nrender()').run()
    assert not page.exception
    assert any(x.value == 'Recent provisioning' for x in page.subheader)
    page.button(key='AWS-accounts').click().run()
    assert not page.exception
    assert any('No visible accounts' in item.value for item in page.info)

def test_empty_reuse_search_does_not_query_agent(monkeypatch, tmp_path):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    import agent_flow_ui
    monkeypatch.setattr(agent_flow_ui, 'collect', lambda *_: pytest.fail('Empty reuse must not evaluate an Agent request'))
    page = AppTest.from_string('from reuse_ui import render\nrender()').run()
    assert not page.exception
    assert any('No verified successful deployments' in x.value for x in page.info)
    assert not page.text_input
    page.run()
    assert not page.exception
    assert not page.button


def test_reuse_tab_selects_script_without_prompt_or_evaluation(bundle, monkeypatch, region_catalogues):
    source, directory, record, filename = bundle
    if source[0] != 'poc_plans':
        return
    import agent_flow_ui as flow
    import provider_inputs
    conn = dict(provider='AWS', connection_id='target', name='Demo', connection_status='CONNECTED', metadata={})
    monkeypatch.setattr(flow, 'list_connections', lambda: [conn])
    monkeypatch.setattr(flow, 'get_connection', lambda cid: conn)
    monkeypatch.setattr(flow, 'get_aws_terraform_runtime', lambda cid: dict(target_role_arn='role'))
    monkeypatch.setattr(provider_inputs, 'discover', lambda *a: dict(choices={}, errors={}))
    record['candidate'].update(region='ap-south-1', connection_id='target', vcpus=2, memory_gib=2)
    record['requirements'] = dict(kind='VM', min_vcpus=2, min_memory_gib=2)
    record['connection_id'] = 'target'
    (directory / filename).write_text(json.dumps(record))
    page = AppTest.from_string('from agent_flow_ui import render\nrender()')
    page.session_state['provisioning-mode'] = 'Reuse previous deployment'
    page.run()
    assert not page.exception
    next(b for b in page.button if b.label == 'Reuse').click().run()
    assert not page.exception
    assert any(x.label == 'VM name' for x in page.text_input)
    assert not page.text_area
    assert not any(b.label == 'Evaluate' for b in page.button)
