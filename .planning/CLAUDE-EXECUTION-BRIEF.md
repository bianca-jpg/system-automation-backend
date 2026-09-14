# Claude Execution Brief — Milestone v1.2 Platform Hardening

**Para:** Claude Code (ou executor GSD)  
**Repo:** `backend`  
**Data:** 2026-08-07  
**Modo atual:** Planning completo — **ainda não executar** até o humano pedir `/gsd-plan-phase` / `/gsd-execute-phase` ou autorização explícita.

> **Nota 2026-08-12:** este brief nunca foi executado via GSD, mas commits regulares fora do fluxo formal já fecharam boa parte de CI-02/03, SEC-01, OPS-01/02/03, OPS-05 e CONTRACT-02 — reverificado direto no código. Ver notas inline por fase abaixo e detalhe em `.planning/REQUIREMENTS.md` § v1.2 e `.planning/milestones/v1.2-PLATFORM-HARDENING.md` § Status update. Antes de rodar `/gsd-plan-phase` numa dessas fases, confira o que já existe — planejar do zero geraria retrabalho.

---

## 1. Contexto em uma página

- **Produto:** Backend FastAPI automation OR (otimização de Ordens de Reserva + ingestão Databricks + auth/RBAC/parâmetros).
- **Milestone anterior (v1.1):** Phases 1–3 Linx — schema `produto_tamanho_posicao` + `ordens_reserva_linx`, ingestão da referência, escrita na geração. **Não apagar / não reabrir** esse histórico em ROADMAP/STATE sem necessidade.
- **Milestone atual (v1.2):** Remediação do audit de plataforma (CI, segurança, deploy Celery/migrations, Docker, contratos, observabilidade).
- **Fonte da verdade do audit:** `.planning/research/AUDIT-2026-08-07.md`
- **Fases:** `.planning/milestones/v1.2-PLATFORM-HARDENING.md` + seção no `.planning/ROADMAP.md`
- **REQs:** seção **v1.2** em `.planning/REQUIREMENTS.md` (`CI-*`, `SEC-*`, `OPS-*`, `CONTRACT-*`, `OBS-*`)
- **Padrão a espelhar:** Collab API em `collab-backend`
  - `.github/workflows/ci.yml` + `deploy-ecs.yaml` (`needs: quality/tests`, worker/beat, migrate)
  - `.docker/Dockerfile` sem `--group dev` + `.dockerignore`

## 2. Regras absolutas

1. **Não misturar** trabalho Linx de negócio (GRADE/INTG) neste milestone.
2. **Não inventar Cognito** como obrigatório da Phase 5 — o audit pede **fail-fast** JWT/seed/OTP; Cognito é dívida estratégica (P2-03).
3. Preferir **padrões Collab** quando houver dúvida de CI/ECS/Docker.
4. Commits Conventional Commits; branch a partir de `develop`.
5. Testes preferencialmente via ambiente do projeto (Docker compose `api` se uv/PyICU falhar no host — precedente Phase 1).
6. **Não criar** Makefiles/scripts ad hoc só para wrappear um comando.
7. Atualizar `.planning/STATE.md` ao avançar fases; gerar `*-SUMMARY.md` por plan.

## 3. Ordem de execução sugerida

| Wave | Phases | Por quê |
|------|--------|---------|
| A | **4** CI gates + **5** Auth fail-fast (+ início **7** dockerignore/Dockerfile) | Para o sangramento: deploy cego + defaults inseguros |
| B | **6** worker+beat+migrations | Só depois (ou junto com) CI verde; muda infra AWS |
| C | **8** contratos/docs + **9** observability | Menos bloqueante; depende de secrets (Sentry/CORS) |

Antes de cada phase: `/gsd-discuss-phase N` (se decisões abertas) → `/gsd-plan-phase N` → `/gsd-execute-phase N`.

## 4. Brief por fase (o que fazer / o que não fazer)

### Phase 4 — CI quality gates (CI-01..03)

**Fazer:**
- Adicionar job(s) `quality`: `uv run ruff check`, `ruff format --check`, `pyright` (alinhar a `pyproject.toml` / adicionar config se faltar).
- Adicionar job `tests`: pytest com Postgres (+ Redis se necessário para fixtures).
- Fazer `deploy` em `pipeline.yml` (ou renomear espelhando Collab) depender de quality+tests.
- Rodar em `pull_request` e no push da branch de deploy.

