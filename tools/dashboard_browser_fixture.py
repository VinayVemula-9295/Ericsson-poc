"""Synthetic read-only dashboard data for local browser acceptance checks only."""
from datetime import datetime, timezone
import time


def snapshot(provider, refresh=False, **kwargs):
    from multicloud_dashboard import cost_totals
    if refresh:
        time.sleep(2)
    if provider != 'AWS':
        return dict(connections=[], nodes=[], costs=[], errors=[], totals={}, partial_cost=False)
    date = datetime.now(timezone.utc).strftime('%Y-%m-01')
    connections = [dict(provider='AWS', connection_id=key, name=name, account_id=key,
                        connection_status=state, last_tested_at='2026-11-10T12:00:00Z',
                        metadata={'region': 'ap-south-1'})
                   for key, name, state in [('one', 'POC One', 'CONNECTED'), ('two', 'POC Two', 'FAILED')]]
    nodes = [dict(id=key, name=name, type='organization', parent_id=None, metadata={'billing': 'Organization'},
                  uid=key, connection_ids=['one', 'two']) for key, name in [('o-one', 'First Organization'), ('o-two', 'Second Organization')]]
    nodes += [dict(id=key, name=name, type='account', parent_id='o-two' if key == 'management-two' else 'o-one',
                   metadata={'management': key.startswith('management')}, uid=key,
                   connection_ids=['one', 'two'])
              for key, name in [('one', 'POC One'), ('two', 'POC Two'), ('management-one', 'Management One'), ('management-two', 'Management Two')]]
    costs = [dict(status='Available', owner='single', data=[dict(target=key, date=date, dimension='service',
                  currency='USD', amount=amount) for key, amount in [('one', '10'), ('two', '20'), ('management-one', '30'), ('management-two', '40')]])]
    costs[0]['data'] += [dict(r, dimension='record_type', category='Usage') for r in costs[0]['data']]
    return dict(connections=connections, nodes=nodes, costs=costs, errors=[], totals=cost_totals(costs), partial_cost=False)
