# CI/CD Pipeline — fargate_service

App FastAPI simple desplegada vía CodePipeline → CodeBuild → CodeDeploy sobre EC2.

## Flujo general

```
GitHub (push) → CodePipeline → CodeBuild (buildspec.yml)
                                    ↓ build + push a ECR
                               CodeBuild (buildspec-testing.yml)
                                    ↓ pull + smoke tests (pytest)
                               CodeDeploy (appspec.yml + deploy.sh)
                                    ↓ pull + docker compose up
                                   EC2
```

---

## Estructura de archivos

```
.aws/
├── cicd/
│   ├── buildspec.yml          # Build: construye y sube imagen a ECR
│   ├── buildspec-testing.yml  # Testing: levanta el contenedor y corre pytest
│   ├── appspec.yml            # Deploy: instrucciones para CodeDeploy
│   ├── deploy.sh              # Script ejecutado en EC2 al desplegar
│   └── README.md
├── tests/
│   ├── conftest.py            # Fixture: exige TESTING_BASE_URL
│   └── test_smoke.py          # Smoke E2E contra /health, / y el catch-all
docker-compose.yml             # Definición del contenedor en producción
```

---

## Crear un nuevo pipeline

### Paso 1 — Clonar pipeline plantilla

En la consola de AWS CodePipeline, clonar según el ambiente:

| Ambiente | Pipeline a clonar |
|---|---|
| Producción | `prod_test_code_pipeline` |
| Desarrollo / staging | `development_test_code_pipeline` |

### Paso 2 — Ajustar el pipeline clonado

**Stage Source:**
- `FullRepositoryId` → `owner/nuevo-repo`
- `BranchName` → rama que dispara el pipeline (ej: `main`, `develop`)
- El namespace de output variables debe llamarse `SourceVariables` (default)

**Trigger:**
- `gitConfiguration.push.branches.includes` → misma rama del Source

**Stage Build (CodeBuild):**
- Crear un nuevo proyecto de CodeBuild apuntando a este repo
- Verificar que estas variables de entorno estén configuradas en el stage:

| Variable | Valor |
|---|---|
| `BRANCH_NAME` | `#{SourceVariables.BranchName}` |
| `FULL_GITHUB_REPO_NAME` | `#{SourceVariables.FullRepositoryName}` |
| `COMMIT_HASH` | `#{SourceVariables.CommitId}` |
| `COMMIT_MSG` | `#{SourceVariables.CommitMessage}` |
| `PIPELINE_EXECUTION_ID` | `#{codepipeline.PipelineExecutionId}` |
| `DEPLOY_FOLDER` | nombre de carpeta en el servidor (ej: `fargate-service`) |
| `DEPLOY_ENV` | `stage` o `prod` |

**Stage Deploy (CodeDeploy):**
- `InstanceTagValue` → tag `Name` de la instancia EC2 destino

### Paso 3 — Copiar `.aws/` y `docker-compose.yml` al nuevo repo

### Paso 4 — Configurar variables en `buildspec.yml`

```yaml
env:
  variables:
    AWS_DEFAULT_REGION: "us-east-2"
    ECR_REPOSITORY: "fargate_service"
    BASE_DEPLOY_PATH: "/home/ubuntu/apps"
    SECRETS_MANAGER_PROJECT_NAME: "fargate_service"
```

| Variable | Descripción |
|---|---|
| `ECR_REPOSITORY` | Nombre del repositorio ECR |
| `SECRETS_MANAGER_PROJECT_NAME` | Nombre del proyecto en Secrets Manager (sin prefijo de ambiente) |
| `BASE_DEPLOY_PATH` | Ruta base en el servidor — generalmente no cambia |

---

## Cómo funciona internamente

### buildspec.yml — Build

**pre_build:**
1. Valida que estén presentes todas las variables requeridas.
2. Login a ECR con `aws ecr get-login-password`.
3. Calcula variables derivadas:

| Variable | Valor |
|---|---|
| `BRANCH_TAG` | rama sanitizada (no alfanumérico → `-`) |
| `FINAL_DEPLOY_PATH` | `BASE_DEPLOY_PATH/DEPLOY_FOLDER` |
| `HASH_TAG_BY_ENVIRONMENT` | `{BRANCH_TAG}-{7 chars del commit}-v{BUILD_NUMBER}` |

4. Reemplaza placeholders con `sed` en `appspec.yml`, `deploy.sh`, `docker-compose.yml`, `buildspec-testing.yml`.

**build:** construye y sube la imagen con `docker buildx` (cache de registry en ECR):

