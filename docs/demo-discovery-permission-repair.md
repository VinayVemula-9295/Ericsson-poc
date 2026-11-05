# Repair Demo Terraform-input discovery

## Latest result: repaired on 2026-11-05

At `09:43:23 UTC`, the supplemental read policy was applied to Demo account
`333347372766` / `IIPPInventoryReadRole`. The prior supplemental policy did not
exist; its rollback record is `.cache/demo-read-repair-refreshed-dev/previous-policy.json`.
Existing permissions and trust were preserved. The saved IAM document was read
back and matched the proposed policy.

The refreshed `dev` profile points to Test account `695729002971`. Its SSO login
also authorizes Demo AdministratorAccess, so the repair used a temporary workspace
SSO profile targeting Demo. The user's `dev` configuration was not changed.
No credentials were copied into the workspace or sent remotely.

Fresh discovery through the application runner returned **no lookup errors**:
three subnets, seven Kubernetes versions, zero compatible cluster roles, zero
compatible worker roles, and zero eligible KMS keys. The original access-denied
issue is fixed. A real cluster saved plan still cannot be generated until the
missing roles and customer-managed key are separately provisioned. No cluster
plan, resource apply, application deployment or execution-flag change occurred.

The original diagnosis and repair procedure follow for reference.

## Original diagnosis

Live diagnosis on 2026-11-05 confirmed that the application assumes
`IIPPInventoryReadRole` in Demo account `333347372766`. AWS denied `iam:ListRoles`,
`eks:DescribeClusterVersions` and `kms:ListKeys` because no identity-based policy
allows them. The EKS SDK operation exists and was called; this is an IAM grant
problem, not an unsupported SDK. Key description also needs `kms:DescribeKey`.

The controller's attempt to assume the existing Demo
`OrganizationAccountAccessRole` is denied. The local `dev` administrator profile
returned `ExpiredToken`. No live IAM update could be completed with those sessions.
Refresh a non-root administrator profile locally. Do not put credentials in chat,
source files or the application connection registry.

## Scoped repair

The new repair tool changes only the Demo inventory role's supplemental inline
policy `IIPPPlanInputReads`. It adds four discovery reads, restricts regional
reads to `ap-south-1` and key descriptions to the Demo account's regional keys.
It preserves all prior inline-policy statements and does not change role trust,
External IDs, execution permissions, other accounts or execution flags. It creates
no roles, KMS keys, clusters or VMs. It refuses root and unexpected target accounts.

Preview offline from the application root:

```powershell
.\.venv\Scripts\python.exe scripts/repair_demo_plan_reads.py
```

After refreshing `dev`, preview against the live role:

```powershell
.\.venv\Scripts\python.exe scripts/repair_demo_plan_reads.py --profile dev
```

Apply the additive repair with a new backup directory:

```powershell
.\.venv\Scripts\python.exe scripts/repair_demo_plan_reads.py --profile dev --execute --backup-dir .cache/demo-read-repair-backup
```

The profile can be a Demo administrator or a management administrator authorized
to assume the existing Demo administrator role. A management session without
that access will fail without changing IAM. `--backup-dir` must be a fresh path;
the backup is written before the IAM mutation. Repeated successful runs do not
duplicate the added statements. AWS documents inline policy updates through
[PutRolePolicy](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_update-role-permissions.html).

Then refresh live values in the Demo cluster form and verify existing cluster
and worker roles, supported versions and keys. If empty lists replace the denied
errors, the reads are repaired but prerequisites are absent and require separate
setup. KMS DescribeKey can additionally require a compatible key policy;
see [AWS KMS IAM policies](https://docs.aws.amazon.com/kms/latest/developerguide/iam-policies.html).
Generate a new cluster saved plan only after mandatory inputs validate. Do not
apply resources as part of this permission repair.

## Verification and rollback

Six mocked tests pass: minimal read permissions, dry-run behavior, preserving
existing permissions with backup and idempotence, rejecting root/wrong accounts,
and failing before writes when backup creation fails. No credentials are used.

The initial live repair was blocked by expired administrator credentials. The
retry outcome above supersedes that blocker; no successful cluster plan is claimed.

If rollback is needed, inspect `previous-policy.json` in the repair backup. Restore
only its `previous_policy` document to the named role/policy using an authorized
administrator. If it was null, remove only the newly created supplemental inline
policy. Preserve unrelated policies and never change role trust for this repair.
