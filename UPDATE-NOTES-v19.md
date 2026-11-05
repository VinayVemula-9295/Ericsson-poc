# InfraMind v19 — requirement parsing and per-cloud selection

## Fixes
- Correctly parses common request variants including `4 Vpcu and 8Gib Server`, `4 vCPU and 8 GiB RAM`, and `4 CPU / 8 GB VM`.
- Prevents silently defaulting to 2 vCPU / 8 GiB when the user supplied the values in those supported formats.
- AWS discovery preserves every exact CPU/RAM profile returned by live EC2 specifications, even when exact matches exceed the normal candidate shortlist limit.
- Work recommendation layout now presents three distinct roles under each provider tab: Selectable, Recommended, and Optimal.
- Selectable prefers an exact hardware match; if none exists, it uses the closest candidate meeting both minimum CPU and RAM.
- Recommended follows the requested deterministic ranking strategy within the preferred hardware-fit tier.
- Optimal is the lowest-priced eligible option within that same preferred hardware-fit tier.
- Each candidate card has an explicit action. AWS actions can continue to the existing Terraform plan flow only after AWS offering/spec checks; Azure/GCP remain comparison-only because this release has no Azure/GCP Terraform provisioning stack.
- Reordered the UI so Detailed comparison and exclusions appears before Choose from all returned priced configurations.
- Removed the Platform / Terraform-only provisioning / Human approval / tagline / Controller region block from the left sidebar. Product identity and workflow remain in the main interface.

## Validation
- `I need 4 Vpcu and 8Gib Server` parses as 4 vCPU / 8.0 GiB with no defaults.
- AWS shortlist regression verifies all exact profiles are retained.
- Full local unit suite: 84 tests passed.
- Python compilation passed.
- No live EC2 API, AWS Price List API, browser session, Terraform plan/apply, or EC2 deployment was run in this build environment.
