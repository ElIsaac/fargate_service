#!/bin/bash
set -euo pipefail

REGION="us-east-2"
ACCOUNT_ID="__ACCOUNT_ID__"
ECR_REPOSITORY="__ECR_REPOSITORY__"
SECRETS_MANAGER_PROJECT_NAME="__SECRETS_MANAGER_PROJECT_NAME__"
COMPOSE_FILE="__DEPLOY_PATH__/docker-compose.yml"



if [ ! -f /tmp/deploy_env ]; then
  echo "ERROR: /tmp/deploy_env no encontrado. ¿Se ejecutó detect_environment.sh?" >&2
  exit 1
fi
set -a
source /tmp/deploy_env
set +a
 
if [ -z "${DEPLOY_ENVIRONMENT:-}" ]; then
  echo "ERROR: DEPLOY_ENVIRONMENT no está definido en /tmp/deploy_env" >&2
  exit 1
fi
echo "==> Ambiente cargado: ${DEPLOY_ENVIRONMENT}"


SECRET_NAME="${DEPLOY_ENVIRONMENT}/${SECRETS_MANAGER_PROJECT_NAME}"
ENV_FILE="$(dirname "$COMPOSE_FILE")/.env"

sudo mkdir -p "$(dirname "$ENV_FILE")"
sudo chown ubuntu:ubuntu "$(dirname "$ENV_FILE")"

echo "==> Recuperando secrets de '${SECRET_NAME}'"
SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --region "$REGION" \
  --query 'SecretString' \
  --output text)

if [ -z "${SECRET_JSON:-}" ]; then
  echo "ERROR: No se pudo recuperar el secret '${SECRET_NAME}'" >&2
  exit 1
fi

echo "==> Escribiendo .env en ${ENV_FILE}"
echo "$SECRET_JSON" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k, v in data.items():
    v_escaped = str(v).replace(\"'\", \"'\\\"'\\\"'\")
    print(f\"{k}='{v_escaped}'\")
" > "$ENV_FILE"

sudo chmod 644 "$ENV_FILE"
echo "   Variables escritas: $(wc -l < "$ENV_FILE")"

ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"

echo "==> Login a ECR"
aws ecr get-login-password --region "$REGION" \
  | sudo docker login --username AWS --password-stdin "$ECR_REGISTRY"

echo "==> Pull imagen latest"
sudo docker pull "${ECR_REGISTRY}/${ECR_REPOSITORY}:__LATEST_TAG_BY_ENVIRONMENT__"

echo "==> Reiniciando contenedor"
sudo docker compose -f "$COMPOSE_FILE" down --remove-orphans
sudo docker compose -f "$COMPOSE_FILE" up -d

echo "==> Limpiando imagenes viejas"
sudo docker image prune -f




echo "==> Deploy exitoso en ambiente: ${DEPLOY_ENVIRONMENT}"