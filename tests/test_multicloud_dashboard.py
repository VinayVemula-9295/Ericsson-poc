from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from streamlit.testing.v1 import AppTest
import dashboard_adapters as adapters
import multicloud_dashboard as service


@pytest.fixture
def connection(monkeypatch):
    monkeypatch.setenv('CONTROL_PLANE_ENABLED', 'false')
    service._cache.clear()
    c = dict(connection_id='c', provider='AWS', name='test', account_id='123',
             metadata={'discovery_boundary': 'o-boundary'}, connection_status='CONNECTED')
    monkeypatch.setattr(service, 'get_connection', lambda _: c)
    monkeypatch.setattr(service, 'get_saved_secret', lambda _: {})
    return c


def test_authorization_precedes_cache(connection, monkeypatch):
    adapter = MagicMock()
    adapter.hierarchy.return_value = []
    monkeypatch.setitem(service.ADAPTERS, 'AWS', lambda *_: adapter)
    service.query('c', 'hierarchy')
    service.query('c', 'hierarchy')
    assert adapter.hierarchy.call_count == 1
    def denied(_):
        raise ValueError('Customer access denied')
    monkeypatch.setattr(service, 'get_connection', denied)
    with pytest.raises(ValueError, match='denied'):
        service.query('c', 'hierarchy')


def test_target_access_and_refresh(connection, monkeypatch):
    adapter = MagicMock()
    adapter.resources.return_value = ([], [])
    monkeypatch.setitem(service.ADAPTERS, 'AWS', lambda *_: adapter)
    with pytest.raises(ValueError, match='matching'):
        service.query('c', 'resources', 'other-account')
    assert service.query('c', 'resources', '123')['status'] == 'Available'
    adapter.resources.side_effect = RuntimeError('SECRET-CONTENT')
    result = service.query('c', 'resources', '123', refresh=True)
    assert result['status'] == 'Synchronization failed'
    assert result['data'] == []
    assert result['last_successful_sync']
    assert 'SECRET-CONTENT' not in str(result)


def test_partial_and_configuration_invalidation(connection, monkeypatch):
    adapter = MagicMock()
    adapter.resources.return_value = ([{'name': 'live'}], ['RDS unavailable'])
    monkeypatch.setitem(service.ADAPTERS, 'AWS', lambda *_: adapter)
    assert service.query('c', 'resources', '123')['status'] == 'Partial'
    connection['updated_at'] = 'new revision'
    service.query('c', 'resources', '123')
    assert adapter.resources.call_count == 2


def test_missing_boundary_keeps_configured_target_in_customer_mode(connection, monkeypatch):
    import control_plane
    monkeypatch.setattr(control_plane, 'enabled', lambda: True)
    monkeypatch.setattr(control_plane, 'principal', lambda: {'username': 'test'})
    monkeypatch.setattr(control_plane, 'scope', lambda _: {})
    connection['metadata'] = {}
    result = service.query('c', 'hierarchy')
    assert result['status'] == 'Not configured'
    assert result['data'][0]['id'] == '123'


def test_cost_currency_and_no_double_count():
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    rows = [dict(date=date, dimension='service', currency='USD', amount='1.25'),
            dict(date=date, dimension='region', currency='USD', amount='1.25'),
            dict(date=date, dimension='service', currency='EUR', amount='2')]
    assert service.cost_totals([{'data': rows}]) == {'USD': Decimal('1.25'), 'EUR': Decimal('2')}
    assert service.cost_totals([{'data': []}]) == {}


