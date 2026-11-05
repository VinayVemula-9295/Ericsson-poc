# Server and EKS Terraform IAM

These documents target management `704298683641`, Demo `333347372766`, Test
`695729002971`, region `ap-south-1`. Execution policies are templates: replace
`REPLACE_WITH_VERIFIED_KEY_ID` with the target's existing enabled symmetric customer
KMS key. Do not attach the placeholder documents as production policies.

| Document | Role / purpose |
| --- | --- |
| controller-policy.json | Management `CloudPilotReadTargetMetricsRole`: central backend state, native locks, connection secrets, target role assumption and public pricing |
| poc-execution-policy.json | Demo `IIPPTerraformExecutionRole`: tagged T3 servers and `iipp-*` EKS clusters/node groups |
| test-execution-policy.json | Test `IIPPTerraformExecutionRole`: same lifecycle with Test ownership tags |
| inventory-policy.json | Target `IIPPInventoryReadRole`: inventory, plan-input discovery and telemetry |
| eks-cluster-trust.json | `IIPPEKSClusterRole`: trust EKS |
| eks-node-trust.json | `IIPPEKSNodeRole`: trust EC2 |
| service-role-attachments.json | Required AWS managed policy ARNs for the separate EKS roles |

Target execution trust must grant only the existing controller role
`sts:AssumeRole`, conditioned on the existing connection `sts:ExternalId`.
The controller itself trusts its EC2 runtime through its existing instance profile.
Real connection trust is exported only into private audit directories; no External
IDs are included here. Existing trusts are preserved.

The bootstrap administrator creates/reuses service-linked roles
`AWSServiceRoleForAmazonEKS`, `AWSServiceRoleForAmazonEKSNodegroup`, and
`AWSServiceRoleForAutoScaling`. Runtime execution does not receive arbitrary IAM
role/policy management or wildcard PassRole. Server instance profiles are optional;
use `--instance-role-arn` in the review tool or `--poc-instance-role-arn` /
`--test-instance-role-arn` in the two-account generator to authorize a specific
role plus account-scoped profile lookup. Servers without profiles need neither.

S3 backend credentials remain the controller's; AWS provider credentials assume
the target role. State object deletion is not granted; deletion of `.tflock`
objects is required. A customer-encrypted state bucket also requires appropriate
controller KMS permissions and key-policy delegation, configured independently.

EKS key access grants only DescribeKey/CreateGrant on the selected key. Its key
policy must also allow that access. `kms:GrantIsForAWSResource` is deliberately
absent: EKS does not support that condition during CreateCluster. Node CNI access
uses the existing node-role managed-policy approach for IPv4. Separate workload
roles, Kubernetes RBAC, Helm, Fargate, add-ons, load-balancer controllers and
arbitrary VPC/network provisioning require their own reviewed permissions.

Ownership tags must use `Project=Innominds-IIPP` and `Environment=poc` or `test`.
Both application planning paths already supply these tags. Use `iipp-*` cluster
names and approved T3 server types. Existing networking must be suitable for private
EKS endpoints and node egress; IAM permissions cannot repair missing routing.

See [the repair and verification guide](../../../docs/iam-lifecycle-repair.md).

References: [EKS actions and resources](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html),
[EKS KMS constraints](https://docs.aws.amazon.com/eks/latest/userguide/enable-kms.html),
[EKS cluster roles](https://docs.aws.amazon.com/eks/latest/userguide/cluster-iam-role.html),
[EKS node roles](https://docs.aws.amazon.com/eks/latest/userguide/create-node-role.html),
[Terraform S3 backend permissions](https://developer.hashicorp.com/terraform/language/backend/s3).
