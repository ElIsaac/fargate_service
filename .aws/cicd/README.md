# CI/CD Pipeline — fargate_service

App FastAPI desplegada vía CodePipeline → CodeBuild → **Amazon ECS (Fargate)**.

## Flujo general

```
GitHub (push) → CodePipeline → CodeBuild (buildspec.yml)
                                    ↓ build + push a ECR + genera imagedefinitions.json
                               CodeBuild (buildspec-testing.yml)
                                    ↓ pull + smoke tests (pytest)
                               Deploy: acción "Amazon ECS" (Rolling)
                                    ↓ nueva revisión de Task Definition + update del Service
                                ECS Fargate (detrás de un ALB)
```

A diferencia del deploy a EC2 (CodeDeploy + `docker compose`), en Fargate **no hay
instancia ni `deploy.sh`**: ECS corre el contenedor a partir de una Task Definition,
y la acción de deploy solo le indica qué imagen usar (`imagedefinitions.json`).

---

## Estructura de archivos

```
.aws/
├── cicd/
│   ├── buildspec.yml          # Build: construye, sube a ECR y genera imagedefinitions.json
│   ├── buildspec-testing.yml  # Testing: levanta el contenedor y corre pytest
│   └── README.md
├── ecs/
│   └── taskdef.json           # Plantilla de Task Definition (referencia para crear la infra)
├── tests/
│   ├── conftest.py            # Fixture: exige TESTING_BASE_URL
│   └── test_smoke.py          # Smoke E2E contra /health, / y el catch-all
docker-compose.yml             # Solo para uso local (no se usa en prod con Fargate)
```

---

## Parte 1 — Infraestructura de ECS (se crea UNA vez)

El pipeline asume que esto ya existe. Pasos mínimos (region `us-east-2`):

### 1.1 — Repositorio ECR
Ya existe (`fargate_service`), creado por el build.

### 1.2 — Log group de CloudWatch
```
aws logs create-log-group --log-group-name /ecs/fargate_service --region us-east-2
```

### 1.3 — Roles IAM
- **Execution role** (`fargate_service-execution-role`): permite a ECS hacer pull de ECR,
  escribir logs y leer los secrets referenciados en la task def.
  - Policy gestionada: `AmazonECSTaskExecutionRolePolicy`
  - + permiso `secretsmanager:GetSecretValue` sobre `prod/fargate_service`.
- **Task role** (`fargate_service-task-role`): permisos que necesita la app en runtime
  (vacío si la app no llama a otros servicios AWS).

### 1.4 — Secrets Manager
Crear el secret `prod/fargate_service`. Cada variable de entorno de la app se referencia
desde `containerDefinitions[].secrets` en la task def (ver [taskdef.json](../ecs/taskdef.json)),
en lugar de bajarse a un `.env`.

### 1.5 — Networking + ALB
- VPC con subnets (privadas para los tasks, públicas para el ALB).
- **Application Load Balancer** + **Target Group** tipo `ip` (Fargate usa `awsvpc`),
  health check → `/health`, puerto 8000.
- Security groups: el del ALB acepta 80/443 desde internet; el de los tasks acepta 8000
  solo desde el SG del ALB.

### 1.6 — Cluster + Task Definition + Service
```
aws ecs create-cluster --cluster-name fargate_service --region us-east-2

# Registrar la task def (reemplaza __ACCOUNT_ID__ y ajusta secrets/cpu/memory):
aws ecs register-task-definition --cli-input-json file://.aws/ecs/taskdef.json --region us-east-2

# Crear el service (Fargate, detrás del target group del ALB):
aws ecs create-service \
  --cluster fargate_service \
  --service-name fargate_service \
  --task-definition fargate_service \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-aaa,subnet-bbb],securityGroups=[sg-tasks],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=arn:aws:elasticloadbalancing:...:targetgroup/...,containerName=fargate_service,containerPort=8000" \
  --region us-east-2
```

> El `containerName` del service y el `"name"` en `imagedefinitions.json` (variable
> `CONTAINER_NAME` del buildspec) **deben coincidir** con el `name` del contenedor en la
> task def. Por defecto: `fargate_service`.

---

## Parte 2 — Pipeline en CodePipeline

### Stage Source
- `FullRepositoryId` → `owner/repo`
- `BranchName` → rama que dispara (ej: `main`)
- Output variables namespace: `SourceVariables`

### Stage Build (CodeBuild — buildspec.yml)
Variables de entorno del stage:

| Variable | Valor |
|---|---|
| `BRANCH_NAME` | `#{SourceVariables.BranchName}` |
| `FULL_GITHUB_REPO_NAME` | `#{SourceVariables.FullRepositoryName}` |
| `COMMIT_HASH` | `#{SourceVariables.CommitId}` |
| `COMMIT_MSG` | `#{SourceVariables.CommitMessage}` |
| `PIPELINE_EXECUTION_ID` | `#{codepipeline.PipelineExecutionId}` |

`ECR_REPOSITORY` y `CONTAINER_NAME` ya vienen como defaults en el `buildspec.yml`.

Output artifact: `deployment-artifacts` (contiene `imagedefinitions.json`, `.aws/cicd/**`, `.aws/tests/**`).

### Stage Testing (CodeBuild — buildspec-testing.yml)
Input artifact: `deployment-artifacts`. Hace pull de la imagen y corre pytest.

### Stage Deploy (acción "Amazon ECS")
- **Action provider:** Amazon ECS (Standard / Rolling)
- **Input artifact:** `deployment-artifacts`
- **Cluster name:** `fargate_service`
- **Service name:** `fargate_service`
- **Image definitions file:** `imagedefinitions.json`

La acción lee `imagedefinitions.json`, crea una nueva revisión de la Task Definition con
el `imageUri` indicado y hace `UpdateService` (rolling deployment).

---

## Cómo funciona internamente

### buildspec.yml — Build
1. Valida variables requeridas y hace login a ECR.
2. Calcula tags (`{BRANCH_TAG}-{SHORT_HASH}-v{BUILD_NUMBER}`, etc.).
3. Inyecta variables en `buildspec-testing.yml` (la etapa de testing hace pull).
4. `docker buildx build --push` con cache de registry en ECR.
5. Genera `imagedefinitions.json` apuntando al tag inmutable por build.

### buildspec-testing.yml — Testing
Levanta Docker-in-Docker, hace pull de la imagen recién construida, la corre exponiendo
el puerto 8000, espera a `/health` y ejecuta `pytest .aws/tests/test_smoke.py`.

---

## Permisos del rol de servicio de CodePipeline
Para la acción de deploy a ECS, el rol de CodePipeline necesita además:
`ecs:DescribeServices`, `ecs:DescribeTaskDefinition`, `ecs:DescribeTasks`,
`ecs:ListTasks`, `ecs:RegisterTaskDefinition`, `ecs:UpdateService`, y `iam:PassRole`
sobre los roles de la task (execution role y task role).

---

## Resumen de cambios por proyecto nuevo

| Dónde | Qué cambiar |
|---|---|
| `buildspec.yml` | `ECR_REPOSITORY`, `CONTAINER_NAME` |
| `.aws/ecs/taskdef.json` | `family`, `name`, secrets, cpu/memory, roles |
| Pipeline → Source | Repositorio y rama |
| Pipeline → Build | Variables de entorno del stage |
| Pipeline → Deploy | Cluster name, Service name |
| Infra ECS | Cluster, Task Def, Service, ALB, roles, secret |
