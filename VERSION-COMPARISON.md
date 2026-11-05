# Release comparison — v10 to v18

| Version | Main contribution | Status in the v18 lineage |
|---|---|---|
| v10 | Base Work/Agent dashboard, cloud adapters and AWS Terraform saved-plan workflow | Superseded as base |
| v11 | Exact/nearest CPU and RAM matching, improved recommendation evidence | Retained in v14 base |
| v12 | Live AWS EC2 offering/spec discovery; Azure/GCP remain pricing references when not connected | Retained and extended with saved provider connections and inventory APIs |
| v13 | Backup, safe Docker deployment, rollback and cleanup scripts; reviewer-gated saved plans | Chosen v14 base; deployment safety preserved |
| dashboard refinement v5 | Alternate dashboard layout patch | Not layered over v13 because its target layout conflicts with the selected v13 Work/Agent UI |
| **v14** | IIPP branding; Terraform-only corrected architecture; provider Configure panel; Secrets Manager credential handling; live inventory adapters; S3-only state for new deployments; shared plugin cache | Safe implementation base; deployed by user before v15 update |
| v15 | Innominds CloudPilot manager-demo UX; shared provider/account top bar; modal Configure + success dialog; concise live Agent recommendations; Work → Agent → Work destroy workflow | Previous release |
| **v16** | InfraMind branding; Work-only live inventory; one-shot Configure and success dialogs; concise sanitized Agent responses; visually highlighted recommendation | Previous source package; recommendation ranking still proximity-biased for some candidates |

| **v17** | Cost-aware eligibility and ranking; exact-match-first, then lowest-cost/strategy ranking within eligible tier; explicit Recommended and Optimal labels; 6–8-line evidence-grounded Agent explanation after prompt; full priced candidate selection and exclusions | Previous source package |
| **v18** | Fresh AWS Price List Query API lookup on each new comparison; 5-minute bounded cache; actual price timestamps/effective dates; fail-closed pricing; visible source/timestamp; Agent explanation grounded in retrieved rates; controller `pricing:GetProducts` policy | Current source package; requires controller IAM permission and EC2 deployment |

## Why v13 was selected

V13 was the latest supplied bundle and already contained the safer Docker backup/health-check/rollback process and reviewer-gated Terraform plan/apply/destroy workflow. V10–V12 features were already present in that codebase. Layering v10/v11/v12 patches on top would duplicate and risk overwriting the newer safe-deploy work.

## Main v14 design changes

1. v14 introduced the formal platform branding; v15 introduced the visible identity Innominds CloudPilot; v16 established **InfraMind — Multi-Cloud Intelligent Provisioning**; v17 improves recommendation ranking and Agent explanation quality. Legacy technical identifiers (`cloudpilot-ericsson-poc`, Docker image/container names, `/opt/cloudpilot/data`, IAM role name and approval-token path) remain unchanged for in-place upgrade compatibility.
2. Architecture follows the supplied reasoning/provisioning flow but excludes Kubernetes and GitOps.
3. Configure panel saves provider secrets in Secrets Manager; only metadata and secret ARNs stay in the app data directory.
4. AWS comparison and plan validation use the selected target account's live EC2 APIs; no hardcoded AWS VM catalog fallback is used for discovery.
5. Terraform new deployments require an S3 backend; old local state deployments are preserved rather than silently migrated.
6. A shared plugin cache reduces repeated provider downloads; `terraform init` is globally serialized.
7. POC/TEST IAM policies and budgets are bootstrapped separately using administrator-run scripts.

## Not included / not yet live

- Azure/GCP Terraform resource stacks and their full plan/apply/destroy lifecycle.
- Private/telco provider adapters.
- Production-grade dynamic cost forecasting and hard budget enforcement. AWS Budgets only sends alerts.
- Actual creation of the two AWS member accounts, roles, budgets or state bucket; these require the user's AWS management/admin identity and valid email addresses.
- Deployment of v14 to the user's EC2 instance and live AWS/Bedrock/API validation.


## v15 — previous release: manager demo UX and lifecycle flow

- Renames the product-facing UI to **Innominds CloudPilot** and removes the right-side header logo.
- Adds one global provider/account selector shared by Work and Agent.
- Moves provider configuration into a modal; verified save opens a success dialog.
- Passes selected-account live inventory context into Agent prompts; the full live inventory and telemetry panel is displayed in Work only.
- Makes Agent recommendations concise and explicit about exact vs nearest safe VM matches, regional offerings, and unverified account quota/spare capacity.
- Moves destroy-plan preparation to an Agent handoff from Work; saved destroy plans and final destroy approval are shown only in Work.
- Keeps S3 as the mandatory backend for all new Terraform deployment state. Existing legacy local states remain untouched.


## v16 — previous release: InfraMind UX and Agent output hardening

- Renames the product-facing UI to **InfraMind — Multi-Cloud Intelligent Provisioning** and uses **Think Intelligent. Provision Anywhere.** as the tagline. The Innominds company logo stays in the left navigation.
- Moves the full Live AWS/account inventory and telemetry panel to Work only; Agent still queries the selected account as evidence for prompt responses.
- Fixes Configure modal persistence by using one-shot open events and an explicit Close control; success confirmation is also one-shot. Unrelated button clicks no longer reopen Configure.
- Adds a response sanitizer for `<thinking>`, `<analysis>`, `<reasoning>` and similar internal tags before Markdown rendering. Agent responses target one bold recommendation plus no more than three short evidence/limitation bullets.
- Highlights the recommended AWS configuration (or nearest non-eligible profile) while retaining the exact-match, closest-configurations, all-discovered-options, and detailed-comparison sections.
- Keeps Terraform-only provisioning, S3 state, human approval and the Work → Agent → Work destroy workflow unchanged.


## v17 ranking correction

The previous proximity-based ordering could put a more expensive candidate first even when a cheaper profile met all hard CPU/RAM requirements. v17 ranks candidates by: (1) eligible exact hardware-match tier, when one exists; otherwise (2) the tier meeting/exceeding both minimums; (3) the requested strategy and verified evidence within that tier. `Optimal` is the lowest priced eligible candidate in the preferred hardware tier. Latency is included only when comparable measured values are supplied.


## v18 live AWS price verification

Each new Agent comparison forces a fresh AWS Price List Query API request for the discovered EC2 shortlist, then ranks candidates using those returned rates. Successful low-level results are cached for no more than the configured TTL (default 300 seconds) between calls; manual refresh and a new comparison clear the price cache. Failed and ambiguous rates are not cached and never fall back to hardcoded/sample prices. AWS price rows include the actual retrieval timestamp, effective/publication date, product SKU and API endpoint region.

The API is called with the controller role and requires `pricing:GetProducts`; see `docs/aws-live-pricing.md`. The price is public On-Demand compute pricing and is not the account's final bill. No live request against the user's AWS credentials was run during the package build.
