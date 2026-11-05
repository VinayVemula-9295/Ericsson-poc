# Linux / Windows provisioning POC

The Agent no longer shows a POC toggle or separate extract/confirm/research/
explain/download steps. The current UI includes automatic explanations and real
saved-plan approval/apply when the runner is enabled. See
[the current workflow](agent-prompt-workflow.md) for usage and requirements. The
remaining content describes the earlier review-only POC baseline; live cloud
validation is still required for the new targets.

Agent defaults to **Extended Linux / Windows POC**. Turn the toggle off to use
the preserved v19 AWS approval/apply/destroy workflow.

1. Describe the workload and extract requirements; no cloud APIs run yet.
2. Confirm CPU/RAM, Linux or Windows, workload type and mandatory constraints.
   Residency requirements need explicit provider region IDs. Confidential or
   restricted requests use deterministic output rather than external AI.
3. Choose tested connections or public catalogue references; find options.
4. Inspect evidence and exclusions, select an option and download configuration.
5. With a verified connection, supply non-secret inputs for a real saved plan.
   This requires the Linux runner, enabled workflow and central S3 backend.

## Coverage and remaining validation

- AWS: live hardware discovery, Linux/Windows pricing and Amazon image discovery.
  Quota ceilings may be retrieved; usage/headroom and spare capacity stay unknown.
- Azure: live standard-family specifications with a tested connection and public
  OS-specific prices. Unconnected specifications retain labelled POC references.
- GCP: live standard-family specifications with a tested connection; N2 CPU/RAM
  pricing plus Windows licence meters. Other discovered families have unavailable
  pricing. Unconnected specifications retain labelled POC references.
- Kubernetes: Linux node sizing with 20% system overhead and an optional spare
  node. EKS/AKS/GKE configurations are exported. Version/node compatibility,
  pod/IP limits and control-plane prices still require validation.
- Policy: mandatory residency, processing location, encryption, networking,
  quota and latency checks fail closed when evidence is unknown. A catalogue
  row cannot prove encryption or private networking of a future deployment.
- Research: bounded official documentation retrieval with a 24-hour cache;
  reference text does not establish policy compliance or launch capacity.
- Terraform: nine provider/OS/target variants with pinned provider versions and
  S3 backends. Configuration export is distinct from a real binary saved plan.
- Review: token-protected checksum-bound POC approval/rejection with one-hour
  expiry. New-target execution remains disabled pending live validation.

Provider schemas and real plan/apply/destroy need Linux/cloud validation.
Existing AWS state and approval workflows remain separate and preserved.

## Latency evidence

Upload a JSON array; replace timestamps with actual measurements:

```json
[{"origin":"Mumbai","provider":"AWS","region":"ap-south-1",
  "metric":"p95","method":"https","window":"5m",
  "measured_at":"2026-11-04T12:00:00Z","value_ms":12.5}]
```

Origin, metric, method and window must match the confirmed request. Measurements
must be finite, nonnegative, not in the future, and no older than 15 minutes.

## Runner and secrets

Existing `ENABLE_TERRAFORM_WORKFLOW`, `TF_STATE_BUCKET`, region and cache settings
apply. Credentials come from existing Secrets Manager connections. Windows Azure
planning requires `IIPP_AZURE_WINDOWS_ADMIN_PASSWORD` in the protected runtime;
passwords are not accepted in exported or saved tfvars. Binary plans/state can
contain secrets and must remain access-controlled.

VM planning verifies provider-published image identity and OS/architecture.
Review uses existing approval-token settings and a POC reviewer name, not an
enterprise-authenticated identity. Records are under `DATA_DIR/poc_plans`; events
are under `DATA_DIR/poc_audit.jsonl`. No existing deployment is migrated.

No accounts, IAM resources, deployment or cloud apply are performed by this update.
Public prices do not assume Free Tier, credits or discounts. GPU, BYOL, custom or
third-party images, Windows Kubernetes, private cloud, GitOps, autonomous scaling
and enterprise authentication are excluded.
