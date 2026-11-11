import json
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

import app_auth
import multicloud_dashboard as dashboard
from deployment_workflow import progress


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    monkeypatch.delenv('CONTROL_PLANE_USERS_FILE', raising=False)
    monkeypatch.delenv('IIPP_ADMIN_PASSWORD', raising=False)
    password, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
    monkeypatch.setenv('IIPP_ADMIN_USERNAME', 'admin')
    monkeypatch.setenv('IIPP_ADMIN_SALT', salt)
    monkeypatch.setenv('IIPP_ADMIN_PASSWORD_HASH', app_auth.password_hash(password, salt))
    app_auth._attempts.clear()
    return password


def test_password_verification_throttle_and_no_secret_in_identity(admin):
    assert app_auth.authenticate('admin', admin) == {'username': 'admin', 'role': 'administrator'}
    for _ in range(5):
        assert app_auth.authenticate('admin', 'incorrect') is None
    assert app_auth.authenticate('admin', admin) is None
    app_auth._attempts.clear()
    assert app_auth.authenticate('unknown', admin) is None


def test_session_expiry_and_absolute_limit(monkeypatch):
    monkeypatch.setenv('IIPP_SESSION_IDLE_SECONDS', '60')
    monkeypatch.setenv('IIPP_SESSION_MAX_SECONDS', '120')
    state = {'iipp-auth': {'created': 100, 'last_seen': 150}}
    assert app_auth.session_valid(state, 160)
    assert not app_auth.session_valid(state, 220)
    state['iipp-auth'] = {'created': 100, 'last_seen': 219}
    assert not app_auth.session_valid(state, 220)
    assert not app_auth.session_valid({}, 220)


def test_single_workspace_registry_requires_admin_and_disabled_users_are_denied(tmp_path, monkeypatch):
    import control_plane as cp
    password, salt = secrets.token_urlsafe(24), secrets.token_hex(16)
    registry = tmp_path / 'users.json'
    registry.write_text(json.dumps({'viewer': dict(role='viewer', salt=salt, password_hash=app_auth.password_hash(password, salt)),
        'disabled-admin': dict(role='administrator', disabled=True, salt=salt, password_hash=app_auth.password_hash(password, salt))}))
    monkeypatch.setenv('CONTROL_PLANE_USERS_FILE', str(registry))
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    app_auth._attempts.clear()
    assert app_auth.authenticate('viewer', password) is None
    assert app_auth.authenticate('disabled-admin', password) is None
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'true')
    assert app_auth.authenticate('viewer', password)['role'] == 'viewer'


@pytest.mark.parametrize('state,expected', [('AWAITING_APPROVAL', 'current'), ('APPLYING', 'completed'), ('REJECTED', 'cancelled')])
def test_workflow_does_not_skip_review(state, expected):
    steps = progress(state)
    assert steps[2]['state'] == expected
    assert steps[4]['state'] == 'pending'


def test_workflow_failure_uses_persisted_evidence():
    assert progress('FAILED', [{'status': 'APPLYING'}])[3]['state'] == 'failed'
    assert progress('FAILED', [{'status': 'PREPARING'}])[2]['state'] == 'pending'
    assert all(s['state'] == 'completed' for s in progress('SUCCEEDED'))
    assert progress('DESTROY_FAILED')[4]['state'] == 'failed'


def cost_row(target, amount, dimension='service', date=None):
    return dict(target=target, amount=amount, dimension=dimension, currency='USD', category='EC2',
                date=date or datetime.now(timezone.utc).strftime('%Y-%m-01'), estimated=True)


def test_organization_billing_requires_explicit_scope_and_discovery(monkeypatch):
    c = dict(connection_id='one', provider='AWS', name='Management', account_id='123', metadata={'discovery_boundary': 'o-approved'})
    monkeypatch.setattr(dashboard, 'get_connection', lambda _: c)
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    dashboard._cache.clear()
    with pytest.raises(ValueError, match='Approved'):
        dashboard.query('one', 'organization_cost', 'o-approved')
    c['metadata']['billing_scope'] = 'organization'
    adapter = MagicMock()
    adapter.hierarchy.return_value = [dict(id='o-approved', type='organization', metadata={'management_account_id': '123'}), dict(id='123', type='account'), dict(id='456', type='account')]
    adapter.client.return_value.get_caller_identity.return_value = {'Account': '123'}
    adapter.cost.return_value = [cost_row('123', '2'), cost_row('456', '3')]
    monkeypatch.setattr(dashboard, 'get_saved_secret', lambda _: {})
    monkeypatch.setitem(dashboard.ADAPTERS, 'AWS', lambda *_: adapter)
    assert dashboard.query('one', 'organization_cost', 'o-approved')['status'] == 'Available'
    assert adapter.cost.call_args.args[0] == ['123', '456']
    with pytest.raises(ValueError, match='Approved'):
        dashboard.query('one', 'organization_cost', 'o-other')


def test_org_and_member_billing_do_not_double_count(monkeypatch):
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    connections = [dict(connection_id='org', provider='AWS', name='Management', account_id='123',
                        metadata=dict(discovery_boundary='o-approved', billing_scope='organization')),
                   dict(connection_id='member', provider='AWS', name='Member', account_id='456', metadata={})]
    def query(cid, section, *args, **kwargs):
        rows = ([dict(id=t, name=t, type='account', parent_id=None, metadata={}) for t in ['123', '456']]
                if section == 'hierarchy' else [cost_row('123', '2'), cost_row('456', '3')] if cid == 'org' else [cost_row('456', '3')])
        return dict(status='Available', data=rows, errors=[])
    monkeypatch.setattr(dashboard, 'query', query)
    result = dashboard.provider_snapshot('AWS', connections=connections)
    assert result['totals'] == {'USD': Decimal('5')}
    assert result['billed_targets'] == 2
    assert not result['partial_cost']


