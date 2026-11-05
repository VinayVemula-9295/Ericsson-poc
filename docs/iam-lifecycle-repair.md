# Terraform IAM repair — 2026-11-05

## Verified outcome

Test administrator profile `dev` was verified as account `695729002971`.
Initially only the inventory role existed: the execution role, EKS roles and
customer KMS keys were absent. The inventory trust names the correct controller
but does not require an External ID. The saved connection secret does contain an
ID; it was reused in the new execution trust without printing, rotating or
changing it. Inventory trust and connection secrets were left unchanged.

Applied in Test:

- Created `IIPPTerraformExecutionRole` with controller-only trust and the existing
  External ID. Attached `IIPPServerLifecycle` for tagged server plan/apply/destroy.
- Created separate EKS cluster and node roles with the managed policies in
  `policy_templates/aws/terraform-lifecycle/service-role-attachments.json`.
- Created the required EKS, managed-node-group and Auto Scaling service-linked roles.
- Added inventory plan-input reads using `IIPPPlanInputReads`.

Private before/after snapshots and execution evidence:
`.cache/iam-review/695729002971-20261105T105219928463Z/`.
Readback and Access Analyzer evidence:
`.cache/iam-review/695729002971-20261105T105403235991Z/`.
Full EKS and server policy validation returned no findings. The EKS template's
key ARN remains a placeholder: syntax validation does not establish key access.
Live principal simulation allowed Test-tagged `ec2:TerminateInstances` and
implicitly denied the same simulated instance with `Environment=poc`.
No EC2 instance or EKS cluster was created or destroyed.

## Remaining prerequisites

- Management's available `default` profile is root. Read-only inspection succeeded;
  no management changes were made. Use a non-root management administrator for writes.
- No Demo administrator profile was available. No Demo live audit or writes occurred.
- Test has no KMS keys. Supply an enabled symmetric customer key and verify its key
  policy before installing full EKS execution permissions. No keys were created.
- Test SCP inspection was denied. Management direct-SCP listing succeeded, but
  inherited SCPs and policy contents are not certified by this audit.
- Real plans were not run: the controller's backend runtime credentials were not
  available locally. Four plans remain required (server/EKS in Demo/Test).
  Apply/destroy success is not proven by IAM validation or simulation.

## Finish with administrator sessions

Run from the application root. Audit each account independently so unavailable
access to one account does not prevent reviewing the other:

```powershell
.\.venv\Scripts\python.exe scripts/review_iipp_iam.py --profile iipp-demo-admin --account 333347372766 --output-dir .cache/iam-review
.\.venv\Scripts\python.exe scripts/review_iipp_iam.py --profile dev --account 695729002971 --output-dir .cache/iam-review
```

With an actual KMS key ARN, repeat for the respective account with `--key-arn ARN`
and `--execute`. The tool verifies account identity, rejects root writes, validates
the proposed policy, backs up trust/inline/managed/boundary documents and preserves
unrelated attachments. `--execute` without a key installs only server permissions;
it cannot silently install the placeholder KMS policy. Key policies are exported
for independent review, not automatically rewritten. Changes are not transactional:
if an AWS call fails after a write, inspect live roles and the backup before retrying.

For a missing execution role whose inventory trust has no External ID condition,
pass `--external-id-file PRIVATE_JSON` containing the existing connection's
`external_id`. Obtain it through authorized secret access, never paste it into
commands or logs. The new execution role requires that ID; inventory trust remains
unchanged. Existing execution trust is never replaced by the tool.

Regenerate controller policy with `scripts/generate_iipp_controller_policy.py`
using the documented account IDs and existing bucket, then use
`scripts/configure_iipp_state.py --management-profile iipp-management-admin`
with `--controller-policy PATH --backup-dir UNIQUE_PATH`; dry-run first, then
`--execute`. Existing KMS state encryption intentionally stops that setup pending
compatible key permissions. See [two-account setup](two-account-terraform-setup.md).

`iam-terraform-policy-example.json` is an older server-only example, not the
complete lifecycle policy. Use the new [policy bundle](../policy_templates/aws/terraform-lifecycle/README.md).

## Saved-plan baseline and validation

IAM Policy Autopilot installation was attempted. The installed Windows executable
could not run (Access denied), including outside the sandbox. No analyzer-derived
baseline is claimed. The existing policies were corrected using AWS documentation;
the runnable baseline command after obtaining each actual saved-plan JSON is:

```powershell
uvx iam-policy-autopilot@latest generate-policies C:/Users/vvemula/Downloads/inframind-v19-release/inframind-v19/.cache/iam-review/test-server-plan.json --region ap-south-1 --account 695729002971 --pretty
```

Repeat with each account's real server/EKS plan JSON. Keep plans private; do not
commit or print their contents. Review generated actions against provider refresh,
update/replacement and deletion behavior. A create plan alone cannot certify all
future destroy permissions. Keep infrastructure execution disabled during these
checks and preserve the application's separate saved-plan approval workflow.

## Restore IAM changes

Use the unique `before.json` from the execution directory, not a later audit, and
verify the account identity first. Export each previous inline policy from
`snapshot[RoleName].inline[PolicyName]` to a private JSON file, then restore with:

```powershell
aws iam put-role-policy --profile dev --role-name ROLE_NAME --policy-name POLICY_NAME --policy-document file://PRIVATE_POLICY_JSON
```

If `IIPPPlanInputReads` or `IIPPServerLifecycle` was absent in the before snapshot,
remove only the newly added policy with `aws iam delete-role-policy --profile dev
--role-name ROLE_NAME --policy-name POLICY_NAME`. Existing managed attachments and
trust were not changed. Newly created EKS/execution/service-linked roles can remain
until their use has been reviewed; do not automatically delete roles or key grants
that may now be referenced by resources. Never restore by deleting Terraform state.
