# Technology Stack

**Analysis Date:** 2026-08-04

## Backend Stack (`backend/`)

### Languages

**Primary:**
- **Python** 3.13+ — specified in `pyproject.toml`

### Runtime & Package Manager

**Environment:**
- Python 3.13-slim Docker base image for production
- **uv** — modern Python package manager (frozen lockfile in `uv.lock`)

**Package Manager:**
- uv (`uv sync`, `uv run`)
- Lockfile: `uv.lock` (committed, dependency freeze)
- Dev dependencies: organized in `[dependency-groups]` in `pyproject.toml`

### Core Frameworks

**HTTP & ASGI:**
- **FastAPI** 0.115+ — REST API framework with auto-generated OpenAPI/Swagger docs
- **Uvicorn** 0.34+ — ASGI async web server

**Database & Persistence:**
- **SQLAlchemy** 2.0.36+ (with asyncio support) — async ORM for PostgreSQL
- **asyncpg** 0.30+ — async PostgreSQL driver
- **Alembic** 1.14+ — schema migrations (`alembic/versions/`, current head: 028 — confirme com `uv run alembic heads`)

**Async Task Queue:**
- **Celery** 5.4+ (with Redis broker) — distributed task scheduler and worker
- **Redis** 5.2+ — message broker and result backend for Celery
- Celery Beat — periodic task scheduler (default sync every 7200 seconds / 2 hours via `INGESTAO_INTERVALO_SEGUNDOS`)

**Authentication & Security:**
- **python-jose** 3.3+ (with cryptography) — JWT token creation/verification (HS256 algorithm)
- **bcrypt** 4.2+ — password hashing (PBKDF2 via bcrypt)

**Email & Notifications:**
- **aiosmtplib** 3.0+ — async SMTP client for email delivery (STARTTLS, provider-agnostic)

**HTTP Client & Integration:**
- **httpx** 0.28+ — async HTTP client (for Databricks SQL API calls)

**Data Validation & Configuration:**
- **Pydantic** v2 (via pydantic-settings 2.6+) — schema validation and settings management
- **python-dotenv** 1.0+ — `.env` file loading
- **email-validator** 2.2+ — email format validation

**Internationalization:**
- **pyicu** 2.12+ — Unicode Collation Algorithm binding (pt_BR locale-aware string sorting in history)

**Monitoring & Observability:**
- **prometheus-client** 0.21+ — Prometheus metrics endpoint (`/metrics`)

### Testing (Backend)

- **pytest** 8.3+ — test runner
- **pytest-asyncio** 0.24+ — async test support

### Build & Deployment

**Containerization:**
- Docker with `.docker/Dockerfile` (Python 3.13-slim base)
- Docker Compose at `.docker/docker-compose.yml` with services: `dev_db` (Postgres 16-alpine), `redis` (7-alpine), `api`, `celery_worker`, `celery_beat`

**Build Dependencies:**
- `gcc`, `g++`, `pkg-config`, `libicu-dev`, `libpq-dev`, `curl`, `locales` (installed in Dockerfile for C extensions like PyICU)

**Entry Points:**
- `app/main.py` — FastAPI app instance
- `app/workers/celery_app.py` — Celery application (worker + beat)
- `alembic/env.py` — migration runner

**Run Commands:**
- API: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- Worker: `celery -A app.workers.celery_app worker --loglevel=info`
- Beat: `celery -A app.workers.celery_app beat --loglevel=info`
- Migrations: `alembic upgrade head`

**Production Deployment:**
- AWS ECS (Elastic Container Service)
- ECR repository: `system-automation-back-end`
- Task definition family: `system-automation-backend-dev`
- Container image registry: `ghcr.io/astral-sh/uv:latest` (for base uv binary)

---

## Frontend Stack (`frontend/`)

### Languages

**Primary:**
- **TypeScript** ~5.9.3 — strict static typing
- **JavaScript/JSX** — React component syntax
- **CSS** — Tailwind CSS for styling

### Runtime & Package Manager

**Environment:**
- **Node.js** (LTS version, inferred from Next.js 16 requirements)
- **pnpm** — fast, strict package manager (preferred)

**Package Manager:**
- pnpm (with lockfile)
- Monorepo-aware

### Core Frameworks

**Framework:**
- **Next.js** 16.2.2 — React meta-framework (App Router, server/client components, middleware, API routes)
- **React** 19.2.3 — UI library (concurrent features, automatic batching)
- **React DOM** 19.2.3 — React rendering target

