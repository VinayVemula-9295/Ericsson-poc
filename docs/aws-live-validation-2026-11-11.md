# AWS authentication and live integration validation — 2026-11-11

The expired-SSO finding from the previous report is superseded. All four profiles
successfully called STS. Organization, account billing, member inventory and the
running dashboard were checked with actual AWS responses. No policies, trust
relationships, saved connections, deployed files or infrastructure were changed.

## Authentication and the deployed identity

| Profile / runtime | Account | Actual authentication | Result |
| --- | --- | --- | --- |
| default | 704298683641 | root | STS succeeded; diagnostic use only |
| management | 704298683641 | root | STS succeeded; diagnostic use only |
| dev | 695729002971 | AWSReservedSSO_AdministratorAccess_de0895382bf96769 | SSO active; STS succeeded |
| Demo | 333347372766 | AWSReservedSSO_AdministratorAccess_617044ce903f3817 | SSO active; STS succeeded |
| Running EC2 application | 704298683641 | CloudPilotReadTargetMetricsRole instance role | STS succeeded; non-root |

The running controller ARN was
`arn:aws:sts::704298683641:assumed-role/CloudPilotReadTargetMetricsRole/i-0603365940d403eb5`.
It successfully read the existing connection secrets and assumed
`IIPPInventoryReadRole` separately in both member accounts. STS returned the
expected account IDs in each target session. This validates the actual runtime
role path independently of the local SSO administrator profiles.

The member trusts name the exact management controller role. Neither currently
has an `sts:ExternalId` trust condition. Successful assumption therefore validates
the current principal trust, not enforcement of an External ID. The application
continues to support secret-stored External IDs. Any trust-policy hardening must
be reviewed and applied manually with the matching stored value.

No SSO profile names have been embedded in production modules. The new
`tools/validate_live_aws.py` accepts explicit operator-supplied profile arguments.
Its standalone management diagnostic uses the supplied profile; it does not
relax the production adapter's root rejection.

## Organization discovery

Management-profile diagnostics executed all seven implemented Organizations reads:
DescribeOrganization, ListRoots, ListAccounts, ListOrganizationalUnitsForParent,
ListParents, DescribeAccount and DescribeOrganizationalUnit. Paginated lists were
fully consumed. Actual results:

* Organization: `o-x32fez8kdn`, feature set ALL.
* Management: Naresh-Acc, `704298683641`, parent `r-4ktl`.
* Root: `r-4ktl`.
* OU: IIPP-Sandbox, `ou-4ktl-6244uz84`, parent `r-4ktl`.
* Demo-Account: `333347372766`, parent IIPP-Sandbox.
* Test-Account: `695729002971`, parent IIPP-Sandbox.
* Total: **1 organization, 3 ACTIVE accounts, 1 management account**.

The deployed controller separately called DescribeOrganization, ListRoots and
ListAccounts: **all returned AccessDeniedException**. Its inspected identity
policies contain no Organizations allow actions. Its two saved member connections
also have no discovery boundary, organization role ARN or organization billing
scope. Both identities and configuration explain why the live UI still reports
Organizations Unavailable, despite successful local management-profile diagnostics.

## Billing reconciliation

Window: UTC calendar MTD starting 2026-11-01; exclusive request end 2026-11-12,
including the current UTC date at the time of the read. Previous-month queries
covered October. Observed values below were fetched around **03:13–03:17 UTC**
(08:43–08:47 IST). They are Cost Explorer billing data, not instantaneous usage.

Metric: **UnblendedCost** for every comparison. The selected account/service
aggregation was reconciled against an independent, unfiltered consolidated query.
The control query is used only for validation and is never added to account sums.

| MTD scope | Exact USD value |
| --- | ---: |
| Management account | -0.0000006135 |
| Demo-Account | -0.0000001373 |
| Test-Account | -0.0000001220 |
| Organization account sum | **-0.0000008728** |
| Independent consolidated total | **-0.0000008728** |
| Difference | **0** |
| Two connected member accounts only | **-0.0000002593** |

October's account sum and consolidated control also matched exactly:
USD -0.0000003962, difference zero. The organization total includes the management
account exactly once. Connected spend is a subset and is not added to it.

