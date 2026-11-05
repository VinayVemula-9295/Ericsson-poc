# CloudPilot v9 — Work cost view and Agent cleanup

## Work
- Adds connection cards for AWS, Azure, and GCP.
- Adds an explicit table of verified running AWS EC2 resources with per-instance estimated monthly compute cost where live public pricing is available.
- Keeps AWS Cost Explorer month-to-date charges grouped by AWS service separate from the forward EC2 run-rate.
- Shows Azure/GCP account inventory and monthly account run-rate as unavailable/not connected until real account integrations are configured.

## Agent
- Removes the Connected AWS workload monitor section and its duplicate telemetry polling/threshold evaluation from the Agent page. Live workload telemetry remains under Work.
- Removes the Recommendation strategy and Monthly compute budget form controls; defaults and preferences are inferred from the natural-language prompt.
- Marks comparison recommendations provisional when any provider price/specification data is unavailable.
- Shows curated AWS/GCP profile specifications when APIs cannot return data, while keeping prices unavailable and not treating those profiles as live verification.
- Shows AWS IAM permission remediation for `DescribeInstanceTypeOfferings` / `DescribeInstanceTypes` and GCP public pricing setup instructions for missing `GOOGLE_CLOUD_BILLING_API_KEY`.

## Validation and limitations
- 48 unit/static tests pass; Python compilation passes.
- AWS administrator action is required to attach the EC2 read-only policy to the live role.
- GCP public catalog pricing remains N/A until the API is enabled and an appropriate API key is configured in the container deployment.
- Azure/GCP account resource inventory and provisioning are not implemented/connected; no mock services are shown as live.
- No live provider API tests or cloud resource creation were run.
