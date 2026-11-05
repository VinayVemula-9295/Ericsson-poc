# Provider Configure panel

The global **Configure** button is available from both Work and Agent. It opens a provider form and stores credentials in AWS Secrets Manager; `/data/provider_connections.json` contains only connection metadata and secret ARNs.

## AWS

Fields: connection name, authentication mode, inventory role ARN, Terraform execution role ARN, External ID, default region and output format. For the generated IIPP POC/TEST roles, the External ID is required; copy the matching value from `~/.iipp-target-account-connections.json`. The recommended mode is the EC2 controller instance profile plus STS AssumeRole into AWS-POC/AWS-TEST. Access key ID and secret key fields are available for advanced inventory scenarios, but long-lived keys are discouraged.

For the default two-account setup, use:

- `AWS-POC`: `IIPPInventoryReadRole`, `IIPPTerraformExecutionRole`, POC external ID, region `ap-south-1`.
- `AWS-TEST`: corresponding roles in the TEST account, TEST external ID, region `ap-south-1`.

Terraform state access always uses the controller EC2 role and central S3 bucket. The Terraform AWS provider assumes the configured target-account execution role. Do not configure the target account access key as the S3 backend credential.

## Azure

Fields: Tenant ID, Client ID, Client Secret, Subscription ID and default region. The test action authenticates with the Azure service principal and queries Azure Resource Manager. Use a secret reference / managed identity or federation approach in production; rotate service-principal secrets before expiry.

## Google Cloud

Fields: Service Account Email, Project ID, complete service-account JSON key file, default region and default zone. A standalone PEM private key is not enough to build a service-account credential; the complete JSON file is required. Workload Identity Federation is preferred to long-lived service-account key files in production.

## Test and save behavior

- **Test connection & save:** authenticates and calls the provider inventory API, then saves only if Secrets Manager can store the credentials and the test passes.
- **Save without testing:** saves a not-tested connection; it must pass a live test before it can be used in the live inventory or AWS Terraform selector.
- **Refresh live inventory:** from Work, explicitly queries the selected saved connection and displays returned resource samples.

Secrets never belong in Streamlit session state as a durable store, source code, logs, deployment JSON, Terraform variables files, or Git. Secret retrieval errors should be surfaced without printing raw credential values.

## Current functional scope

AWS, Azure and GCP connection validation/live inventory are implemented. The AWS POC stack is the only Terraform apply target in this release. Azure/GCP Terraform stacks, provider-specific approval policies, quotas and end-to-end apply/destroy workflows must be implemented and tested before those providers are enabled for provisioning.
