---
phase: quick/260827-emo
plan: 01
subsystem: api
tags: [fastapi, prometheus, api-key, hmac, settings, security]

requires: []
provides:
  - "Guard require_metrics_key (app/shared/metrics/security.py) fechando GET /metrics atrás de X-Metrics-Key"
  - "Campo Settings.metrics_api_key com fail-fast de boot em PROD (>= 32 caracteres)"
  - "Documentação de rotação da chave de métricas em docs/seguranca.md"
affects: [metrics, observability, prometheus-scraper-config]

tech-stack:
  added: []
  patterns:
    - "Guard de API key fixa em header (não RBAC) para consumidores sem JWT — fastapi.security.APIKeyHeader + hmac.compare_digest sobre bytes"
    - "Dependência declarada no construtor do APIRouter (dependencies=[Depends(...)]) para proteger por construção qualquer rota futura do router"

key-files:
  created:
    - app/shared/metrics/security.py
    - app/tests/test_metrics_guard.py
  modified:
    - app/shared/config/settings.py
    - app/shared/metrics/router.py
    - app/tests/test_database_options.py
    - .env.example
    - docs/api.md
    - docs/seguranca.md

key-decisions:
  - "Fail-closed: METRICS_API_KEY vazia -> 503 (nunca 200), seguindo o precedente de MICROSOFT_TENANT_ID/CLIENT_ID vazios"
  - "401 sem header, 403 com header presente mas inválido (distinção literal do pedido)"
  - "Guard mora em app/shared/metrics/, não em app/shared/security/ (aquele pacote é só para o RBAC de auth)"
  - "Sem rate limit nesta tarefa (dívida aceita e documentada em docs/seguranca.md)"

requirements-completed: [QUICK-260827-emo]

duration: ~20min
completed: 2026-08-27
---

# Quick Task 260827-emo: Proteger GET /metrics com guard de API key Summary

**`GET /metrics` deixou de ser público: guard `require_metrics_key` por header `X-Metrics-Key` comparado com `hmac.compare_digest`, fail-closed (503 sem chave) e fail-fast de boot em PROD (Settings exige `METRICS_API_KEY` >= 32 caracteres).**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-08-27T10:38:00-03:00 (aprox.)
- **Completed:** 2026-08-27T10:53:00-03:00
- **Tasks:** 3 planejadas + 1 deviation commit
- **Files modified:** 8 (2 criados, 6 modificados)

## Accomplishments
- `app/shared/metrics/security.py` criado com `require_metrics_key`: 503 (chave não configurada) → 401 (sem header) → 403 (header errado) → 200 (header correto), nessa ordem, com `hmac.compare_digest` sobre bytes
- `Settings.metrics_api_key` adicionado com default vazio (não quebra dev) e fail-fast de PROD (`ValueError` se < 32 caracteres), posicionado depois da checagem de `seed_auth_on_startup` para não alterar a ordem de falha que `test_settings_seed.py` já afirma
- `app/shared/metrics/router.py` fechado por construção: `APIRouter(dependencies=[Depends(require_metrics_key)])`
- 13 testes novos em `app/tests/test_metrics_guard.py` cobrindo Settings (default/env/PROD em 3 cenários) e o endpoint HTTP (401/403/200/503/gate de `compare_digest`/wiring no router/não-regressão de `/` e `/health`)
- `.env.example`, `docs/api.md` e `docs/seguranca.md` atualizados; `/metrics` não é mais descrito como público

## Task Commits

Each task was committed atomically:

1. **Task 1: METRICS_API_KEY no Settings + fail-fast de PROD** - `5fb97d5` (feat)
2. **Task 2: guard require_metrics_key e fechamento do router de metrics** - `bc9818b` (feat)
3. **Task 3: documentar a chave (.env.example, api.md, seguranca.md)** - `9a29d0b` (docs)

**Deviation fix:** `2f0d034` (fix) — ver "Deviations from Plan" abaixo.

_Nota: os testes foram escritos junto com o código de cada task (TDD), não em commits separados de RED/GREEN — behavior e implementação chegaram no mesmo commit por task, seguindo o padrão dos demais commits `feat` deste repositório._

## Files Created/Modified
- `app/shared/metrics/security.py` - `require_metrics_key`, `METRICS_API_KEY_HEADER`, `metrics_key_scheme`
- `app/shared/metrics/router.py` - guard declarado no construtor do `APIRouter`
- `app/shared/config/settings.py` - campo `metrics_api_key` + checagem de PROD em `validate_cross_field_constraints`
- `app/tests/test_metrics_guard.py` - 13 testes (Settings + HTTP)
- `app/tests/test_database_options.py` - `METRICS_API_KEY` adicionado aos overrides de `test_settings_prod_aceita_somente_configuracao_remota_explicita`
- `.env.example` - seção `METRICS_API_KEY` documentada
- `docs/api.md` - linha de `/metrics` deixa de dizer "público"
- `docs/seguranca.md` - subseção "Rotação da chave de métricas" dentro de `## Rotação`

