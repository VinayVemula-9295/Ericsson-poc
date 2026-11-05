# Application guidance

## Project and scope

This directory is the application root. Run the commands below from here.
The product is **Intelligent Provisioning Platform**, a Python/Streamlit application
for cloud inventory, infrastructure recommendations, and reviewed Terraform
provisioning. The Docker runtime uses Python 3.12.

AWS is the only end-to-end provisioning provider. Azure and GCP support connection
configuration and live inventory, but their comparison prices are public-catalogue
references and their Terraform provisioning stacks are not implemented. Kubernetes
and GitOps are outside the legacy architecture. The extended POC adds Linux/Windows
catalogue comparisons, evidence policy, illustrative Kubernetes sizing and template/
saved-plan generation. New-target execution remains disabled; see
`docs/linux-windows-poc.md` for verified coverage and remaining validation.

Read `README.md` for architecture and configuration, `UPDATE-NOTES-v19.md` for v19
behavior, and the relevant topic guide in `docs/` before changing a subsystem.
Check the current code when older release documentation differs from v19 behavior.

## Code map

| Area | Main entry points |
| --- | --- |
| Streamlit Work/Agent pages and provider configuration | `app.py`, `provider_ui.py` |
| Agent tools and model-provider integration | `agent.py`, `llm_provider.py` |
| Request parsing, hardware fit, and ranking | `infrastructure_request.py`, `instance_matching.py`, `recommendation_engine.py` |
| Comparison, cloud data, and pricing | `cloud_comparison.py`, `aws_data.py`, `cloud_adapters/` |
| Connections, credentials, and inventory | `provider_connections.py` |
| Policy and monitoring | `policy_engine.py`, `policy_templates/aws/`, `scaling_policy.py`, `trend.py` |
| Terraform lifecycle and AWS demo stack | `terraform_service.py`, `terraform/stacks/aws_demo/` |
| Disabled legacy direct EC2 execution | `provisioning.py` |
| Regression tests | `tests/` |

## Local development and verification

Use an isolated Python environment. Install development dependencies with:

```sh
python -m pip install -r requirements-dev.txt
```

Launch the dashboard with:

```sh
python -m streamlit run app.py
```

`terraform_service.py` imports Unix `fcntl`, as do Terraform safety tests. Use
Linux, WSL, or the Linux container environment for the full application and test
suite; native Windows cannot run these paths unchanged. The Dockerfile includes
Terraform, while a local pip installation does not install the Terraform CLI.

Run regression tests with:

```sh
python -m pytest
```

For focused checks, select the relevant suite:

```sh
python -m pytest tests/test_multicloud_features.py tests/test_aws_live_pricing.py
python -m pytest tests/test_app_helpers.py tests/test_agent_ui.py
python -m pytest tests/test_terraform_safety.py
```

Keep test cloud calls mocked and filesystem writes in temporary directories.
Run relevant tests for code changes and the full suite for changes spanning
recommendations, provider connections, or Terraform safety. Add regression coverage
for changed behavior. Documentation-only changes need reference and content review.
Report the checks actually run and any environment limitations. Unit tests do not
establish live IAM, cloud API, Bedrock, browser, or Terraform deployment success.

## Behavior to preserve

- Prefer eligible exact CPU/RAM matches; otherwise require both requested minimums.
  Retain every exact AWS hardware profile even when it exceeds the shortlist limit.
- Keep Selectable, Recommended, and Optimal distinct. Recommended uses the chosen
  deterministic strategy within the preferred hardware-fit tier; Optimal uses the
  lowest eligible price in that tier. Model explanations must follow those results.
- Use live AWS specification and regional offering checks. An offering is not proof
  of quota or spare capacity. Do not substitute hardcoded AWS types or fixture prices
  when discovery or pricing fails.
- Preserve AWS price source and actual UTC fetch time. Monthly public compute
  estimates use hourly price times 730 and are not final bills. Label Azure/GCP
  catalogue estimates and unavailable evidence accurately. Claim latency winners
  only with comparable measured evidence.
- Work and Agent share the selected cloud connection. Failed inventory refreshes
  must surface unavailable data rather than demo inventory or stale verified results.
- Keep explanations concise and strip internal reasoning tags before rendering.

## Provisioning, secrets, and deployment

- Preserve deterministic policy checks, explicit human approval, audit events, and
  checksum verification of the exact saved plan for both apply and destroy. Preparing
  a plan must not execute it. Do not restore direct EC2 stop/modify/start resizing.
- Keep `ENABLE_TERRAFORM_WORKFLOW` and `ENABLE_APPROVAL_EXECUTION` disabled by
  default. New Terraform deployments require `TF_STATE_BUCKET`, S3 native locking,
  and the configured backend. Preserve the global provider-cache init lock and
  per-deployment locking. Do not silently fall back to local state.
- Keep controller credentials for the S3 backend separate from the target execution
  role. Preserve target-account validation and External ID checks before STS calls.
- Store connection credentials in Secrets Manager; the local connection registry
  contains non-secret metadata and a secret ARN. Never include credentials, API keys,
  approval tokens, or secret values in code, test fixtures, documentation, or logs.
  Use `CONFIGURATION.example.env` to discover settings without exposing real secrets.
- Treat local verification separately from deployment and cloud mutations. Run
  deployment, IAM/account bootstrap, or live apply/destroy only when requested as
  part of the task. Do not recreate the documented existing Demo/Test AWS accounts.
- For a deployment task, follow `docs/deploy-v19.md` and
  `SAFE-DEPLOY-AND-CLEANUP.md`. Preserve `.env`, runtime data, connection metadata,
  Terraform state, saved plans, approval tokens, and rollback images/containers.
  Do not use a release overlay with `rsync --delete`.
- Preserve existing runtime paths, Docker identifiers, IAM role names, and the
  `innominds-iipp` Secrets Manager namespace as compatibility identifiers; a product
  branding change alone does not authorize renaming them.
- Consult `docs/terraform-state-and-cache.md`, `docs/aws-account-bootstrap.md`,
  `docs/aws-live-pricing.md`, and `docs/provider-configuration.md` for subsystem details.
