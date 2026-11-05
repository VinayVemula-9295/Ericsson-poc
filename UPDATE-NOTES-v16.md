# InfraMind v16 — UI polish and Agent response fixes

## Product identity
- Visible product name: **InfraMind**.
- Descriptor: **Multi-Cloud Intelligent Provisioning**.
- Tagline: **Think Intelligent. Provision Anywhere.**
- Retain the Innominds company logo in the left navigation; remove the old “Policy first · AI second” sidebar copy.

## Work / Agent separation
- Live connected-account inventory and AWS CloudWatch telemetry render in Work only.
- Agent continues to use the selected cloud/account connection to fetch fresh live evidence for responses; it no longer duplicates the inventory panel.

## Configure modal fix
- Configure dialog uses a one-shot open flag consumed at page render. This prevents unrelated button clicks and page reruns from reopening the modal.
- Modal includes a Close control.
- Successful save opens a separate one-shot success dialog.

## Agent output fixes
- Prompt requires one bold recommendation line and no more than three concise supporting bullets (target under 70 words).
- Sanitizer removes `<thinking>`, `<analysis>`, `<reasoning>`, `<scratchpad>` and `<chain_of_thought>` blocks and safely unwraps `<final>` / `<answer>` tags before Markdown rendering.
- The recommendation card highlights the recommended AWS hardware fit; nearest non-eligible fit is explicitly marked as informational only.
- Existing exact-match, closest-configuration, full discovered AWS options and detailed comparison/exclusion sections remain.

## Preserved behavior
- Terraform remains the only provisioning/IaC tool.
- Existing S3 state backend requirements, native locking, human approval and audit remain unchanged.
- Destroy workflow remains Work → Agent prepare destroy plan → Work review and approve.
- Existing state, runtime data, Docker image/container identifiers, IAM names and Secrets Manager namespace are retained for in-place EC2 deployment.

## Validation
- Pytest: 68 passed.
- Python syntax compilation: passed.
- Shell syntax checks and archive integrity: passed.
- No live EC2 deployment or UI browser test was executed by the local build environment; use `docs/deploy-v16.md` for the EC2 rollout and acceptance checks.
