# CloudPilot POC v6 update notes

## Logo placement
- Moved the supplied Innominds logo into the left sidebar, directly above the sidebar CloudPilot title, matching the screenshot where the logo was not visible.
- Kept the existing Work/Agent navigation and connected environment details. Removed the duplicate main-page logo banner.

## Prevent immediate reverse resize / stale plan display
- On successful reviewer-approved resize, preserve a concise audit record in `/data/approval_audit.jsonl`, clear the active CPU event and active infrastructure plan, and return the Agent page to idle.
- Removed the expanded execution result JSON/receipt from the dashboard UI.
- Start a 15-minute post-resize cooldown and reset high/low streak counters.
- Only CPU datapoints timestamped after the cooldown can count toward another scale-up or scale-down event. The cooldown boundary is retained if the current EC2 instance type is refreshed later by AWS API reads.
- Added migration behavior: if an earlier POC version left an `APPLIED` plan persisted, the app clears that stale plan/event and starts a cooldown before evaluating more thresholds.

## Thresholds
- Scale-up remains CPU >= 70% for 2 distinct fresh datapoints.
- Scale-down remains CPU <= 25% for 3 distinct fresh datapoints, and a smaller allowlisted/available type must exist.
- Cooldown is not a substitute for monitoring. After cooldown expires, sustained low CPU measured by three new datapoints may legitimately generate a scale-down recommendation. Changes remain reviewer-approved.

## Validation
- Added a regression test proving old low-CPU datapoints cannot cause an immediate scale-down during post-resize cooldown.
- Added source checks that the applied plan is cleared and the execution receipt JSON is not rendered.