| Organization MTD billing record type | Exact USD value |
| --- | ---: |
| Usage | 14.8161993848 |
| Credit | -14.8162002576 |
| Tax | 0 |
| Refund | No record returned |

Credits offset essentially all charges. The tiny negative residual is present in
the provider response and was preserved, not clamped or subtracted twice. Both
organization and connected net values render as **USD -0.00** with the current
two-decimal money formatter. That display is a verified near-zero bill, not a
failed retrieval or absence of infrastructure usage. These are applied billing
credits, not an available promotional-credit balance.

Account-specific Cost Explorer reads succeeded under both SSO sessions and under
both deployed inventory roles. Service, daily, region and record-type groups were
retrieved using the current adapter. Member signed records:

| Account | Usage USD | Credits USD | Net USD |
| --- | ---: | ---: | ---: |
| Demo | 0.9667729378 | -0.9667730751 | -0.0000001373 |
| Test | 3.1033988366 | -3.1033989586 | -0.0000001220 |

The non-root deployed controller also successfully called GetCostAndUsage and
GetDimensionValues: consolidated MTD was USD -0.0000008728, with three linked
accounts returned. Thus **runtime consolidated billing permission is working**;
organization discovery and connection scope currently prevent the dashboard from
presenting it as organization billing.

## Inventory: SSO diagnostics versus actual application roles

Scope: configured region **ap-south-1**, plus the adapter's global S3 listing.
No claims are made about workloads in other regions. All supported list APIs
were paginated. The SSO administrator reads succeeded without collector warnings.

| Account | SSO inventory observations | EC2 | Other listed workload services |
| --- | --- | ---: | --- |
| Demo | 1 VPC, 542 CloudWatch metric descriptors | 0 instances | No rows returned |
| Test | 1 VPC, 510 CloudWatch metric descriptors | 0 instances | No rows returned |

CloudWatch descriptors are historical/available metric definitions, not running
servers or proof that hundreds of workloads exist. Unknown metric runtime states
were left unavailable. Inventory row counts can change as metric descriptors appear.

The current local adapter was also executed **in memory** inside the existing
container, using its actual Secrets Manager connections and member roles. No
deployed source file was replaced. This confirmed partial inventory coverage for
both application roles:

| Read area | Demo role | Test role |
| --- | --- | --- |
| STS target identity | Verified | Verified |
| EC2 instances / EBS volumes | Successful, empty | Successful, empty |
| VPC | 1 available VPC | 1 available VPC |
| CloudWatch ListMetrics | Successful | Successful |
| Auto Scaling DescribeAutoScalingGroups | AccessDenied | AccessDenied |
| RDS DescribeDBInstances | AccessDenied | AccessDenied |
| EKS ListClusters | AccessDeniedException | AccessDeniedException |
| ECS ListClusters | AccessDeniedException | AccessDeniedException |
| Lambda ListFunctions | AccessDeniedException | AccessDeniedException |
| ELB classic/v2 DescribeLoadBalancers | AccessDenied | AccessDenied |
| S3 listing | Unavailable; existing policies lack ListAllMyBuckets | Same |
| Account/service/record-type billing | Verified | Verified |

Denied inventory cannot establish that a service has no resources. Downstream
reads such as EKS DescribeNodegroup could not be exercised when the prerequisite
list returned denied or empty. They remain unverified under the application role.

## Actual dashboard browser check and deployed build

Signed into the existing site at `http://13.203.223.14:8501` using the existing
protected administrator credentials, then signed out. No configuration was saved,
and no Evaluate, Terraform, approval, apply or destroy action was submitted.

Observed:

* Heading: **Cloud overview**. The newer **Connected Clouds** heading is not deployed.
* Organizations: **Unavailable**.
* AWS accounts: **2**, both shown as verified; verification timestamps remain their
  stored previous connection-test timestamps because validation did not rewrite them.
* Visible-account and connected-account billing: **USD -0.00**, backed by live reads.
* Demo/Test account table shows the correct account IDs and ap-south-1.
* New account-resource dialog is absent from deployed source.
* Deployment history and Provisioning Agent loaded successfully.
* **Zero Streamlit exceptions and zero browser JavaScript errors** during the check.

