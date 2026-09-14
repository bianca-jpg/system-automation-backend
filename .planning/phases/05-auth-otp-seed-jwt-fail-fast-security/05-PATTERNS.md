# Phase 5: Auth / OTP / seed / JWT fail-fast security - Pattern Map

**Mapped:** 2026-08-12
**Files analyzed:** 9 (7 modified, 2 new code files, 1 new doc, 1 new migration)
**Analogs found:** 8 / 9 (docs/seguranca.md has no code analog — content sourced from `seed.py` + REQUIREMENTS.md instead)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/modules/auth/domain/otp_hash.py` (NEW) | utility (crypto) | transform | `app/shared/infrastructure/rate_limit.py::_opaque_key` | role-match (same HMAC-SHA256 shape, different module) |
| `app/modules/auth/infrastructure/repositorio_otp.py` | service/repository | CRUD + event-driven (email send) | itself (existing file) + `app/modules/comunicacoes/infrastructure/email_sender.py` for the send call | exact (same file) / role-match (email send) |
| `app/modules/auth/infrastructure/models.py` | model | CRUD | itself (existing file) | exact |
| `app/modules/auth/application/casos_uso.py` | service (use cases) | request-response | itself (existing file) + `app/modules/comunicacoes/infrastructure/http/routes.py` for rate-limit call shape | exact / role-match |
| `app/modules/auth/domain/tokens.py` | domain (JWT) | transform | itself (existing file) | exact |
| `app/modules/auth/infrastructure/http/routes.py` | route/controller | request-response | itself (existing file) + `app/modules/comunicacoes/infrastructure/http/routes.py` (rate-limit wiring) | exact / role-match |
| `app/shared/config/settings.py` | config | transform | itself (existing file) | exact |
| `.env.example` | config | — | itself (existing file) | exact |
| new Alembic migration (widen `auth_otp_challenges.code`) | migration | batch/DDL | `alembic/versions/028_remove_colunas_ingestao_sem_leitor.py` (most recent) | exact (style/convention) |
| `app/tests/test_auth_flows.py` | test | request-response | itself (existing file) | exact |
| `docs/seguranca.md` (NEW) | config/doc | — | `app/modules/auth/bootstrap/seed.py` (content source, not code pattern) | no code analog |

## Pattern Assignments

### `app/modules/auth/domain/otp_hash.py` (NEW) — utility, transform

**Analog:** `app/shared/infrastructure/rate_limit.py` (lines 51-57)

**Existing HMAC pattern to mirror:**
```python
# Source: app/shared/infrastructure/rate_limit.py:51-57
def _opaque_key(*, namespace: str, scope: str, identity: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{scope}:{identity}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return f"automation:rate:{namespace}:{scope}:{digest}"
```

**New file shape (from RESEARCH.md Pattern 2, ready to implement as-is):**
```python
import hmac
import hashlib

def hash_otp_code(code: str, *, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()

def verify_otp_code(candidate: str, *, stored_hash: str, secret: str) -> bool:
    return hmac.compare_digest(hash_otp_code(candidate, secret=secret), stored_hash)
```

**Convention notes:**
- Follow existing module docstring style used across `app/modules/auth/domain/*.py` (`senha.py` has a one-line module purpose docstring — mirror that).
- Constant-time compare via stdlib `hmac.compare_digest` — never hand-roll (see Don't Hand-Roll in RESEARCH.md).
- Key is `settings.jwt_secret`, obtained via `get_settings()` at the call site in `repositorio_otp.py`, not inside `otp_hash.py` (keep this module pure/stateless, matching `_opaque_key`'s signature style of taking `secret` as a parameter, not reading settings itself).

---

### `app/modules/auth/infrastructure/repositorio_otp.py` — service/repository, CRUD + event-driven

**Analog:** itself (existing file, full content read) + `app/modules/comunicacoes/infrastructure/email_sender.py` for the SMTP call shape.

**Current file in full (baseline to diff against):**
```python
# Source: app/modules/auth/infrastructure/repositorio_otp.py (current, full file, 73 lines)
import logging
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.infrastructure.models import AuthOtpChallenge, OtpPurpose
from app.shared.config.settings import ENV

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 15


async def criar_desafio(
    session: AsyncSession, *, email: str, purpose: OtpPurpose,
) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = datetime.now(UTC) + timedelta(minutes=OTP_TTL_MINUTES)
    await remover_desafios(session, email=email, purpose=purpose)
    session.add(
        AuthOtpChallenge(email=email, code=code, purpose=purpose.value, expires_at=expires_at)
    )
    await session.flush()
    if ENV != "PROD":
        logger.info("OTP %s gerado para %s: %s", purpose.value, email, code)
    else:
        logger.info("OTP %s gerado para %s", purpose.value, email)
    return code


async def verificar_desafio(
    session: AsyncSession, *, email: str, purpose: OtpPurpose, code: str,
) -> AuthOtpChallenge | None:
    now = datetime.now(UTC)
    return await session.scalar(
        select(AuthOtpChallenge)
        .where(
            AuthOtpChallenge.email == email,
            AuthOtpChallenge.purpose == purpose.value,
            AuthOtpChallenge.code == code,   # <-- must change: fetch-then-compare (SEC-05)
            AuthOtpChallenge.expires_at >= now,
        )
        .order_by(AuthOtpChallenge.id.desc())
        .limit(1)
    )
```

**Target shape for `criar_desafio` (hash + PROD email, SEC-03 + SEC-05):**
```python
code = f"{secrets.randbelow(1_000_000):06d}"
settings = get_settings()
hashed = hash_otp_code(code, secret=settings.jwt_secret)
await remover_desafios(session, email=email, purpose=purpose)
session.add(
    AuthOtpChallenge(email=email, code=hashed, purpose=purpose.value, expires_at=expires_at)
)
await session.flush()
if ENV == "PROD":
    sender = SmtpEmailSender(settings)
    try:
        await sender.enviar(...)  # minimal value object — see Design note below
    except FalhaTerminalEntrega, FalhaTransitoriaEntrega:
        logger.exception("Falha ao enviar OTP por e-mail para %s", email)
        # degrade gracefully per RESEARCH.md Open Question 2 — do not log the code
else:
    logger.info("OTP %s gerado para %s: %s", purpose.value, email, code)
return code
```

**Target shape for `verificar_desafio` (fetch-then-compare, SEC-05):** see RESEARCH.md Pattern 2 (lines 236-249) — query drops the `code ==` filter, fetches the single active row by `(email, purpose, expires_at >= now)`, then compares in Python with `verify_otp_code`.

**SMTP send analog (`SmtpEmailSender`):**
```python
# Source: app/modules/comunicacoes/infrastructure/email_sender.py:12-35 (grepped, this session)
from app.modules.comunicacoes.infrastructure.email_sender import SmtpEmailSender
# raises FalhaTerminalEntrega("smtp_not_configured") if not settings.smtp_configured
# raises FalhaTerminalEntrega("smtp_authentication") / FalhaTransitoriaEntrega("smtp_connect_timeout") / ("smtp_connect_error")
class SmtpEmailSender:
    async def enviar(self, entrega: EntregaEmail) -> None: ...
```
**Design note (unresolved, flag to planner):** `EntregaEmail` (comunicacoes' domain value object) may carry outbox-only fields (delivery id, attempt count). Check its exact dataclass shape before reusing directly — a minimal parallel value object scoped to `auth` may be cleaner. This is called out as a judgment call in RESEARCH.md, not a blocking finding.

---

### `app/modules/auth/infrastructure/models.py` — model, CRUD

**Analog:** itself (existing file, full content read, 55 lines).

**Column to widen (SEC-05):**
```python
# Source: app/modules/auth/infrastructure/models.py:40-54 (current)
class AuthOtpChallenge(Base):
    __tablename__ = "auth_otp_challenges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(6), nullable=False)   # <-- widen to String(64)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```
Change: `code: Mapped[str] = mapped_column(String(64), nullable=False)` — a hex SHA-256 digest is 64 chars. Comment style in this file already explains index rationale inline (`# Sem index=True...`) — follow that convention if adding a note about why 64.

---

### `app/modules/auth/application/casos_uso.py` — service (use cases), request-response

**Analog:** itself (existing file, full content read, 271 lines) + `app/modules/comunicacoes/infrastructure/http/routes.py` for the rate-limit call shape (Pattern 1).

**`sign_out` — current no-op (line 114-115):**
```python
async def sign_out(_session: AsyncSession) -> SignOutResponse:
    return SignOutResponse()
```
**Target (SEC-06, from RESEARCH.md Pattern 3):**
```python
async def sign_out(current_user: CurrentUser) -> SignOutResponse:
    redis = get_redis()
    await redis.set(f"auth:signed_out_since:{current_user.id}", str(int(time.time())), ex=7 * 86_400)
    return SignOutResponse()
```
Note: signature changes from taking `_session: AsyncSession` to `current_user: CurrentUser` (imported from `app.modules.auth.infrastructure.http.dependencies`) — the route must supply `Depends(get_current_user)`.

**`refresh_token` — current (lines 98-111):**
```python
async def refresh_token(session: AsyncSession, body: RefreshTokenRequest) -> AuthResponse:
    try:
        payload = decode_refresh_token(body.refresh_token)
    except Exception as exc:
        raise SessaoInvalidaError() from exc
    user_id = int(payload["sub"])
    user = await repositorio_usuario.buscar_por_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError()
    return _auth_success(user)
```
**Target insertion (SEC-06 cutoff check, right after `decode_refresh_token`):**
```python
cutoff = await get_redis().get(f"auth:signed_out_since:{payload['sub']}")
if cutoff is not None and int(payload.get("iat", 0)) < int(cutoff):
    raise SessaoInvalidaError()
```

**Rate-limit call shape to add to `sign_in`, `register`, `confirm_register`, `request_password_recovery`, `confirm_password_recovery` (SEC-07) — mirrors comunicacoes exactly:**
```python
# Source: app/modules/comunicacoes/infrastructure/http/routes.py:186-214 (read directly, this session)
client_ip = request.client.host if request.client else "unknown"
settings = get_settings()
try:
    rate = await enforce_dual_fixed_window(
        get_redis(),
        namespace="smtp",              # -> per-endpoint namespace for auth, e.g. "auth_sign_in"
        user_identity=str(actor.id),   # -> normalizar_email(body.identifier) for auth (unauthenticated)
        ip_identity=client_ip,
        secret=settings.jwt_secret,
        user_limit=_SMTP_USER_RATE_PER_MINUTE,
        ip_limit=_SMTP_IP_RATE_PER_MINUTE,
    )
except RateLimitUnavailableError as exc:
    cause = type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__
    logger.warning("Rate limit SMTP indisponível: %s", cause)
    raise HTTPException(
        status_code=503,
        detail="Serviço de comunicação temporariamente indisponível. Tente novamente mais tarde.",
        headers={"Retry-After": str(_RATE_LIMIT_UNAVAILABLE_RETRY_AFTER_SECONDS)},
    ) from exc
```
Note: `client_ip`/`request` are only available at the route (FastAPI `Request` param) — the rate-limit call is likely wired in `routes.py` (passing `request`) rather than deep inside `casos_uso.py`, OR `casos_uso.py` functions gain a `client_ip: str` parameter. Follow whichever tier `comunicacoes` uses — verify at implementation time; this excerpt is from the route file, not comunicacoes' `casos_uso.py`, so the auth equivalent is likely also route-tier logic layered in front of the existing `service.sign_in(db, body)` calls in `routes.py`.

---

### `app/modules/auth/domain/tokens.py` — domain (JWT), transform

**Analog:** itself (existing file, full content read, 82 lines).

**`create_refresh_token` — current (lines 33-44):**
```python
def create_refresh_token(*, user_id: int, email: str, roles: list[str]) -> str:
    expire = datetime.now(UTC) + timedelta(days=7)
    return _encode_token(
        {"typ": "refresh", "sub": str(user_id), "email": email, "roles": roles, "jti": uuid4().hex, "exp": expire}
    )
```
**Target (SEC-06, add `iat`):**
```python
def create_refresh_token(*, user_id: int, email: str, roles: list[str]) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(days=7)
    return _encode_token(
        {"typ": "refresh", "sub": str(user_id), "email": email, "roles": roles,
         "jti": uuid4().hex, "iat": int(now.timestamp()), "exp": expire}
    )
```
Note: `decode_refresh_token` (lines 57-64) needs no change — it already returns the full payload dict via `jwt.decode`, so `payload.get("iat")` will just work once `iat` is added at creation.

---

### `app/modules/auth/infrastructure/http/routes.py` — route/controller, request-response

**Analog:** itself (existing file, full content read, 185 lines) + `app/modules/comunicacoes/infrastructure/http/routes.py` rate-limit wiring (excerpt above) + `app/modules/auth/infrastructure/http/dependencies.py::get_current_user`/`CurrentUser`.

**`sign_out` route — current (lines 83-85):**
```python
@router.post("/sign-out", response_model=SignOutResponse)
async def sign_out(db: AsyncSession = Depends(get_db)) -> SignOutResponse:
    return await service.sign_out(db)
```
**Target (SEC-06):**
```python
@router.post("/sign-out", response_model=SignOutResponse)
async def sign_out(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> SignOutResponse:
    return await service.sign_out(current_user)
```
Import `CurrentUser`, `get_current_user` from `app.modules.auth.infrastructure.http.dependencies` (already the file that defines them — routes.py is a sibling in the same `http/` package).

**Error-handling convention already established in this file (mirror exactly for any new exceptions, e.g. a rate-limit 429/503):**
```python
# Source: app/modules/auth/infrastructure/http/routes.py:43-48 (existing convention)
try:
    return await service.sign_in(db, body)
except CredenciaisInvalidasError as exc:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
    ) from exc
```
Every route in this file follows: call `service.<use_case>(db, body)` inside `try`, catch a specific domain exception from `app.modules.auth.domain.exceptions`, translate to `HTTPException` with a fixed status/detail. New rate-limit logic should follow the same `try/except HTTPException` idiom, catching `RateLimitUnavailableError` (503) and checking `rate.allowed` (429) — both raised directly as `HTTPException`, not wrapped in a domain exception, matching the `comunicacoes` precedent (no domain-level rate-limit exception type exists there either).

**`get_current_user` dependency (already exists, reusable as-is for sign-out):**
```python
# Source: app/modules/auth/infrastructure/http/dependencies.py:44-74 (existing, unchanged)
async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    ...
```

---

### `app/shared/config/settings.py` — config, transform

**Analog:** itself (existing file, targeted reads at lines 425-440, 505-600).

**SEC-02 default flip — current (lines 432-434):**
```python
seed_auth_on_startup: bool = Field(
    default=True, validation_alias="SEED_AUTH_ON_STARTUP"
)
```
**Target:** change `default=True` to `default=False`. The PROD fail-fast half is already implemented and must NOT be touched:
```python
# Source: app/shared/config/settings.py:594-595 (already implemented, leave as-is)
if self.seed_auth_on_startup:
    raise ValueError("SEED_AUTH_ON_STARTUP deve ser false em PROD")
```

**Existing fail-fast validator convention to mirror if any new PROD-only check is added (e.g. SMTP-configured-in-PROD, flagged as an open question in RESEARCH.md Environment Availability):**
```python
# Source: app/shared/config/settings.py:555, 587-595 (existing convention inside validate_cross_field_constraints)
if ENV == "PROD":
    ...
    if (
        self.jwt_secret == "change-me-in-production"
        or len(self.jwt_secret) < 32
    ):
        raise ValueError("PROD exige JWT_SECRET forte com pelo menos 32 caracteres")
    if self.seed_auth_on_startup:
        raise ValueError("SEED_AUTH_ON_STARTUP deve ser false em PROD")
```
All PROD-only validation lives inside the single `@model_validator(mode="after") def validate_cross_field_constraints(self)` method (line 514) — any new PROD-only rule (e.g. requiring `smtp_configured` in PROD) should be added as another `if ENV == "PROD": ... raise ValueError(...)` block inside this same method, not a new validator.

---

### `.env.example` — config

**Analog:** itself (existing file; line 159 per RESEARCH.md).
**Change:** `SEED_AUTH_ON_STARTUP=true` → `SEED_AUTH_ON_STARTUP=false`, to match the SEC-02 default flip (Pitfall 4 in RESEARCH.md — required companion change, not optional).

---

### New Alembic migration — widen `auth_otp_challenges.code`

**Analog:** `alembic/versions/028_remove_colunas_ingestao_sem_leitor.py` (most recent, full file read — 74 lines).

**Convention to mirror (module docstring + revision header + explicit downgrade):**
```python
# Source: alembic/versions/028_remove_colunas_ingestao_sem_leitor.py:1-45 (structure)
"""<one-line imperative summary of what changed and why>

Revision ID: 029
Revises: 028
Create Date: 2026-08-12

<prose explaining the change, referencing the affected table/column and why
it's safe — this codebase's migrations consistently document the "why", not
just the "what">
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "auth_otp_challenges",
        "code",
        type_=sa.String(length=64),
        existing_type=sa.String(length=6),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "auth_otp_challenges",
        "code",
        type_=sa.String(length=6),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
```
**Note:** verify actual next revision number via `uv run alembic heads` (per CLAUDE.md instruction — do not hardcode "029" as fact, confirm at implementation time). Also confirm no existing rows exceed 6 chars before a downgrade is ever exercised (not a concern for upgrade — widening never truncates).

---

### `app/tests/test_auth_flows.py` — test, request-response

**Analog:** itself (existing file; `_ler_otp_async`/`ler_otp` helper, lines 83-102).

**Current helper (breaks under SEC-05 — Pitfall 1):**
```python
# Source: app/tests/test_auth_flows.py:83-102 (current)
async def _ler_otp_async(email, purpose):
    test_engine, session_factory = _nova_sessao_isolada()
    try:
        async with session_factory() as session:
            row = await session.scalar(
                select(AuthOtpChallenge)
                .where(...)
            )
            return row.code if row else None
    finally:
        await test_engine.dispose()


def ler_otp(email, purpose):
    return asyncio.run(_ler_otp_async(email, purpose))
```
Used at 3+ call sites (`test_register_...`, `test_password_recovery_fluxo_completo_altera_senha`, the anti-enumeration test) — e.g.:
```python
# Source: app/tests/test_auth_flows.py:196, 228, 276, 287
assert ler_otp(email, OtpPurpose.REGISTER) is not None
codigo = ler_otp(email, OtpPurpose.REGISTER)
assert ler_otp(email_inexistente, OtpPurpose.RECOVERY) is None
codigo = ler_otp(email, OtpPurpose.RECOVERY)
```
**Required change (per RESEARCH.md Pitfall 1):** once `code` is hashed, `row.code` returns the hash, not the plaintext usable in a subsequent `confirm` call. Replace the DB-column read with a `caplog`-based capture of the `logger.info("OTP %s gerado para %s: %s", purpose.value, email, code)` line emitted in non-PROD by `criar_desafio` — this is the same line the pre-existing code already relies on for dev/staging visibility, so no new logging needs to be added, only the test's read method changes. The `is not None` / `is None` boolean checks (existence, not value) must be preserved for the anti-enumeration assertion.

---

## Shared Patterns

### Rate limiting (SEC-07)
**Source:** `app/shared/infrastructure/rate_limit.py::enforce_dual_fixed_window` + `app/modules/comunicacoes/infrastructure/http/routes.py:186-214`
**Apply to:** `sign-in`, `register`, `register/confirm`, `password/recovery`, `password/recovery/confirm` in `app/modules/auth/infrastructure/http/routes.py` (or `casos_uso.py`, tier TBD by planner — see note above).
```python
from app.shared.infrastructure.rate_limit import RateLimitUnavailableError, enforce_dual_fixed_window
from app.shared.infrastructure.redis_client import get_redis

client_ip = request.client.host if request.client else "unknown"
try:
    rate = await enforce_dual_fixed_window(
        get_redis(), namespace="auth_sign_in", user_identity=normalizar_email(body.identifier),
        ip_identity=client_ip, secret=settings.jwt_secret, user_limit=5, ip_limit=20,
    )
except RateLimitUnavailableError as exc:
    raise HTTPException(status_code=503, headers={"Retry-After": "30"}) from exc
if not rate.allowed:
    raise HTTPException(status_code=429, headers={"Retry-After": str(rate.retry_after)})
```
Suggested limits table (RESEARCH.md Pattern 1): sign-in 5/20, confirm endpoints 5/20, register/recovery-request 3/10.
**Open decision (flag to planner, RESEARCH.md Pitfall 3 / Open Question 1):** fail-open vs fail-closed for `sign-in` specifically on `RateLimitUnavailableError` — not a copy-paste, needs an explicit choice.

### Constant-time secret comparison
**Source:** stdlib `hmac.compare_digest`, already used implicitly via the `_opaque_key` HMAC pattern in `rate_limit.py`.
**Apply to:** `otp_hash.py::verify_otp_code` — never a manual `==` on the hash/candidate.

### HTTPException translation from domain exceptions
**Source:** `app/modules/auth/infrastructure/http/routes.py` (every existing route: try/except around `service.<fn>`, translating one specific domain exception to one specific `HTTPException`).
**Apply to:** Any new domain exception introduced for rate-limit/sign-out-revocation flows should either reuse `SessaoInvalidaError` (already exists, already mapped to 401 in `token/refresh`) or follow the exact same try/except/raise-from shape for a new exception type.

### Redis client access
**Source:** `app/shared/infrastructure/redis_client.py::get_redis()`, already used by `comunicacoes` and `rate_limit.py`.
**Apply to:** SEC-06 sign-out cutoff write/read, SEC-07 rate-limit calls — no new Redis client/connection code needed.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| `docs/seguranca.md` (NEW) | doc | — | No code analog; content sourced from `app/modules/auth/bootstrap/seed.py::DEFAULT_AUTH_USERS` (5 seeded accounts, one per role) and the SEC-04 requirement text. Follow `docs/`'s existing one-doc-per-topic convention (see `docs/index.md`, `docs/api.md`, `docs/arquitetura.md` for structure/tone) — verify structure directly before writing, not covered by this pattern map since docs aren't code patterns. |

## Metadata

**Analog search scope:** `app/modules/auth/` (all files), `app/shared/infrastructure/rate_limit.py`, `app/shared/infrastructure/redis_client.py` (referenced, not re-read — signature already known via `comunicacoes` usage), `app/modules/comunicacoes/infrastructure/http/routes.py`, `app/modules/comunicacoes/infrastructure/email_sender.py`, `app/shared/config/settings.py`, `alembic/versions/028_*.py`, `app/tests/test_auth_flows.py`.
**Files scanned/read this session:** 11 (7 full reads, 4 targeted grep+read of large files: `settings.py`, `comunicacoes/routes.py`, `email_sender.py`, `test_auth_flows.py`).
**Pattern extraction date:** 2026-08-12