def test_organization_totals_and_poc_subtotal_without_overlap(connection, monkeypatch):
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    connections = [dict(connection, connection_id='org', account_id='management',
                        metadata={'billing_scope': 'organization', 'discovery_boundary': 'o-one'}),
                   dict(connection, connection_id='poc-one', account_id='one', metadata={'region': 'ap-south-1'}),
                   dict(connection, connection_id='poc-two', account_id='two'),
                   dict(connection, connection_id='duplicate', account_id='one'),
                   dict(connection, connection_id='org-two', account_id='other',
                        metadata={'billing_scope': 'organization', 'discovery_boundary': 'o-two'})]
    def billing(target, amount):
        return dict(target=target, date=date, dimension='service', currency='USD', amount=amount)
    def query(cid, section, *args, **kwargs):
        c = next(c for c in connections if c['connection_id'] == cid)
        if section == 'hierarchy':
            ids = ['management', 'one', 'two'] if cid == 'org' else ['other'] if cid == 'org-two' else [c['account_id']]
            data = [dict(id=i, name=i, type='account', parent_id=None, metadata={'management': i == 'management'}) for i in ids]
            if cid in {'org', 'org-two'}:
                data.append(dict(id=c['metadata']['discovery_boundary'], name='Org', type='organization', parent_id=None, metadata={}))
        else:
            data = [billing('management', '30'), billing('one', '10'), billing('two', '20')] if cid == 'org' else [billing(c['account_id'], '40' if cid == 'org-two' else '10' if c['account_id'] == 'one' else '20')]
            data += [dict(data[0], dimension='region')]  # Alternate billing dimension is not extra spend.
            data += [dict(r, dimension='record_type', category='Usage') for r in data if r['dimension'] == 'service']
        return dict(status='Available', data=data)
    monkeypatch.setattr(service, 'query', query)
    snapshot = service.provider_snapshot('AWS', connections=connections)
    assert snapshot['totals'] == {'USD': Decimal('100')}
    assert snapshot['billing']['gross_usage'] == {'USD': Decimal('100')}
    assert sum(n['type'] == 'organization' for n in snapshot['nodes']) == 2
    assert snapshot['partial_cost'] is False
    # POC subtotal excludes management/other when they are only discovered, not connected.
    snapshot['connections'] = [c for c in connections if c['connection_id'].startswith('poc') or c['connection_id'] == 'duplicate']
    accounts = service.account_summaries('AWS', snapshot)
    subtotal, partial = service.connected_cost_summary(accounts)
    assert subtotal == {'USD': Decimal('30')} and not partial
    assert service.connected_usage_summary(accounts) == ({'USD': Decimal('30')}, False)
    assert len(accounts) == 4
    assert next(r for r in accounts if r['id'] == 'one')['region'] == 'ap-south-1'


def test_account_summary_missing_cost_offline_and_customer_boundary():
    connection = dict(connection_id='c', provider='AWS', name='<POC>', account_id='one', connection_status='FAILED')
    snapshot = dict(connections=[connection], nodes=[dict(id='one', name='One', owner='customer-one',
                    type='account', connection_ids=['c'], metadata={})], costs=[dict(owner='customer-two',
                    status='Available', data=[dict(target='one', date=datetime.now(timezone.utc).strftime('%Y-%m-01'),
                    dimension='service', currency='USD', amount='99')])])
    accounts = service.account_summaries('AWS', snapshot)
    assert accounts[0]['online'] is False
    assert accounts[0]['totals'] == {}
    assert service.connected_cost_summary(accounts) == ({}, False)


def test_account_details_table_shows_two_accounts_and_escapes_names(monkeypatch):
    import hierarchy_ui
    c = dict(connection_id='one', provider='AWS', name='<One>', account_id='1', connection_status='CONNECTED')
    connections = [c, dict(c, connection_id='two', name='Two', account_id='2', connection_status='FAILED')]
    snapshot = dict(connections=connections, nodes=[dict(id=c['account_id'], name=c['name'], type='account',
                    metadata={}, connection_ids=[c['connection_id']]) for c in connections], costs=[], errors=[])
    monkeypatch.setattr(hierarchy_ui, 'provider_snapshot', lambda provider, **_: dict(snapshot, totals={}, partial_cost=True) if provider == 'AWS' else
                        dict(connections=[], nodes=[], errors=[], totals={}, partial_cost=False))
    page = AppTest.from_string('from hierarchy_ui import render\nrender([])', default_timeout=15).run()
    page.button(key='AWS-accounts').click().run()
    assert not page.exception
    table = page.dataframe[0].value
    assert len(table) == 2
    assert table.iloc[0]['Account Name'] == '<One>'
    assert 'Verified' in table.iloc[0]['Connection Status']
    assert table.iloc[0]['MTD Cost'] == ''
    assert list(table.columns) == ['Account Name', 'Account ID', 'Account Type', 'Configured Region',
                                   'MTD Cost', 'Connection Status', 'Last Verified']
    assert not any(b.label in {'Explore hierarchy', 'Connect / manage'} for b in page.button)



