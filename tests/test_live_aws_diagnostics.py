from datetime import datetime, timezone

from tools.validate_live_aws import capture, summarize_cost


def test_live_summary_reconciles_signed_costs_without_counting_alternate_views():
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    rows = [dict(date=date, target=account, currency='USD', dimension=dimension,
                 category=category, amount=amount)
            for account, category, amount in [('management', 'Usage', '10'), ('member', 'Usage', '5'), ('member', 'Credit', '-3')]
            for dimension in ('service', 'region', 'record_type')]
    result = summarize_cost(rows)
    assert result['totals'] == [dict(period='MTD', currency='USD', amount='12')]
    assert sum(float(r['amount']) for r in result['accounts']) == 12
    assert next(r['amount'] for r in result['record_types'] if r['record_type'] == 'Credit') == '-3'


def test_live_summary_preserves_currency_and_unavailable_period():
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    rows = [dict(date=date, target='member', currency=c, dimension='service', category='Service', amount='0') for c in ('USD', 'EUR')]
    result = summarize_cost(rows)
    assert {r['currency'] for r in result['totals']} == {'USD', 'EUR'}
    assert all(r['period'] == 'MTD' for r in result['totals'])
    assert summarize_cost([])['totals'] == []


def test_diagnostic_failure_exports_codes_without_raw_secret_bearing_messages():
    from botocore.exceptions import ClientError
    def fail():
        raise ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'private-external-id'}}, 'AssumeRole')
    result = capture(fail)
    assert result == dict(status='Failed', operation='AssumeRole', code='AccessDenied')
    assert 'private-external-id' not in str(result)