SHA-256 hashes of app.py, dashboard_adapters.py, multicloud_dashboard.py and
hierarchy_ui.py in the running container differ from the local implementation.
The newer source changes are therefore not evidence of current website behavior.
The requested no-deployment constraint was honored.

## IAM findings and remaining manual configuration

Read-only IAM inspection confirmed no permissions boundary on the controller or
either inventory role. SCPs were enumerated at root, OU and account levels:
FullAWSAccess is attached, and root also has DenyLeaveAndCloseAccount, which denies
only organizations:LeaveOrganization and account:CloseAccount. No inspected SCP
denies the requested inventory reads. The member roles contain AWSRevokeOlderSessions;
fresh runtime AssumeRole sessions successfully read allowed APIs. Its conditional
deny uses DateLessThan on aws:TokenIssueTime with 2026-11-03 cutoffs, confirmed by
GetRolePolicy. It should not be interpreted as a blanket denial of current sessions.

Required manual steps, without adding administrator permissions:

1. Review `iam-dashboard-organization-policy.json` and grant its seven Organizations
   read actions to the authorized management read identity. The existing controller
   already has the two required Cost Explorer reads. A separate management read
   role may be used; then configure its trust and exact controller AssumeRole ARN.
2. Review `iam-dashboard-inventory-policy.json` against the denied reads above and
   apply only the required service read additions to each IIPPInventoryReadRole.
   Preserve existing provisioning and connection permissions. The reusable
   `policy_templates/aws/dashboard-inventory-role.json` is an alternative for a
   separately reviewed role; do not recreate or overwrite existing roles blindly.
3. If adopting External ID conditions, update trust and the matching protected
   connection secret together under an approved change. Existing trusts currently
   restrict the controller principal but do not enforce External ID.
4. In the new configuration UI, set the approved organization boundary and
   organization billing scope on an authorized connection, with the management read
   role ARN where separate from its member inventory role. The same connection
   currently supplies one External ID to both roles; use compatible trust settings
   or separate connections. No root credentials or local SSO names are needed in
   the deployed application.
5. Roll out the local implementation only under a separately authorized deployment.
   Revalidate the organization card, management/account breakdown, connected subtotal
   and resource dialogs afterward. This validation task did not authorize rollout.

There is no need to refresh SSO again to address the observed application failures.
They are deployed-code, connection-scope and least-privilege policy gaps.

## Reproducible diagnostics and evidence

Run the profile diagnostic explicitly; profile names are CLI parameters only:

```powershell
.\.venv\Scripts\python.exe tools/validate_live_aws.py --profiles default management dev Demo --management-profile management --member-profiles Demo dev --region ap-south-1 --output .cache/aws-live-validation.json
```

Evidence produced in this workspace:

* `.cache/aws-live-validation.json`: STS, hierarchy, account/service/record-type costs,
  SSO inventory and consolidated reconciliation.
* `.cache/aws-runtime-validation.json`: actual runtime controller, saved connection
  metadata, dedicated AssumeRole results and deployed dashboard backend snapshot.
* `.cache/aws-current-runtime-access.json`: current adapter tested in memory against
  runtime identities, including explicit denied operations.
* `.cache/aws-iam-validation.json`: existing policy/trust metadata and SCP attachments;
  credential values and External IDs are excluded.
* `.cache/aws-role-session-revocation.json`: the existing DateLessThan revocation
  conditions, confirming the deny applies to older sessions.
* `.cache/aws-live-dashboard/result.json`, `deployed-dashboard.png`,
  `deployed-accounts.png`: authenticated live browser evidence.

All were newly generated in this validation. Billing figures are point-in-time
evidence and may change as AWS updates the open period. The previous mocked UI
screenshots and prior regression results were not used to establish live access.

The new diagnostic aggregation/sanitization tests and existing enhancement tests
passed: **9 passed**. Python compilation of the diagnostic tool also passed. No
production application logic was changed during this authentication validation.

AWS references: [GetCostAndUsage](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html),
[ListAccounts](https://docs.aws.amazon.com/organizations/latest/APIReference/API_ListAccounts.html).
