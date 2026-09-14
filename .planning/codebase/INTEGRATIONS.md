# External Integrations

**Analysis Date:** 2026-08-04

## APIs & External Services

### Databricks REST API (SQL Statement Execution)

**Service:** Azure Databricks — source of order and inventory data

**Purpose:** Periodic full-refresh ingestion of `pedidos` (open orders) and `estoque` (stock) tables from Databricks into PostgreSQL

**Client:** Custom async HTTP client via `httpx` (no databricks-sql-connector dependency)

**Implementation:** `backend/app/shared/infrastructure/databricks_client.py`

**Auth:**
- Bearer token in `Authorization` header
- Environment variable: `DATABRICKS_TOKEN` (gitignored, in `.env` only)

**Endpoints:**
- Base: `https://{DATABRICKS_SERVER_HOSTNAME}/api/2.0/sql/statements/`
- Pattern: POST with SQL query, statement ID polling

**Configuration:**
- `DATABRICKS_SERVER_HOSTNAME` — cluster hostname (e.g., `adb-xxxxxxxxxxxx.xx.azuredatabricks.net`)
- `DATABRICKS_HTTP_PATH` — warehouse connection path (e.g., `/sql/1.0/warehouses/xxxxxxxxxxxxxxxx`)
- `DATABRICKS_TOKEN` — OAuth token (secret, in `.env` only)
- `DATABRICKS_TABELA_PEDIDOS` — full path to orders table (e.g., `catalogo.schema.pedidos_em_aberto`)
- `DATABRICKS_TABELA_ESTOQUE` — full path to inventory table (e.g., `catalogo.schema.estoque`)
- `DATABRICKS_TABELA_PEDIDOS_PROCESSADOS` — full path to processed orders table (source of truth for ERP state: `indica_reserva`, `indica_embalado`)

**Features:**
- Two result disposition modes:
  - **INLINE:** result embedded in JSON response (25 MiB limit, good for stock data)
  - **EXTERNAL_LINKS:** result as pre-signed URLs (for large datasets like orders)
- Auto-fallback: if INLINE exceeds 25 MiB, automatically retries in EXTERNAL_LINKS mode
- Poll-based execution: checks statement status every 2 seconds, max 60 polls (2 minutes timeout)
- Data returned as list of dicts with string values (type conversion handled by consumer)

**Scheduling:**
- Celery beat task runs periodically
- Interval: `INGESTAO_INTERVALO_SEGUNDOS` (default 7200s = 2 hours, can be adjusted)
- Task: `backend/app/workers/tasks/ingestao.py` → `sincronizar_databricks()`
- Channel isolation: pedidos filtered by current month; estoque aggregated by `(cd_prod_cor, sg_tamanho, canal)`

---

## Data Storage

### Primary Database: PostgreSQL

**Provider:** PostgreSQL 12+ (self-hosted or cloud)

**Purpose:** Core application data (users, orders, inventory, parameters, history, reservations, processing flags)

**Client:** SQLAlchemy 2.0+ with asyncpg async driver

**Connection:**
- URL format: `postgresql+asyncpg://user:password@host:port/database`
- Configuration: `backend/app/shared/config/settings.py`
- Session management: `backend/app/shared/database/session.py`
- Base model: `backend/app/shared/database/base.py`

**Environment Variables:**
- `POSTGRES_USER` — database user (default: `automation`)
- `POSTGRES_PASSWORD` — database password (default: `automation`)
- `POSTGRES_DB` — database name (default: `system_automation`)
- `POSTGRES_HOST` — database hostname (default: `dev_db`; Docker-aware: resolves to `localhost` outside container)
- `POSTGRES_PORT` — port (default: `5432`)
- `DATABASE_URL` — optional complete URL override (takes priority over individual POSTGRES_* vars)
- `DEV_POSTGRES_HOST_LOCAL` — local resolution hint (overrides `dev_db` → `localhost` mapping)

**Schema Management:**
- Alembic migrations in `backend/alembic/versions/`
- Migrations run on startup: `alembic upgrade head`
- Auto-create tables flag: `CREATE_TABLES_ON_STARTUP=true` (dev only; prod uses Alembic)
- Seed users on startup: `SEED_AUTH_ON_STARTUP=true`