**Styling & Design System:**
- **Tailwind CSS** 4.1.18 — utility-first CSS framework
- **@tailwindcss/postcss** 4.1.18 — PostCSS plugin for Tailwind
- **@system-automation/design-system** — local package (git submodule at `design-system/`, transpiled in next.config.ts)
- **clsx** 2.1.1 — conditional classname builder
- **tailwind-merge** 3.6+ — smart Tailwind class merging
- **tw-animate-css** 1.4+ — Tailwind animation utilities
- **class-variance-authority** 0.7.1 — component variant patterns

**UI Components & Icons:**
- **@base-ui/react** 1.4.1 — headless component primitives (accessible, unstyled)
- **lucide-react** 0.577+ — icon library
- **@tanstack/react-virtual** 3.13.12 — virtual scrolling for large lists

**Authentication:**
- **next-auth** 5.0.0-beta.30 — Next.js authentication library
  - Credentials provider with JWT tokens
  - Session management with token refresh
  - Role-based access control (RBAC)

**Form & Data Validation:**
- **zod** 4.4.3 — TypeScript-first schema validation library
- **input-otp** 1.4.2 — OTP input component

**Security:**
- **jose** 6.2.2 — JWT library (compatible with backend HS256 tokens)

### Testing (Frontend)

- **vitest** 4.1.5 — test runner (Vite-native, faster than Jest)
- **@testing-library/react** 16.3.2 — React component testing utilities
- **@testing-library/dom** 10.4.1 — DOM testing utilities
- **@testing-library/jest-dom** 6.9.1 — assertion matchers
- **@testing-library/user-event** 14.6.1 — user interaction simulation
- **jsdom** 29.1.1 — JavaScript DOM implementation for tests

### Code Quality & Type Checking

**Linting:**
- **ESLint** 9.39+ — code quality checker
- **eslint-config-next** 16.1.7 — Next.js-specific ESLint rules

**Type Checking:**
- **TypeScript** ~5.9.3 — via `tsc --noEmit`

### Build & Development Tools

**Dev Dependencies:**
- **@vitejs/plugin-react** 5.2+ — React plugin for Vitest
- **@types/node** 24.12.4 — Node.js type definitions
- **@types/react** 19.2.14 — React type definitions
- **@types/react-dom** 19.2.3 — React DOM type definitions

### Configuration

**TypeScript Configuration (`tsconfig.json`):**
- Target: ES2017
- Module: ESNext
- Strict mode: enabled
- JSX: react-jsx
- Path alias: `@/*` maps to project root (relative imports)
- Exclude: `node_modules/`, `.next/`, `design-system/`

**Next.js Configuration (`next.config.ts`):**
- Transpiles `@system-automation/design-system` package
- Output file tracing for minimal build artifacts
- Webpack bundling (default)

**Vitest Configuration (`vitest.config.ts`):**
- Environment: jsdom (browser-like testing)
- React plugin enabled
- Setup file: `vitest.setup.ts`
- Include: `**/*.test.{ts,tsx}`
- Exclude: `node_modules/`, `.next/`, `design-system/`

### Build Commands

- `pnpm dev` — Development server (port 3000, live reload via webpack)
- `pnpm build` — Production build (optimized output)
- `pnpm start` — Production server
- `pnpm typecheck` — Type checking only (`tsc --noEmit`)
- `pnpm lint` — ESLint checks
- `pnpm test` — Run vitest suite once
- `pnpm test:watch` — Watch mode for tests

### Deployment

- No specific cloud provider lock-in detected
- Compatible with:
  - Vercel (native Next.js)
  - AWS Amplify
  - Any Node.js-capable platform
  - Static export (if configured)

---

## Cross-Project Communication

**REST API Integration:**
- Backend at `http://localhost:8000` (dev) or configured `API_URL` (prod)
- Frontend at `http://localhost:3000` (dev) or configured hostname (prod)
- Protocol: HTTP/HTTPS with JSON payloads
- Authentication: Bearer token in `Authorization` header (JWT from backend)

**JWT Specification:**
- Algorithm: HS256 (symmetric HMAC)
- Signing key: `JWT_SECRET` (shared between backend and frontend)
- Issued by: Backend (`python-jose`)
- Verified by: Frontend (NextAuth with `jose` library)
- Token types: access token (8h TTL), refresh token (7d TTL)

**Role-Based Access Control:**
- Backend levels: `basico`, `operacional`, `gestor`, `administrador`, `admin_tecnico`
- Frontend integration: NextAuth session carries roles from backend

---

*Stack analysis: 2026-08-04*
