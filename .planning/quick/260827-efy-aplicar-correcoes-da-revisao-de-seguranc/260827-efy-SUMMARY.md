---
phase: quick/260827-efy
plan: 01
subsystem: api
tags: [pydantic, fastapi, starlette, asgi-middleware, logging, otp, security]

requires: []
provides:
  - "RegisterRequest.user_name/phone bounded (max_length 255/20, espelhando as colunas)"
  - "ParametroCreate/ParametroUpdate.valor/descricao bounded a 4000; ChangeRequestCreate.justification bounded a 2000 e proposed_payload validado a 16 KiB serializado"
  - "unhandled_exception_handler loga logger.exception com traceback antes do 500, sem mudar o contrato de resposta"
  - "MaxBodySizeMiddleware novo (app/shared/http/body_limit.py) rejeita corpo declarado acima de REQUEST_MAX_BODY_BYTES (default 2 MiB) com 413, adicionado antes do CORSMiddleware"
  - "Codigo OTP em claro no log restrito a ENV==DEV; PROD e qualquer ambiente intermediario usam e-mail e nunca logam o codigo"
affects: [auth, parametros, http-edge, observabilidade]

tech-stack:
  added: []
  patterns:
    - "Middleware ASGI puro (nao BaseHTTPMiddleware) para nao bufferizar corpo antes de medir Content-Length"
    - "field_validator + @classmethod no estilo de app/modules/pedidos/application/schemas.py para bound de payload serializado"

key-files:
  created:
    - app/shared/http/body_limit.py
    - app/tests/test_shared_errors_handlers.py
    - app/tests/test_request_body_limit.py
  modified:
    - app/modules/auth/application/schemas.py
    - app/modules/parametros/application/schemas.py
    - app/shared/errors/handlers.py
    - app/main.py
    - app/shared/config/settings.py
    - .env.example
    - app/modules/auth/infrastructure/repositorio_otp.py
    - app/tests/conftest.py
    - app/tests/test_auth_flows.py
    - app/tests/test_parametros.py
    - app/tests/test_auth_otp_email.py
    - docs/seguranca.md

key-decisions:
  - "phone limitado a 20 (nao 32): coluna auth_users.phone e String(20); aceitar 32 no schema so moveria a falha para o INSERT (StringDataRightTruncation -> 500)"
  - "valor/descricao/justification sao tetos de aplicacao deliberados (colunas Text sem limite no banco), nao espelhos de coluna"
  - "MaxBodySizeMiddleware adicionado ANTES do CORSMiddleware para que o 413 saia com cabecalhos de CORS (Starlette.add_middleware faz insert(0,...), o ultimo adicionado fica mais externo)"
  - "Passthrough obrigatorio de scope['type'] != 'http' no MaxBodySizeMiddleware para nao quebrar o WebSocket /api/realtime/ws"
  - "Guarda de ENV em criar_desafio invertida: log em claro so em DEV (nao mais 'tudo que nao for PROD'), fechando a lacuna de um futuro STAGING/TEST vazando o codigo"
  - "T-efy-08 (ambiente intermediario sem SMTP configurado) documentado como lacuna aceita, sem fallback inventado"

requirements-completed: [QUICK-260827-EFY]

duration: ~35min
completed: 2026-08-27
---

# Quick Task 260827-efy: Correções da revisão de segurança do backend Summary

**5 correções de segurança aplicadas: bounds de tamanho em RegisterRequest/ParametroCreate-Update/ChangeRequestCreate, log de traceback no handler 500, MaxBodySizeMiddleware novo (413 antes do CORS, passthrough de WebSocket), e política de log do OTP em claro restrita a ENV=DEV.**

## Performance

- **Duration:** ~35min
- **Tasks:** 4/4 completas (incluindo o gate de qualidade da Task 4)
- **Files modified:** 12 arquivos de produção/config/docs + 2 arquivos de teste novos + 3 ajustados

## Accomplishments

- `RegisterRequest.user_name` (max_length=255) e `.phone` (max_length=20) ganham bound espelhando as colunas `auth_users`, evitando 422 chegar tarde como 500 por truncamento no asyncpg.
- `ParametroCreate/ParametroUpdate.valor/descricao` (4000) e `ChangeRequestCreate.justification` (2000) ganham teto de aplicação; `proposed_payload` ganha `field_validator` que recusa acima de 16 KiB serializado em UTF-8.
- `unhandled_exception_handler` agora grava `logger.exception` com método, path e traceback completo antes do 500 — hoje uma falha 500 não deixava rastro nenhum no log.
- `MaxBodySizeMiddleware` novo (ASGI puro, sem `BaseHTTPMiddleware`) responde 413 pelo `Content-Length` declarado, sem executar a rota; adicionado **antes** do `CORSMiddleware` para que o 413 saia com cabeçalhos de CORS; passthrough obrigatório para `scope["type"] != "http"` mantém o WebSocket `/api/realtime/ws` intacto.
- `criar_desafio` inverte a guarda: código OTP em claro só é logado em `ENV == "DEV"`; `PROD` e qualquer ambiente intermediário (STAGING/TEST/desconhecido) entregam por e-mail com degradação graciosa (`logger.exception` na falha) e nunca logam o código.

