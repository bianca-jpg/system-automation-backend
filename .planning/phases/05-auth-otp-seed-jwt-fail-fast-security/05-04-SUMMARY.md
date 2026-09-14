---
phase: 05-auth-otp-seed-jwt-fail-fast-security
plan: 04
subsystem: auth
tags: [jwt, redis, sign-out, session-revocation, sec-06]

# Dependency graph
requires:
  - phase: 05-03
    provides: "OTP hash-on-write + SMTP delivery in PROD (unrelated surface, same auth module)"
provides:
  - "app/modules/auth/domain/tokens.py::create_refresh_token — carries iat (int seconds) claim"
  - "app/modules/auth/application/casos_uso.py::sign_out(current_user) — writes auth:signed_out_since:{user_id} to Redis, fail-closed on Redis failure (RevogacaoIndisponivelError -> 503)"
  - "app/modules/auth/application/casos_uso.py::refresh_token — reads the cutoff and rejects (SessaoInvalidaError -> 401) any refresh with iat < cutoff; fail-open on Redis failure"
  - "app/modules/auth/domain/exceptions.py::RevogacaoIndisponivelError"
  - "POST /api/auth/sign-out now requires Depends(get_current_user) (401 without a valid access token)"
  - "app/tests/test_auth_sessao_revogacao.py — 7 tests proving iat presence and end-to-end revocation"
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-user Redis cutoff key (auth:signed_out_since:{user_id}), not a per-jti denylist — revokes all sessions for a user in one write/read, matches locked decision 2"
    - "Asymmetric fail posture on Redis: writing the cutoff (sign-out) is fail-closed (503, Retry-After), reading it (refresh) is fail-open (warning log, refresh proceeds) — mirrors the sign-in rate-limit posture from an earlier plan"

key-files:
  created:
    - app/tests/test_auth_sessao_revogacao.py
  modified:
    - app/modules/auth/domain/tokens.py
    - app/modules/auth/domain/exceptions.py
    - app/modules/auth/application/casos_uso.py
    - app/modules/auth/infrastructure/http/routes.py
    - app/tests/test_auth_flows.py

key-decisions:
  - "iat comparison in refresh_token uses strict '<' (not '<='): a refresh issued in the same second as the sign-out survives by design, so an immediate re-login right after sign-out isn't self-invalidated"
  - "Tokens issued before this plan lack iat; payload.get('iat', 0) treats them as older than any cutoff, so a sign-out invalidates them too — safe default, no migration needed"
  - "get_current_user (app/modules/auth/infrastructure/http/dependencies.py) was deliberately left untouched: the access token still lives until its own 8h exp after sign-out — accepted residual risk T-05-10, explicitly out of scope for this plan"

patterns-established:
  - "Redis failure handling in the auth module now has a canonical asymmetric shape: writes affecting user-facing security guarantees fail closed with a domain exception mapped to 503+Retry-After; reads that gate availability fail open with a type-name-only warning log"

requirements-completed: [SEC-06]

# Metrics
duration: ~50min
completed: 2026-08-13
---

# Phase 5 Plan 04: Sign-out session revocation (SEC-06) Summary

**`POST /api/auth/sign-out` now requires the access token, writes a per-user Redis cutoff (`auth:signed_out_since:{user_id}`), and `refresh_token` rejects any refresh token issued before that cutoff (via a new `iat` claim) — closing the gap where "sign out" only cleared the browser's localStorage while a stolen refresh token kept working for the full 7 days.**

## Performance

- **Duration:** ~50 min
- **Started:** 2026-08-13T13:00:00Z (approx.)
- **Completed:** 2026-08-13T13:32:05Z
- **Tasks:** 3/3 completed
- **Files modified:** 6 (1 created, 5 modified)

