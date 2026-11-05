# Live Agent cluster plan check — 2026-11-05

**Result: does not work as requested on the currently deployed application.**

Tested the live Agent at `http://13.203.223.14:8501` with:

> I need kubernetes custer of 2 worker nodes with  2Vcpu and 1gib ram

Clicked **Use configuration** on the AWS **Selectable** card, preserved the active
Demo-Account selection, entered cluster name `iipp-cluster-ui-check`, and clicked
**Generate Terraform plan**. No resource approval/apply action was used.

## Observed behavior

| Check | Actual result |
| --- | --- |
| Selectable worker type | t3.small, 2 vCPU / 2 GiB per worker, ap-south-1 |
| Worker count | 4 workers, instead of the requested 2 |
| Worker compute estimate | USD 65.41/month for the displayed 4 workers |
| AWS Recommended and Optimal | Both unavailable in the deployed UI |
| Live subnets and Kubernetes version | Populated |
| Compatible cluster role, worker role and KMS key | None available; required fields empty |
| Generate Terraform plan | “Complete all Terraform inputs before generating the plan.” |
| Saved plan / plan dialog | Neither generated |
| Application exception | None |
| Apply / resource creation | Not performed |

The permissions repair is working: the old access-denied warning is absent.
Planning is now correctly prevented by missing mandatory prerequisites. Separately,
the live site's old pod-based sizing ignores the explicit worker count and excludes
the 1 GiB worker profile because of default pod requirements. The corrected local
worker-count and tier behavior has not been deployed.

## Required to complete this acceptance test

- Deploy the implemented worker-count and comparison changes through the normal
  release workflow when deployment is authorized.
- Separately provision compatible Demo EKS cluster and worker roles and an enabled
  customer-managed symmetric KMS key, including execution/key permissions and
  suitable networking. The earlier chosen discovery-only workflow does not create
  these automatically.
- Repeat this exact browser flow and verify the saved plan has one EKS cluster,
  a managed node group and exactly two desired/minimum/maximum workers. A successful
  Terraform plan has not yet been established for the cluster.

Evidence: `.cache/live-demo-cluster-ui-result.json`,
`.cache/live-demo-cluster-plan-result.png`, and
`.cache/live-demo-cluster-inputs.png`. The browser checker is
`.cache/check-live-demo-cluster-ui.py`; it uses live data, without mocked provider
calls or fabricated resource identifiers.
