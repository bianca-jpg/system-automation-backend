---
phase: 05-auth-otp-seed-jwt-fail-fast-security
plan: 05
subsystem: auth
tags: [rate-limit, redis, sign-in, otp, sec-07]

# Dependency graph
requires:
  - phase: 05-04
    provides: "Sign-out session revocation (SEC-06) — unrelated surface, same auth module and Redis client"
provides:
  - "app/modules/auth/infrastructure/http/routes.py::_aplicar_rate_limit — gate usuário+IP reutilizando enforce_dual_fixed_window (mesmo mecanismo já em produção via comunicacoes)"
  - "Rate limit ligado em sign-in (fail-open), register/password-recovery (fail-closed, namespace auth_otp_request) e register-confirm/password-recovery-confirm (fail-closed, namespace auth_otp_confirm)"
  - "app/tests/conftest.py::_FakeAuthRateRedis, ::auth_rate_limit_redis — fixture autouse que isola contadores de rate limit de auth por teste"
  - "app/tests/test_auth_rate_limit.py — 8 testes provando 429/503/fail-open/opacidade de chaves"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Rate limit assimétrico por criticidade de disponibilidade: sign-in fail-open (Redis fora do ar não pode virar apagão de login), os quatro endpoints de OTP fail-closed (503 + Retry-After: 30) — decisão travada 3 do RESEARCH.md, espelhando a postura já usada em SEC-06 para leitura vs escrita do corte de sign-out"
    - "Gate de rate limit roda como primeira instrução do handler, antes de qualquer chamada a service.*, para que uma tentativa com credencial errada também conte contra o teto (prova: test_sign_in_conta_tentativa_mesmo_com_senha_errada)"

key-files:
  created:
    - app/tests/test_auth_rate_limit.py
  modified:
    - app/modules/auth/infrastructure/http/routes.py
    - app/tests/conftest.py
    - .planning/REQUIREMENTS.md

key-decisions:
  - "Limites por endpoint seguem exatamente a tabela do RESEARCH.md: sign-in 5/20 por min; confirmação de OTP 5/20 (1e6 combinações, TTL 15min); pedido de OTP 3/10 (superfície de custo/flood de e-mail, não de adivinhação)"
  - "O fake de teste (_FakeAuthRateRedis) monkeypatcha get_redis do módulo de ROTAS de auth, não o de casos_uso — a revogação de sessão (SEC-06) continua falando com o Redis real de teste (banco 15), são dois pontos de uso distintos"
  - "REQUIREMENTS.md: corrigido também o drift pré-existente de SEC-02/SEC-04 (marcados 'Partial'/'Pending' na tabela apesar de já fechados desde o plano 05-01, conforme 05-01-SUMMARY.md e o código atual de settings.py) — fora do escopo direto deste plano (SEC-07), mas necessário para que a tabela reflita corretamente o fechamento da Fase 5"

patterns-established:
  - "Terceiro uso do padrão enforce_dual_fixed_window neste repositório (comunicacoes -> SEC-07 auth): reforça que rate limit usuário+IP é o mecanismo canônico do projeto, não algo a reinventar por módulo"

requirements-completed: [SEC-07]

# Metrics
duration: ~2h
completed: 2026-08-13
---

# Phase 5 Plan 05: Rate limit de auth (SEC-07) Summary

**Rate limit usuário+IP (`enforce_dual_fixed_window`, o mesmo mecanismo já em produção via `comunicacoes`) ligado nos 5 endpoints de auth que aceitam segredo adivinhável, com política assimétrica: sign-in fail-open, os quatro endpoints de OTP fail-closed — força bruta de senha e de código OTP deixam de ser gratuitas.**

## Performance

- **Duration:** ~2h (inclui um desvio de infraestrutura de teste não planejado — ver Issues Encountered)
- **Started:** 2026-08-13T11:10:00-03:00 (approx.)
- **Completed:** 2026-08-13T14:10:00-03:00 (approx.)
- **Tasks:** 3/3 completed
- **Files modified:** 3 (1 created, 2 modified) + REQUIREMENTS.md

