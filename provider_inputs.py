"""Read-only, account-scoped choices for server and managed Kubernetes plans.

Only public resource identifiers leave this module. Secrets and access tokens
stay in local SDK clients; failed discovery never substitutes sample resources.
"""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

from provider_connections import get_connection, get_saved_secret, get_aws_session


def choice(value, label=None, **metadata):
    return dict(value=value, label=label or value, **metadata)


def _json(url, token, host):
    if not url.startswith("https://" + host + "/"):
        raise ValueError("Unexpected provider API host")
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _azure_list(url, token):
    rows = []
    for _ in range(100):
        page = _json(url, token, "management.azure.com")
        rows.extend(page.get("value", []))
        url = page.get("nextLink")
        if not url:
            return rows
    raise ValueError("Azure pagination limit exceeded")


def _aws_pages(client, operation, result_key, **kwargs):
    rows, token = [], None
    for _ in range(100):
        args = dict(kwargs)
        request_token, response_token = (("nextToken", "nextToken") if operation == "describe_cluster_versions" else
                                         ("Marker", "Marker") if operation == "list_roles" else
                                         ("Marker", "NextMarker") if operation == "list_keys" else ("NextToken", "NextToken"))
        if token:
            args[request_token] = token
        page = getattr(client, operation)(**args)
        rows.extend(page.get(result_key, []))
        token = page.get(response_token)
        if not token:
            return rows
    raise ValueError("AWS pagination limit exceeded")


