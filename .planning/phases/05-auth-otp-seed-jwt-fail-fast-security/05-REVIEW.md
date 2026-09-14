---
phase: 05-auth-otp-seed-jwt-fail-fast-security
reviewed: 2026-08-13T00:00:00Z
depth: standard
files_reviewed: 19
files_reviewed_list:
  - app/shared/config/settings.py
  - app/modules/auth/domain/otp_hash.py
  - app/modules/auth/infrastructure/models.py
  - alembic/versions/030_auth_otp_code_hash.py
  - app/modules/auth/infrastructure/otp_email.py
  - app/modules/auth/infrastructure/repositorio_otp.py
  - app/modules/auth/domain/tokens.py
  - app/modules/auth/domain/exceptions.py
  - app/modules/auth/application/casos_uso.py
  - app/modules/auth/infrastructure/http/routes.py
  - app/tests/test_settings_seed.py
  - app/tests/test_docs_seguranca.py
  - app/tests/test_auth_otp_hash.py
  - app/tests/test_migration_030_auth_otp_code_hash.py
  - app/tests/test_auth_otp_email.py
  - app/tests/test_auth_flows.py
  - app/tests/test_auth_sessao_revogacao.py
  - app/tests/test_auth_rate_limit.py
  - app/tests/conftest.py
findings:
  critical: 3
  warning: 4
  info: 2
  total: 9
status: issues_found
---

# Phase 05: Code Review Report

**Reviewed:** 2026-08-13T00:00:00Z
**Depth:** standard
**Files Reviewed:** 19
**Status:** issues_found

## Summary

Reviewed the full file set of Phase 5 (seed default hardening, OTP hashing-at-rest + SMTP delivery, sign-out session revocation, rate limiting). The individual mechanisms are each well-built in isolation — HMAC-based OTP hashing uses constant-time comparison, the rate limiter is atomic (single Lua script) and uses opaque HMAC-derived keys, the `PROD` cross-field validator in `settings.py` closes several real misconfiguration gaps, and the test suite is thorough for the happy paths.

However, three issues undermine the stated security goals of this exact phase and are classified Critical:

