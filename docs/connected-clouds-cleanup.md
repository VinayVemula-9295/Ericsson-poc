# Connected Clouds cleanup

The provider cards show one Infrastructure Spend – MTD metric. AWS uses the
existing Decimal gross Usage record-type aggregation, with consolidated reads
preferred over overlapping member reads. Credits and net expenditure remain in
the backend and account billing views. Missing gross coverage is never inferred
from net expenditure. Azure/GCP cards are presentation-only POC data (1/4 and
1/3 hierarchy counts; USD 64.20 and USD 48.60). They do not trigger live billing.

AWS account tables retain seven columns; OU and account state remain in hierarchy
metadata. Member selection still opens live resource discovery. Partial inventory
has one concise notice; sanitized service/region/API diagnostics remain logged.

## Controller configuration

An administrator can set `organization_credential_source=controller`, the approved
`discovery_boundary`, and `billing_scope=organization` on an existing connection.
This uses the runtime AWS credential chain only for hierarchy/consolidated cost;
the member inventory role and Terraform execution role remain unchanged. The
default `role` source preserves the existing organization read role behavior.
Controller hierarchy and cost reads validate the discovered management account.
Customer authorization and explicit discovery boundaries remain mandatory.

Organizations has no friendly organization name or status field. The label is
derived from the live management account name; Discovery Verified describes the
read result, separately from actual account State and organization FeatureSet.

## Pending IAM approval

Runtime STS identified CloudPilotReadTargetMetricsRole in account 704298683641.
Its Organizations reads fail AccessDeniedException. Inspected policies lack these
allows; no boundary is configured. Its existing Cost Explorer reads work.
Review `iam-dashboard-organization-policy.json` (the seven requested read actions)
before applying this additive inline policy from an authorized administrator:

```sh
aws iam put-role-policy --profile management \
  --role-name CloudPilotReadTargetMetricsRole \
  --policy-name IIPPOrganizationsRead \
  --policy-document file://iam-dashboard-organization-policy.json
```

This command has NOT been executed. No member inventory permission expansion,
trust changes, provisioning flag changes, or cloud resource writes are included.
After approval, refresh hierarchy and billing and verify all seven Organizations
reads and MTD linked-account gross usage from the application runtime.