**Key Tables:**
- `auth_users` — user accounts with roles
- `pedidos` — orders (channel-isolated)
- `estoque` — inventory (channel-isolated)
- `ordens_reserva` — generated reservation orders
- `pedido_modificacoes` — order modification audit trail
- `pedidos_processados` — ERP processing state flags
- `parametros` — business logic knobs
- `alertas` — system alerts
- `history` — processed orders history

**Docker Service:**
- Container: `system_automation_db` (Postgres 16-alpine)
- Healthcheck: `pg_isready` every 10s
- Volumes: `automation_dev_db` (persisted data)
- Network: `automation_network` (bridge)

### Message Broker & Cache: Redis

**Provider:** Redis 7+ (self-hosted or cloud)

**Purpose:**
- Celery task broker (job queue)
- Celery result backend (task result storage)
- General-purpose caching (optional)

**Connection:**
- URL format: `redis://host:port/db`
- Configuration: `backend/app/shared/config/settings.py`
- Health check: `backend/app/shared/infrastructure/redis_client.py` → `check_redis()`

**Environment Variables:**
- `REDIS_URL` — Redis connection for app caching (default: `redis://redis:6379/0`)
- `CELERY_BROKER_URL` — Celery task broker (default: `redis://redis:6379/0`)
- `CELERY_RESULT_BACKEND` — Celery result storage (default: `redis://redis:6379/1`)
- `REDIS_URL_LOCAL` — local resolution hint (overrides `redis://redis` → `localhost:6379/0` mapping)

**Docker Service:**
- Container: `system_automation_redis` (Redis 7-alpine)
- Healthcheck: `redis-cli ping` every 10s
- Volumes: `automation_redis_data` (persisted data)
- Network: `automation_network` (bridge)

**Celery Configuration:**
- Task broker: Redis DB 0
- Result backend: Redis DB 1
- Worker: `celery_worker` service (runs task jobs)
- Beat scheduler: `celery_beat` service (triggers periodic tasks)

### File Storage

**Local Storage Only:**
- No cloud storage integration detected (S3, GCS, etc.)
- Mock/fixture JSON files (test/development):
  - `estoque_mock.json` — stock data fixture
  - `teste_pedidos.json` — test orders
  - `teste_ordens_reserva.json` — test reservation orders
  - `or_sem_adequacao.json` — test fixture (no adequacy)
  - `or_com_adequacao.json` — test fixture (with adequacy)
  - `pedidos_processados.db` — SQLite DB for processed orders tracking (local development)

---

## Authentication & Identity

### Backend Authentication: Custom JWT with Credentials Flow

**Strategy:** Custom implementation with JWT tokens (access + refresh) and bcrypt passwords

**Location:** `backend/app/modules/auth/`

**Implementation Files:**
- Security: `security.py` — JWT signing/verification, password hashing
- Routes: `routes.py` — HTTP endpoints
- Service: `service.py` — business logic (user CRUD, password reset, registration)
- Models: `models.py` — `AuthUser` ORM entity
- Schemas: `schemas.py` — request/response Pydantic models

**Token Types & TTL:**
- **Access Token** (typ=access):
  - TTL: 8 hours (configurable via `JWT_EXPIRE_MINUTES`, default 480)
  - Payload: `sub` (user_id), `email`, `roles` (list), `exp`, `typ=access`
  - Used: Bearer in `Authorization` header for API calls
- **Refresh Token** (typ=refresh):
  - TTL: 7 days
  - Payload: `sub` (user_id), `email`, `roles`, `jti` (UUID), `exp`, `typ=refresh`
  - Used: To obtain new access token when expired
- **Session Token** (typ=session):
  - TTL: 15 minutes
  - Payload: `email`, `purpose` (e.g., `password_reset`), `exp`, `typ=session`
  - Used: For account operations (password reset, email confirmation)

**Signing:**
- Library: `python-jose` (with cryptography backend)
- Algorithm: HS256 (HMAC-SHA256, symmetric key)
- Secret key: `JWT_SECRET` (must be strong in production)