## Accomplishments
- `_FakeAuthRateRedis` + fixture autouse `auth_rate_limit_redis` isolam os contadores de rate limit de auth por teste, monkeypatchando `get_redis` só no módulo de rotas (SEC-06 continua com Redis real de teste) — sem essa fixture, as 15 chamadas de sign-in e 12 de register/recovery já existentes em `test_auth_flows.py` estourariam o teto de IP contra o Redis real do banco 15
- Helper `_aplicar_rate_limit` + 5 constantes de limite ligados nas rotas `sign-in`, `register`, `register/confirm`, `password/recovery`, `password/recovery/confirm`, sempre como primeira instrução do handler
- Política de indisponibilidade implementada exatamente como travado no RESEARCH.md (decisão 3): `sign-in` fail-open (loga aviso, deixa passar); os quatro endpoints de OTP fail-closed (503 + `Retry-After: 30`)
- 8 novos testes em `test_auth_rate_limit.py`: 429 com `Retry-After` exato do fake (37) em sign-in, confirmação de OTP e pedido de OTP; prova de que uma tentativa de senha ERRADA também conta contra o teto de sign-in (o gate roda antes da checagem de credencial); 503 fail-closed nos dois endpoints de confirmação/pedido de OTP; 200 fail-open em sign-in com aviso no log e sem vazamento de identidade; chaves de rate limit provadas opacas (HMAC, sem e-mail em texto claro)
- Log de indisponibilidade nunca inclui identidade, IP, senha ou token — só `namespace` e o nome da causa (mesma disciplina de `comunicacoes`)
- Suíte completa verde e estável em duas execuções consecutivas (749 passed, 16 skipped, mesmos 10 falhas pré-existentes/limitação de ambiente documentadas abaixo)

## Task Commits

Each task was committed atomically:

1. **Task 1: Isolar o rate limit de auth na suíte antes de ligá-lo** - `84669b9` (test)
2. **Task 2: Ligar o rate limit nos 5 endpoints de auth** - `25f6e1f` (feat)
3. **Task 3: Provar 429, fail-closed e fail-open** - `00b717e` (test)

**Plan metadata:** committed alongside this summary (see below)

## Files Created/Modified
- `app/tests/conftest.py` - `_FakeAuthRateRedis` (espelha `_FakeRateRedis` de `test_comunicacoes.py`) + fixture autouse `auth_rate_limit_redis`
- `app/modules/auth/infrastructure/http/routes.py` - `_aplicar_rate_limit` + 7 constantes de módulo; wiring nos 5 endpoints; `responses={429, 503}` documentados nos decorators (sign-in só documenta 429, por ser fail-open)
- `app/tests/test_auth_rate_limit.py` - 8 testes cobrindo 429 (3 endpoints), 503 fail-closed (2 endpoints), 200 fail-open (sign-in) e opacidade de chaves
- `.planning/REQUIREMENTS.md` - SEC-07 marcado Done; corrigido drift pré-existente de SEC-02/SEC-04 (ver Decisions Made)

## Decisions Made
- Limites por endpoint conforme tabela do RESEARCH.md (sign-in 5/20; confirmação OTP 5/20; pedido OTP 3/10) — sem desvio
- Fake de teste monkeypatcha `get_redis` só no módulo de rotas de auth, preservando o Redis real de teste para a revogação de sessão (SEC-06)
- Corrigido o drift de status de SEC-02/SEC-04 em `REQUIREMENTS.md` (detalhe em Deviations)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug/Doc drift] REQUIREMENTS.md continha status incorreto para SEC-02 e SEC-04**
- **Found during:** Escrita do SUMMARY (passo final, ao marcar SEC-07)
- **Issue:** A tabela de rastreabilidade e as linhas de checklist de `REQUIREMENTS.md` marcavam SEC-02 como "Partial" (alegando que o default global de `SEED_AUTH_ON_STARTUP` continuava `True`) e SEC-04 como "Pending" — ambas afirmações contradizem o código atual (`settings.py:434-436` já tem `default=False`) e o próprio `05-01-SUMMARY.md`, que registra `requirements-completed: [SEC-02, SEC-04]` desde 2026-08-12
- **Fix:** Corrigidas as duas linhas de checklist, a tabela de rastreabilidade e a contagem de cobertura (`14 Done, 1 Partial, 6 Pending`), com nota explícita de que a fase 5 fecha 100% (SEC-01..07 todos Done)
- **Files modified:** `.planning/REQUIREMENTS.md`
- **Verification:** Conferido diretamente contra `app/shared/config/settings.py:434-436` (default já `False`) e `docs/seguranca.md`/`test_docs_seguranca.py` (runbook existente e testado)
- **Committed in:** commit de metadados do plano (junto com este SUMMARY)

---

**Total deviations:** 1 auto-fixed (Rule 1, documentação)
**Impact on plan:** Nenhum impacto em código; apenas corrige a rastreabilidade de requisitos para refletir o estado real, necessário para o fechamento de fase que o orquestrador vai verificar em seguida.

## Issues Encountered

