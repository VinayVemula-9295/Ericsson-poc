"""Secure provider configuration and live connection checks for IIPP.

Secrets are stored only in AWS Secrets Manager. The local JSON file contains
non-secret metadata and Secrets Manager ARNs. No credentials are written to
Streamlit session state by this module, deployment records, or application logs.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

PROVIDERS = {"AWS", "Azure", "GCP"}
APP_NAME = "innominds-iipp"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_dir() -> Path:
    path = Path(os.getenv("DATA_DIR", "/data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config_path() -> Path:
    return _data_dir() / "provider_connections.json"


def _read_all() -> dict[str, Any]:
    try:
        payload = json.loads(_config_path().read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"connections": []}
    except (OSError, json.JSONDecodeError):
        return {"connections": []}


def _write_all(payload: dict[str, Any]) -> None:
    path = _config_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _secrets_client():
    region = os.getenv("AWS_SECRETS_MANAGER_REGION") or os.getenv("AWS_REGION", "ap-south-1")
    return boto3.client("secretsmanager", region_name=region,
                        config=Config(connect_timeout=5, read_timeout=15,
                                      retries={"max_attempts": 3, "mode": "standard"}))


def _secret_name(provider: str, connection_id: str) -> str:
    return f"{APP_NAME}/connections/{provider.lower()}/{connection_id}"


def list_connections() -> list[dict[str, Any]]:
    """Return sanitized connection metadata; secret material is never returned."""
    rows = _read_all().get("connections", [])
    result = []
    for row in rows:
        if isinstance(row, dict):
            result.append({k: v for k, v in row.items() if k not in {"secret", "credentials", "private_key", "client_secret"}})
    return sorted(result, key=lambda x: (x.get("provider", ""), x.get("name", "")))


def get_connection(connection_id: str) -> dict[str, Any] | None:
    if not connection_id:
        return None
    for row in _read_all().get("connections", []):
        if isinstance(row, dict) and row.get("connection_id") == connection_id:
            return {k: v for k, v in row.items() if k not in {"secret", "credentials", "private_key", "client_secret"}}
    return None


def _get_secret(secret_arn: str) -> dict[str, Any]:
    if not secret_arn:
        return {}
    response = _secrets_client().get_secret_value(SecretId=secret_arn)
    raw = response.get("SecretString")
    if not raw:
        raise RuntimeError("The configured provider secret is not stored as a SecretString.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("The configured provider secret has an unexpected format.")
    return value


def _put_secret(provider: str, connection_id: str, secret_payload: dict[str, Any], prior_arn: str | None) -> str:
    client = _secrets_client()
    secret_string = json.dumps(secret_payload, separators=(",", ":"))
    if prior_arn:
        try:
            client.put_secret_value(SecretId=prior_arn, SecretString=secret_string)
            return prior_arn
        except client.exceptions.ResourceNotFoundException:
            pass
    response = client.create_secret(
        Name=_secret_name(provider, connection_id),
        Description=f"Encrypted credentials for Innominds Intelligent Provisioning Platform {provider} connection {connection_id}",
        SecretString=secret_string,
        Tags=[{"Key": "Application", "Value": APP_NAME},
              {"Key": "Provider", "Value": provider},
              {"Key": "ConnectionId", "Value": connection_id}],
    )
    return str(response["ARN"])


def _connection_id(provider: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:36]
    if not slug:
        raise ValueError("Connection name must contain letters or numbers.")
    return f"{provider.lower()}-{slug}"


def _build_aws_base_session(metadata: dict[str, Any], secret: dict[str, Any] | None = None):
    secret = secret or {}
    mode = metadata.get("credential_mode", "instance_role")
    region = metadata.get("region") or os.getenv("AWS_REGION", "ap-south-1")
    if mode == "access_keys":
        access_key = str(secret.get("access_key_id", "")).strip()
        secret_key = str(secret.get("secret_access_key", ""))
        if not access_key or not secret_key:
            raise ValueError("AWS access key credentials are incomplete.")
        return boto3.Session(aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                             region_name=region)
    return boto3.Session(region_name=region)


def _assumed_aws_session(metadata: dict[str, Any], secret: dict[str, Any] | None = None,
                         purpose: str = "inventory"):
    secret = secret or {}
    base = _build_aws_base_session(metadata, secret)
    role_key = "terraform_role_arn" if purpose == "terraform" else "inventory_role_arn"
    role_arn = str(metadata.get(role_key) or "").strip()
    if not role_arn:
        return base
    sts = base.client("sts", region_name=metadata.get("region") or "ap-south-1")
    request = {"RoleArn": role_arn, "RoleSessionName": f"IIPP-{purpose}-{uuid.uuid4().hex[:8]}",
               "DurationSeconds": 3600}
    external_id = str(secret.get("external_id", "")).strip()
    if external_id:
        if not re.fullmatch(r"[\w+=,.@:/-]{2,1224}", external_id):
            raise ValueError("External ID contains unsupported characters. Use only letters, numbers and _+=,.@:/-.")
        request["ExternalId"] = external_id
    credentials = sts.assume_role(**request)["Credentials"]
    return boto3.Session(aws_access_key_id=credentials["AccessKeyId"],
                         aws_secret_access_key=credentials["SecretAccessKey"],
                         aws_session_token=credentials["SessionToken"],
                         region_name=metadata.get("region") or "ap-south-1")


def test_provider_credentials(provider: str, metadata: dict[str, Any],
                              secret_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Test credentials and query real account inventory. No configuration is saved."""
    provider_raw = provider.strip().lower()
    provider = {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}.get(provider_raw, provider)
    secret_payload = secret_payload or {}
    if provider not in PROVIDERS:
        raise ValueError("Provider must be AWS, Azure or GCP.")
    try:
        if provider == "AWS":
            session = _assumed_aws_session(metadata, secret_payload, "inventory")
            region = str(metadata.get("region") or "ap-south-1")
            identity = session.client("sts", region_name=region).get_caller_identity()
            ec2 = session.client("ec2", region_name=region,
                                 config=Config(connect_timeout=5, read_timeout=15,
                                               retries={"max_attempts": 2, "mode": "standard"}))
            inventory_role = str(metadata.get("inventory_role_arn") or "").strip()
            terraform_role = str(metadata.get("terraform_role_arn") or "").strip()
            if inventory_role and inventory_role.split(":")[4] != identity.get("Account"):
                raise ValueError("The live inventory identity account does not match the configured Inventory Read Role account ID.")
            if terraform_role and terraform_role.split(":")[4] != identity.get("Account"):
                raise ValueError("The live inventory account ID does not match the configured Terraform Execution Role account ID.")
            reservations = ec2.describe_instances(MaxResults=5).get("Reservations", [])
            resource_rows = []
            for reservation in reservations:
                for instance in reservation.get("Instances", []):
                    tags = {x.get("Key"): x.get("Value") for x in instance.get("Tags", [])}
                    resource_rows.append({
                        "name": tags.get("Name", "(untagged)"),
                        "instance_id": instance.get("InstanceId"),
                        "type": instance.get("InstanceType"),
                        "state": instance.get("State", {}).get("Name"),
                        "availability_zone": instance.get("Placement", {}).get("AvailabilityZone"),
                        "private_ip": instance.get("PrivateIpAddress"),
                    })
            instance_count = len(resource_rows)
            return {"ok": True, "provider": provider, "account_id": identity.get("Account"),
                    "identity": identity.get("Arn"), "region": region,
                    "sampled_instances": instance_count, "resources": resource_rows,
                    "inventory_source": "AWS STS GetCallerIdentity + EC2 DescribeInstances",
                    "checked_at": _now()}

        if provider == "Azure":
            try:
                from azure.identity import ClientSecretCredential
                from azure.mgmt.resource import ResourceManagementClient
                from azure.mgmt.resource.subscriptions import SubscriptionClient
            except ImportError as exc:
                raise RuntimeError("Azure SDK packages are missing. Install the requirements in this release and rebuild the container.") from exc
            tenant = str(metadata.get("tenant_id", "")).strip()
            client_id = str(metadata.get("client_id", "")).strip()
            subscription = str(metadata.get("subscription_id", "")).strip()
            client_secret = str(secret_payload.get("client_secret", ""))
            if not all((tenant, client_id, subscription, client_secret)):
                raise ValueError("Azure Tenant ID, Client ID, Client Secret and Subscription ID are required.")
            credential = ClientSecretCredential(tenant_id=tenant, client_id=client_id, client_secret=client_secret)
            subscription_info = SubscriptionClient(credential).subscriptions.get(subscription)
            resources = ResourceManagementClient(credential, subscription).resources.list()
            sample = []
            for item in resources:
                sample.append({"name": item.name, "type": item.type, "location": item.location, "id": item.id})
                if len(sample) >= 25:
                    break
            return {"ok": True, "provider": provider, "subscription_id": subscription,
                    "subscription_name": getattr(subscription_info, "display_name", None),
                    "subscription_state": str(getattr(subscription_info, "state", "Unknown")),
                    "sampled_resources": len(sample), "resources": sample,
                    "inventory_source": "Azure Resource Manager ResourceManagementClient",
                    "checked_at": _now()}

        try:
            from google.cloud import compute_v1
            from google.oauth2 import service_account
        except ImportError as exc:
            raise RuntimeError("Google Cloud SDK packages are missing. Install the requirements in this release and rebuild the container.") from exc
        project_id = str(metadata.get("project_id", "")).strip()
        key_info = secret_payload.get("service_account_json")
        if isinstance(key_info, str):
            key_info = json.loads(key_info)
        if not project_id or not isinstance(key_info, dict):
            raise ValueError("GCP Project ID and a complete service-account JSON key file are required.")
        if metadata.get("service_account_email") and key_info.get("client_email") != metadata.get("service_account_email"):
            raise ValueError("The service-account email does not match the uploaded JSON key file.")
        credentials = service_account.Credentials.from_service_account_info(
            key_info, scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"])
        client = compute_v1.InstancesClient(credentials=credentials)
        sample = []
        for zone, scoped_list in client.aggregated_list(project=project_id, max_results=25):
            for instance in scoped_list.instances:
                sample.append({"name": instance.name, "id": str(instance.id),
                               "zone": instance.zone.rsplit("/", 1)[-1] if instance.zone else zone,
                               "machine_type": instance.machine_type.rsplit("/", 1)[-1] if instance.machine_type else None,
                               "status": instance.status})
                if len(sample) >= 25:
                    break
            if len(sample) >= 25:
                break
        return {"ok": True, "provider": provider, "project_id": project_id,
                "service_account_email": key_info.get("client_email"),
                "sampled_resources": len(sample), "resources": sample,
                "inventory_source": "Google Compute Engine aggregatedList API",
                "checked_at": _now()}
    except Exception as exc:
        # Do not echo any credential material into the UI or logs.
        message = str(exc)
        for value in secret_payload.values():
            if isinstance(value, str) and value:
                message = message.replace(value, "[REDACTED]")
        return {"ok": False, "provider": provider,
                "error": f"{type(exc).__name__}: {message[:700]}", "checked_at": _now()}


def save_provider_connection(provider: str, name: str, metadata: dict[str, Any],
                              secret_payload: dict[str, Any] | None = None,
                              test_result: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_raw = provider.strip().lower()
    provider = {"aws": "AWS", "azure": "Azure", "gcp": "GCP"}.get(provider_raw, provider)
    if provider not in PROVIDERS:
        raise ValueError("Provider must be AWS, Azure or GCP.")
    name = name.strip()
    if not name or len(name) > 60:
        raise ValueError("Connection name is required and must be 60 characters or fewer.")
    connection_id = _connection_id(provider, name)
    current = _read_all()
    existing = next((x for x in current.get("connections", [])
                     if isinstance(x, dict) and x.get("connection_id") == connection_id), {})
    secret_payload = secret_payload or {}
    secret_payload = {k: v for k, v in secret_payload.items() if v not in (None, "", {}, [])}
    secret_arn = existing.get("secret_arn")
    if secret_payload:
        secret_arn = _put_secret(provider, connection_id, secret_payload, secret_arn)
    if provider in {"Azure", "GCP"} and not secret_arn:
        raise ValueError(f"{provider} credentials must be stored in AWS Secrets Manager before saving the connection.")
    if provider == "AWS" and metadata.get("credential_mode") == "access_keys" and not secret_arn:
        raise ValueError("AWS access key credentials must be stored in AWS Secrets Manager.")
    record = {
        "connection_id": connection_id, "provider": provider, "name": name,
        "metadata": {k: v for k, v in metadata.items() if k not in {"client_secret", "private_key", "access_key_id", "secret_access_key", "external_id", "service_account_json"}},
        "secret_arn": secret_arn,
        "connection_status": "CONNECTED" if (test_result or {}).get("ok") else "SAVED_NOT_TESTED",
        "last_tested_at": (test_result or {}).get("checked_at"),
        "account_id": (test_result or {}).get("account_id"),
        "subscription_id": (test_result or {}).get("subscription_id"),
        "project_id": (test_result or {}).get("project_id"),
        "inventory_source": (test_result or {}).get("inventory_source"),
        "updated_at": _now(),
    }
    rows = [x for x in current.get("connections", [])
            if not (isinstance(x, dict) and x.get("connection_id") == connection_id)]
    rows.append(record)
    current["connections"] = rows
    _write_all(current)
    return {k: v for k, v in record.items() if k != "secret_arn"} | {"secret_configured": bool(secret_arn)}


def get_saved_secret(connection_id: str) -> dict[str, Any]:
    connection = get_connection(connection_id)
    if not connection:
        raise ValueError("Provider connection was not found.")
    return _get_secret(str(connection.get("secret_arn") or ""))


def get_aws_session(connection_id: str | None = None, purpose: str = "inventory"):
    """Return a boto3 session for an AWS connection; no config means EC2 role/default chain."""
    connection = get_connection(connection_id) if connection_id else None
    if not connection:
        return boto3.Session(region_name=os.getenv("AWS_REGION", "ap-south-1"))
    if connection.get("provider") != "AWS":
        raise ValueError("Selected connection is not an AWS connection.")
    secret = _get_secret(str(connection.get("secret_arn") or ""))
    return _assumed_aws_session(connection.get("metadata") or {}, secret, purpose)


def get_aws_terraform_runtime(connection_id: str | None) -> dict[str, Any]:
    """Return ephemeral source credentials and assume-role config for Terraform.

    S3 backend authentication stays on the controller/source identity. The AWS
    provider in Terraform assumes the selected target execution role.
    """
    connection = get_connection(connection_id) if connection_id else None
    if not connection:
        return {"connection_id": None, "target_role_arn": "", "external_id": "", "source_env": {}}
    if connection.get("provider") != "AWS":
        raise ValueError("Terraform's current stack supports AWS only; choose an AWS connection.")
    metadata = connection.get("metadata") or {}
    secret = _get_secret(str(connection.get("secret_arn") or ""))
    # Terraform backend state must always use the EC2 controller role, not
    # credentials belonging to a target account. The AWS provider then assumes
    # the target Terraform execution role from that controller identity.
    source_env: dict[str, str] = {}
    inventory_role_arn = str(metadata.get("inventory_role_arn") or "")
    target_role_arn = str(metadata.get("terraform_role_arn") or "")
    if not inventory_role_arn:
        raise ValueError("This AWS connection has no Inventory Read Role ARN. Configure the target-account inventory role before provisioning.")
    if not target_role_arn:
        raise ValueError("This AWS connection has no Terraform Execution Role ARN. Configure the target-account role before preparing infrastructure.")
    if connection.get("connection_status") != "CONNECTED" or not connection.get("account_id"):
        raise ValueError("This AWS connection has not passed a live identity/inventory test. Test and save it before provisioning.")
    if inventory_role_arn.split(":")[4] != str(connection.get("account_id")) or target_role_arn.split(":")[4] != str(connection.get("account_id")):
        raise ValueError("Inventory and Terraform execution roles must belong to the same account that passed the live identity test.")
    return {"connection_id": connection_id,
            "connection_name": connection.get("name"),
            "target_role_arn": target_role_arn,
            "external_id": str(secret.get("external_id") or ""),
            "source_env": source_env,
            "account_id": connection.get("account_id")}


def configured_inventory(connection_id: str) -> dict[str, Any]:
    connection = get_connection(connection_id)
    if not connection:
        return {"ok": False, "error": "Provider connection not found."}
    secret = _get_secret(str(connection.get("secret_arn") or ""))
    result = test_provider_credentials(connection["provider"], connection.get("metadata") or {}, secret)
    # Every explicit inventory refresh updates connection health. A previously
    # connected account must not remain marked CONNECTED after its latest live
    # identity/inventory check fails; the Agent and Terraform paths fail closed.
    current = _read_all()
    for row in current.get("connections", []):
        if isinstance(row, dict) and row.get("connection_id") == connection_id:
            row["connection_status"] = "CONNECTED" if result.get("ok") else "ERROR"
            row["last_tested_at"] = result.get("checked_at")
            row["last_test_error"] = None if result.get("ok") else str(result.get("error") or "Live inventory validation failed.")[:700]
            for field in ("account_id", "subscription_id", "project_id", "inventory_source"):
                if result.get("ok"):
                    row[field] = result.get(field)
                else:
                    row.pop(field, None)
            break
    _write_all(current)
    return result


def configured_aws_metrics(connection_id: str, instance_id: str, hours: int = 1) -> dict[str, Any]:
    """Fetch fresh CloudWatch telemetry for an EC2 instance in a verified AWS connection."""
    if not re.fullmatch(r"i-[0-9a-fA-F]{8,17}", str(instance_id or "")):
        raise ValueError("Provide a valid EC2 instance ID.")
    connection = get_connection(connection_id)
    if not connection or connection.get("provider") != "AWS":
        raise ValueError("Select a saved AWS connection.")
    if connection.get("connection_status") != "CONNECTED" or not connection.get("account_id"):
        raise ValueError("The AWS connection must pass Test connection before telemetry can be queried.")
    hours = max(1, min(int(hours), 24))
    metadata = connection.get("metadata") or {}
    secret = _get_secret(str(connection.get("secret_arn") or ""))
    session = _assumed_aws_session(metadata, secret, "inventory")
    region = str(metadata.get("region") or "ap-south-1")
    identity = session.client("sts", region_name=region).get_caller_identity()
    if str(identity.get("Account")) != str(connection.get("account_id")):
        raise ValueError("The active AWS identity no longer matches the saved connection account ID.")
    ec2 = session.client("ec2", region_name=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instances = [instance for reservation in response.get("Reservations", [])
                 for instance in reservation.get("Instances", [])]
    if not instances:
        raise ValueError(f"Instance {instance_id} was not found in account {connection.get('account_id')} region {region}.")

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    cw = session.client("cloudwatch", region_name=region,
                        config=Config(connect_timeout=5, read_timeout=15,
                                      retries={"max_attempts": 2, "mode": "standard"}))
    ec2_dims = [{"Name": "InstanceId", "Value": instance_id}]
    definitions = [
        ("CPU", "AWS/EC2", "CPUUtilization", ec2_dims, ["Average", "Maximum"], "Percent"),
        ("Memory", "CWAgent", "mem_used_percent", None, ["Average", "Maximum"], "Percent"),
        ("Disk", "CWAgent", "disk_used_percent", None, ["Average", "Maximum"], "Percent"),
        ("Network In", "AWS/EC2", "NetworkIn", ec2_dims, ["Sum"], "Bytes per 300-second period"),
        ("Network Out", "AWS/EC2", "NetworkOut", ec2_dims, ["Sum"], "Bytes per 300-second period"),
    ]
    metrics: dict[str, Any] = {}
    for label, namespace, metric_name, dimensions, stats, unit in definitions:
        try:
            actual_dimensions = dimensions
            if actual_dimensions is None:
                discovered = cw.list_metrics(Namespace="CWAgent", MetricName=metric_name,
                                             Dimensions=ec2_dims).get("Metrics", [])
                candidates = [item.get("Dimensions", []) for item in discovered
                              if any(d.get("Name") == "InstanceId" and d.get("Value") == instance_id
                                     for d in item.get("Dimensions", []))]
                candidates.sort(key=lambda dims: (len(dims), str(dims)))
                actual_dimensions = candidates[0] if candidates else ec2_dims
            period_seconds = 300 if namespace == "AWS/EC2" else 60
            result = cw.get_metric_statistics(Namespace=namespace, MetricName=metric_name,
                                              Dimensions=actual_dimensions, StartTime=start,
                                              EndTime=now, Period=period_seconds, Statistics=stats)
            points = sorted(result.get("Datapoints", []), key=lambda item: item["Timestamp"])
            stat_name = "Sum" if "Sum" in stats else "Average"
            cooked = [{"timestamp": point["Timestamp"].astimezone(timezone.utc).isoformat(),
                       "value": float(point[stat_name]), "average": float(point[stat_name]),
                       "maximum": float(point.get("Maximum", point[stat_name])),
                       "statistic": stat_name, "unit": unit}
                      for point in points if point.get(stat_name) is not None]
            metrics[label] = {"available": bool(cooked), "namespace": namespace,
                              "metric_name": metric_name, "dimensions": actual_dimensions,
                              "period_seconds": period_seconds, "statistic": stat_name, "unit": unit,
                              "points": cooked, "latest": cooked[-1]["value"] if cooked else None,
                              "latest_timestamp": cooked[-1]["timestamp"] if cooked else None,
                              "error": None}
        except Exception as exc:
            metrics[label] = {"available": False, "namespace": namespace,
                              "metric_name": metric_name, "dimensions": dimensions or ec2_dims,
                              "period_seconds": 300 if namespace == "AWS/EC2" else 60, "statistic": "Sum" if "Sum" in stats else "Average",
                              "unit": unit, "points": [], "latest": None,
                              "latest_timestamp": None,
                              "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    instance = instances[0]
    tags = {tag.get("Key"): tag.get("Value") for tag in instance.get("Tags", [])}
    return {"ok": True, "provider": "AWS", "connection_id": connection_id,
            "connection_name": connection.get("name"), "account_id": connection.get("account_id"),
            "identity": identity.get("Arn"), "region": region, "instance_id": instance_id,
            "instance_name": tags.get("Name", "(untagged)"),
            "instance_type": instance.get("InstanceType"),
            "instance_state": instance.get("State", {}).get("Name"),
            "generated_at": now.isoformat(), "metrics": metrics,
            "source": "Verified AWS AssumeRole + EC2 DescribeInstances + CloudWatch metrics APIs"}
