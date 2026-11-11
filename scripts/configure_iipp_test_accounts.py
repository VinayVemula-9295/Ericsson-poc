#!/usr/bin/env python3
"""Configure IIPP inventory/execution roles and monthly budget alerts in two AWS member accounts.

Dry-run by default. Run from the AWS Organizations management account or a delegated
administrator permitted to assume OrganizationAccountAccessRole in the target accounts.
The generated external IDs are written to a mode-0600 local file, never printed.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any
import boto3
from botocore.exceptions import ClientError

ROLE_INVENTORY = "IIPPInventoryReadRole"
ROLE_EXECUTION = "IIPPTerraformExecutionRole"
APP = "Innominds-IIPP"


def source_role_from_identity() -> str:
    arn = boto3.client("sts").get_caller_identity()["Arn"]
    # Convert an assumed-role session ARN to its IAM role ARN.
    if ":assumed-role/" in arn:
        prefix, session = arn.split(":assumed-role/", 1)
        role_path = session.rsplit("/", 1)[0]
        return prefix.replace(":sts:", ":iam:") + ":role/" + role_path
    if ":user/" in arn or ":root" in arn:
        raise RuntimeError("Use an IAM role as the IIPP controller principal, not an IAM user or root identity.")
    return arn


def inventory_policy(account: str | None = None) -> dict[str, Any]:
    policy = {"Version": "2012-10-17", "Statement": [{
        "Sid": "IIPPReadInventoryTelemetryQuotasAndCosts", "Effect": "Allow",
        "Action": [
            "sts:GetCallerIdentity",
            "ec2:DescribeInstances", "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings",
            "ec2:DescribeImages", "ec2:DescribeSubnets", "ec2:DescribeVpcs",
            "ec2:DescribeSecurityGroups", "ec2:DescribeVolumes", "ec2:DescribeAvailabilityZones",
            "ec2:DescribeRegions", "ec2:DescribeNetworkInterfaces", "ec2:DescribeAddresses",
            "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics",
            "servicequotas:GetServiceQuota", "servicequotas:ListServiceQuotas",
            "eks:DescribeClusterVersions", "iam:ListRoles", "kms:ListKeys", "kms:DescribeKey",
            "ce:GetCostAndUsage", "ce:GetCostForecast", "ce:GetDimensionValues"
        ], "Resource": "*"
    }]}
    if account is not None:
        if not re.fullmatch(r"\d{12}", account):
            raise ValueError("Inventory account must be a 12-digit AWS account ID")
        policy["Statement"].append({"Sid": "ValidateAccountPlanRoles", "Effect": "Allow",
                                    "Action": "iam:GetRole", "Resource": f"arn:aws:iam::{account}:role/*"})
    return policy


def execution_policy(region: str, environment: str) -> dict[str, Any]:
    # POC execution is intentionally limited to small T3 instances in one region.
    # Extend approved families only after the policy engine and spend controls are validated.
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "IIPPReadOnlyDiscovery", "Effect": "Allow", "Action": [
            "ec2:DescribeInstances", "ec2:DescribeInstanceTypes", "ec2:DescribeInstanceTypeOfferings",
            "ec2:DescribeImages", "ec2:DescribeSubnets", "ec2:DescribeVpcs",
            "ec2:DescribeSecurityGroups", "ec2:DescribeVolumes", "ec2:DescribeAvailabilityZones",
            "ec2:DescribeRegions", "ec2:DescribeNetworkInterfaces"
        ], "Resource": "*"},
        {"Sid": "IIPPLaunchSmallTaggedPOCInstances", "Effect": "Allow",
         "Action": "ec2:RunInstances", "Resource": "*", "Condition": {
             "StringEquals": {"aws:RequestedRegion": region,
                              "aws:RequestTag/Project": APP,
                              "aws:RequestTag/Environment": environment},
             "StringLike": {"ec2:InstanceType": "t3.*"}
         }},
        {"Sid": "IIPPTagOnlyAtRunInstances", "Effect": "Allow",
         "Action": "ec2:CreateTags", "Resource": "arn:aws:ec2:*:*:instance/*", "Condition": {
             "StringEquals": {"ec2:CreateAction": "RunInstances", "aws:RequestTag/Project": APP,
                              "aws:RequestTag/Environment": environment}
         }},
        {"Sid": "IIPPDestroyOnlyTaggedProjectInstances", "Effect": "Allow",
         "Action": "ec2:TerminateInstances", "Resource": "arn:aws:ec2:*:*:instance/*", "Condition": {
             "StringEquals": {"aws:RequestedRegion": region, "aws:ResourceTag/Project": APP,
                              "aws:ResourceTag/Environment": environment}
         }}
    ]}


def trust_policy(source_role_arn: str, external_id: str) -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"AWS": source_role_arn},
        "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"sts:ExternalId": external_id}}
    }]}


def ensure_role(iam, role_name: str, trust: dict[str, Any], policy: dict[str, Any], execute: bool) -> None:
    if not execute:
        print(f"DRY RUN: create/update role {role_name} with trust and inline policy")
        return
    try:
        iam.get_role(RoleName=role_name)
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=json.dumps(trust))
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
                        Description=f"{APP} controlled {role_name}", MaxSessionDuration=3600,
                        Tags=[{"Key": "Application", "Value": APP},
                              {"Key": "ManagedBy", "Value": APP}])
    iam.put_role_policy(RoleName=role_name, PolicyName=f"{APP}-Permissions", PolicyDocument=json.dumps(policy))


def create_budget(budgets, account_id: str, name: str, amount: float, email: str, execute: bool) -> None:
    notifications = []
    for threshold in (50.0, 80.0, 100.0):
        notifications.append({"Notification": {
            "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN",
            "Threshold": threshold, "ThresholdType": "PERCENTAGE"},
            "Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}]})
    request = {"AccountId": account_id,
               "Budget": {"BudgetName": f"{APP}-{name}-Monthly-Cost", "BudgetLimit": {"Amount": str(amount), "Unit": "USD"},
                          "TimeUnit": "MONTHLY", "BudgetType": "COST"},
               "NotificationsWithSubscribers": notifications}
    if not execute:
        print(f"DRY RUN: create {name} monthly USD {amount:g} budget for {account_id}; alerts 50/80/100% to {email}")
        return
    try:
        budgets.create_budget(**request)
        print(f"Created monthly budget for {name} ({account_id})")
    except budgets.exceptions.DuplicateRecordException:
        print(f"Budget already exists for {name} ({account_id}); left unchanged. Review/update it in AWS Budgets.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poc-account-id", required=True)
    parser.add_argument("--test-account-id", required=True)
    parser.add_argument("--budget-email", default=os.getenv("IIPP_BUDGET_EMAIL"))
    parser.add_argument("--source-role-arn", default=os.getenv("IIPP_SOURCE_ROLE_ARN"))
    parser.add_argument("--region", default=os.getenv("IIPP_ALLOWED_REGION", "ap-south-1"))
    parser.add_argument("--poc-budget-usd", type=float, default=float(os.getenv("IIPP_POC_BUDGET_USD", "75")))
    parser.add_argument("--test-budget-usd", type=float, default=float(os.getenv("IIPP_TEST_BUDGET_USD", "35")))
    parser.add_argument("--execute", action="store_true", help="Apply IAM role and budget changes. Default is dry-run.")
    args = parser.parse_args()
    for value, label in ((args.poc_account_id, "POC"), (args.test_account_id, "TEST")):
        if not value.isdigit() or len(value) != 12:
            parser.error(f"--{label.lower()}-account-id must be a 12-digit AWS account ID")
    if args.poc_account_id == args.test_account_id:
        parser.error("POC and TEST account IDs must be different")
    if not args.budget_email:
        parser.error("Provide --budget-email or IIPP_BUDGET_EMAIL")
    if not args.source_role_arn:
        args.source_role_arn = source_role_from_identity()
    if not args.source_role_arn.startswith("arn:aws:iam::"):
        parser.error("--source-role-arn must be an IAM role ARN")
    if not args.execute:
        print("DRY RUN MODE — no AWS changes will be made.")
    identity = boto3.client("sts").get_caller_identity()
    print(f"Caller account: {identity['Account']} | source role: {args.source_role_arn}")
    targets = [
        ("POC", args.poc_account_id, args.poc_budget_usd),
        ("TEST", args.test_account_id, args.test_budget_usd),
    ]
    out = Path.home() / ".iipp-target-account-connections.json"
    try:
        existing = json.loads(out.read_text())
    except (OSError, json.JSONDecodeError):
        existing = {}
    credentials: dict[str, Any] = existing if isinstance(existing, dict) else {}

    for label, account_id, budget_amount in targets:
        if budget_amount <= 0:
            parser.error(f"{label} budget must be greater than zero")
        key = label.lower()
        external_id = credentials.get(key, {}).get("external_id") or secrets.token_urlsafe(36)
        trust = trust_policy(args.source_role_arn, external_id)
        if args.execute:
            role_creds = boto3.client("sts").assume_role(
                RoleArn=f"arn:aws:iam::{account_id}:role/OrganizationAccountAccessRole",
                RoleSessionName=f"IIPP-bootstrap-{label}")
            session = boto3.Session(aws_access_key_id=role_creds["Credentials"]["AccessKeyId"],
                                    aws_secret_access_key=role_creds["Credentials"]["SecretAccessKey"],
                                    aws_session_token=role_creds["Credentials"]["SessionToken"], region_name=args.region)
            iam = session.client("iam")
            ensure_role(iam, ROLE_INVENTORY, trust, inventory_policy(), True)
            ensure_role(iam, ROLE_EXECUTION, trust, execution_policy(args.region, key), True)
        else:
            print(f"DRY RUN: would configure trust for {args.source_role_arn} with a unique external ID")
            print(f"DRY RUN: inventory policy allows read-only EC2/CloudWatch/quotas/cost access")
            print(f"DRY RUN: execution policy allows tagged t3.* {key}-environment instances in {args.region} only")
        if args.execute:
            credentials[key] = {"account_id": account_id,
                                "inventory_role_arn": f"arn:aws:iam::{account_id}:role/{ROLE_INVENTORY}",
                                "terraform_role_arn": f"arn:aws:iam::{account_id}:role/{ROLE_EXECUTION}",
                                "external_id": external_id}
            out.write_text(json.dumps(credentials, indent=2) + "\n")
            os.chmod(out, 0o600)
        else:
            credentials[key] = {"account_id": account_id,
                                "inventory_role_arn": f"arn:aws:iam::{account_id}:role/{ROLE_INVENTORY}",
                                "terraform_role_arn": f"arn:aws:iam::{account_id}:role/{ROLE_EXECUTION}",
                                "external_id": external_id}
        if args.execute:
            budgets = boto3.client("budgets", region_name="us-east-1")
            create_budget(budgets, account_id, label, budget_amount, args.budget_email, True)
        else:
            create_budget(None, account_id, label, budget_amount, args.budget_email, False)

    if args.execute:
        policy = {"Version": "2012-10-17", "Statement": [{
            "Sid": "AllowIIPPAssumeOnlyConfiguredNonProductionRoles", "Effect": "Allow",
            "Action": "sts:AssumeRole", "Resource": [
                f"arn:aws:iam::{args.poc_account_id}:role/{ROLE_INVENTORY}",
                f"arn:aws:iam::{args.poc_account_id}:role/{ROLE_EXECUTION}",
                f"arn:aws:iam::{args.test_account_id}:role/{ROLE_INVENTORY}",
                f"arn:aws:iam::{args.test_account_id}:role/{ROLE_EXECUTION}"]}]}
        policy_path = Path.home() / ".iipp-source-assume-roles-policy.json"
        policy_path.write_text(json.dumps(policy, indent=2) + "\n")
        os.chmod(policy_path, 0o600)
        print(f"Saved connection role details and External IDs to {out} (mode 0600).")
        print(f"Attach the generated source assume-role policy to {args.source_role_arn}: {policy_path}")
        print("Next, configure two AWS connections in IIPP with each account's role ARNs and External ID.")
    else:
        print("Review the planned accounts, role permissions, allowed region/instance family and budget amounts.")
        print("Re-run with --execute from an Organizations management-account administrator to apply.")
    print("Budget alerts are notifications, not hard spend caps. Use service quotas and IIPP policy checks for prevention.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClientError as exc:
        err = exc.response.get("Error", {})
        print(f"AWS ERROR: {err.get('Code')}: {err.get('Message')}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