def test_aws_nested_hierarchy_and_management():
    api = MagicMock()
    api.describe_organization.return_value = {'Organization': {'Id': 'o-1', 'ManagementAccountId': '123'}}
    api.list_roots.return_value = {'Roots': [{'Id': 'r-1', 'Name': 'Root'}]}
    api.list_organizational_units_for_parent.side_effect = lambda **kw: {'OrganizationalUnits': [{'Id': 'ou-1', 'Name': 'Nested'}]} if kw['ParentId'] == 'r-1' else {}
    api.list_accounts.return_value = {'Accounts': [{'Id': '123', 'Name': 'Management', 'State': 'ACTIVE'}]}
    api.describe_account.return_value = {'Account': {'Id': '123', 'Name': 'Management', 'State': 'ACTIVE'}}
    api.list_parents.return_value = {'Parents': [{'Id': 'ou-1'}]}
    api.describe_organizational_unit.return_value = {'OrganizationalUnit': {'Name': 'Nested'}}
    api.get_paginator.side_effect = lambda method: type('Paginator', (), {'paginate': lambda self, **kw: [getattr(api, method)(**kw)]})()
    adapter = object.__new__(adapters.AWSAdapter)
    adapter.meta = {}
    adapter.session = MagicMock()
    adapter.session.client.return_value = api
    nodes = adapter.hierarchy('o-1')
    assert nodes[-1]['parent_id'] == 'ou-1'
    assert nodes[-1]['metadata'] == {'state': 'ACTIVE', 'management': True, 'email': None}
    with pytest.raises(ValueError):
        adapter.hierarchy('o-other')


def test_aws_pagination_handles_empty_page():
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Roots': [], 'NextToken': 'next'}, {'Roots': [{'Id': 'r'}]}]
    assert list(adapters.pages(client, 'list_roots', 'Roots')) == [{'Id': 'r'}]


def test_gcp_recursive_projects_and_folders():
    adapter = object.__new__(adapters.GCPAdapter)
    adapter.request = lambda _: {'displayName': 'Organization'}
    def listing(url, field):
        if field == 'folders':
            return [{'name': 'folders/2', 'displayName': 'Folder'}] if 'parent=organizations/1' in url else []
        return [{'projectId': 'p-direct' if 'organizations/1' in url else 'p-folder', 'name': 'projects/3', 'state': 'ACTIVE'}]
    adapter.listing = listing
    rows = adapter.hierarchy('organizations/1')
    assert {n['parent_id'] for n in rows if n['type'] == 'project'} == {'organizations/1', 'folders/2'}


@pytest.mark.parametrize('asset', ['compute.googleapis.com/Instance', 'storage.googleapis.com/Bucket',
    'compute.googleapis.com/Network', 'sqladmin.googleapis.com/Instance', 'container.googleapis.com/Cluster', 'cloudfunctions.googleapis.com/CloudFunction'])
def test_gcp_inventory_asset_types(asset):
    adapter = object.__new__(adapters.GCPAdapter)
    adapter.listing = lambda *_: [{'name': 'resources/live', 'assetType': asset, 'location': 'region'}]
    rows, errors = adapter.resources('project')
    assert rows[0]['service'] == asset
    assert not errors


@pytest.mark.parametrize('kind', ['Microsoft.Compute/virtualMachines', 'Microsoft.Storage/storageAccounts',
    'Microsoft.Network/virtualNetworks', 'Microsoft.Sql/servers/databases', 'Microsoft.ContainerService/managedClusters', 'Microsoft.Web/sites'])
def test_azure_inventory_resource_types(kind):
    adapter = object.__new__(adapters.AzureAdapter)
    adapter.request = lambda _: {'statuses': [{'code': 'PowerState/running', 'displayStatus': 'VM running'}]}
    adapter.listing = lambda url: [] if 'resourcegroups?' in url else [{'id': '/subscriptions/s/resourceGroups/g/providers/' + kind + '/r', 'name': 'live', 'type': kind, 'location': 'region'}]
    rows, errors = adapter.resources('s')
    assert rows[0]['service'] == kind
    assert rows[0]['parent_id'] == '/subscriptions/s/resourceGroups/g'
    assert not errors


def test_ui_empty_three_providers_and_configuration(monkeypatch):
    import hierarchy_ui
    monkeypatch.setattr(hierarchy_ui, 'provider_snapshot', lambda provider, **_: dict(connections=[], nodes=[], errors=[], totals={}, partial_cost=False))
    page = AppTest.from_string('from hierarchy_ui import render\nrender([])', default_timeout=15).run()
    assert not page.exception
    assert [b.label for b in page.button if b.key.endswith('-accounts')] == ['View accounts ↗', 'View subscriptions ↗', 'View projects ↗']
    page.button(key='Azure-accounts').click().run()
    assert not page.exception
    assert any('No visible subscriptions' in item.value for item in page.info)


