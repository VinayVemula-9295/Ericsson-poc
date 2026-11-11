# Demo provisioning changes and verification

**Latest IAM retry:** the Demo inventory discovery permissions were repaired on
2026-11-05 at 09:43 UTC. Fresh reads now have no lookup errors and return seven
Kubernetes versions. Compatible cluster/worker roles and customer-managed keys
are absent, so cluster planning still needs prerequisite setup. See
[the repair outcome](demo-discovery-permission-repair.md). The earlier denied-read
results below describe the initial acceptance test.

Server and cluster saved-plan dialogs have Close on the left and Edit/Apply on
the right. Apply opens the approval form. Edit preserves
entered fields, removes the displayed saved plan and requires validation and a new
saved plan before approval. Apply still requires reviewer name, the protected token,
acknowledgement, valid checksums, current target identity and enabled execution.
Success opens a one-time resource-details dialog. Safe non-sensitive Terraform
outputs are retained in the deployment record; output retrieval failure does not
change a successful apply into a failure. Public IPs remain unassigned by default.

After Generate Terraform IaC, **Generate Terraform** appears only after successful
Validate Configuration for the current source and initiator. Edit clears validation
even before any values change; source/input changes and failed validation also hide
the action and invalidate saved approval state. Successful revalidation restores it.
The existing policy and initiator restrictions still apply, and generation prepares
a reviewed saved plan without applying it.

Reuse shows Configuration details immediately after Previous configuration. Values
come from saved selected CPU/RAM specifications; older minimum-only records label
them as required, and missing sizing remains unavailable.

Explicit worker prompts preserve the requested count and apply CPU/RAM minima per
worker. Pod-based requests retain their scheduling calculation. Optimal requires
a distinct eligible CPU/RAM upgrade over Recommended; otherwise it displays
"No eligible upgrade available". No eligible result means unavailable, never a
fixture value. Environment and TTL are visible beneath Subnet ID for servers and
Worker node IAM role ARN for EKS. Other providers retain visible environment/TTL
controls even when those field names do not apply.

The reference UI update adds Home, Agent, Deployment and an empty Telemetry tab.
Home contains the previous Telemetry content. Cloud provider and Account /
Subscription / Project selectors are visible beside Configure. Agent Advanced
options appear above the composer; Send uses Configure blue. Live Terraform input
choices load automatically when account evidence changes; the manual refresh and
roles/permissions expander have been removed. Planning still revalidates inputs.

Advanced options now uses searchable region dropdowns from the official public
AWS, Azure and Google Cloud region tables. Successful catalogue reads are cached
for six hours; source and retrieval captions are hidden in the UI. These lists do not require
connected accounts or establish account availability. Failed catalogue reads disable
that provider's selector and skip it during comparison; no available catalogues
blocks submission. GCP comparison zones come from the published zone table.

EKS Cluster IAM role ARN, Worker node IAM role ARN and KMS key ARN offer verified
existing account-scoped choices with manual override. Missing resources remain unavailable; form entry never creates roles or keys. Before planning,
targeted `iam:GetRole` and `kms:DescribeKey` reads validate the supplied ARNs, expected
trust principals and key properties. The selected account/region checks and explicit
approval workflow remain in effect. The inventory connection needs these targeted
read permissions; listing permissions alone are insufficient.

## Live Demo check: 2026-11-05

Updated modules were loaded into a temporary directory on the existing Linux
runner without deploying the application. The request was:

> I need a Kubernetes cluster of 2 worker nodes with 2 vCPU and 1 GiB RAM

Target: Demo account `333347372766`, region `ap-south-1`. Planning was already
enabled with the configured S3 backend; apply remained disabled.

| Tier | Instance type | Hardware per worker | Workers | Monthly worker compute USD |
| --- | --- | --- | --- | --- |
| Selectable | t3.micro | 2 vCPU / 1 GiB | 2 | 16.352 |
| Recommended | t3.small | 2 vCPU / 2 GiB | 2 | 32.704 |
| Optimal | t3.small (shared) | 2 vCPU / 2 GiB | 2 | 32.704 |

Prices were fetched through AWS Price List Query API, AmazonEC2 GetProducts.
These estimates exclude EKS control-plane, storage and networking charges and
do not establish spare capacity, scheduling capacity or quotas.