def discover(connection_id, region, kind, os_name="Linux"):
    conn = get_connection(connection_id)
    if not conn or conn.get("connection_status") != "CONNECTED":
        raise ValueError("Select a tested cloud account before loading live inputs")
    result = dict(provider=conn["provider"], connection_id=connection_id, region=region,
                  retrieved_at=datetime.now(timezone.utc).isoformat(), choices={}, errors={}, diagnostics=[])
    fields, errors = result["choices"], result["errors"]

    def load(names, fetch):
        names = [names] if isinstance(names, str) else names
        try:
            values = fetch()
            if len(names) == 1:
                fields[names[0]] = values
            else:
                fields.update(values)
        except Exception as exc:
            response = getattr(exc, "response", {})
            code = response.get("Error", {}).get("Code", "SDKOrServiceError")
            operation = getattr(exc, "operation_name", "")
            operation = operation if isinstance(operation, str) and operation.isalnum() else "UnknownOperation"
            code = code if code in {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "ThrottlingException", "NotFoundException"} else "SDKOrServiceError"
            for name in names:
                permissions = {"cluster_role_arn": "iam:ListRoles", "node_role_arn": "iam:ListRoles",
                               "kubernetes_version": "eks:DescribeClusterVersions", "kms_key_arn": "kms:ListKeys / kms:DescribeKey"}
                errors[name] = f"{code}. Required read: {permissions.get(name, 'provider resource read')}. Check SDK support and selected account permissions."
                result["diagnostics"].append(dict(field=name, operation=operation, category=code,
                                                  permission=permissions.get(name, "provider resource read")))

    if conn["provider"] == "AWS":
        session = get_aws_session(connection_id, purpose="inventory")
        ec2 = session.client("ec2", region_name=region)
        load("subnet_id", lambda: [choice(s["SubnetId"], f"{s['SubnetId']} · {s['AvailabilityZone']} · {s['VpcId']}",
                                               vpc_id=s["VpcId"], zone=s["AvailabilityZone"])
                                   for s in _aws_pages(ec2, "describe_subnets", "Subnets") if s.get("State") == "available"])
        if kind == "VM":
            from poc_catalog import aws_images
            load("image_id", lambda: [choice(i["id"], i["name"] + " · " + i["id"]) for i in aws_images(connection_id, region, os_name)])
            load("security_group_id", lambda: [choice(s["GroupId"], s["GroupName"] + " · " + s["GroupId"], vpc_id=s["VpcId"])
                                               for s in _aws_pages(ec2, "describe_security_groups", "SecurityGroups")])
        else:
            fields["second_subnet_id"] = fields.get("subnet_id", [])
            if "subnet_id" in errors:
                errors["second_subnet_id"] = errors["subnet_id"]
            def roles():
                rows = _aws_pages(session.client("iam"), "list_roles", "Roles")
                def trusting(role, service):
                    policy = role.get("AssumeRolePolicyDocument", {})
                    for statement in policy.get("Statement", []):
                        principal = statement.get("Principal", {})
                        services = principal.get("Service", []) if isinstance(principal, dict) else []
                        services = [services] if isinstance(services, str) else services
                        if statement.get("Effect") == "Allow" and service in services:
                            return True
                    return False
                return {field: [choice(r["Arn"], r["RoleName"]) for r in rows if trusting(r, service)]
                        for field, service in (("cluster_role_arn", "eks.amazonaws.com"), ("node_role_arn", "ec2.amazonaws.com"))}
            load(["cluster_role_arn", "node_role_arn"], roles)
            eks = session.client("eks", region_name=region)
            load("kubernetes_version", lambda: [choice(v["clusterVersion"]) for v in _aws_pages(
                eks, "describe_cluster_versions", "clusterVersions", includeAll=False)
                if v.get("versionStatus", "STANDARD_SUPPORT") != "UNSUPPORTED"])
            def keys():
                kms = session.client("kms", region_name=region)
                values = []
                for key in _aws_pages(kms, "list_keys", "Keys"):
                    try:
                        info = kms.describe_key(KeyId=key["KeyId"])["KeyMetadata"]
                    except Exception:
                        errors["kms_key_arn"] = "Some keys could not be inspected. Required read: kms:DescribeKey; check the key policy."
                        continue
                    if (info.get("KeyState") == "Enabled" and info.get("KeyUsage") == "ENCRYPT_DECRYPT"
                            and info.get("KeyManager") == "CUSTOMER" and info.get("KeySpec") == "SYMMETRIC_DEFAULT"):
                        values.append(choice(info["Arn"]))
                return values
            load("kms_key_arn", keys)
    elif conn["provider"] == "Azure":
        from azure.identity import ClientSecretCredential
        meta = conn["metadata"]
        token = ClientSecretCredential(meta["tenant_id"], meta["client_id"], get_saved_secret(connection_id)["client_secret"]).get_token("https://management.azure.com/.default").token
        base = "https://management.azure.com/subscriptions/" + quote(meta["subscription_id"], safe="")
        def listing(path, version):
            return _azure_list(base + path + "?api-version=" + version, token)
        load("resource_group", lambda: [choice(r["name"]) for r in listing("/resourcegroups", "2021-04-01")])
        def subnets():
            return [choice(s["id"], v["name"] + " / " + s["name"], resource_group=v["id"].split("/")[4])
                    for v in listing("/providers/Microsoft.Network/virtualNetworks", "2024-05-01")
                    if v.get("location", "").lower() == region.lower() for s in v.get("properties", {}).get("subnets", [])]
        load("subnet_id", subnets)
        if kind == "Kubernetes":
            def versions():
                rows = _json(base + "/providers/Microsoft.ContainerService/locations/" + quote(region, safe="") + "/kubernetesVersions?api-version=2026-03-01", token, "management.azure.com").get("values", [])
                return [choice(v) for row in rows if not row.get("isPreview")
                        for v in (row.get("patchVersions") or {row["version"]: {}})]
            load("kubernetes_version", versions)
        else:
            load("network_security_group_id", lambda: [choice(r["id"], r["name"], resource_group=r["id"].split("/")[4])
                 for r in listing("/providers/Microsoft.Network/networkSecurityGroups", "2024-05-01") if r.get("location", "").lower() == region.lower()])
            def images():
                publisher, offer, sku = ("MicrosoftWindowsServer", "WindowsServer", "2022-datacenter") if os_name == "Windows" else ("Canonical", "ubuntu-24_04-lts", "server")
                path = f"/providers/Microsoft.Compute/locations/{quote(region, safe='')}/publishers/{publisher}/artifacttypes/vmimage/offers/{offer}/skus/{sku}/versions"
                # Image versions API returns a JSON array, unlike ARM resource lists.
                rows = _json(base + path + "?api-version=2024-03-01", token, "management.azure.com")
                return [choice(r["id"], publisher + " · " + r["name"]) for r in sorted(rows, key=lambda r: r["name"], reverse=True)[:20]]
            load("image_id", images)
    elif conn["provider"] == "GCP":
        from google.auth.transport.requests import Request, AuthorizedSession
        from google.oauth2 import service_account
        info = get_saved_secret(connection_id)["service_account_json"]
        credentials = service_account.Credentials.from_service_account_info(json.loads(info) if isinstance(info, str) else info,
                      scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        http = AuthorizedSession(credentials)
        project = quote(conn["metadata"]["project_id"], safe="")
        def get(url):
            if not url.startswith(("https://compute.googleapis.com/", "https://container.googleapis.com/", "https://iam.googleapis.com/")):
                raise ValueError("Unexpected Google API host")
            response = http.get(url, timeout=20)
            response.raise_for_status()
            return response.json()
        def listing(url, key="items"):
            values, token = [], None
            for _ in range(100):
                page = get(url + ("&" if "?" in url else "?") + "pageToken=" + quote(token, safe="") if token else url)
                values.extend(page.get(key, []))
                token = page.get("nextPageToken")
                if not token:
                    return values
            raise ValueError("GCP pagination limit exceeded")
        base = f"https://compute.googleapis.com/compute/v1/projects/{project}"
        load("zone", lambda: [choice(z["name"]) for z in listing(base + "/zones") if z["name"].startswith(region + "-") and z.get("status") == "UP"])
        load("subnet_id", lambda: [choice(s["selfLink"], s["name"], network_id=s["network"], ranges=[r["rangeName"] for r in s.get("secondaryIpRanges", [])])
                                   for s in listing(base + "/regions/" + quote(region, safe="") + "/subnetworks")])
        if kind == "Kubernetes":
            load("network_id", lambda: [choice(n["selfLink"], n["name"]) for n in listing(base + "/global/networks")])
            def versions():
                data = get(f"https://container.googleapis.com/v1/projects/{project}/locations/{quote(region, safe='')}/serverConfig")
                return [choice(v) for v in data.get("validMasterVersions", []) if v in data.get("validNodeVersions", [])]
            load("kubernetes_version", versions)
            load("node_service_account", lambda: [choice(a["email"]) for a in listing(f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts", "accounts") if not a.get("disabled")])
        else:
            image_project = "windows-cloud" if os_name == "Windows" else "ubuntu-os-cloud"
            def images():
                rows = listing(f"https://compute.googleapis.com/compute/v1/projects/{image_project}/global/images?filter=status%3DREADY")
                return [choice(f"projects/{image_project}/global/images/{i['name']}") for i in sorted(rows, key=lambda i: i.get("creationTimestamp", ""), reverse=True)
                        if i.get("architecture") == "X86_64" and "pro-" not in i["name"] and
                        (i["name"].startswith("windows-server-") if os_name == "Windows" else i["name"].startswith("ubuntu-"))][:20]
            load("image_id", images)
    else:
        raise ValueError("Unsupported cloud provider")
    # Stable de-duplication without assuming Kubernetes versions are floats.
    for name, values in fields.items():
        fields[name] = list({v["value"]: v for v in values}.values())
    return result


def compatible_choices(name, evidence, inputs):
    """Filter dependent networks without mixing accounts, VPCs or EKS zones."""
    rows = evidence.get("choices", {}).get(name, [])
    fields = evidence.get("choices", {})
    subnet = next((s for s in fields.get("subnet_id", []) if s["value"] == inputs.get("subnet_id")), {})
    if name == "second_subnet_id":
        rows = [r for r in rows if r.get("vpc_id") == subnet.get("vpc_id") and r.get("zone") != subnet.get("zone")]
    if name == "security_group_id":
        rows = [r for r in rows if not subnet or r.get("vpc_id") == subnet.get("vpc_id")]
    if name == "subnet_id" and evidence.get("provider") == "GCP" and inputs.get("network_id"):
        rows = [r for r in rows if r.get("network_id") == inputs["network_id"]]
    if name in {"pod_range_name", "service_range_name"}:
        rows = [choice(r) for r in subnet.get("ranges", []) if r != inputs.get("pod_range_name") or name == "pod_range_name"]
    return rows


def validate_live_inputs(candidate, request, inputs):
    """Fresh read at planning time, including live hardware in the chosen account."""
    cid, region = candidate["connection_id"], candidate["region"]
    conn = get_connection(cid)
    if not conn or conn.get("provider") != candidate["provider"] or conn.get("connection_status") != "CONNECTED":
        raise ValueError("Select a tested account matching the configuration provider")
    if candidate["provider"] == "AWS":
        ec2 = get_aws_session(cid, purpose="inventory").client("ec2", region_name=region)
        from cloud_adapters.aws import _usable_for_cloudpilot_linux
        specs = ec2.describe_instance_types(InstanceTypes=[candidate["sku"]]).get("InstanceTypes", [])
        spec = next((s for s in specs if s.get("InstanceType") == candidate["sku"]), None)
        if not spec or not _usable_for_cloudpilot_linux(spec)[0]:
            raise ValueError("Selected instance type is unavailable or incompatible in this account")
        cpu, memory = spec["VCpuInfo"]["DefaultVCpus"], spec["MemoryInfo"]["SizeInMiB"] / 1024
        offered = ec2.describe_instance_type_offerings(LocationType="region", Filters=[
            {"Name": "location", "Values": [region]}, {"Name": "instance-type", "Values": [candidate["sku"]]}])
        if not offered.get("InstanceTypeOfferings"):
            raise ValueError("Selected instance type is no longer offered in this region")
    else:
        from poc_catalog import profiles
        rows = profiles(candidate["provider"], cid, region, inputs.get("zone", candidate.get("zone", "")))
        spec = next((r for r in rows if r["sku"] == candidate["sku"]), None)
        if not spec:
            raise ValueError("Selected machine size is unavailable in this account and location")
        cpu, memory = spec["vcpus"], spec["memory_gib"]
    if cpu != candidate["vcpus"] or abs(memory - candidate["memory_gib"]) > .001:
        raise ValueError("Live hardware changed; send the request again")
    evidence = discover(cid, region, request["kind"], candidate["os"])
    required = {"subnet_id", "image_id"} if request["kind"] == "VM" else {"subnet_id", "kubernetes_version"}
    if candidate["provider"] == "AWS":
        required |= {"security_group_id"} if request["kind"] == "VM" else {"second_subnet_id", "cluster_role_arn", "node_role_arn", "kms_key_arn"}
    elif candidate["provider"] == "Azure":
        required.add("resource_group")
        if request["kind"] == "VM":
            required.add("network_security_group_id")
    else:
        required.add("zone")
        if request["kind"] == "Kubernetes":
            required |= {"network_id", "pod_range_name", "service_range_name", "node_service_account"}
    for name in required:
        values = {r["value"] for r in compatible_choices(name, evidence, inputs)}
        if inputs.get(name) not in values:
            raise ValueError("Live account validation failed for " + name.replace("_", " ") + "; refresh live values and choose an available value")
    return {"connection_id": cid, "region": region, "retrieved_at": evidence["retrieved_at"], "status": "verified"}
