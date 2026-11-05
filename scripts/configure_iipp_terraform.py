#!/usr/bin/env python3
"""Prepare EC2/EKS IAM for existing IIPP accounts. Offline by default; --execute uses admin credentials.

Does not alter connection trust/External IDs, budgets, networks, or execution flags.
Requires existing inventory/execution roles. EKS names must start with iipp-.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT = "Innominds-IIPP"
CLUSTER_ROLE = "IIPPEKSClusterRole"
NODE_ROLE = "IIPPEKSNodeRole"


def document(statements):
    return {"Version": "2012-10-17", "Statement": statements}


def allow(sid, actions, resources, conditions=None):
    result = dict(Sid=sid, Effect="Allow", Action=actions, Resource=resources)
    if conditions:
        result["Condition"] = conditions
    return result


def execution_policy(account, region, environment, key_arn, instance_role=None):
    ec2 = f"arn:aws:ec2:{region}:{account}:"
    eks = f"arn:aws:eks:{region}:{account}:"
    regional = {"StringEquals": {"aws:RequestedRegion": region}}
    tags = {"aws:RequestTag/Project": PROJECT, "aws:RequestTag/Environment": environment}
    statements = [
        allow("Discovery", ["ec2:Describe*", "eks:DescribeClusterVersions"], "*", regional),
        # InstanceType and request tags apply to instances, not referenced images/subnets.
        allow("LaunchInstances", "ec2:RunInstances", ec2 + "instance/*",
              {"StringEquals": {**tags, "aws:RequestedRegion": region},
               "StringLike": {"ec2:InstanceType": "t3.*"}}),
        allow("LaunchDependencies", "ec2:RunInstances", [
            f"arn:aws:ec2:{region}::image/*", ec2 + "subnet/*", ec2 + "security-group/*",
            ec2 + "network-interface/*", ec2 + "volume/*"], regional),
        allow("LaunchTags", "ec2:CreateTags", [ec2 + "instance/*", ec2 + "volume/*", ec2 + "network-interface/*"],
              {"StringEquals": {"ec2:CreateAction": "RunInstances", "aws:RequestedRegion": region}}),
        allow("ManagedInstanceLifecycle", ["ec2:TerminateInstances", "ec2:ModifyInstanceAttribute",
              "ec2:MonitorInstances", "ec2:UnmonitorInstances", "ec2:CreateTags", "ec2:DeleteTags"], ec2 + "instance/*",
              {"StringEquals": {"aws:RequestedRegion": region, "aws:ResourceTag/Project": PROJECT,
                                "aws:ResourceTag/Environment": environment}}),
        # CreateCluster does not support resource-level permissions (AWS SAR).
        allow("CreateCluster", "eks:CreateCluster", "*",
              {"StringEquals": {**tags, "aws:RequestedRegion": region}}),
        allow("ClusterLifecycle", ["eks:DescribeCluster", "eks:DeleteCluster", "eks:UpdateClusterConfig",
              "eks:UpdateClusterVersion", "eks:DescribeUpdate", "eks:ListUpdates",
              "eks:CreateNodegroup", "eks:ListNodegroups"], eks + "cluster/iipp-*", regional),
        allow("NodegroupLifecycle", ["eks:CreateNodegroup", "eks:DescribeNodegroup", "eks:DeleteNodegroup",
              "eks:UpdateNodegroupConfig", "eks:UpdateNodegroupVersion", "eks:DescribeUpdate", "eks:ListUpdates"],
              eks + "nodegroup/iipp-*/*/*", regional),
        allow("EKSTags", ["eks:TagResource", "eks:UntagResource", "eks:ListTagsForResource"],
              [eks + "cluster/iipp-*", eks + "nodegroup/iipp-*/*/*"], regional),
        allow("ReadServiceRoles", "iam:GetRole", [f"arn:aws:iam::{account}:role/{r}" for r in (CLUSTER_ROLE, NODE_ROLE)]),
        allow("PassClusterRole", "iam:PassRole", f"arn:aws:iam::{account}:role/{CLUSTER_ROLE}",
              {"StringEquals": {"iam:PassedToService": "eks.amazonaws.com"}}),
        allow("PassNodeRole", "iam:PassRole", f"arn:aws:iam::{account}:role/{NODE_ROLE}",
              {"StringEquals": {"iam:PassedToService": "eks.amazonaws.com"}}),
        allow("DescribeClusterKey", "kms:DescribeKey", key_arn),
        # EKS CreateCluster does not support GrantIsForAWSResource (EKS KMS guide).
        allow("EKSKeyGrants", "kms:CreateGrant", key_arn),
    ]
    if instance_role:
        statements += [allow("PassInstanceRole", "iam:PassRole", instance_role,
                             {"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}}),
                       allow("ReadInstanceProfiles", "iam:GetInstanceProfile",
                             f"arn:aws:iam::{account}:instance-profile/*")]
    return document(statements)


def role_snapshot(iam, role):
    """Capture effective managed-policy versions and boundary, not just attachment names."""
    try:
        result = {"role": iam.get_role(RoleName=role)["Role"], "inline": {}, "attached": []}
    except iam.exceptions.NoSuchEntityException:
        return {"role": role, "existed": False}
    for page in iam.get_paginator("list_role_policies").paginate(RoleName=role):
        for name in page["PolicyNames"]:
            result["inline"][name] = iam.get_role_policy(RoleName=role, PolicyName=name)["PolicyDocument"]
    for page in iam.get_paginator("list_attached_role_policies").paginate(RoleName=role):
        result["attached"].extend(page["AttachedPolicies"])
    arns = [p["PolicyArn"] for p in result["attached"]]
    boundary = result["role"].get("PermissionsBoundary", {}).get("PermissionsBoundaryArn")
    result["managed_documents"] = {}
    for arn in set(arns + ([boundary] if boundary else [])):
        version = iam.get_policy(PolicyArn=arn)["Policy"]["DefaultVersionId"]
        result["managed_documents"][arn] = iam.get_policy_version(PolicyArn=arn, VersionId=version)["PolicyVersion"]
    return result


def service_trust(service):
    return document([{"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"}])


def verify_identity(session, account):
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != account or identity["Arn"].endswith(":root"):
        raise ValueError(f"A non-root administrator session for account {account} is required")
    return session


def admin_sessions(region, management_profile=None, poc_profile=None, test_profile=None, session_factory=None):
    """Resolve and verify every account before any setup writes; never fall back on profile failure."""
    if bool(poc_profile) != bool(test_profile):
        raise ValueError("Specify both --poc-profile and --test-profile")
    if session_factory is None:
        import boto3
        session_factory = boto3.Session
    def profile_session(profile, account):
        from botocore.exceptions import ProfileNotFound, SSOTokenLoadError, UnauthorizedSSOTokenError
        try:
            return verify_identity(session_factory(profile_name=profile, region_name=region), account)
        except ProfileNotFound as exc:
            raise ValueError(f"AWS profile {profile} is missing. Run: aws configure sso --profile {profile}") from exc
        except (SSOTokenLoadError, UnauthorizedSSOTokenError) as exc:
            raise ValueError(f"AWS SSO sign-in is required. Run: aws sso login --profile {profile}") from exc
    source = profile_session(management_profile, "704298683641")
    targets = []
    for account, profile in (("333347372766", poc_profile), ("695729002971", test_profile)):
        if profile:
            targets.append(profile_session(profile, account))
            continue
        else:
            credentials = source.client("sts").assume_role(
                RoleArn=f"arn:aws:iam::{account}:role/OrganizationAccountAccessRole",
                RoleSessionName="IIPP-Terraform-Setup")["Credentials"]
            target = session_factory(region_name=region, aws_access_key_id=credentials["AccessKeyId"],
                                     aws_secret_access_key=credentials["SecretAccessKey"], aws_session_token=credentials["SessionToken"])
        targets.append(verify_identity(target, account))
    return source, targets


def check_access(source, targets, region):
    """Read-only prerequisites, not a certification of effective apply permissions."""
    source.client("iam").get_role(RoleName="CloudPilotReadTargetMetricsRole")
    source.client("s3").head_bucket(Bucket="inframind-tfstate-704298683641-20261104", ExpectedBucketOwner="704298683641")
    for account, target in zip(("333347372766", "695729002971"), targets):
        iam = target.client("iam")
        for role in ("IIPPInventoryReadRole", "IIPPTerraformExecutionRole"):
            snapshot = role_snapshot(iam, role)
            if snapshot.get("existed") is False:
                raise ValueError(f"Missing target role: {account}/{role}")
        kms = target.client("kms", region_name=region)
        keys = []
        for page in kms.get_paginator("list_keys").paginate():
            for item in page["Keys"]:
                key = kms.describe_key(KeyId=item["KeyId"])["KeyMetadata"]
                if (key["KeyState"] == "Enabled" and key["KeyManager"] == "CUSTOMER"
                        and key.get("KeySpec") == "SYMMETRIC_DEFAULT" and key["KeyUsage"] == "ENCRYPT_DECRYPT"):
                    keys.append(key["Arn"])
        ec2 = target.client("ec2", region_name=region)
        subnets = [s for page in ec2.get_paginator("describe_subnets").paginate() for s in page["Subnets"]]
        print(json.dumps({"account": account, "region": region, "eligible_key_arns": keys,
                          "subnets": [{k: s[k] for k in ("SubnetId", "VpcId", "AvailabilityZone", "AvailableIpAddressCount")} for s in subnets]}))
    print("Read-only prerequisites checked; key policy, routing, SCPs and effective write permissions still require verification.")


def configure_target(session, account, region, environment, key_arn, backup_dir=None, instance_role=None):
    verify_identity(session, account)
    iam = session.client("iam")
    # Fail before writes if connections have not been bootstrapped. Never replace their trust.
    for role in ("IIPPInventoryReadRole", "IIPPTerraformExecutionRole"):
        iam.get_role(RoleName=role)
    if backup_dir is None:
        raise ValueError("A unique backup directory is required before IAM changes")
    backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    for role in ("IIPPInventoryReadRole", "IIPPTerraformExecutionRole", CLUSTER_ROLE, NODE_ROLE):
        snapshot = role_snapshot(iam, role)
        path = backup_dir / (role + ".json")
        path.write_text(json.dumps(snapshot, default=str, indent=2), encoding="utf-8")
        path.chmod(0o600)
    key = session.client("kms").describe_key(KeyId=key_arn)["KeyMetadata"]
    if (key["KeyState"] != "Enabled" or key["KeyUsage"] != "ENCRYPT_DECRYPT"
            or key["KeyManager"] != "CUSTOMER" or key.get("KeySpec") != "SYMMETRIC_DEFAULT"):
        raise ValueError("EKS requires an enabled customer managed encryption key")
    for role, service, policies in [
        (CLUSTER_ROLE, "eks.amazonaws.com", ["AmazonEKSClusterPolicy"]),
        (NODE_ROLE, "ec2.amazonaws.com", ["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly", "AmazonEKS_CNI_Policy"]),
    ]:
        trust = service_trust(service)
        try:
            existing = iam.get_role(RoleName=role)["Role"]["AssumeRolePolicyDocument"]
            if existing.get("Statement") != trust["Statement"]:
                raise ValueError(f"Existing {role} trust differs; review it before setup")
        except iam.exceptions.NoSuchEntityException:
            iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust))
        for policy in policies:
            iam.attach_role_policy(RoleName=role, PolicyArn="arn:aws:iam::aws:policy/" + policy)
    for service, role in (("eks.amazonaws.com", "AWSServiceRoleForAmazonEKS"),
                          ("eks-nodegroup.amazonaws.com", "AWSServiceRoleForAmazonEKSNodegroup"),
                          ("autoscaling.amazonaws.com", "AWSServiceRoleForAutoScaling")):
        try:
            iam.get_role(RoleName=role)
        except iam.exceptions.NoSuchEntityException:
            iam.create_service_linked_role(AWSServiceName=service)
    from configure_iipp_test_accounts import inventory_policy
    iam.put_role_policy(RoleName="IIPPInventoryReadRole", PolicyName="IIPPPlanInputReads",
                        PolicyDocument=json.dumps(inventory_policy()))
    iam.put_role_policy(RoleName="IIPPTerraformExecutionRole", PolicyName="Innominds-IIPP-Permissions",
                        PolicyDocument=json.dumps(execution_policy(account, region, environment, key_arn, instance_role)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--poc-key-arn")
    parser.add_argument("--test-key-arn")
    parser.add_argument("--poc-instance-role-arn")
    parser.add_argument("--test-instance-role-arn")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--management-profile")
    parser.add_argument("--poc-profile")
    parser.add_argument("--test-profile")
    parser.add_argument("--check-access", action="store_true", help="Read-only identity, role, KMS and subnet checks; no setup writes")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if bool(args.poc_profile) != bool(args.test_profile):
        parser.error("Specify both --poc-profile and --test-profile")
    if args.check_access:
        if args.execute:
            parser.error("--check-access cannot be combined with --execute")
        source, sessions = admin_sessions(args.region, args.management_profile, args.poc_profile, args.test_profile)
        check_access(source, sessions, args.region)
        return
    if not args.output_dir or not args.poc_key_arn or not args.test_key_arn:
        parser.error("--output-dir, --poc-key-arn and --test-key-arn are required for policy generation/setup")
    targets = [("poc", "333347372766", args.poc_key_arn), ("test", "695729002971", args.test_key_arn)]
    instance_roles = {"poc": args.poc_instance_role_arn, "test": args.test_instance_role_arn}
    for _, account, key in targets:
        if not key.startswith(f"arn:aws:kms:{args.region}:{account}:key/"):
            parser.error("KMS key must belong to its target account and selected region")
    for environment, account, _ in targets:
        role = instance_roles[environment]
        if role and not role.startswith(f"arn:aws:iam::{account}:role/"):
            parser.error("Instance role must belong to its target account")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for environment, account, key in targets:
        path = args.output_dir / f"{environment}-execution-policy.json"
        path.write_text(json.dumps(execution_policy(account, args.region, environment, key, instance_roles[environment]), indent=2) + "\n")
    from configure_iipp_test_accounts import inventory_policy
    artifacts = {"inventory-policy.json": inventory_policy(),
                 "eks-cluster-trust.json": service_trust("eks.amazonaws.com"),
                 "eks-node-trust.json": service_trust("ec2.amazonaws.com"),
                 "service-role-attachments.json": {
                     CLUSTER_ROLE: ["arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"],
                     NODE_ROLE: ["arn:aws:iam::aws:policy/" + name for name in
                                 ("AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly", "AmazonEKS_CNI_Policy")]}}
    for name, value in artifacts.items():
        (args.output_dir / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print("Prepared scoped policies; connection trust and External IDs are preserved.")
    if not args.execute:
        return
    source, sessions = admin_sessions(args.region, args.management_profile, args.poc_profile, args.test_profile)
    check_access(source, sessions, args.region)
    for (environment, account, key), target in zip(targets, sessions):
        from datetime import datetime, timezone
        backup = args.output_dir / (account + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        configure_target(target, account, args.region, environment, key, backup, instance_roles[environment])
        print(f"Configured {environment} IAM; networking and key policy require independent verification.")


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        raise SystemExit(str(exc))
