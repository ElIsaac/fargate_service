#!/bin/bash
set -euo pipefail

TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
if [ -z "${TOKEN:-}" ]; then
  echo "ERROR: No se pudo obtener token IMDSv2" >&2
  exit 1
fi

INSTANCE_ID=$(curl -s \
  -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/instance-id)
if [ -z "${INSTANCE_ID:-}" ]; then
  echo "ERROR: No se pudo obtener INSTANCE_ID" >&2
  exit 1
fi

ENVIRONMENT=$(aws ec2 describe-tags \
  --filters "Name=resource-id,Values=$INSTANCE_ID" \
            "Name=key,Values=Environment" \
  --query 'Tags[0].Value' \
  --output text \
  --region us-east-2)

if [ -z "${ENVIRONMENT:-}" ] || [ "$ENVIRONMENT" = "None" ]; then
  echo "ERROR: El tag 'Environment' no existe en la instancia $INSTANCE_ID" >&2
  exit 1
fi

case "$ENVIRONMENT" in
  stage)
    echo "Deploying to STAGING environment"
    ;;
  prod)
    echo "Deploying to PRODUCTION environment"
    ;;
  *)
    echo "ERROR: Valor inválido para tag 'Environment': $ENVIRONMENT" >&2
    exit 1
    ;;
esac

echo "DEPLOY_ENVIRONMENT=${ENVIRONMENT}" > /tmp/deploy_env
echo "==> Ambiente detectado y guardado: ${ENVIRONMENT}"