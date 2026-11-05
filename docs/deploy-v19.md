# EC2 upgrade checklist — Intelligent Provisioning Platform v19

This is a source/UI and recommendation-logic upgrade. It does not migrate or delete existing Terraform state, does not create AWS resources, and does not grant IAM permissions. The current app checkout on the EC2 host is `~/iipp-v14`; keep that runtime path and the Docker image/container identifiers for compatibility.

## 1. Confirm recovery prerequisites

Confirm the EBS snapshot is `Completed`. Then on EC2:

```bash
cd "$HOME/iipp-v14"
chmod +x backup.sh deploy.sh rollback.sh cleanup.sh
BACKUP_STAMP="$(./backup.sh)"
printf '%s\n' "$BACKUP_STAMP" > "$HOME/inframind-v19-predeploy-stamp.txt"
echo "Backup: $HOME/cloudpilot-backups/$BACKUP_STAMP"
```

Do not continue unless the backup contains `project.tar.gz` and `docker-rollback.txt`.

## 2. Upload and inspect the release

From your workstation:

```powershell
scp -i "C:\path\to\your-key.pem" .\inframind-v19-release.zip ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
```

On EC2:

```bash
mkdir -p "$HOME/inframind-v19-staging"
unzip -q "$HOME/inframind-v19-release.zip" -d "$HOME/inframind-v19-staging"
less "$HOME/inframind-v19-staging/inframind-v19/UPDATE-NOTES-v19.md"
less "$HOME/inframind-v19-staging/inframind-v19/IMPLEMENTATION_STATUS.md"
```

## 3. Overlay source while retaining runtime data

```bash
rsync -av \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='terraform_deployments/' \
  --exclude='terraform-plugin-cache/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  "$HOME/inframind-v19-staging/inframind-v19/" \
  "$HOME/iipp-v14/"
```

Do not use `rsync --delete`. Preserve `.env`, `/opt/cloudpilot/data`, provider connection metadata, Terraform state, saved plans, approval token, and previous Docker images/container.

## 4. Deploy with provisioning disabled

```bash
cd "$HOME/iipp-v14"
export AWS_REGION=ap-south-1
export AWS_SECRETS_MANAGER_REGION=ap-south-1
export ENABLE_TERRAFORM_WORKFLOW=false
export ENABLE_APPROVAL_EXECUTION=false
export TF_STATE_REGION=ap-south-1
export TF_STATE_PREFIX=iipp/deployments
export TF_PLUGIN_CACHE_DIR=/opt/cloudpilot/terraform-plugin-cache
CLOUDPILOT_BACKUP_STAMP="$(cat "$HOME/inframind-v19-predeploy-stamp.txt")" ./deploy.sh
```

Check:

```bash
sudo docker ps -a
sudo docker logs --tail 120 cloudpilot-ai
curl -fsS http://127.0.0.1:8501/_stcore/health
```

Do not remove the retained previous container or rollback image until the demo acceptance checks pass.

## 5. Enable live AWS Price List access (read-only)

The Price List call is made by the EC2 controller role in Naresh-Acc (`704298683641`), not by the Demo/Test inventory roles. In IAM, open `CloudPilotReadTargetMetricsRole` → Permissions → Add permissions → Create inline policy → JSON and apply the contents of `iam-live-pricing-policy.json`. Name the policy `IIPPLiveEC2PublicPriceListRead`. This grants only `pricing:GetProducts` on `*`. If an organization SCP or permissions boundary denies the API, an identity policy alone will not override that deny.

After the policy is attached, run the real AWS price verification from the EC2 host:

```bash
python3 - <<'PYPRICE'
from aws_data import clear_price_cache, get_ec2_instance_on_demand_price
clear_price_cache()
for instance_type in ("m5a.xlarge", "c5n.xlarge"):
    result = get_ec2_instance_on_demand_price(instance_type, region="ap-south-1")
    print({k: result.get(k) for k in (
        "available", "instance_type", "region", "hourly_usd", "monthly_730h_usd",
        "price_source", "retrieved_at", "effective_date", "publication_date",
        "pricing_api_region", "cache_status", "error"
    )})
PYPRICE
```

