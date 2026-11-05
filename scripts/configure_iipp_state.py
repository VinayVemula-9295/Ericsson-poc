#!/usr/bin/env python3
"""Configure the existing central bucket and controller policy; dry-run by default."""
import argparse
import json
from pathlib import Path

BUCKET = "inframind-tfstate-704298683641-20261104"
ACCOUNT = "704298683641"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-policy", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--management-profile")
    args = parser.parse_args()
    policy = json.loads(args.controller_policy.read_text())
    if not args.execute:
        print("DRY RUN: preserve bucket policy/encryption; enable versioning/public blocks/TLS; attach controller policy")
        return
    import boto3
    from botocore.exceptions import ClientError
    s = boto3.Session(profile_name=args.management_profile, region_name="ap-south-1")
    identity = s.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT or identity["Arn"].endswith(":root"):
        raise ValueError("Non-root management-account administrator credentials required")
    s3 = s.client("s3")
    params = dict(Bucket=BUCKET, ExpectedBucketOwner=ACCOUNT)
    s3.head_bucket(**params)
    previous = {}
    for name in ("get_bucket_policy", "get_bucket_versioning", "get_bucket_encryption", "get_public_access_block"):
        try:
            value = getattr(s3, name)(**params)
            value.pop("ResponseMetadata", None)
            previous[name] = value
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("NoSuchBucketPolicy", "NoSuchPublicAccessBlockConfiguration", "ServerSideEncryptionConfigurationNotFoundError"):
                raise
    iam = s.client("iam")
    try:
        previous["controller_policy"] = iam.get_role_policy(RoleName="CloudPilotReadTargetMetricsRole", PolicyName="IIPPCentralTerraformAccess")["PolicyDocument"]
    except iam.exceptions.NoSuchEntityException:
        pass
    args.backup_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    backup = args.backup_dir / "previous.json"
    backup.write_text(json.dumps(previous, indent=2), encoding="utf-8")
    backup.chmod(0o600)
    bucket_policy = json.loads(previous.get("get_bucket_policy", {}).get("Policy", '{"Version":"2012-10-17","Statement":[]}'))
    deny = dict(Sid="IIPPDenyInsecureTransport", Effect="Deny", Principal="*", Action="s3:*",
                Resource=[f"arn:aws:s3:::{BUCKET}", f"arn:aws:s3:::{BUCKET}/*"],
                Condition={"Bool": {"aws:SecureTransport": "false"}})
    statements = bucket_policy["Statement"]
    if isinstance(statements, dict):
        statements = [statements]
    bucket_policy["Statement"] = [x for x in statements if x.get("Sid") != deny["Sid"]] + [deny]
    if "get_bucket_encryption" in previous:
        encryption = previous["get_bucket_encryption"]["ServerSideEncryptionConfiguration"]["Rules"]
        if any(x["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] != "AES256" for x in encryption):
            raise ValueError("Bucket uses KMS: extend controller policy/key policy before continuing; no settings changed")
    s3.put_bucket_versioning(**params, VersioningConfiguration={"Status": "Enabled"})
    s3.put_public_access_block(**params, PublicAccessBlockConfiguration=dict(BlockPublicAcls=True, IgnorePublicAcls=True, BlockPublicPolicy=True, RestrictPublicBuckets=True))
    if "get_bucket_encryption" not in previous:
        s3.put_bucket_encryption(**params, ServerSideEncryptionConfiguration={"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_policy(**params, Policy=json.dumps(bucket_policy))
    iam.put_role_policy(RoleName="CloudPilotReadTargetMetricsRole", PolicyName="IIPPCentralTerraformAccess", PolicyDocument=json.dumps(policy))
    print("Configured central state protections and controller access; previous settings backed up.")


if __name__ == "__main__":
    main()
