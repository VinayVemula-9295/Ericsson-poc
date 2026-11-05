#!/usr/bin/env bash
# Create a protected source/data/container backup before deploying a new IIPP version.
set -euo pipefail
umask 077

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
CONTAINER="${CLOUDPILOT_CONTAINER:-cloudpilot-ai}"
DATA_DIR="${CLOUDPILOT_DATA_DIR:-/opt/cloudpilot/data}"
BACKUP_ROOT="${CLOUDPILOT_BACKUP_ROOT:-$HOME/cloudpilot-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$STAMP"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_ROOT" "$BACKUP_DIR"

echo "[backup] Saving project source: $PROJECT_DIR" >&2
# Keep configuration, scripts, tests, Terraform code and assets. Exclude only rebuildable caches.
tar -czf "$BACKUP_DIR/project.tar.gz" \
  --exclude="$(basename "$PROJECT_DIR")/.git" \
  --exclude="$(basename "$PROJECT_DIR")/.venv" \
  --exclude="$(basename "$PROJECT_DIR")/venv" \
  --exclude="$(basename "$PROJECT_DIR")/__pycache__" \
  --exclude="$(basename "$PROJECT_DIR")/.pytest_cache" \
  --exclude="$(basename "$PROJECT_DIR")/.mypy_cache" \
  --exclude="$(basename "$PROJECT_DIR")/.ruff_cache" \
  -C "$(dirname "$PROJECT_DIR")" "$(basename "$PROJECT_DIR")"
chmod 600 "$BACKUP_DIR/project.tar.gz"

if [ -d "$DATA_DIR" ]; then
  echo "[backup] Saving persistent data: $DATA_DIR" >&2
  sudo tar -czf "$BACKUP_DIR/cloudpilot-data.tar.gz" -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
  sudo chown "$(id -u):$(id -g)" "$BACKUP_DIR/cloudpilot-data.tar.gz"
  chmod 600 "$BACKUP_DIR/cloudpilot-data.tar.gz"
else
  echo "[backup] Persistent data directory not found; recording that fact." >&2
  printf 'NOT_PRESENT: %s\n' "$DATA_DIR" > "$BACKUP_DIR/data-backup-status.txt"
  chmod 600 "$BACKUP_DIR/data-backup-status.txt"
fi

if sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "[backup] Saving current container metadata and filesystem snapshot." >&2
  sudo docker inspect "$CONTAINER" > "$BACKUP_DIR/container-inspect.json"
  chmod 600 "$BACKUP_DIR/container-inspect.json"
  IMAGE_ID="$(sudo docker inspect -f '{{.Image}}' "$CONTAINER")"
  sudo docker tag "$IMAGE_ID" "cloudpilot-ai:rollback-base-$STAMP"
  sudo docker commit --pause=true "$CONTAINER" "cloudpilot-ai:rollback-$STAMP" >/dev/null
  printf 'container=%s\nimage_id=%s\nbase_tag=cloudpilot-ai:rollback-base-%s\nfilesystem_snapshot=cloudpilot-ai:rollback-%s\n' \
    "$CONTAINER" "$IMAGE_ID" "$STAMP" "$STAMP" > "$BACKUP_DIR/docker-rollback.txt"
else
  echo "[backup] WARNING: container '$CONTAINER' not found; only source/data were backed up." >&2
  printf 'container_not_found=%s\n' "$CONTAINER" > "$BACKUP_DIR/docker-rollback.txt"
fi
chmod 600 "$BACKUP_DIR/docker-rollback.txt"

printf 'created_at=%s\nproject_dir=%s\ndata_dir=%s\n' "$STAMP" "$PROJECT_DIR" "$DATA_DIR" > "$BACKUP_DIR/metadata.txt"
chmod 600 "$BACKUP_DIR/metadata.txt"

echo "[backup] Backup completed: $BACKUP_DIR" >&2
# Timestamp is stdout so deploy.sh can capture it without parsing log messages.
printf '%s\n' "$STAMP"
