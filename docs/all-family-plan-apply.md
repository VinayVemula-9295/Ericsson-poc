# Enable all verified AWS families for server and EKS planning

The application supports the explicit runtime setting
`IIPP_ALLOWED_AWS_INSTANCE_FAMILIES=*`. Default installations retain `t3`.
The wildcard removes only the instance-family restriction. The configured region,
target account/environment, hardware/offering validation, TTL, saved-plan checksum,
reviewer approval, backend state and execution feature flags remain enforced.

Target execution IAM must match the application setting. Both
`scripts/configure_iipp_terraform.py` and `scripts/review_iipp_iam.py` accept
`--allowed-instance-families '*'` or an explicit list such as `t3,t3a,c4`.
All-family IAM retains the existing target-account instance resource, requested
region and ownership-tag conditions. EKS role passing stays restricted to the
designated roles; encryption grants stay restricted to a verified key.
Inventory setup additionally grants `iam:GetRole` for the selected account's roles
so plan validation can verify cluster/worker trust.

Run an account audit first with a verified non-root administrator profile; review
the exported policy and backup before applying with `--execute`. Supply an actual
enabled symmetric customer KMS key via `--key-arn` to install EKS permissions.
Without a key the review tool installs server-only permissions, not a placeholder
EKS key policy. Missing EKS service roles and keys must be resolved in each account.
Existing connection trust and External IDs must be preserved.

After target IAM is verified, deploy the application with the explicit all-family
runtime setting while preserving the other running environment and mounts.
Do not infer effective cloud permission from an enabled UI button: validate policy,
verify selected account resources, and test saved-plan generation. Actual apply
remains a separate approval of the exact saved plan. Permission setup does not
authorize creating a test server or EKS cluster without that approval.