## Task Commits

Todos os commits abaixo pertencem exclusivamente a esta tarefa (260827-efy). Outros commits que aparecem intercalados no `git log` — `bc9818b`, `5fb97d5`, `9a29d0b`, `2f0d034` — pertencem a uma tarefa concorrente não relacionada (260827-emo, guard de `GET /metrics`), executada em paralelo no mesmo checkout (não é worktree); não foram tocados nem incluídos em nenhum commit desta tarefa.

1. **Task 1a: Bounds em RegisterRequest** - `6fb5015` (fix)
2. **Task 1b: Bounds em ParametroCreate/Update/ChangeRequestCreate** - `fe82f27` (fix)
3. **Task 2a: Log da exceção não tratada** - `7ae6c66` (fix)
4. **Task 2b: MaxBodySizeMiddleware + REQUEST_MAX_BODY_BYTES** - `600f7e2` (feat)
5. **Task 3a: Log do OTP restrito a DEV** - `c583d35` (fix)
6. **Task 3e: docs/seguranca.md atualizado** - `b2ac258` (docs)
7. **Task 4 (gate): ruff format em handlers.py/test_shared_errors_handlers.py** - `b2c8df8` (style, ver Deviations)

## Files Created/Modified

- `app/shared/http/body_limit.py` - `MaxBodySizeMiddleware` novo, ASGI puro
- `app/tests/test_request_body_limit.py` - 413, passthrough abaixo do teto, GET sem corpo, passthrough de scope não-http, fiação real com CORS
- `app/tests/test_shared_errors_handlers.py` - Prova de log com traceback e contrato de resposta inalterado
- `app/modules/auth/application/schemas.py` - `RegisterRequest.user_name`/`.phone` bounded
- `app/modules/parametros/application/schemas.py` - `ParametroCreate/Update`/`ChangeRequestCreate` bounded + validator de payload
- `app/shared/errors/handlers.py` - `logger.exception` antes do 500
- `app/main.py` - `MaxBodySizeMiddleware` adicionado antes do `CORSMiddleware`
- `app/shared/config/settings.py` - `request_max_body_bytes` (default 2 MiB)
- `.env.example` - `REQUEST_MAX_BODY_BYTES` documentado
- `app/modules/auth/infrastructure/repositorio_otp.py` - guarda de `ENV` invertida em `criar_desafio`
- `app/tests/conftest.py` - `ENV=dev` travado como default da suíte
- `app/tests/test_auth_flows.py` - 2 testes novos (`user_name`/`phone` acima do bound → 422)
- `app/tests/test_parametros.py` - 6 testes de schema puro cobrindo os novos bounds
- `app/tests/test_auth_otp_email.py` - 1 teste renomeado + 2 testes novos (STAGING)
- `docs/seguranca.md` - política de entrega de OTP por ambiente atualizada

## Decisions Made

Ver `key-decisions` no frontmatter. Nenhuma decisão fora do que o plano já havia congelado (inclusive a divergência deliberada `phone` max_length=20, não 32).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `SIM117`/formatação divergente em `handlers.py`/`test_shared_errors_handlers.py`**
- **Found during:** Task 4 (gate de qualidade — `ruff check`/`ruff format --check`)
- **Issue:** `with` aninhado (`SIM117`) no teste novo, e formatação divergente (linha longa) em `handlers.py` e no teste
- **Fix:** `with` combinado num único statement; `ruff format` aplicado nos dois arquivos
- **Files modified:** `app/shared/errors/handlers.py`, `app/tests/test_shared_errors_handlers.py`
- **Verification:** `ruff check`/`ruff format --check` limpos; `pytest` do arquivo continua verde
- **Committed in:** `b2c8df8` — commit separado em vez de emenda no commit original (`7ae6c66`), porque emendar um commit que não é o HEAD exigiria rebase interativo, proibido pelas regras de execução

---

**Total deviations:** 1 auto-fixado (1 blocking/lint). Nenhuma mudança de comportamento — só estilo.
**Impact on plan:** Nenhum. O plano previa exatamente esse tipo de correção no gate da Task 4 e permitia emendar; a emenda não foi possível pela restrição de não fazer rebase interativo em commit não-HEAD, resolvido com um commit `style` dedicado.

## Issues Encountered

**Trabalho concorrente no mesmo checkout (não isolado por worktree):** durante a execução, o mesmo diretório de trabalho continha, em vários pontos, alterações não commitadas de uma tarefa paralela e não relacionada (260827-emo — guard de `GET /metrics` por `X-Metrics-Key`), incluindo commits intercalados no `git log` (`bc9818b`, `5fb97d5`, `9a29d0b`, `2f0d034`). Cada commit desta tarefa foi feito com `git add <arquivo>` explícito (nunca `-A`/`.`), e antes de cada commit o `git status --short` foi conferido para garantir que nenhum arquivo daquela tarefa fosse incluído. Nenhum `git stash`/`git clean`/reset destrutivo foi usado. Ver `.planning/quick/260827-efy-aplicar-correcoes-da-revisao-de-seguranc/deferred-items.md` para o detalhe.

