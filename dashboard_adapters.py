"""Read-only cloud discovery. No catalogue estimates or fixture fallback."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import urllib.request
import urllib.error
import time


def azure_credential(meta, secret):
    from azure.identity import ClientSecretCredential, ManagedIdentityCredential, WorkloadIdentityCredential
    mode = meta.get('credential_mode', 'client_secret')
    if mode == 'managed_identity':
        return ManagedIdentityCredential(client_id=meta.get('client_id') or None)
    if mode == 'workload_identity':
        return WorkloadIdentityCredential(tenant_id=meta['tenant_id'], client_id=meta['client_id'], token_file_path=meta['token_file'])
    return ClientSecretCredential(meta['tenant_id'], meta['client_id'], secret['client_secret'])


def gcp_credential(meta, secret):
    import google.auth
    from google.oauth2 import service_account
    if meta.get('credential_mode') == 'workload_identity':
        from google.auth import load_credentials_from_dict
        config = secret['federation_config']
        if config.get('type') != 'external_account':
            raise ValueError('Expected external_account configuration')
        return load_credentials_from_dict(config, scopes=['https://www.googleapis.com/auth/cloud-platform'])[0]
    info = secret['service_account_json']
    if isinstance(info, str):
        info = json.loads(info)
    return service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/cloud-platform'])


def node(identity, name, kind, parent=None, **metadata):
    return dict(id=identity, name=name or identity, type=kind, parent_id=parent, metadata=metadata)


def api_failure(exc):
    """Expose diagnostic codes without propagating secret-bearing API messages."""
    code = getattr(exc, 'response', {}).get('Error', {}).get('Code')
    return (str(getattr(exc, 'operation_name', 'API read')) + ': ' + code) if code else 'Read unavailable; verify permissions and network access'


def pages(client, method, field, **kwargs):
    for result in client.get_paginator(method).paginate(**kwargs):
        yield from result.get(field, [])


def cost_pages(client, **kwargs):
    # GetCostAndUsage exposes NextPageToken but has no built-in paginator in
    # botocore. Use its standard Paginator with the API's documented tokens.
    from botocore.paginate import Paginator
    if client.can_paginate('get_cost_and_usage'):
        paginator = client.get_paginator('get_cost_and_usage')
    else:
        paginator = Paginator(client.get_cost_and_usage,
            {'input_token': 'NextPageToken', 'output_token': 'NextPageToken', 'result_key': 'ResultsByTime'},
            client.meta.service_model.operation_model('GetCostAndUsage'))
    return paginator.paginate(**kwargs)


class AWSAdapter:
    def __init__(self, connection, secret):
        from provider_connections import _assumed_aws_session
        self.session = _assumed_aws_session(connection.get('metadata', {}), secret, 'inventory')
        self.meta = connection.get('metadata', {})
        caller = self.session.client('sts').get_caller_identity()
        if caller.get('Arn', '').endswith(':root'):
            raise ValueError('Root credentials are unsupported; configure a dedicated IAM role.')

    def client(self, service, **kwargs):
        from botocore.config import Config
        return self.session.client(service, config=Config(connect_timeout=5, read_timeout=20,
                                   retries={'mode': 'standard', 'total_max_attempts': 3}), **kwargs)

    def hierarchy(self, boundary):
        client = self.client('organizations', region_name=self.meta.get('organizations_region', 'us-east-1'))
        org = client.describe_organization()['Organization']
        if boundary and org['Id'] != boundary:
            raise ValueError('Discovery boundary mismatch')
        management = org.get('ManagementAccountId', org.get('MasterAccountId'))
        if self.meta.get('organization_credential_source') == 'controller' and self.client('sts').get_caller_identity()['Account'] != management:
            raise ValueError('Controller organization discovery requires the management account')
        accounts = list(pages(client, 'list_accounts', 'Accounts'))
        manager = next((a for a in accounts if a['Id'] == management), {})
        result = [node(org['Id'], (manager.get('Name') + ' organization') if manager.get('Name') else org['Id'], 'organization', arn=org.get('Arn'),
                       management_account_id=management, management_account_name=manager.get('Name'),
                       feature_set=org.get('FeatureSet'), total_accounts=len(accounts),
                       active_accounts=sum(a.get('State', a.get('Status')) == 'ACTIVE' for a in accounts),
                       suspended_accounts=sum(a.get('State', a.get('Status')) == 'SUSPENDED' for a in accounts),
                       last_successful_sync=datetime.now(timezone.utc).isoformat())]
        visited = set()
        def walk(parent):
            if parent in visited:
                return
            visited.add(parent)
            for ou in pages(client, 'list_organizational_units_for_parent', 'OrganizationalUnits', ParentId=parent):
                detail = client.describe_organizational_unit(OrganizationalUnitId=ou['Id'])['OrganizationalUnit']
                result.append(node(ou['Id'], detail['Name'], 'ou', parent, arn=detail.get('Arn')))
                walk(ou['Id'])
        for root in pages(client, 'list_roots', 'Roots'):
            result.append(node(root['Id'], root['Name'], 'root', org['Id']))
            walk(root['Id'])
        for account in accounts:
            detail = client.describe_account(AccountId=account['Id'])['Account']
            parents = list(pages(client, 'list_parents', 'Parents', ChildId=account['Id']))
            result.append(node(account['Id'], detail['Name'], 'account', parents[0]['Id'] if parents else None,
                               email=detail.get('Email'), state=detail.get('State', detail.get('Status')),
                               management=account['Id'] == management))
        result[0]['metadata']['roots'] = ', '.join(n['id'] for n in result if n['type'] == 'root')
        result[0]['metadata']['organizational_units'] = ', '.join(n['name'] for n in result if n['type'] == 'ou') or 'None'
        result[0]['metadata']['member_accounts'] = ', '.join(n['name'] + ' (' + n['id'] + ')' for n in result
            if n['type'] == 'account' and not n['metadata']['management'])
        result[0]['metadata']['discovery_status'] = 'Verified'
        return result

    def resources(self, target):
        identity = self.client('sts').get_caller_identity()['Account']
        if identity != target:
            raise ValueError('Target identity mismatch')
        region = self.meta.get('region', 'us-east-1')
        ec2 = self.client('ec2', region_name=region)
        rows, errors = [], []
        try:
            regions = [r['RegionName'] for r in ec2.describe_regions()['Regions']]
            if self.meta.get('inventory_regions'):
                configured = self.meta['inventory_regions']
                if isinstance(configured, str):
                    configured = [r.strip() for r in configured.split(',') if r.strip()]
                if set(configured) - set(regions):
                    errors.append('Some configured regions are unsupported or not enabled.')
                regions = [r for r in configured if r in regions]
        except Exception:
            regions = [region]
            errors.append('Region discovery unavailable; only the configured region was inspected.')
        from concurrent.futures import ThreadPoolExecutor
        def collect_region(location):
            regional, warnings = [], []
            clients = {}
            def api_client(api):
                if api not in clients:
                    clients[api] = self.client(api, region_name=location)
                return clients[api]
            collectors = {
                'EC2': ('ec2', 'describe_instances', 'Reservations', 'Instances', 'InstanceId'),
                'VPC': ('ec2', 'describe_vpcs', 'Vpcs', None, 'VpcId'),
                'EBS': ('ec2', 'describe_volumes', 'Volumes', None, 'VolumeId'),
                'Auto Scaling': ('autoscaling', 'describe_auto_scaling_groups', 'AutoScalingGroups', None, 'AutoScalingGroupName'),
                'CloudWatch': ('cloudwatch', 'list_metrics', 'Metrics', None, 'MetricName'),
                'RDS': ('rds', 'describe_db_instances', 'DBInstances', None, 'DBInstanceIdentifier'),
                'EKS': ('eks', 'list_clusters', 'clusters', None, None),
                'Lambda': ('lambda', 'list_functions', 'Functions', None, 'FunctionName'),
                'Load Balancers': ('elbv2', 'describe_load_balancers', 'LoadBalancers', None, 'LoadBalancerArn'),
                'Classic Load Balancers': ('elb', 'describe_load_balancers', 'LoadBalancerDescriptions', None, 'LoadBalancerName'),
            }
            for service, (api, method, field, nested, key) in collectors.items():
                try:
                    client = api_client(api)
                    responses = client.get_paginator(method).paginate() if client.can_paginate(method) else [getattr(client, method)()]
                    for response in responses:
                        items = response.get(field, [])
                        if nested:
                            items = [x for group in items for x in group.get(nested, [])]
                        for item in items:
                            name = item[key] if key else item
                            state = item.get('State') if isinstance(item, dict) else None
                            status = (item.get('DBInstanceStatus') or (state.get('Name') or state.get('Code') if isinstance(state, dict) else state)) if isinstance(item, dict) else None
                            try:
                                if service == 'EKS':
                                    status = client.describe_cluster(name=name)['cluster'].get('status')
                                    for group_name in pages(client, 'list_nodegroups', 'nodegroups', clusterName=name):
                                        group = client.describe_nodegroup(clusterName=name, nodegroupName=group_name)['nodegroup']
                                        regional.append(dict(id=group.get('nodegroupArn', group_name), name=group_name,
                                            service='EKS', resource_type='EKS node group', account=target, region=location,
                                            status=group.get('status', 'Unavailable'), configuration=', '.join(group.get('instanceTypes', [])), cost='Unavailable'))
                                if service == 'Lambda':
                                    status = client.get_function_configuration(FunctionName=name).get('State')
                            except Exception:
                                warnings.append(service + ' / ' + location + ': resource status unavailable')
                            display = item.get('LoadBalancerName') or next((t['Value'] for t in item.get('Tags', []) if t.get('Key') == 'Name'), name) if isinstance(item, dict) else name
                            regional.append(dict(id=name, name=display, service=service, resource_type=service,
                                account=target, region=location, status=status or ('Exists' if service in {'VPC', 'Classic Load Balancers'} else 'Unavailable'),
                                configuration=(item.get('InstanceType') or item.get('DBInstanceClass') or item.get('Runtime') or item.get('VolumeType') or item.get('LaunchConfigurationName') or '') if isinstance(item, dict) else '',
                                attachments=str(item.get('Attachments', [])) if isinstance(item, dict) and service == 'EBS' else '',
                                cost='Unavailable (service billing is shown separately)'))
                except Exception as exc:
                    warnings.append(service + ' / ' + location + ': ' + api_failure(exc))
            try:
                ecs = api_client('ecs')
                for arn in pages(ecs, 'list_clusters', 'clusterArns'):
                    cluster = ecs.describe_clusters(clusters=[arn])['clusters'][0]
                    regional.append(dict(id=arn, name=cluster.get('clusterName', arn), service='ECS', resource_type='ECS cluster',
                        account=target, region=location, status=cluster.get('status', 'Unavailable'),
                        running_tasks=cluster.get('runningTasksCount'), cost='Unavailable'))
                    for service_arn in pages(ecs, 'list_services', 'serviceArns', cluster=arn):
                        detail = ecs.describe_services(cluster=arn, services=[service_arn])['services'][0]
                        regional.append(dict(id=service_arn, name=detail.get('serviceName', service_arn), service='ECS',
                            resource_type='ECS service', account=target, region=location, status=detail.get('status', 'Unavailable'),
                            running_tasks=detail.get('runningCount'), desired_tasks=detail.get('desiredCount'), cost='Unavailable'))
            except Exception as exc:
                warnings.append('ECS / ' + location + ': ' + api_failure(exc))
            return regional, warnings
        with ThreadPoolExecutor(max_workers=4) as pool:
            for regional, warnings in pool.map(collect_region, regions):
                rows.extend(regional)
                errors.extend(warnings)
        try:
            s3 = self.client('s3')
            responses = s3.get_paginator('list_buckets').paginate() if s3.can_paginate('list_buckets') else [s3.list_buckets()]
            for response in responses:
                for bucket in response['Buckets']:
                    try:
                        location = s3.get_bucket_location(Bucket=bucket['Name']).get('LocationConstraint') or 'us-east-1'
                    except Exception:
                        location = 'Unavailable'
                        errors.append('S3: bucket location unavailable')
                    rows.append(dict(id=bucket['Name'], name=bucket['Name'], service='S3', resource_type='S3 bucket',
                        account=target, region='eu-west-1' if location == 'EU' else location, status='Exists', cost='Unavailable'))
        except Exception:
            errors.append('S3: unavailable')
        stamp = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row['last_discovered'] = stamp
        return rows, errors

    def cost(self, targets, start, end):
        client = self.client('ce', region_name='us-east-1')
        # Validate the billing API and preserve its linked-account visibility.
        from botocore.paginate import Paginator
        dimension_pages = Paginator(client.get_dimension_values,
            {'input_token': 'NextPageToken', 'output_token': 'NextPageToken', 'result_key': 'DimensionValues'},
            client.meta.service_model.operation_model('GetDimensionValues'))
        for _ in dimension_pages.paginate(TimePeriod={'Start': start, 'End': end},
                Dimension='LINKED_ACCOUNT', Context='COST_AND_USAGE',
                Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': sorted(set(targets))}}):
            pass
        rows = []
        periods = {}
        for dimension in ('SERVICE', 'REGION', 'RECORD_TYPE'):
            responses = cost_pages(client,
                TimePeriod={'Start': start, 'End': end}, Granularity='DAILY',
                Metrics=['UnblendedCost'], Filter={'Dimensions': {'Key': 'LINKED_ACCOUNT', 'Values': targets}},
                GroupBy=[{'Type': 'DIMENSION', 'Key': 'LINKED_ACCOUNT'}, {'Type': 'DIMENSION', 'Key': dimension}])
            for response in responses:
                for day in response['ResultsByTime']:
                    periods[(dimension.lower(), day['TimePeriod']['Start'])] = day.get('Total', {}).get('UnblendedCost', {}).get('Unit')
                    # CE returns a period even when no usage was reported. Export-based
                    # providers do not establish zero when historical rows are absent.
                    reported = {group['Keys'][0] for group in day['Groups']}
                    # Zero rows are added only after all pages have been read.
                    for group in day['Groups']:
                        value = group['Metrics']['UnblendedCost']
                        rows.append(dict(date=day['TimePeriod']['Start'], target=group['Keys'][0], dimension=dimension.lower(),
                                         category=group['Keys'][1], amount=value['Amount'], currency=value['Unit'], estimated=day.get('Estimated', False)))
        # A successful period with no groups establishes zero, but never invent a currency.
        currencies = {r['currency'] for r in rows} | {c for c in periods.values() if c}
        if len(currencies) == 1:
            currency = next(iter(currencies))
            for dimension in ('service', 'region', 'record_type'):
                dates = {date for dim, date in periods if dim == dimension}
                present = {(r['date'], r['target']) for r in rows if r['dimension'] == dimension}
                for date in dates:
                    for target in set(targets):
                        if (date, target) not in present:
                            rows.append(dict(date=date, target=target, dimension=dimension, category='No reported usage',
                                             amount='0', currency=currency, estimated=True))
        return rows


class RESTAdapter:
    def request(self, url, body=None):
        if not any(url.startswith(host) for host in self.hosts):
            raise ValueError('Unexpected cloud endpoint')
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Authorization': 'Bearer ' + self.token(), 'Content-Type': 'application/json'})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                delay = exc.headers.get('Retry-After', '')
                time.sleep(min(float(delay), 5) if delay.isdigit() else 0.5 * 2 ** attempt)

    def listing(self, url, field='value'):
        seen = set()
        while url:
            if url in seen:
                raise ValueError('Repeated page')
            seen.add(url)
            result = self.request(url)
            yield from result.get(field, [])
            if result.get('nextPageToken'):
                import urllib.parse
                parsed = urllib.parse.urlsplit(url)
                query = dict(urllib.parse.parse_qsl(parsed.query))
                query['pageToken'] = result['nextPageToken']
                url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
            else:
                url = result.get('nextLink') or result.get('next')


class AzureAdapter(RESTAdapter):
    hosts = ('https://management.azure.com/',)
    base = hosts[0]
    def __init__(self, connection, secret):
        self.meta = connection.get('metadata', {})
        self.credential = azure_credential(self.meta, secret)

    def token(self):
        return self.credential.get_token('https://management.azure.com/.default').token

    def hierarchy(self, boundary):
        tenant = self.meta['tenant_id']
        if boundary != tenant:
            raise ValueError('Discovery boundary mismatch')
        tenants = list(self.listing(self.base + 'tenants?api-version=2022-12-01'))
        info = next((t for t in tenants if t['tenantId'] == tenant), None)
        if info is None:
            raise ValueError('Tenant identity mismatch')
        result = [node(tenant, info.get('displayName', tenant), 'tenant')]
        for group in self.listing(self.base + 'providers/Microsoft.Management/managementGroups?api-version=2020-05-01'):
            detail = self.request(self.base + group['id'].lstrip('/') + '?api-version=2020-05-01')
            props = detail.get('properties', {})
            parent = props.get('details', {}).get('parent', {}).get('id') or tenant
            result.append(node(group['id'], props.get('displayName', group['name']), 'management_group', parent))
        parents = {}
        for group in list(result):
            if group['type'] == 'management_group':
                for child in self.listing(self.base + group['id'].lstrip('/') + '/descendants?api-version=2020-05-01'):
                    parents[child['id']] = child.get('properties', {}).get('parent', {}).get('id', group['id'])
        for sub in self.listing(self.base + 'subscriptions?api-version=2022-12-01'):
            if sub.get('tenantId') and sub['tenantId'] != tenant:
                continue
            result.append(node(sub['subscriptionId'], sub.get('displayName'), 'subscription', parents.get(sub['id'], tenant), state=sub.get('state')))
        return result

    def resources(self, target):
        prefix = self.base + 'subscriptions/' + target
        rows = []
        for group in self.listing(prefix + '/resourcegroups?api-version=2021-04-01'):
            rows.append(dict(id=group['id'], name=group['name'], service='Resource Groups', resource_type='Resource group', account=target,
                region=group.get('location'), status=group.get('properties', {}).get('provisioningState', 'Exists'), cost='Unavailable'))
        errors = []
        for item in self.listing(prefix + '/resources?api-version=2021-04-01'):
            status = item.get('properties', {}).get('provisioningState', 'Unavailable')
            if item['type'].lower() == 'microsoft.compute/virtualmachines':
                try:
                    view = self.request(self.base + item['id'].lstrip('/') + '/instanceView?api-version=2024-07-01')
                    status = next((s.get('displayStatus') or s['code'] for s in view.get('statuses', []) if s['code'].startswith('PowerState/')), 'Unavailable')
                except Exception:
                    errors.append(item['name'] + ': runtime status unavailable')
            rows.append(dict(id=item['id'], name=item['name'], service=item['type'], region=item.get('location'),
                             resource_type=item['type'], account=target, cost='Unavailable',
                             parent_id=item['id'].split('/providers/')[0], status=status))
        return rows, errors

    def cost(self, targets, start, end):
        rows = []
        for target in targets:
            for dimension in ('ServiceName', 'ResourceLocation'):
                body = dict(type='ActualCost', timeframe='Custom', timePeriod={'from': start, 'to': end + 'T00:00:00Z'},
                            dataset=dict(granularity='Daily', aggregation={'totalCost': {'name': 'Cost', 'function': 'Sum'}},
                                         grouping=[{'type': 'Dimension', 'name': dimension}]))
                url = self.base + 'subscriptions/' + target + '/providers/Microsoft.CostManagement/query?api-version=2023-11-01'
                while url:
                    result = self.request(url, body)['properties']
                    columns = [c['name'] for c in result['columns']]
                    for values in result['rows']:
                        row = dict(zip(columns, values))
                        date = str(row['UsageDate'])
                        rows.append(dict(date=f'{date[:4]}-{date[4:6]}-{date[6:8]}', target=target,
                            dimension='service' if dimension == 'ServiceName' else 'region', category=row.get(dimension, 'Unallocated'),
                            amount=row.get('Cost', row.get('PreTaxCost')), currency=row['Currency']))
                    url = result.get('nextLink')
        return rows


class GCPAdapter(RESTAdapter):
    hosts = ('https://cloudresourcemanager.googleapis.com/', 'https://cloudasset.googleapis.com/')
    def __init__(self, connection, secret):
        self.meta = connection.get('metadata', {})
        self.credential = gcp_credential(self.meta, secret)

    def token(self):
        from google.auth.transport.requests import Request
        if not self.credential.valid:
            self.credential.refresh(Request())
        return self.credential.token

    def hierarchy(self, boundary):
        base = self.hosts[0] + 'v3/'
        if not boundary.startswith(('organizations/', 'folders/')):
            raise ValueError('Expected organization or folder boundary')
        info = self.request(base + boundary)
        result = [node(boundary, info.get('displayName'), 'organization' if boundary.startswith('organizations/') else 'folder')]
        visited = set()
        def walk(parent):
            if parent in visited:
                return
            visited.add(parent)
            for folder in self.listing(base + 'folders?parent=' + parent, 'folders'):
                result.append(node(folder['name'], folder.get('displayName'), 'folder', parent, state=folder.get('state')))
                walk(folder['name'])
            for project in self.listing(base + 'projects?parent=' + parent, 'projects'):
                result.append(node(project['projectId'], project.get('displayName'), 'project', parent, project_number=project['name'], state=project.get('state')))
        walk(boundary)
        return result

    def resources(self, target):
        rows = []
        # Cloud Asset provides paginated project-wide inventory across all locations.
        for item in self.listing(self.hosts[1] + 'v1/projects/' + target + ':searchAllResources', 'results'):
            rows.append(dict(id=item['name'], name=item.get('displayName') or item['name'].rsplit('/', 1)[-1],
                             service=item['assetType'], resource_type=item['assetType'], account=target, cost='Unavailable',
                             region=item.get('location', 'global'), status=item.get('state') or 'Unavailable'))
        return rows, []

    def cost(self, targets, start, end):
        import re
        from google.cloud import bigquery
        table = self.meta.get('billing_export_table', '')
        if not re.fullmatch(r'[a-z][a-z0-9-]*\.[A-Za-z_][\w]*\.[A-Za-z_][\w]*', table):
            raise ValueError('Billing export not configured')
        client = bigquery.Client(project=self.meta.get('billing_query_project') or table.split('.')[0], credentials=self.credential)
        sql = f'''SELECT DATE(usage_start_time) date, project.id target, service.description service,
            COALESCE(location.region, 'Unallocated') region, currency,
            SUM(cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c),0)) amount,
            SUM(IF(cost_type = 'regular', cost, 0)) gross_usage,
            SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c),0)) credit_amount,
            cost_type
            FROM `{table}` WHERE usage_start_time >= TIMESTAMP(@start) AND usage_start_time < TIMESTAMP(@end)
            AND project.id IN UNNEST(@targets) GROUP BY 1,2,3,4,5,cost_type'''
        config = bigquery.QueryJobConfig(maximum_bytes_billed=int(self.meta.get('billing_max_bytes', 1000000000)), query_parameters=[
            bigquery.ScalarQueryParameter('start', 'STRING', start), bigquery.ScalarQueryParameter('end', 'STRING', end),
            bigquery.ArrayQueryParameter('targets', 'STRING', targets)])
        rows = []
        for item in client.query(sql, job_config=config).result(timeout=60):
            for dimension in ('service', 'region'):
                rows.append(dict(date=str(item['date']), target=item['target'], dimension=dimension,
                                 category=item[dimension], currency=item['currency'], amount=str(item['amount'])))
            components = {'Usage': item['gross_usage'], 'Credit': item['credit_amount']}
            if item['cost_type'] != 'regular':
                components[item['cost_type']] = Decimal(str(item['amount'])) - Decimal(str(item['credit_amount']))
            for category, amount in components.items():
                rows.append(dict(date=str(item['date']), target=item['target'], dimension='record_type',
                                 category=category, currency=item['currency'], amount=str(amount)))
        return rows


ADAPTERS = {'AWS': AWSAdapter, 'Azure': AzureAdapter, 'GCP': GCPAdapter}
