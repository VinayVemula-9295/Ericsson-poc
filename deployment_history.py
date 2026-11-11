"""Read-only, credential-free deployment history across both persisted workflows."""
from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path


STATUS_LABELS = {
    "PREPARING": "Preparing", "DRAFT": "Preparing", "VALIDATED": "Preparing",
    "AWAITING_APPROVAL": "Awaiting approval", "APPLYING": "In progress",
    "SUCCEEDED": "Completed", "FAILED": "Failed", "REJECTED": "Rejected",
    "PLAN_READY_EXECUTION_UNVALIDATED": "Plan ready · execution unvalidated",
    "REVIEWED_EXECUTION_DISABLED": "Reviewed · execution disabled",
    "DESTROY_AWAITING_APPROVAL": "Awaiting destroy approval",
    "DESTROYING": "Destroying", "DESTROYED": "Destroyed",
    "DESTROY_PLAN_FAILED": "Destroy plan failed", "DESTROY_FAILED": "Destroy failed",
}


def load_destroy_review(source, deployment_id):
    """Load only the display fields needed to resume a saved destroy review."""
    patterns = {"poc_plans": (r"poc-[0-9a-f]{32}", "record.json"),
                "terraform_deployments": (r"cp-[0-9a-f]{16}", "deployment.json")}
    if source not in patterns or not re.fullmatch(patterns[source][0], deployment_id):
        raise ValueError("Invalid deployment source or ID")
    path = Path(os.getenv("DATA_DIR", "/data")) / source / deployment_id / patterns[source][1]
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("status") != "DESTROY_AWAITING_APPROVAL":
        raise ValueError("Refresh deployment history; this review is no longer pending")
    return {key: record[key] for key in ("status", "destroy_plan_summary") if key in record}


def _timestamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0


def configuration_details(candidate, config, requirements):
    """Use saved specifications; older records may contain only minimum requirements."""
    parts = []
    for key, fallback, unit in [('vcpus', 'min_vcpus', 'vCPU'), ('memory_gib', 'min_memory_gib', 'GiB RAM')]:
        actual = candidate.get(key)
        value = actual if actual is not None else config.get(fallback) or requirements.get(fallback)
        try:
            number = float(value)
            if number <= 0 or not math.isfinite(number):
                raise ValueError('Invalid sizing')
            parts.append(f'{number:g} {unit}' + (' required' if actual is None else ''))
        except (TypeError, ValueError, OverflowError):
            parts.append(unit + ' unavailable')
    return ' · '.join(parts)


def load_history(data_dir: Path | None = None) -> tuple[list[dict], int]:
    """Read all states without invoking Terraform, provider APIs or creating files.

    Returns safe display records and the number of unreadable/malformed records.
    Existing stores remain authoritative; no duplicate history database is needed.
    """
    root = Path(data_dir) if data_dir is not None else Path(os.getenv("DATA_DIR", "/data"))
    rows, unreadable = [], 0
    for folder, filename in (("poc_plans", "record.json"), ("terraform_deployments", "deployment.json")):
        for path in (root / folder).glob("*/" + filename):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not record.get("deployment_id") or not isinstance(record.get("status"), str):
                    raise ValueError("Invalid deployment record")
                candidate = record.get("candidate") or {}
                config = record.get("requested_configuration") or {}
                from control_plane import visible_connection
                if not visible_connection(record.get('connection_id') or record.get('aws_connection_id') or config.get('aws_connection_id')):
                    continue
                requirements = record.get("requirements") or {}
                from control_plane import enabled, scope
                ownership = (scope(record.get('connection_id') or record.get('aws_connection_id') or config.get('aws_connection_id')) or {}) if enabled() else {}
                if not all(isinstance(v, dict) for v in (candidate, config, requirements)):
                    raise ValueError("Invalid deployment metadata")
                name = record.get("resource_name") or config.get("instance_name")
                if not name and folder == "poc_plans":
                    # Older Agent records stored their name only in the input file.
                    # Extract that field only; never expose other variables.
                    try:
                        values = json.loads((path.parent / "terraform.tfvars.json").read_text(encoding="utf-8"))
                        name = values.get("name") if isinstance(values, dict) else None
                    except (OSError, ValueError):
                        pass
                raw_status = record["status"]
                history = record.get("state_history") or []
                if not isinstance(history, list):
                    history = []
                rows.append({
                    'customer': ownership.get('customer', 'Unassigned'),
                    'project': ownership.get('project', 'Unassigned'),
                    "source": folder,
                    "resource_identifiers": record.get("resource_identifiers", []),
                    "key": folder + ":" + path.parent.name,
                    "deployment_id": str(record["deployment_id"]),
                    "name": str(name or record["deployment_id"]),
                    "provider": str(candidate.get("provider") or record.get("provider") or "AWS"),
                    "target": str(record.get("target_identity") or record.get("target_account_id") or config.get("account_id") or "Unavailable"),
                    "region": str(candidate.get("region") or record.get("region") or config.get("region") or "Unavailable"),
                    "status": STATUS_LABELS.get(raw_status, raw_status.replace("_", " ").title()),
                    "raw_status": raw_status,
                    "created_at": str(record.get("created_at") or "Unavailable"),
                    "updated_at": str(record.get("updated_at") or record.get("completed_at") or record.get("created_at") or "Unavailable"),
                    "completed_at": str(record.get("completed_at") or "Unavailable"),
                    "configuration": str(candidate.get("sku") or config.get("instance_type") or "Unavailable"),
                    "configuration_details": configuration_details(candidate, config, requirements),
                    "kind": str(requirements.get("kind") or "VM"),
                    "reviewer": str(record.get("reviewer") or "Unavailable"),
                    "initiated_by": str(record.get("initiated_by") or "Unavailable"),
                    "subscription": str(record.get("subscription_id") or record.get("project_id") or "Unavailable"),
                    "state_history": [{"Status": STATUS_LABELS.get(item.get("status"), str(item.get("status") or "Unknown")),
                                       "Time (UTC)": str(item.get("timestamp") or "Unavailable")}
                                      for item in history if isinstance(item, dict)],
                    "execution_events": [{"status": str(item.get('status', '')), "timestamp": str(item.get('timestamp', ''))}
                                         for item in history if isinstance(item, dict)],
                })
            except (OSError, ValueError, TypeError):
                unreadable += 1
    return sorted(rows, key=lambda row: _timestamp(row["created_at"]), reverse=True), unreadable


