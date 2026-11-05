# Agent prompt and Terraform workflow

The [Demo provisioning update](demo-provisioning-verification.md) supersedes the
older tier-unavailable and pod-only sizing descriptions below. Explicit worker
counts are preserved; tiers share an eligible configuration when an upgrade is
absent. Plan dialogs include Edit, and successful apply shows safe resource details.

Agent now opens directly on **Describe workload and constraints** with the
**Ask Intelligent Provisioning agent** prompt. The earlier POC toggle, intro,
evidence sidebar, raw comparison table, research button, explanation button and
configuration download have been removed from the page.

1. Describe a Linux/Windows server or use Kubernetes, K8s, EKS, AKS or GKE in the
   prompt for managed Kubernetes with Linux workers. Send retrieves provider
   evidence and displays an explanation automatically. Missing CPU/RAM values
   are identified as inferred defaults. Advanced options contain only AWS,
   Azure and GCP regions. Comparisons automatically use the active tested account
   for each provider, or its first tested account. Provider accounts are chosen
   again explicitly in Terraform inputs. Include residency region IDs in the
   prompt when required. Kubernetes pod requests and replicas can be described
   in the prompt and adjusted in Terraform inputs.
2. AWS, Azure and GCP each show Selectable, Recommended and Optimal cards.
   Selectable is an exact CPU/RAM match, or the nearest profile meeting both
   minimums. Recommended is the nearest eligible increase in CPU or RAM without
   reducing either; Optimal adds a further increase. Prices break hardware-distance
   ties. Missing upgrades remain unavailable instead of repeating a profile.
   Budgets and mandatory evidence still apply. Discovery retains every exact
   AWS profile and reserves alternatives for two progressive hardware upgrades.
   Kubernetes comparisons use worker totals, including system
   overhead and the selected spare-node allowance. Control-plane, network and
   storage charges are excluded; version compatibility is not established by
   a price comparison.
3. Use configuration opens a provider-specific tested-account dropdown and
   labelled inputs. Selecting a catalogue choice no longer requires resending
   the prompt to attach an account. Live choices load from that account and region;
   **Refresh live values** discards any displayed plan and reloads them.
   AWS servers ask for VM name, AMI ID, subnet ID and security group ID. The execution role
   comes from the verified connection. Kubernetes uses cluster name, version
   and provider-specific network/IAM inputs instead of a VM AMI. EKS additionally
   requires two subnets, cluster/node IAM roles and a KMS key. Azure/GCP ask for
   their own image, network and resource-group/project inputs. EKS/AKS/GKE versions
   come from their service APIs. EKS second subnets must share the first subnet's
   VPC and use another availability zone. AWS security groups and GCP networks,
   subnets and secondary ranges are filtered for compatibility. Failed lookups
   show unavailable fields without sample or old account values. See
   [live input permissions and behavior](provider-live-inputs.md).
4. Generate Terraform plan revalidates the chosen account's hardware and input
   resources through fresh read-only API calls, runs init, validate and plan, then displays the actual
   saved-plan summary in a dialog. Preparing or viewing a plan never applies it.
5. Terraform apply opens an approval dialog containing the plan, approver name,
   masked token input and explicit acknowledgement. A valid approval applies
   the exact saved binary plan. Editing inputs, choosing another configuration,
   sending a new prompt or changing the global account invalidates the current
   displayed plan. Backend approval also checks one-hour expiry, artifact
   checksums, current target connection, backend identity and mandatory policy.

The Work page retains automatic refresh, with a manual **Refresh** beside
**Platform controller account**. The refresh clears the telemetry and price
caches. The duplicated active-context caption, target inventory panel and
requested explanatory captions are removed. Applied Agent resources appear in
the lifecycle table. Existing AWS deployment destroy handoffs are preserved;
the new multi-cloud records do not introduce a destroy action.

## Runtime requirements and verification limits

### Deployment history

The **Deployments** sidebar page lists all saved Agent plan/deployment statuses,
including preparing, awaiting approval, in progress, completed and failed. It also
includes existing AWS workflow records. Rows show the resource name, cloud, target
account/subscription/project, region and creation time; **Details** shows safe
metadata and the saved status timeline. No credentials, raw plans, state or runner
logs are rendered. Refresh rereads disk; active operations refresh every ten seconds.

Agent records remain authoritative under `DATA_DIR/poc_plans`; older AWS records
remain under `DATA_DIR/terraform_deployments`. Keep the existing persistent DATA_DIR
mount to retain history across container restarts/releases. New Agent records save
the resource name and timestamped status transitions atomically, including failures.
Old records are displayed without migration; their names are recovered from saved
inputs when available. A runner interruption may leave the last saved status as
in progress; the page does not infer success or rerun a deployment. The page is
read-only and introduces no new plan, approval, apply or destroy controls.

The defaults remain `ENABLE_TERRAFORM_WORKFLOW=false` and
`ENABLE_APPROVAL_EXECUTION=false`. Planning requires the enabled Linux runner,
Terraform CLI, central `TF_STATE_BUCKET`/`TF_STATE_REGION` and tested provider
connections. Apply additionally requires approved execution enabled and the
configured approval token. AWS account/environment, region, instance-family and
TTL policies remain enforced; names and credentials alone cannot bypass policy
or grant missing IAM permissions. Kubernetes needs the provider's cluster and
node permissions, networking, encryption keys and a supported version.

Credentials are loaded only into the runner environment. External IDs, approval
tokens and Windows passwords are excluded from saved variable files and audit
events. Azure Windows passwords use `IIPP_AZURE_WINDOWS_ADMIN_PASSWORD` in the
protected runtime. Terraform binary plans/state remain sensitive artifacts.

New records live under `DATA_DIR/poc_plans`; state uses
`iipp/poc/<deployment-id>/terraform.tfstate` in the central S3 backend with native
locking. Apply holds a per-deployment lock and provider initialization uses the
existing global cache lock. Existing AWS deployments and state are preserved.
Old review-only POC plans must be regenerated before they can be applied.

Unit and browser checks mock provider calls and Terraform operations. They do
not establish live IAM, cloud capacity, successful cloud plan/apply, resource
connectivity or end-to-end destroy for the new targets. This source update does
not deploy the application, enable write flags or create cloud resources.

## Local verification — 2026-11-05

- 149 tests passed with `tests/test_terraform_safety.py` excluded. The full suite
  was attempted; that module cannot be collected on native Windows because
  `fcntl` is unavailable. New approval tests mock the Terraform runner and lock;
  they cover tokens, identity, policies, expiry, artifact changes and replay.
  Input tests also cover progressive hardware tiers, provider account routing,
  AWS/EKS pagination, AKS/GKE version contracts, dependent network choices,
  failed refreshes, hardware revalidation and saved cluster size/version inputs.
- Headless Edge checks passed at 1440px and 390px for the initial prompt,
  cloud choices, plan dialog, approval dialog, EKS/AKS/GKE inputs and Work page.
  Browser previews use explicit offline fixtures and block real execution.
- All nine Terraform JSON configurations parsed. The cached AWS 5.100.0 provider
  validated Linux VM, Windows VM and Linux Kubernetes schemas, including the
  required deployment tags. Azure/GCP provider schema validation could not
  complete because registry/provider downloads failed, including outside the
  sandbox. No cloud credentials were used by these schema checks.
- Changed Python modules compile. No live Terraform plan/apply/destroy or
  application deployment was performed.
