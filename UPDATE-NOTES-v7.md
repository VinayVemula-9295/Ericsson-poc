# CloudPilot v7 implementation notes

This iteration hardens and documents the existing AWS-first multi-cloud POC rather than replacing it with an unrelated app.

## Included

- Existing Work / Agent views, Innominds logo, Cloud Comparison, Cloud Connections, deployment/approval history, and settings are retained.
- Read-only provider adapters and normalized pricing comparisons for AWS, Azure Retail Prices, and GCP Billing Catalog.
- Deterministic eligibility checks and separate Lowest Cost, Balanced, Best Fit for Workload, and Lowest Latency strategies. Missing prices and unmeasured latency are not filled with invented values.
- Bedrock remains the default LLM. Optional provider defaults are documented. LLM tool access remains allowlisted and schema-validated.
- AWS API calls have bounded connect/read timeouts and retry settings. Public pricing reads retry transient throttling/server errors with bounds.
- Provider HTTP error details redact configured secrets before displaying error messages.
- Terraform create-new-instance workflow records state transitions, saves and hashes plans, requires reviewer approval, checks plan age and integrity, and never applies in startup or refresh handlers.
- Destroy-plan preparation can be retried after a previous destroy-plan failure; it still requires a saved plan and a separate approval before destroy apply.
- Added tests for import path, secret redaction, transient Azure pricing retry, normalized warning handling, Terraform state history, and retrying a failed destroy-plan generation.

## Deployment guardrails

- Terraform new-instance workflow is disabled by default (`ENABLE_TERRAFORM_WORKFLOW=false`).
- The separate EC2 stop/modify/start resize workflow is disabled by default (`ENABLE_APPROVAL_EXECUTION=false`) and can interrupt the target workload.
- Tests mock cloud calls. This iteration did not provision, resize, stop, or destroy any AWS resource.
- Review IAM example policies for account/region scoping before enabling write flows. Restrict Streamlit port 8501 to approved reviewer IPs.

## Not complete / not verified

- Azure and GCP adapters are public-pricing only; live account telemetry and provisioning are not connected.
- Live AWS IAM, CloudWatch, Cost Explorer, Pricing API, Bedrock, and SNS delivery were not verifiable from this local build environment.
- Cross-provider prices are narrow demo SKU mappings, not an exhaustive catalog search or guaranteed full TCO. Azure/GCP options require live API validation.
- Comparable end-to-end latency measurements are not connected; Lowest Latency correctly remains unranked without them.
- Terraform state currently uses local persistent storage; production must move to protected encrypted/versioned/locked remote state before deployment use.
- Terraform apply/destroy has not been tested against a live account. It must only be run by an authorized reviewer with IAM, network, cost, and cleanup controls validated.
- Gemini adapter still uses the supported legacy GenerateContent API. It should migrate to Google's newer Interactions API and be live-tested before production use.
- Model credentials/API quotas were not live-tested. Bedrock is the default and needs an appropriate IAM role and model access.

## Verify locally

```bash
python -m py_compile app.py agent.py aws_data.py cloud_comparison.py llm_provider.py provisioning.py recommendation_engine.py scaling_policy.py terraform_service.py trend.py cloud_adapters/*.py
pytest -q
bash -n deploy.sh scripts/create_cpu_alarms.sh
python - <<'PYJSON'
import json, pathlib
for path in pathlib.Path('.').glob('iam-*.json'):
    json.loads(path.read_text())
print('IAM example JSON valid')
PYJSON
```
