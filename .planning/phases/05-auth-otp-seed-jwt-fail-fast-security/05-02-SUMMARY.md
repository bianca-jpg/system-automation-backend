---
phase: 05-auth-otp-seed-jwt-fail-fast-security
plan: 02
subsystem: auth
tags: [hmac-sha256, alembic, postgres, otp, sec-05]

# Dependency graph
requires: []
provides:
  - "app/modules/auth/domain/otp_hash.py::hash_otp_code / verify_otp_code (HMAC-SHA256 keyed por secret)"
  - "AuthOtpChallenge.code alargado para String(64) (digest hex, não texto claro)"
  - "Migration 029 (revision 029, down_revision 028) — alembic heads == 029"
  - "Prova destrutiva upgrade/downgrade/upgrade da migration 029 contra Postgres real"
affects: [05-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "HMAC-SHA256 keyed por segredo para hash de OTP (mesmo formato de app/shared/infrastructure/rate_limit.py::_opaque_key, módulo puro sem get_settings)"
    - "hmac.compare_digest obrigatório na comparação de digest (constant-time)"
    - "Migration com id sequencial + docstring 'why' + downgrade destrutivo explícito, seguindo alembic/versions/028_*.py"

key-files:
  created:
    - app/modules/auth/domain/otp_hash.py
    - app/tests/test_auth_otp_hash.py
    - alembic/versions/029_auth_otp_code_hash.py
    - app/tests/test_migration_029_auth_otp_code_hash.py
  modified:
    - app/modules/auth/infrastructure/models.py

key-decisions:
  - "Migration 029 confirmada contra a head real (028) antes de escrever — sem assumir o número"
  - "Downgrade da 029 apaga as linhas de auth_otp_challenges antes de encurtar a coluna, evitando StringDataRightTruncation; seguro porque desafios são efêmeros (TTL 15min) e reemitíveis"
  - "otp_hash.py não importa get_settings — quem injeta o secret é o chamador (repositorio_otp.py, plano 03), mantendo o módulo puro"

patterns-established:
  - "Toda migration nova neste repo segue: id numérico sequencial, docstring com Revision ID/Revises/Create Date + prosa do PORQUÊ, downgrade explícito e comentado"

requirements-completed: [SEC-05]

# Metrics
duration: 25min
completed: 2026-08-12
---

# Phase 5 Plan 02: OTP hash primitive + schema widening Summary

> **Nota de renumeração (2026-08-17).** Toda menção a "migration 029" abaixo se refere ao
> que hoje é a **030**. Depois deste plano, `feat(auth): persiste nome, cpf e telefone no
> cadastro de usuario` tomou o número 029, e a migration do hash de OTP foi renumerada
> (commits `e2010e1` e `02753f5`). Estado real no repositório:
>
> - `alembic/versions/030_auth_otp_code_hash.py` — `revision = "030"`, `down_revision = "029"`
> - `app/tests/test_migration_030_auth_otp_code_hash.py`
> - `alembic/versions/029_auth_users_nome_cpf_telefone.py` é outra migration, não esta
>
> Os nomes de arquivo citados no corpo deste SUMMARY não existem mais com o número 029.

**HMAC-SHA256 OTP hashing primitive (constant-time verify) plus Alembic migration 029 widening `auth_otp_challenges.code` to varchar(64), with a destructive upgrade/downgrade/upgrade cycle proven against a real disposable Postgres.**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-08-12T13:20:00-03:00 (approx.)
- **Completed:** 2026-08-12T13:35:21-03:00
- **Tasks:** 3/3 completed
- **Files modified:** 5 (4 created, 1 modified)

## Accomplishments
- `hash_otp_code`/`verify_otp_code` implemented as pure stdlib functions (HMAC-SHA256, `hmac.compare_digest`), mirroring the existing `_opaque_key` precedent in `rate_limit.py`
- `AuthOtpChallenge.code` widened from `String(6)` to `String(64)` in the model, with migration 029 (BLOCKING task) landing before any code that would write a 64-char digest
- Destructive migration test proves the full upgrade → insert 64-char digest → downgrade (row-deleting) → upgrade cycle against a real, disposable Postgres database — not mocked
- Confirmed `uv run alembic heads` prints exactly one head (`029`) and the full test suite (725 passed, 16 skipped) stays green

## Task Commits

Each task was committed atomically:

1. **Task 1: Criar o módulo de domínio otp_hash.py e seu teste unitário** - `dea9228` (feat)
2. **Task 2: [BLOCKING] Alargar AuthOtpChallenge.code para String(64) e criar a migration 029** - `c53df60` (feat)
3. **Task 3: Teste destrutivo da migration 029** - `5b60a3c` (test)

**Plan metadata:** committed alongside this summary (see below)

## Files Created/Modified
- `app/modules/auth/domain/otp_hash.py` - Pure HMAC-SHA256 hash/verify functions for OTP codes, no `get_settings` import
- `app/tests/test_auth_otp_hash.py` - 9 pure unit tests (determinism, format, keying, constant-time guard)
- `app/modules/auth/infrastructure/models.py` - `AuthOtpChallenge.code` widened `String(6)` → `String(64)`, with an inline comment explaining why (SHA-256 hex digest length)
- `alembic/versions/029_auth_otp_code_hash.py` - Migration widening `auth_otp_challenges.code`; downgrade deletes rows first, then narrows back to `String(6)`
- `app/tests/test_migration_029_auth_otp_code_hash.py` - Destructive integration test (upgrade/downgrade/upgrade cycle) against an isolated scratch Postgres database

## Decisions Made
- Confirmed the real Alembic head (`028`) via `uv run alembic heads` before writing the migration, per the plan's explicit instruction not to assume `029`
- Wrote the migration by hand (mirroring `028_remove_colunas_ingestao_sem_leitor.py`'s structure) rather than relying on autogenerate, since the plan flagged autogenerate as prone to proposing unrelated diffs when the local DB is out of sync
- Downgrade deletes all rows in `auth_otp_challenges` before narrowing the column — documented in the migration docstring as safe because OTP challenges are ephemeral (15-min TTL) and always reissuable
- Kept `otp_hash.py` free of any `get_settings`/config import — the secret is injected by the caller (deferred to plan 03's `repositorio_otp.py` wiring), keeping this module a pure, easily-testable primitive

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module docstring accidentally matched the `grep -c "bcrypt"` acceptance check**
- **Found during:** Task 1 verification (running the acceptance-criteria greps)
- **Issue:** The initial docstring for `otp_hash.py` explained the design choice using the literal phrase "não bcrypt", which caused `grep -c "bcrypt"` to return 1 instead of the required 0
- **Fix:** Reworded the docstring to reference `senha.py` instead of naming `bcrypt` literally, preserving the same explanatory intent
- **Files modified:** `app/modules/auth/domain/otp_hash.py`
- **Verification:** `grep -c "bcrypt" app/modules/auth/domain/otp_hash.py` now returns 0; all 9 unit tests still pass
- **Committed in:** `dea9228` (part of Task 1 commit)

**2. [Rule 1 - Bug] Inline model comment accidentally matched the `grep -c "String(64)"` acceptance check twice**
- **Found during:** Task 2 verification (running the acceptance-criteria greps)
- **Issue:** The inline comment above `code: Mapped[str] = mapped_column(String(64), ...)` used the literal text `String(64):`, causing the grep count to be 2 instead of the required 1
- **Fix:** Reworded the comment to say "64 caracteres" instead of repeating the type literal
- **Files modified:** `app/modules/auth/infrastructure/models.py`
- **Verification:** `grep -c "String(64)" app/modules/auth/infrastructure/models.py` now returns 1
- **Committed in:** `c53df60` (part of Task 2 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 1 — cosmetic wording bugs against the plan's own grep-based acceptance criteria)
**Impact on plan:** No functional or scope impact; both fixes are wording-only adjustments to satisfy exact acceptance-criteria greps.

## Issues Encountered
- This worktree's branch (`worktree-agent-a7a7222451705a0de`) initially had no common ancestor with the expected base commit (`5571c93...`) — HEAD was on an unrelated orphan commit ("chore: inicializa branch main vazia"). Corrected via the mandated `git reset --hard` to the expected base commit as the first setup step (before any task work), per the `worktree_branch_check` protocol.
- The worktree had no `.env` file (gitignored, not copied by `git worktree add`) and no `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/` directory (untracked in the source checkout, so absent from the worktree). Copied `.env` from the main repo checkout (safe — it's gitignored, never committed) to enable local test/DB access, and read the plan/research/pattern docs directly from the main repo path since they were needed only for context, not for editing.
- `MIGRATION_TEST_DATABASE_URL` was not set in the environment. Created a disposable `automation_migration_test_local` database on the local Postgres instance, ran the destructive migration test against it for real verification (passed), then dropped the database — leaving no environment-specific artifact behind. The test itself correctly reports `skip` (not error) when the env var is absent, as required by the plan's acceptance criteria.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Plan 05-03 (SMTP delivery + wiring the hash into `repositorio_otp.py`) can now proceed: the `otp_hash.py` primitive and the widened `auth_otp_challenges.code` column are both in place and proven
- `uv run alembic heads` confirms a single head, `029` — no branch conflict for plan 05-03 to build on
- Full test suite green (725 passed, 16 skipped) with no regressions introduced by the column widening

---
*Phase: 05-auth-otp-seed-jwt-fail-fast-security*
*Completed: 2026-08-12*

## Self-Check: PASSED

- FOUND: app/modules/auth/domain/otp_hash.py
- FOUND: app/tests/test_auth_otp_hash.py
- FOUND: app/modules/auth/infrastructure/models.py
- FOUND: alembic/versions/029_auth_otp_code_hash.py
- FOUND: app/tests/test_migration_029_auth_otp_code_hash.py
- FOUND: .planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-02-SUMMARY.md
- FOUND commit: dea9228 (Task 1)
- FOUND commit: c53df60 (Task 2)
- FOUND commit: 5b60a3c (Task 3)
