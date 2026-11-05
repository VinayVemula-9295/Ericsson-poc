# EC2 upgrade checklist — InfraMind v17

This is a source/UI and recommendation-logic upgrade. It does not migrate or delete existing Terraform state, does not create AWS resources, and does not grant IAM permissions. The current app checkout on the EC2 host is `~/iipp-v14`; keep that runtime path and the Docker image/container identifiers for compatibility.

## 1. Confirm recovery prerequisites

Confirm the EBS snapshot is `Completed`. Then on EC2:

```bash
cd "$HOME/iipp-v14"
chmod +x backup.sh deploy.sh rollback.sh cleanup.sh
BACKUP_STAMP="$(./backup.sh)"
printf '%s\n' "$BACKUP_STAMP" > "$HOME/inframind-v17-predeploy-stamp.txt"
echo "Backup: $HOME/cloudpilot-backups/$BACKUP_STAMP"
```

Do not continue unless the backup contains `project.tar.gz` and `docker-rollback.txt`.

## 2. Upload and inspect the release

From your workstation:

```powershell
scp -i "C:\path\to\your-key.pem" .\inframind-v17-release.zip ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
```

On EC2:

```bash
mkdir -p "$HOME/inframind-v17-staging"
unzip -q "$HOME/inframind-v17-release.zip" -d "$HOME/inframind-v17-staging"
less "$HOME/inframind-v17-staging/inframind-v17/UPDATE-NOTES-v17.md"
less "$HOME/inframind-v17-staging/inframind-v17/IMPLEMENTATION_STATUS.md"
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
  "$HOME/inframind-v17-staging/inframind-v17/" \
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
CLOUDPILOT_BACKUP_STAMP="$(cat "$HOME/inframind-v17-predeploy-stamp.txt")" ./deploy.sh
```

Check:

```bash
sudo docker ps -a
sudo docker logs --tail 120 cloudpilot-ai
curl -fsS http://127.0.0.1:8501/_stcore/health
```

Do not remove the retained previous container or rollback image until the demo acceptance checks pass.

## 5. Demo acceptance checklist

1. Product identity reads **InfraMind — Multi-Cloud Intelligent Provisioning** with tagline **Think Intelligent. Provision Anywhere.**
2. Use a request such as: `I need a Linux VM with 4 vCPU and 9 GiB RAM in ap-south-1. Prefer a balanced, cost-effective configuration.`
3. Confirm an exact CPU/RAM profile is preferred only if one exists with valid evidence. Otherwise, confirm only configurations meeting/exceeding both minimums are marked eligible.
4. With the supplied regression sample prices, check that `m5a.xlarge` (USD 81.03/month) is ranked ahead of `c5n.xlarge` (USD 157.68/month), provided live AWS regional offering and current price evidence return those values. These are sample values from the user's screenshots, not guaranteed current prices.
5. Confirm a candidate receives **Recommended** and the separately calculated lowest-cost eligible candidate receives **Optimal · lowest cost**. A single SKU may have both labels.
6. Confirm the selection dropdown contains all returned priced USD options meeting the CPU/RAM minimums. Below-minimum or unverified profiles must not be selectable for Terraform.
7. Confirm **Exact match check and closest configurations** and **Detailed comparison and exclusions** remain visible.
8. Confirm the Agent produces its 6–8 line explanation automatically after comparison. It must say when latency is unmeasured and distinguish regional offerings from account quota/spare capacity. Search visually for literal `<thinking>`, `<analysis>` or similar internal tags; none should appear.
9. Test a prompt with an explicit lowest-cost requirement, and another latency-sensitive prompt. With no measured latency, the Agent must not claim a latency winner.
10. Verify Work/Agent selected cloud account context and Configure save behavior still operate as before.

## 6. Keep Terraform disabled until S3 and roles are verified

Before enabling a new Terraform workflow:

- Create a dedicated S3 bucket in the controller account.
- Enable bucket versioning, encryption and Block Public Access.
- Grant the controller role scoped state and S3 lockfile permissions.
- Verify each target account's `IIPPTerraformExecutionRole` trust and restricted permissions, with matching External ID.
- Test a unique S3 state key and native S3 lockfile in Test-Account.
- Confirm policy validation and exact saved-plan checksums.
- Restrict TCP 8501 to the reviewer/demo IP range.

Only then set `TF_STATE_BUCKET`, enable Terraform plan generation, and perform a controlled plan review. Keep `ENABLE_APPROVAL_EXECUTION=false` until a reviewer is ready for the apply test. Do not enable apply solely because the UI renders.

## 7. Rollback

```bash
cd "$HOME/iipp-v14"
./rollback.sh "$(cat "$HOME/inframind-v17-predeploy-stamp.txt")"
```

Do not run broad Docker prune or delete files under `/opt/cloudpilot/data` as part of this UI/recommendation upgrade.