**Password Security:**
- Algorithm: bcrypt (via `bcrypt` package)
- Salt: auto-generated per password
- Cost factor: bcrypt default

**Environment Variables:**
- `JWT_SECRET` — HMAC secret (production: use strong random value, not default)
- `JWT_ALGORITHM` — algorithm (fixed to `HS256`)
- `JWT_EXPIRE_MINUTES` — access token TTL in minutes (default 480 = 8h)

**API Endpoints:**
- `POST /api/auth/sign-in` — login with email/password → returns access + refresh tokens
- `POST /api/auth/token/refresh` — refresh access token using refresh token
- `POST /api/auth/sign-out` — revoke session (best-effort, stateless JWT so no server revocation)
- `POST /api/auth/register` — create user account (step 1)
- `POST /api/auth/register/confirm` — confirm registration (step 2, email validation)
- `POST /api/auth/password/recovery` — request password reset (sends email)
- `POST /api/auth/password/recovery/confirm` — confirm password reset with session token
- `POST /api/auth/account/update` — update user email/password
- `POST /api/auth/account/email/confirm` — confirm new email address

**RBAC (Role-Based Access Control):**
- 5 role levels: `basico` < `operacional` < `gestor` < `administrador` < `admin_tecnico`
- Roles stored in `auth_users.roles` (JSON array)
- Role check: re-read from DB on every request (no trust in token alone)

### Frontend Authentication: NextAuth 5.0 + Backend JWT

**Strategy:** NextAuth session wraps backend JWT tokens

**Location:** `frontend/auth.ts` (main NextAuth configuration)

**Implementation Files:**
- NextAuth config: `auth.ts` — Credentials provider, session callbacks
- API routes: `lib/api/auth-backend.ts` — HTTP calls to backend auth endpoints
- Session helper: `lib/auth/session-policy.ts` — token expiry and refresh logic
- Secret: `lib/auth/auth-secret.ts` — NextAuth secret retrieval
- Role resolution: `shared/config/auth/automation-panel-roles.ts` — map backend roles to frontend roles
- Session provider: `shared/providers/SessionProvider.tsx` — React context wrapper

**Token Handling:**
- Backend JWT obtained via `/api/auth/sign-in` call
- JWT wrapped in NextAuth session object
- Access token stored in NextAuth session (in-memory)
- Refresh token stored in secure httpOnly cookie (not in session)
- Token refresh: NextAuth callback intercepts expiry, calls backend `/api/auth/token/refresh`

**Session Encryption:**
- Secret: `AUTH_SECRET` environment variable
- Default (dev): `dev-only-system-automation-auth-secret`
- Production: must be set to a strong random value

**Credentials Provider:**
- Fields: `email`, `password`, `handoff` (optional JWT handoff from other services)
- Flow:
  1. User submits email/password on login form
  2. NextAuth calls `authorize()` callback
  3. Callback makes `POST /api/auth/sign-in` to backend
  4. Backend verifies credentials, returns access + refresh tokens
  5. NextAuth builds session with tokens + user data

**Role-Based Access Control:**
- Backend roles synced to NextAuth session
- Frontend role resolution: `resolveautomationRole(roles)` maps to single role
- Access denial: `automationPanelAccessDenied` exception if user lacks required roles
- Fallback: mock auth for dev (if `ALLOW_DEV_AUTH_MOCKS=true` and backend not configured)

**Environment Variables:**
- `AUTH_SECRET` — NextAuth session encryption secret (production-critical)
- `NEXT_PUBLIC_API_URL` — backend API URL (exposed to browser, used by client-side code)
- `API_URL` — backend API URL (server-side only, for Route Handlers and NextAuth callbacks)
- `ALLOW_DEV_AUTH_MOCKS` — boolean flag to allow mock credentials in dev (development only)

**CORS:**
- Backend CORS allows frontend origin (default dev: `http://localhost:3000`)
- Configured via `CORS_ORIGINS` env var on backend

---

## Email & Notifications

### SMTP Integration

**Service:** Provider-agnostic SMTP (tested with Gmail and Microsoft 365)

