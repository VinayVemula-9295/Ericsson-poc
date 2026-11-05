#!/usr/bin/env python3
"""Repair only Demo Terraform-input discovery reads; dry-run by default.

Use a non-root Demo administrator profile, or a management administrator profile
authorized to assume Demo's existing OrganizationAccountAccessRole. Never creates
roles, keys, accounts, resources, or enables Terraform execution.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

ACCOUNT = "333347372766"
MANAGEMENT = "704298683641"
ROLE = "IIPPInventoryReadRole"
POLICY = "IIPPPlanInputReads"


def discovery_policy(region="ap-south-1"):
    import re
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
        raise ValueError("Use a valid AWS region")
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "ListClusterAndWorkerRoles", "Effect": "Allow", "Action": "iam:ListRoles", "Resource": "*"},
        {"Sid": "ListRegionalVersionsAndKeys", "Effect": "Allow",
         "Action": ["eks:DescribeClusterVersions", "kms:ListKeys"], "Resource": "*",
         "Condition": {"StringEquals": {"aws:RequestedRegion": region}}},
        {"Sid": "DescribeTargetRegionalKeys", "Effect": "Allow", "Action": "kms:DescribeKey",
         "Resource": f"arn:aws:kms:{region}:{ACCOUNT}:key/*"},
    ]}


def target_session(profile, region, session_factory=None):
    if not profile:
        raise ValueError("An explicit non-root administrator --profile is required")
    if session_factory is None:
        import boto3
        session_factory = boto3.Session
    from botocore.config import Config
    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 1})
    session = session_factory(profile_name=profile, region_name=region)
    identity = session.client("sts", config=config).get_caller_identity()
    if identity["Arn"].endswith(":root"):
        raise ValueError("Use a non-root administrator session")
    if identity["Account"] == MANAGEMENT:
        credentials = session.client("sts", config=config).assume_role(
            RoleArn=f"arn:aws:iam::{ACCOUNT}:role/OrganizationAccountAccessRole",
            RoleSessionName="IIPP-Demo-Discovery-Repair")["Credentials"]
        session = session_factory(region_name=region, aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"], aws_session_token=credentials["SessionToken"])
        identity = session.client("sts", config=config).get_caller_identity()
    if identity["Account"] != ACCOUNT or identity["Arn"].endswith(":root"):
        raise ValueError("Administrator must resolve to the existing Demo target account")
    return session


def repair(session, region, execute=False, backup_dir=None):
    policy = discovery_policy(region)
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT or identity["Arn"].endswith(":root"):
        raise ValueError("Non-root Demo account identity required before IAM access")
    iam = session.client("iam")
    role = iam.get_role(RoleName=ROLE)["Role"]
    if role["Arn"] != f"arn:aws:iam::{ACCOUNT}:role/{ROLE}":
        raise ValueError("Unexpected inventory role ARN; no writes performed")
    try:
        previous = iam.get_role_policy(RoleName=ROLE, PolicyName=POLICY)["PolicyDocument"]
    except iam.exceptions.NoSuchEntityException:
        previous = None
    # Supplement an existing policy without deleting any of its statements.
    merged = json.loads(json.dumps(previous)) if previous else {"Version": "2012-10-17", "Statement": []}
    if isinstance(merged["Statement"], dict):
        merged["Statement"] = [merged["Statement"]]
    for statement in policy["Statement"]:
        if not any({k: v for k, v in existing.items() if k != "Sid"} ==
                   {k: v for k, v in statement.items() if k != "Sid"} for existing in merged["Statement"]):
            new_statement = dict(statement)
            taken = {s.get("Sid") for s in merged["Statement"]}
            while new_statement["Sid"] in taken:
                new_statement["Sid"] += "Repair"
            merged["Statement"].append(new_statement)
    changed = merged != previous
    result = dict(account=ACCOUNT, role=ROLE, policy_name=POLICY, changed=changed, executed=False,
                  permissions_boundary_present=bool(role.get("PermissionsBoundary")), proposed_policy=merged)
    if execute and changed:
        if backup_dir is None:
            raise ValueError("A backup directory is required before any IAM write")
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=False)
        snapshot = dict(account=ACCOUNT, role=ROLE, policy_name=POLICY, previous_policy=previous,
                        created_at=datetime.now(timezone.utc).isoformat())
        (backup_dir / "previous-policy.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        iam.put_role_policy(RoleName=ROLE, PolicyName=POLICY, PolicyDocument=json.dumps(merged))
        actual = iam.get_role_policy(RoleName=ROLE, PolicyName=POLICY)["PolicyDocument"]
        if actual != merged:
            raise ValueError("Saved IAM policy does not match the repair; inspect the backup before retrying")
        result["executed"] = True
        result["backup"] = str(backup_dir / "previous-policy.json")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.execute and (not args.profile or not args.backup_dir):
        parser.error("--execute requires --profile and a new --backup-dir")
    if not args.profile:
        print(json.dumps(dict(account=ACCOUNT, role=ROLE, policy_name=POLICY,
                              proposed_policy=discovery_policy(args.region), executed=False), indent=2))
        return
    session = target_session(args.profile, args.region)
    print(json.dumps(repair(session, args.region, args.execute, args.backup_dir), indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        from botocore.exceptions import ClientError
        if isinstance(exc, ClientError):
            raise SystemExit("AWS repair blocked: " + exc.response.get("Error", {}).get("Code", "UnknownError"))
        if isinstance(exc, ValueError):
            raise SystemExit(str(exc))
        raise SystemExit("Repair blocked: " + type(exc).__name__)
