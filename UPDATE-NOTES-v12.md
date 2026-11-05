# CloudPilot v12 — live AWS discovery and dashboard image

## Agent infrastructure discovery

- Removed the curated AWS instance-type shortlist and all AWS CPU/RAM fallback mappings from the Agent's new-infrastructure comparison path.
- AWS discovery now queries `DescribeInstanceTypeOfferings` for every instance type returned for the requested region, follows `NextToken` pages, and fetches live hardware specifications from `DescribeInstanceTypes` in batches of at most 100 type names.
- AWS hardware values (vCPU and RAM) are taken only from the EC2 API response. If an API call fails, the Agent shows an error and does not substitute hardcoded CPU/RAM values.
- Candidate selection ranks live region offerings by exact match, then candidates meeting both requested minimums, then undersized diagnostics. It shows the closest compatible x86_64, EBS-root, HVM, On-Demand virtual-machine candidates for the current Linux AMI workflow.
- Public AWS On-Demand prices are retrieved only for the closest candidate shortlist. They are public retail estimates, not account-negotiated prices or bills.
- An optional STS `GetCallerIdentity` call displays the AWS account ID used by the runtime SDK credentials. EC2 region offerings do not establish vCPU quota or guarantee spare On-Demand capacity.
- The Terraform plan workflow no longer blocks all but a static set of EC2 names. It validates the chosen type against live EC2 offerings in the actual subnet AZ, live type specifications, x86_64 AMI compatibility, EBS root, HVM virtualization and On-Demand support before generating a plan. Applying the saved plan remains reviewer-approved only.
- Azure and GCP are intentionally unchanged as POC public-price comparisons, using curated local reference mappings. They are not described as connected account inventory and cannot be provisioned.

## Dashboard image

- The main dashboard header now checks for `assets/images.png` and displays it when present. Missing image files do not prevent the app from starting.
- The `deploy.sh` script reports whether the image asset is present before the Docker build. The Dockerfile copies `assets/` into the image.
- `images.png` was not present in the supplied v11 ZIP and was not uploaded as a chat attachment. Keep/copy the already-existing EC2 file at `~/cloudpilot-ericsson-poc/assets/images.png` when updating the checkout so it is included in the next Docker image.

## Runtime requirements and limits

- The CloudPilot EC2 role needs `ec2:DescribeInstanceTypeOfferings` and `ec2:DescribeInstanceTypes`; `iam-instance-type-policy.json` contains those read-only actions.
- `sts:GetCallerIdentity` is optional for displaying an account ID; inability to read the caller identity does not block EC2 discovery.
- The Agent's new-instance comparison cache remains short-lived and can be force-refreshed using **Refresh provider pricing**.
- Discovery checks regional offerings. Terraform performs an additional Availability Zone check using the actual subnet. Neither check guarantees physical capacity at launch; the final launch can still be rejected by AWS.
- The separate Work resize workflow intentionally retains its own conservative family allowlist. This v12 change applies to the Agent's new-instance discovery and Terraform-plan workflow.

## Validation

- `python -m py_compile` passed for changed Python modules.
- `pytest -q`: 53 tests passed.
- Live AWS API calls, Docker build/restart, and actual EC2 provisioning were not run from the local packaging environment.
