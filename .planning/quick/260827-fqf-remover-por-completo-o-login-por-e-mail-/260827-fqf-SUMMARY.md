---
phase: quick/260827-fqf
plan: 01
subsystem: auth
tags: [auth, sso, migration, security-hardening]
requires: []
provides:
  - "POST /api/auth/sso/microsoft como unico login da aplicacao"
  - "auth_users sem password_hash/user_name/cpf/phone; auth_otp_challenges removida"
affects:
  - app/modules/auth/**
  - app/shared/config/settings.py
  - app/shared/infrastructure/account_lockout.py (removido)
  - app/modules/pedidos/infrastructure/resumo/listar_alertas.py
  - docs/seguranca.md
  - .planning/REQUIREMENTS.md
tech-stack:
  removed: [bcrypt]
  patterns:
    - "emissao direta de token de teste via create_access_token no lugar de POST /sign-in"
key-files:
  created:
    - alembic/versions/035_auth_remove_login_local.py
    - app/tests/test_migration_035_auth_remove_login_local.py
    - app/tests/test_auth_sso_microsoft.py
  modified:
    - app/modules/auth/application/casos_uso.py
    - app/modules/auth/application/schemas.py
    - app/modules/auth/service.py
    - app/modules/auth/domain/exceptions.py
    - app/modules/auth/domain/tokens.py
    - app/modules/auth/infrastructure/security.py
    - app/modules/auth/infrastructure/models.py
    - app/modules/auth/infrastructure/repositorio_usuario.py
    - app/modules/auth/infrastructure/http/routes.py
    - app/modules/auth/__init__.py
    - app/bootstrap.py
    - app/shared/config/settings.py
    - alembic/env.py
    - pyproject.toml
    - uv.lock
    - .env.example
    - .docker/docker-compose.yml
    - .docker/docker-compose.prod.yml
    - .github/workflows/pipeline.yml
    - app/modules/pedidos/infrastructure/resumo/listar_alertas.py
    - docs/seguranca.md
    - docs/api.md
    - docs/index.md
    - docs/arquitetura.md
    - README.md
    - .claude/CLAUDE.md
    - .planning/REQUIREMENTS.md
    - app/tests/conftest.py (por sessao concorrente, ver nota)
    - app/tests/test_auth_flows.py (por sessao concorrente, ver nota)
    - app/tests/test_auth_reuso_refresh_token.py (por sessao concorrente, ver nota)
    - app/tests/test_auth_sessao_revogacao.py (por sessao concorrente, ver nota)
    - app/tests/test_parametros.py
    - app/tests/test_pedidos_routes.py
    - app/tests/test_comunicacoes.py
    - app/tests/test_comunicacoes_delivery_repository.py
    - app/tests/test_request_body_limit.py
    - app/tests/test_schema_guard.py
    - app/tests/test_database_options.py
    - app/tests/test_metrics_guard.py
    - app/tests/test_docs_seguranca.py
  deleted:
    - app/modules/auth/domain/senha.py
    - app/modules/auth/domain/otp_hash.py
    - app/modules/auth/infrastructure/repositorio_otp.py
    - app/modules/auth/infrastructure/otp_email.py
    - app/modules/auth/bootstrap/seed.py
    - app/modules/auth/bootstrap/__init__.py
    - app/shared/infrastructure/account_lockout.py
    - app/tests/test_auth.py
    - app/tests/test_auth_otp_hash.py
    - app/tests/test_auth_otp_email.py
    - app/tests/test_auth_rate_limit.py
    - app/tests/test_migration_030_auth_otp_code_hash.py
    - app/tests/test_settings_seed.py
decisions:
  - "SEC-08 (lockout de conta) removido junto com o login local: sem segredo adivinhavel restante, nao ha o que travar por tentativa"
  - "AuthResponse/AuthUserResponse encolhem (perdem challenge/display_name/cpf/phone) — aprovado pela mantenedora, frontend em reescrita paralela"
  - "Alerta operacional 'acesso' removido de listar_alertas.py — confirmed_at nunca mais fica nulo sem cadastro local"
  - "docs/seguranca.md e REQUIREMENTS.md: mudancas da sessao concorrente (SEC-08, migration 034) ja estavam commitadas antes desta task comecar — editados por cima normalmente"
metrics:
  duration: "~3h (sessao com interrupcao e retomada, mais colisao com execucao concorrente do mesmo plano)"
  completed: 2026-08-28
---

# Phase quick/260827-fqf Plan 01: Remover por completo o login por e-mail e senha Summary

Remoção completa do login local (senha/OTP/cadastro/recuperação) do backend, deixando `POST /api/auth/sso/microsoft` (Microsoft Entra ID) como único caminho de autenticação; schema perde 4 colunas de `auth_users` e a tabela `auth_otp_challenges` inteira (migration 035); SSO ganhou cobertura de teste que não existia.

## O que foi feito

- **Task 1 (baseline):** suíte real 934 passed/18 skipped/0 failed (após corrigir `system_automation_test` que estava atrasado em uma migration — 033 em vez do head 034 da aplicação; isso não é código, só estado de banco, corrigido com `alembic upgrade head` direto no banco de teste). `alembic heads` = 034. `ruff check`: 392 erros pré-existentes (majoritariamente `EXE002` em arquivos não relacionados a auth). `ruff format --check`: 6 arquivos sujos pré-existentes. `pyright`: binário ausente no container (`uv run pyright` falha com "Permission denied"; não há `pyright` instalado nem como pacote Python) — ambiente pré-existente, fora do escopo desta task, não avaliado nesta nem em nenhuma quick task anterior. Nenhuma divergência de inventário encontrada nos greps de reconhecimento.
- **Task 2 (checkpoint, respondido pela mantenedora via handoff, registrado aqui):**
  - **(a) SEC-08/lockout:** a sessão do SEC-08 já tinha terminado e commitado (`059236e`) antes desta task começar. Autorizado remover `account_lockout.py`, as 4 settings `AUTH_LOCKOUT_*` e os testes de lockout — SEC-08 deixou de se aplicar porque não sobra nenhum segredo adivinhável em nenhum endpoint (SSO usa token assinado da Microsoft).
  - **(b) Contrato de `AuthResponse`/`AuthUserResponse` encolhendo:** aprovado — perdem `challenge`, `display_name`, `cpf`, `phone`. O frontend está sendo removido/reescrito em paralelo, fora deste repo, e vai parar de depender desses campos.
  - **(c) Alerta "acesso pendente" removido:** confirmado, executado exatamente como investigado (bloco 4 de `listar_alertas.py`).
  - **(d) `docs/seguranca.md` e `.planning/REQUIREMENTS.md`:** já estavam commitados (parte do commit `52e34a6` e do trabalho SEC-08), editados por cima normalmente na Task 8.
- **Task 3:** removeu o código de produção do login local — 7 arquivos apagados (`domain/senha.py`, `domain/otp_hash.py`, `infrastructure/repositorio_otp.py`, `infrastructure/otp_email.py`, `bootstrap/seed.py`, `bootstrap/__init__.py`, `shared/infrastructure/account_lockout.py`); `casos_uso.py`, `schemas.py`, `service.py`, `exceptions.py`, `tokens.py`, `security.py`, `models.py`, `repositorio_usuario.py`, `http/routes.py` reduzidos ao que resta (SSO + sessão + RBAC + gestão de usuários); `settings.py`/`app/bootstrap.py`/`pyproject.toml`/`.env.example`/docker-compose/pipeline.yml limpos de seed e bcrypt; `listar_alertas.py` perde o bloco de alerta de acesso. 3 commits: `a8cde52`, `dba6112`, `de43349`.
- **Task 4:** migration `035_auth_remove_login_local.py` — backfill de `confirmed_at`, drop de `uq_auth_users_cpf` + 4 colunas de `auth_users`, drop de `auth_otp_challenges` (confirmado via `pg_constraint` que nenhuma FK aponta para ela). `downgrade()` restaura estrutura (não dados). Teste de ciclo destrutivo (`upgrade 034 → upgrade 035 → downgrade 034 → upgrade 035`) passa em banco descartável. Aplicada nos dois bancos reais (`system_automation` e `system_automation_test`); `alembic check` limpo nos dois.
- **Tasks 5 e 6:** cirurgia na suíte de testes de auth (6 arquivos exclusivos apagados, `conftest.py`/`test_auth_flows.py`/`test_auth_sessao_revogacao.py`/`test_auth_reuso_refresh_token.py` adaptados para emitir token direto em vez de `POST /sign-in`) e cobertura nova do SSO (`test_auth_sso_microsoft.py`, 5 testes: 503 não configurado, 401 token inválido, 403 sem e-mail, 200 provisiona com papel mínimo, 200 conta existente preserva papéis e abre rota RBAC).
- **Task 7:** adaptou os testes de outros módulos que só usavam o sign-in por senha para obter token — `test_parametros.py`, `test_pedidos_routes.py` (emissão direta via `create_access_token`), `test_comunicacoes.py`/`test_comunicacoes_delivery_repository.py` (removeram o kwarg `password_hash`), `test_request_body_limit.py` (troca `/sign-in` por `/sso/microsoft` como POST público genérico), `test_schema_guard.py` (remove import de `AuthOtpChallenge`), `test_database_options.py`/`test_metrics_guard.py` (removem `SEED_AUTH_ON_STARTUP`). `test_migration_022_communication_delivery.py` não precisou de mudança — só faz upgrade até a revisão 022, onde `password_hash` ainda existe e é `NOT NULL` (só a 031 liberou nulo e a 035 dropou a coluna).
- **Task 8:** `docs/seguranca.md` reescrito para runbook SSO-only (detecção por `auth_provider = 'local'`, remoção sem mais rotação de senha, prevenção anota SEC-02/03/04/05/07/08 como N/A); `test_docs_seguranca.py` ganha teste negativo contra reintrodução das 5 senhas antigas; `docs/api.md`, `docs/index.md`, `docs/arquitetura.md`, `README.md`, `.claude/CLAUDE.md` atualizados; `.planning/REQUIREMENTS.md` anota SEC-02/03/04/05/07/08 como N/A preservando o histórico (nenhuma linha de requisito apagada). Gate final: `ruff check`/`ruff format --check` não pioraram vs. baseline; `alembic check` limpo; suíte completa 881 passed/18 skipped/0 failed (sem falha nova).

## Colisão com execução concorrente do mesmo plano (achado durante a Task 4)

Durante a Task 4, ao rodar `git status --short` para confirmar o estado antes de commitar, descobri que **outra sessão estava executando este mesmo quick task em paralelo**, no mesmo working tree (sem worktree, `workflow.use_worktrees=false`, exatamente o cenário que o `<context>` do plano já alertava para *outras* tarefas). Essa sessão concorrente:
- Criou e commitou (`5b57d44`) uma migration `035_auth_remove_login_local.py` e um `test_migration_035_auth_remove_login_local.py` **byte-idênticos** aos que eu tinha acabado de escrever de forma independente (confirmado com `git diff HEAD` vazio nos dois arquivos) — bundlado no mesmo commit com a remoção dos 6 arquivos de teste exclusivos de senha/OTP (parte da Task 5).
- Em seguida commitou (`66435e9`) a adaptação de `conftest.py`/`test_auth_flows.py`/`test_auth_reuso_refresh_token.py`/`test_auth_sessao_revogacao.py` e a criação de `test_auth_sso_microsoft.py` — Tasks 5 e 6 completas.

Como o conteúdo era idêntico ou funcionalmente equivalente ao que o plano pedia, e as Tasks 4/5/6 ficaram provadamente corretas (suíte rodada e verde), **não desfiz nem recriei esse trabalho** — segui em frente a partir do estado real do repositório (Tasks 7 e 8), confirmando `git status --short`/`git log` antes de cada commit para não colidir de novo. Isso está registrado aqui porque é uma anomalia de coordenação (duas execuções do mesmo plano ativas ao mesmo tempo) que o orquestrador deveria investigar — não uma decisão de design desta task.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `docs/seguranca.md`: seção "Remoção" corrigida quanto ao bloqueio de FK**
- **Found during:** Task 8
- **Issue:** o plano instruía manter o texto "`parametro_change_requests.requested_by` NOT NULL sem `ON DELETE`, que bloqueia o DELETE" como cenário ainda vigente. Conferido com `\d auth_users` no banco real: a migration 034 (quick task 260825-f21, já commitada antes desta task) já tornou as duas FKs (`requested_by`/`reviewed_by`) `ON DELETE SET NULL` e `requested_by` nullable — nenhuma FK bloqueia mais o DELETE hoje.
- **Fix:** reescrevi a seção "Remoção" para refletir o estado real (nenhuma FK bloqueia, sem necessidade de "rebaixar papel" como alternativa) e documentei a mudança de comportamento desde a 034.
- **Files modified:** docs/seguranca.md
- **Commit:** c88c03e

**2. [Rule 1 - Bug] Cabeçalho de nível da seção de rotação de métricas**
- **Found during:** Task 8
- **Issue:** o plano assumia que a seção "Rotação da chave de métricas" já começava com `## Rotação` (nível 2) e por isso satisfaria o gate de `test_docs_seguranca.py::test_runbook_tem_secoes_obrigatorias`. Na prática o arquivo original tinha `### Rotação da chave de métricas` (nível 3), que não bate com `linha.startswith("## Rotação")`.
- **Fix:** promovi o cabeçalho para nível 2 (`## Rotação da chave de métricas`).
- **Files modified:** docs/seguranca.md
- **Commit:** c88c03e

**3. [Rule 3 - Blocking] Banco de teste atrasado em uma migration antes do baseline**
- **Found during:** Task 1
- **Issue:** `system_automation_test` estava em `033`, mas o banco de aplicação (`system_automation`) já estava em `034` — 2 falhas de baseline ligadas a `parametro_change_requests.requested_by` (mismatch de nullable entre model e banco).
- **Fix:** `alembic upgrade head` direto no banco de teste (nenhum arquivo alterado, nenhum commit). Baseline real: 934 passed/18 skipped/0 failed.
- **Files modified:** nenhum (só estado de banco)
- **Commit:** n/a (não é mudança de código)

## Auth gates

Nenhum.

## Known Stubs

Nenhum.

## Threat Flags

Nenhuma superfície nova fora do `<threat_model>` do plano.

## Pendências / Próximos passos

- Coordenação com o time de frontend: o contrato de `AuthResponse`/`AuthUserResponse` encolheu (sem `challenge`, `display_name`, `cpf`, `phone`). O frontend está sendo removido/reescrito em paralelo (fora deste repo) — confirmar que essa reescrita já não depende desses campos antes de qualquer deploy conjunto.
- `ruff check`/`ruff format --check` continuam com débito pré-existente (378 erros / 5 arquivos, todos fora do escopo desta task, não piorou vs. baseline de 392/6). `pyright` seguiu inavaliável no ambiente (binário ausente no container) — mesma situação da Task 1, não é regressão desta task.
- Investigar com o orquestrador por que duas execuções do mesmo quick task ficaram ativas ao mesmo tempo no mesmo working tree (ver seção "Colisão com execução concorrente" acima).

## Self-Check: PASSED

- `alembic/versions/035_auth_remove_login_local.py`: FOUND
- `app/tests/test_migration_035_auth_remove_login_local.py`: FOUND
- `app/tests/test_auth_sso_microsoft.py`: FOUND
- `app/shared/infrastructure/account_lockout.py`: CONFIRMED REMOVED (não existe mais)
- Commits verificados em `git log --oneline`: `a8cde52`, `dba6112`, `de43349`, `5b57d44`, `66435e9`, `ecb9604`, `c88c03e`, `01ba144` — todos presentes
- `\d auth_users` (ambos os bancos): sem `password_hash`/`user_name`/`cpf`/`phone`; `auth_otp_challenges` inexistente <!-- pragma: allowlist secret -->
- Suíte completa final: 881 passed, 18 skipped, 0 failed
