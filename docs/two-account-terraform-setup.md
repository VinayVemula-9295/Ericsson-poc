# Two-account EC2 and EKS setup

For the later live IAM repair, reusable policy bundle, verified Test changes and
remaining blockers, see [IAM lifecycle repair](iam-lifecycle-repair.md). Historical
credential-status statements below describe their original verification time.

Management/controller: `704298683641`, role `CloudPilotReadTargetMetricsRole`.
Demo/POC: `333347372766`. Test: `695729002971`. Region: `ap-south-1`.
Central bucket: `inframind-tfstate-704298683641-20261104`.

## Direct SSO administrator setup

The setup tools support separate administrator profiles for all three accounts;
this avoids granting the runtime controller access to OrganizationAccountAccessRole.
The user must complete browser authentication locally. Never copy SSO tokens or
administrator credentials to the server or into chat.

From the application directory in PowerShell, run the guided helper:

```powershell
.\tools\connect_iipp_sso.ps1
```

It runs `aws configure sso` for each profile and verifies each account identity.
Use SSO session `iipp-admin`, your actual portal Start URL and SSO Region, and the
existing administrator permission set. Set deployment region `ap-south-1` and
output `json`. Required mappings:

| Profile | Account |
| --- | --- |
| iipp-management-admin | 704298683641 |
| iipp-demo-admin | 333347372766 |
| iipp-test-admin | 695729002971 |

After sign-in, run this read-only check:

```powershell
.\.venv\Scripts\python.exe scripts/configure_iipp_terraform.py --check-access --management-profile iipp-management-admin --poc-profile iipp-demo-admin --test-profile iipp-test-admin
```

This checks all three identities before setup writes, inspects existing target
roles, and lists eligible customer KMS keys and subnet metadata. It does not prove
IAM write permissions, key-policy delegation, private routing, or cloud capacity.
Missing or expired named profiles fail without falling back to default credentials.

For the target setup command below, add all three `--*-profile` options. Select
actual target KMS keys from the check; if none exist, create dedicated symmetric
customer keys with IAM delegation before setup. Run without `--execute` first;
then add `--execute` to apply. Existing role policies/trust and attachments are
backed up in unique private directories before target writes. The tool preserves
connection trust and External IDs; it does not remove unrelated policies or SCPs.

For `configure_iipp_state.py`, add
`--management-profile iipp-management-admin`. Preserve its dry-run-first procedure
and use a new backup directory for execution. These local admin sessions are used
only for bootstrap; the application's runtime role remains scoped.

After IAM, key and networking checks pass, follow the runtime section below.
Success requires real saved EC2 and EKS plans in both Demo and Test. Keep approval
execution disabled; creating a plan must not create or destroy infrastructure.

## Completed live on 2026-11-05

- Enabled bucket versioning and all four public-access blocks; preserved AES256
  encryption and existing bucket policy, adding a TLS-only requirement.
- Attached supplemental controller inline policy `IIPPCentralTerraformAccess`:
  both state prefixes, lock deletion only, four target role ARNs and existing
  connection-secret/pricing access. Other attached policies remain unchanged.
- Verified controller list/write/read/delete of unique temporary lock probes under
  both `iipp/deployments/` and `iipp/poc/`. Probe delete markers/versions can remain
  in the versioned bucket; no deployment state was modified.
- Confirmed inventory-role assumption succeeds in both subaccounts.

The live controller cannot assume `OrganizationAccountAccessRole` in either target.
Local default credentials are a root identity (STS rejects root role assumption);
the existing `dev` profile is expired. Both inventory roles currently deny
`kms:ListKeys`. Target IAM, keys and networking are not verified/configured.
The live runner has an empty `TF_STATE_BUCKET` and both execution flags false.
No application release, real Terraform plan or infrastructure apply was performed.

## Finish target IAM using a valid administrator session

Use a management administrator role/user session authorized to assume
`OrganizationAccountAccessRole` in both targets. Do not recreate accounts,
connections, budgets or External IDs. Prepare an enabled symmetric customer KMS
key in each target/region; its key policy must delegate the execution role's
DescribeKey/CreateGrant permissions. Key creation is an administrator prerequisite.

