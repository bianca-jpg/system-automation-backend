---
phase: 05-auth-otp-seed-jwt-fail-fast-security
plan: 01
subsystem: auth
tags: [pydantic-settings, fail-fast, seed-users, runbook, drift-guard]

# Dependency graph
requires: []
provides:
  - "SEED_AUTH_ON_STARTUP default False (SEC-02) — nenhum ambiente cria contas seed sem opt-in explícito"
  - "docs/seguranca.md — runbook executável de detecção/rotação/remoção das 5 contas seed (SEC-04)"
  - "app/tests/test_settings_seed.py — prova do default e do fail-fast PROD pré-existente"
  - "app/tests/test_docs_seguranca.py — guard de drift entre DEFAULT_AUTH_USERS e o runbook"
affects: [05-02, 05-03, 05-04, 05-05, auth, docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Testes de Settings constroem Settings(_env_file=None) diretamente, nunca get_settings() (evita o @lru_cache)"
    - "Guard de drift entre código-fonte e documentação: teste importa a tupla de dados real (DEFAULT_AUTH_USERS) e assere presença literal no markdown"

key-files:
  created:
    - app/tests/test_settings_seed.py
    - app/tests/test_docs_seguranca.py
    - docs/seguranca.md
  modified:
    - app/shared/config/settings.py
    - .env.example
    - docs/index.md

key-decisions:
  - "seed_auth_on_startup passa a default=False; o fail-fast de PROD pré-existente (settings.py:594-595, agora ~601) não foi tocado"
  - "Runbook de rotação usa o fluxo real de password/recovery (não um endpoint dedicado a contas seed) — rotação e recuperação de senha são a mesma operação"
  - "Remoção documenta explicitamente o bloqueio de FK NOT NULL em parametro_change_requests.requested_by como critério de decisão entre rotação e remoção"

patterns-established:
  - "Runbooks operacionais (docs/*.md) protegidos contra drift por teste que importa a fonte de verdade do código, não uma cópia estática"

requirements-completed: [SEC-02, SEC-04]

# Metrics
duration: ~15min
completed: 2026-08-12
---

# Phase 5 Plan 1: Seed default seguro + runbook de contas seed Summary

**`SEED_AUTH_ON_STARTUP` default trocado de `True` para `False`, com `.env.example` coerente, e novo runbook `docs/seguranca.md` (detecção/rotação/remoção das 5 contas seed) protegido por teste de drift.**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2/2 completed
- **Files modified:** 6 (2 created de teste, 1 doc nova, 1 doc atualizada, 2 arquivos de config)

## Accomplishments

- SEC-02 fechado: nenhum ambiente sobe mais com 5 contas de senha pública sem opt-in explícito; o fail-fast de PROD pré-existente continua intacto e provado por teste
- SEC-04 fechado: existe um procedimento executável (query SQL de detecção + passos numerados de rotação e remoção) para o passivo de contas seed já criadas em ambientes onde o seed rodou
- Guard de drift automatizado: se alguém adicionar uma 6ª conta seed sem documentá-la, o teste falha o build

## Task Commits

1. **Task 1: Virar o default de SEED_AUTH_ON_STARTUP para False e provar em teste** - `e6348dd` (fix)
2. **Task 2: Escrever o runbook docs/seguranca.md e travá-lo contra drift** - `08a71de` (docs)

_Nenhuma task usou TDD explícito (não marcado `tdd="true"` no plano); testes foram escritos junto com a implementação em cada task, verificados verdes antes do commit._

## Files Created/Modified

- `app/shared/config/settings.py` - `seed_auth_on_startup` agora `default=False`, com comentário explicando o motivo (senha conhecida = opt-in)
- `.env.example` - `SEED_AUTH_ON_STARTUP=false`, comentário aponta para `docs/seguranca.md`
- `app/tests/test_settings_seed.py` - 3 testes: default `False` sem env var, opt-in explícito funciona, `ENV=PROD` + `SEED_AUTH_ON_STARTUP=true` continua falhando o boot
- `docs/seguranca.md` - runbook novo: o que são as contas seed, as 5 contas em tabela, detecção via SQL, rotação via `password/recovery`, remoção com bloqueio de FK documentado, prevenção, checklist por ambiente
- `docs/index.md` - nova linha na tabela de documentação linkando `seguranca.md`
- `app/tests/test_docs_seguranca.py` - 3 testes: todas as 5 contas de `DEFAULT_AUTH_USERS` aparecem no runbook (e-mail + senha), seções obrigatórias presentes, `docs/index.md` linka o runbook

## Decisions Made

- Nenhuma decisão fora do que o plano já especificava. A construção do teste de PROD seguiu exatamente o roteiro do plano (`monkeypatch.setattr` em `ENV` + variáveis remotas explícitas para passar pelas outras validações PROD antes de chegar na checagem de seed).

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered

- O worktree desta execução foi criado a partir de `main` (commit vazio `7b24b805`) em vez de `develop` (commit base esperado `5571c933`), e o diretório `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/` (com os 4 arquivos de plano/pesquisa) estava presente apenas como arquivos não versionados no checkout principal, não no worktree. Corrigido no início da execução: `git reset --hard 5571c933...` trouxe o HEAD do worktree para a base correta (nenhuma mudança seria perdida — `git status` confirmou árvore limpa antes do reset); os arquivos de plano/pesquisa foram lidos diretamente do checkout principal via caminho absoluto, já que são inputs de leitura, não haviam necessidade de copiá-los para o worktree.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- SEC-02 e SEC-04 fechados; `Settings` (lido por todos os outros módulos) validado com a suíte completa verde (722 passed, 15 skipped — skips pré-existentes, não relacionados a este plano)
- Plano 05-02 (SEC-05, hash de OTP + migration 029) e os demais planos da fase (05-03 SEC-03, 05-04 SEC-06, 05-05 SEC-07) não têm dependência deste plano no grafo (`depends_on: []` em todos) e podem seguir em paralelo/sequência conforme o wave plan
- Nenhum bloqueio identificado para o restante da fase

---
*Phase: 05-auth-otp-seed-jwt-fail-fast-security*
*Completed: 2026-08-12*

## Self-Check: PASSED

- FOUND: app/tests/test_settings_seed.py
- FOUND: app/tests/test_docs_seguranca.py
- FOUND: docs/seguranca.md
- FOUND: docs/index.md
- FOUND: .planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-01-SUMMARY.md
- FOUND commit: e6348dd (fix(auth): default SEED_AUTH_ON_STARTUP para false)
- FOUND commit: 08a71de (docs(auth): runbook de contas seed com guard de drift)
- FOUND commit: 83f3fe6 (docs(05-01): completa plano)
