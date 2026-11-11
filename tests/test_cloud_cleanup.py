from unittest.mock import MagicMock
import pytest
import multicloud_dashboard as dashboard


@pytest.mark.parametrize('section,target,expected', [('hierarchy',None,''), ('resources','member','member-role')])
def test_controller_source_only_changes_management_reads(monkeypatch, section, target, expected):
    monkeypatch.setenv('CONTROL_PLANE_ENABLED','false')
    connection=dict(provider='AWS',name='Member',account_id='member',metadata=dict(
        inventory_role_arn='member-role',aws_profile='private-profile',credential_mode='access_keys',
        organization_credential_source='controller',discovery_boundary='o-approved'))
    monkeypatch.setattr(dashboard,'get_connection',lambda _:connection)
    monkeypatch.setattr(dashboard,'get_saved_secret',lambda _: {})
    seen=[]
    adapter=MagicMock()
    adapter.hierarchy.return_value=[]
    adapter.resources.return_value=([],['EC2 / region: DescribeInstances: AccessDenied'])
    def create(c,secret):
        seen.append(c['metadata'])
        return adapter
    monkeypatch.setitem(dashboard.ADAPTERS,'AWS',create)
    dashboard._cache.clear()
    dashboard.query('one',section,target)
    assert seen[0]['inventory_role_arn']==expected
    assert connection['metadata']['inventory_role_arn']=='member-role'
    if section=='hierarchy':
        assert seen[0]['aws_profile']==''
        assert seen[0]['credential_mode']=='instance_role'


def test_clean_cards_and_poc_do_not_call_other_clouds(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import hierarchy_ui
    calls=[]
    def snapshot(provider,**kwargs):
        calls.append(provider)
        return dict(connections=[],nodes=[],costs=[],errors=['AccessDenied'],partial_cost=True)
    monkeypatch.setattr(hierarchy_ui,'provider_snapshot',snapshot)
    page=AppTest.from_string('from hierarchy_ui import render\nrender([])').run()
    assert not page.exception
    assert calls==['AWS']
    assert [m.label for m in page.metric]==['Infrastructure Spend – MTD']*3
    assert [m.value for m in page.metric][1:]==['USD 64.20','USD 48.60']
    assert not page.warning
    assert not page.expander
    assert [c.value for c in page.caption][1:]==['POC demo','POC demo']


def test_partial_inventory_logs_without_customer_exceptions(monkeypatch,caplog):
    from streamlit.testing.v1 import AppTest
    import hierarchy_ui
    monkeypatch.setattr(hierarchy_ui,'query',lambda *a,**k:dict(status='Partial',
        errors=['Auto Scaling: AccessDenied'],data=[],last_successful_sync=None))
    account=dict(name='Member',id='member',region='ap-south-1',online=True,owner='single',connection_ids=['one'])
    page=AppTest.from_string('from hierarchy_ui import account_dialog\naccount_dialog("AWS",'+repr(account)+',dict(costs=[],connections=[]))').run()
    assert not page.exception and not page.warning
    assert all('Last successful discovery' not in c.value and 'Inventory: Partial' not in c.value for c in page.caption)
    assert page.selectbox[0].label=='Inventory connection'
