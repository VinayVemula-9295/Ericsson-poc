# Organization discovery, billing and account inventory

The [Connected Clouds cleanup](connected-clouds-cleanup.md) supersedes card layout,
customer diagnostic presentation, organization labels, and adds the explicitly
configured controller credential source described below.

This implementation extends the existing Streamlit application and Secrets Manager
connection registry. It does not deploy the application or modify AWS IAM.

## Architecture and changes

`dashboard_adapters.py` owns provider API reads. `multicloud_dashboard.py` owns
authorization, caching, billing deduplication and account summaries.
`hierarchy_ui.py` presents Connected Clouds, organization metadata, the selectable
account table and the resource/billing dialog. `provider_ui.py` exposes separate
organization and inventory role settings. `provider_connections.py` supports a
server-side named source profile. `app.py`, `shell_ui.py` and `assets/platform.css`
provide the shared primary Configure button, compact shell, centered navigation
and bounded Administrator profile with A initials.

AWS discovery reads DescribeOrganization, ListRoots, ListAccounts,
ListOrganizationalUnitsForParent, ListParents, DescribeAccount and
DescribeOrganizationalUnit. All list operations use SDK paginators. Management
accounts are ordinary account nodes with a management flag, never extra accounts.
AWS Organizations supplies no friendly organization name; the organization ID is
used as its label. Management account names come from account API responses.

In single-workspace mode, an empty AWS discovery boundary allows API discovery of
the credential's own organization. Customer-scoped control-plane connections still
require an explicit approved boundary. Mismatching boundaries are rejected. No
fallback to another profile occurs. Organization and billing reads use the optional
management read role; resource reads continue using the selected inventory role.
Root identities are rejected by the dashboard adapter.

## Billing methodology

The metric is **UnblendedCost**, consistently used across all AWS queries. This
represents net expenditure across the returned billing line items, including signed
credit/refund records, rather than a promotional credit balance. It is not amortized
cost or NetUnblendedCost. Credit, Refund, Tax, discount and adjustment record types
are displayed verbatim in the RECORD_TYPE breakdown. No additional subtraction of
credits occurs. SERVICE and RECORD_TYPE are alternative views of the same costs;
only SERVICE rows enter totals. Daily amounts use Decimal, UTC calendar months,
and provider currency without conversion. The previous month is a full month.

Organization billing requires explicit `billing_scope=organization`, a matching
discovery boundary, successful hierarchy discovery and a verified management-account
session. Cost Explorer filters LINKED_ACCOUNT to the deduplicated discovered account
IDs, including the management account. It groups daily by account/service,
account/region and account/record type. GetDimensionValues verifies billing API
access. GetCostForecast and GetTags are not called and are not granted.

Successful organization reads take precedence over overlapping individual reads.
Deduplication keys include customer ownership and account identity. The connected
subtotal includes only targets with a successful last verification, once each.
It is a subset, never added to the organization total. Missing or failed reads are
unavailable; insufficient coverage is partial. Empty exports cannot establish zero.
AWS zero rows are inferred only after all pages and when the response establishes
a currency. Completely empty responses remain unavailable instead of inventing USD.

Azure ActualCost queries can aggregate discovered subscriptions under an approved
tenant; GCP queries aggregate discovered projects using the existing BigQuery billing
export, including credits. Cloud Billing does not supply a project usage-cost query
equivalent to Cost Explorer: enable billing export and configure its table. These
integrations require provider credentials and have not been live verified here.
Tenant/project billing may be partial when management-level or unallocated billing
charges are outside subscription/project exports. Currency totals stay separate.

Billing cache defaults to 3600 seconds (`BILLING_CACHE_SECONDS`); hierarchy and
inventory default to 300 (`INVENTORY_CACHE_SECONDS`). Refresh bypasses caches.
SDK retries use three attempts with bounded timeouts. Logs record operation/error
codes, not raw exception payloads or credentials. The frontend exposes safe API
operation/error codes and guidance to inspect IAM, SCPs and permission boundaries.

## Inventory and account dialog

Select an account table row to open the dialog. Inventory checks STS caller account
against the selected account. Existing AssumeRole and secret-stored External ID
handling remain in use. Inventory covers EC2 instances, EBS, VPC, EKS clusters/node
groups, ECS clusters/services, RDS, Lambda, classic/v2 load balancers, Auto Scaling,
CloudWatch metric descriptors and S3 bucket locations. States come from APIs;
unknown states remain unavailable. No resource cost allocation is invented.
API failures produce partial coverage and per-service warnings. Inventory scans
enabled regions, or the optional `inventory_regions` metadata list. Configure the
default region in the existing form. Accounts without a matching connection retain
their organization information and show inventory configuration guidance.

Search, service, region and state filters operate on the fetched inventory, with
25 rows per page. Billing shows service totals, top five services, daily trends,
record types and synchronization timestamps separately from resource inventory.

## Manual IAM configuration

Review these artifacts before applying anything:

