# Dashboard and provisioning redesign

The existing Streamlit app now uses the stakeholder preview's colours, spacing,
rounded containers, hover effects, accessible focus states and reduced-motion
support. Sidebar order: Dashboard, Provisioning Agent, Deployment. The Innominds
logo sits above the product title. The top-right Control Center menu provides
Configure and Infrastructure & Monitoring; account setup uses the existing dialog.

## Completed functionality

- Read-only dashboard groups AWS, Azure and GCP registry entries, counts distinct
  identities, shows last verification and configured regions, and reads the ten
  latest deployment records. Locally stored vendor brand images replace letters.
- Deployment has a consolidated table, search, status filtering, ten-record pages,
  detail selection and existing reviewed destroy operations. View All routes here.
- Redeploy/Retry safely reads both record formats, loads allowlisted inputs, and
  returns to Evaluate. Original Terraform/state are retained as source references;
  fresh planning creates a new state key. Destroyed deployments need no live state
  for configuration reuse. Missing sizing/configuration produces an explicit error.
- Provisioning Agent supports new requests and history browsing. Evaluate refreshes
  discovery. ASK preserves exact requested hardware (and saved SKU when available);
  Recommended and Optimal remain successive eligible hardware upgrades.
- Explicit selection and Use Configuration gate inputs. Generate Terraform IaC
  produces the existing generator's source plus a separate input-value section.
  Input editing, guarded validation, Terraform Plan and human approval are separate.
  Planning consumes edited values and runs the existing Terraform CLI validation,
  provider/policy checks and saved-plan flow. Edits invalidate previous approval.
- Initiator is user supplied and stored separately from reviewer. Old records
  show unavailable initiator metadata instead of inventing an identity.

## Backend compatibility and limitations

No REST API or database migration was introduced. Existing Python service signatures
remain compatible. The saved-plan record adds optional initiator, source-deployment,
subscription and project fields. New portable modules normalize dashboard data and
validate supported draft inputs; a safe reuse reader extends the existing store.

Account-scoped billing is not implemented in existing provider adapters. Dashboard
spend therefore displays Unavailable. Controller billing and compute estimates
remain in Infrastructure & Monitoring and are not misrepresented as target-account
spend. Regions are configured defaults, not exhaustive regional discovery. Connected
means last verified, not continuous health monitoring.

Azure/GCP execution and any previously disabled execution targets remain disabled.
No feature flags were enabled, credentials changed, live cloud mutation performed,
or application release deployed. Full Terraform validation requires the Linux runner.
The portable Validate action checks supported values and protected source structure;
CLI/live-reference validation occurs before Terraform planning. Secrets and protected
security settings remain outside editable draft inputs.

## Changed and new files

Application: `app.py`, `agent_flow_ui.py`, `deployment_history.py`,
`deployment_history_ui.py`, `poc_planner.py`.

New modules: `dashboard_ui.py`, `terraform_draft.py`.

Visual assets: `assets/platform.css`, `assets/aws-logo.png`, `assets/azure-logo.svg`,
`assets/azure-logo.png`, `assets/gcp-logo.png`, `assets/gcp-official.svg`,
`assets/azure-logo.ico`, `assets/gcp-logo.ico`, `assets/PROVIDER-LOGOS.md`.

Verification: `tests/test_dashboard_redesign.py`, updated `tests/test_agent_ui.py`,
`tests/test_agent_plan_policy.py`, `tests/test_poc_ui.py`, `tests/test_app_helpers.py`,
`tests/test_multicloud_features.py`, `tests/test_deployment_history.py`, and
`tools/check_dashboard_browser.py`.

Preview: `../ui-previews/08-stakeholder-preview.html`, `stakeholder-preview.js`, and
`check_stakeholder_preview.py`. Preview accounts and actions remain illustrative.

## Verification

- Final portable regression suite: 261 tests passed in 45.39 seconds;
  recorded in `.cache/redesign-release-check.txt`.
- Updated preview: desktop/mobile checks passed; no external requests or JS errors.
- Running application: empty-store dashboard, three provider images, configuration
  dialog, Deployment navigation and mobile overflow checks passed.
- Python compilation completed. There is no separate frontend package build.
- Full collection is blocked on native Windows by `fcntl` in Terraform safety tests.
  WSL and Docker are unavailable; no Linux/live Terraform verification is claimed.

Test screenshots are in `.cache/dashboard-application*.png`; preview screenshots are
in `../ui-previews/.artifacts/08-*.png`.
# Current refinement

The provider cards, consolidated history table and direct stored-script reuse now
follow [Saved Terraform reuse](saved-script-reuse.md), which supersedes the earlier
Evaluate-based reuse behavior described below.

