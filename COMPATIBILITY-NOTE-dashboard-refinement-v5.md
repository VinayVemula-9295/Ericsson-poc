# Compatibility note for `cloudpilot_dashboard_refinement_v5.py`

The separately uploaded `cloudpilot_dashboard_refinement_v5.py` targets a different live `app.py` layout. It requires legacy helper markers such as `_cloudpilot_server_tiers` and `_cloudpilot_show_apply_dialog`, which are not present in the v10 source tree bundled here. Its preflight guard will therefore reject this v10 source; do not try to layer that script onto this v11 project.

This v11 update is applied directly to the v10 source tree. Deploy it using the included `deploy.sh` from the updated project directory after reviewing the deployment environment and restricting access to the Streamlit port. The script rebuilds the Docker image and keeps the existing `/opt/cloudpilot/data` bind mount, so saved records remain outside the image. It does not run Terraform plan/apply/destroy. Keep the prior project directory and image backup available for rollback.
