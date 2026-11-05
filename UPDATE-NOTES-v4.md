# CloudPilot POC update notes

## Included changes
- Added the supplied Innominds logo as `assets/innominds-logo.jpg` and replaced the redundant main-page `CloudPilot` title with the logo. The left sidebar navigation and Work/Agent sections remain unchanged.
- Added read-only EC2 candidate discovery: the agent can query offered instance types in the target AZ, fetch vCPU/memory/network specifications, and compare Linux On-Demand compute estimates.
- Added `t3.large` to the bounded same-family allowlist and scale-up ladder. The approval UI only offers configured types that EC2 lists as offered in the target location.
- The LLM plan is asked to emit a preferred instance type line; if that type is parsed, it becomes the approval dropdown default only if it is still allowlisted and offered. If the model explicitly says no resize is justified, the approval panel is hidden for that event.
- Added `iam-instance-type-policy.json` and `scripts/create_cpu_alarms.sh` for read-only type discovery and independent CloudWatch/SNS alert delivery.

## Important caveats
- EC2 `DescribeInstanceTypeOfferings` shows which types are offered in the region/AZ; it does not guarantee unused On-Demand capacity.
- A `t3.micro`, `t3.small`, and `t3.large` each have 2 vCPUs. `t3.small` doubles memory relative to micro and has a higher CPU-credit baseline, but cannot be assumed to solve sustained CPU-bound traffic without measurement.
- The LLM does not determine when a CPU threshold is crossed; deterministic code emits the event, then Bedrock uses AWS tools to assess the event and prepare the plan.
- Notification setup requires an existing SNS topic with confirmed email/SMS subscriptions and permission to create CloudWatch alarms. The included script does not create SNS subscribers.
- The package cannot change the live EC2 host on its own. Deploy from the CloudPilot host after reviewing IAM, the security group, disk space, and the backup/rollback plan.

## Deployment outline
1. Copy this ZIP to the CloudPilot EC2 host.
2. Extract to a new directory and enter `cloudpilot-ericsson-poc/`.
3. Attach `iam-instance-type-policy.json` to `CloudPilotReadTargetMetricsRole` using an administrator's AWS CLI identity.
4. Restrict inbound port 8501 to the reviewer IP.
5. Run `bash deploy.sh`; the script tags/commits a backup and builds before removing the existing container.
6. Check `sudo docker logs --tail 100 cloudpilot-ai`, then open Work and Agent and confirm the candidate table is populated.
7. Configure and verify SNS subscriptions and run the alert script separately if email/SMS notifications are required.