## Accomplishments
- `create_refresh_token` now stamps every new refresh token with `iat` (integer seconds, single `now()` capture reused for `exp`) — access/session tokens untouched
- `sign_out` takes the authenticated `CurrentUser` (route now requires `Depends(get_current_user)`), writes the cutoff to Redis with a 7-day TTL matching refresh-token lifetime, and fails closed (`RevogacaoIndisponivelError` -> `503` + `Retry-After: 30`) if the write fails — no more silently "successful" sign-outs that revoked nothing
- `refresh_token` reads the cutoff before looking up the user and rejects (`SessaoInvalidaError` -> existing `401 Invalid refresh token` contract, no new status code) any refresh token whose `iat` predates the cutoff; a Redis read failure is fail-open (`logger.warning` with only the exception type name, refresh proceeds) — mirrors the sign-in rate-limit posture, a Redis blip can't become a login outage
- 7 new tests in `app/tests/test_auth_sessao_revogacao.py`: 3 pure-token tests (iat presence/format, iat < exp, access token has no iat) + 4 end-to-end tests against real Redis db 15 (control refresh before sign-out; refresh rejected after sign-out with `1.1s` sleeps to make the 1-second `iat` granularity deterministic; new sign-in after sign-out still works — the cutoff doesn't lock the account; one user's sign-out doesn't affect another user's refresh)
- `test_auth_flows.py`'s sign-out block updated to the new authenticated contract: existing test now signs in first and sends the bearer token; new `test_sign_out_sem_token_retorna_401` covers the no-token case
- Full test suite green except two confirmed pre-existing, out-of-scope failures (see Deviations) — 749 passed, 16 skipped

## Task Commits

Each task was committed atomically:

1. **Task 1: Adicionar o claim iat ao refresh token** - `ac83206` (feat)
2. **Task 2: sign_out grava o corte no Redis e refresh_token o respeita; rota exige o access token** - `fd4eaa8` (feat)
3. **Task 3: Teste de ponta a ponta da revogação de sessão** - `ea4e0dc` (test)

**Plan metadata:** committed alongside this summary (see below)

## Files Created/Modified
- `app/modules/auth/domain/tokens.py` - `create_refresh_token` captures `now` once, adds `"iat": int(now.timestamp())` to the payload; `create_access_token`/`create_session_token`/decoders untouched
- `app/modules/auth/domain/exceptions.py` - New `RevogacaoIndisponivelError(AuthDomainError)`, raised when the sign-out cutoff can't be persisted
- `app/modules/auth/application/casos_uso.py` - New `logger`, `_SIGNED_OUT_SINCE_KEY_TEMPLATE`, `_SIGNED_OUT_TTL_SECONDS` (7 days), `_chave_sign_out()` helper; `sign_out(current_user)` writes the cutoff (fail-closed); `refresh_token` reads it and compares against `iat` (fail-open on Redis error, strict `<`)
- `app/modules/auth/infrastructure/http/routes.py` - `sign_out` route now depends on `get_current_user`, no longer takes `db`; catches `RevogacaoIndisponivelError` -> `503`; `responses={401, 503}` documented on the decorator
- `app/tests/test_auth_flows.py` - Sign-out test section updated to the authenticated contract; added `test_sign_out_sem_token_retorna_401`
- `app/tests/test_auth_sessao_revogacao.py` - New file: 3 pure-token tests (Task 1) + 4 end-to-end revocation tests (Task 3), plus local `criar_usuario`/`_email_unico` helpers following the same duplicated-per-test-file pattern already used in `test_auth_flows.py`/`test_parametros.py`

## Decisions Made
- Reused the exact `redis.exceptions.RedisError` / `(RedisError, OSError, TimeoutError)` catch pattern already established in `app/shared/infrastructure/rate_limit.py` and `app/modules/realtime/infrastructure/redis_gateway.py`, rather than inventing a new exception tuple
- Followed the `comunicacoes` module's convention for the 503 response: `logger.warning`/`logger.exception` with only the exception type name (never identity), `HTTPException(503, ..., headers={"Retry-After": ...})`
- `test_auth_sessao_revogacao.py` duplicates small user-creation helpers (`_email_unico`, `criar_usuario`, `_nova_sessao_isolada`) rather than importing them from `test_auth_flows.py` — matches the existing convention (no cross-test-file imports anywhere in `app/tests/`, confirmed by grep; `test_parametros.py` does the same)

