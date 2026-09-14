# Phase 5: Auth / OTP / seed / JWT fail-fast security - Research

**Researched:** 2026-08-12
**Domain:** FastAPI backend auth hardening — OTP delivery, secret hashing, token revocation, rate limiting
**Confidence:** MEDIUM-HIGH (codebase patterns verified by direct read; a few design choices are recommendations, not locked decisions — see Assumptions Log)

## Summary

This phase closes 6 of 7 SEC-* gaps in `app/modules/auth/` (SEC-01 is already done and out of scope). All the infrastructure needed already exists elsewhere in the codebase and is proven in production-adjacent code (`app/modules/comunicacoes/`, `app/shared/infrastructure/rate_limit.py`, `app/shared/infrastructure/redis_client.py`) — this phase is almost entirely about **wiring existing primitives into `auth`**, not introducing new libraries. No new third-party packages are required: `bcrypt`, `aiosmtplib`, `redis`, and `python-jose[cryptography]` are already declared in `pyproject.toml` and already used for exactly these kinds of operations elsewhere in the app.

The riskiest design decision is **SEC-05 (OTP hashing)**: the codebase's only existing "hash a secret" pattern is bcrypt for user passwords (`app/modules/auth/domain/senha.py`), but bcrypt's deliberately-slow cost factor is the wrong tool for a 6-digit, rate-limited, 15-minute-TTL numeric code — it adds latency to every registration/recovery confirmation for no real security gain (see Common Pitfalls). The codebase already has an established alternative pattern for exactly this shape of problem: HMAC-SHA256 with `settings.jwt_secret` as key, used today in `app/shared/infrastructure/rate_limit.py::_opaque_key` and `app/shared/pagination/cursor.py`. This research recommends following that existing pattern for OTP hashing rather than introducing bcrypt for a case it wasn't designed for. This is a recommendation, not a verified fact — flagged in the Assumptions Log for confirmation.