- **Container Docker compartilhado apontava para o repositório principal, não para este worktree:** o stack `docker compose` já estava rodando (criado por uma sessão anterior) com os bind mounts (`../app`, `../alembic`) resolvidos contra o **repositório principal** (`backend/app`), não contra este worktree. Como uma sessão concorrente está adicionando uma coluna `auth_provider` em `AuthUser` no repositório principal (trabalho não relacionado a este plano), rodar `docker compose exec api uv run pytest` contra o container compartilhado produzia `UndefinedColumnError: auth_users.auth_provider does not exist` — um erro que nada tem a ver com este plano, mas que apareceria como se fosse. Diagnosticado via `docker inspect --format '{{ .Mounts }}'`, confirmando que o container aponta para o path do repo principal.
  - **Resolução:** montado um stack Docker totalmente isolado para este worktree (rede própria `wt-a0ee058e-net`, Postgres e Redis dedicados, imagem construída a partir do `Dockerfile` deste worktree via `docker build`, sem bind mounts sobre `app/`/`alembic/` — o código testado é exatamente o `COPY` feito no build a partir deste worktree). Banco de teste `system_automation_test` criado e migrado do zero (`alembic upgrade head`, revisão 030) dentro desse stack isolado.
  - **Efeito colateral positivo:** com um banco de teste fresco (sem os dados acumulados de sessões anteriores), `test_parametros.py::test_listar_parametros_contem_criado` — documentado em `deferred-items.md` como falha pré-existente por acúmulo de linhas — passou normalmente, confirmando que a causa raiz descrita ali (linhas acumuladas no banco local compartilhado) está correta.
  - **Limpeza:** os recursos Docker ad-hoc (`wt-a0ee058e-db`, `wt-a0ee058e-redis`, `wt-a0ee058e-net`, imagem `wt-a0ee058e-api`) foram removidos ao final da execução; nenhum deles é referenciado por commits ou arquivos versionados.
- **`ruff check .` sem argumentos reporta ~408 erros que não são reais:** o `pyproject.toml` não tem seção `[tool.ruff]`; o pipeline real (arquivo de workflow do GitHub Actions, linha 115) roda `ruff check --select E4,E7,E9,F app alembic`. Rodar `ruff check .` puro (sem `--select`) ativa o catálogo completo de regras (incluindo `EXE002`, que dispara em praticamente todo arquivo do repo por causa de bits de execução preservados pelo bind mount do Docker Desktop no Windows) e reporta um `F401` genuíno mas pré-existente em `app/modules/pedidos/infrastructure/repositorio_resumo.py` (módulo fora de escopo, em edição concorrente). Rodando com o `--select` exato do pipeline, `app/modules/auth` + `app/tests/conftest.py` + `app/tests/test_auth_rate_limit.py` passam limpos (`All checks passed!`).
- **Dois falhas pré-existentes, fora de escopo, confirmadas novamente** (ver `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/deferred-items.md`): `test_pedidos_read_projection.py` (event loop teardown, módulo `pedidos` em edição concorrente) e as 3 falhas de `test_celery_routing.py`/3 de `test_docs_seguranca.py`/1 de `test_pipeline_safety.py` que dependem de `.github/` e `docs/` não copiados para dentro da imagem Docker (limitação de ambiente documentada no prompt de execução, não relacionada a código).

## User Setup Required

None - no external service configuration required. Reutiliza o mesmo Redis já configurado para a aplicação (`enforce_dual_fixed_window`, já usado por `comunicacoes`); nenhuma variável de ambiente nova.

## Next Phase Readiness

- **SEC-07 fechado**: os 5 endpoints de auth que aceitam segredo adivinhável (sign-in, register, register/confirm, password/recovery, password/recovery/confirm) estão sob rate limit usuário+IP, com política de indisponibilidade correta por endpoint
- **Fase 5 (Auth/OTP/seed/JWT) 100% completa**: todos os 7 requisitos SEC-01 a SEC-07 estão fechados (ver correção de rastreabilidade em `.planning/REQUIREMENTS.md`, incluída neste plano)
- Suíte completa verde e estável em duas execuções consecutivas, contra um banco de teste isolado e recém-migrado (revisão Alembic 030)
- Nenhum arquivo de `app/modules/pedidos/` ou `app/modules/parametros/` foi tocado, conforme restrição da execução
- Pronto para a verificação de fase (`/gsd-verify-work` ou equivalente) que o orquestrador executa a seguir

---
*Phase: 05-auth-otp-seed-jwt-fail-fast-security*
*Completed: 2026-08-13*

## Self-Check: PASSED

- FOUND: app/tests/test_auth_rate_limit.py
- FOUND: app/tests/conftest.py
- FOUND: app/modules/auth/infrastructure/http/routes.py
- FOUND: .planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-05-SUMMARY.md
- FOUND: .planning/REQUIREMENTS.md
- FOUND commit: 84669b9 (Task 1)
- FOUND commit: 25f6e1f (Task 2)
- FOUND commit: 00b717e (Task 3)
