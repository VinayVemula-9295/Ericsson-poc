# Intelligent Provisioning Platform v18 implementation status

**Package status:** source bundle prepared and locally unit-tested. This release is not yet deployed to the user's EC2 host.

## Implemented in the v18 source package

- Product name **Intelligent Provisioning Platform — Multi-Cloud Intelligent Provisioning** with tagline **Think Intelligent. Provision Anywhere.** The Innominds logo remains in the left navigation only.
- One bordered global top bar shared by Work and Agent: cloud selector, provider-specific AWS account / Azure subscription / GCP project selector, and Configure action.
- Configure opens a modal with provider-specific fields. Successful validation and save closes that modal and displays a separate success dialog.
- AWS AssumeRole authentication, External ID validation, and Secrets Manager credential storage; Azure service-principal and GCP service-account configuration flows.
- The selected connection is shared by Work and Agent. The full live inventory panel is displayed in Work only; Agent receives fresh selected-account inventory evidence and automatically emits a grounded 6–8 line explanation after infrastructure comparison.
- Hardware recommendations distinguish exact fit, a profile meeting/exceeding requested CPU and RAM, and nearest informational-only alternatives. AWS type specifications and regional offerings are checked from live EC2 APIs; offerings do not prove quota or spare capacity.
- Policy-first Terraform-only provisioning, reviewer-gated saved create plans, exact saved-plan checksum validation, and audit trail.
- Destroy workflow: Work requests Agent to prepare a saved Terraform destroy plan; Agent prepares but does not apply it; Work receives the saved plan for explicit reviewer approval and apply.
- AWS is the only end-to-end Terraform resource provider in this POC. Azure and GCP have configuration and live inventory paths, but do not have Terraform resource stacks in this release.
- New Terraform deployments require a dedicated versioned S3 state bucket, unique deployment keys, S3 native lockfiles, and scoped controller access. No local state fallback is allowed for new deployments.
- A shared Terraform provider plugin cache and serialized `terraform init` reduce repeated provider downloads. Per-deployment `.terraform/` working caches are removed after plan creation and lifecycle operations while plans and state are retained.
- Backup, safe deployment, health-check and rollback scripts are included. Legacy direct EC2 API resize/stop/start actions are disabled.

## Current user AWS environment — known status

| Item | Known status |
|---|---|
| Management/controller account | Naresh-Acc · `704298683641` |
| POC member account | Demo-Account · `333347372766` |
| TEST member account | Test-Account · `695729002971` |
| Organization OU | User reports both member accounts are under `IIPP-Sandbox` |
| Member budgets | User reports monthly budgets created for the member accounts |
| EC2 controller identity | `CloudPilotReadTargetMetricsRole` verified on EC2 |
| Inventory role assumption | STS AssumeRole verified for Demo and Test |
| EC2 inventory | DescribeInstances completed without authorization errors; the sampled region returned no instances |
| Secrets Manager save permission | Previously denied `secretsmanager:CreateSecret`; controller policy update still needs confirmation |
| Terraform execution roles | Not confirmed created/configured in both member accounts |
| Central S3 state bucket | Not confirmed created/configured |
| v18 deployment | Not yet confirmed on EC2; source package only until deployed and browser-validated |

## Verification in the local release build

- Python compilation: passed.
- Unit tests: **82 passed**.
- Shell syntax, JSON policy parsing and ZIP integrity checks: run as part of packaging.
- Streamlit UI was not launched in this build environment. Validate the modal and page interactions on EC2 after deployment.
- Terraform CLI and Docker were unavailable in the local build environment; Terraform fmt/validate/plan/apply/destroy and a live Docker build were not run here.

## EC2 deployment and live prerequisites

1. Confirm the EBS snapshot is `Completed` and create a fresh application/data/container backup.
2. Upload `inframind-v18-release.zip` and follow [`docs/deploy-v18.md`](docs/deploy-v18.md), preserving `.env`, `/opt/cloudpilot/data`, Terraform state and previous Docker images.
3. Deploy with `ENABLE_TERRAFORM_WORKFLOW=false` and `ENABLE_APPROVAL_EXECUTION=false` first.
4. Confirm the controller has narrowly scoped Secrets Manager access for `innominds-iipp/connections/*`; verify Demo and Test connection tests from the UI.
5. Create a dedicated S3 state bucket in account `704298683641`; enable versioning, encryption and Block Public Access. Attach prefix-scoped state and lockfile permissions to the controller role.
6. Create/review `IIPPTerraformExecutionRole` in Demo and Test with each account's matching External ID. Do not use AdministratorAccess.
7. Restrict TCP 8501 to the authorized demo/reviewer IP range.
8. Only after S3 init/plan checks, IAM and target roles pass, enable Terraform plan generation. Keep apply approval disabled until a reviewer is ready to run a controlled test.
9. Test create plan → explicit approval → apply in Test first. Test destroy plan generation in Agent and final destroy-plan approval in Work.

## Limitations and safety notes

- This ZIP does not create AWS accounts, budgets, IAM roles, S3 buckets or cloud resources; those are separate AWS administration tasks.
- Existing local Terraform state files are preserved. This release does not migrate or delete legacy states.
- The current AWS Terraform stack creates a new demo EC2 instance; it does not import or resize the existing QuickCart workload.
- Azure/GCP resource provisioning is not implemented in this release.
- AWS Budget notifications are alerts, not hard spending caps.
- Do not enable Terraform apply until versioned S3 state, controller IAM, target execution roles, reviewed policies and dashboard ingress restrictions are verified.