| Tag | Propósito |
|---|---|
| `{BRANCH_TAG}-{SHORT_HASH}-v{BUILD_NUMBER}` | Tag principal (por commit + build) |
| `{BRANCH_TAG}-v{BUILD_NUMBER}` | Trazabilidad por número de build |
| `latest-{BRANCH_TAG}` | Pull rápido |
| `cache-{BRANCH_TAG}` | Cache de capas para builds futuros |

**Artefactos a las siguientes etapas:** `.aws/cicd/**/*`, `.aws/tests/**/*`, `docker-compose.yml` (ya con placeholders reemplazados).

### buildspec-testing.yml — Testing

Esta app **no requiere secretos ni autenticación para arrancar**, por lo que el testing es directo:

1. Levanta Docker-in-Docker (DinD) con storage driver `vfs`.
2. Login a ECR + pull de la imagen recién construida (`HASH_TAG_BY_ENVIRONMENT`).
3. `docker run` exponiendo el puerto `8000` (el que escucha el contenedor, ver `Dockerfile`).
4. Espera hasta que `http://localhost:8000/health` responda (timeout 30s).
5. Ejecuta `pytest .aws/tests/test_smoke.py` y publica `report.xml` (JUnit).
6. Limpia el contenedor.

### appspec.yml — Deploy

CodeDeploy copia los artefactos al servidor y ejecuta:

```
ApplicationStart → deploy.sh (runas: ubuntu, timeout: 300s)
```

Archivos copiados:
- `docker-compose.yml` → `{DEPLOY_PATH}/`
- `.aws/cicd/` → `{DEPLOY_PATH}/cicd/`

### deploy.sh — Script en EC2

Se ejecuta como usuario `ubuntu` (placeholders ya reemplazados por `buildspec.yml`):

1. Login a ECR.
2. Pull de la imagen `{ECR_REPOSITORY}:{HASH_TAG_BY_ENVIRONMENT}`.
3. Descarga el secret `{DEPLOY_ENV}/{SECRETS_MANAGER_PROJECT_NAME}` y vuelca su clave `container_env` a un archivo `.env` junto al compose (lo auto-carga `docker compose` para resolver `${PORT}`, `${CONTAINER_NAME}`, `${CONTAINER_PORT}`).
4. `docker compose up -d --wait --remove-orphans`.
5. `docker image prune -f`.

Placeholders que reemplaza `buildspec.yml`:

| Placeholder | Reemplazado por |
|---|---|
| `__ACCOUNT_ID__` | ID de la cuenta AWS |
| `__ECR_REPOSITORY__` | Nombre del repo ECR |
| `__SECRETS_MANAGER_PROJECT_NAME__` | Nombre del proyecto |
| `__DEPLOY_PATH__` | Ruta final de despliegue |
| `__DEPLOY_ENV__` | Ambiente (`stage` o `prod`) |
| `__HASH_TAG_BY_DEPLOY_ENV__` | Tag de la imagen a desplegar |

---

## Secrets Manager — Estructura requerida

```
{DEPLOY_ENV}/{SECRETS_MANAGER_PROJECT_NAME}   ← variables de la app
```

El secret es un JSON con la clave `container_env`, que `deploy.sh` convierte en el
`.env` que consume `docker-compose.yml`. Mínimo requerido:

```json
{
  "container_env": {
    "PORT": "8123",
    "CONTAINER_NAME": "fargate_service",
    "CONTAINER_PORT": "8000"
  }
}
```

> A diferencia de proyectos con login, esta app no necesita el secret
> `testing_credentials`: la suite de smoke no autentica.

---

## Requisitos de la instancia EC2

- Tag `Name` que coincida con `InstanceTagValue` en el pipeline
- Docker y docker-compose instalados
- AWS CLI configurado
- Usuario `ubuntu` con permisos sudo
- CodeDeploy Agent instalado y corriendo
- Instance Profile con permisos para: ECR, Secrets Manager

---

## Resumen de cambios por proyecto nuevo

| Dónde | Qué cambiar |
|---|---|
| `buildspec.yml` | `ECR_REPOSITORY`, `SECRETS_MANAGER_PROJECT_NAME`, `BASE_DEPLOY_PATH` |
| Pipeline → Source | Repositorio y rama |
| Pipeline → Trigger | Rama que dispara el pipeline |
| Pipeline → Build | Nuevo proyecto CodeBuild + variables de entorno |
| Pipeline → Deploy | `InstanceTagValue` (tag Name del EC2) |
| Secrets Manager | Crear `stage/…` y `prod/…` con la clave `container_env` |