* `iam-dashboard-organization-policy.json`: management discovery role.
* `iam-dashboard-billing-policy.json`: management billing role.
* `iam-dashboard-inventory-policy.json`: per-account inventory permissions.
* `policy_templates/aws/dashboard-inventory-role.json`: reusable CloudFormation role.

Deploy the CloudFormation template separately into each intended member account.
Supply `PlatformRoleArn` as the exact authorized controller IAM role ARN, a unique
ExternalId using a protected parameter file, and the desired RoleName. For example,
prepare a local JSON array containing `ParameterKey` and `ParameterValue` objects
for PlatformRoleArn, ExternalId and RoleName, then run:

```sh
aws cloudformation create-stack --profile MEMBER_PROFILE \
  --template-body file://policy_templates/aws/dashboard-inventory-role.json \
  --stack-name iipp-inventory-read --capabilities CAPABILITY_NAMED_IAM \
  --parameters file://protected-inventory-parameters.json
```

Use a file compatible with the CLI create-stack parameters format. Protect it
as a secret and never commit it. Alternatively deploy in the CloudFormation console
using the NoEcho ExternalId parameter. The controller identity policy must allow
`sts:AssumeRole` on each exact output role ARN. Trust uses that exact controller
principal plus `sts:ExternalId`; no account root principal or administrator policy
is needed. Existing Secrets Manager permissions remain separately scoped to stored
connection secrets. Configure each connection's inventory ARN and secret External
ID, then verify it with the existing connection test. Billing permissions stay on
the management role, not automatically on member inventory roles.

Configure the approved organization ID and organization billing scope on the
management connection. When a member connection also needs hierarchy discovery,
set its organization read role ARN explicitly. The source role must be permitted
to assume it. For different organization/member External IDs, use separate
connections; the current connection secret has one External ID.

For an optional independent policy baseline review from source:

```sh
uvx iam-policy-autopilot@latest generate-policies dashboard_adapters.py --region us-east-1
```

Autopilot was not installed/run in this workspace. The supplied policy artifacts
correspond to the requested API operations and need account-specific IAM review.
CloudFormation validation and live AssumeRole validation require configured
non-root credentials. Check SCPs, permission boundaries, Cost Explorer enablement
and billing access if reads are denied.

## Live evidence and limits

Read-only CLI diagnostics on 2026-11-11 verified organization o-x32fez8kdn,
feature set ALL, root r-4ktl, OU IIPP-Sandbox (ou-4ktl-6244uz84), and three ACTIVE
accounts: Demo-Account, Test-Account, Naresh-Acc. Both default and management CLI
profiles identify as management root. **Updated 2026-11-11:** dev and Demo STS calls
now succeed using refreshed SSO sessions. Live organization and billing diagnostics,
member-profile inventory, runtime AssumeRole and billing, and the deployed browser
have been revalidated. The deployed controller remains denied Organizations reads;
member inventory roles have partial service coverage. See the
[updated live validation report](aws-live-validation-2026-11-11.md) for amounts,
identity distinctions, permission gaps and evidence.
These diagnostic IDs are evidence only and are not embedded in application logic.

The running application uses a non-root EC2 controller role. Configure its approved
organization read access and scope separately; the local management root profiles
are diagnostic identities only. Recheck saved connections with:

```sh
python tools/validate_dashboard_access.py --connection CONNECTION --inventory
```

For organization billing that tool also validates the configured organization scope.
No deployment, IAM attachment, provisioning, apply or destroy was performed.

## Validation results

* Windows-compatible regression suite: **348 passed**. Command:
  `python -m pytest -q --tb=short -p no:cacheprovider --ignore=tests/test_terraform_safety.py`.
* Focused enhancement suite: **6 passed**, including signed adjustments without
  duplicate aggregation, root rejection, separate management-role routing,
  sanitized errors, boundary discovery and correct selected-account dialogs.
  Four of these tests were already included in the 348-test run; the two dialog
  cases were added afterward and verified in the focused run.
* Local Edge/Playwright acceptance: **passed**, including login/logout,
  organization hover/focus/mobile tap, account-table rendering, configuration,
  Deployment/Agent/reuse navigation, fixed header and responsive provider cards
  at 1440, 1280, 768 and 390 pixels, with no browser exceptions.
* Browser tests use isolated test fixtures and disposable local login credentials;
  their screenshots and billing values are not evidence of live cloud data.
* Python compile checks passed for the changed service and UI modules.
* Full unfiltered suite cannot collect `test_terraform_safety.py` on native Windows
  because fcntl is unavailable. WSL is not installed and Docker is unavailable.
  Run that suite in the documented Linux runtime before release.
* CloudFormation structure is generated as JSON; AWS template deployment and
  permission effectiveness have not been live validated.

AWS API reference: [Organizations DescribeOrganization](https://docs.aws.amazon.com/organizations/latest/APIReference/API_DescribeOrganization.html)
and [Cost Explorer GetCostAndUsage](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html).