## Decisions Made
Nenhuma decisão nova além das já travadas no PLAN (D-01..D-09) — plano executado seguindo as decisões registradas na frontmatter/`<decisoes_travadas>`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `test_settings_prod_aceita_somente_configuracao_remota_explicita` quebrou por causa do novo fail-fast**
- **Found during:** Verificação final (suíte completa, depois da Task 3)
- **Issue:** `app/tests/test_database_options.py::test_settings_prod_aceita_somente_configuracao_remota_explicita` monta um `Settings` completo de PROD via subprocesso, com credenciais remotas explícitas para todos os outros campos — mas não setava `METRICS_API_KEY`. O fail-fast introduzido na Task 1 (commit `5fb97d5`) passou a rejeitar esse cenário, quebrando um teste que antes passava.
- **Fix:** Adicionado `METRICS_API_KEY="prod-metrics-api-key-with-thirty-two-chars"` aos overrides do teste, sem alterar o que ele verifica.
- **Files modified:** `app/tests/test_database_options.py`
- **Verification:** `uv run pytest app/tests/test_database_options.py -q` → 5 passed
- **Committed in:** `2f0d034`

---

**Total deviations:** 1 auto-fixed (1 bug, Rule 1)
**Impact on plan:** Correção estritamente decorrente da própria mudança da Task 1; sem escopo adicional.

## Issues Encountered

**Working tree compartilhado.** Durante a execução, outra sessão fez commits concorrentes em `develop` (`600f7e2`, `c583d35`, `2f0d034`-adjacentes de outra tarefa, `b2ac258`, `b2c8df8` — incluindo edições em `docs/seguranca.md` e `app/main.py`). Nenhum desses commits é desta tarefa; confirmado via `git show --stat` em cada commit próprio (`5fb97d5`, `bc9818b`, `9a29d0b`, `2f0d034`) que só os arquivos do plano/deviation foram tocados. `app/main.py`, `app/modules/health/routes.py` e `app/modules/__init__.py` permanecem intocados por esta tarefa (D-06 preservado).

**3 falhas pré-existentes na suíte completa, não relacionadas a esta tarefa** (mencionadas no próprio PLAN como risco conhecido — "se a suíte completa falhar por schema, os gates por arquivo das Tasks 1–3 são o que vale"):
- `app/tests/test_auth_flows.py::test_delete_user_preserva_change_request_zerando_requested_by`
- `app/tests/test_schema_guard.py::test_colunas_do_model_existem_no_banco[parametro_change_requests]` — nullability divergente de `parametro_change_requests.requested_by` (migration da Fase 11/Victoria ainda não aplicada no banco de teste local)
- `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` — falha intermitente já documentada na memória do projeto (poluição de usuário não confirmado entre arquivos de teste)

Nenhuma dessas falhas menciona `metrics`, `X-Metrics-Key` ou `Settings.metrics_api_key`; confirmado que são causadas por trabalho concorrente de outra frente (Phase 11, v1.2) e por uma falha intermitente conhecida — não corrigidas aqui, por estarem fora do escopo desta tarefa (Rule de scope boundary).

## User Setup Required

**External services require manual configuration.** Cada ambiente (incluindo PROD) precisa gerar um valor para `METRICS_API_KEY` com `openssl rand -hex 32` e configurá-lo no `.env`/secret manager **e** no scraper Prometheus (header `X-Metrics-Key`). Sem isso:
- Ambientes não-PROD: `GET /metrics` responde 503 (scrape para de funcionar, mas o boot não quebra).
- PROD: o boot da API falha (`ValidationError`) até `METRICS_API_KEY` existir com >= 32 caracteres.

Ver seção `user_setup` do PLAN e a nova subseção "Rotação da chave de métricas" em `docs/seguranca.md`.

## Next Phase Readiness
- Nenhum bloqueio para outras frentes. O endpoint `/metrics` agora exige configuração explícita em todo ambiente que o utilize (incluindo o scraper Prometheus já em produção, se houver — precisa do header antes do próximo deploy).
- Suíte completa: 916 passed / 18 skipped / 3 failed (as 3 falhas são pré-existentes e não desta tarefa — baseline anterior era 886 passed/18 skipped/0 failed, mas divergiu por trabalho concorrente de outra sessão durante esta execução, não por esta tarefa).

---
*Phase: quick/260827-emo*
*Completed: 2026-08-27*

## Self-Check: PASSED

All created/modified files found on disk; all 4 task/deviation commits (`5fb97d5`, `bc9818b`, `9a29d0b`, `2f0d034`) found in `git log`.
