# Customer POC implementation

Architecture reviewed: Streamlit app.py owns navigation; provider_connections.py
owns Secrets Manager credentials and identity checks. dashboard_adapters.py and
multicloud_dashboard.py provide read-only discovery, billing and scoped caching.
The two existing Terraform stores remain authoritative for history and reuse.
control_plane.py provides optional customer authorization and reviewed execution.
Existing uncommitted implementation is preserved.

Implementation sequence:
1. Replace the demo gate with admin authentication, bounded sessions and secure setup.
2. Implement ordered full-width provider summaries, hierarchy panels and cost overview.
3. Extend AWS inventory for ECS and load balancers and normalize resource metadata.
4. Add explicitly scoped organization billing and deduplicate account totals.
5. Add a state-driven deployment stepper with safe execution events.
6. Enforce verified successful sources at reuse listing and execution boundaries.
7. Document read permissions and setup; run regression and browser validation.

All existing approval, checksum, locking and saved-plan safeguards are retained.
No deployment, IAM attachment or cloud resource modification is included. Live
reads require configured credentials; absent permissions remain visible.
