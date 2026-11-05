# CloudPilot v13 — safe deployment, rollback and conservative cleanup

- Added `backup.sh` to archive project source/config/assets and `/opt/cloudpilot/data`, protect container inspection metadata, tag the current image, and commit a snapshot of the current container filesystem.
- Reworked `deploy.sh` to create/reuse an explicit backup before build, build before downtime, retain the previous container under a timestamped name, verify `/_stcore/health`, and automatically restore the previous container if startup/health validation fails.
- Added `rollback.sh` to restore the most recent or explicitly selected previous container while preserving the replaced current container for inspection.
- Added `cleanup.sh` with dry-run default. It protects active/stopped container images, rollback images, newest backup tags, persistent data, project archives, `.bak` source files and backup folders. It cleans only Python/test caches, unreferenced older backup tags beyond retention, dangling images and Docker builder cache.
- Added `SAFE-DEPLOY-AND-CLEANUP.md` with EC2 instructions and disk-space cautions.
- Updated `.dockerignore` to keep editor backups, old patch scripts, caches, and ZIP archives out of future Docker build contexts without deleting them from the host.

No deployment or cleanup was executed against the user's EC2 host. Run cleanup in dry-run mode first and verify the backup before accepting a new deployment.

## Safe upgrade order

- Extract `backup.sh` from the release ZIP to the home directory and run it against the existing checkout BEFORE overlaying the new source.
- Pass the returned timestamp as `CLOUDPILOT_BACKUP_STAMP` to `deploy.sh` so the deployment uses the pre-upgrade source/data/container snapshot.
