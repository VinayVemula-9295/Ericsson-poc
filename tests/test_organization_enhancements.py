from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import dashboard_adapters as adapters
import multicloud_dashboard as dashboard
import pytest


def test_signed_adjustments_are_not_added_twice():
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    amounts = [('Usage', '100'), ('Credit', '-25'), ('Refund', '-5'), ('Tax', '10')]
    rows = [dict(target='a', date=date, currency='USD', amount=amount,
                 dimension=dimension, category=kind) for dimension in ('service', 'record_type') for kind, amount in amounts]
    assert dashboard.cost_totals([dict(data=rows)]) == {'USD': Decimal('80')}


def test_single_workspace_can_verify_own_organization_without_boundary(monkeypatch):
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    connection = dict(provider='AWS', name='Management', account_id='management', metadata={})
    monkeypatch.setattr(dashboard, 'get_connection', lambda _: connection)
    monkeypatch.setattr(dashboard, 'get_saved_secret', lambda _: {})
    adapter = MagicMock()
    adapter.hierarchy.return_value = [dict(type='organization', id='o-live')]
    monkeypatch.setitem(dashboard.ADAPTERS, 'AWS', lambda *_: adapter)
    dashboard._cache.clear()
    result = dashboard.query('management', 'hierarchy')
    assert result['status'] == 'Available'
    adapter.hierarchy.assert_called_once_with(None)


def test_organization_reads_use_separate_role_and_failures_are_safe(monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    connection = dict(provider='AWS', name='Member', account_id='member', metadata=dict(
        inventory_role_arn='inventory-role', organization_role_arn='management-role', discovery_boundary='o-live'))
    monkeypatch.setattr(dashboard, 'get_connection', lambda _: connection)
    monkeypatch.setattr(dashboard, 'get_saved_secret', lambda _: {'external_id': 'private-id'})
    seen = []
    adapter = MagicMock()
    adapter.hierarchy.side_effect = ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'private-id'}}, 'ListRoots')
    def create(c, _):
        seen.append(c['metadata']['inventory_role_arn'])
        return adapter
    monkeypatch.setitem(dashboard.ADAPTERS, 'AWS', create)
    dashboard._cache.clear()
    result = dashboard.query('member', 'hierarchy')
    assert seen == ['management-role']
    assert connection['metadata']['inventory_role_arn'] == 'inventory-role'
    assert 'ListRoots: AccessDeniedException' in result['errors'][0]
    assert 'private-id' not in str(result)


def test_root_dashboard_identity_rejected(monkeypatch):
    import provider_connections
    session = MagicMock()
    session.client.return_value.get_caller_identity.return_value = {'Arn': 'arn:aws:iam::123456789012:root'}
    monkeypatch.setattr(provider_connections, '_assumed_aws_session', lambda *_: session)
    import pytest
    with pytest.raises(ValueError, match='Root credentials'):
        adapters.AWSAdapter({'metadata': {}}, {})


@pytest.mark.parametrize('account_id', ['member-one', 'member-two'])
def test_account_dialog_reads_only_selected_account(account_id, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import hierarchy_ui
    calls = []
    def read(connection, section, target, **kwargs):
        calls.append((connection, section, target))
        return dict(status='Available', last_successful_sync='verified', errors=[], data=[dict(
            id='i-selected', name='Selected instance', service='EC2', region='ap-south-1', status='stopped')])
    monkeypatch.setattr(hierarchy_ui, 'query', read)
    account = dict(name=account_id, id=account_id, region='ap-south-1', online=True, totals={},
                   owner='single', connection_ids=['connection-' + account_id])
    snapshot = dict(costs=[], connections=[])
    page = AppTest.from_string('from hierarchy_ui import account_dialog\naccount_dialog("AWS", ' + repr(account) + ', ' + repr(snapshot) + ')', default_timeout=15).run()
    assert not page.exception
    assert calls == [('connection-' + account_id, 'resources', account_id)]
    assert page.dataframe[0].value.iloc[0]['status'] == 'stopped'
