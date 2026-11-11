# Customer POC validation and handoff

Source changes were implemented in the existing Streamlit application. Existing
uncommitted work and both Terraform execution services were preserved. No
application release, policy attachment or infrastructure apply/destroy was run.

## Implemented behavior

- Full-width provider rows in AWS, Microsoft Azure, Google Cloud Platform order,
  using the supplied images as layout references and existing vendor artwork.
- Approved organization/tenant/folder trees with management-account labels,
  account details, resource/service counts, regional inspection and runtime filters.
- Combined MTD billing and individual provider, target, service and previous-month
  comparisons. Currencies remain separate; partial coverage and last sync are
  visible. No public catalogue estimate enters billing totals.
- Explicit organization billing on authorized AWS management connections, with
  discovered target filters and deduplication of organization/member overlap.
- Cost Explorer pagination corrected using botocore's Paginator because the SDK
  does not provide a built-in GetCostAndUsage paginator.
- AWS EC2, VPC, EKS, ECS clusters/services, RDS, Lambda, S3, ALB/NLB and classic
  load-balancer discovery. Missing read permissions retain observed resources
  with unavailable status where possible and mark incomplete collection partial.
- Existing Azure ARM inventory plus VM power state, subscription cost queries;
  existing GCP Cloud Asset inventory and parameterized BigQuery billing export.
- Deployment selector, sequential progress, completed/current/pending/failure
  indicators, step inspection and protected execution-event logs. Existing plan,
  approval, checksum, state locking and destroy controls remain authoritative.
- Reuse backend listing and artifact loaders enforce SUCCEEDED plus source-bundle
  integrity and customer visibility. Pending, failed, cancelled, rejected,
  destroyed and in-progress sources are excluded. Execution revalidates sources.
- Admin login with salted password hashes, bounded server sessions, throttling,
  logout cleanup and existing optional control-plane identity support.
- Persistent Innominds header, responsive layouts, centered workspace navigation,
  professional empty states and cleaned Profile. Workload telemetry remains
  accessible from Dashboard for administrators.

## Main files changed

| Area | Files |
| --- | --- |
| Authentication and shell | app_auth.py, shell_ui.py, app.py, control_plane_ui.py, assets/platform.css |
| Cloud discovery and billing | dashboard_adapters.py, multicloud_dashboard.py, hierarchy_ui.py, cost_dashboard_ui.py, provider_ui.py |
| Deployment and reuse | deployment_workflow.py, deployment_history.py, deployment_history_ui.py, reuse_artifacts.py, reuse_ui.py |
| Runtime configuration | CONFIGURATION.example.env, deploy.sh, .dockerignore |
| Operator tools | tools/setup_admin.py, tools/validate_dashboard_access.py, tools/check_customer_poc_browser.py, tools/check_dashboard_browser.py |
| Permissions | iam-dashboard-inventory-policy.json, iam-dashboard-organization-policy.json, iam-dashboard-billing-policy.json |
| Tests and documentation | tests/test_customer_poc.py and updated existing UI suites; README.md, docs/multicloud-dashboard.md and customer-poc guides |

## Live AWS results, 10 November 2026 UTC

The checks used local AWS profiles with the actual dashboard adapters. They did
not create connections, change IAM or modify cloud resources. They validate
adapter/API behavior; registered connection Secrets Manager/STS setup must still
be checked in the eventual deployment environment.

| Read | Result |
| --- | --- |
| dev STS identity | Verified successfully |
| dev inventory across enabled regions | Available, 17 observed VPC resources, no failed collectors; no compute resources were returned |
| dev organization hierarchy | Permission denied, as surfaced by the application |
| dev Cost Explorer after pagination correction | Available, 114 daily account/service and account/region rows, USD, 1 October through 10 November inclusive |
| management organization hierarchy | Available, 6 nodes, 3 accounts, 1 management account |
| management organization Cost Explorer | Available, 654 daily rows covering all 3 accounts, USD |

Current provider costs were provisional according to Cost Explorer. No monetary
values or secrets are included in the verification logs. Private local evidence:
.cache/customer-poc-live-dev-billing.json and
.cache/customer-poc-live-organization.json. The initial dev inventory report's
billing failure was superseded by the successful post-fix billing report.

## Automated validation

Final Windows-compatible suite: **339 passed in 126.83 seconds**. A focused
hierarchy/billing/deployment check passed 48 tests. The generated local
admin credential pair was also verified against its stored salted hash without
printing either credential.

Run the Windows-compatible regression suite with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --tb=short --ignore=tests/test_terraform_safety.py
.\.venv\Scripts\python.exe tools/check_customer_poc_browser.py
```

Browser validation passed in headless Edge: real login/logout, ordered provider
rows, unavailable hierarchy, configuration dialog, Deployment/Agent/Reuse
navigation, fixed header position during scrolling, 1440/1280/768/390 layouts,
no horizontal page overflow and no browser or Streamlit exceptions. Screenshots
are in .cache/customer-poc-browser/. Python compilation and git diff --check pass.

| Acceptance item | Verification |
| --- | --- |
| Provider ordering and hierarchy navigation | Browser and Streamlit tests; live AWS hierarchy read |
| Sequential deployment state and execution events | State-mapping and existing lifecycle regression tests |
| Organization, provider and target billing | Aggregation/deduplication tests and actual AWS organization/account CE reads |
| Running service discovery | Adapter coverage and actual dev account scan; active EC2/EKS/ECS fixtures use mocks |
| Cloud permissions | Read-only policy artifacts and setup guide; IAM attachment/Access Analyzer validation remains operator-owned |
| Fixed header, branding and responsive workspace | Browser screenshots and scroll position assertions |
| Admin login, expiry and logout | Browser, password/throttle/session/registry authorization tests |
| Successful-source-only Reuse | Backend integrity/status/visibility and existing planning tests |
| Profile cleanup and retained monitoring | Browser and app regression tests; Dashboard telemetry entry retained |
| Existing recommendation/provisioning behavior | Windows-compatible regression suite; Unix safety suite requires Linux |

The native full-suite command cannot collect tests/test_terraform_safety.py
because fcntl is Unix-only. Its 11 tests require the Linux runtime; WSL and Docker
are unavailable on this host. No Terraform apply/destroy was performed.

## Setup and remaining boundaries

See [account permissions and setup](customer-poc-access.md). A unique admin password
was generated in local ignored .env.admin.password; .env.admin contains its salted
hash. Load that environment before starting the app, retrieve the password locally
and retain it in a password manager. No password was printed or committed.

Azure and GCP require actual credentials, authorized tenant/project scopes and
billing configuration for live validation. GCP actual usage costs require the
BigQuery export; the public Billing catalogue is not a usage ledger. AWS policies
are reviewable templates and were not attached. IAM analyzer execution was denied
by Windows; review substituted policy ARNs and validate them on an authorized runner.

Per-resource cost allocation and unavailable resource runtime states remain marked
unavailable. Last successful sync timestamps use the process cache and reset on
restart. Login throttles are process-local. Azure/GCP execution maturity retains
the existing plan-only/disabled gates; this UI update does not enable new writes.
