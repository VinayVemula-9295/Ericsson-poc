# CloudPilot provider setup and cost interpretation

## What Work displays

- **AWS** is marked connected only when the CloudPilot IAM role can retrieve EC2 inventory. Work lists currently running EC2 instances and a separate estimated 730-hour monthly compute run-rate per instance, when the AWS Price List API returns a price.
- AWS Cost Explorer's service-cost breakdown is **actual month-to-date account spend**, not a projected cost for each currently running resource. It appears in the existing Cost visibility section and should not be added to the EC2 estimates.
- Azure and GCP are shown as **not connected** until their account adapters/credentials are added. The public pricing lookup is not account connectivity and does not discover real resources in those accounts.
- The current connected-account resource inventory is AWS EC2. Do not interpret it as a full inventory of RDS, S3, Lambda, or other AWS services. Cost Explorer can still show spend by AWS service where billing data exists.


## Exact and nearest CPU/RAM alternatives

CloudPilot checks a bounded shortlist rather than claiming the configured profile catalogue is the complete provider catalogue. For a 4 vCPU / 8 GiB request, AWS `c6i.xlarge` and Azure `Standard_F4s_v2` are included as exact hardware candidates; AWS/Azure 4 / 16 GiB profiles and the configured GCP N2 profiles are shown as nearest alternatives. The AWS EC2 APIs verify regional offering and live type specifications. Azure and GCP adapters only retrieve public pricing and use reviewed local CPU/RAM mappings; they do not check a user's subscription/project, quota, zone restrictions or launch capacity.

The Agent labels each profile as an exact match, meets/exceeds the request, or below at least one requested minimum. Under-sized alternatives are not eligible for AWS provisioning unless the user changes the original CPU/RAM request and re-runs the comparison. A regional AWS offering is not an account quota check or a reservation of spare capacity; the actual Terraform plan validates the type against the subnet's Availability Zone and actual instance specifications.

## Fix AWS `DescribeInstanceTypeOfferings` AccessDenied

The screenshot's error means the currently attached role is missing this read permission. Merely having a policy JSON in the repository does not grant permissions; an AWS administrator must attach the policy to the role used by the CloudPilot EC2 instance (currently shown as `CloudPilotReadTargetMetricsRole`).

The minimal policy is in `iam-instance-type-policy.json`:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "ReadEC2InstanceTypeOfferingsAndSpecifications",
    "Effect": "Allow",
    "Action": [
      "ec2:DescribeInstanceTypeOfferings",
      "ec2:DescribeInstanceTypes"
    ],
    "Resource": "*"
  }]
}
```

In AWS Console:

1. Open **IAM → Roles → CloudPilotReadTargetMetricsRole**.
2. Choose **Add permissions → Create inline policy** (or attach an approved customer-managed policy).
3. Add the actions above with resource `*`, review and save.
4. Return to CloudPilot and click **Refresh provider pricing** to clear cached results and retry the calls.

This policy only enables type/spec discovery. It does not grant provisioning access, and region/AZ offering does not guarantee spare capacity. Terraform deployment needs its separately reviewed policy.

## Fix GCP public pricing `GOOGLE_CLOUD_BILLING_API_KEY is not configured`

The Google Cloud Billing Catalog API requires an API key. A key is for public catalog pricing only; it does not connect a GCP project or enable provisioning.

1. In a Google Cloud project, enable **Cloud Billing Catalog API**.
2. Create an API key and restrict it to Cloud Billing Catalog API.
3. On the CloudPilot EC2 host, from the project directory, set the key in the deployment shell and rebuild/restart:

```bash
# Enter the newly rotated key without echoing it or saving it to shell history.
read -rsp 'New Google Cloud Billing API key: ' GOOGLE_CLOUD_BILLING_API_KEY
echo
export GOOGLE_CLOUD_BILLING_API_KEY
./deploy.sh
unset GOOGLE_CLOUD_BILLING_API_KEY
```

Keep the `export` and `./deploy.sh` commands on separate lines. Do not add a trailing backslash after the export command. The deploy script reads the environment variable, stores it in a user-only file, and mounts that file read-only in Docker. This key is only for public pricing lookup, not GCP account connection or provisioning.

`deploy.sh` writes it to a user-only local secret file and mounts the file read-only in the container. Do not hardcode it in Python, Dockerfile, a committed `.env`, or a public image.

Official guide: https://cloud.google.com/billing/v1/how-tos/catalog-api

If the key remains missing or restricted incorrectly, GCP price remains N/A. The UI may show curated N2 CPU/RAM specifications for context, but those do not count as verified prices and cannot win a price comparison.

## Cost semantics

- **Estimated EC2 compute / month**: public Linux/shared-tenancy on-demand hourly price × 730 hours for that running EC2 instance; excludes EBS, transfer, tax, discounts and non-compute services.
- **AWS service costs (actual MTD)**: Cost Explorer account-level posted spend grouped by AWS service for the selected MTD period.
- **Azure/GCP live service estimate**: unavailable until account inventory is connected. Public prices alone do not say which services a customer's account is running.