The first successful lookup after clearing should show `cache_status=MISS`, a current `retrieved_at`, an AWS Price List Query API source, and an actual USD hourly rate. Do not assume the rate equals the older screenshot fixture. Full pricing assumptions and troubleshooting: [`aws-live-pricing.md`](aws-live-pricing.md).

## 6. Demo acceptance checklist

1. Product identity reads **Intelligent Provisioning Platform** once in the shared header. Agent has one centered provisioning prompt and a **Find options** action; there is no separate general-question chat.
2. Use a request such as: `I need a Linux VM with 4 vCPU and 9 GiB RAM in ap-south-1. Prefer a balanced, cost-effective configuration.`
3. Confirm an exact CPU/RAM profile is preferred only if one exists with valid evidence. Otherwise, confirm only configurations meeting/exceeding both minimums are marked eligible.
4. Check that AWS candidates are ranked using the hourly rates returned by the current Price List API lookup. Do not expect fixed screenshot sample values.
5. Confirm a candidate receives **Recommended** and the separately calculated lowest-cost eligible candidate receives **Optimal · lowest cost**. A single SKU may have both labels.
6. Confirm the selection dropdown contains all returned priced USD options meeting the CPU/RAM minimums. Below-minimum or unverified profiles must not be selectable for Terraform.
7. Confirm **Exact match check and closest configurations** and **Detailed comparison and exclusions** remain visible.
8. Confirm the Agent produces its 6–8 line explanation automatically after comparison. It must say when latency is unmeasured and distinguish regional offerings from account quota/spare capacity. Search visually for literal `<thinking>`, `<analysis>` or similar internal tags; none should appear.
9. Test a prompt with an explicit lowest-cost requirement, and another latency-sensitive prompt. With no measured latency, the Agent must not claim a latency winner.
10. Verify Work/Agent selected cloud account context and Configure save behavior still operate as before.

## 7. Keep Terraform disabled until S3 and roles are verified

Before enabling a new Terraform workflow:

- Create a dedicated S3 bucket in the controller account.
- Enable bucket versioning, encryption and Block Public Access.
- Grant the controller role scoped state and S3 lockfile permissions.
- Verify each target account's `IIPPTerraformExecutionRole` trust and restricted permissions, with matching External ID.
- Test a unique S3 state key and native S3 lockfile in Test-Account.
- Confirm policy validation and exact saved-plan checksums.
- Restrict TCP 8501 to the reviewer/demo IP range.

Only then set `TF_STATE_BUCKET`, enable Terraform plan generation, and perform a controlled plan review. Keep `ENABLE_APPROVAL_EXECUTION=false` until a reviewer is ready for the apply test. Do not enable apply solely because the UI renders.

## 8. Rollback

```bash
cd "$HOME/iipp-v14"
./rollback.sh "$(cat "$HOME/inframind-v19-predeploy-stamp.txt")"
```

Do not run broad Docker prune or delete files under `/opt/cloudpilot/data` as part of this UI/recommendation upgrade.


## v19 acceptance checks

1. In Agent, submit exactly: `I need 4 Vpcu and 8Gib Server`.
2. Confirm Parsed workload requirements show **4 vCPU** and **8.0 GiB RAM**, and no default warning appears.
3. Confirm AWS exact matches returned by live `DescribeInstanceTypes` are present in the Selectable tier. For example, if the selected region/account returns a compatible 4-vCPU / 8-GiB profile such as `c6i.xlarge`, it should be classified as exact; the actual returned live profile is authoritative.
4. Under each AWS/Azure/GCP tab, inspect Selectable, Recommended and Optimal cards. Recommended and Optimal must remain inside the exact-match tier when that provider has a priced exact profile; otherwise use the meets-minimum tier.
5. Confirm each AWS card's action starts the existing Terraform plan selection flow only when AWS offering/spec checks pass. Azure/GCP actions remain disabled for provisioning in this release.
6. Confirm `Detailed comparison and exclusions` appears before `Choose from all returned priced configurations that meet the request`.
7. Confirm the left sidebar no longer displays the Platform block or controller-region tagline.
8. Keep Terraform apply disabled until the versioned S3 backend, controller IAM, target execution roles and reviewer approval are verified.