The Selectable configuration was used for live input discovery. Compatible
subnets were `subnet-0e47bdf5897c97c7b` and `subnet-03f871fd73028b441`.
The connection specifies execution role
`arn:aws:iam::333347372766:role/IIPPTerraformExecutionRole`.

**Cluster plan generation is blocked:** the selected inventory role was denied
`iam:ListRoles`, `eks:DescribeClusterVersions` and KMS discovery
(`kms:ListKeys` / `kms:DescribeKey`). Cluster/node roles, version and KMS key
could not be validated. No cluster plan ID was generated and no apply ran.
An administrator must grant the supplemental reads in
`iam-plan-inputs-policy.json`, subject to applicable SCPs, permission boundaries
and KMS key policies, then Refresh live values. Missing roles or keys still need
separate setup. No IAM changes were made during verification.

The live server planning path succeeded with the updated modules for both names:

| Name | Saved plan ID | Result |
| --- | --- | --- |
| iipp-server-plan-check | poc-7d602f8f28da4026a7c670f444616f0a | AWAITING_APPROVAL; 1 to add |
| iipp-server-plan-edited | poc-46e4cf318c5d47bc89470885b79a964c | AWAITING_APPROVAL; 1 to add |

The plans have distinct checksums. These are backend planning checks; browser
Edit interaction was verified separately with offline fixtures. Both records
remain unapplied and are subject to the normal one-hour approval expiry.

## Required role separation

- Inventory role: supplemental EC2, EKS-version, IAM-role and KMS reads above.
- Controller role: S3 backend state/lock access and authorized assumption of target
  roles with the configured External ID. Do not grant the worker role backend access.
- Terraform execution role: resource-specific EC2/EKS create, describe, list, tag,
  update and delete actions needed by the reviewed stack; `iam:PassRole` limited
  to the selected cluster and worker ARNs, with appropriate `iam:PassedToService`
  conditions. Include required KMS use/grant permissions and key-policy grants.
  First-use service-linked-role creation must be limited by `iam:AWSServiceName`.
  The existing EC2 example policy alone does not authorize EKS provisioning.
- EKS cluster role: trust `eks.amazonaws.com`; `AmazonEKSClusterPolicy` or
  equivalent least-privilege permissions. See [AWS cluster roles](https://docs.aws.amazon.com/eks/latest/userguide/cluster-iam-role.html).
- Worker role: trust `ec2.amazonaws.com`; `AmazonEKSWorkerNodePolicy` and
  `AmazonEC2ContainerRegistryPullOnly` or equivalents. Provide VPC CNI permissions
  through its own service-account role or the worker role. See [AWS worker roles](https://docs.aws.amazon.com/eks/latest/userguide/create-node-role.html).

Private worker subnets require DNS and access to EKS/ECR/S3 and other needed
services through NAT or VPC endpoints. A discovered ARN alone does not prove
all execution permissions or network readiness.

## Local verification

The final Windows-compatible regression suite passed **195 tests** with UTF-8
enabled and `tests/test_terraform_safety.py` excluded for the platform limit below.
The focused Agent browser checker passed all server/cluster interactions and
desktop/mobile checks, including Azure/GCP input routing with offline fixtures.

- Streamlit interaction tests cover server and cluster Edit, preserved inputs,
  regenerated plan identity/name, two-worker scaling and stale approval removal.
- Tests cover sanitized Terraform outputs, success popup dismissal, permission
  diagnostics, partial KMS failures, tier reuse and worker-only prompt parsing.
- Browser checks exercise both Edit flows and regenerated dialogs at desktop and
  mobile widths with mocked cloud calls; these do not prove live Terraform success.
- All nine configurations parse. Cached AWS 5.100.0 schemas validate Linux VM,
  Windows VM and Linux Kubernetes configurations including the new outputs.
- Native Windows cannot collect the fcntl-dependent Terraform safety module.
  WSL is unavailable and pytest is absent from the Linux runtime; no packages
  were installed there. The full Linux suite remains unverified.

The source was not deployed and no cloud resource was created.

## Dashboard refinement

The shared product title is larger and bold, including on mobile. Home no longer
shows the controller/sample-account caption or the overflow menu. Region captions
and Azure/GCP price-source timestamps are hidden; underlying evidence is retained.
AWS comparison tiers include verified families outside runner policy, with execution
blocked for those choices. Recommended and Optimal require distinct successive
hardware upgrades. Plan dialogs group Edit/Apply at bottom right. Successful AWS
deployment rows offer a light-red Destroy action using the existing reviewed flow.
