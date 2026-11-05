"""Read-only, credential-free deployment history across both persisted workflows."""
from __future__ import annotations

import json
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
                requirements = record.get("requirements") or {}
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
                    "kind": str(requirements.get("kind") or "VM"),
                    "reviewer": str(record.get("reviewer") or "Unavailable"),
                    "state_history": [{"Status": STATUS_LABELS.get(item.get("status"), str(item.get("status") or "Unknown")),
                                       "Time (UTC)": str(item.get("timestamp") or "Unavailable")}
                                      for item in history if isinstance(item, dict)],
                })
            except (OSError, ValueError, TypeError):
                unreadable += 1
    return sorted(rows, key=lambda row: _timestamp(row["created_at"]), reverse=True), unreadable