**Pré-existente, fora de escopo, não corrigido (ver `deferred-items.md` para detalhes completos):**
1. `test_auth_flows.py::test_delete_user_preserva_change_request_zerando_requested_by` — `ForeignKeyViolationError`, confirmado pré-existente via `git stash` + rerun antes de qualquer mudança deste plano.
2. `test_schema_guard.py::test_colunas_do_model_existem_no_banco[parametro_change_requests]` — nullability divergente (`requested_by`). Mesma causa raiz do item 1: `system_automation_test` não recebeu `alembic upgrade head` para a migration 034 (quick task 260825-f21).
3. `ruff format --check` reporta 2 arquivos pré-existentes não tocados por este plano: `alembic/versions/034_parametro_change_requests_fk_set_null.py` e duas linhas de `test_auth_flows.py` (781/793, fora da seção editada por este plano).
4. `app/tests/test_pedidos_processing_sem_adequar_memory_024.py` falha na coleta nativamente no Windows (`ModuleNotFoundError: No module named 'resource'` — módulo Unix-only). Ambiente de execução desta sessão é Windows nativo, não Docker (o padrão documentado do projeto). Excluído via `--ignore` na suíte completa da Task 4; não é um defeito de código.

**Achado fora do escopo, registrado no plano, não implementado aqui (conforme instruído):** `SetUserRolesRequest.roles: list[str] = Field(min_length=1)` em `app/modules/auth/application/schemas.py:143` não tem `max_length` na lista nem bound por item. Vale um follow-up (`max_length` + `Literal`/enum dos 5 papéis válidos).

**T-efy-08 — lacuna de SMTP em ambiente intermediário (STANDBY, documentada, sem comportamento inventado):** num ambiente tipo `STAGING` sem `SMTP_HOST`/`SMTP_USER`/`SMTP_PASSWORD` configurados, `SmtpEmailSender.enviar` levanta `FalhaTerminalEntrega("smtp_not_configured")`, cai no `except FalhaEntregaEmail` de `criar_desafio`, é logado sem o código, e o usuário fica sem caminho de obter o OTP. O código atual **não define fallback** — nenhuma comportamento foi inventado. Decisão pendente para quando um ambiente intermediário for provisionado de verdade: exigir SMTP no boot (fail-fast em `settings.py`, no mesmo padrão do fail-fast de PROD) **ou** responder 503 no endpoint de recovery/register quando a entrega falhar por falta de configuração. Comentário equivalente deixado em `app/modules/auth/infrastructure/repositorio_otp.py`.

## User Setup Required

None - nenhuma configuração de serviço externo necessária. `REQUEST_MAX_BODY_BYTES` tem default funcional (2 MiB) e é opcional no `.env`.

## Verificação (números da suíte)

- **Baseline (STATE.md, antes desta tarefa):** 886 passed / 18 skipped / 0 failed.
- **Depois desta tarefa** (suíte completa, `--ignore` do módulo Unix-only citado acima; inclui também os testes da tarefa concorrente 260827-emo que compartilhou o branch): **916 passed / 18 skipped / 2 failed**. Os 2 failed são pré-existentes e não relacionados a este plano (migration 034 pendente no banco de teste — ver Issues Encountered e `deferred-items.md`).
- `uv run ruff check app alembic` — limpo.
- `uv run ruff format --check app alembic` — limpo, exceto 2 arquivos pré-existentes fora do escopo desta tarefa (ver Issues Encountered item 3).
- `uv run pyright app alembic` — `0 errors, 0 warnings, 0 informations`.
- Grep de não-regressão: `ENV == "DEV"` é a única comparação de `ENV` em `repositorio_otp.py`; nenhum `logger.info` com `code`/`codigo` fora do ramo DEV.
- Grep de fiação: em `app/main.py`, `MaxBodySizeMiddleware` é adicionado antes de `CORSMiddleware`.
- `git log --oneline`: nenhum commit desta tarefa contém `Co-Authored-By` ou menção a Claude.

## Next Phase Readiness

Nenhum bloqueio para o próximo trabalho. Duas dívidas conhecidas ficam registradas para follow-up futuro (não bloqueantes):
- T-efy-08: decisão pendente sobre fail-fast de SMTP vs 503 em ambiente intermediário.
- `SetUserRolesRequest.roles` sem bound de tamanho/item (achado fora do escopo desta revisão).

## Self-Check: PASSED

Todos os 15 arquivos de código/docs citados neste SUMMARY e os 2 arquivos de metadados da
tarefa foram confirmados em disco (`FOUND`); os 7 commit hashes desta tarefa foram confirmados
em `git log --oneline --all` (`FOUND`). Nenhum item ausente.

---
*Phase: quick/260827-efy*
*Completed: 2026-08-27*
