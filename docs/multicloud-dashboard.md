# Unified cloud dashboard

The Dashboard uses the existing Streamlit shell and connection registry in both
single-workspace and authenticated control-plane modes. Single-workspace mode now
requires admin login; customer/project authorization uses the control plane.
See [current access and billing configuration](customer-poc-access.md). Configure opens the provider form
directly. Deployment pagination is below the table; running deployments still
update automatically.

## Overview cards and account details

AWS, Azure and GCP appear as three cards on desktop and stack on narrow screens.
Organization/tenant counts reveal authorized names, IDs and metadata on hover,
keyboard focus or tap. A failed discovery shows an unavailable count, or a known
count with `+` when additional roots may be missing.

Infrastructure spend is the combined MTD amount across visible organizations and
targets. Approved organization billing includes management-account charges, such
as the server hosting this application. Overlapping member reads are counted once.
The connected-account subtotal includes each configured target once, including
offline targets when billing is available. Currency totals remain separate.

View accounts/subscriptions/projects expands a table below the cards with name,
ID, configured region, MTD cost and last verification. Green denotes a successful
connection verification; grey denotes offline/unverified or discovered targets
without a connection. This is not a continuous health probe. Missing billing is
Unavailable; incomplete coverage is partial. Explore hierarchy and Connect/manage
remain available from this panel. The previous separate expenditure panel and
dashboard workload telemetry expander have been removed.

The branded header is fixed at the viewport top; Streamlit's native running/Stop
control sits immediately beneath it at the right. WORKSPACE is centered across
the sidebar above navigation. The Deployment workflow stepper is removed from the
history page; history details and reviewed operations remain available.

## Discovery and authorization

An administrator sets an Approved discovery boundary on a connection:

| Cloud | Boundary | Discovery reads |
| --- | --- | --- |
| AWS | Organization ID | DescribeOrganization, ListRoots, ListOrganizationalUnitsForParent, ListAccountsForParent |
| Azure | Entra tenant ID | Tenant/subscription reads and Microsoft.Management management-group reads/descendants |
| GCP | organizations/ID or folders/ID | Resource Manager organization/folder get and folder/project list |

Assign each connection to its customer/project using the existing control plane.
A discovery boundary grants visibility of its discovered descendants to that
customer. Do not assign an organization-wide discovery connection to a customer
that must see only part of that organization. Leave the boundary empty to display
only the configured target. Permissions can restrict discovery, so unavailable
organization counts must not be read as zero.

Accounts discovered through a boundary require matching configured target
connections before inventory/billing inspection. Organization discovery credentials
are never automatically reused for another target. Existing account identity
checks, STS External IDs and provisioning safeguards remain in place.

## Credentials and read permissions

AWS uses its configured inventory role and controller source identity. Organization
discovery requires a management/delegated identity authorized for those calls.
Inventory needs EC2 DescribeRegions/DescribeInstances/DescribeVpcs, RDS
DescribeDBInstances, EKS ListClusters/DescribeCluster, Lambda ListFunctions, S3
ListAllMyBuckets/GetBucketLocation. Billing separately needs ce:GetCostAndUsage.
Keep resource and role scopes as narrow as each API supports; no IAM changes are
performed by the dashboard.

Azure supports an explicit client secret, managed identity, or workload identity.
Workload identity needs the tenant, client ID, and a server-mounted federated token
file. Managed identities must exist on the runtime host. Grant resource Reader at
the intended subscriptions and management-group read visibility at approved scopes;
grant Cost Management Reader separately. Entra tenant names come from the ARM
tenants response, avoiding an additional Graph directory-read grant.

GCP supports a service-account key or an external_account federation configuration
stored in Secrets Manager. Federation configuration is administrator-trusted input:
only use configurations generated for the runtime's trusted identity provider.
Enable Resource Manager and Cloud Asset APIs. Inventory uses Cloud Asset
searchAllResources at the project scope, preserving native asset types across
Compute, Storage, Networks, SQL, GKE, and Functions. It includes additional asset
types returned by that API. State is shown when present, otherwise Unavailable.
Inventory visibility reflects the source API's supported assets and freshness.
Grant cloudasset.assets.searchAllResources and Resource Manager reads at the
approved scope rather than broad editor/owner roles.

Credential setup retains existing secret-based modes. For Reconnect, blank secret
inputs retain the saved secret. Tokens are refreshed through the selected SDK
credential, never by falling back to a different identity.

## Billing

AWS uses UnblendedCost and Azure uses ActualCost. GCP uses the standard BigQuery
billing export: set project.dataset.table and the billing query project in
Configure. Enable billing export separately; it is not created automatically.
The query identity needs BigQuery job creation in the query project and read access
to the export table. The default maximum bytes billed is 1 GB per query; optional
connection metadata `billing_max_bytes` changes this limit.

GCP queries include credits and restrict dates and project IDs with parameters.
Export gaps and missing historical data cannot establish zero spend. Displayed
periods use UTC calendar months; the previous month is a full month. Cost Explorer
and Azure billing are separate provider bases, not normalized invoice accounting.
Currencies remain separate. Summaries deduplicate target costs and mark failed or
incomplete coverage as partial. Explicit AWS organization billing can cover
discovered descendants with the approved management billing identity. Other
connections cover configured targets. No catalogue estimate is substituted.

Provider data is cached in memory (hierarchy/inventory five minutes; billing one
hour), keyed by principal, customer ownership, complete connection revision, target,
and section. Every cache read rechecks connection visibility. Explicit synchronization
bypasses the cache. A failed refresh replaces the prior result with unavailable
data while preserving its last successful synchronization timestamp. Cache entries
are bounded and disappear on process restart. No secrets are stored in this cache.

## Verification

Mocked tests cover provider discovery, inventory normalization, access-before-cache,
target matching, missing configuration, partial failures, refresh failure, cost
aggregation, and Streamlit drill-down. Existing provisioning tests remain separate.
Live acceptance must verify authorized hierarchies, all requested service families,
region completeness, billing periods, federation renewal, and customer isolation
against dedicated test tenants. Native Windows cannot collect Terraform safety
tests because they require Unix fcntl. Run those on the Linux runner.