**Primary recommendation:** Wire each SEC-* gap directly to the nearest existing analogous mechanism already proven in this codebase — `enforce_dual_fixed_window` for SEC-07, `SmtpEmailSender`/`aiosmtplib` (called synchronously, not through the outbox) for SEC-03, HMAC-SHA256 keyed by `settings.jwt_secret` for SEC-05, a Redis "signed-out-since" cutoff for SEC-06, a flipped Pydantic default for SEC-02, and a new `docs/seguranca.md` runbook for SEC-04. Do not build new abstractions; do not reuse the full comunicacoes delivery-outbox for OTP (wrong latency profile — see Pitfall 2).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SEC-01 | PROD falha ao iniciar se `JWT_SECRET` ausente/placeholder/curto | **Already implemented** (`settings.py:588-592`) — out of scope for this phase's plan; research confirms it should not be re-touched, only left as-is |
| SEC-02 | `SEED_AUTH_ON_STARTUP` default seguro (`False`); seed proibido em PROD | PROD fail-fast half already done (`settings.py:594-595`). Remaining work: flip default at `settings.py:432-434` from `True` to `False`, and update `.env.example:159` to match (Pitfall 4). See Code Examples. |
| SEC-03 | OTP em PROD entregue por e-mail; nunca logado em PROD | Reuse `SmtpEmailSender` (`comunicacoes/infrastructure/email_sender.py`) called synchronously from `repositorio_otp.py::criar_desafio` when `ENV == "PROD"` — see Pattern 4. Do NOT route through the delivery outbox (Pitfall 2, Alternatives Considered). |
| SEC-04 | Runbook: usuários seed legados (rotação/remoção) | Pure documentation. Seed accounts/passwords enumerated in `app/modules/auth/bootstrap/seed.py::DEFAULT_AUTH_USERS` (5 accounts, one per role, all with hardcoded default passwords). Recommend a new `docs/seguranca.md`, linked from `docs/index.md`, matching the existing one-doc-per-topic convention. |
| SEC-05 | Código OTP armazenado com hash (não plaintext) | HMAC-SHA256 keyed by `settings.jwt_secret`, matching the existing `_opaque_key` pattern in `rate_limit.py`. Requires widening `AuthOtpChallenge.code` column via Alembic migration (Pitfall 5) and changing `verificar_desafio`'s query shape (Pattern 2). Also requires updating the `ler_otp()` test helper (Pitfall 1) — a required companion change, not optional. |
| SEC-06 | `POST /api/auth/sign-out` revoga refresh token(s); reuso após sign-out rejeitado | Redis "signed-out-since" per-user cutoff (Pattern 3): add `iat` to `create_refresh_token`, check cutoff in `refresh_token` use case, write cutoff in `sign_out` (now requiring `Depends(get_current_user)` — confirmed compatible with FE's existing `Authorization: Bearer <access_token>` header, no FE change needed). |
| SEC-07 | Rate limit (usuário+IP) em sign-in e confirmação de OTP | Reuse `enforce_dual_fixed_window` exactly as `comunicacoes` does (Pattern 1) — new namespaces per auth endpoint, `user_identity` = normalized target email (unauthenticated endpoints). Suggested limits table in Pattern 1. Fail-open-vs-closed behavior for `sign-in` flagged as Open Question 1 (Pitfall 3). |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Seed-on-boot default / PROD fail-fast | API / Backend (settings/bootstrap) | — | Pure startup config validation in `app/shared/config/settings.py`; no other tier involved |
| OTP delivery (email) | API / Backend (application layer) | Database / Storage (SMTP settings already in `Settings`) | Synchronous SMTP call from the auth use case that generates the OTP; no new tier |
| OTP storage hashing | Database / Storage (schema) + API / Backend (domain) | — | Hash computed in `app/modules/auth/domain/` or `infrastructure/`, persisted in `auth_otp_challenges.code` |
| Sign-out / refresh revocation | API / Backend (auth) | Database / Storage (Redis cutoff key) | Revocation state lives in Redis, checked by the API on every refresh; no browser/client changes needed (FE already sends the access token) |
| Rate limiting (sign-in, OTP confirm/request) | API / Backend (auth routes) | Database / Storage (Redis) | Same tier and same Redis instance as the existing `comunicacoes` rate limiter |
| Seed-user runbook | Docs (no runtime tier) | — | Pure documentation; no code executes it |

## Standard Stack

### Core (all already installed — no new packages)

| Library | Version (pyproject.toml) | Purpose | Why Standard (for this codebase) |
|---------|---------|---------|--------------|
| `bcrypt` | `>=4.2.1` [VERIFIED: pyproject.toml] | User password hashing (existing) | Already the codebase's password hash; reused as-is, not for OTP |
| `aiosmtplib` | `>=3.0.0` [VERIFIED: pyproject.toml] | Async SMTP send | Already used by `comunicacoes/infrastructure/email_sender.py::SmtpEmailSender` |
| `redis` (`redis.asyncio`) | `>=5.2.0` [VERIFIED: pyproject.toml] | Rate limiting + revocation cutoff storage | Already used by `app/shared/infrastructure/rate_limit.py` and `redis_client.py::get_redis()` |
| `python-jose[cryptography]` | `>=3.3.0` [VERIFIED: pyproject.toml] | JWT encode/decode (HS256) | Already the codebase's only JWT library (`app/modules/auth/domain/tokens.py`) |
| `hashlib`/`hmac`/`secrets` (stdlib) | Python 3.13 stdlib [VERIFIED: pyproject.toml requires-python] | OTP hashing (HMAC-SHA256), constant-time compare | No install needed; already the pattern used in `rate_limit.py::_opaque_key` and `pagination/cursor.py` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `sqlalchemy[asyncio]` | already in use | Query `AuthOtpChallenge` by `(email, purpose, expires_at)` instead of by code value once hashed | SEC-05 — the current `WHERE code == :code` filter must become a fetch-then-compare in Python |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| HMAC-SHA256 for OTP hash | `bcrypt.hashpw` (reuse `senha.py`) | Consistent with password hashing, but its cost factor (~100–300ms/op, tuned for offline brute-force resistance of high-entropy user passwords) adds needless latency to every OTP confirm call and provides no real extra protection for a 6-digit, rate-limited, 15-min-TTL code — the actual defense against brute force is SEC-07 rate limiting + TTL, not hash slowness |
| Synchronous SMTP send for OTP | Route OTP through the existing `comunicacoes` delivery outbox (`processar_entregas_pendentes`, polled by Celery beat every `COMMUNICATION_DELIVERY_INTERVAL_SECONDS` = 30s default) | Outbox is durable/retryable but adds up to ~30s+ latency before the user's inbox — unacceptable for a code the user is actively waiting to type in during registration/recovery. Outbox is also keyed on `actor_id` (an authenticated internal user) and `Idempotency-Key`, a shape that doesn't fit an unauthenticated OTP request |
| Redis "signed-out-since" cutoff (SEC-06) | Per-`jti` denylist (store every issued refresh `jti`, delete/mark on sign-out) | Denylist is more precise (revokes exactly one session) but requires tracking every issued `jti` somewhere (currently nothing persists `jti` outside the token itself) — a per-user cutoff timestamp is a single Redis key per user, cheaper, and matches "revoke all vigente" wording in SEC-06 acceptably since the FE has one active session per browser context |

**Installation:** None — no new dependencies for this phase.

**Version verification:** All versions above were read directly from this project's `pyproject.toml` (an authoritative source for this repo), not fetched from PyPI — the phase introduces zero new packages, so registry-freshness checks against PyPI are not applicable here.

## Package Legitimacy Audit

**Not applicable — this phase installs no new external packages.** `slopcheck` was attempted (`pip show slopcheck` returned "not found"; install was not attempted since there is nothing to audit) and is not needed because every library used (`bcrypt`, `aiosmtplib`, `redis`, `python-jose`) is already a declared, in-use dependency of this project, confirmed by direct read of `pyproject.toml` and by finding each library already imported and exercised in `app/modules/auth/` or `app/modules/comunicacoes/` / `app/shared/infrastructure/`.

**Packages removed due to slopcheck [SLOP] verdict:** none (n/a — no new packages)
**Packages flagged as suspicious [SUS]:** none (n/a — no new packages)

## Architecture Patterns

### System Architecture Diagram

```
                         PROD boot
                             │
                 app/shared/config/settings.py
                 validate_cross_field_constraints()
                             │
        ┌────────────────────┴─────────────────────┐
        │ ENV == "PROD"?                            │
        │  - jwt_secret weak/placeholder → raise    │ (SEC-01, done)
        │  - seed_auth_on_startup True   → raise    │ (SEC-02 fail-fast, done)
        └────────────────────┬─────────────────────┘
                 default seed_auth_on_startup        (SEC-02 default flip — this phase)
                             │
   ┌─────────────────────────────────────────────────────────────────┐
   │                      POST /api/auth/register                    │
   │                      POST /api/auth/password/recovery           │
   └───────────────┬───────────────────────────────────┬─────────────┘
                    │ 1. rate-limit gate (SEC-07)        │
                    │    enforce_dual_fixed_window(       │
                    │      namespace="auth_otp_request")   │
                    ▼                                      ▼
        repositorio_otp.criar_desafio()          (user+IP fixed-window,
              │                                    fail-closed 503,
              │ generate 6-digit code               429 w/ Retry-After)
              │ HASH code (HMAC-SHA256) (SEC-05)
              │ persist AuthOtpChallenge.code = hash
              │
              ▼
      ENV == "PROD"?
        │yes                    │no (dev/staging)
        ▼                       ▼
  SmtpEmailSender.enviar()   logger.info(code)  (dev convenience, unchanged)
  (sync call, SEC-03)
        │
        ▼
   user's inbox

   ┌─────────────────────────────────────────────────────────────────┐
   │            POST /api/auth/register/confirm                      │
   │            POST /api/auth/password/recovery/confirm             │
   └───────────────┬───────────────────────────────────┬─────────────┘
                    │ 1. rate-limit gate (SEC-07)         │
                    │    enforce_dual_fixed_window(        │
                    │      namespace="auth_otp_confirm")    │
                    ▼                                       ▼
        repositorio_otp.verificar_desafio()        (user+IP fixed-window,
              │ fetch by (email, purpose, exp>=now)  brute-force-critical:
              │ HMAC-SHA256(candidate) == stored?     6-digit = 1e6 combos)
              │ (constant-time compare)
              ▼
          match / no match

   ┌─────────────────────────────────────────────────────────────────┐
   │                      POST /api/auth/sign-in                     │
   └───────────────┬───────────────────────────────────────────────┘
                    │ rate-limit gate (SEC-07, namespace="auth_sign_in")
                    ▼
             verify_password() (bcrypt, unchanged)

   ┌─────────────────────────────────────────────────────────────────┐
   │                     POST /api/auth/sign-out                     │
   │       (FE sends Authorization: Bearer <access_token>)           │
   └───────────────┬───────────────────────────────────────────────┘
                    │ Depends(get_current_user)  — decodes ACCESS token
                    ▼                              (already exists, no FE change)
        Redis SET "auth:signed_out_since:{user_id}" = now, EX 7d (SEC-06)
                    │
                    ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │                  POST /api/auth/token/refresh                   │
   └───────────────┬───────────────────────────────────────────────┘
                    │ decode_refresh_token() → payload has "iat" (new)
                    ▼
        Redis GET "auth:signed_out_since:{user_id}"
                    │
              iat < cutoff? → reject (SessaoInvalidaError, 401)
              iat >= cutoff / no cutoff → issue new tokens (unchanged)
```

### Recommended Project Structure (files touched, no new folders)

```
app/modules/auth/
├── application/
│   ├── casos_uso.py          # sign_out() gains real logic; sign_in/register/etc. gain rate-limit calls
│   └── schemas.py            # unchanged (SignOutResponse already exists)
├── domain/
│   ├── senha.py              # unchanged (bcrypt stays for passwords only)
│   └── otp_hash.py           # NEW — hash_otp_code()/verify_otp_code() (HMAC-SHA256)
├── infrastructure/
│   ├── repositorio_otp.py    # criar_desafio(): hash before insert, send email in PROD;
│   │                         # verificar_desafio(): fetch-then-compare instead of WHERE code==
│   ├── models.py             # AuthOtpChallenge.code stays String(6+) — hex digest is longer, widen column
│   └── http/
│       ├── dependencies.py   # unchanged (get_current_user already decodes access token)
│       └── routes.py         # sign_out gains Depends(get_current_user); rate-limit checks added to
│                              # sign-in/register/confirm/recovery routes
docs/
└── seguranca.md              # NEW — SEC-04 runbook (seed user rotation/removal)
```

### Pattern 1: Rate limiting auth endpoints (SEC-07)

**What:** Reuse `enforce_dual_fixed_window` exactly as `comunicacoes` does, with a distinct `namespace` per auth endpoint group and `user_identity` = the normalized target email (not an authenticated actor — these endpoints are unauthenticated).
**When to use:** `sign-in`, `register`, `register/confirm`, `password/recovery`, `password/recovery/confirm`.
**Example (adapted from `comunicacoes/infrastructure/http/routes.py`):**
```python
# Source: app/modules/comunicacoes/infrastructure/http/routes.py (existing pattern in this repo)
from app.shared.infrastructure.rate_limit import (
    RateLimitUnavailableError,
    enforce_dual_fixed_window,
)
from app.shared.infrastructure.redis_client import get_redis

client_ip = request.client.host if request.client else "unknown"
settings = get_settings()
try:
    rate = await enforce_dual_fixed_window(
        get_redis(),
        namespace="auth_sign_in",          # distinct per endpoint group
        user_identity=normalizar_email(body.identifier),
        ip_identity=client_ip,
        secret=settings.jwt_secret,        # same secret already used for HMAC-opaque keys
        user_limit=5,                      # tune per endpoint — see Common Pitfalls
        ip_limit=20,
    )
except RateLimitUnavailableError as exc:
    raise HTTPException(status_code=503, detail="...", headers={"Retry-After": "30"}) from exc
if not rate.allowed:
    raise HTTPException(status_code=429, detail="...", headers={"Retry-After": str(rate.retry_after)})
```
**Suggested limits (recommendation, not verified against a compliance standard):**
| Endpoint | user_limit/min | ip_limit/min | Rationale |
|---|---|---|---|
| `sign-in` | 5 | 20 | Matches existing SMTP send limits; password guessing |
| `register/confirm`, `password/recovery/confirm` | 5 | 20 | Highest priority — brute-forcing a 6-digit code (1e6 space); 5/min keeps a full brute force well beyond the 15-min TTL |
| `register`, `password/recovery` | 3 | 10 | Lower — these trigger an OTP email send (abuse/flood/cost surface), not a guess surface |

### Pattern 2: OTP hashing with fetch-then-compare (SEC-05)

**What:** Store `HMAC-SHA256(code, key=settings.jwt_secret)` instead of the plaintext code; query changes from `WHERE code == :code` to `WHERE email, purpose, expires_at >= now` (at most one row, since `remover_desafios` already deletes prior challenges before creating a new one), then compare in Python with `hmac.compare_digest`.
**When to use:** `repositorio_otp.py::criar_desafio` (hash before insert) and `verificar_desafio` (fetch + compare).
**Example:**
```python
# New: app/modules/auth/domain/otp_hash.py
import hmac
import hashlib

def hash_otp_code(code: str, *, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()

def verify_otp_code(candidate: str, *, stored_hash: str, secret: str) -> bool:
    return hmac.compare_digest(hash_otp_code(candidate, secret=secret), stored_hash)
```
```python
# repositorio_otp.py::verificar_desafio (shape change)
row = await session.scalar(
    select(AuthOtpChallenge)
    .where(
        AuthOtpChallenge.email == email,
        AuthOtpChallenge.purpose == purpose.value,
        AuthOtpChallenge.expires_at >= now,
    )
    .order_by(AuthOtpChallenge.id.desc())
    .limit(1)
)
if row is None or not verify_otp_code(code, stored_hash=row.code, secret=settings.jwt_secret):
    return None
return row
```
**Column width note:** `AuthOtpChallenge.code` is `String(6)` today; a hex SHA-256 digest is 64 chars. This requires an Alembic migration widening the column (e.g., `String(64)`), not just an application change.

### Pattern 3: Sign-out revocation via Redis cutoff (SEC-06)

**What:** On sign-out, write `auth:signed_out_since:{user_id} = now` to Redis with a TTL >= the refresh token's max lifetime (7 days, per `create_refresh_token`). On refresh, add an `iat` claim to the refresh token and reject if `iat < cutoff`.
**When to use:** `sign_out` use case + `refresh_token` use case.
**Example:**
```python
# app/modules/auth/domain/tokens.py::create_refresh_token — add iat
def create_refresh_token(*, user_id: int, email: str, roles: list[str]) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(days=7)
    return _encode_token({
        "typ": "refresh", "sub": str(user_id), "email": email, "roles": roles,
        "jti": uuid4().hex, "iat": int(now.timestamp()), "exp": expire,
    })
```
```python
# casos_uso.py::sign_out — needs CurrentUser now (route gains Depends(get_current_user))
async def sign_out(current_user: CurrentUser) -> SignOutResponse:
    redis = get_redis()
    await redis.set(f"auth:signed_out_since:{current_user.id}", str(int(time.time())), ex=7 * 86_400)
    return SignOutResponse()
```
```python
# casos_uso.py::refresh_token — check cutoff
payload = decode_refresh_token(body.refresh_token)
cutoff = await get_redis().get(f"auth:signed_out_since:{payload['sub']}")
if cutoff is not None and int(payload.get("iat", 0)) < int(cutoff):
    raise SessaoInvalidaError()
```
**FE contract note:** `frontend/lib/api/auth-backend.ts::postSignOut` already sends `Authorization: Bearer {accessToken}` and treats non-200 as best-effort (`console.warn`, doesn't throw). Adding `Depends(get_current_user)` to the `sign-out` route requires **no frontend change** — the header is already sent. Route signature changes from `sign_out(db)` to `sign_out(db, current_user: CurrentUser = Depends(get_current_user))`.

### Pattern 4: Synchronous OTP email delivery (SEC-03)

**What:** Reuse the low-level `SmtpEmailSender` adapter (not the full delivery-outbox orchestration) to send the OTP email synchronously from within the `criar_desafio` call path, only when `ENV == "PROD"` (dev/staging keep logging the code, unchanged).
**When to use:** `repositorio_otp.py::criar_desafio`.
**Example:**
```python
# Source: app/modules/comunicacoes/infrastructure/email_sender.py (existing adapter, reused directly)
from app.modules.comunicacoes.infrastructure.email_sender import SmtpEmailSender
from app.modules.comunicacoes.domain.modelos import EntregaEmail  # or an equivalent lightweight value object
from app.modules.comunicacoes.application.ports import FalhaEntregaEmail

if ENV == "PROD":
    sender = SmtpEmailSender(get_settings())
    try:
        await sender.enviar(EntregaEmail(recipient=email, subject="Seu código", content=f"Código: {code}", message_id=...))
    except FalhaEntregaEmail:
        logger.exception("Falha ao enviar OTP por e-mail para %s", email)
        raise  # or degrade gracefully — see Open Questions
    logger.info("OTP %s enviado por e-mail para %s", purpose.value, email)
else:
    logger.info("OTP %s gerado para %s: %s", purpose.value, email, code)
```
**Design note:** `EntregaEmail` in `comunicacoes/domain/modelos.py` may carry outbox-specific fields (delivery id, attempt count) not relevant to OTP — check its exact shape before reusing the dataclass directly; a minimal parallel value object in `auth` may be cleaner than forcing OTP through a `comunicacoes`-shaped type. This is a judgment call for the planner/implementer, not a blocking research finding.

### Anti-Patterns to Avoid

- **Routing OTP through the delivery outbox:** Adds 0–30s+ latency (poll interval) and forces an unauthenticated flow through an idempotency-key/actor_id model designed for authenticated internal senders. Use the SMTP adapter directly instead.
- **Bcrypt for OTP hashing:** Designed for high-entropy human passwords under offline attack; adds latency with no matching threat model for a 6-digit, rate-limited, 15-min code. Use HMAC-SHA256 (matches existing codebase pattern).
- **Filtering `WHERE code == :code` on a hashed column:** Once hashed, the code is not equality-searchable in a meaningful way without recomputing the same hash server-side per candidate — but since remover_desafios guarantees at most one active row per (email, purpose), fetch that one row and compare in Python; don't try to keep SQL-side equality filtering.
- **Per-jti denylist without a queryable index of issued tokens:** Nothing today persists issued `jti` values outside the token itself; building a "check if jti is revoked" system means either persisting every jti (new table) or accepting the simpler per-user cutoff. Don't half-build a denylist that can't actually look anything up.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Rate limiting sign-in/OTP endpoints | A new in-memory or ad-hoc Redis counter | `app/shared/infrastructure/rate_limit.py::enforce_dual_fixed_window` | Already atomic (Lua script), already handles fail-closed on Redis errors, already opaque-keys identities via HMAC, already tested (`test_comunicacoes.py`) |
| SMTP sending/error classification | A new email client wrapper | `app/modules/comunicacoes/infrastructure/email_sender.py::SmtpEmailSender` | Already classifies terminal vs transient vs unknown SMTP failures correctly (auth errors, 4xx/5xx responses, timeouts vs disconnects) |
| Constant-time secret comparison | Custom byte-by-byte comparison loop | `hmac.compare_digest` (stdlib) | Purpose-built to avoid timing side-channels; reinventing this is a classic security bug source |
| Token signing/verification | Custom JWT logic | `python-jose` (already the only JWT lib in this codebase) | `jwt.encode`/`jwt.decode` already handle exp validation, algorithm confusion prevention |

**Key insight:** Every piece of this phase has a working analog already in the codebase, proven in `comunicacoes` or `rate_limit.py`. The job is disciplined reuse, not invention.

## Common Pitfalls

### Pitfall 1: Test helper `ler_otp()` reads the DB column directly — breaks under SEC-05

**What goes wrong:** `app/tests/test_auth_flows.py::_ler_otp_async` does `select(AuthOtpChallenge)...; return row.code`, i.e. it reads the raw column value to get the plaintext code for use in subsequent `confirm` calls in tests. Once SEC-05 hashes `code` before storage, this helper returns a hash, and every test that depends on `ler_otp()` (`test_register_existente_nao_confirmado_reenvia_otp`, the register/confirm flow test, the recovery flow test) will silently receive garbage and fail confusingly.
**Why it happens:** The test bypasses the API's OTP delivery path entirely and reads the DB directly, which was fine when the DB stored plaintext.
**How to avoid:** Update the test helper to capture the plaintext code the same way a human would in non-PROD — via the `logger.info(...code)` line already emitted by `criar_desafio` when `ENV != "PROD"` (use `caplog` to capture it), or thread the return value of `criar_desafio` through a test-only seam. This is a required, not optional, change alongside the SEC-05 implementation — flag explicitly in the plan so it isn't discovered late as a test failure.
**Warning signs:** Any OTP-flow test failing with "Invalid confirmation code" after SEC-05 lands, with no other code change nearby.

### Pitfall 2: Confusing "OTP delivery mechanism" with "OTP delivery durability model"

**What goes wrong:** It's tempting to reuse the full `comunicacoes` delivery-outbox (`agendar_comunicacao` → Celery beat polls every 30s → `processar_entregas_pendentes` → SMTP) for SEC-03 because "it's the existing e-mail sending path." But that path is optimized for durability/retryability of a fire-and-forget internal notification, not for a code the user is staring at their inbox waiting for.
**Why it happens:** Surface-level pattern matching ("we already send email somewhere, reuse it") without checking the latency budget.
**How to avoid:** Call `SmtpEmailSender.enviar()` (or an equivalent minimal wrapper) synchronously and inline in the request path that generates the OTP, not through the outbox. Accept the SMTP timeout (`SMTP_TIMEOUT_SECONDS`, default 10s) as part of the request's response time for `register`/`password/recovery`.
**Warning signs:** OTP emails consistently arriving 10–30+ seconds after the register/recovery API call returns 201/200.

### Pitfall 3: Rate-limiting failures unhandled = accidental account lockout amplification

**What goes wrong:** `enforce_dual_fixed_window` raises `RateLimitUnavailableError` when Redis is unreachable. The existing `comunicacoes` pattern fails **closed** (503) in that case. If auth adopts the same fail-closed behavior for `sign-in`, a Redis outage means **nobody can log in** — a much higher blast radius than "nobody can send one internal comm."
**Why it happens:** Copy-pasting the fail-closed pattern without weighing that sign-in is the single most critical path in the app.
**How to avoid:** This is a real tradeoff, not a pure copy-paste: decide explicitly (and document the decision) whether auth rate-limit-unavailable should fail closed (503, matches `comunicacoes` convention, but riskier for uptime) or fail open with a warning log (available, but leaves auth briefly unprotected during a Redis outage). Flag as an Open Question for the planner/discuss-phase rather than silently picking one.
**Warning signs:** A Redis blip taking down the ability to sign in app-wide, discovered only in an incident.

### Pitfall 4: `.env.example` still ships `SEED_AUTH_ON_STARTUP=true`

**What goes wrong:** Flipping the Pydantic field default to `False` (SEC-02) without also updating `.env.example` leaves the example file contradicting the new safe default, confusing new setups and defeating half the purpose of the fix (docs-as-config drift).
**Why it happens:** The example file isn't code, easy to forget in a code-focused review.
**How to avoid:** Update `.env.example` line 159 (`SEED_AUTH_ON_STARTUP=true` → `SEED_AUTH_ON_STARTUP=false`) as part of the same change; note that local dev setups relying on the seeded users will need to explicitly opt in going forward — coordinate with SEC-04's runbook.
**Warning signs:** New developer onboarding breaks ("no seed users") after this phase ships, without documentation explaining why.

### Pitfall 5: Widening `AuthOtpChallenge.code` requires a migration, not just a model edit

**What goes wrong:** Changing `code: Mapped[str] = mapped_column(String(6), ...)` to a wider type in `models.py` without a corresponding Alembic migration will pass locally against a dev DB that autocreated the wrong-width column, then fail in any environment where migrations are actually applied via `alembic upgrade head` (which is how this app boots — see `.docker/Dockerfile` CMD).
**How to avoid:** Generate a migration (`alembic revision --autogenerate` after the model change, then hand-verify it — this codebase's `alembic/env.py` imports every model for exactly this reason) widening `auth_otp_challenges.code` to fit a hex digest (64 chars for SHA-256).
**Warning signs:** `StringDataRightTruncation` / `value too long for type character varying(6)` errors in any environment past local dev.

## Code Examples

### Existing rate-limit usage (verified in this repo)
```python
# Source: app/modules/comunicacoes/infrastructure/http/routes.py:189-221 (read directly, this session)
rate = await enforce_dual_fixed_window(
    get_redis(),
    namespace="smtp",
    user_identity=str(actor.id),
    ip_identity=client_ip,
    secret=settings.jwt_secret,
    user_limit=_SMTP_USER_RATE_PER_MINUTE,   # 5
    ip_limit=_SMTP_IP_RATE_PER_MINUTE,        # 20
)
```

### Existing HMAC-opaque-key pattern (the precedent for SEC-05's hashing approach)
```python
# Source: app/shared/infrastructure/rate_limit.py:51-57 (read directly, this session)
def _opaque_key(*, namespace: str, scope: str, identity: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{scope}:{identity}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"automation:rate:{namespace}:{scope}:{digest}"
```

### Existing PROD fail-fast pattern (the precedent for SEC-02's default flip's *sibling* fail-fast, already done)
```python
# Source: app/shared/config/settings.py:594-595 (read directly, this session — SEC-02's fail-fast half, already implemented)
if self.seed_auth_on_startup:
    raise ValueError("SEED_AUTH_ON_STARTUP deve ser false em PROD")
```
The remaining SEC-02 work is exclusively the default value change at line 432-434:
```python
# Current — app/shared/config/settings.py:432-434
seed_auth_on_startup: bool = Field(
    default=True, validation_alias="SEED_AUTH_ON_STARTUP"
)
# Target: default=False
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| OTP code stored plaintext, `WHERE code == :code` | Hashed (HMAC-SHA256), fetch-by-(email,purpose,expiry) then compare | This phase (SEC-05) | Query shape changes in `verificar_desafio`; DB leak no longer exposes usable codes |
| `sign_out` is a stateless no-op | Redis-backed per-user revocation cutoff | This phase (SEC-06) | `sign_out` route needs `Depends(get_current_user)`; refresh tokens need `iat` |
| No rate limit in `auth` | Reuse `enforce_dual_fixed_window` per auth endpoint | This phase (SEC-07) | New Redis keys under `automation:rate:auth_*` namespaces |
| OTP only logged in PROD (never delivered) | Synchronous SMTP send in PROD | This phase (SEC-03) | Adds SMTP round-trip latency (bounded by `SMTP_TIMEOUT_SECONDS`, default 10s) to register/recovery request path |

**Deprecated/outdated:** None — this phase closes gaps, it doesn't replace a working mechanism with a different one (except the OTP storage query shape).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | HMAC-SHA256 keyed by `settings.jwt_secret` is the right hashing approach for OTP codes (rather than bcrypt) | Standard Stack / Alternatives Considered / Pattern 2 | If wrong, planner should switch to bcrypt (trivial code change, `senha.py` pattern) at the cost of added latency per OTP verify — not a security regression either way, just a design preference; low risk |
| A2 | Reusing `settings.jwt_secret` as the OTP HMAC key (rather than a dedicated new secret) is acceptable | Pattern 2, Pattern 1 | If wrong (e.g., security review wants key separation), a new `OTP_HASH_SECRET` env var would need to be added to `Settings` + `.env.example` — small, non-breaking addition, but changes the "no new config" framing of this research |
| A3 | A per-user Redis "signed-out-since" cutoff is an acceptable interpretation of SEC-06's "revoga o(s) refresh token(s) vigente(s)" (vs. a precise per-token/per-jti denylist) | Pattern 3 / Alternatives Considered | If the intent was strictly single-session revocation (not "log out everywhere"), this design revokes more than necessary in a multi-device scenario. Given this app's FE (single session per browser, no visible multi-device session list), likely acceptable, but not confirmed with the user |
| A4 | Rate-limit-unavailable behavior for `sign-in` should be decided explicitly (fail-open vs fail-closed), not assumed to match `comunicacoes`'s fail-closed default | Pitfall 3, Open Questions | If fail-closed is copied without discussion, a Redis outage becomes a full login outage — a materially bigger blast radius than the existing precedent |
| A5 | Synchronous, in-request SMTP failure for OTP send should probably not hard-fail the register/recovery request (degrade gracefully with a retry/resend path) — not confirmed | Pattern 4 / Open Questions | If SMTP failures are made to hard-fail registration, a flaky mail provider blocks new user signups entirely; if they're silently swallowed, users never get their code with no visible error |

**If this table is empty:** N/A — table is populated; the above are the specific design points needing user/planner confirmation before locking task details.

## Open Questions

1. **Should sign-in rate-limit-unavailable fail open or fail closed?**
   - What we know: the existing `comunicacoes` precedent fails closed (503) on `RateLimitUnavailableError`.
   - What's unclear: whether that same posture is acceptable for `sign-in`, given the much larger blast radius (all logins blocked vs. one feature blocked) during a Redis outage.
   - Recommendation: raise explicitly in `/gsd-discuss-phase` for this phase; default suggestion is fail-closed for OTP-confirm endpoints (brute-force-critical, lower traffic) and consider fail-open-with-warning for `sign-in` specifically (highest traffic, highest availability cost) — but this is a product/risk decision, not a technical one.

2. **Should a failed OTP email send hard-fail `register`/`password/recovery`, or degrade gracefully?**
   - What we know: `SmtpEmailSender.enviar()` raises typed exceptions (`FalhaTerminalEntrega`, `FalhaTransitoriaEntrega`, `ResultadoIncertoEntrega`) that the outbox module already knows how to interpret for retry decisions.
   - What's unclear: outside the outbox's retry loop, there's no established "what happens on send failure" contract for a synchronous caller.
   - Recommendation: log the failure and still return the existing `RegisterResponse`/`PasswordRecoveryResponse` (treat as best-effort, consistent with the anti-enumeration behavior already in `request_password_recovery`, which never reveals whether an email exists) — but confirm this doesn't contradict the plaintext requirement ("OTP nunca é logado em PROD") if the send fails and there's no fallback delivery.

3. **What exact shape should the OTP email content take (branding, language, "this code expires in 15 minutes" copy)?**
   - What we know: `comunicacoes` module templates are for a different audience/purpose ("Comunicar Time Comercial").
   - What's unclear: whether there's an existing brand/copy standard for user-facing transactional email for the project.
   - Recommendation: keep minimal/plain-text for this phase (matches `EmailMessage.set_content` pattern in `SmtpEmailSender`) unless the user specifies otherwise — not a security-relevant decision, low risk either way.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Redis | SEC-06 (revocation cutoff), SEC-07 (rate limit) | Assumed ✓ (already required by existing `comunicacoes` rate limit + Celery broker; not re-probed this session since it's a hard existing runtime dependency, not new) | `redis>=5.2.0` (pyproject) | None — Redis is already load-bearing infrastructure for this app (Celery broker/backend); no fallback needed or expected |
| SMTP server | SEC-03 (OTP email delivery) | Configured in `.env.example` (Gmail for dev/test, Microsoft 365 documented for prod) but **not connectivity-tested this session** | — | If `smtp_configured` is `False`, `SmtpEmailSender.enviar()` already raises `FalhaTerminalEntrega("smtp_not_configured")` — planner should decide whether PROD boot should fail-fast if SMTP isn't configured (parallel to the existing JWT/seed fail-fast checks), given SEC-03's intent that PROD OTP must be delivered by email |

**Missing dependencies with no fallback:** None identified as blocking — both Redis and SMTP are pre-existing, already-required infrastructure for this app, not new to this phase.

**Missing dependencies with fallback:** None — see above; the "fallback" for missing SMTP config is arguably that PROD boot should fail fast (open question, see below), not a graceful runtime fallback.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3+ / pytest-asyncio 0.24+ (`asyncio_mode = "auto"`) [VERIFIED: pyproject.toml] |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (`testpaths = ["app/tests"]`) |
| Quick run command | `uv run pytest app/tests/test_auth_flows.py -q` |
| Full suite command | `uv run pytest -q` |

Tests connect to a real, isolated Redis DB 15 (`app/tests/conftest.py::_configure_isolated_test_redis`) and a real isolated Postgres — not mocked — matching the existing `test_comunicacoes.py` rate-limit test pattern (`_FakeRateRedis` is used only for the pure-function `enforce_dual_fixed_window` unit test, not for route-level tests).

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SEC-02 | Default `seed_auth_on_startup` is `False`; PROD still fails if `True` | unit | `uv run pytest app/tests/test_settings*.py -k seed -x` | ❌ Wave 0 — no existing settings test file found this session; may need a new `test_settings_seed_default.py` or an addition to an existing settings test module (search for `test_settings` files before creating new) |
| SEC-03 | OTP delivered via SMTP in PROD; never logged in PROD | unit/integration | `uv run pytest app/tests/test_auth_flows.py -k otp_email -x` | ❌ Wave 0 — needs a test that monkeypatches `ENV`/`SmtpEmailSender` to assert `enviar()` is called and the code never appears in PROD logs |
| SEC-04 | Runbook documents seed-user rotation/removal | manual-only | n/a (documentation review) | n/a — no automated test applies to a doc; verify via review checklist in plan |
| SEC-05 | OTP stored as HMAC hash, not plaintext; verify still works | unit | `uv run pytest app/tests/test_auth_flows.py -k otp -x` | ✅ existing `test_auth_flows.py` OTP tests exist but **must be updated** for Pitfall 1 (`ler_otp()` helper) as part of this same change, not as an afterthought |
| SEC-06 | Sign-out revokes refresh; reuse after sign-out rejected | integration | `uv run pytest app/tests/test_auth_flows.py -k sign_out -x` | ❌ Wave 0 — existing `test_sign_out_retorna_status_signed_out` only checks the no-op response shape; needs a new test asserting a refresh-after-sign-out is rejected |
| SEC-07 | Rate limit blocks excess sign-in/OTP-confirm attempts, returns 429/503 with `Retry-After` | integration | `uv run pytest app/tests/test_auth_flows.py -k rate_limit -x` | ❌ Wave 0 — no existing auth rate-limit test; model directly on `test_comunicacoes.py::test_rate_limit_bloqueia_nova_intencao_mas_permite_replay` and `test_rate_limit_indisponivel_bloqueia_nova_intencao_mas_nao_replay` |

### Sampling Rate
- **Per task commit:** `uv run pytest app/tests/test_auth_flows.py -q`
- **Per wave merge:** `uv run pytest -q` (full suite — this phase touches shared `Settings` validation, which other modules' tests may implicitly depend on)
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] Update `app/tests/test_auth_flows.py::_ler_otp_async`/`ler_otp` to read the plaintext code via log capture (`caplog`) instead of the raw DB column — required before any SEC-05 test can pass (see Pitfall 1)
- [ ] New test(s) for SEC-02 default value (`seed_auth_on_startup` defaults `False` when unset) — locate or create the appropriate `test_settings*.py`
- [ ] New test(s) for SEC-03 (PROD sends email, never logs code; non-PROD unchanged)
- [ ] New test(s) for SEC-06 (refresh rejected after sign-out; `iat` present in refresh payload)
- [ ] New test(s) for SEC-07 (429 with `Retry-After` on excess sign-in/otp-confirm attempts; 503 fail behavior per Open Question 1's resolution)
- [ ] Alembic migration + `alembic upgrade head` check for widened `auth_otp_challenges.code` column (SEE Pitfall 5) — verify via `app/tests/test_migration_*.py` pattern already used elsewhere in this repo (e.g. `test_migration_026_realtime_active_ledger.py`)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | JWT (HS256, `python-jose`) for session tokens; bcrypt for passwords (unchanged, out of scope) |
| V3 Session Management | yes | Refresh token revocation via Redis cutoff (SEC-06); access tokens remain valid up to their 8h `JWT_EXPIRE_MINUTES=480` TTL after sign-out — **not covered by SEC-06's literal scope** (refresh-only), flagged as residual risk below |
| V4 Access Control | partial | RBAC via `require_min_role` (unchanged, out of scope for this phase) |
| V5 Input Validation | yes | Pydantic `Field(pattern=r"^\d{6}$")` already validates OTP code shape on the request schemas (unchanged) |
| V6 Cryptography | yes | HMAC-SHA256 (stdlib `hmac`/`hashlib`) for OTP hashing — never hand-roll comparison, always `hmac.compare_digest` |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| OTP brute force (1e6 combos, no rate limit today) | Tampering / Elevation of Privilege | SEC-07 rate limit on confirm endpoints + 15-min TTL (already exists) |
| Refresh token replay after logout | Repudiation / Elevation of Privilege | SEC-06 Redis cutoff check on every refresh |
| Plaintext OTP in DB readable by anyone with DB read access | Information Disclosure | SEC-05 HMAC hashing |
| Credential stuffing on sign-in (no rate limit today) | Elevation of Privilege | SEC-07 rate limit on sign-in |
| **Residual, out of scope this phase:** access token remains valid up to 8h after sign-out | Repudiation | Not addressed by SEC-06 as literally scoped (refresh-only); flagged as Open Question-adjacent residual risk — a cheap future extension would check the same Redis cutoff inside `get_current_user`, since it already round-trips to the DB per request |

## Sources

### Primary (HIGH confidence — direct code read, this session)
- `app/shared/config/settings.py` — seed/JWT fail-fast validators, SMTP settings, ENV handling
- `app/modules/auth/infrastructure/repositorio_otp.py` — current OTP generation/verification
- `app/modules/auth/infrastructure/models.py` — `AuthOtpChallenge` schema
- `app/modules/auth/domain/senha.py`, `app/modules/auth/domain/tokens.py`, `app/modules/auth/infrastructure/security.py` — password hashing, JWT creation/decoding
- `app/modules/auth/application/casos_uso.py`, `app/modules/auth/infrastructure/http/routes.py`, `app/modules/auth/infrastructure/http/dependencies.py` — auth use cases, routes, RBAC dependency
- `app/shared/infrastructure/rate_limit.py`, `app/modules/comunicacoes/infrastructure/http/routes.py` — existing rate-limit pattern and its production usage
- `app/modules/comunicacoes/infrastructure/email_sender.py`, `app/modules/comunicacoes/application/entregas.py`, `app/modules/comunicacoes/application/casos_uso.py` — existing SMTP adapter and delivery-outbox orchestration
- `app/shared/infrastructure/redis_client.py` — shared Redis client factory
- `app/modules/auth/bootstrap/seed.py` — seed user list/passwords (informs SEC-04 runbook content)
- `app/tests/test_auth_flows.py` — existing auth test conventions, including the `ler_otp()` helper that breaks under SEC-05
- `app/tests/test_comunicacoes.py`, `app/tests/conftest.py` — existing rate-limit test pattern, isolated test Redis convention
- `pyproject.toml` — declared dependency versions, pytest config
- `.env.example` — current env var defaults (`SEED_AUTH_ON_STARTUP=true`, SMTP config)
- `docs/index.md`, `docs/api.md` — doc structure convention for SEC-04 runbook placement
- `frontend/lib/api/auth-backend.ts` — confirms FE sign-out contract (sends access token only, best-effort)
- `.planning/REQUIREMENTS.md`, `.planning/milestones/v1.2-PLATFORM-HARDENING.md`, `.planning/STATE.md` — SEC-01..07 requirement text and status
- `.planning/codebase/CONVENTIONS.md`, `.planning/codebase/CONCERNS.md` — naming/style conventions, prior security concerns

### Secondary (MEDIUM confidence — WebSearch, cross-checked against OWASP project pages)
- OWASP Password Storage Cheat Sheet (via WebSearch summary) — Argon2id/bcrypt/PBKDF2 guidance for password storage; used to justify *not* applying the same guidance mechanically to OTP codes, since OTPs are a different threat model (short-lived, rate-limited, low-entropy-by-design) than user-chosen passwords
- OWASP ASVS references on OTP/anti-automation rate limiting (via WebSearch summary) — general confirmation that rate limiting is the standard mitigation for OTP brute force, not hash cost factor

### Tertiary (LOW confidence)
- None used as a basis for recommendations in this document.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — every library is already installed and already used for an analogous purpose in this exact codebase; zero external verification needed
- Architecture: HIGH — every pattern (rate limit, SMTP adapter, Redis client, RBAC dependency) was read directly from working code in this repo, not inferred
- OTP hashing algorithm choice (HMAC vs bcrypt): MEDIUM — grounded in codebase precedent + general OWASP guidance, but is a judgment call, not a documented standard for this exact case (flagged in Assumptions Log as A1)
- Sign-out revocation design (per-user cutoff vs per-jti denylist): MEDIUM — reasoned from FE contract constraints found in this session, but not confirmed with the user (A3)
- Pitfalls: HIGH — Pitfall 1 (test helper) and Pitfall 5 (migration) are directly observed in the code, not speculative

**Research date:** 2026-08-12
**Valid until:** 30 days (stable internal codebase; no fast-moving external dependency risk since no new packages are introduced)
