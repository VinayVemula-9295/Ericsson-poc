# Terraform state and cache strategy

## New deployments

- State bucket: central S3 bucket in the controller account, not in each workload account.
- Legacy server state key: `iipp/deployments/<deployment-id>/terraform.tfstate`.
- Current Agent server/EKS state key: `iipp/poc/<deployment-id>/terraform.tfstate`.
- Controller IAM must cover both prefixes. See [two-account setup](two-account-terraform-setup.md).
- Lock: `use_lockfile = true` (S3 `.tflock` object).
- Recovery: S3 versioning enabled.
- Encryption: S3 server-side encryption enabled; bucket blocks public access and denies non-TLS requests.
- Authentication: S3 backend uses the EC2 controller IAM role. The Terraform AWS provider assumes the target account's Terraform execution role.
- Credentials are not embedded in backend configuration. Do not pass access key/secret values via `-backend-config`.

The code fails closed for new deployments if `TF_STATE_BUCKET` is empty. The bucket and IAM policy must be created before enabling `ENABLE_TERRAFORM_WORKFLOW=true`.

## Shared provider cache and per-deployment cleanup

The host path `/opt/cloudpilot/terraform-plugin-cache` is mounted into Docker as `/terraform-plugin-cache`, and `TF_PLUGIN_CACHE_DIR` points there. This lets Terraform reuse provider downloads rather than repeatedly fetching the same large provider package. Terraform may also link provider files from the cache into a working directory when the platform supports it; the cache is not a promise that every working directory is tiny.

The deployment service therefore also manages the `.terraform` working directory lifecycle:

1. During `terraform init`, `validate` and `plan`, the working directory exists temporarily.
2. Terraform writes `.terraform.lock.hcl` and the saved plan `tfplan` into the deployment directory.
3. After the plan summary and checksum are recorded, IIPP removes only the regenerable `.terraform/` directory. The saved plan, lock file, Terraform source, deployment metadata and S3 state are retained.
4. At reviewer approval, IIPP runs `terraform init` again against the same S3 key using the retained lock file in read-only mode, then applies the exact checksum-verified plan. It does not generate a new plan.
5. After apply completes, IIPP removes `.terraform/` again. Destroy planning and destroy approval use the same reinitialize/cleanup cycle.

Only the short period while Terraform is initializing/planning/applying requires the per-deployment working directory. The shared provider cache can retain one or more provider versions and should be reviewed during maintenance. Do not remove it while a Terraform operation is active. Terraform's plugin cache is not guaranteed concurrency-safe, so IIPP serializes `terraform init` operations.

See the [Terraform S3 backend documentation](https://developer.hashicorp.com/terraform/language/backend/s3) for S3 locking and state permissions, and the [Terraform CLI configuration documentation](https://developer.hashicorp.com/terraform/cli/config/config-file) for provider cache and mirror behavior.

## Historical deployments

The 18 existing local `terraform.tfstate` files in `/opt/cloudpilot/data/terraform_deployments` remain preserved. This release does not silently migrate or delete them. Existing local-state deployment records may be reinitialized locally for destroy-plan operations; they are not eligible for apply via the new S3-only flow. Pending old approval plans must be regenerated because the prior cleanup removed their local `.terraform` working directories.

Only newly prepared deployments use S3. A future controlled state migration needs a separate per-deployment process (`terraform init -migrate-state` or equivalent), backup, state lineage/serial checks, remote verification and destroy workflow validation. Do not simply copy a state file to S3 and delete the original.