**Purpose:** Email notifications (password recovery, user invites, business alerts)

**Library:** `aiosmtplib` 3.0+ (async SMTP client)

**Implementation:** `backend/app/modules/comunicacoes/email_service.py`

**Endpoints:**
- `POST /api/comunicacoes/comunicar-comercial` — send email to commercial team

**Environment Variables:**
- `SMTP_HOST` — SMTP server hostname (default: `smtp.gmail.com`)
- `SMTP_PORT` — SMTP port (default: `587`)
- `SMTP_USER` — SMTP username/auth account (secret, in `.env` only)
- `SMTP_PASSWORD` — SMTP password or app password (secret, in `.env` only)
- `SMTP_FROM` — sender email address (if empty, falls back to `SMTP_USER`)
- `SMTP_STARTTLS` — boolean, enable STARTTLS (default: `true`)

**Providers:**
- **Development:** Gmail (SMTP: `smtp.gmail.com:587`)
  - Setup: Enable 2-factor auth, generate 16-digit app password
  - No TLS certificate issues
- **Production:** Microsoft 365 (SMTP: `smtp.office365.com:587`)
  - Requires IT to enable "Authenticated SMTP"
  - Use corporate email account in `SMTP_USER` / `SMTP_FROM`

**Error Handling:**
- `EmailNotConfiguredError` — raised when `SMTP_USER` or `SMTP_PASSWORD` missing
- `EmailSendError` — raised on SMTP transmission failure

**Usage Example (Backend):**
```python
from app.modules.comunicacoes.email_service import enviar_email

await enviar_email(
    to="recipient@example.com",
    subject="Subject Line",
    body="Email body text"
)
```

---

## CORS (Cross-Origin Resource Sharing)

**Backend CORS Middleware:**
- Middleware: FastAPI `CORSMiddleware` in `backend/app/main.py`
- Configuration:
  - Allowed origins: comma-separated list from `CORS_ORIGINS` env var
  - Allowed methods: `["*"]` (all methods)
  - Allowed headers: `["*"]` (all headers)

**Environment Variable:**
- `CORS_ORIGINS` — comma-separated list of allowed origins
- Default (dev): `http://localhost:3000,http://localhost:5173,http://localhost:8000`
- Production: set to frontend origin only

---

## Webhooks & Callbacks

**Incoming Webhooks:**
- None detected

**Outgoing Webhooks/Callbacks:**
- None detected
- Email notifications are synchronous SMTP calls (not webhook pattern)

---

## Monitoring & Observability

### Prometheus Metrics

**Service:** Prometheus-compatible metrics endpoint

**Library:** `prometheus-client` 0.21+

**Implementation:** `backend/app/shared/metrics/router.py`

**Endpoint:**
- `GET /metrics` — Prometheus-compatible metrics format

**Metrics Exposed:**
- Default prometheus-client metrics (HTTP requests, request duration, exceptions, etc.)

**Configuration:**
- No Prometheus server configured in codebase
- Metrics endpoint available for external scraping

### Error Tracking

**Status:** Not configured

**Expected Integration Points:**
- No Sentry, DataDog, or similar error tracking service detected
- Error handling: `backend/app/shared/errors/handlers.py` — FastAPI exception handlers (converts errors to HTTP responses)

### Logging

**Backend:**
- Framework: Python `logging` module
- Setup: `backend/app/shared/logging/setup.py`
- Output: stdout (captured by Docker logs)
- No log aggregation service (ELK, Splunk, etc.) detected

**Frontend:**
- Framework: Browser console (native)
- No log aggregation detected

---

## CI/CD & Deployment

### Git & Version Control

**Backend Repository:**
- Location: `backend/` (separate git repo)
- Main branch: `main` (production releases only)
- Development branch: `develop` (active development)
- Commit convention: Conventional Commits with optional scope
- Branch prefix: `feat/`, `fix/`, `chore/`, `refactor/`, `docs/` from `develop`

**Frontend Repository:**
- Location: `frontend/` (separate git repo)
- Design system: git submodule at `design-system/` → `your-org/design-system`
- Main branch: `main` (production releases only)
- Development branch: `develop` (active development)

