# Guía: Crear un nuevo pipeline CI/CD con AWS CodePipeline

## ¿Cómo funciona el sistema?

Cada pipeline sigue este flujo al detectar un push en la rama configurada:

```
GitHub (push) → CodePipeline → CodeBuild (build + push a ECR)
                                         ↓
                              EC2 ← CodeDeploy (pull + deploy)
```

El sistema está diseñado para ser reutilizable: existe un pipeline plantilla para `prod` y otro para `development`. Crear un nuevo pipeline para cualquier proyecto es cuestión de clonar, ajustar unos pocos valores y copiar la carpeta `cicd/`.

---

## Crear un nuevo pipeline

### Paso 1 — Clonar el pipeline plantilla

En la consola de AWS CodePipeline, clonar uno de los pipelines base según el caso:

| Caso | Pipeline a clonar |
|---|---|
| Ambiente de producción | `prod_test_code_pipeline` |
| Ambiente de desarrollo / staging | `development_test_code_pipeline` |

---

### Paso 2 — Ajustar la configuración del pipeline

En el pipeline clonado, modificar los siguientes tres puntos:

**Stage Source — repositorio y rama origen:**
- `FullRepositoryId` → repo de GitHub en formato `owner/nuevo-repo`
- `BranchName` → rama que triggereará el pipeline (ej: `main`, `develop`)

**Trigger — rama a escuchar:**
- En `gitConfiguration.push.branches.includes` → cambiar a la misma rama del Source

**Stage Build — servidor destino:**
- `BuildProject` → crear un nuevo proyecto de codebuild 
- `Environment Variables` → En teoria estas madres ya estan configuradas si clonaste el (development|prod)_test_code_pipelane pero igual checa que si esten. Ojo en el stage Source el nameSpace de las output variables el namespace se debe llamar `SourceVariables` esto por defecto ya esta configurado pero tambien es bueno revisar

| Nombre | Valor |
|---|---|
| BRANCH_NAME | `#{SourceVariables.BranchName}` |
| FULL_GITHUB_REPO_NAME | `#{SourceVariables.FullRepositoryName}` |
| COMMIT_HASH | `#{SourceVariables.CommitId}` |
| COMMIT_MSG | `#{SourceVariables.CommitMessage}` |
| PIPELINE_EXECUTION_ID | `#{codepipeline.PipelineExecutionId}` |

**Stage Deploy — servidor destino:**
- `InstanceTagValue` → tag `Name` de la instancia EC2 destino

> Los demás campos (roles, bucket S3, CodeBuild project, conexión GitHub) se reutilizan tal cual del pipeline clonado.

---

### Paso 3 — Copiar la carpeta `cicd/` al nuevo repo

