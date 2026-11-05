# EC2 upgrade checklist — InfraMind v16

This is a source/UI upgrade on the EC2 host. It preserves the existing `.env`, `/opt/cloudpilot/data`, provider connection registry, 18 legacy Terraform state files, current container and rollback image. The S3 backend remains mandatory for every new Terraform deployment.

## 1. Confirm backup and rollback prerequisites

Confirm the EBS snapshot is `Completed` in EC2. Then run on the EC2 host:

```bash
cd "$HOME/iipp-v14"
chmod +x backup.sh deploy.sh rollback.sh cleanup.sh
BACKUP_STAMP="$(./backup.sh)"
printf '%s\n' "$BACKUP_STAMP" > "$HOME/inframind-v16-predeploy-stamp.txt"
echo "Backup: $HOME/cloudpilot-backups/$BACKUP_STAMP"
```

Do not continue if the backup does not contain both `project.tar.gz` and `docker-rollback.txt`.

## 2. Upload and inspect the release

Upload from your workstation:

```powershell
scp -i "C:\path\to\your-key.pem" .\inframind-v16-release.zip ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
```

On EC2:

```bash
mkdir -p "$HOME/inframind-v16-staging"
unzip -q "$HOME/inframind-v16-release.zip" -d "$HOME/inframind-v16-staging"
less "$HOME/inframind-v16-staging/inframind-v16/UPDATE-NOTES-v16.md"
less "$HOME/inframind-v16-staging/inframind-v16/IMPLEMENTATION_STATUS.md"
```

## 3. Overlay source without replacing persistent runtime data

```bash
rsync -av \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='terraform_deployments/' \
  --exclude='terraform-plugin-cache/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  "$HOME/inframind-v16-staging/inframind-v16/" \
  "$HOME/iipp-v14/"
```

Do not use `--delete`. This deliberately preserves the existing source files not included in the new ZIP, local runtime metadata and provider connection registry, Terraform states and saved plans.

## 4. Deploy in read-only mode first

Keep provisioning disabled until the S3 bucket, IAM permissions and target execution roles are verified:

```bash
cd "$HOME/iipp-v14"
export AWS_REGION=ap-south-1
export AWS_SECRETS_MANAGER_REGION=ap-south-1
export ENABLE_TERRAFORM_WORKFLOW=false
export ENABLE_APPROVAL_EXECUTION=false
export TF_STATE_REGION=ap-south-1
export TF_STATE_PREFIX=iipp/deployments
export TF_PLUGIN_CACHE_DIR=/opt/cloudpilot/terraform-plugin-cache
CLOUDPILOT_BACKUP_STAMP="$(cat "$HOME/inframind-v16-predeploy-stamp.txt")" ./deploy.sh
```

Verify the container and health endpoint:

```bash
sudo docker ps -a
sudo docker logs --tail 100 cloudpilot-ai
curl -fsS http://127.0.0.1:8501/_stcore/health
```

## 5. Demo acceptance checks

1. Confirm the visible name is **InfraMind — Multi-Cloud Intelligent Provisioning**, with the tagline **Think Intelligent. Provision Anywhere.** The Innominds company logo stays in the left navigation.
2. Confirm one top bar displays Cloud provider, the matching AWS account / Azure subscription / GCP project selector, and Configure.
3. Select AWS → Demo-Account; confirm Work and Agent show the same selected account context.
4. In Work, refresh live inventory and verify STS identity/account ID. A successful empty inventory is valid when no resources exist in that region.
5. Switch to Agent. Confirm the full inventory table and telemetry panel are not displayed there; ask a prompt that requires live account evidence to verify the selected connection is used.
6. Select AWS → Test-Account and verify the shared context changes in both workspaces.
7. Open Configure, use Close, then click other workspace controls. Confirm the dialog does not reopen unexpectedly. Test/save a connection and verify the success dialog appears once.
8. In Agent, try: `I need a Linux VM with 4 vCPU and 5 GiB RAM in ap-south-1. Give me the exact match or nearest safe option.` Verify a short bold recommendation with live AWS specs and regional offering evidence, and no `<thinking>` or internal analysis text.
9. Verify **Exact match check and closest configurations**, **Choose from all discovered AWS configurations that meet the request**, **Detailed comparison and exclusions**, and the recommended-row marker remain available.
10. Verify Work lifecycle displays created deployments only. Destroy path: Work → Ask Agent → Prepare destroy plan → return to Work → review/approve the exact saved destroy plan.

## 6. S3-backed Terraform remains disabled until all prerequisites are true

The Terraform service refuses to generate a new plan without `TF_STATE_BUCKET`. Before enabling it:

- Create a dedicated bucket in the controller account.
- Enable versioning, encryption and Block Public Access.
- Grant the EC2 controller role bucket/prefix read-write and lockfile permissions.
- Create/verify the restricted target execution roles in Demo and Test with matching External IDs.
- Save and test the connection with both Inventory Read Role and Terraform Execution Role ARNs.
- Restrict dashboard port 8501 to the reviewer/demo IP range.
- Run `terraform init`/plan in Test and verify the exact per-deployment key under `iipp/deployments/`.

Only then set `TF_STATE_BUCKET`, `ENABLE_TERRAFORM_WORKFLOW=true` and redeploy. Keep `ENABLE_APPROVAL_EXECUTION=false` until you are ready for the controlled approval test. Never turn on provisioning solely because the UI renders.

## 7. Rollback

```bash
cd "$HOME/iipp-v14"
./rollback.sh "$(cat "$HOME/inframind-v16-predeploy-stamp.txt")"
```

Keep the previous container and rollback image until the demo is accepted. Do not run broad Docker prune or delete anything under `/opt/cloudpilot/data` as part of this UI upgrade.
