#!/bin/bash
set -euo pipefail

# Los placeholders __...__ los reemplaza buildspec.yml por sed antes de que
# este script llegue al servidor vía CodeDeploy.
REGION="us-east-2"
ACCOUNT_ID="__ACCOUNT_ID__"
ECR_REPOSITORY="__ECR_REPOSITORY__"
SECRETS_MANAGER_PROJECT_NAME="__SECRETS_MANAGER_PROJECT_NAME__"
COMPOSE_FILE="__DEPLOY_PATH__/docker-compose.yml"
DEPLOY_ENV="__DEPLOY_ENV__"
HASH_TAG="__HASH_TAG_BY_DEPLOY_ENV__"

echo "==> Ambiente cargado: ${DEPLOY_ENV}"

ECR_REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
COMPOSE_DIR="$(dirname "$COMPOSE_FILE")"
# docker compose auto-carga `.env` desde el directorio del compose para resolver
# las interpolaciones ${PORT}, ${CONTAINER_NAME}, ${CONTAINER_PORT}.
ENV_FILE="${COMPOSE_DIR}/.env"

echo "==> Login a ECR"
aws ecr get-login-password --region "$REGION" \
  | sudo docker login --username AWS --password-stdin "$ECR_REGISTRY"

echo "==> Pull imagen ${HASH_TAG}"
sudo docker pull "${ECR_REGISTRY}/${ECR_REPOSITORY}:${HASH_TAG}"

echo "==> Extrayendo container_env del secret para docker-compose"
APP_SECRET=$(aws secretsmanager get-secret-value \
  --region "$REGION" \
  --secret-id "${DEPLOY_ENV}/${SECRETS_MANAGER_PROJECT_NAME}" \
  --query SecretString --output text)

APP_ENV_JSON=$(echo "$APP_SECRET" | jq -r '.container_env')
if [ -z "$APP_ENV_JSON" ] || [ "$APP_ENV_JSON" = "null" ]; then
  echo "[ERROR] El secret ${DEPLOY_ENV}/${SECRETS_MANAGER_PROJECT_NAME} no contiene la clave 'container_env'" >&2
  exit 1
fi

echo "$APP_ENV_JSON" | jq -r 'to_entries | .[] | "\(.key)=\(.value)"' \
  | sudo tee "$ENV_FILE" > /dev/null
sudo chown ubuntu:ubuntu "$ENV_FILE"
sudo chmod 600 "$ENV_FILE"

echo "==> Iniciando despliegue..."
sudo docker compose -f "$COMPOSE_FILE" up -d --wait --remove-orphans
echo "==> Despliegue exitoso. La aplicación está corriendo."
sudo docker image prune -f
