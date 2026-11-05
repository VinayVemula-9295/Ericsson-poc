# Update notes — v15 · Innominds CloudPilot

## Manager-demo UX

- Platform display name: **Innominds CloudPilot**.
- Innominds logo is retained in the left navigation; the duplicate logo at the right of the page header is removed.
- One bordered top bar is used across Work and Agent. It contains the Cloud provider selector, a provider-specific AWS account / Azure subscription / GCP project selector, and Configure.
- Configure opens in a dialog. The form has provider-specific fields; a successful Test & Save returns a separate success dialog.
- The selected cloud/account is shared by Work and Agent. Changing it updates both workspaces and their live inventory context.
- The Configure form keeps credentials in AWS Secrets Manager; only non-secret connection metadata and the secret ARN are kept in application data.

## Agent recommendations

- Agent responses are constrained to 3–5 concise bullets, ideally under 100 words.
- Recommendations use a verified live inventory snapshot for the selected account.
- Exact match, safe match meeting/exceeding CPU and RAM, and nearest informational-only option are clearly distinguished.
- AWS EC2 specifications and regional offerings are queried live. A regional offering is not a promise of account quota or immediate spare capacity.
- Azure/GCP connections and inventory can be tested, but their Terraform provisioning stacks are not included in this AWS-first POC.

## Terraform create/destroy journey

- New resource creation is initiated in Agent. Terraform prepares a saved plan; create approval remains explicit.
- After successful apply, the created deployment appears in Work → Provisioned resources and lifecycle.
- Work offers **Ask Agent to prepare destroy plan**. It routes the user to Agent.
- Agent prepares and saves a Terraform destroy plan but never applies it. It returns the user to Work.
- Work shows the saved destroy plan, reviewer field, approval token and explicit acknowledgement. Only the exact checksummed plan is applied after approval.
- The destroy action is not shown directly after resource creation in Agent.

## State and cache

- New Terraform states use S3 with unique deployment keys, bucket versioning and native S3 lockfiles.
- A missing `TF_STATE_BUCKET` disables Terraform and prevents local-state fallback for new deployments.
- A shared provider plugin cache and serialized `terraform init` reduce duplicate downloads. Per-deployment `.terraform` working caches are removed after safe lifecycle points; the saved plan and state are retained.
- Existing legacy local state is preserved; no migration is attempted in this release.

## Known live prerequisites

- The user's existing AWS management/controller account is `704298683641`; Demo-Account is `333347372766`; Test-Account is `695729002971`.
- STS AssumeRole and EC2 inventory API access have been verified from the EC2 controller for both member accounts. The selected `ap-south-1` inventory sample returned no EC2 instances.
- The controller previously received `AccessDenied` for `secretsmanager:CreateSecret`. Confirm the scoped Secrets Manager policy is attached before expecting Configure → Test & Save to work.
- The target Terraform execution roles and central versioned S3 state bucket are not yet confirmed. Keep Terraform disabled until these are ready.

## Local verification

- Python compilation: passed.
- Pytest: 62 passed.
- Streamlit rendering, live AWS IAM/Secrets Manager, S3 backend and actual Terraform operations must be verified on the backed-up EC2 host.
