import json
import pytest
from dashboard_ui import account_rows
from terraform_draft import generate, validate
from deployment_history import load_reuse


def test_multicloud_account_states_and_unknown_cost():
    rows = account_rows([dict(provider='AWS', connection_id='a', account_id='1', connection_status='CONNECTED'),
                         dict(provider='AWS', connection_id='b', account_id='2', connection_status='SAVED_NOT_TESTED'),
                         dict(provider='Azure', connection_id='c', subscription_id='s', connection_status='FAILED')])
    assert len(rows) == 3
    assert rows[0]['Connected'] == '🟢 Connected'
    assert rows[1]['Connected'] == '⚪ Not verified'
    assert rows[2]['Connected'] == '🔴 Failed'
    assert all(r['Cost'] == 'Unavailable' for r in rows)


def test_draft_edits_and_protected_provider():
    candidate = dict(provider='AWS', region='ap-south-1', sku='t3.small', os='Linux')
    request = dict(kind='VM')
    inputs = dict(name='original', region='ap-south-1', execution_role_arn='approved-role')
    draft = generate(candidate, request, inputs)
    draft['inputs']['name'] = 'edited'
    assert validate(json.dumps(draft), candidate, request, inputs)['inputs']['name'] == 'edited'
    draft['inputs']['region'] = 'us-east-1'
    with pytest.raises(ValueError, match='Account'):
        validate(json.dumps(draft), candidate, request, inputs)
    draft = generate(candidate, request, inputs)
    draft['configuration']['terraform']['backend'] = {'local': {}}
    with pytest.raises(ValueError, match='protected'):
        validate(json.dumps(draft), candidate, request, inputs)
    with pytest.raises(ValueError, match='Invalid JSON'):
        validate('{', candidate, request, inputs)


def test_successful_reuse_without_state_and_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    deployment_id = 'poc-' + 'a' * 32
    directory = tmp_path / 'poc_plans' / deployment_id
    directory.mkdir(parents=True)
    record = dict(status='SUCCEEDED', candidate=dict(provider='AWS', sku='t3.small', region='ap-south-1', vcpus=2, memory_gib=2))
    (directory / 'terraform.tfvars.json').write_text(json.dumps(dict(name='saved', admin_password='never-expose', external_id='never-expose')))
    (directory / 'main.tf.json').write_text('{}')
    (directory / '.terraform.lock.hcl').write_text('locked')
    from reuse_artifacts import snapshot
    snapshot(directory, record)
    (directory / 'record.json').write_text(json.dumps(record))
    draft = load_reuse('poc_plans', deployment_id)
    assert draft['inputs'] == {'name': 'saved'}
    assert draft['state_reference'] is None
    with pytest.raises(ValueError):
        load_reuse('poc_plans', '../outside')