def load_reuse(source, deployment_id):
    patterns = {'poc_plans': (r'poc-[0-9a-f]{32}', 'record.json'),
                'terraform_deployments': (r'cp-[0-9a-f]{16}', 'deployment.json')}
    if source not in patterns or not re.fullmatch(patterns[source][0], deployment_id):
        raise ValueError('Invalid deployment source or ID')
    directory = Path(os.getenv('DATA_DIR', '/data')) / source / deployment_id
    from reuse_artifacts import load
    directory, record, _ = load(source, deployment_id)
    candidate = record.get('candidate') or {}
    config = record.get('requested_configuration') or {}
    request = record.get('requirements') or {}
    sku = candidate.get('sku') or config.get('instance_type')
    region = candidate.get('region') or config.get('region') or record.get('region')
    if not sku or not region:
        raise ValueError('Saved sizing or region is unavailable; create a new request.')
    values = {}
    try:
        values = json.loads((directory / 'terraform.tfvars.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        pass
    if not isinstance(values, dict):
        values = {}
    allowed = {'name', 'image_id', 'subnet_id', 'second_subnet_id', 'security_group_id',
               'resource_group', 'network_id', 'zone', 'kubernetes_version'}
    safe = {k: v for k, v in values.items() if k in allowed and isinstance(v, str)}
    for old, new in [('instance_name', 'name'), ('ami_id', 'image_id'), ('subnet_id', 'subnet_id'), ('security_group_id', 'security_group_id')]:
        if config.get(old):
            safe[new] = config[old]
    provider = candidate.get('provider') or record.get('provider') or 'AWS'
    cpu = request.get('min_vcpus') or candidate.get('vcpus') or config.get('min_vcpus')
    ram = request.get('min_memory_gib') or candidate.get('memory_gib') or config.get('min_memory_gib')
    prompt = f"Create {candidate.get('os', config.get('os', 'Linux'))} {request.get('kind', 'VM')} on {provider} using {sku} in {region}."
    if request.get('worker_count'):
        prompt += f" Use {request['worker_count']} worker nodes."
    if cpu and ram:
        prompt += f' Require {cpu} vCPU and {ram} GiB RAM.'
    return {'prompt': prompt, 'inputs': safe, 'provider': provider, 'region': region, 'requested_sku': sku,
            'connection_id': record.get('connection_id') or record.get('aws_connection_id') or config.get('aws_connection_id'),
            'source_deployment': deployment_id, 'source': source,
            'terraform_available': (directory / 'main.tf.json').exists() or (directory / 'main.tf').exists(),
            'state_reference': record.get('state_key'), 'status': record.get('status')}


def load_reusable_history():
    """Backend listing exposes only authorized, successful, integrity-verified sources."""
    from reuse_artifacts import availability
    rows, unreadable = load_history()
    return [row for row in rows if row['raw_status'] == 'SUCCEEDED' and availability(row) is None], unreadable