def test_ui_select_and_detail(connection, monkeypatch):
    import hierarchy_ui
    snapshot = dict(connections=[connection], nodes=[dict(id='123', name='Live account', type='account', parent_id=None,
                    uid='unique', metadata={}, connection_ids=['c'])], errors=[], totals={}, partial_cost=False)
    monkeypatch.setattr(hierarchy_ui, 'provider_snapshot', lambda provider, **_: snapshot if provider == 'AWS' else dict(connections=[], nodes=[], errors=[], totals={}, partial_cost=False))
    monkeypatch.setattr(hierarchy_ui, 'query', lambda *_: dict(status='Available', data=[], errors=[], last_successful_sync='timestamp'))
    page = AppTest.from_string('from hierarchy_ui import render\nrender([])', default_timeout=15).run()
    page.button(key='AWS-accounts').click().run()
    assert not page.exception
    assert page.dataframe[0].value.iloc[0]['Account ID'] == '123'


def test_account_dialog_waits_until_global_dialog_closes(connection, monkeypatch):
    import hierarchy_ui
    from types import SimpleNamespace
    snapshot = dict(connections=[connection], nodes=[dict(id='123', name='Live account', type='account',
                    parent_id=None, uid='unique', metadata={}, connection_ids=['c'])],
                    costs=[], errors=[], totals={}, partial_cost=False)
    monkeypatch.setattr(hierarchy_ui, 'provider_snapshot', lambda provider, **_: snapshot if provider == 'AWS' else
                        dict(connections=[], nodes=[], costs=[], errors=[], totals={}, partial_cost=False))
    original_dataframe = hierarchy_ui.st.dataframe
    def selected_dataframe(data, **kwargs):
        if kwargs.get('key') == 'AWS-account-table':
            return SimpleNamespace(selection=SimpleNamespace(rows=[0]))
        return original_dataframe(data, **kwargs)
    monkeypatch.setattr(hierarchy_ui.st, 'dataframe', selected_dataframe)
    opened = []
    monkeypatch.setattr(hierarchy_ui, 'account_dialog', lambda *args: opened.append(args))
    page = AppTest.from_string('from hierarchy_ui import render\nrender([])', default_timeout=15)
    page.session_state['overview-details-provider'] = 'AWS'
    page.session_state['iipp-dialog-opened-this-run'] = True
    page.run()
    assert not page.exception
    assert opened == []
    assert page.session_state['iipp-pending-account-dialog']['account_id'] == '123'
    page.session_state['iipp-dialog-opened-this-run'] = False
    page.run()
    assert not page.exception
    assert len(opened) == 1
    assert opened[0][1]['id'] == '123'
    page.session_state['iipp-dialog-opened-this-run'] = True
    page.run()
    assert len(opened) == 1
    page.session_state['iipp-dialog-opened-this-run'] = False
    page.run()
    assert len(opened) == 2


def test_worker_carries_authenticated_principal(connection, monkeypatch):
    import control_plane as cp
    monkeypatch.setattr(cp, 'enabled', lambda: True)
    monkeypatch.setattr(cp, 'principal', lambda: {'username': 'customer-user'})
    monkeypatch.setattr(cp, 'scope', lambda _: {'customer': 'one'})
    def check(*args, **kwargs):
        assert cp._identity.get() == 'customer-user'
        return {'status': 'Available', 'data': []}
    monkeypatch.setattr(service, 'query', check)
    result = service.provider_snapshot('AWS', connections=[connection])
    assert len(result['connections']) == 1


def test_discovered_target_merges_only_inside_customer(connection, monkeypatch):
    import control_plane as cp
    monkeypatch.setattr(cp, 'enabled', lambda: True)
    monkeypatch.setattr(cp, 'principal', lambda: {'username': 'administrator'})
    monkeypatch.setattr(cp, 'scope', lambda cid: {'connection_id': cid, 'customer': 'other' if cid == 'third' else 'one'})
    monkeypatch.setattr(service, 'query', lambda *args, **kwargs: {'status': 'Available', 'data': [
        dict(id='123', name='Target', type='account', parent_id=None, metadata={})] if args[1] == 'hierarchy' else []})
    result = service.provider_snapshot('AWS', connections=[connection, dict(connection, connection_id='second'), dict(connection, connection_id='third')])
    assert len(result['nodes']) == 2
    assert sorted(len(n['connection_ids']) for n in result['nodes']) == [1, 2]


