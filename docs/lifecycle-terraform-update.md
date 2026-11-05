# Terraform planning and deployment lifecycle

Work no longer renders provisioned-resource history. Deployments retains both
record stores and offers Destroy below the details for successful AWS resources,
failed destroy attempts and pending destroy reviews. Destroy prepares a saved plan;
Submit opens reviewer/token validation. Only the reviewed binary plan can execute.
Both services enforce the execution flag and token on the server. Destroy reviews
expire after one hour. History, resource identifiers and state remain retained.

Kubernetes comparison cards identify EKS, AKS and GKE, worker count and per-node
hardware. Prices remain worker compute estimates excluding other cluster charges.
Azure/GCP execution validation is outside this release.

## Live prerequisite check, 2026-11-05

The runner is healthy but both execution flags remain false and its state bucket
setting is empty. The local `dev` administrator profile returns ExpiredToken.
The controller role cannot assume OrganizationAccountAccessRole in either Demo
(333347372766) or Test (695729002971). No target IAM mutation or live infrastructure
apply/destroy was attempted. Refresh an authorized administrator session before
following two-account-terraform-setup.md. The target setup script now backs up
existing role trust, inline policies and attached-policy references before writes.

After verifying target IAM, KMS and networking, configure the existing bucket,
enable planning, and generate EC2/EKS plans in both accounts. Keep approval
execution disabled until those checks pass. A successful plan does not prove
creation permissions, capacity, node connectivity or successful destruction.
