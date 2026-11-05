#!/usr/bin/env bash
# Create an encrypted, versioned S3 backend bucket. Defaults to DRY RUN.
set -euo pipefail

REGION="${TF_STATE_REGION:-ap-south-1}"
BUCKET="${TF_STATE_BUCKET:-}"
EXECUTE=false
for arg in "$@"; do
  case "$arg" in
    --execute) EXECUTE=true ;;
    --help|-h)
      echo "Usage: TF_STATE_BUCKET=<unique-name> [TF_STATE_REGION=ap-south-1] $0 [--execute]"
      echo "Default: dry-run only. --execute creates/configures the bucket. Run with an administrative identity."
      exit 0 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "$BUCKET" ]]; then
  echo "Set TF_STATE_BUCKET to a globally unique bucket name." >&2
  exit 2
fi
if ! [[ "$BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]]; then
  echo "Bucket name is not in the expected S3 naming format." >&2
  exit 2
fi

IDENTITY="$(aws sts get-caller-identity --output json)"
ACCOUNT_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])' <<< "$IDENTITY")"
echo "Caller account: $ACCOUNT_ID"
echo "Bucket: $BUCKET"
echo "Region: $REGION"
echo "Actions: create if absent; block public access; enforce bucket-owner ownership; enable versioning; enable AES-256 server-side encryption; deny non-TLS access."
if [[ "$EXECUTE" != true ]]; then
  echo "DRY RUN: no AWS changes made. Re-run with --execute using an administrator identity with S3 bucket permissions."
  exit 0
fi

if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  echo "Bucket already exists and is accessible. It will be configured in place."
else
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
fi

aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" --ownership-controls \
  'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

POLICY_FILE="$(mktemp)"
trap 'rm -f "$POLICY_FILE"' EXIT
cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "DenyInsecureTransport",
    "Effect": "Deny",
    "Principal": "*",
    "Action": "s3:*",
    "Resource": ["arn:aws:s3:::$BUCKET", "arn:aws:s3:::$BUCKET/*"],
    "Condition": {"Bool": {"aws:SecureTransport": "false"}}
  }]
}
EOF
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "file://$POLICY_FILE"

echo "Configured bucket: $BUCKET"
aws s3api get-bucket-versioning --bucket "$BUCKET"
aws s3api get-bucket-encryption --bucket "$BUCKET"
echo "NEXT: attach policy_templates/aws/IIPPStateBucketAccess.template.json to the controller role, then set TF_STATE_BUCKET and TF_STATE_REGION before enabling Terraform."
