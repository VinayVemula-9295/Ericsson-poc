# Gross infrastructure usage cards

Presentation update: [Connected Clouds cleanup](connected-clouds-cleanup.md)
supersedes the breakdown-card layout below. Cards now show only Infrastructure
Spend – MTD; credits/net remain internal and in account billing views.

Connected Clouds now emphasizes **Gross Infrastructure Usage – MTD**, followed by
signed **Credits Applied – MTD**, **Net Expenditure – MTD**, and connected-target
**Usage – MTD**. Billing details expose exact amounts and other billing record types.
This guide supersedes the previous net-first card presentation.

AWS uses actual Cost Explorer UnblendedCost grouped by RECORD_TYPE. Only Usage
records contribute to the gross headline. Credit records are shown separately;
refunds, taxes, fees and other adjustments remain separate in billing details.
Net expenditure continues to use the existing service-based total. Alternative
service, region and record-type views are never added together.

The aggregation runs after existing account/customer deduplication. Organization
reads include management and member accounts exactly once; the connected subtotal
selects only successfully verified accounts. The existing net totals and account
table/dialog cost semantics remain unchanged. New `billing` summaries alongside
net totals expose gross_usage, credits, net, adjustments and partial coverage.

Amounts remain Decimal and currencies stay separate. The period is the UTC calendar
month. Zero and unavailable remain distinct. Negative zero is normalized only in
display formatting; precise signed values remain available in billing details.

GCP's BigQuery query now separates regular cost before credits, credits, and other
cost types while preserving the existing net result. This is regular exported cost,
not undiscounted public list pricing. Azure's current actual-cost integration can
display net expenditure but cannot establish gross/credits; those components remain
unavailable for live Azure connections until billing-detail ingestion is configured.

## Explicit POC illustrations

As requested, unconfigured Azure/GCP cards have presentation-only examples below
USD 100, visibly labeled **Demo data · Illustrative billing**:

| Card | Gross | Credits | Net |
| --- | ---: | ---: | ---: |
| Azure | USD 64.20 | USD -20.00 | USD 44.20 |
| GCP | USD 48.60 | USD -10.00 | USD 38.60 |

Samples are not inserted into service results, account details, inventory, exports,
or aggregate billing. They do not fabricate connection/organization counts or
last-sync timestamps. Configured connections and API errors suppress samples.
Actual billing always takes precedence, including a genuine zero-cost response.

## Implementation and validation

`multicloud_dashboard.py` adds reusable breakdowns and verified-connected gross
aggregation. `hierarchy_ui.py` owns card rendering and isolated illustrations.
`dashboard_adapters.py` preserves GCP cost/credit components. The shared money
formatter suppresses negative zero, and card CSS retains responsive layouts.

Fresh read-only AWS diagnostics at **2026-11-11 03:41 UTC** confirmed:

| Organization MTD | Exact USD | Card display |
| --- | ---: | --- |
| Gross usage | 14.8161993848 | USD 14.82 |
| Credits | -14.8162002576 | USD -14.82 |
| Net expenditure | -0.0000008728 | USD 0.00 |

Connected Demo/Test gross usage was USD 4.0701717744 (USD 4.07 displayed), using
the connected-account scope from the previously inspected runtime registry.
The three-account sum matched a separate consolidated record-type query for usage,
credits and net expenditure. No historical AWS amounts are production constants.
Sanitized evidence is in `.cache/gross-billing-live.json`.

Management credentials were used only for operator diagnostics. The production root
guard remains intact. No IAM policies, saved cloud connections or deployed files
were changed. The live website still requires the previously documented organization
permissions/configuration and a separately authorized deployment of this source.

Automated coverage includes credit/adjustment separation, gross/net reconciliation,
organization/member overlap, duplicate connections, connected subsets, currencies,
zero and missing values, GCP query mapping and demo/live precedence. Browser checks
cover desktop/mobile cards, demo labels and existing navigation. The local BigQuery
SDK is absent, so GCP query mapping is mocked; live Azure/GCP billing is unverified.
The SDK dependency is already present in requirements.txt.

Validation result: **359 Windows-compatible tests passed**, Python compilation
passed, and the final desktop/mobile browser acceptance passed with no browser
exceptions. The Linux-only fcntl Terraform safety suite remains excluded on this
Windows host; no Terraform execution code was changed.

GCP schema reference: [standard billing export](https://cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage).
