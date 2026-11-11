"""Read-only profile diagnostics. No credentials or resource payloads are exported."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import boto3
from botocore.config import Config
from dashboard_adapters import AWSAdapter, cost_pages

CONFIG = Config(connect_timeout=5, read_timeout=20, retries={'mode': 'standard', 'total_max_attempts': 3})


def error(exc):
    return dict(status='Failed', operation=getattr(exc, 'operation_name', None),
                code=getattr(exc, 'response', {}).get('Error', {}).get('Code', type(exc).__name__))


def capture(function):
    try:
        return dict(status='Verified', data=function())
    except Exception as exc:
        return error(exc)


def summarize_cost(rows):
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    totals = defaultdict(Decimal)
    services = defaultdict(Decimal)
    records = defaultdict(Decimal)
    accounts = defaultdict(Decimal)
    for row in rows:
        period = 'MTD' if row['date'].startswith(current) else 'Previous month'
        amount = Decimal(row['amount'])
        if row['dimension'] == 'service':
            totals[(period, row['currency'])] += amount
            accounts[(period, row['target'], row['currency'])] += amount
            if period == 'MTD':
                services[(row['category'], row['currency'])] += amount
        elif row['dimension'] == 'record_type' and period == 'MTD':
            records[(row['category'], row['currency'])] += amount
    def values(mapping, keys):
        return [dict(zip(keys, key), amount=str(value)) for key, value in sorted(mapping.items())]
    return dict(totals=values(totals, ['period', 'currency']),
                accounts=values(accounts, ['period', 'account', 'currency']),
                services=values(services, ['service', 'currency']),
                record_types=values(records, ['record_type', 'currency']), rows=len(rows))


def diagnostic_adapter(session, region):
    # The production constructor rejects root. This standalone operator diagnostic
    # explicitly uses the supplied profile, including root only for management diagnostics.
    adapter = object.__new__(AWSAdapter)
    adapter.session = session
    adapter.meta = dict(region=region, inventory_regions=[region])
    return adapter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', nargs='+', required=True)
    parser.add_argument('--management-profile', required=True)
    parser.add_argument('--member-profiles', nargs='+', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not {args.management_profile, *args.member_profiles}.issubset(set(args.profiles)):
        parser.error('Management and member profiles must be included in --profiles.')
    today = datetime.now(timezone.utc).date()
    current = today.replace(day=1)
    previous = (current - timedelta(days=1)).replace(day=1)
    end = (today + timedelta(days=1)).isoformat()
    sessions = {p: boto3.Session(profile_name=p, region_name=args.region) for p in args.profiles}
    result = dict(started_at=datetime.now(timezone.utc).isoformat(), region=args.region, profiles={}, members={})
    def sts(profile):
        caller = sessions[profile].client('sts', config=CONFIG).get_caller_identity()
        return dict(account=caller['Account'], arn=caller['Arn'], root=caller['Arn'].endswith(':root'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = {p: pool.submit(capture, lambda p=p: sts(p)) for p in sessions}
        result['profiles'] = {p: f.result() for p, f in checks.items()}
    management = diagnostic_adapter(sessions[args.management_profile], args.region)
    def hierarchy():
        nodes = management.hierarchy(None)
        return [dict(id=n['id'], name=n['name'], type=n['type'], parent_id=n['parent_id'],
                     metadata={k: v for k, v in n['metadata'].items() if k != 'email'}) for n in nodes]
    result['organization'] = capture(hierarchy)
    if result['organization']['status'] == 'Verified':
        targets = sorted({n['id'] for n in result['organization']['data'] if n['type'] == 'account'})
        grouped = capture(lambda: management.cost(targets, previous.isoformat(), end))
        if grouped['status'] == 'Verified':
            rows = grouped.pop('data')
            grouped['data'] = summarize_cost(rows)
        result['organization_billing'] = grouped
        def consolidated():
            rows = []
            for page in cost_pages(management.client('ce', region_name='us-east-1'),
                    TimePeriod={'Start': previous.isoformat(), 'End': end}, Granularity='DAILY', Metrics=['UnblendedCost']):
                for day in page['ResultsByTime']:
                    metric = day['Total']['UnblendedCost']
                    rows.append(dict(date=day['TimePeriod']['Start'], dimension='service', target='consolidated',
                                     category='All services', amount=metric['Amount'], currency=metric['Unit']))
            return summarize_cost(rows)
        result['consolidated_control'] = capture(consolidated)
        if grouped['status'] == 'Verified' and result['consolidated_control']['status'] == 'Verified':
            def total_map(data):
                return {(r['period'], r['currency']): Decimal(r['amount']) for r in data['totals']}
            a, b = total_map(grouped['data']), total_map(result['consolidated_control']['data'])
            result['reconciliation'] = [dict(period=k[0], currency=k[1], account_sum=str(a.get(k, 0)),
                consolidated=str(b.get(k, 0)), difference=str(a.get(k, 0) - b.get(k, 0))) for k in sorted(a.keys() | b.keys())]
    def member(profile):
        account = result['profiles'][profile].get('data', {}).get('account')
        if not account:
            return dict(status='Unavailable')
        adapter = AWSAdapter(dict(metadata=dict(aws_profile=profile, region=args.region, inventory_regions=[args.region])), {})
        def inventory():
            rows, errors = adapter.resources(account)
            return dict(coverage='Partial' if errors else 'Complete', count=len(rows), errors=errors,
                        resource_rows=sum(r['service'] != 'CloudWatch' for r in rows),
                        metric_descriptors=sum(r['service'] == 'CloudWatch' for r in rows),
                        services=dict(Counter(r['service'] for r in rows)),
                        states=dict(Counter(r['service'] + ': ' + str(r['status']) for r in rows)),
                        regions=sorted({r['region'] for r in rows}),
                        account_identity_confirmed=all(r['account'] == account for r in rows))
        return dict(account=account, inventory=capture(inventory),
                    billing=capture(lambda: summarize_cost(adapter.cost([account], previous.isoformat(), end))))
    with ThreadPoolExecutor(max_workers=2) as pool:
        checks = {p: pool.submit(capture, lambda p=p: member(p)) for p in args.member_profiles}
        result['members'] = {p: f.result() for p, f in checks.items()}
    result['completed_at'] = datetime.now(timezone.utc).isoformat()
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