Ir al repo [ventamovil/test_code_pipeline](https://github.com/ventamovil/test_code_pipeline) y copiar la carpeta `cicd/` completa a la raíz del nuevo repositorio.

Estructura que debe quedar en el nuevo repo:

```
nuevo-repo/
├── cicd/
│   ├── buildspec.yml
│   ├── appspec.yml
│   ├── detect_environment.sh
│   └── deploy.sh
├── docker-compose.yml
└── ... (resto del proyecto)
```

---

### Paso 4 — Configurar las variables en `buildspec.yml`

Editar las tres variables del bloque `env.variables` en `cicd/buildspec.yml`:

```yaml
env:
  variables:
    AWS_DEFAULT_REGION: "us-east-2"

    ECR_REPOSITORY: "nombre-del-repositorio-ecr"
    BASE_DEPLOY_PATH: "/home/ubuntu/apps"
    SECRETS_MANAGER_PROJECT_NAME: "nombre-del-proyecto"
```

| Variable | Descripción | Ejemplo |
|---|---|---|
| `ECR_REPOSITORY` | Nombre del repositorio ECR donde se publicará la imagen | `mi-app-backend` |
| `BASE_DEPLOY_PATH` | Ruta base en el servidor donde se desplegará la app | `/home/ubuntu/apps` |
| `SECRETS_MANAGER_PROJECT_NAME` | Nombre del proyecto en Secrets Manager (sin el prefijo de ambiente) | `mi-app` |

> `BASE_DEPLOY_PATH` generalmente no cambia entre servidores

---

## Cómo funciona internamente

### Build (CodeBuild + `buildspec.yml`)

CodeBuild recibe las variables del pipeline y ejecuta tres fases:

**pre_build — Preparación**

Calcula dinámicamente los valores que se usarán en el despliegue:

```
BRANCH_TAG          = rama sanitizada (ej: "main", "develop")
GITHUB_REPO_NAME    = nombre del repo extraído de FULL_GITHUB_REPO_NAME
FINAL_DEPLOY_PATH   = BASE_DEPLOY_PATH / BRANCH_TAG_REPO_NAME
LATEST_TAG          = BRANCH_TAG-latest  (ej: "main-latest")
```

Luego reemplaza los placeholders en los archivos de la carpeta `cicd/` y en `docker-compose.yml` usando `sed`:

| Placeholder | Reemplazado por |
|---|---|
| `__DEPLOY_PATH__` | Ruta final de despliegue en el servidor |
| `__ACCOUNT_ID__` | ID de la cuenta AWS |
| `__ECR_REPOSITORY__` | Nombre del repositorio ECR |
| `__SECRETS_MANAGER_PROJECT_NAME__` | Nombre del proyecto en Secrets Manager |
| `__LATEST_TAG_BY_ENVIRONMENT__` | Tag `latest` con prefijo de rama |

**build — Construcción de imagen**

Construye y publica la imagen Docker en ECR con dos tags:
- `{rama}-latest` — para que el servidor siempre pueda hacer pull del último
- `{rama}-v{build_number}-{commit_hash}` — para trazabilidad

Usa cache de ECR para optimizar tiempos de build.

**post_build — Resumen**

Muestra los tags generados. Los artefactos que pasan a CodeDeploy son `cicd/**/*` y `docker-compose.yml` (ya con los placeholders reemplazados).

---

### Deploy (CodeDeploy + `appspec.yml`)

CodeDeploy copia los artefactos al servidor EC2 y ejecuta los hooks en orden:

#### Hook 1: `detect_environment.sh` (BeforeInstall)

Consulta el tag `Environment` de la instancia EC2 via IMDSv2 y lo guarda en `/tmp/deploy_env`. El valor debe ser `stage` o `prod`.

#### Hook 2: `deploy.sh` (ApplicationStart)

1. Lee el ambiente desde `/tmp/deploy_env`
2. Obtiene los secrets de **AWS Secrets Manager** → `{ambiente}/{SECRETS_MANAGER_PROJECT_NAME}`
3. Escribe el archivo `.env` en el directorio de despliegue
4. Hace pull de la imagen `{rama}-latest` desde ECR
5. Reinicia los contenedores con `docker compose down` + `up -d`
6. Limpia imágenes antiguas con `docker image prune`

---

## Secrets Manager

Los secrets deben existir antes del primer deploy, organizados así:

```
stage/{SECRETS_MANAGER_PROJECT_NAME}
prod/{SECRETS_MANAGER_PROJECT_NAME}
```

Cada secret es un JSON con las variables de entorno del proyecto:

```json
{
  "DB_HOST": "...",
  "DB_PASSWORD": "...",
  "APP_SECRET_KEY": "..."
}
```

`deploy.sh` los convierte automáticamente a un archivo `.env` al momento del deploy.

---

## Requisitos de la instancia EC2

La instancia destino debe tener:

- Tag `Environment` con valor `stage` o `prod`
- Tag `Name` que coincida con `InstanceTagValue` en el pipeline
- Docker y docker-compose instalados
- AWS CLI configurado
- Usuario `ubuntu` con permisos sudo
- **CodeDeploy Agent** instalado y corriendo
- Instance Profile con permisos para: ECR, Secrets Manager, EC2 (`describe-tags`)

---

## Recursos AWS compartidos (no requieren configuración por proyecto)

| Recurso | Detalle |
|---|---|
| **CodeConnections** | Conexión con GitHub ya configurada y autorizada |
| **S3** | Bucket de artefactos (auto-gestionado por CodePipeline) |
| **IAM Roles** | `GenericCodePipelineRole` y roles de CodeBuild/CodeDeploy/EC2 |

---

## Resumen de cambios por proyecto nuevo

| Dónde | Qué cambiar |
|---|---|
| CodeBuild | crear nuevo proyecto |
| Pipeline → Stage Source | `Repositorio` y `rama de la cual tomara el codigo` |
| Pipeline → Trigger | `rama que al detectar cambios dispara la ejecucion` (main, development, etc) |
| Pipeline → Stage Build | `proyecto codebuild recien creado` |
| Pipeline → Stage Deploy | `Instance Name` (servidor destino) |
| `cicd/buildspec.yml` | `ECR_REPOSITORY` y `SECRETS_MANAGER_PROJECT_NAME` |
| AWS Secrets Manager | Crear secrets `stage/…` y `prod/…` para el proyecto |