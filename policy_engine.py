"""Deterministic policy-first checks for IIPP Terraform provisioning requests.

These checks are an application guardrail. AWS IAM permissions remain the final
cloud-side authority and must enforce the same or stricter limits.
"""
from __future__ import annotations
import os
from typing import Any


def _csv_env(name: str, default: str) -> set[str]:
    raw = os.getenv(name, default)
    return {part.strip() for part in raw.split(",") if part.strip()}


def validate_aws_launch(region: str, instance_type: str) -> None:
    """Shared comparison/runner allowlist check; does not grant execution approval."""
    allowed_regions = _csv_env("IIPP_ALLOWED_AWS_REGIONS", "ap-south-1")
    allowed_families = _csv_env("IIPP_ALLOWED_AWS_INSTANCE_FAMILIES", "t3")
    if region not in allowed_regions:
        raise ValueError(f"Policy denied region {region}. Allowed regions: {', '.join(sorted(allowed_regions))}.")
    if "*" not in allowed_families and instance_type.split(".", 1)[0] not in allowed_families:
        raise ValueError(f"Policy denied instance family {instance_type.split('.', 1)[0]}. Allowed families: {', '.join(sorted(allowed_families))}.")


def evaluate_aws_provision_request(payload: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    allowed_envs = {"poc", "test"}
    connection_name = str(runtime.get("connection_name", "")).lower()
    account_environment = ("test" if "test" in connection_name else "poc"
                           if "poc" in connection_name or connection_name in {"demo-account", "demo account"} else "")
    region = str(payload.get("region", ""))
    instance_type = str(payload.get("instance_type", ""))
    family = instance_type.split(".", 1)[0]
    environment = str(payload.get("environment", "poc")).lower()
    validate_aws_launch(region, instance_type)
    if environment not in allowed_envs:
        raise ValueError("Policy denied environment. Only POC and TEST are allowed.")
    if not account_environment or environment != account_environment:
        raise ValueError("Policy denied cross-environment placement. AWS-POC can only create POC-tagged resources and AWS-TEST can only create TEST-tagged resources.")
    if not runtime.get("account_id") or not runtime.get("target_role_arn"):
        raise ValueError("Policy requires a verified target account identity and Terraform Execution Role.")
    ttl = int(payload.get("ttl_days", 0))
    if ttl < 1 or ttl > 30:
        raise ValueError("Policy denied TTL. Temporary POC resources must carry TTL_Days between 1 and 30.")
    return {
        "decision": "ALLOW_TO_PLAN",
        "policy": "IIPP-AWS-NonProduction-v1",
        "scope": {"account_id": runtime["account_id"], "region": region,
                  "instance_family": family, "environment": environment,
                  "ttl_days": ttl},
        "controls": ["verified AWS connection", "explicit Terraform execution role",
                      "region allow-list", "instance-family allow-list",
                      "POC/TEST environment tag", "TTL tag", "no public IP in reviewed stack",
                      "human approval required before apply"],
        "note": "ALLOW_TO_PLAN is not approval to apply. AWS IAM and reviewer approval remain mandatory.",
    }
