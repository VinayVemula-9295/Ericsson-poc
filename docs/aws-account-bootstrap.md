# AWS POC and TEST account bootstrap

For the current central bucket and EC2/EKS permissions, use the
[two-account setup and live status](two-account-terraform-setup.md). The older
bootstrap execution policy below is EC2-only; it does not enable EKS.

## Account model — current user environment

- Management/controller account: Naresh-Acc, `704298683641`.
- Existing POC member account: Demo-Account, `333347372766`.
- Existing TEST member account: Test-Account, `695729002971`.
- The user reports that Demo-Account and Test-Account are already members under the `IIPP-Sandbox` OU and have monthly budgets.
- Do **not** run the account-creation command below for these existing accounts. Use the actual IDs above when configuring IAM roles and the controller policy.
- Each target account needs `IIPPInventoryReadRole` (read only) and `IIPPTerraformExecutionRole` (restricted EC2 create/destroy in one approved region). STS assumption of the inventory roles has been verified from the controller; Terraform execution roles and the S3 state bucket are not yet confirmed.
- The controller role assumes only the target roles. S3 state is accessed with the controller identity, not the target role.

## Permissions boundary

`create_iipp_test_accounts.py` must be run from the AWS Organizations management account (or an appropriately delegated account administrator) with permission to create accounts, tags and OUs, and move accounts. AWS Organizations account creation is asynchronous and the caller needs `organizations:CreateAccount`; if tags are supplied, it also needs `organizations:TagResource`. The automatically-created `OrganizationAccountAccessRole` is used for initial setup of each member account. See [AWS CreateAccount](https://docs.aws.amazon.com/organizations/latest/APIReference/API_CreateAccount.html).

The EC2 controller identity supplied in this project is `CloudPilotReadTargetMetricsRole` in account `704298683641`; it is currently a read/metrics role and was observed without `s3:ListAllMyBuckets`. Do not run Organizations account-creation commands from that restricted EC2 role. Use an authorized management-account admin/CloudShell profile. Provide two unique, valid, reachable email addresses.

## 1. Optional: create member accounts in a new environment

```bash
cd ~/cloudpilot-ericsson-poc
python3 scripts/create_iipp_test_accounts.py \
  --poc-email '<unique-poc-account-email>' \
  --test-email '<unique-test-account-email>'
```

Review planned names and email addresses. Only after they are correct, run:

```bash
python3 scripts/create_iipp_test_accounts.py \
  --poc-email '<unique-poc-account-email>' \
  --test-email '<unique-test-account-email>' \
  --execute
```

This optional workflow creates/moves accounts under the configured IIPP non-production OU. It is not needed for the current Demo-Account and Test-Account. Do not blindly rerun a failed `CreateAccount` request: check AWS Organizations account state first.

## 2. Configure target IAM roles and budgets (current environment)

For the current environment, use POC account ID `333347372766` and TEST account ID `695729002971`. The user reports budgets have already been created; do not recreate them unless they need adjustment.

Use the real 12-digit account IDs returned by Organizations and an email that can receive budget notifications:

```bash
python3 scripts/configure_iipp_test_accounts.py \
  --poc-account-id '<12-digit-poc-account-id>' \
  --test-account-id '<12-digit-test-account-id>' \
  --source-role-arn 'arn:aws:iam::704298683641:role/CloudPilotReadTargetMetricsRole' \
  --budget-email '<cloud-cost-alert-email>' \
  --region ap-south-1
```

Default monthly alert budgets are USD 75 for POC and USD 35 for TEST. These are starter values; change them with `--poc-budget-usd` and `--test-budget-usd` before execution. The dry run makes no IAM or budget changes. After checking the output, add `--execute` and run from the authorized management account:

```bash
python3 scripts/configure_iipp_test_accounts.py \
  --poc-account-id '<12-digit-poc-account-id>' \
  --test-account-id '<12-digit-test-account-id>' \
  --source-role-arn 'arn:aws:iam::704298683641:role/CloudPilotReadTargetMetricsRole' \
  --budget-email '<cloud-cost-alert-email>' \
  --region ap-south-1 \
  --execute
```

The script creates unique External IDs and saves account IDs, role ARNs and External IDs in `~/.iipp-target-account-connections.json` with mode `0600`. It also emits `~/.iipp-source-assume-roles-policy.json`; an IAM administrator must review and attach that policy to the controller role. Budget alerts notify at 50%, 80% and 100% of actual monthly spend. **AWS Budgets are alerts, not guaranteed hard spend caps**. Use IAM policy restrictions, quotas, and the IIPP policy engine to constrain creation.

The execution role in this POC permits tagged T3 instance families in the configured region; it does not grant `AdministratorAccess`. Its allowed region defaults to `ap-south-1`. This is a POC starter policy and must be reviewed against the exact EC2 `RunInstances` resource-level authorization needs before enabling apply. It is not a general-purpose production policy.

New member accounts may not have an approved VPC/subnet/security-group setup for the demo. The current Terraform stack deliberately reuses an existing subnet and security group; it does not create a VPC, subnet, route table or security group. Verify the default VPC/subnets or have an administrator prepare an isolated private test network before making a plan.

## 3. Generate the controller policy after the target accounts exist

```bash
python3 scripts/generate_iipp_controller_policy.py \
  --bucket '<globally-unique-central-state-bucket>' \
  --region ap-south-1 \
  --controller-account-id 704298683641 \
  --poc-account-id '<12-digit-poc-account-id>' \
  --test-account-id '<12-digit-test-account-id>'
```

The generated file `~/.iipp-controller-policy.json` includes prefix-scoped S3 state/lock permissions, Secrets Manager permissions for IIPP connection secrets, and AssumeRole permissions for the four target roles. Have an IAM administrator review/attach it to `CloudPilotReadTargetMetricsRole`. Also confirm existing Bedrock, telemetry, pricing and CloudWatch permissions remain attached.

## 4. Add connections in the dashboard

Open **Configure → AWS** twice:

- Connection name: `AWS-POC`; enter the POC inventory role ARN, Terraform execution role ARN, the POC External ID, and `ap-south-1`.
- Connection name: `AWS-TEST`; enter the TEST role ARNs, TEST External ID, and `ap-south-1`.

Use **Test connection & save**. The identity/account ID returned must match the intended AWS account. The target role ARN must be configured for Terraform. Only tested AWS connections with an execution role are eligible for provisioning.
