# CloudPilot v11 update notes — exact/nearest VM matching

- Cloud comparison now requests multiple bounded candidate profiles per provider instead of selecting a single profile that already meets both minima. The candidate set includes exact, nearest over-provisioned, and nearest under-sized alternatives.
- Added AWS `c6i.xlarge` (4 vCPU / 8 GiB) and Azure `Standard_F4s_v2` (4 vCPU / 8 GiB) as exact-profile candidates. AWS is validated through `DescribeInstanceTypeOfferings` and `DescribeInstanceTypes` in the selected region; Azure's public Retail Prices API does not connect or validate a customer's subscription.
- For a 4 vCPU / 8 GiB request, CloudPilot can show exact candidates, 4 / 16 GiB choices that meet both minima, and 2 / 8 GiB choices that are nearby but do not meet the CPU requirement. The UI labels these differences explicitly.
- Undersized alternatives are informational only and cannot be selected for AWS Terraform planning against the original request. AWS plan creation continues to verify the selected type in the subnet's Availability Zone and compare actual EC2 specifications to the requested minimum.
- AWS regional offering is not proof of account quota or spare capacity. Azure/GCP remain public pricing comparisons only; no claim is made about those customer accounts until they are connected.
- Reduced default provider-price/spec cache TTL from 15 minutes to 60 seconds. The Agent's **Refresh provider pricing** action still clears caches immediately.
- Added the `c6i.xlarge` type to the AWS Terraform allowlist and Agent selection list. This update does not run Terraform plan/apply/destroy or create/modify cloud resources.

## Verification

- Python compilation: passed.
- Unit tests: 51 passed.
- Live AWS region offering, account quota, AZ capacity, Azure retail price, and GCP billing catalog requests were not run in this local build environment.

## Evidence limitations

- AWS EC2 `c6i.xlarge` hardware specification is 4 vCPU / 8 GiB in the AWS instance type documentation. The selected-region EC2 API response is still required to establish whether AWS reports the type as offered there.
- Azure `Standard_F4s_v2` is documented as 4 vCPU / 8 GiB. A Retail Prices API response is not a subscription quota or zone-capacity check.
- GCP comparison remains limited to configured N2 public pricing profiles; its public billing catalogue does not mean a GCP account is connected.
