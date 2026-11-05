#!/usr/bin/env bash
# Safe IIPP deployment: backup, build, retain old container, health-check, auto-rollback on failure.
set -euo pipefail

TARGET_INSTANCE_ID="${TARGET_INSTANCE_ID:-${APP_INSTANCE_ID:-}}"
AWS_REGION="${AWS_REGION:-ap-south-1}"
IMAGE="cloudpilot-ai:ericsson-poc"
OLD_CONTAINER="${CLOUDPILOT_CONTAINER:-cloudpilot-ai}"
TOKEN_FILE="$HOME/.cloudpilot-approval-token"
APPROVAL_TOKEN="${APPROVAL_TOKEN:-}"
SECRET_DIR="$HOME"
DOCKER_RUN_ARGS=()
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker was not found. Install/start Docker before deploying." >&2
  exit 1
fi
if ! sudo docker info >/dev/null 2>&1; then
  echo "Docker daemon is not available." >&2
  exit 1
fi

# Reuse a backup only when the caller explicitly supplies its timestamp and the snapshot exists.
if [ -n "${CLOUDPILOT_BACKUP_STAMP:-}" ]; then
  BACKUP_STAMP="$CLOUDPILOT_BACKUP_STAMP"
  BACKUP_DIR="$HOME/cloudpilot-backups/$BACKUP_STAMP"
  if [ ! -s "$BACKUP_DIR/project.tar.gz" ] || [ ! -s "$BACKUP_DIR/docker-rollback.txt" ]; then
    echo "Requested backup $BACKUP_STAMP is incomplete. Run ./backup.sh first or unset CLOUDPILOT_BACKUP_STAMP." >&2
    exit 1
  fi
  if sudo docker inspect "$OLD_CONTAINER" >/dev/null 2>&1 && ! sudo docker image inspect "cloudpilot-ai:rollback-$BACKUP_STAMP" >/dev/null 2>&1; then
    echo "Backup does not contain the expected committed container snapshot. Refusing deployment." >&2
    exit 1
  fi
  echo "Using existing backup: $BACKUP_DIR"
else
  if [ ! -x "$SCRIPT_DIR/backup.sh" ]; then
    echo "backup.sh is missing or not executable. Refusing deployment without a rollback backup." >&2
    exit 1
  fi
  BACKUP_STAMP="$("$SCRIPT_DIR/backup.sh" "$SCRIPT_DIR")"
  BACKUP_DIR="$HOME/cloudpilot-backups/$BACKUP_STAMP"
fi

PREVIOUS_CONTAINER="${OLD_CONTAINER}-previous-${BACKUP_STAMP}"
if sudo docker inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1; then
  echo "Rollback container name already exists: $PREVIOUS_CONTAINER" >&2
  exit 1
fi

# Keep reviewer token out of console logs.
if [ -z "$APPROVAL_TOKEN" ] && [ -f "$TOKEN_FILE" ]; then
  APPROVAL_TOKEN="$(cat "$TOKEN_FILE")"
fi
if [ -z "$APPROVAL_TOKEN" ]; then
  if command -v openssl >/dev/null 2>&1; then
    APPROVAL_TOKEN="$(openssl rand -hex 24)"
  else
    APPROVAL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
  fi
fi
printf '%s' "$APPROVAL_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

mount_optional_secret() {
  local env_name="$1" file_name="$2" secret_value mount_name
  secret_value="${!env_name:-}"
  if [ -n "$secret_value" ]; then
    (umask 077; printf '%s' "$secret_value" > "$SECRET_DIR/$file_name")
    chmod 600 "$SECRET_DIR/$file_name"
  fi
  case "$env_name" in
    GOOGLE_CLOUD_BILLING_API_KEY) mount_name="google-cloud-billing-api-key" ;;
    OPENAI_API_KEY) mount_name="openai-api-key" ;;
    ANTHROPIC_API_KEY) mount_name="anthropic-api-key" ;;
    GEMINI_API_KEY) mount_name="gemini-api-key" ;;
    LOCAL_LLM_API_KEY) mount_name="local-llm-api-key" ;;
  esac
  if [ -f "$SECRET_DIR/$file_name" ]; then
    DOCKER_RUN_ARGS+=(-v "$SECRET_DIR/$file_name:/run/secrets/$mount_name:ro")
  fi
}
mount_optional_secret "GOOGLE_CLOUD_BILLING_API_KEY" ".cloudpilot-google-cloud-billing-api-key"
mount_optional_secret "OPENAI_API_KEY" ".cloudpilot-openai-api-key"
mount_optional_secret "ANTHROPIC_API_KEY" ".cloudpilot-anthropic-api-key"
mount_optional_secret "GEMINI_API_KEY" ".cloudpilot-gemini-api-key"
mount_optional_secret "LOCAL_LLM_API_KEY" ".cloudpilot-local-llm-api-key"

# Build while the previous container is still running, so a failed build causes no downtime.
echo "Building $IMAGE ..."
sudo docker build -t "$IMAGE" .

if sudo docker inspect "$OLD_CONTAINER" >/dev/null 2>&1; then
  echo "Retaining the existing container as $PREVIOUS_CONTAINER ..."
  sudo docker stop --time 20 "$OLD_CONTAINER" >/dev/null 2>&1 || true
  sudo docker rename "$OLD_CONTAINER" "$PREVIOUS_CONTAINER"
fi

