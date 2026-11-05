# Demo VM Terraform planning fix — 2026-11-05

## Cause and correction

The live container had ENABLE_TERRAFORM_WORKFLOW=false and an empty
TF_STATE_BUCKET. Its Demo execution role was already assumable, so an administrator
SSO session was not required to generate the requested Demo plan.

The AWS cards also selected t3a.micro although the configured execution-family
policy permits t3 only. Cards now filter by the same approved families and regions
before selecting hardware tiers; this request selects the eligible t3.micro.
Deployment preserves those policy settings and keeps defaults disabled in source.

Runtime now uses the existing central state bucket,
inframind-tfstate-704298683641-20261104, with planning enabled and approval execution
disabled. No IAM policies were broadened. Target credentials remain separate from
controller/backend credentials.

## Verification

- Full Linux suite: 199 passed in 23.40 seconds before deployment.
- Live server init, validate, plan and show succeeded.
- Browser request: `i need 2Vcpu and 1GIB ram server`, AWS Selectable, Demo-Account,
  VM name `demo-vm`; live AMI/subnet/security-group values loaded.
- Final browser-created record: `poc-afd48d7552ea4f0bba0b53cdba74fafa`, status
  AWAITING_APPROVAL, t3.micro, 2 vCPU and 1 GiB RAM.
- Real saved binary plan verified on the server; summary:
  `Plan: 1 to add, 0 to change, 0 to destroy.`
- No Terraform apply or infrastructure creation/destruction was performed.
- Final browser test passed: plan-review dialog rendered the actual summary,
  with no error alerts or application exceptions. Screenshot:
  `.cache/demo-vm-plan-fixed.png`. The browser check waits for Streamlit's lazy
  code rendering before checking the summary.

## Deployment and recovery

Deployed with the deployment skill at
`C:/Users/vvemula/.codex/skills/deployment/SKILL.md`.

- Endpoint: http://13.203.223.14:8501
- Source: `/home/ubuntu/intelligent-provisioning-platform-releases/20261105T070351Z/intelligent-provisioning-platform`
- Archive: `intelligent-provisioning-platform-20261105T070351Z.zip`, 476411 bytes
- SHA-256: `90a5bd31de064d97db99aab8450d406cf9cf754c4369238261e5ca5c957c84c4`
- Backup: `20261105-070513`; original container, state, secrets and mounts retained
- Health: ok; workflow flag true, approval-execution flag false; deployed source verified

Rollback on the server:

```bash
cd /home/ubuntu/intelligent-provisioning-platform-releases/20261105T070351Z/intelligent-provisioning-platform
./rollback.sh 20261105-070513
```

Reconcile active-release metadata after rollback. The restored container will
restore the previous planning-disabled settings.

## Remaining account prerequisites

The controller's assumption of the Test account's IIPPTerraformExecutionRole still
returns AccessDenied. This fix verifies Demo EC2 planning only; Test and EKS plans
remain unverified. Do not interpret successful planning as proof of apply
permissions, capacity, EKS node connectivity or successful destruction.