1. The entire `PROD` security posture (weak-JWT-secret rejection, forbidden dev hosts, `SEED_AUTH_ON_STARTUP` block, and OTP delivery-by-email instead of plaintext-in-logs) hangs on a single case-sensitive string equality (`ENV == "PROD"`) with no validation that `ENV` is one of the documented allowed values. A trailing space, typo, or unexpected value silently degrades the entire deployment to dev-like (insecure) behavior with no error raised anywhere.
2. Sign-out (SEC-06, this phase's Plan 3) only ever invalidates **refresh** tokens via the Redis cutoff; **access tokens carry no `iat` and are never checked against the cutoff at all** (confirmed in `get_current_user`). Combined with the default `JWT_EXPIRE_MINUTES=480` (8h), a leaked/stolen access token stays fully authenticated for up to 8 hours after the API has already told the client `{"status": "signed_out"}`.
3. The new synchronous SMTP delivery introduced by this phase's Plan 2 (`otp_email.py`, called from `criar_desafio`) reopens a timing side-channel in `request_password_recovery`, which is explicitly designed (and tested) to be anti-enumeration. In `PROD`, an existing account triggers a synchronous SMTP round-trip before the response is returned; a non-existing account returns almost immediately. The response body/status code are identical, but the latency difference lets an attacker distinguish valid from invalid recovery emails — precisely the property the endpoint's own comments say it protects against.

Additional Warning/Info items cover a JWT-secret reuse-for-multiple-purposes smell, a duplicated "7 days" magic number between the refresh token TTL and the sign-out cutoff TTL, a missing DB constraint that lets `criar_desafio` create two concurrently-valid OTP challenges under a race, an unhandled-exception path in `refresh_token`, and two files referenced by this phase's own tests (`app/modules/auth/bootstrap/seed.py`, `docs/seguranca.md`) that were not included in the reviewed file set.

## Critical Issues

### CR-01: Entire PROD security posture depends on a fragile, unvalidated string match

**File:** `app/shared/config/settings.py:11`, `app/shared/config/settings.py:567`, `app/modules/auth/infrastructure/repositorio_otp.py:14`, `app/modules/auth/infrastructure/repositorio_otp.py:52`

**Issue:**
```python
# settings.py:11
ENV = os.getenv("ENV", "dev").upper()
...
# settings.py:567
if ENV == "PROD":
    ... # forbidden dev hosts, JWT_SECRET strength, SEED_AUTH_ON_STARTUP=false
```
```python
# repositorio_otp.py:14, 52
from app.shared.config.settings import ENV, get_settings
...
if ENV == "PROD":
    await enviar_codigo_otp(...)   # deliver by e-mail, never log the code
else:
    logger.info("OTP %s gerado para %s: %s", purpose.value, email, code)  # plaintext code+email in logs
```
Every single one of this phase's fail-fast guards — rejecting the default/weak `JWT_SECRET`, rejecting `localhost`/`dev_db`/`redis` hosts, rejecting `SEED_AUTH_ON_STARTUP=true`, and switching OTP delivery from "log the plaintext code" to "e-mail the hashed-at-rest code" — is gated by one case-sensitive equality check against the literal string `"PROD"`. There is no `Literal`/enum validation on `ENV` anywhere in `Settings`, so:
- `ENV=prod ` (trailing whitespace from a copy-pasted `.env`/orchestrator config) → `.upper()` yields `"PROD "` ≠ `"PROD"`.
- `ENV=Production`, `ENV=PRD`, or any other value a deployment platform might use → same silent bypass.

In every one of these cases the app boots successfully (no `ValidationError`), all `PROD`-only guards are skipped, and `criar_desafio` falls back to logging the OTP code **and the recipient e-mail in plaintext** to the application log stream instead of sending it — in what would actually be a production, internet-facing deployment. This is a silent, total regression of every control this phase added.

**Fix:**
```python
# settings.py
from typing import Literal

_ENV_RAW = os.getenv("ENV", "dev").strip().upper()
if _ENV_RAW not in {"DEV", "TEST", "STAGING", "PROD"}:
    raise RuntimeError(
        f"ENV={_ENV_RAW!r} não reconhecido; use um de DEV/TEST/STAGING/PROD"
    )
ENV = _ENV_RAW
```
Fail fast at import time instead of silently defaulting to dev-like behavior for any unrecognized value.

---

### CR-02: Sign-out only revokes refresh tokens — access tokens remain valid for up to 8 hours after "sign out"

**File:** `app/modules/auth/application/casos_uso.py:198-210` (`sign_out`), `:162-195` (`refresh_token`), `app/modules/auth/domain/tokens.py:15-30` (`create_access_token`)

**Issue:** `sign_out` writes a per-user cutoff timestamp to Redis (`_chave_sign_out`), and `refresh_token` correctly rejects any refresh token whose `iat` predates that cutoff. But `create_access_token` (tokens.py:15-30) never embeds an `iat` claim, and `get_current_user` (`app/modules/auth/infrastructure/http/dependencies.py`) — the dependency every protected route uses — decodes the access token and checks only its signature/expiry/DB user existence; it never consults `_chave_sign_out` (nor could it, since there is no `iat` to compare). This means:
- The access token issued at the last sign-in remains **100% valid** for its full lifetime (`JWT_EXPIRE_MINUTES`, default `480` = 8 hours — see `.env.example:127`) regardless of any subsequent sign-out.
- A client that calls `POST /api/auth/sign-out` and receives `{"status": "signed_out"}` has a false sense of security: any bearer of the still-live access token (e.g., leaked via XSS, a compromised device, a proxy log) can keep calling every authenticated endpoint, including admin-only ones, for up to 8 hours.

This is a direct gap in exactly the feature this phase's Plan 3 claims to deliver ("sign-out session revocation").

**Fix:** Either (a) also stamp access tokens with `iat` and check the same Redis cutoff in `get_current_user`, or (b) shorten `JWT_EXPIRE_MINUTES` materially and document that sign-out is refresh-only revocation with a bounded exposure window. Given the code already pays the Redis round-trip cost on every refresh, adding the same check to `get_current_user` is the more complete fix:
```python
# tokens.py
def create_access_token(*, user_id: int, email: str, roles: list[str]) -> tuple[str, int]:
    ...
    token = _encode_token({..., "iat": int(datetime.now(UTC).timestamp()), ...})
    ...

# dependencies.py
payload = decode_access_token(creds.credentials)
cutoff = await get_redis().get(f"auth:signed_out_since:{payload.get('sub')}")
if cutoff is not None and int(payload.get("iat", 0)) < int(cutoff):
    raise _unauthorized("Session revoked")
```

---

### CR-03: Synchronous SMTP delivery reopens the timing side-channel that `request_password_recovery` is explicitly designed to prevent

**File:** `app/modules/auth/application/casos_uso.py:280-290` (`request_password_recovery`), `app/modules/auth/infrastructure/repositorio_otp.py:52-68` (`criar_desafio`), `app/modules/auth/infrastructure/otp_email.py:1-14`

**Issue:** `request_password_recovery` is deliberately written to return the identical response/status for existing and non-existing e-mails (anti user-enumeration, verified by `test_password_recovery_nao_revela_se_email_existe`):
```python
async def request_password_recovery(session, body):
    email = normalizar_email(body.email)
    user = await repositorio_usuario.buscar_por_email(session, email)
    if user is not None:
        await repositorio_otp.criar_desafio(session, email=email, purpose=OtpPurpose.RECOVERY)
        await session.commit()
    return PasswordRecoveryResponse(status="recovery_requested")
```
Since Plan 2 of this phase, `criar_desafio` — invoked only on the "user exists" branch — now performs a **synchronous** network round-trip to an SMTP server in `PROD` (`otp_email.py`'s own docstring: "O envio é síncrono e direto pelo adapter SMTP ... NÃO passa pela fila assíncrona"). The "user does not exist" branch returns almost instantly (one `SELECT`, no network I/O). This produces an easily measurable latency delta (SMTP handshake + send is commonly tens to hundreds of milliseconds, vs. a single indexed lookup) between the two cases, even though the JSON body and status code are identical. An attacker can enumerate registered e-mails by timing `POST /api/auth/password/recovery`, which is the exact attack this endpoint's design (and its own inline comments, e.g. "coerente com a postura anti-enumeração de request_password_recovery" in `repositorio_otp.py:57`) claims to defend against. This is a regression introduced by this phase, not pre-existing behavior.

**Fix:** Decouple the response from the delivery latency — e.g. schedule the e-mail send on a background task (`BackgroundTasks`, or the existing `comunicacoes` async queue that `otp_email.py`'s docstring says was deliberately bypassed for latency reasons) so the HTTP response time is constant regardless of whether a challenge was created, or add a constant-time floor (`await asyncio.sleep(remaining)`) before responding in both branches.

## Warnings

### WR-01: `JWT_SECRET` reused as the HMAC key for three unrelated purposes without domain separation

**File:** `app/modules/auth/domain/otp_hash.py:9-12`, `app/modules/auth/infrastructure/repositorio_otp.py:41,101`, `app/modules/auth/infrastructure/http/routes.py:91`, `app/shared/infrastructure/rate_limit.py:51-57`

**Issue:** The same `settings.jwt_secret` is used as the HMAC key for (1) signing/verifying JWTs (`tokens.py`), (2) hashing OTP codes at rest (`hash_otp_code(code, secret=settings.jwt_secret)`), and (3) deriving opaque rate-limit bucket keys (`_opaque_key(..., secret=secret)` called with `get_settings().jwt_secret` in `routes.py:91`). None of these HMAC computations mix in a fixed, purpose-specific domain-separation label as part of the keyed input (the rate limiter mixes in `scope`/`identity`, but not a constant tied to the secret's *other* uses; `hash_otp_code`'s message is just the 6-digit code). Reusing one long-lived signing secret across three cryptographic roles is a well-known anti-pattern — it means rotating the JWT secret (e.g., after a suspected compromise) simultaneously and silently invalidates all in-flight OTP challenges and resets every rate-limit bucket, and it removes the ability to rotate each purpose's key independently.

**Fix:** Derive purpose-specific subkeys via HKDF (or at minimum distinct HMAC(secret, "otp-hash"|"rate-limit") derivations) instead of passing the raw `jwt_secret` into each unrelated primitive:
```python
def _derive_key(secret: str, purpose: str) -> bytes:
    return hmac.new(secret.encode(), purpose.encode(), hashlib.sha256).digest()
```

### WR-02: "7 days" duplicated as a magic number between refresh-token TTL and sign-out cutoff TTL

**File:** `app/modules/auth/domain/tokens.py:35`, `app/modules/auth/application/casos_uso.py:66`

**Issue:**
```python
# tokens.py:35
expire = now + timedelta(days=7)
```
```python
# casos_uso.py:66
_SIGNED_OUT_TTL_SECONDS = 7 * 86_400
```
The comment at `casos_uso.py:64-66` explicitly says the Redis TTL "espelha os 7 dias de vida do refresh token", i.e. correctness of the whole revocation mechanism depends on these two independently-hardcoded values staying in sync. If a future change bumps the refresh-token lifetime (e.g., to 14 or 30 days) without updating `_SIGNED_OUT_TTL_SECONDS`, the sign-out cutoff key would expire from Redis *before* the (now longer-lived) refresh tokens it was supposed to keep invalidating — silently reopening a revoked session once the shorter TTL lapses, with no test or type system to catch the drift.

**Fix:** Define a single shared constant (e.g. `REFRESH_TOKEN_TTL = timedelta(days=7)` in `tokens.py`, exported and imported by `casos_uso.py` as `int(REFRESH_TOKEN_TTL.total_seconds())`) instead of two independent literals.

### WR-03: No DB constraint enforcing "at most one active OTP challenge per (email, purpose)" — race can create duplicates

**File:** `app/modules/auth/infrastructure/repositorio_otp.py:21-29` (`remover_desafios`), `app/modules/auth/infrastructure/models.py:51-68` (`AuthOtpChallenge`)

**Issue:** `verificar_desafio`'s correctness comment relies on the invariant "remover_desafios garante no máximo um desafio ativo por (email, purpose)" (repositorio_otp.py:85-88). That invariant is enforced purely by application-level ordering (`DELETE` then `INSERT` in the same session, not a single atomic upsert), and there is no unique constraint on `(email, purpose)` in `models.py` or migration 030. Two concurrent requests for the same `(email, purpose)` (e.g., a user double-clicking "resend code", or a legitimate register + recovery race for the same address) can each execute `remover_desafios` (deleting zero or one row) and then insert a new row, leaving two simultaneously-valid challenges. `verificar_desafio`'s `ORDER BY id DESC LIMIT 1` still returns a result, so a stale/superseded code would keep validating one of the rows until its own TTL expires, contrary to the intended "only the latest code is valid" behavior a user would reasonably expect after requesting "resend."

**Fix:** Add a unique index on `(email, purpose)` (with an `ON CONFLICT DO UPDATE` upsert in `criar_desafio`), or wrap delete+insert in a `SELECT ... FOR UPDATE`-guarded transaction.

### WR-04: `refresh_token()` can raise an unhandled 500 instead of 401 for a structurally malformed payload

**File:** `app/modules/auth/application/casos_uso.py:162-195`

**Issue:**
```python
try:
    payload = decode_refresh_token(body.refresh_token)
except Exception as exc:
    raise SessaoInvalidaError() from exc
...
cutoff = await get_redis().get(_chave_sign_out(payload["sub"]))   # KeyError if "sub" missing
...
user_id = int(payload["sub"])                                     # ValueError if not numeric
```
Only the `decode_refresh_token` call itself is guarded against exceptions. If a syntactically valid, correctly-signed token were ever produced without a `sub` claim, or with a non-numeric `sub` (e.g. future schema drift, or a token minted by another code path/version), `payload["sub"]` raises `KeyError`, and `int(payload["sub"])` raises `ValueError` — both propagate uncaught out of the route handler, producing a `500 Internal Server Error` instead of the intended `401 Invalid refresh token`. `decode_access_token`'s sibling consumer (`get_current_user` in `dependencies.py`) already guards this exact case with an explicit `sub is None` / `int(sub)` try/except; `refresh_token` here does not follow the same defensive pattern.

**Fix:**
```python
try:
    payload = decode_refresh_token(body.refresh_token)
    user_id = int(payload["sub"])
except Exception as exc:
    raise SessaoInvalidaError() from exc
```
Move the `sub` extraction inside the same guarded block (or add an explicit `except (KeyError, TypeError, ValueError)` branch) so any malformed payload maps to `SessaoInvalidaError` → 401, consistent with `get_current_user`.

## Info

### IN-01: Two session-token flows are structurally dead / unreachable through the current API surface

**File:** `app/modules/auth/application/casos_uso.py:121-131` (`sign_in`, `CONFIRM_SIGN_UP` challenge), `:317-324` (`confirm_new_password`, `new_password_required`), `app/modules/auth/domain/tokens.py:69-83` (`create_session_token`/`decode_session_token`)

**Issue:** `sign_in` issues a `create_session_token(purpose="confirm_sign_up")` inside the `SignInChallengeResponse` for unconfirmed users, but no endpoint in `routes.py`/`casos_uso.py` ever calls `decode_session_token(..., purpose="confirm_sign_up")` — actual registration confirmation goes through `/register/confirm` with an e-mail + OTP code, not this session token. Conversely, `confirm_new_password` decodes a session token with `purpose="new_password_required"`, but no code path anywhere calls `create_session_token(purpose="new_password_required")` — the test that exercises this endpoint (`test_confirm_new_password_sucesso`) has to manually mint the token itself, and its own comment block says "fluxo hoje sem nenhum emissor de session na aplicação." Both look like leftover scaffolding (plausibly from an earlier Cognito-style design) that add surface area (an extra JWT-accepting endpoint, an extra token type/purpose) without a matching producer/consumer pair.

**Fix:** Either wire a real producer for `new_password_required` and a real consumer for `confirm_sign_up`, or remove the dead branch/endpoint and document that registration confirmation is exclusively OTP-code-based.

### IN-02: Files central to this phase's "seed hardening" plan were not included in the reviewed scope

**File:** `app/tests/test_docs_seguranca.py:1-4`, `app/tests/test_settings_seed.py`

**Issue:** `test_docs_seguranca.py` imports `DEFAULT_AUTH_USERS` from `app.modules.auth.bootstrap.seed` and asserts that `docs/seguranca.md` documents every seed account's e-mail/password — but neither `app/modules/auth/bootstrap/seed.py` nor `docs/seguranca.md` was part of the files provided for this review, even though they appear to be the actual deliverable of Phase 5's "seed default hardening" plan (only the `SEED_AUTH_ON_STARTUP` settings flag was reviewable). This review cannot assess whether the seeded default passwords, the runbook's detection/rotation/removal guidance, or the seed's own opt-in wiring are correct.

**Fix:** Include `app/modules/auth/bootstrap/seed.py` and `docs/seguranca.md` in a follow-up review pass for complete coverage of Plan 1.

---

_Reviewed: 2026-08-13T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
