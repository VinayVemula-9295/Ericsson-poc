#!/usr/bin/env bash
# Conservative cleanup. Default mode is dry-run; pass --apply to remove listed cache/old artifacts.
set -euo pipefail

PROJECT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"
MODE="${2:---dry-run}"
KEEP_BACKUP_TAGS="${KEEP_BACKUP_TAGS:-2}"

if [ "$MODE" != "--dry-run" ] && [ "$MODE" != "--apply" ]; then
  echo "Usage: $0 [project-dir] [--dry-run|--apply]" >&2
  exit 2
fi

mapfile -t CACHE_DIRS < <(find "$PROJECT_DIR" -type d \( -name '__pycache__' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache' \) -prune -print 2>/dev/null)
mapfile -t PYC_FILES < <(find "$PROJECT_DIR" -type f -name '*.pyc' -print 2>/dev/null)

mapfile -t BACKUP_TAGS < <(sudo docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E '^cloudpilot-ai:backup-[0-9]{8}-[0-9]{6}$' | sort -r || true)

# An image ID referenced by any container (including stopped containers) is protected.
declare -A USED_IMAGE_IDS=()
while IFS= read -r cid; do
  [ -n "$cid" ] || continue
  image_id="$(sudo docker inspect -f '{{.Image}}' "$cid" 2>/dev/null || true)"
  [ -n "$image_id" ] && USED_IMAGE_IDS["$image_id"]=1
done < <(sudo docker ps -aq)

REMOVE_TAGS=()
index=0
for tag in "${BACKUP_TAGS[@]}"; do
  index=$((index + 1))
  image_id="$(sudo docker image inspect -f '{{.Id}}' "$tag" 2>/dev/null || true)"
  [ -n "$image_id" ] || continue
  if [ -n "${USED_IMAGE_IDS[$image_id]:-}" ]; then
    continue
  fi
  if [ "$index" -le "$KEEP_BACKUP_TAGS" ]; then
    continue
  fi
  REMOVE_TAGS+=("$tag")
done

echo "Project cache directories to remove: ${#CACHE_DIRS[@]}"
printf '  %s\n' "${CACHE_DIRS[@]:-}"
echo "Python bytecode files to remove: ${#PYC_FILES[@]}"
printf '  %s\n' "${PYC_FILES[@]:-}"
echo "Unused old cloudpilot-ai:backup-* tags eligible for removal: ${#REMOVE_TAGS[@]}"
printf '  %s\n' "${REMOVE_TAGS[@]:-}"
echo "Docker builder cache currently reported:"
sudo docker builder du 2>/dev/null || true

echo "Protected: running/stopped containers, current image, rollback-* images, the newest $KEEP_BACKUP_TAGS backup tags, project archives, source .bak files, ~/cloudpilot-backups, ~/cloudpilot-patch-backups and /opt/cloudpilot/data."

if [ "$MODE" = "--dry-run" ]; then
  echo "Dry run only. No files or images were deleted. Run '$0 "$PROJECT_DIR" --apply' to apply this cleanup."
  exit 0
fi

for path in "${CACHE_DIRS[@]}"; do
  [ -n "$path" ] && rm -rf -- "$path"
done
for path in "${PYC_FILES[@]}"; do
  [ -n "$path" ] && rm -f -- "$path"
done
for tag in "${REMOVE_TAGS[@]}"; do
  echo "Removing unused image tag: $tag"
  sudo docker image rm "$tag"
done

# Rebuildable builder cache and dangling images are safe to prune; containers and tagged images are not touched.
sudo docker builder prune -af
sudo docker image prune -f

echo "Cleanup completed. Recheck disk usage with: df -h && sudo docker system df"