def test_aws_all_inventory_families():
    adapter = object.__new__(adapters.AWSAdapter)
    adapter.meta = {'region': 'region'}
    client = MagicMock()
    client.get_caller_identity.return_value = {'Account': '123'}
    client.describe_regions.return_value = {'Regions': [{'RegionName': 'region'}]}
    client.can_paginate.return_value = True
    responses = {
        'describe_instances': {'Reservations': [{'Instances': [{'InstanceId': 'i', 'State': {'Name': 'running'}}]}]},
        'describe_vpcs': {'Vpcs': [{'VpcId': 'vpc'}]},
        'describe_volumes': {'Volumes': [{'VolumeId': 'vol-1', 'State': 'in-use'}]},
        'describe_auto_scaling_groups': {'AutoScalingGroups': [{'AutoScalingGroupName': 'asg'}]},
        'list_metrics': {'Metrics': [{'MetricName': 'CPUUtilization'}]},
        'list_nodegroups': {'nodegroups': []},
        'describe_db_instances': {'DBInstances': [{'DBInstanceIdentifier': 'db', 'DBInstanceStatus': 'available'}]},
        'list_clusters': {'clusters': ['cluster'], 'clusterArns': ['arn:cluster']},
        'list_functions': {'Functions': [{'FunctionName': 'function'}]},
        'list_buckets': {'Buckets': [{'Name': 'bucket'}]},
        'describe_load_balancers': {'LoadBalancers': [{'LoadBalancerArn': 'arn:lb', 'LoadBalancerName': 'lb', 'State': {'Code': 'active'}}],
                                   'LoadBalancerDescriptions': [{'LoadBalancerName': 'classic'}]},
        'list_services': {'serviceArns': ['arn:service']},
    }
    client.get_paginator.side_effect = lambda method: type('Paginator', (), {'paginate': lambda self, **kw: [responses[method]]})()
    client.describe_cluster.return_value = {'cluster': {'status': 'ACTIVE'}}
    client.describe_clusters.return_value = {'clusters': [{'clusterName': 'cluster', 'status': 'ACTIVE', 'runningTasksCount': 1}]}
    client.describe_services.return_value = {'services': [{'serviceName': 'service', 'status': 'ACTIVE', 'runningCount': 1, 'desiredCount': 1}]}
    client.get_function_configuration.return_value = {'State': 'Active'}
    client.get_bucket_location.return_value = {'LocationConstraint': None}
    adapter.client = lambda *args, **kw: client
    rows, errors = adapter.resources('123')
    assert {r['service'] for r in rows} == {'EC2', 'VPC', 'RDS', 'EKS', 'Lambda', 'S3', 'ECS', 'EBS', 'Auto Scaling', 'CloudWatch', 'Load Balancers', 'Classic Load Balancers'}
    assert all(r['account'] == '123' and r['resource_type'] for r in rows)
    assert not errors
    with pytest.raises(ValueError, match='identity'):
        adapter.resources('other')


def test_rest_pagination_and_endpoint_boundary():
    adapter = adapters.RESTAdapter()
    adapter.request = MagicMock(side_effect=[{'results': [], 'nextPageToken': 'two'}, {'results': [{'name': 'live'}]}])
    assert list(adapter.listing('https://cloud.example/resources?parent=p', 'results')) == [{'name': 'live'}]
    assert 'pageToken=two' in adapter.request.call_args.args[0]
    azure = object.__new__(adapters.AzureAdapter)
    with pytest.raises(ValueError, match='endpoint'):
        azure.request('https://attacker.example/')


def test_azure_tenant_and_nested_groups():
    adapter = object.__new__(adapters.AzureAdapter)
    adapter.meta = {'tenant_id': 'tenant'}
    group_id = '/providers/Microsoft.Management/managementGroups/nested'
    def listing(url):
        if '/tenants?' in url:
            return [{'tenantId': 'tenant', 'displayName': 'Live tenant'}]
        if '/descendants?' in url:
            return [{'id': '/subscriptions/s', 'properties': {'parent': {'id': group_id}}}]
        if '/subscriptions?' in url:
            return [{'subscriptionId': 's', 'id': '/subscriptions/s', 'displayName': 'Live sub', 'state': 'Enabled'}]
        return [{'id': group_id, 'name': 'nested'}]
    adapter.listing = listing
    adapter.request = lambda _: {'properties': {'displayName': 'Nested', 'details': {'parent': {'id': 'tenant'}}}}
    rows = adapter.hierarchy('tenant')
    assert rows[-1]['parent_id'] == group_id
    assert rows[-1]['metadata']['state'] == 'Enabled'
