# CloudPilot v8 — Implementation Update

## Requested workflow and UI

- Left navigation is limited to **Work** and **Agent**. The Innominds logo remains above the CloudPilot title in the sidebar.
- Agent accepts a natural-language infrastructure request. The parser recognizes CPU/RAM forms including the common `4 vpc 16gb ram` phrasing and records which defaults it used.
- Requested minimum CPU/RAM selects a curated VM profile for each cloud before calling public pricing adapters. For 4 vCPU / 16 GiB Linux, the curated examples are AWS `m6i.xlarge`, Azure `Standard_D4s_v5`, and GCP `n2-standard-4`.
- The comparison separates public prices from connected-account status. Azure and GCP remain pricing-only without user-account credentials and provisioning adapters. It does not fabricate price, capacity, latency, or account telemetry.
- AWS can be selected for a creation workflow when actual EC2 specifications and the regional offering are verified, even if the AWS public-price lookup is unavailable. In that case price remains N/A and is not scored.
- The AWS create form is embedded in Agent and includes region, instance type, Linux AMI ID, subnet ID, security-group ID, owner/TTL tags and optional instance profile. The create button stays visible while disabled configuration is diagnosed; clicking it reports why a plan cannot yet be prepared.
- Terraform validates that the selected instance type is offered in the **Availability Zone of the selected subnet**, confirms the AMI is Linux/x86_64, checks the subnet and security group share a VPC, and verifies requested CPU/RAM.
- Resource creation remains a reviewer-gated saved-plan workflow: prepare plan → inspect exact saved plan → token + explicit approval → apply checksummed plan. No create/apply occurs on prompt submission or dashboard refresh.

## Provider-price adapter reliability

- GCP uses the documented Cloud Billing Catalog v1 service child-resource route `/v1/services/{serviceId}/skus`, with API-key configuration and explicit errors when the API key or usable SKU pricing is unavailable.
- When both a city-specific GCP N2 SKU and a broad regional SKU are returned, the adapter prefers a configured city-specific price (for example, Mumbai for `asia-south1`). Remaining distinct prices are not guessed.
- API errors must not expose the Google API key from the query string.
- AWS and Azure use live/public provider APIs for applicable prices and retain missing price as unavailable rather than zero.

## Validation completed in this environment

Commands executed from the project root:

```bash
python -m py_compile app.py agent.py aws_data.py cloud_comparison.py infrastructure_request.py recommendation_engine.py terraform_service.py cloud_adapters/aws.py cloud_adapters/azure.py cloud_adapters/gcp.py
python -m pytest -q
bash -n deploy.sh scripts/create_cpu_alarms.sh
```

Results: **44 tests passed**; Python compilation passed; shell syntax checks passed. Tests use fixtures/mocks for provider calls and Terraform safety; there was no live cloud provisioning, no live AWS-account validation, and no interactive Streamlit browser test in this environment (Streamlit is not installed in the local test interpreter).

## Operational prerequisites and limitations

- The EC2 host previously failed Docker build with `No space left on device` on a 6–7 GiB root volume. Expand the EBS root volume to about **20 GiB** and grow the partition/filesystem before building this package again. `sudo docker builder prune -af` can also reclaim unused build cache, but do not prune volumes or remove the running image.
- AWS EC2 read permissions are needed for `DescribeImages`, `DescribeSubnets`, `DescribeSecurityGroups`, `DescribeInstanceTypeOfferings`, and `DescribeInstanceTypes`. Creation requires the separately reviewed Terraform policy.
- Public GCP prices require `GOOGLE_CLOUD_BILLING_API_KEY` and the Cloud Billing API enabled. Public catalog access is not a connection to a GCP workload account.
- Azure/GCP telemetry and provisioning are unavailable until connected accounts and adapters are implemented. Lowest Latency cannot choose a winner without comparable measured latency.
- Terraform uses local state in this POC under the persistent data directory. Production deployment requires an encrypted, versioned, locking remote backend and least-privilege IAM review.
- Before enabling Terraform planning, restrict TCP 8501 to authorized reviewer IPs and review the permissions. Enable the new-instance plan flow with `ENABLE_TERRAFORM_WORKFLOW=true`; leave the separate existing-instance resize flow disabled unless it has been reviewed and explicitly approved.
