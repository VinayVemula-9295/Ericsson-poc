# Current navigation and telemetry dashboard

Navigation is Agent, Telemetry, Deployment; Agent is the default. The shared header uses the Innominds logo, platform title, selected account, cloud/account switcher, and Configure dialog. Previously open Work/Deployments sessions migrate to the new names.

Telemetry starts with Connected accounts: three provider cards and a comparison table. AWS inventory and estimated monthly compute use existing controller APIs; errors remain unavailable. Azure/GCP cards are explicitly Connected ? Demo with fixed sample values, isolated from saved connections, provider queries, and provisioning. Existing workload telemetry remains below the overview.

Deployment shows Successfully deployed above History. Only current SUCCEEDED records appear in the first table; all statuses appear in History. Both use the existing records and Details dialog, including reviewed destroy actions. No records or cloud resources are created by this presentation change.

# Agent Workspace UI

The current prompt-led UI replaces the sidebar layout described below. See
[Agent prompt and Terraform workflow](agent-prompt-workflow.md) for current usage,
approval behavior and runtime requirements. The following describes the prior
option 04 layout retained as historical context.

The Agent page implements design option 04: a request and recommendation column, with decision evidence, cloud coverage, and recent audit activity alongside it. Smaller screens stack evidence below the request flow. Work keeps its inventory and resource lifecycle responsibilities.

The request form still uses **Find options**. Recommendations use existing live discovery, deterministic ranking, and sanitized AI explanations. Open **Compare configurations and choose a deployment target** to inspect all candidates and select an eligible AWS configuration. The deployment form then appears below the provisioning layer; preparing a plan never executes it. Existing saved-plan review, reviewer credentials, checksum verification, and destroy approvals are unchanged.

Cloud targets are AWS, Azure, and Google Cloud only. Azure/GCP provisioning remains unavailable. Kubernetes appears as a planned capability with execution disabled. Policy status is labelled as required before planning, never fabricated as passed. The evidence panel reads existing state without querying clouds and only displays cached inventory belonging to the selected connection. It describes that evidence as a last read and directs users to Work for current metrics. No preview fixtures or sample prices are used in the application.

## Backup and rollback

The UI backup before this change is `backups/agent-workspace-20261104-231159/current-ui.zip` relative to the release workspace. Its SHA-256 is stored in the adjacent `SHA256.txt`. It contains the prior application UI, provider UI, theme, assets, README, and UI tests; credentials, runtime state, and cloud resources are not modified.

To roll back, stop the local application, extract the backup to a staging directory, and restore its `app.py` and UI tests to the application root. The newly added `workspace_ui.py` and `assets/agent-workspace.css` become unused. Do not overwrite runtime data or Terraform state. This change does not deploy the application or enable any cloud writes.
