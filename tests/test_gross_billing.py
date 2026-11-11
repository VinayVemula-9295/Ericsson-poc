from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock
import pytest
from multicloud_dashboard import billing_breakdown, account_summaries, connected_usage_summary
from hierarchy_ui import card_billing
from cost_dashboard_ui import money


def row(category, amount, target='management', dimension='record_type', currency='USD'):
    return dict(date=datetime.now(timezone.utc).strftime('%Y-%m-01'), target=target,
                category=category, amount=amount, dimension=dimension, currency=currency)


def test_usage_credit_net_and_adjustments_are_independent():
    rows = [row('Usage', '14.8161993848'), row('Credit', '-14.8162002576'),
            row('Refund', '-1'), row('Tax', '2'), row('Fee', '3'),
            row('All services', '3.9999991272', dimension='service'),
            row('Region', '3.9999991272', dimension='region')]
    result = billing_breakdown([dict(data=rows)])
    assert result['gross_usage'] == {'USD': Decimal('14.8161993848')}
    assert money(result['gross_usage']) == 'USD 14.82'
    assert money(result['credits']) == 'USD -14.82'
    assert result['adjustments']['Refund'] == {'USD': Decimal('-1')}
    assert result['net'] == {'USD': Decimal('3.9999991272')}
    assert not result['partial']
    assert money({'USD': Decimal('-0.0000008728')}) == 'USD 0.00'


def test_missing_components_not_inferred_from_net_and_currency_kept_separate():
    result = billing_breakdown([dict(data=[row('S', '12', dimension='service')])])
    assert result['net'] == {'USD': Decimal('12')}
    assert result['gross_usage'] == result['credits'] == {}
    assert result['partial']
    result = billing_breakdown([dict(data=[row('Usage', '2'), row('Usage', '3', currency='EUR')])])
    assert result['gross_usage'] == {'USD': Decimal('2'), 'EUR': Decimal('3')}
    assert billing_breakdown([])['gross_usage'] == {}
    assert billing_breakdown([dict(data=[row('No reported usage', '0')])])['gross_usage'] == {'USD': Decimal(0)}


@pytest.mark.parametrize('provider,gross,credit,net', [('Azure','64.20','-20.00','44.20'),('GCP','48.60','-10.00','38.60')])
def test_samples_only_when_unconfigured_and_never_modify_live_results(provider,gross,credit,net):
    snapshot = dict(connections=[], nodes=[], costs=[], errors=[])
    result, demo = card_billing(provider,snapshot)
    assert demo and result['gross_usage']['USD'] == Decimal(gross)
    assert result['credits']['USD'] == Decimal(credit)
    assert result['net']['USD'] == Decimal(net)
    assert snapshot['costs'] == []
    for change in [dict(connections=[{}]),dict(errors=['API denied']), dict(costs=[dict(data=[row('Service','9',dimension='service')])])]:
        live, demo = card_billing(provider,dict(snapshot,**change))
        assert not demo and live['gross_usage'] == {}
    assert not card_billing('AWS',snapshot)[1]


def test_connected_gross_does_not_include_management_or_offline_accounts():
    accounts = [dict(online=online, billing=billing_breakdown([dict(data=[row('Usage',amount)])]))
                for online,amount in [(True,'5'),(False,'10')]]
    assert connected_usage_summary(accounts) == ({'USD':Decimal('5')},False)


def test_gcp_export_preserves_regular_usage_credits_and_tax(monkeypatch):
    import sys
    import google.cloud
    bigquery = MagicMock()
    monkeypatch.setitem(sys.modules, 'google.cloud.bigquery', bigquery)
    monkeypatch.setattr(google.cloud, 'bigquery', bigquery, raising=False)
    from dashboard_adapters import GCPAdapter
    api = MagicMock()
    monkeypatch.setattr(bigquery,'Client',lambda **_:api)
    date=datetime.now(timezone.utc).date().replace(day=1)
    api.query.return_value.result.return_value = [dict(date=date,target='p',service='Compute',region='r',currency='USD',
        cost_type='regular',gross_usage='50',credit_amount='-20',amount='30'),
        dict(date=date,target='p',service='Tax',region='r',currency='USD',cost_type='tax',gross_usage='0',credit_amount='0',amount='2')]
    adapter=object.__new__(GCPAdapter)
    adapter.meta={'billing_export_table':'project.dataset.table'}
    adapter.credential=None
    rows=adapter.cost(['p'],str(date),str(date))
    result=billing_breakdown([dict(data=rows)])
    assert result['gross_usage']=={'USD':Decimal('50')}
    assert result['credits']=={'USD':Decimal('-20')}
    assert result['net']=={'USD':Decimal('32')}
    assert result['adjustments']['tax']=={'USD':Decimal('2')}
    assert 'cost_type' in api.query.call_args.args[0]