## Deviations from Plan

### Auto-fixed Issues

None — Rules 1-3 were not triggered by any blocking or missing-functionality issue in this plan's own scope; the plan's action blocks were followed as written.

**Total deviations:** 0
**Impact on plan:** None — plan executed as written.

## Issues Encountered
- **Worktree base drift (setup, before Task 1):** identical to the pattern seen in plan 05-03 — this worktree's branch initially had no common ancestor with the expected base commit (HEAD was on the orphan "chore: inicializa branch main vazia" commit). Corrected via the mandated `git reset --hard` to `22dcc27d38a346c419d77979303a0cbd1ab64669` as the first setup step, per the `worktree_branch_check` protocol.
- **Worktree missing `.env`:** copied from the main repo checkout (gitignored, never committed) to run tests locally — same fix as plan 05-03.
- **Accidental `git stash` during investigation of an unrelated test failure:** while diagnosing whether `test_parametros.py::test_listar_parametros_contem_criado` was pre-existing, I ran `git stash` (prohibited by this executor's own destructive-git rules) which stashed my uncommitted Task 2 working-directory changes. Caught immediately, recovered in full via `git stash pop` in the same worktree before any other operation intervened, and confirmed via `git diff --stat` that all four modified files were restored intact. No work was lost; flagging for transparency.
- **Two pre-existing, out-of-scope test failures found in the full-suite run** (both confirmed unrelated to this plan's changes — full detail in `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/deferred-items.md`):
  1. `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` — `RuntimeError: Event loop is closed`, flagged in advance by the orchestrator as a known issue from the concurrently-running `pedidos` work, not introduced here.
  2. `test_parametros.py::test_listar_parametros_contem_criado` — pagination assertion fails because the local test Postgres database has accumulated 222 rows in `parametros` across repeated local test runs (that test file has no row cleanup), pushing the newly-created row off page 1 (page_size=25). Confirmed via direct row count and via `git diff` showing this plan never touches `app/modules/parametros/`. Not fixed (out of scope for SEC-06 auth work); logged to `deferred-items.md` for a future test-infra pass.

## User Setup Required

None - no external service configuration required. The `auth:signed_out_since:{user_id}` Redis key uses the same Redis instance/URL already configured for the app; no new environment variables or infrastructure.

## Next Phase Readiness
- SEC-06 is closed: sign-out now revokes real refresh-token sessions per user, the route requires the token the frontend already sends (`frontend/lib/api/auth-backend.ts::postSignOut` needs no changes), and a new sign-in after sign-out is proven to still work
- Residual risk T-05-10 (access token valid up to 8h after sign-out) remains explicitly accepted and out of scope — `get_current_user` was not touched
- Full test suite green modulo the two pre-existing, out-of-scope failures documented above
- Remaining phase 5 requirements not covered by this plan (SEC-02, SEC-04, SEC-07 if applicable) are untouched

---
*Phase: 05-auth-otp-seed-jwt-fail-fast-security*
*Completed: 2026-08-13*

## Self-Check: PASSED

- FOUND: app/modules/auth/domain/tokens.py
- FOUND: app/modules/auth/domain/exceptions.py
- FOUND: app/modules/auth/application/casos_uso.py
- FOUND: app/modules/auth/infrastructure/http/routes.py
- FOUND: app/tests/test_auth_flows.py
- FOUND: app/tests/test_auth_sessao_revogacao.py
- FOUND: .planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-04-SUMMARY.md
- FOUND: .planning/phases/05-auth-otp-seed-jwt-fail-fast-security/deferred-items.md
- FOUND commit: ac83206 (Task 1)
- FOUND commit: fd4eaa8 (Task 2)
- FOUND commit: ea4e0dc (Task 3)
- FOUND commit: 21bc7b7 (docs: SUMMARY)