### GitHub Actions / CI Pipeline

**Backend Deployment Pipeline:**
- File: `backend/.github/workflows/pipeline.yml`
- Trigger: push to `develop` branch
- Steps:
  1. Checkout code
  2. Configure AWS credentials (OIDC role)
  3. Login to Amazon ECR
  4. Build Docker image, tag with `git sha`, push to ECR
  5. Fetch current ECS task definition
  6. Update task definition with new image
  7. Deploy to ECS service
- Configuration:
  - AWS region: `us-east-1`
  - ECR repository: `system-automation-back-end`
  - ECS cluster: `your-ecs-cluster`
  - ECS service: `system-automation-backend-dev`
  - Task definition family: `system-automation-backend-dev`
- IAM: GitHub OIDC role `github-oidc` in account `383976104411`

**Frontend CI:**
- File: `frontend/.github/workflows/techdocs.yml` (documentation only)
- No deployment pipeline detected for frontend

### Docker & Containerization

**Backend Dockerfile:**
- Location: `backend/.docker/Dockerfile`
- Base image: `python:3.13-slim`
- Build args: `UID`, `GID` (for user/group creation)
- Workdir: `/workspace`
- Build dependencies: gcc, g++, pkg-config, libicu-dev, libpq-dev (for C extensions)
- User: non-root `appuser` (UID/GID configurable)
- Entrypoint: `uvicorn app.main:app --host 0.0.0.0 --port 8000` (default)
- Expose: port 8000

**Docker Compose:**
- File: `backend/.docker/docker-compose.yml`
- Services:
  - `dev_db` — PostgreSQL 16-alpine (port 5432)
  - `redis` — Redis 7-alpine (port 6379)
  - `api` — FastAPI server (port 8000)
  - `celery_worker` — Celery worker
  - `celery_beat` — Celery beat scheduler
- Network: `automation_network` (bridge)
- Volumes: `automation_dev_db`, `automation_redis_data`
- Environment: loaded from `../.env`

### Production Hosting

**Deployment Target:**
- AWS ECS (Elastic Container Service)
- Task definition: `system-automation-backend-dev` (family)
- Service: `system-automation-backend-dev`
- Cluster: `your-ecs-cluster`

**Frontend Hosting:**
- Not detected in current analysis
- Compatible with: Vercel, AWS Amplify, any Node.js platform

---

## Environment Configuration Summary

### Backend Environment Variables (`.env`)

**Critical (Secrets):**
- `DATABRICKS_TOKEN` — Databricks API token
- `JWT_SECRET` — JWT signing secret
- `SMTP_USER` — SMTP authentication user
- `SMTP_PASSWORD` — SMTP authentication password

**Database:**
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`
- Or: `DATABASE_URL` (complete URL override)

**Databricks:**
- `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`
- `DATABRICKS_TABELA_PEDIDOS`, `DATABRICKS_TABELA_ESTOQUE`, `DATABRICKS_TABELA_PEDIDOS_PROCESSADOS`
- `INGESTAO_INTERVALO_SEGUNDOS` (default 7200)

**Redis/Celery:**
- `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`

**JWT:**
- `JWT_SECRET`, `JWT_ALGORITHM` (default HS256), `JWT_EXPIRE_MINUTES` (default 480)

**SMTP:**
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_STARTTLS`

**CORS:**
- `CORS_ORIGINS` (comma-separated)

**Startup:**
- `CREATE_TABLES_ON_STARTUP` (default false for prod, true for dev)
- `SEED_AUTH_ON_STARTUP` (default true)

**Business Logic:**
- `TOLERANCIA_ADEQUACAO` (default 0.05)
- `CRITERIO_SELECAO` (default `valor`)

### Frontend Environment Variables (`.env`)

**API Configuration:**
- `NEXT_PUBLIC_API_URL` — backend URL (client-side)
- `API_URL` — backend URL (server-side)

**Critical (Secrets):**
- `AUTH_SECRET` — NextAuth session encryption secret

**Development:**
- `ALLOW_DEV_AUTH_MOCKS` — enable mock auth (dev only)

---

*Integration audit: 2026-08-04*
