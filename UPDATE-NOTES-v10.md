# CloudPilot v10 update notes

- Fix GCP Compute Engine service discovery: request a larger catalog page, follow pagination, normalize the display name and use the published service identifier as a fallback.
- Report the number of catalog entries scanned if Compute Engine is not found.
- Clarify API-key rotation and shell instructions; the `export` and `./deploy.sh` commands must be separate lines, without a trailing backslash.
- Keep GCP prices unavailable when the public catalog API does not successfully return verified regional CPU/RAM rates.
- Terraform new-instance workflow remains disabled unless explicitly enabled after reviewing dashboard exposure and IAM permissions.

No GCP account connection or provisioning has been added. The API key enables public catalog pricing only.