## v18 recommendation and Agent improvements

- Live provider inventory and its CloudWatch telemetry panel render only in Work. Agent receives selected-account inventory as evidence for prompt responses. AWS Price List Query API prices are refreshed on each new comparison and passed into the explanation.
- Configure and success dialogs use one-shot open events; explicit Close / Continue actions prevent persistent dialog state from resurfacing on unrelated button clicks.
- Agent comparison explanations are limited to 6–8 short lines with a bold recommendation; internal reasoning tags are removed before rendering.
- The comparison continues to show **Exact match check and closest configurations**, **Choose from all discovered AWS configurations that meet the request**, and **Detailed comparison and exclusions**. Recommended / nearest safe AWS configuration is highlighted above the comparison.


## v18 recommendation acceptance criteria

- Exact hardware matches form the preferred candidate tier when they have usable, comparable USD price evidence and required provider evidence. If no eligible exact match exists, candidates must meet or exceed both minimum vCPU and RAM values.
- Within the preferred tier, Balanced ranks compute cost (65%), hardware fit (20%) and availability evidence (15%); measured latency contributes only when comparable values are available. Lowest Cost, Lowest Latency and Best Fit for Workload retain explicit strategies.
- Optimal is the lowest-priced eligible candidate within the preferred exact/feasible tier. Recommended is the strategy-ranked choice. Both are labelled in the cards, table and candidate selector.
- Agent produces a 6–8 line explanation after the comparison and consumes the deterministic result as read-only evidence. The LLM cannot alter the ranking or perform provisioning. Internal reasoning tags are removed before rendering.
- Candidate pricing and availability evidence are displayed separately. Regional AWS offering is not live launch capacity; Azure/GCP public pricing is not proof of connected account inventory.
- Regression fixture for 4 vCPU / 9 GiB using supplied sample prices verifies AWS `m5a.xlarge` at USD 81.03/month ranks ahead of AWS `c5n.xlarge` at USD 157.68/month when both are verified eligible candidates. The fixture validates ranking logic, not live current market pricing.


## v18 acceptance scenario

For the user-provided 4 vCPU / 9 GiB sample, if live AWS EC2 offering data and the current price lookup verify the same values shown in the screenshots, `m5a.xlarge` (USD 81.03/month) must rank ahead of `c5n.xlarge` (USD 157.68/month), because both satisfy the requested minimums and `m5a.xlarge` has the lower compute estimate. The regression test uses fixture values and does not constitute a live AWS price validation.

The UI labels both the strategy-selected candidate (`Recommended`) and the lowest-priced eligible candidate (`Optimal · lowest cost`), retains the exact-match and closest-option sections, and exposes the returned priced options that meet both CPU and RAM minimums in a selector. Azure/GCP catalogue entries may be compared but remain explicitly distinguished from verified account capacity and AWS-only provisioning.


## v18 live AWS Price List integration

- New Agent comparison submissions clear both the Streamlit cloud-option cache and the internal AWS Price List cache before discovery.
- AWS prices are queried from `AmazonEC2:GetProducts` with instance type and region filters plus Linux/shared/On-Demand constraints.
- Successful rates record actual retrieval time, effective date, publication date, product SKU and Price List endpoint region.
- A bounded 300-second default TTL prevents indefinite reuse between requests; failed/ambiguous lookups are never cached.
- AWS price unavailable means no AWS price-based candidate ranking; no screenshot/example/hardcoded AWS price is substituted.
- Controller policy generator includes `pricing:GetProducts`; `iam-live-pricing-policy.json` is a minimal policy for `CloudPilotReadTargetMetricsRole` in Naresh-Acc.
- Agent receives the fetched rate and timestamps as deterministic evidence. LLM output explains but cannot override the deterministic price ranking.
- Build tests cover live-price parsing, region filtering, 730-hour monthly estimate, freshness/cache behavior, ambiguous-rate rejection and Agent evidence propagation.

**Not live-verified here:** The package build environment does not have the user's AWS credentials. After attaching `pricing:GetProducts` to the controller role, run the verification snippet in `docs/aws-live-pricing.md` from the EC2 host. The ZIP is not an EC2 deployment and no real AWS Price List call was made during build validation.


## v19 parser and selection refinement

- Parser recognizes `Vpcu`/`vPCU` common spelling variants and RAM expressions such as `8Gib Server` or `8GB VM`; explicit values do not fall back to defaults.
- Live AWS candidate shortlist always retains all exact CPU/RAM profiles returned by EC2, even when there are more exact profiles than the configured shortlist limit.
- Agent comparison shows per-provider Selectable, Recommended and Optimal cards. Exact profiles are the preferred tier; only when none exist are meets-minimum profiles used.
- Detailed comparison and exclusions is placed before the all-returned-priced-configurations selector.
- The left sidebar Platform / Terraform-only provisioning / Human approval / tagline / Controller region block is removed.
- Azure/GCP remain comparison-only; only AWS candidate actions can continue to the current Terraform plan flow.
- Local validation: 84 tests passed and Python compilation passed. Browser and live AWS checks remain deployment-time acceptance tests.
