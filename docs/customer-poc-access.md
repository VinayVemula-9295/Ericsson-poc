# Dashboard access, billing and authentication

## Admin login

The demo entry bypass is removed. Existing CONTROL_PLANE_USERS_FILE identities
remain supported (PBKDF2-SHA256, 600,000 iterations). Single-workspace mode accepts
IIPP_ADMIN_USERNAME, IIPP_ADMIN_SALT and IIPP_ADMIN_PASSWORD_HASH. A protected
IIPP_ADMIN_PASSWORD environment variable is also supported for existing operators;
prefer the salted hash. Credentials never enter frontend code or browser storage.
An unconfigured installation stays on the login screen.

Create new local credentials, without printing a password:

```powershell
.\.venv\Scripts\python.exe tools/setup_admin.py
Get-Content .env.admin | ForEach-Object {
  $pair = $_.Split('=', 2)
  [Environment]::SetEnvironmentVariable($pair[0], $pair[1], 'Process')
}
.\.venv\Scripts\python.exe runtime_entrypoint.py
```

Retrieve .env.admin.password locally and store it in a password manager, then
remove that file. Both files are ignored by Git and excluded from Docker builds.
On Windows use the user's private directory ACLs; on Linux the setup creates
files with mode 0600. For deployment load the generated hash configuration into
the deployment environment; deploy.sh forwards it and refuses unauthenticated
configuration before replacing a container. Do not regenerate existing identities.

Start the local gateway with `python runtime_entrypoint.py`; it keeps Streamlit on
127.0.0.1:8502 and serves the same-origin browser at port 8501. Docker uses the
same entrypoint and public port. Do not expose the internal Streamlit port.

The gateway exchanges a 90-second, single-use login grant for an opaque
HttpOnly/SameSite=Lax cookie. SQLite stores only SHA-256 hashes of session IDs
under DATA_DIR with owner-only permissions. Browser refresh restores the user
through a short-lived, single-use server proof; frontend identity values are not
trusted. The server rechecks session expiry and registry users on each rerun.
Sign-out revokes the session. Idle expiry
defaults to 30 minutes and absolute expiry to 8 hours; IIPP_SESSION_IDLE_SECONDS
and IIPP_SESSION_MAX_SECONDS configure those limits without extending the
absolute creation time. Set IIPP_TRUSTED_PROXY_IPS to the trusted proxy addresses
or CIDRs when TLS terminates before the gateway, so it can mark cookies Secure.
Login failures are generic and limited to five attempts per username per five
minutes per app process. Retain Streamlit XSRF and CORS protections.
Admin login is single-workspace authorization; enable the existing control plane
for customer/project roles and independent approvals.

## AWS roles and policies

Three separate reviewable read-only policies are supplied:

| File | Attach to | Purpose |
| --- | --- | --- |
| iam-dashboard-inventory-policy.json | Each account's IIPPInventoryReadRole | EC2, VPC, EKS, ECS, RDS, Lambda, S3, ALB/NLB/classic ELB and CloudWatch |
| iam-dashboard-organization-policy.json | Authorized organization discovery identity | Organization, root, OU and account reads |
| iam-dashboard-billing-policy.json | Inventory identity with billing authority | Cost Explorer GetCostAndUsage |

Replace REPLACE_ACCOUNT_ID in the inventory template with its target account.
Use partition-specific ARNs for GovCloud/China. List APIs that do not support
resource restrictions use Resource '*'; resource descriptions are restricted to
the target account and S3 location reads use s3:ResourceAccount. No object reads,
secret listing, resource writes, IAM mutation, or administration is granted.
CloudWatch metrics remain in the existing telemetry view; the Profile menu no
longer carries monitoring navigation. Existing provisioning/network validation
and Price List permissions are separate and must be retained.

The controller needs sts:AssumeRole only for configured target inventory roles
and secretsmanager:GetSecretValue only for configured connection secret ARNs
(plus kms:Decrypt on the specific key if using a customer-managed key). Keep the
existing controller trust principal and unique sts:ExternalId condition. Dashboard
policies do not change trust or execution permissions. The inventory session
identity is checked against the selected account before collecting resources.

Organizations hierarchy reads require a management/delegated identity with the
appropriate organization permissions. Set the approved o-... boundary in Configure.
Member inventory credentials are never silently replaced with management credentials.
Organization discovery does not itself grant access to inspect member resources.
Connect each member account for resource inspection.

Enable Cost Explorer outside the application and allow IAM billing access. A member
account may have only its own billing visibility; consolidated visibility requires
an authorized management billing identity. Select target billing (default) for
individual connections. Select organization billing only on an approved management
connection assigned to a customer permitted to see the entire organization. The
backend enumerates accounts under the exact approved boundary before querying CE
with explicit LINKED_ACCOUNT filters. A failed hierarchy read blocks that query.
Use a separate organization connection per authorized scope, never a shared
organization connection for mutually isolated customers.