Run from the application root (Python with boto3), substituting the actual keys:

```bash
python scripts/configure_iipp_terraform.py \
  --poc-key-arn arn:aws:kms:ap-south-1:333347372766:key/ACTUAL-KEY-ID \
  --test-key-arn arn:aws:kms:ap-south-1:695729002971:key/ACTUAL-KEY-ID \
  --output-dir .cache/terraform-setup
```

This writes the two execution policies offline. Repeat with `--execute` to apply.
The command preserves inventory/execution role trust and External IDs, supplements
inventory reads and replaces only `Innominds-IIPP-Permissions` on the execution
roles. Review any additional attached policies/boundaries/SCPs separately: this
command does not certify their effective permissions. It creates/reuses
`IIPPEKSClusterRole` and `IIPPEKSNodeRole`, attaching EKS cluster, worker, ECR pull
and node CNI policies; a dedicated CNI workload role can replace node CNI later.
It also ensures EKS/nodegroup/Auto Scaling service-linked roles exist.

The policy allows `t3.*` servers and EKS clusters named `iipp-*`; use that prefix
in the Terraform cluster-name input. CreateCluster uses `Resource: "*"` because
the API does not support resource-level authorization, constrained by region and
project/environment tags. Lifecycle operations use target-account cluster ARNs.
Managed-node-group PassRole targets `eks.amazonaws.com`; EC2 instance-profile
PassRole, if added, targets `ec2.amazonaws.com`.

References: [AWS EKS authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_eks.html),
[EKS node role](https://docs.aws.amazon.com/eks/latest/userguide/create-node-role.html).

Prepare existing private networking in each target: two subnets in separate AZs
of the same VPC, sufficient IP capacity and NAT/service endpoints for nodes.
Private cluster API access from the controller additionally requires routing,
DNS and security rules. The Terraform stack does not create this network.
Select the target's cluster/node roles and KMS key in Agent inputs.

## Controller policy regeneration and runtime

```bash
python scripts/generate_iipp_controller_policy.py \
  --bucket inframind-tfstate-704298683641-20261104 \
  --region ap-south-1 --controller-account-id 704298683641 \
  --poc-account-id 333347372766 --test-account-id 695729002971 \
  --output .cache/terraform-setup/controller-policy.json
python scripts/configure_iipp_state.py \
  --controller-policy .cache/terraform-setup/controller-policy.json \
  --backup-dir .cache/terraform-setup/new-unique-backup
```

The second command is dry-run by default; `--execute` configures the existing
bucket and controller. It preserves bucket policy statements and encryption,
backs up prior settings and refuses automatic KMS changes. A KMS state bucket
additionally needs controller Encrypt/Decrypt/GenerateDataKey permissions and a
compatible key policy before use.

After target prerequisites pass, deploy the current source using the deployment
workflow and set `TF_STATE_BUCKET` to the bucket above, `TF_STATE_REGION=ap-south-1`,
`TF_STATE_PREFIX=iipp/deployments`, `IIPP_ALLOWED_AWS_REGIONS=ap-south-1`,
`IIPP_ALLOWED_AWS_INSTANCE_FAMILIES=t3`, `ENABLE_TERRAFORM_WORKFLOW=true`.
Keep `ENABLE_APPROVAL_EXECUTION=false` until four real plans (server/EKS in each
target) succeed. Then enable it with the protected approval-token configuration
and explicitly approve the saved plans. Preserve account-role validation, checksums,
expiry and S3 native locks. Provider credentials assume the target role; backend
credentials remain the controller identity.

Agent state uses `iipp/poc/<deployment-id>/terraform.tfstate`; legacy server state
uses `iipp/deployments/<deployment-id>/terraform.tfstate`. No existing state is
migrated. Deployment details now offer a reviewed destroy workflow for AWS records;
it requires the protected reviewer/token validation and execution gate.
