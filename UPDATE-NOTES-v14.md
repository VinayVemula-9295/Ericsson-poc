# Update notes — v14 (IIPP)

- Rebranded the user-facing product as **Innominds Intelligent Provisioning Platform (IIPP)** and packaged the supplied Innominds logo and original architecture reference.
- Reworked the target flow around request → reasoning layer → deterministic policy + Bedrock reasoning + live telemetry → Terraform plan → human approval → exact Terraform apply → cloud resources + audit. Kubernetes and GitOps are excluded.
- Added global Configure panel with AWS, Azure and GCP forms and Test/Save behavior.
- Added provider connection storage using AWS Secrets Manager; local application data contains connection metadata and secret ARNs, not clear-text credentials. A failed inventory refresh marks a connection ERROR and clears stale identity fields, preventing stale account data from being treated as live.
- Added AWS STS/EC2, Azure Resource Manager and Google Compute Engine live connection tests and on-demand Work inventory refresh.
- Enforced selected AWS target connection and target roles for provisioning; controller account is not a default Terraform target. Removed the legacy direct EC2 API resize approval path so all IIPP resource changes follow Terraform saved-plan and reviewer approval.
- Added deterministic app-side POC policy checks and account-specific environment isolation. Target IAM execution role allows only tagged T3 instances in the approved region and environment.
- Made new Terraform deployments fail closed without `TF_STATE_BUCKET`; S3 state uses unique deployment keys, versioning expectation and native S3 lockfile. Existing historical local states remain untouched.
- Added shared provider cache and global `terraform init` serialization to avoid repeated provider binaries and unsafe concurrent cache updates. Regenerable per-deployment `.terraform` folders are cleaned after plan/apply/destroy, and a failed approval-time init is cleaned before retry.
- Added scripts for Organizations member accounts, target roles, monthly budget alerts, central bucket setup and controller IAM policy generation.
- Preserved safe Docker backup/deploy/health-check/rollback scripts and current technical identifiers for a compatible EC2 overlay.

Limitations: the source package cannot create accounts or IAM permissions without an administrator identity; live AWS/S3/Secrets Manager/Bedrock calls are not validated in the sandbox. Azure/GCP inventory is included but their Terraform provisioning stacks are not part of this AWS-first POC release.
