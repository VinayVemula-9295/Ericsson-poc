"""Read-only official provider catalogue and image discovery for the POC."""
from __future__ import annotations
import json
import re
import urllib.request
from datetime import datetime, timezone
from provider_connections import get_connection, get_saved_secret, get_aws_session

SOURCES = {
    "AWS": "https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeInstanceTypes.html",
    "Azure": "https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list",
    "GCP": "https://cloud.google.com/compute/docs/reference/rest/v1/machineTypes/list",
}

def profiles(provider: str, connection_id: str, region: str, zone: str = "") -> list[dict]:
    """No fixture fallback. Credentials remain ephemeral and outside returned evidence."""
    connection = get_connection(connection_id)
    if not connection or connection.get("provider") != provider or connection.get("connection_status") != "CONNECTED":
        raise ValueError("A tested connection for this provider is required for live specifications")
    meta = connection.get("metadata", {})
    secret = get_saved_secret(connection_id)
    rows = []
    if provider == "Azure":
        from azure.identity import ClientSecretCredential
        credential = ClientSecretCredential(meta["tenant_id"], meta["client_id"], secret["client_secret"])
        token = credential.get_token("https://management.azure.com/.default").token
        url = f"https://management.azure.com/subscriptions/{meta['subscription_id']}/providers/Microsoft.Compute/skus?api-version=2021-07-01"
        for _ in range(100):
            if not url:
                break
            if not url.startswith("https://management.azure.com/"):
                raise ValueError("Unexpected Azure pagination host")
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=20) as response:
                data = json.load(response)
            for sku in data.get("value", []):
                name = sku.get("name", "")
                if sku.get("resourceType") != "virtualMachines" or region not in sku.get("locations", []):
                    continue
                if not re.match(r"^Standard_[BDEF][0-9]", name) or sku.get("restrictions"):
                    continue
                cap = {x["name"]: x["value"] for x in sku.get("capabilities", [])}
                if cap.get("CpuArchitectureType", "x64") != "x64":
                    continue
                rows.append({"sku": name, "vcpus": int(cap.get("vCPUs", 0)), "memory_gib": float(cap.get("MemoryGB", 0))})
            url = data.get("nextLink")
        else:
            raise ValueError("Azure catalogue pagination limit exceeded")
    elif provider == "GCP":
        from google.cloud import compute_v1
        from google.oauth2 import service_account
        info = secret["service_account_json"]
        if isinstance(info, str):
            info = json.loads(info)
        credentials = service_account.Credentials.from_service_account_info(info)
        for machine in compute_v1.MachineTypesClient(credentials=credentials).list(project=meta["project_id"], zone=zone):
            if not re.match(r"^(n2|n2d|e2|c2|c3)-(standard|highmem|highcpu)-", machine.name):
                continue
            rows.append({"sku": machine.name, "vcpus": machine.guest_cpus, "memory_gib": machine.memory_mb / 1024})
    else:
        raise ValueError("AWS live discovery is supplied by the existing EC2 adapter")
    now = datetime.now(timezone.utc).isoformat()
    return [dict(row, provider=provider, region=region, spec_source=SOURCES[provider], retrieved_at=now,
                 account_connected=True, subscription_inventory_verified=True) for row in rows if row["vcpus"] and row["memory_gib"]]

def aws_images(connection_id: str, region: str, os_name: str) -> list[dict]:
    ec2 = get_aws_session(connection_id, purpose="inventory").client("ec2", region_name=region)
    filters = [{"Name": "state", "Values": ["available"]}, {"Name": "architecture", "Values": ["x86_64"]},
               {"Name": "name", "Values": ["Windows_Server-*-English-Full-Base-*" if os_name == "Windows" else "al2023-ami-2023*-kernel-*-x86_64"]}]
    images = ec2.describe_images(Owners=["amazon"], Filters=filters).get("Images", [])
    return [{"id": i["ImageId"], "name": i["Name"], "created_at": i["CreationDate"]} for i in sorted(images, key=lambda i: i["CreationDate"], reverse=True)[:20]]

def aws_quota(connection_id: str, region: str) -> dict:
    session = get_aws_session(connection_id, purpose="inventory")
    quota = session.client("service-quotas", region_name=region).get_service_quota(ServiceCode="ec2", QuotaCode="L-1216C47A")["Quota"]["Value"]
    # Usage is deliberately unknown: counting running instances misses pending demand,
    # reservations and other quota consumers. Never manufacture headroom.
    return {"standard_ondemand_vcpu_quota": quota, "quota_headroom_vcpus": None,
            "usage_status": "unknown", "spare_capacity": "unverified"}
