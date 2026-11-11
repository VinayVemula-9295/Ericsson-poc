"""Audit/export one account independently; --execute repairs target IAM after backup.

No infrastructure, key, connection or runtime changes. Existing trust is immutable.
When no --key-arn is supplied, only server execution permissions are installed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from configure_iipp_terraform import (CLUSTER_ROLE, NODE_ROLE, execution_policy,
                                     role_snapshot, service_trust, verify_identity, compatible_service_trust)
from configure_iipp_test_accounts import inventory_policy

TARGETS = {"333347372766": "poc", "695729002971": "test"}
ROLES = ["IIPPInventoryReadRole", "IIPPTerraformExecutionRole", CLUSTER_ROLE, NODE_ROLE]
ATTACHMENTS = {CLUSTER_ROLE: ["AmazonEKSClusterPolicy"], NODE_ROLE: [
    "AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly", "AmazonEKS_CNI_Policy"]}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    path.chmod(0o600)


def connection_trust(snapshot, controller, existing_external_id=None):
    """Allow bootstrap only from an existing, narrowly scoped connection trust."""
    trust = snapshot.get("role", {}).get("AssumeRolePolicyDocument", {})
    statements = trust.get("Statement", [])
    if len(statements) != 1:
        raise ValueError("Inventory trust needs review: expected one controller statement")
    stmt = statements[0]
    external = stmt.get("Condition", {}).get("StringEquals", {}).get("sts:ExternalId")
    if (stmt.get("Effect") != "Allow" or stmt.get("Action") != "sts:AssumeRole"
            or stmt.get("Principal") != {"AWS": controller}
            or (not external and not existing_external_id)):
        raise ValueError("Inventory trust must restrict the controller role and require an External ID")
    if external and (not isinstance(external, str) or (existing_external_id and external != existing_external_id)):
        raise ValueError("Existing External ID differs from the saved connection")
    if not external:
        # Only the NEW execution role receives this copy; inventory trust is untouched.
        trust = json.loads(json.dumps(trust))
        trust["Statement"][0].setdefault("Condition", {}).setdefault("StringEquals", {})["sts:ExternalId"] = existing_external_id
    return trust


def server_policy(policy):
    return {**policy, "Statement": [s for s in policy["Statement"] if s["Sid"] in {
        "Discovery", "LaunchInstances", "LaunchDependencies", "LaunchTags", "ManagedInstanceLifecycle",
        "PassInstanceRole", "ReadInstanceProfiles"}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--account", required=True, choices=["704298683641", *TARGETS])
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--key-arn")
    parser.add_argument("--instance-role-arn")
    parser.add_argument("--allowed-instance-families", default="t3", help="Comma-separated families or *; does not remove account, region or ownership controls")
    parser.add_argument("--external-id-file", type=Path, help="Private JSON containing the existing connection external_id; never printed")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.key_arn and not args.key_arn.startswith(f"arn:aws:kms:{args.region}:{args.account}:key/"):
        parser.error("KMS key must belong to the selected account and region")
    if args.instance_role_arn and not args.instance_role_arn.startswith(f"arn:aws:iam::{args.account}:role/"):
        parser.error("Instance role must belong to the selected account")
    if args.execute and args.account not in TARGETS:
        parser.error("Management changes use configure_iipp_state.py with a non-root admin session")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = Config(connect_timeout=5, read_timeout=15, retries={"mode": "standard", "total_max_attempts": 3})
    def client(service):
        return session.client(service, config=config)
    identity = client("sts").get_caller_identity()
    if identity["Account"] != args.account:
        raise ValueError("Profile belongs to a different account")
    if args.execute:
        verify_identity(session, args.account)
    output = args.output_dir / (args.account + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    iam = client("iam")
    roles = ROLES if args.account in TARGETS else ["CloudPilotReadTargetMetricsRole"]
    snapshots = {role: role_snapshot(iam, role) for role in roles}
    save(output / "before.json", snapshots)
    evidence = {"identity": {k: identity[k] for k in ("Account", "Arn")}, "errors": {}, "applied": False}
    def record(name, action):
        try:
            evidence[name] = action()
        except ClientError as exc:
            evidence["errors"][name] = exc.response["Error"]["Code"]
    kms = client("kms")
    def keys():
        result = []
        for page in kms.get_paginator("list_keys").paginate():
            for key in page["Keys"]:
                meta = kms.describe_key(KeyId=key["KeyId"])["KeyMetadata"]
                result.append(meta)
                if meta["KeyManager"] == "CUSTOMER":
                    save(output / ("key-" + key["KeyId"] + ".json"),
                         json.loads(kms.get_key_policy(KeyId=key["KeyId"], PolicyName="default")["Policy"]))
        return result
    record("keys", keys)
    record("subnets", lambda: [s for page in client("ec2").get_paginator("describe_subnets").paginate() for s in page["Subnets"]])
    record("clusters", lambda: [s for page in client("eks").get_paginator("list_clusters").paginate() for s in page["clusters"]])
    record("direct_scps", lambda: [p for page in client("organizations").get_paginator("list_policies_for_target").paginate(
        TargetId=args.account, Filter="SERVICE_CONTROL_POLICY") for p in page["Policies"]])
    evidence["scp_limit"] = "Direct attachments only; inherited SCPs and their contents require organization administrator review."
    save(output / "evidence.json", evidence)
    if args.account not in TARGETS:
        s3 = client("s3")
        bucket = {"Bucket": "inframind-tfstate-704298683641-20261104", "ExpectedBucketOwner": args.account}
        for method in ("get_bucket_policy", "get_bucket_encryption", "get_bucket_versioning", "get_public_access_block"):
            record(method, lambda method=method: getattr(s3, method)(**bucket))
        save(output / "evidence.json", evidence)
        print(f"Management audit saved: {output}")
        return
    environment = TARGETS[args.account]
    key = args.key_arn or f"arn:aws:kms:{args.region}:{args.account}:key/REPLACE_WITH_VERIFIED_KEY_ID"
    policy = execution_policy(args.account, args.region, environment, key, args.instance_role_arn, args.allowed_instance_families)
    save(output / "execution-policy.json", policy)
    save(output / "server-execution-policy.json", server_policy(policy))
    save(output / "inventory-policy.json", inventory_policy(args.account))
    save(output / "service-role-attachments.json", ATTACHMENTS)
    for role, service in ((CLUSTER_ROLE, "eks.amazonaws.com"), (NODE_ROLE, "ec2.amazonaws.com")):
        save(output / (role + "-trust.json"), service_trust(service))
    proposed = policy if args.key_arn else server_policy(policy)
    record("policy_validation", lambda: [finding for page in client("accessanalyzer").get_paginator("validate_policy").paginate(
        policyDocument=json.dumps(proposed), policyType="IDENTITY_POLICY") for finding in page["findings"]])
    record("full_policy_validation", lambda: [finding for page in client("accessanalyzer").get_paginator("validate_policy").paginate(
        policyDocument=json.dumps(policy), policyType="IDENTITY_POLICY") for finding in page["findings"]])
    save(output / "evidence.json", evidence)
    if not args.execute:
        print(f"Audit and policies saved: {output}; no AWS changes")
        return
    if "policy_validation" in evidence["errors"] or any(f["findingType"] == "ERROR" for f in evidence.get("policy_validation", [])):
        raise ValueError("Policy validation must succeed before writes")
    if args.key_arn:
        key_meta = kms.describe_key(KeyId=args.key_arn)["KeyMetadata"]
        if any(key_meta.get(k) != v for k, v in {"KeyState": "Enabled", "KeyManager": "CUSTOMER",
                "KeySpec": "SYMMETRIC_DEFAULT", "KeyUsage": "ENCRYPT_DECRYPT"}.items()):
            raise ValueError("Selected KMS key is not an enabled symmetric customer key")
    controller = "arn:aws:iam::704298683641:role/CloudPilotReadTargetMetricsRole"
    external_id = None
    if args.external_id_file:
        external_id = json.loads(args.external_id_file.read_text(encoding="utf-8"))["external_id"]
        if not isinstance(external_id, str) or not 2 <= len(external_id) <= 1224:
            raise ValueError("Existing connection External ID is invalid")
    trust = connection_trust(snapshots[ROLES[0]], controller, external_id)
    save(output / "execution-trust.json", trust)
    # Validate all existing service trust before the first write.
    for role, service in ((CLUSTER_ROLE, "eks.amazonaws.com"), (NODE_ROLE, "ec2.amazonaws.com")):
        snapshot = snapshots[role]
        if snapshot.get("existed") is not False and not compatible_service_trust(snapshot["role"]["AssumeRolePolicyDocument"], service):
            raise ValueError(f"Existing {role} trust requires review")
    if snapshots[ROLES[1]].get("existed") is False:
        iam.create_role(RoleName=ROLES[1], AssumeRolePolicyDocument=json.dumps(trust),
                        Tags=[{"Key": "Project", "Value": "Innominds-IIPP"}])
        iam.get_waiter("role_exists").wait(RoleName=ROLES[1])
    for role, service in ((CLUSTER_ROLE, "eks.amazonaws.com"), (NODE_ROLE, "ec2.amazonaws.com")):
        if snapshots[role].get("existed") is False:
            iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(service_trust(service)))
        for managed in ATTACHMENTS[role]:
            iam.attach_role_policy(RoleName=role, PolicyArn="arn:aws:iam::aws:policy/" + managed)
    for service, role in (("eks.amazonaws.com", "AWSServiceRoleForAmazonEKS"),
                          ("eks-nodegroup.amazonaws.com", "AWSServiceRoleForAmazonEKSNodegroup"),
                          ("autoscaling.amazonaws.com", "AWSServiceRoleForAutoScaling")):
        try:
            iam.get_role(RoleName=role)
        except iam.exceptions.NoSuchEntityException:
            iam.create_service_linked_role(AWSServiceName=service)
    iam.put_role_policy(RoleName=ROLES[0], PolicyName="IIPPPlanInputReads", PolicyDocument=json.dumps(inventory_policy(args.account)))
    # A separate server policy avoids replacing an existing EKS policy without a KMS key.
    iam.put_role_policy(RoleName=ROLES[1], PolicyName="Innominds-IIPP-Permissions" if args.key_arn else "IIPPServerLifecycle",
                        PolicyDocument=json.dumps(proposed))
    evidence["applied"] = True
    evidence["execution_scope"] = "server-and-eks" if args.key_arn else "server-only; EKS requires a verified KMS key"
    save(output / "after.json", {role: role_snapshot(iam, role) for role in ROLES})
    evidence["simulation_limit"] = "IAM simulations do not establish SCP, key-policy or real apply/destroy success."
    for env_value in (environment, "wrong-environment"):
        record("terminate_simulation_" + env_value, lambda env_value=env_value: iam.simulate_principal_policy(
            PolicySourceArn=f"arn:aws:iam::{args.account}:role/{ROLES[1]}",
            ActionNames=["ec2:TerminateInstances"],
            ResourceArns=[f"arn:aws:ec2:{args.region}:{args.account}:instance/i-00000000000000000"],
            ContextEntries=[{"ContextKeyName": name, "ContextKeyValues": [value], "ContextKeyType": "string"}
                            for name, value in [("aws:RequestedRegion", args.region),
                                ("aws:ResourceTag/Project", "Innominds-IIPP"),
                                ("aws:ResourceTag/Environment", env_value)]])["EvaluationResults"])
    save(output / "evidence.json", evidence)
    print(f"Target IAM updated; private backup/evidence: {output}; {evidence['execution_scope']}")


if __name__ == "__main__":
    main()
