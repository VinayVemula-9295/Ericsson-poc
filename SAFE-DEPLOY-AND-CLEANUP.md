# Safe deploy, rollback and disk cleanup

## Safety model

- `backup.sh` creates a private timestamped backup under `~/cloudpilot-backups/`.
- The backup contains the project source/config/assets (excluding only rebuildable caches), `/opt/cloudpilot/data` when present, Docker container inspection metadata, a tag for the current image, and a committed snapshot of the current container filesystem.
- `deploy.sh` builds the new image while the old app is still running, then renames and retains the old container. The new container must pass the Streamlit health endpoint before deployment is reported successful. If it fails, the old container is restored automatically.
- `rollback.sh [YYYYMMDD-HHMMSS]` restores a retained previous container. With no argument it selects the newest retained previous container. The currently deployed container is preserved under a `cloudpilot-ai-rejected-*` name rather than deleted.
- `cleanup.sh [project-dir] --dry-run` previews conservative cleanup. Add `--apply` only after reviewing the list.

## IMPORTANT: back up the current source BEFORE overwriting it

Put the v13 ZIP in `$HOME` first. Replace the example ZIP filename below with the actual downloaded filename.

```bash
cd ~
ZIP="$HOME/cloudpilot-multicloud-poc-v13-safe-deploy.zip"
unzip -p "$ZIP" cloudpilot-ericsson-poc/backup.sh > "$HOME/cloudpilot-backup-v13.sh"
chmod 700 "$HOME/cloudpilot-backup-v13.sh"
STAMP="$("$HOME/cloudpilot-backup-v13.sh" "$HOME/cloudpilot-ericsson-poc")"
echo "Backup timestamp: $STAMP"
```

Confirm the backup was created before extracting the new project:

```bash
ls -lh "$HOME/cloudpilot-backups/$STAMP"
sudo docker image inspect "cloudpilot-ai:rollback-$STAMP" >/dev/null && echo "Container snapshot verified"
```

Now extract the release over the existing project. This is an overlay, not a delete-and-recreate operation, so the EC2-only `assets/images.png` file is retained because it is not included in the ZIP:

```bash
unzip -o "$ZIP" -d "$HOME"
cd "$HOME/cloudpilot-ericsson-poc"
chmod +x deploy.sh backup.sh rollback.sh cleanup.sh
CLOUDPILOT_BACKUP_STAMP="$STAMP" ./deploy.sh
```

The `CLOUDPILOT_BACKUP_STAMP` variable tells `deploy.sh` to reuse the already-created backup of the OLD checkout. Without it, `deploy.sh` creates a fresh backup automatically before deployment.

## Roll back if the new version is unsuitable

```bash
cd ~/cloudpilot-ericsson-poc
./rollback.sh
```

Or select a specific retained deployment:

```bash
./rollback.sh YYYYMMDD-HHMMSS
```

The previous container is retained, not deleted. The backup of `/opt/cloudpilot/data` is kept separately under `~/cloudpilot-backups/<timestamp>/cloudpilot-data.tar.gz`; do not restore it automatically unless a data-format rollback is actually required, because doing so overwrites current persistent records.

## Conservative disk cleanup

First inspect current disk usage and Docker references:

```bash
df -h
du -sh ~/cloudpilot-ericsson-poc /opt/cloudpilot/data 2>/dev/null || true
sudo docker system df
sudo docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
sudo docker images
```

Preview cleanup:

```bash
./cleanup.sh "$HOME/cloudpilot-ericsson-poc" --dry-run
```

Apply only after reviewing the list:

```bash
./cleanup.sh "$HOME/cloudpilot-ericsson-poc" --apply
```

The cleanup removes only Python/test caches, unreferenced older `cloudpilot-ai:backup-*` tags beyond the retention count, dangling images and Docker builder cache. It protects:

- Running or stopped Docker containers, including retained previous containers.
- The current CloudPilot image and any image referenced by any container.
- All `cloudpilot-ai:rollback-*` images and the newest two `cloudpilot-ai:backup-*` tags.
- `~/cloudpilot-backups`, `~/cloudpilot-patch-backups`, project ZIP archives, source `.bak` files, `.env` files, approval tokens, and `/opt/cloudpilot/data`.
- Terraform state, saved plans, deployment history and audit records.

It does not delete unknown stopped containers or user files. Review `docker ps -a` separately before deciding whether an old stopped container can be removed. Shared image layers are reclaimed only when no remaining image/container references them.

## Disk-space note

The root filesystem was reported at 95% usage with about 1.2 GB available. Check `/opt/cloudpilot/data` size before making a backup. If the backup fails, stop; do not continue to deployment. The backup script uses a compressed project/data archive and a Docker snapshot, so required free space depends on the current persistent data and container writable layer.

The dashboard image `assets/images.png` is not bundled in the release archive. Preserve the existing file at `~/cloudpilot-ericsson-poc/assets/images.png` when extracting the release; Docker will include it in the next image build when it exists.
