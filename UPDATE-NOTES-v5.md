# CloudPilot POC v5 update notes

## Branding
- Packaged the exact Innominds logo supplied for this update.
- The main page now displays the logo immediately above the `CloudPilot` heading.
- Kept the existing left sidebar and Work/Agent navigation intact.

## Scale-down diagnostics and behavior
- Added visible scale-up (`n/2`) and scale-down (`n/3`) distinct CPU datapoint counters to Work and Agent.
- The UI explains when there is no fresh CloudWatch datapoint, when more low-CPU samples are needed, and when the current type is already the smallest allowlisted size.
- A changed EC2 instance type resets the old trend history so pre-resize samples do not immediately trigger a reverse resize. It waits for newer CPU datapoints for the current type.
- Approval candidate options are direction-aware: a scale-down event offers only smaller same-family types in the allowlist and live EC2 offerings; scale-up offers only larger types.
- If a scale-down event exists but the LLM does not select a preferred target, the deterministic threshold candidate is shown for human review, still subject to the allowlist/live offering checks and explicit approval.
- The system does not apply infrastructure changes automatically. EC2 stop/modify/start causes downtime and requires the reviewer token/acknowledgement.

## Important behavior to understand
- `t3.micro` is the smallest `t3` type allowed by this POC, so it cannot be scaled down further. To demonstrate scale-down, first approve a scale-up to a larger type such as `t3.small`, then allow CPU to remain at or below 25% for three distinct datapoints measured after the type change.
- A dashboard refresh every 30 seconds is not a new CloudWatch datapoint. Standard EC2 monitoring can take several minutes between datapoints.
- EC2 instance type offerings in an Availability Zone do not guarantee spare capacity.

## Deployment
- `deploy.sh` now honors `TARGET_INSTANCE_ID` and keeps backwards compatibility with `APP_INSTANCE_ID`.
- Build and run the included tests before deployment.
