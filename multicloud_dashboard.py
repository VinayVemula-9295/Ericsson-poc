"""Authorized dashboard orchestration; only sanitized API data is cached."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
import threading
import time
import os
import logging

from provider_connections import get_connection, get_saved_secret, list_connections
from dashboard_adapters import ADAPTERS

_cache = {}
_lock = threading.Lock()
TARGET_TYPES = {'AWS': 'account', 'Azure': 'subscription', 'GCP': 'project'}


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(connection):
    meta = connection.get('metadata') or {}
    for key in ('account_id', 'subscription_id', 'project_id'):
        if connection.get(key) or meta.get(key):
            return connection.get(key) or meta[key]
    return None


def failure(exc):
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
    status = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
    if status == 403 or code in ('AccessDenied', 'AccessDeniedException', 'UnauthorizedOperation'):
        return 'Permission denied'
    if status == 401 or code in ('ExpiredToken', 'InvalidClientTokenId', 'UnrecognizedClientException'):
        return 'Authentication failed'
    return 'Synchronization failed'


def query(connection_id, section, target=None, refresh=False):
    # Reauthorize on every read, including cached responses and secret access.
    connection = get_connection(connection_id)
    if connection is None:
        raise ValueError('Connection unavailable')
    from control_plane import enabled, principal, scope
    owner = scope(connection_id) if enabled() else {'workspace': 'single'}
    actor = principal()['username'] if enabled() else 'single'
    revision = hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest()
    key = (actor, json.dumps(owner, sort_keys=True), connection_id, revision, section, target)
    ttl = int(os.getenv('BILLING_CACHE_SECONDS', '3600') if section in {'cost', 'organization_cost'} else os.getenv('INVENTORY_CACHE_SECONDS', '300'))
    with _lock:
        hit = _cache.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < ttl:
            return hit[1]
    meta = connection.get('metadata') or {}
    boundary = meta.get('discovery_boundary')
    targets = [identity(connection)]
    if section == 'hierarchy' and not boundary and (connection['provider'] != 'AWS' or enabled()):
        native = identity(connection)
        return dict(status='Not configured', data=[dict(id=native, name=connection['name'], type=TARGET_TYPES[connection['provider']],
                    parent_id=None, metadata={})] if native else [], checked_at=now(), last_successful_sync=None,
                    errors=['An administrator must configure an approved discovery boundary.'])
    if section == 'resources' and target != identity(connection):
        raise ValueError('A matching target connection is required')
    # Billing remains constrained to the configured target; hierarchy authority is not billing authority.
    if section == 'cost' and (not targets[0] or target != targets[0]):
        raise ValueError('A matching billing target is required')
    if section == 'organization_cost':
        if meta.get('billing_scope') != 'organization' or not boundary or target != boundary:
            raise ValueError('Approved organization billing scope is required')
        hierarchy = query(connection_id, 'hierarchy', refresh=refresh)
        if hierarchy['status'] != 'Available':
            return dict(status='Unavailable', data=[], checked_at=now(), last_successful_sync=None,
                        errors=['Organization billing requires successful discovery of the approved boundary.'])
        targets = sorted({n['id'] for n in hierarchy['data'] if n['type'] == TARGET_TYPES[connection['provider']]})
        if not targets:
            return dict(status='Unavailable', data=[], checked_at=now(), last_successful_sync=None,
                        errors=['No authorized organization accounts were discovered.'])
    try:
        adapter_connection = connection
        if connection['provider'] == 'AWS' and section in {'hierarchy', 'organization_cost'}:
            if meta.get('organization_credential_source', 'role') == 'controller':
                if not boundary:
                    raise ValueError('Controller discovery requires an approved boundary')
                # Explicit management reads only; inventory/execution keep their own roles.
                adapter_connection = dict(connection, metadata=dict(meta, inventory_role_arn='',
                    credential_mode='instance_role', aws_profile=''))
            elif meta.get('organization_role_arn'):
                adapter_connection = dict(connection, metadata=dict(meta, inventory_role_arn=meta['organization_role_arn']))
        adapter = ADAPTERS[connection['provider']](adapter_connection, get_saved_secret(connection_id))
        if section == 'organization_cost' and connection['provider'] == 'AWS':
            organization = next(n for n in hierarchy['data'] if n['type'] == 'organization')
            manager = organization.get('metadata', {}).get('management_account_id')
            if not manager or adapter.client('sts').get_caller_identity()['Account'] != manager:
                raise ValueError('Consolidated billing requires the verified management-account role.')
        errors = []
        if section == 'hierarchy':
            data = adapter.hierarchy(boundary)
        elif section == 'resources':
            data, errors = adapter.resources(target)
        elif section in {'cost', 'organization_cost'}:
            today = datetime.now(timezone.utc).date()
            current = today.replace(day=1)
            previous = (current - timedelta(days=1)).replace(day=1)
            data = adapter.cost(targets, previous.isoformat(), (today + timedelta(days=1)).isoformat())
        else:
            raise ValueError('Unknown dashboard section')
        stamp = now()
        for error in errors:
            logging.getLogger(__name__).warning('Cloud read incomplete provider=%s connection=%s target=%s section=%s diagnostic=%s',
                connection['provider'], connection_id, target, section, error)
        result = dict(status='Partial' if errors else 'Available', data=data, errors=errors,
                      checked_at=stamp, last_successful_sync=stamp if not errors else (hit[1].get('last_successful_sync') if hit else None))
    except Exception as exc:
        code = getattr(exc, 'response', {}).get('Error', {}).get('Code', '')
        operation = getattr(exc, 'operation_name', section)
        logging.getLogger(__name__).warning('Cloud read failed provider=%s section=%s operation=%s code=%s', connection['provider'], section, operation, code or type(exc).__name__)
        reason = (f'{operation}: {code}. Verify the configured role, IAM permissions, SCPs and permission boundary.'
                  if code else 'Check the connection, approved scope, enabled APIs, and read permissions.')
        if str(exc) in {'Root credentials are unsupported; configure a dedicated IAM role.',
                        'Discovery boundary mismatch', 'Target identity mismatch',
                        'Consolidated billing requires the verified management-account role.'}:
            reason = str(exc)
        result = dict(status='Not configured' if section == 'cost' and connection['provider'] == 'GCP' and not meta.get('billing_export_table') else failure(exc),
                      data=[], checked_at=now(), last_successful_sync=hit[1].get('last_successful_sync') if hit else None,
                      errors=[reason])
    with _lock:
        if len(_cache) >= 512:
            _cache.clear()
        _cache[key] = (time.monotonic(), result)
    return result


def cost_totals(results):
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    totals = {}
    for result in results:
        for row in result.get('data', []):
            if row['dimension'] == 'service' and row['date'].startswith(current):
                totals[row['currency']] = totals.get(row['currency'], Decimal(0)) + Decimal(str(row['amount']))
    return totals


def billing_breakdown(results):
    """Alternative record-type view of deduplicated net billing, never extra spend."""
    current = datetime.now(timezone.utc).strftime('%Y-%m')
    gross, credits, adjustments = {}, {}, {}
    expected, observed = set(), set()
    for result in results:
        owner = result.get('owner', 'single')
        for row in result.get('data', []):
            if not row['date'].startswith(current):
                continue
            currency = row['currency']
            key = (owner, row['target'], currency)
            if row['dimension'] == 'service':
                expected.add(key)
            if row['dimension'] != 'record_type':
                continue
            observed.add(key)
            gross.setdefault(currency, Decimal(0))
            credits.setdefault(currency, Decimal(0))
            amount = Decimal(str(row['amount']))
            category = row['category']
            if category == 'Usage':
                gross[currency] += amount
            elif category == 'Credit':
                credits[currency] += amount
            elif category != 'No reported usage':
                bucket = adjustments.setdefault(category, {})
                bucket[currency] = bucket.get(currency, Decimal(0)) + amount
    return dict(gross_usage=gross, credits=credits, net=cost_totals(results),
                adjustments=adjustments, partial=bool(expected - observed) or
                any(r.get('status', 'Available') != 'Available' for r in results))


def connected_usage_summary(accounts):
    totals = {}
    selected = [a for a in accounts if a['online']]
    for account in selected:
        for currency, amount in account.get('billing', {}).get('gross_usage', {}).items():
            totals[currency] = totals.get(currency, Decimal(0)) + amount
    return totals, any(not a.get('billing', {}).get('gross_usage') or
                       a['billing']['partial'] for a in selected)


def provider_snapshot(provider, refresh=False, connections=None):
    connections = list_connections() if connections is None else connections
    connections = [c for c in connections if c['provider'] == provider]
    nodes, errors, costs = {}, [], []
    seen_targets = set()
    import control_plane as cp
    username = cp.principal()['username'] if cp.enabled() else None
    def load(c):
        # Carry the principal to workers; Streamlit session state is thread-local.
        with cp.identity(username):
            hierarchy = query(c['connection_id'], 'hierarchy', refresh=refresh)
            meta = c.get('metadata') or {}
            organization = meta.get('billing_scope') == 'organization' and meta.get('discovery_boundary')
            cost = query(c['connection_id'], 'organization_cost' if organization else 'cost', organization or identity(c), refresh=refresh) if identity(c) else {'status': 'Not configured', 'data': []}
        return c, hierarchy, cost
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(copy_context().run, load, c) for c in connections]
        for future in futures:
            c, hierarchy, cost = future.result()
            owner = (cp.scope(c['connection_id']) or {}).get('customer', 'unassigned') if cp.enabled() else 'single'
            if hierarchy['status'] != 'Available':
                errors.extend(c['name'] + ': ' + e for e in hierarchy.get('errors', [hierarchy['status']]))
            if cost['status'] != 'Available':
                errors.extend(c['name'] + ': ' + e for e in cost.get('errors', [cost['status']]))
            for item in hierarchy['data']:
                # Never merge a native identity across assigned customers.
                key = (owner, item['id'])
                if key not in nodes:
                    nodes[key] = dict(item, owner=owner, uid=provider + ':' + hashlib.sha256((owner + item['id']).encode()).hexdigest(), connection_ids=[])
                nodes[key]['connection_ids'].append(c['connection_id'])
            if identity(c) and not any(n['id'] == identity(c) and c['connection_id'] in n['connection_ids'] for n in nodes.values()):
                key = (owner, identity(c))
                if key not in nodes:
                    nodes[key] = dict(id=identity(c), name=c['name'], type=TARGET_TYPES[provider], owner=owner,
                        parent_id=None, metadata={}, uid=provider + ':' + c['connection_id'], connection_ids=[])
                nodes[key]['connection_ids'].append(c['connection_id'])
            costs.append(dict(cost, connection_id=c['connection_id'], connection_name=c['name'],
                              organization_scope=(c.get('metadata') or {}).get('billing_scope') == 'organization',
                              owner=owner,
                              configured_target=identity(c)))
    # Prefer complete organization reads over overlapping member connections.
    # Ownership remains part of the key, preventing cross-customer aggregation.
    costs.sort(key=lambda r: (r.get('status') == 'Available', r.get('organization_scope', False), len(r.get('data', []))), reverse=True)
    normalized = []
    for cost in costs:
        owner = cost['owner']
        targets = {r['target'] for r in cost['data']}
        fresh = {t for t in targets if (owner, t) not in seen_targets}
        if fresh:
            normalized.append(dict(cost, data=[r for r in cost['data'] if r['target'] in fresh]))
            seen_targets.update((owner, t) for t in fresh)
        elif cost['status'] != 'Available' and (owner, cost['configured_target']) not in seen_targets:
            normalized.append(cost)
        elif not targets and cost['status'] == 'Available':
            normalized.append(cost)
    costs = normalized
    discovered = {(n['owner'], n['id']) for n in nodes.values() if n['type'] == TARGET_TYPES[provider]}
    covered = {(cost['owner'], r['target']) for cost in costs for r in cost['data']}
    return dict(connections=connections, nodes=list(nodes.values()), errors=errors, costs=costs,
                billing=billing_breakdown(costs),
                totals=cost_totals(costs), partial_cost=any(r['status'] != 'Available' for r in costs) or bool(discovered - covered),
                discovered_targets=len(discovered), billed_targets=len(covered))


def account_summaries(provider, snapshot):
    """Present authorized targets using the already deduplicated billing results."""
    rows = []
    for node in snapshot['nodes']:
        if node['type'] != TARGET_TYPES[provider]:
            continue
        links = [c for c in snapshot['connections'] if identity(c) == node['id']
                 and c['connection_id'] in node['connection_ids']]
        results = [dict(result, data=[r for r in result.get('data', []) if r['target'] == node['id']])
                   for result in snapshot.get('costs', []) if result.get('owner', 'single') == node.get('owner', 'single')]
        results = [result for result in results if result['data']]
        totals = cost_totals(results)
        regions = sorted({(c.get('metadata') or {}).get('default_region') or
                          (c.get('metadata') or {}).get('region') for c in links} - {None, ''})
        verified = [c for c in links if c.get('connection_status') == 'CONNECTED']
        rows.append(dict(id=node['id'], name=node['name'],
                         owner=node.get('owner', 'single'), configured=bool(links), online=bool(verified),
                         region=', '.join(regions) or 'Unavailable', totals=totals,
                         billing=billing_breakdown(results),
                         partial=any(r['status'] != 'Available' for r in results),
                         last_verified=max((c.get('last_tested_at') or '' for c in links), default='') or 'Never',
                         connection_ids=[c['connection_id'] for c in links],
                         parent=next((n['name'] for n in snapshot['nodes'] if n['id'] == node.get('parent_id') and n.get('owner') == node.get('owner')), 'Unavailable'),
                         state=node.get('metadata', {}).get('state', 'Unavailable'),
                         failed=any(c.get('connection_status') in {'FAILED', 'ERROR'} for c in links),
                         management=bool(node.get('metadata', {}).get('management'))))
    return sorted(rows, key=lambda row: (not row['configured'], row['name'] or row['id'], row['id']))


def connected_cost_summary(accounts):
    configured = [row for row in accounts if row['online']]
    totals = {}
    for row in configured:
        for currency, amount in row['totals'].items():
            totals[currency] = totals.get(currency, Decimal(0)) + amount
    return totals, any(not row['totals'] or row['partial'] for row in configured)
