#!/usr/bin/env bash
# Restore the stopped pre-deployment container retained by deploy.sh.
set -euo pipefail

CURRENT_CONTAINER="${CLOUDPILOT_CONTAINER:-cloudpilot-ai}"
REQUESTED_STAMP="${1:-latest}"

if [ "$REQUESTED_STAMP" = "latest" ]; then
  PREVIOUS_CONTAINER="$(sudo docker ps -a --format '{{.Names}}' | grep -E "^${CURRENT_CONTAINER}-previous-[0-9]{8}-[0-9]{6}$" | sort -r | head -n 1 || true)"
else
  PREVIOUS_CONTAINER="${CURRENT_CONTAINER}-previous-${REQUESTED_STAMP}"
fi

if [ -z "$PREVIOUS_CONTAINER" ] || ! sudo docker inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1; then
  echo "No retained previous container found. List candidates with:" >&2
  echo "sudo docker ps -a --filter name=${CURRENT_CONTAINER}-previous-" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
REJECTED_CONTAINER="${CURRENT_CONTAINER}-rejected-${STAMP}"

if sudo docker inspect "$CURRENT_CONTAINER" >/dev/null 2>&1; then
  echo "Preserving current container as $REJECTED_CONTAINER ..."
  sudo docker stop --time 20 "$CURRENT_CONTAINER" >/dev/null 2>&1 || true
  sudo docker rename "$CURRENT_CONTAINER" "$REJECTED_CONTAINER"
fi

echo "Restoring $PREVIOUS_CONTAINER as $CURRENT_CONTAINER ..."
sudo docker rename "$PREVIOUS_CONTAINER" "$CURRENT_CONTAINER"
sudo docker start "$CURRENT_CONTAINER" >/dev/null

healthy=0
for _ in $(seq 1 30); do
  if ! sudo docker inspect -f '{{.State.Running}}' "$CURRENT_CONTAINER" 2>/dev/null | grep -q true; then
    break
  fi
  if sudo docker exec "$CURRENT_CONTAINER" python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=2).read()' \
    >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done

if [ "$healthy" -eq 1 ]; then
  echo "Rollback successful. Previous Intelligent Provisioning Platform container is healthy."
  echo "The replaced container, if any, is preserved as: $REJECTED_CONTAINER"
else
  echo "Previous container was started but did not pass the Streamlit health check." >&2
  sudo docker logs --tail 100 "$CURRENT_CONTAINER" >&2 || true
  exit 1
fi
