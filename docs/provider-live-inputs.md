# Account-backed Terraform inputs

Explicit worker-count prompts now use per-worker CPU/RAM minima and preserve the
requested count. The form offers worker count instead of inferred pod replicas in
this mode. Lookup errors identify denied permissions and API operations.
See [Demo verification and required roles](demo-provisioning-verification.md).

Agent Advanced options contain searchable region dropdowns fetched from official
public AWS, Azure and GCP region tables, cached for six hours. A published region
does not establish account eligibility or capacity. Use configuration
opens the matching provider's tested-account dropdown in Terraform inputs, for
both servers and managed Kubernetes. AWS accounts never appear under Azure or
GCP configurations. Configurations compared without an account can attach one here.

Every newly selected configuration, account change or account retest fetches
account-scoped choices. Ordinary field edits reuse that
form's evidence; generating a plan performs a fresh backend read. Changes to
accounts, resources, workload sizing, environment or TTL invalidate the displayed
saved plan. Discovery errors clear previous values and identify unavailable fields.
Manual fields remain available when discovery returns no choices; they cannot
bypass the backend's live validation.

| Provider | Live server inputs | Additional managed Kubernetes inputs |
| --- | --- | --- |
| AWS | Amazon-published x86 images, regional subnets and security groups | EKS versions and subnets in two availability zones of the same VPC; verified IAM role and KMS key ARN choices with manual override |
| Azure | Resource groups, regional VNet subnets, NSGs and Canonical/Windows platform image versions | Non-preview AKS patch versions for the subscription and region |
| GCP | Regional subnets, active zones and versioned provider images | GKE versions supported by both control plane and nodes, VPC networks, subnet secondary ranges and enabled project service accounts |

Names, SSH public keys/usernames, and the GKE master CIDR remain user inputs.
Existing resources are referenced by the generated Terraform configuration; this
workflow does not create a VPC, subnet, IAM role or encryption key on form entry.
Server images are independently verified for provider, OS and architecture.
Manual IAM role ARNs are checked with `iam:GetRole` for the selected account,
exact ARN and appropriate service trust. The manual KMS key ARN is checked with
`kms:DescribeKey` for account, region and enabled customer-managed symmetric
encryption properties. Account-scoped discovery offers compatible existing roles and enabled customer-managed symmetric keys. Manual entry remains available; every plan freshly validates the exact chosen ARNs. Empty lists or denied reads never create resources or substitute values.
Regional offerings and supported versions do not establish spare capacity,
quota headroom, IAM grants, pod/IP capacity or network reachability.

For Kubernetes, the form carries pod CPU, pod RAM, replicas and the spare-node
choice from the prompt. Editing them recalculates worker count with 20% system
overhead. The chosen SKU, count, version and network values feed the saved plan.
Worker compute estimates exclude control-plane, storage and networking charges.

## Read permissions

AWS discovery uses the selected connection's inventory role. The supplemental
read-only [policy](../iam-plan-inputs-policy.json) covers EC2 types, offerings,
images, subnets, security groups, `eks:DescribeClusterVersions`, `iam:ListRoles`,
`kms:ListKeys` and `kms:DescribeKey`. Manual ARN validation additionally requires
`iam:GetRole`; IAM/KMS listing actions populate the optional verified ARN choices. The bootstrap script's generated inventory
policy includes these reads for future explicitly requested account setup.
Existing account policies are not changed by this source update. The boto3 and
botocore minimums are raised to 1.43 to include the EKS version API used here.

Azure service principals need subscription reads for resource groups, compute
SKUs/platform images, virtual networks, NSGs and AKS Kubernetes versions. GCP
service accounts need Compute machine/image/network/zone reads, GKE server-config
reads and project service-account listing. Enabled APIs and separate Terraform
execution permissions are also necessary. All SDK credentials stay ephemeral;
only public resource identifiers and fetch times enter form evidence.

Planning retains the central S3 backend, policy checks and exact saved-plan
approval. Planning does not apply infrastructure. Existing workflow and execution
feature flags remain unchanged.

Version APIs: [EKS DescribeClusterVersions](https://docs.aws.amazon.com/eks/latest/APIReference/API_DescribeClusterVersions.html),
[AKS Kubernetes versions](https://learn.microsoft.com/en-us/rest/api/aks/managed-clusters/list-kubernetes-versions?view=rest-aks-2026-03-01),
[GKE server configuration](https://docs.cloud.google.com/kubernetes-engine/docs/reference/rest/v1/projects.locations/getServerConfig).

Local checks mock account APIs and Terraform execution. They verify routing,
pagination, filtering, failures and saved-plan safeguards, and cannot establish
successful live cloud planning or deployment. Deployment and cloud mutations
remain separate operations.