restore_previous() {
  echo "Deployment failed. Restoring the previous container..." >&2
  sudo docker rm -f "$OLD_CONTAINER" >/dev/null 2>&1 || true
  if sudo docker inspect "$PREVIOUS_CONTAINER" >/dev/null 2>&1; then
    sudo docker rename "$PREVIOUS_CONTAINER" "$OLD_CONTAINER" || true
    sudo docker start "$OLD_CONTAINER" >/dev/null 2>&1 || true
    sudo docker logs --tail 60 "$OLD_CONTAINER" >&2 || true
  else
    echo "No previous container was retained. Backup files are at $BACKUP_DIR" >&2
  fi
}

sudo mkdir -p /opt/cloudpilot/data /opt/cloudpilot/terraform-plugin-cache
sudo chmod 700 /opt/cloudpilot/data /opt/cloudpilot/terraform-plugin-cache
if [ "${ENABLE_TERRAFORM_WORKFLOW:-false}" = "true" ] && [ -z "${TF_STATE_BUCKET:-}" ]; then
  echo "ENABLE_TERRAFORM_WORKFLOW=true requires TF_STATE_BUCKET. Configure the versioned S3 state bucket first." >&2
  restore_previous
  exit 1
fi
if ! sudo docker run -d \
  --name "$OLD_CONTAINER" \
  --restart unless-stopped \
  -p 8501:8501 \
  -v /opt/cloudpilot/data:/data \
  -v /opt/cloudpilot/terraform-plugin-cache:/terraform-plugin-cache \
  -v "$TOKEN_FILE:/run/secrets/cloudpilot-approval-token:ro" \
  -e AWS_REGION="$AWS_REGION" \
  -e TF_STATE_BUCKET="${TF_STATE_BUCKET:-}" \
  -e TF_STATE_REGION="${TF_STATE_REGION:-$AWS_REGION}" \
  -e TF_STATE_PREFIX="${TF_STATE_PREFIX:-iipp/deployments}" \
  -e IIPP_ALLOWED_AWS_REGIONS="${IIPP_ALLOWED_AWS_REGIONS:-ap-south-1}" \
  -e IIPP_ALLOWED_AWS_INSTANCE_FAMILIES="${IIPP_ALLOWED_AWS_INSTANCE_FAMILIES:-t3}" \
  -e TF_PLUGIN_CACHE_DIR=/terraform-plugin-cache \
  -e AWS_SECRETS_MANAGER_REGION="${AWS_SECRETS_MANAGER_REGION:-$AWS_REGION}" \
  -e TARGET_INSTANCE_ID="$TARGET_INSTANCE_ID" \
  -e ENABLE_COST_EXPLORER=true \
  -e COST_EXPLORER_REGION="${COST_EXPLORER_REGION:-us-east-1}" \
  -e AWS_PRICING_API_REGION="${AWS_PRICING_API_REGION:-us-east-1}" \
  -e AZURE_PRICING_REGION="${AZURE_PRICING_REGION:-centralindia}" \
  -e GCP_PRICING_REGION="${GCP_PRICING_REGION:-asia-south1}" \
  -e LLM_PROVIDER="${LLM_PROVIDER:-bedrock}" \
  -e BEDROCK_ENABLED="${BEDROCK_ENABLED:-true}" \
  -e BEDROCK_MODEL_ID="${BEDROCK_MODEL_ID:-apac.amazon.nova-lite-v1:0}" \
  -e OPENAI_MODEL="${OPENAI_MODEL:-gpt-4o-mini}" \
  -e OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.openai.com/v1}" \
  -e ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-haiku-4-5-20251001}" \
  -e GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.8-flash}" \
  -e LOCAL_LLM_MODEL="${LOCAL_LLM_MODEL:-local-model}" \
  -e LOCAL_LLM_BASE_URL="${LOCAL_LLM_BASE_URL:-}" \
  -e ENABLE_APPROVAL_EXECUTION="${ENABLE_APPROVAL_EXECUTION:-false}" \
  -e IIPP_KUBERNETES_TIMEOUT_SECONDS="${IIPP_KUBERNETES_TIMEOUT_SECONDS:-3600}" \
  -e IIPP_SERVER_TIMEOUT_SECONDS="${IIPP_SERVER_TIMEOUT_SECONDS:-900}" \
  -e ENABLE_TERRAFORM_WORKFLOW="${ENABLE_TERRAFORM_WORKFLOW:-false}" \
  "${DOCKER_RUN_ARGS[@]}" \
  "$IMAGE" >/dev/null; then
  restore_previous
  exit 1
fi

healthy=0
for _ in $(seq 1 45); do
  state="$(sudo docker inspect -f '{{.State.Running}}' "$OLD_CONTAINER" 2>/dev/null || true)"
  if [ "$state" != "true" ]; then
    break
  fi
  if sudo docker exec "$OLD_CONTAINER" python -c \
    'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=2).read()' \
    >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done

if [ "$healthy" -ne 1 ]; then
  echo "New container failed its Streamlit health check. Recent logs:" >&2
  sudo docker logs --tail 120 "$OLD_CONTAINER" >&2 || true
  restore_previous
  exit 1
fi

echo "Intelligent Provisioning Platform deployment succeeded and passed the Streamlit health check."
echo "Backup folder: $BACKUP_DIR"
echo "Previous container retained as: $PREVIOUS_CONTAINER"
echo "To revert this deployment later: ./rollback.sh $BACKUP_STAMP"
echo "Do not delete the previous container or rollback image until the new version is accepted."
echo "Reviewer token saved at: $TOKEN_FILE (not printed)."
echo "Open http://<EC2-public-IP>:8501. Restrict TCP 8501 to the demo/reviewer IP."