def test_combined_costs_preserve_currency_and_history_gaps():
    from cost_dashboard_ui import summarize
    snapshots = {'AWS': {'costs': [{'data': [cost_row('one', '2.25'), cost_row('one', '2.25', 'region')]}]},
                 'Azure': {'costs': [{'data': [dict(cost_row('sub', '4'), currency='EUR')]}]}}
    total, accounts = summarize(snapshots)
    assert total == {'USD': Decimal('2.25'), 'EUR': Decimal('4')}
    assert all(row['Previous month'] is None for row in accounts)


def test_reuse_backend_excludes_unverified_and_non_successful(tmp_path, monkeypatch):
    from deployment_history import load_reusable_history, load_reuse
    from reuse_artifacts import snapshot, load
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    for index, status in enumerate(['SUCCEEDED', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'APPLYING', 'AWAITING_APPROVAL', 'PENDING']):
        did = 'poc-' + f'{index:032x}'
        path = tmp_path / 'poc_plans' / did
        path.mkdir(parents=True)
        for name in ['main.tf.json', 'terraform.tfvars.json', '.terraform.lock.hcl']:
            (path / name).write_text('{}')
        record = dict(deployment_id=did, status=status, candidate=dict(sku='t3.small', region='us-east-1'))
        if index != 1:
            snapshot(path, record)
        (path / 'record.json').write_text(json.dumps(record))
    rows, errors = load_reusable_history()
    assert not errors and len(rows) == 1
    assert rows[0]['deployment_id'] == 'poc-' + '0' * 32
    with pytest.raises(ValueError, match='verified'):
        load_reuse('poc_plans', 'poc-' + f'{1:032x}')
    monkeypatch.setattr('control_plane.visible_connection', lambda _: False)
    with pytest.raises(ValueError, match='denied'):
        load('poc_plans', rows[0]['deployment_id'])


def test_aws_cost_pagination_and_estimated_flag():
    from dashboard_adapters import AWSAdapter
    adapter = object.__new__(AWSAdapter)
    api = MagicMock()
    api.get_dimension_values.return_value = {'DimensionValues': [], 'ReturnSize': 0, 'TotalSize': 0}
    api.get_paginator.return_value.paginate.return_value = [dict(ResultsByTime=[dict(
        TimePeriod={'Start': '2026-11-01'}, Estimated=True, Groups=[dict(Keys=['123', 'EC2'],
        Metrics={'UnblendedCost': {'Amount': '1.23', 'Unit': 'USD'}})])])]
    adapter.client = lambda *args, **kwargs: api
    rows = adapter.cost(['123'], '2026-10-01', '2026-11-11')
    assert len(rows) == 3 and all(r['estimated'] for r in rows)
    assert {r['dimension'] for r in rows} == {'service', 'region', 'record_type'}
    assert api.get_paginator.return_value.paginate.call_args.kwargs['Filter'] == {'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123']}}


def test_cost_explorer_without_sdk_paginator_consumes_all_pages():
    import botocore.session
    from botocore import UNSIGNED
    from botocore.config import Config
    from botocore.stub import Stubber
    from dashboard_adapters import AWSAdapter
    client = botocore.session.get_session().create_client('ce', region_name='us-east-1', config=Config(signature_version=UNSIGNED))
    assert not client.can_paginate('get_cost_and_usage')
    adapter = object.__new__(AWSAdapter)
    adapter.client = lambda *args, **kwargs: client
    with Stubber(client) as stub:
        stub.add_response('get_dimension_values', {'DimensionValues': [], 'ReturnSize': 0, 'TotalSize': 0}, dict(TimePeriod={'Start': '2026-11-01', 'End': '2026-11-02'}, Dimension='LINKED_ACCOUNT', Context='COST_AND_USAGE', Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123']}}))
        for dimension in ('SERVICE', 'REGION', 'RECORD_TYPE'):
            for page in (1, 2):
                result = dict(ResultsByTime=[dict(TimePeriod={'Start': '2026-11-01', 'End': '2026-11-02'}, Estimated=True,
                    Groups=[dict(Keys=['123', f'{dimension}-{page}'], Metrics={'UnblendedCost': {'Amount': str(page), 'Unit': 'USD'}})])])
                if page == 1:
                    result['NextPageToken'] = 'next'
                expected = dict(TimePeriod={'Start': '2026-11-01', 'End': '2026-11-02'}, Granularity='DAILY', Metrics=['UnblendedCost'],
                    Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': ['123']}},
                    GroupBy=[{'Type': 'DIMENSION', 'Key': 'LINKED_ACCOUNT'}, {'Type': 'DIMENSION', 'Key': dimension}])
                if page == 2:
                    expected['NextPageToken'] = 'next'
                stub.add_response('get_cost_and_usage', result, expected)
        rows = adapter.cost(['123'], '2026-11-01', '2026-11-02')
        assert len(rows) == 6
        assert sum(Decimal(r['amount']) for r in rows if r['dimension'] == 'service') == Decimal('3')
        stub.assert_no_pending_responses()
