# Task Definitions do ECS

O pipeline de CI/CD (`pipeline.yml`) faz deploy de 4 services e roda 1 task avulsa a cada push em `develop`.

## Services (rodam continuamente)

### 1. `system-automation-backend-dev` (API)
- Container: `system-automation-backend-dev`
- Port: `8000` (TCP)
- Health Check: `GET /health/ready`

### 2. `system-automation-worker-dev` (Celery Worker)
- Comando: `celery -A app.workers.celery_app worker --loglevel=info --queues=celery`

### 3. `system-automation-orders-worker-dev` (Orders Worker)
- Comando: `celery -A app.workers.celery_app worker --loglevel=info --queues=orders_processing --concurrency=1 --prefetch-multiplier=1`

### 4. `system-automation-beat-dev` (Celery Beat)
- Comando: `celery -A app.workers.celery_app beat --loglevel=info`
- **Deve ter sempre `desired count = 1`** — mais de uma instância duplica a sincronização com o Databricks a cada 2h

## Task avulsa (não é service)

### 5. `system-automation-migrations-dev`
- Comando: `alembic upgrade head`
- Rodada via `aws ecs run-task` pelo pipeline, antes do deploy da API — não fica rodando continuamente

## Como o pipeline usa isso

1. **Rodar migrations antes do deploy**: registra nova revisão de `system-automation-migrations-dev` com a imagem recém buildada, roda via `run-task`, espera terminar (timeout 10 min) e falha o deploy se o `exitCode` não for `0`
2. **Deploy no Amazon ECS**: atualiza o service da API com a nova imagem
3. **Deploy dos processos Celery**: registra nova revisão e força novo deploy em `system-automation-worker-dev`, `system-automation-orders-worker-dev` e `system-automation-beat-dev`
4. **Validar convergência**: espera todos os 4 services chegarem em `runningCount == desiredCount` com rollout `COMPLETED`

## Criar uma nova task definition a partir de outra existente

Se precisar recriar alguma (ex: mudar CPU/memory, adicionar variável):

1. Console ECS → Task Definitions → abra a family de referência mais próxima (ex: `system-automation-backend-dev` para clonar a config de rede/secrets)
2. Selecione a revisão mais recente → **Create new revision** → editor JSON
3. Troque `family`, o `name` dentro de `containerDefinitions[0]` e o `command`
4. Se o novo processo não expõe porta HTTP, remova `portMappings` e `healthCheck`
5. Ajuste `awslogs-group` em `logConfiguration` para não misturar logs com o container de origem

**Não crie um Service para `system-automation-migrations-dev`** — o pipeline já lida com ela via `run-task`.

## Pré-requisito de permissões (IAM)

A role assumida pelo GitHub Actions (`AWS_ROLE_TO_ASSUME` no `pipeline.yml`) precisa poder, além do que já usava para a API:

- `ecs:RunTask` e `ecs:DescribeTasks` (para a etapa de migrations)
- `ecs:RegisterTaskDefinition`
- `iam:PassRole` sobre o `executionRoleArn` das novas task definitions

Se a etapa "Rodar migrations antes do deploy" falhar com `AccessDenied`, é isso que falta ajustar na policy da role.

## Troubleshooting

- **Migrations falham no deploy?** Veja o log em CloudWatch, grupo `/ecs/system-automation-migrations-dev`
- **Workers não sobem?** Confirme que Redis/Postgres estão acessíveis (mesma VPC, security groups)
- **Beat duplicando sincronização?** Confirme `desired count = 1` no service `system-automation-beat-dev`