**Não fazer:**
- Deploy “best effort” se testes flaky — deselect justificado como no Collab, não silenciar suite inteira.
- Instalar deps no host se o padrão do repo é container.

**Done quando:** push com teste quebrado **não** atualiza task definition / serviço ECS.

> **Nota 2026-08-12:** `pipeline.yml` já roda `ruff check` e `pytest` (com Postgres isolado) antes do build/push, sem `continue-on-error` — o "done quando" já vale na prática, mesmo sem os jobs `quality`/`tests` separados. Falta só `ruff format --check` e `pyright`.

### Phase 5 — Auth / OTP / seed / JWT (SEC-01..04)

**Fazer:**
- Fail-fast na boot se `ENV=PROD` (ou equivalente) e `JWT_SECRET` ausente / placeholder (`change-me-in-production`).
- Default `SEED_AUTH_ON_STARTUP=False`; em PROD, seed desligado ou hard-fail se True.
- Wire OTP → `enviar_email` / módulo `comunicacoes` em PROD (código nunca no log de PROD).
- Runbook curto: rotacionar/remover usuários seed se já existem em ambientes compartilhados.

**Não fazer:**
- Reescrever todo o módulo auth.
- Logar OTP em PROD “só um pouco”.
- Assumir Cognito sem decisão explícita do time.

**Done quando:** app PROD não sobe com secret/seed inseguros; fluxo register/recovery recebe e-mail com OTP.

> **Nota 2026-08-12:** `settings.py` já falha o boot em PROD para `JWT_SECRET` fraco (linhas 588-592) e para `SEED_AUTH_ON_STARTUP=True` (linhas 594-595) — a parte "app PROD não sobe" do done já vale. Ainda faltam: default global de `SEED_AUTH_ON_STARTUP` mudar para `False` (continua `True`), OTP sair do log e ir por e-mail em PROD, e o runbook de usuários seed legados.

### Phase 6 — Worker + beat + migrations (OPS-01..03)

**Fazer:**
- Espelhar Collab: services ECS + container names para worker e beat; mesma imagem da API; command Celery distintos.
- Step de migrate (`alembic upgrade head`) one-off **antes** de estabilizar services.
- Confirmar Redis broker acessível da task worker/beat no cluster.
- Validar que `sincronizar_databricks` agenda (2h) roda no beat deployado.

**Não fazer:**
- Só documentar “suba manual no console AWS” sem automação no pipeline.
- Rodar migrate em toda task de API no startup (preferir one-off no deploy, como Collab).

**Done quando:** pipeline atualiza api+worker+beat e schema está em `head` pós-deploy.

> **Nota 2026-08-12 — fase já fechada, com uma decisão diferente da planejada:** o pipeline já atualiza `ECS_WORKER_SERVICE`, `ECS_ORDERS_WORKER_SERVICE` (terceiro serviço, não previsto aqui) e `ECS_BEAT_SERVICE` com a imagem da API. Migrations, porém, **não** são uma task one-off: o `CMD` do `.docker/Dockerfile` roda `alembic upgrade head && exec uvicorn ...` a cada boot de task de API — exatamente o que a linha "Não fazer" abaixo pedia para evitar. Funciona (advisory lock do Postgres serializa boots concorrentes; `health-check-grace-period-seconds 300` no pipeline tolera a espera), mas se algum dia isso for revisitado, é uma escolha consciente a reavaliar, não um bug.

### Phase 7 — Docker hygiene (OPS-04..05)

**Fazer:**
- Criar `.dockerignore` (e espelho em `.docker/` se o build context exigir).
- Trocar `uv sync --frozen --group dev` → sync de produção (sem group dev); manter group dev só em compose local override se necessário.
- Verificar que pytest/ruff **não** precisam estar na imagem ECR (ficam no job CI).

**Não fazer:**
- Multi-stage complexo sem necessidade — Collab é single-stage enxuto.
- Copiar `.planning/`, `.git`, `.env` para a imagem.

**Done quando:** imagem ECR sobe API/workers e não contém toolchain só-dev.

