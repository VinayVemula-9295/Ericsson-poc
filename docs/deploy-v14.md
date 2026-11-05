# EC2 upgrade and first-run checklist — IIPP v14

This guide overlays the release into the existing checkout without deleting the current `.env`, `/opt/cloudpilot/data`, local Terraform state, or prior Docker image. The EBS snapshot has been reported as created; check that its status is **Completed** in EC2 before continuing.

## A. Create a source/container rollback backup

Run this on the EC2 host before overlaying any code:

```bash
cd /home/ubuntu/cloudpilot-ericsson-poc
chmod +x backup.sh deploy.sh rollback.sh cleanup.sh
BACKUP_STAMP="$(./backup.sh)"
echo "Saved rollback stamp: $BACKUP_STAMP"
printf '%s\n' "$BACKUP_STAMP" > "$HOME/iipp-v14-predeploy-stamp.txt"
```

Keep the value of `BACKUP_STAMP`. The backup must create both `project.tar.gz` and `docker-rollback.txt` under `~/cloudpilot-backups/<stamp>/`.

## B. Upload and inspect the ZIP

From the local Windows terminal, upload the downloaded ZIP with `scp` (replace the key path and EC2 public IP):

```powershell
scp -i "C:\path\to\your-key.pem" .\innominds-iipp-v14-release.zip ubuntu@<EC2_PUBLIC_IP>:/home/ubuntu/
```

On EC2:

```bash
mkdir -p "$HOME/iipp-v14-staging"
unzip -q "$HOME/innominds-iipp-v14-release.zip" -d "$HOME/iipp-v14-staging"
find "$HOME/iipp-v14-staging/iipp-v14" -maxdepth 2 -type f | head -60
```

Review the version comparison and implementation status before applying it:

```bash
less "$HOME/iipp-v14-staging/iipp-v14/VERSION-COMPARISON.md"
less "$HOME/iipp-v14-staging/iipp-v14/IMPLEMENTATION_STATUS.md"
```

## C. Overlay code without replacing runtime data

```bash
rsync -av \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='terraform_deployments/' \
  --exclude='terraform-plugin-cache/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  "$HOME/iipp-v14-staging/iipp-v14/" \
  "$HOME/cloudpilot-ericsson-poc/"
```

This overlays application source and scripts, but does not use `--delete`; existing files not present in the ZIP remain. It excludes secrets and runtime state from the source staging path. Confirm `.env` still exists if your deployment uses one.

## D. First deploy with write features disabled

Keep these flags disabled while validating the new UI and connection forms:

```bash
cd /home/ubuntu/cloudpilot-ericsson-poc
export AWS_REGION=ap-south-1
export AWS_SECRETS_MANAGER_REGION=ap-south-1
export ENABLE_TERRAFORM_WORKFLOW=false
export ENABLE_APPROVAL_EXECUTION=false
export TF_STATE_REGION=ap-south-1
export TF_STATE_PREFIX=iipp/deployments
export TF_PLUGIN_CACHE_DIR=/opt/cloudpilot/terraform-plugin-cache
# Only set this to the separate QuickCart/workload EC2 instance, never the IIPP controller.
# export TARGET_INSTANCE_ID='<quickcart-workload-instance-id>'
CLOUDPILOT_BACKUP_STAMP="$(cat "$HOME/iipp-v14-predeploy-stamp.txt")" ./deploy.sh
```

The new image is built while the old container image is retained. The script mounts `/opt/cloudpilot/data` and the shared Terraform plugin cache; then it health-checks Streamlit and restores the previous container if startup fails.

Verify:

```bash
sudo docker ps --filter name=cloudpilot-ai
sudo docker logs --tail 100 cloudpilot-ai
curl -fsS http://127.0.0.1:8501/_stcore/health
```

Open the dashboard through the approved network path. Restrict EC2 security-group ingress on TCP 8501 to the reviewer/demo IP address; do not make it public.

## E. Provision cloud prerequisites before enabling Terraform

1. An AWS Organizations administrator creates the POC and TEST member accounts using [`aws-account-bootstrap.md`](aws-account-bootstrap.md).
2. Attach the generated controller role policy to `CloudPilotReadTargetMetricsRole` from an IAM administrator.
3. Create the central S3 bucket in the controller account using `scripts/bootstrap_iipp_state_bucket.sh` under an administrative AWS identity, for example:

   ```bash
   export TF_STATE_BUCKET='<globally-unique-iipp-state-bucket>'
   export TF_STATE_REGION=ap-south-1
   ./scripts/bootstrap_iipp_state_bucket.sh                 # dry run
   ./scripts/bootstrap_iipp_state_bucket.sh --execute       # creates/configures bucket
   ```

4. Confirm versioning is `Enabled`, encryption is configured and public access is blocked.
5. Create the generated controller policy with `scripts/generate_iipp_controller_policy.py` and attach it to the controller role. It scopes S3 access to `iipp/deployments/*`, Secrets Manager access to the IIPP connection secret prefix and STS AssumeRole to the two target roles in both accounts.
6. Use Configure to save and **Test connection** for `AWS-POC` and `AWS-TEST`.

Do not enable write flags until the role, bucket, connection and security-group checks are successful.

## F. Enable Terraform only after S3 connectivity is verified

For a controlled deployment shell (or your protected deploy environment):

```bash
export TF_STATE_BUCKET='<globally-unique-iipp-state-bucket>'
export TF_STATE_REGION=ap-south-1
export TF_STATE_PREFIX=iipp/deployments
export ENABLE_TERRAFORM_WORKFLOW=true
export ENABLE_APPROVAL_EXECUTION=false
CLOUDPILOT_BACKUP_STAMP="$(cat "$HOME/iipp-v14-predeploy-stamp.txt")" ./deploy.sh
```

This restarts the container with the Terraform UI enabled. Before approving a POC plan, verify the actual selected target account ID, region, T3 instance family, AMI, subnet, security group and plan diff. Apply is an explicit second action requiring the reviewer token and acknowledgement.

## G. Roll back

Use the stamp from step A:

```bash
cd /home/ubuntu/cloudpilot-ericsson-poc
./rollback.sh "$(cat "$HOME/iipp-v14-predeploy-stamp.txt")"
```

Do not run `docker system prune`, `docker image prune`, or broad `rm -rf` commands as part of this upgrade. Retain previous images until the release is accepted. The legacy local Terraform state files remain in `/opt/cloudpilot/data/terraform_deployments`; do not delete them in this release.
