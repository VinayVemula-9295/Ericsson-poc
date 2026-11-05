#!/usr/bin/env python3
"""Generate the least-privilege inline policy for the IIPP EC2 controller role."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True, help="Central S3 state bucket in the controller account")
    parser.add_argument("--region", default="ap-south-1")
    parser.add_argument("--controller-account-id", required=True)
    parser.add_argument("--poc-account-id", required=True)
    parser.add_argument("--test-account-id", required=True)
    parser.add_argument("--output", default=str(Path.home() / ".iipp-controller-policy.json"))
    args = parser.parse_args()
    for value in (args.controller_account_id, args.poc_account_id, args.test_account_id):
        if not value.isdigit() or len(value) != 12:
            parser.error("All account IDs must be 12 digits")
    if args.poc_account_id == args.test_account_id:
        parser.error("POC and TEST accounts must differ")
    bucket = args.bucket
    policy = {"Version": "2012-10-17", "Statement": [
        {"Sid": "IIPPListRemoteStatePrefix", "Effect": "Allow", "Action": ["s3:ListBucket"],
         "Resource": f"arn:aws:s3:::{bucket}",
         "Condition": {"StringLike": {"s3:prefix": ["iipp/deployments", "iipp/deployments/*", "iipp/poc", "iipp/poc/*"]}}},
        {"Sid": "IIPPStateBucketLocation", "Effect": "Allow", "Action": "s3:GetBucketLocation",
         "Resource": f"arn:aws:s3:::{bucket}"},
        {"Sid": "IIPPStateReadWriteAndVersionRecovery", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject"],
         "Resource": [f"arn:aws:s3:::{bucket}/{prefix}/*" for prefix in ("iipp/deployments", "iipp/poc")]},
        {"Sid": "IIPPNativeStateLockfiles", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": [f"arn:aws:s3:::{bucket}/{prefix}/*.tflock" for prefix in ("iipp/deployments", "iipp/poc")]},
        {"Sid": "IIPPProviderSecretsOnly", "Effect": "Allow",
         "Action": ["secretsmanager:CreateSecret", "secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue", "secretsmanager:TagResource", "secretsmanager:UpdateSecret"],
         "Resource": f"arn:aws:secretsmanager:{args.region}:{args.controller_account_id}:secret:innominds-iipp/connections/*"},
        {"Sid": "IIPPAssumeOnlyPOCAndTESTRoles", "Effect": "Allow", "Action": "sts:AssumeRole", "Resource": [
            f"arn:aws:iam::{args.poc_account_id}:role/IIPPInventoryReadRole",
            f"arn:aws:iam::{args.poc_account_id}:role/IIPPTerraformExecutionRole",
            f"arn:aws:iam::{args.test_account_id}:role/IIPPInventoryReadRole",
            f"arn:aws:iam::{args.test_account_id}:role/IIPPTerraformExecutionRole"]},
        {"Sid": "IIPPLiveEC2PublicPriceListRead", "Effect": "Allow", "Action": ["pricing:GetProducts"], "Resource": "*"},
        {"Sid": "IIPPControllerLiveAccountIdentity", "Effect": "Allow", "Action": "sts:GetCallerIdentity", "Resource": "*"}
    ]}
    path = Path(args.output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy, indent=2) + "\n")
    os.chmod(path, 0o600)
    print(f"Generated {path} (mode 0600). Review it and attach it to the IIPP EC2 controller role with an IAM administrator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