> **Nota 2026-08-12:** a troca `uv sync --group dev` → sync de produção já foi feita (`ARG INSTALL_DEV=false` por padrão no Dockerfile; o `docker build` do pipeline não passa `--build-arg INSTALL_DEV=true`). Falta só criar o `.dockerignore` — confirmado ainda ausente.

### Phase 8 — Contratos + docs (CONTRACT-01..03)

**Fazer:**
- Backend: garantir contrato estável de `GET /api/v1/parametros` (já existe — documentar shape para o front).
- Front (repo irmão): substituir hardcodes pelos valores da API.
- Corrigir `docs/arquitetura.md` e `docs/api.md`: remover `alertas/` e `history/` **ou** implementar (preferência do milestone: **corrigir docs**).

**Não fazer:**
- Implementar módulos fantasma só para “bater com a doc”.
- Mudar paths da API sem versionar / avisar o front.

**Done quando:** docs refletem módulos reais; front lê parâmetros da API nos fluxos cobertos.

> **Nota 2026-08-12:** o item "corrigir docs" já não é necessário — reverificado direto no conteúdo atual de `docs/arquitetura.md` e `docs/api.md`, nenhum dos dois lista `alertas` ou `history` como módulo fantasma hoje. `/api/v1/alertas` é documentado corretamente como rota real dentro de `pedidos`. Falta verificar CONTRACT-01 (front) e CONTRACT-03 (contrato documentado) — não checados neste ciclo.

### Phase 9 — Observability (OBS-01..03)

**Fazer:**
- Integrar Sentry SDK com `SENTRY_DSN` opcional (sem DSN = no-op).
- Proteger `/metrics` (token shared secret, basic auth, ou rede privada — escolher e documentar).
- Validar `CORS_ORIGINS` em PROD; fail-fast ou erro claro se só localhost em PROD.

**Não fazer:**
- Expor DSN ou metrics token no repo.
- Cardápio de dashboards Grafana neste milestone — só instrumentação mínima.

**Done quando:** erro 500 aparece no Sentry (staging/prod com DSN); `/metrics` anônimo falha; CORS prod não é wildcard localhost.

## 5. Arquivos quentes (leitura obrigatória antes de editar)

| Área | Paths |
|------|-------|
| CI/CD | `.github/workflows/pipeline.yml` |
| Docker | `.docker/Dockerfile`, `.docker/docker-compose*.yml` |
| Settings | `app/shared/config/settings.py`, `app/bootstrap.py` |
| Auth/OTP/seed | `app/modules/auth/**`, `app/modules/comunicacoes/**` |
| Celery | `app/workers/celery_app.py`, `app/workers/tasks/**` |
| Metrics | `app/shared/metrics/router.py`, `app/main.py` |
| Params | `app/modules/parametros/**` |
| Docs | `docs/arquitetura.md`, `docs/api.md` |
| Collab ref | `../collab-backend/.github/workflows/{ci.yml,deploy-ecs.yaml}` |

## 6. Verificação mínima por wave

```text
Wave A: CI vermelho bloqueia; boot PROD falha com JWT_SECRET placeholder; seed default false
Wave B: ecs services worker+beat healthy; alembic head aplicado; beat loga schedule
Wave C: docs sem alertas/history fantasmas; GET parametros usado no front; /metrics 401/403 sem credencial
```

## 7. Handoff humano

Perguntas que **exigem** humano (não inventar):

1. Contas AWS: nomes finais dos services ECS worker/beat + cluster (hoje só `system-automation-backend-dev`).
2. SMTP PROD real (Office365?) já provisionado nos secrets do task role / SSM?
3. DSN Sentry e política de `/metrics` (ALB privado vs token).
4. Origens CORS de produção do front.
5. Autorização para mudar senhas/remover usuários seed em ambientes já populados.

## 8. Critério de “milestone v1.2 done”

Todos os REQs `CI-*`, `SEC-*`, `OPS-*`, `CONTRACT-*`, `OBS-*` checked em REQUIREMENTS.md + verification/UAT das Phases 4–9 + STATE.md apontando milestone complete.

---
*Brief gerado com o scaffolding de planning — 2026-08-07*