The application queries DAILY UnblendedCost grouped by account/service and
account/region for the previous full UTC month through today (exclusive tomorrow).
Credits and refunds remain included in unblended usage totals; these totals are
not a finalized invoice. Estimated=true is provisional provider billing, distinct
from Price List compute estimates. No catalogue estimate appears in the billing
dashboard. Organization/member overlap is deduplicated by customer and account.
Missing provider or target coverage is marked partial. No currency conversion is
performed. CE API requests may be billable; costs cache for one hour, inventory
and hierarchy for five minutes. Explicit synchronization bypasses the cache.

The AWS IAM analyzer was installed but Windows denied execution of its binaries.
Policy templates therefore use the Service Authorization Reference fallback.
For reproducible analysis on a supported runner, review this command and its
output before attachment (dynamic service dispatch may need manual review):

```sh
uvx iam-policy-autopilot@latest generate-policies /absolute/path/dashboard_adapters.py --service-hints organizations ec2 eks ecs rds lambda elbv2 elb s3 ce cloudwatch --pretty
```

No policy is attached automatically. Validate final substitutions with IAM Access
Analyzer ValidatePolicy and the real target roles before customer acceptance.

## Azure

Reuse the configured Entra application or managed/workload identity. Assign Reader
on the approved management group and subscriptions for management-group get/list/
descendants, subscriptions and resource reads. Add Cost Management Reader at each
subscription (billing account access depends on the agreement). Avoid Owner,
Contributor and tenant-wide directory permissions. A custom role can restrict to
Microsoft.Management/managementGroups/read,
Microsoft.Management/managementGroups/descendants/read,
Microsoft.Resources/subscriptions/read,
Microsoft.Resources/subscriptions/resourceGroups/read,
Microsoft.Resources/subscriptions/resources/read,
Microsoft.Compute/virtualMachines/instanceView/read and
Microsoft.CostManagement/query/read. Ensure ARM tenant enumeration is available.
VM power state is read separately; generic ARM provisioning state is displayed
for other resources when available and is not asserted to be a runtime health check.
ARM resource enumeration includes AKS, ACR, Storage, SQL and App Services.
Cost integration uses the subscription-scoped ActualCost Query API. Configure
tenant ID, subscription ID and credentials in the existing Secrets Manager flow.

## Google Cloud

Enable Cloud Resource Manager, Cloud Asset, BigQuery and Cloud Billing APIs.
Use a service account or configured workload identity with resource hierarchy
get/list access (Organization Viewer and Folder Viewer, plus project get/list as
needed) and Cloud Asset Viewer at each target project. searchAllResources reads
Compute, GKE, Storage, SQL and Functions across locations; absent asset runtime
states are shown as unavailable. Read-only inventory creates no resources.

The Billing API does not return actual project usage charges through its public
catalogue. Configure a standard usage cost BigQuery billing export separately.
Grant BigQuery Data Viewer only on the export table/dataset and BigQuery Job User
on the query project. Billing Account Viewer is needed only for billing account
metadata, not for reading an already authorized export. Configure project.dataset.table
and query project. The query uses date/project parameters, includes credits and
caps bytes billed (default 1 GB; billing_max_bytes metadata overrides it). Query
jobs can incur BigQuery charges. Missing export history is unavailable, never zero.

## Validation and failure behavior

1. Test the connection with Configure and verify the returned target identity.
2. Synchronize cloud overview and inspect hierarchy. Check management account,
   nested OU/group/folder and target relationships against the provider console.
3. Inspect each connected target's resources and regions. Services denied by IAM
   appear as partial inventory, with per-service/region errors. Region discovery
   failure restricts the scan to the configured region and is explicitly marked.
4. Compare billing by service/account with the provider's matching cost basis,
   UTC date interval and currency. Inspect previous-month coverage and last sync.
5. Revoke one test read permission and refresh: prior live values must disappear,
   while the last successful synchronization time remains visible.
6. Verify denied customer access cannot use cache or saved reuse artifacts.

Use tools/validate_dashboard_access.py for a read-only CLI check of the configured
connections. Expired credentials, denied APIs, disabled billing, incomplete exports
and missing configuration return sanitized status messages without credentials.
No sample inventory is substituted. Last-sync timestamps are process-local and
reset on application restart. Per-resource billing allocation is unavailable;
service costs are shown separately rather than invented for individual resources.

Official API and permission references:

- [AWS Cost Explorer query](https://docs.aws.amazon.com/boto3/latest/reference/services/ce/client/get_cost_and_usage.html)
- [ECS service authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecs.html)
- [Load balancer service authorization](https://docs.aws.amazon.com/service-authorization/latest/reference/list_elb.html)
- [Azure Cost Management query](https://learn.microsoft.com/en-us/rest/api/cost-management/query/usage)
- [GCP billing export](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery)
