# Codebase Structure

**Analysis Date:** 2026-08-04

## Directory Layout: Overall Project

```
PROJETO SYSTEM-AUTOMATION/ (project root, NOT a git repo)
├── .planning/
│   └── codebase/
│       ├── ARCHITECTURE.md
│       ├── STRUCTURE.md
│       ├── STACK.md (if applicable)
│       └── TESTING.md (if applicable)
├── backend/ (git repo 1: FastAPI backend)
├── frontend/ (git repo 2: Next.js frontend, contains design-system/ submodule)
├── .venv/ (Python virtual env, ignored)
└── app/ (empty, leftover from a move — ignore)
```

**Note:** The PROJECT ROOT is not a git repository. Each sub-directory (`backend/`, `frontend/`) is its own git repository.

---

## Backend Directory Structure (`backend/`)

```
backend/
├── alembic/                    # Database migrations
│   ├── versions/               # Migration files (*.py)
│   │   ├── 001_*.py
│   │   ├── 002_*.py
│   │   └── 028_*.py            # Current head (confirme com `uv run alembic heads`)
│   ├── env.py                  # Alembic configuration
│   └── script.py.mako          # Migration template
├── alembic.ini                 # Alembic config file
├── .docker/                    # Docker configuration
│   └── docker-compose.yml
├── .github/                    # GitHub Actions workflows
├── .env                        # Environment variables (DO NOT commit)
├── .env.example                # Example .env
├── .gitignore                  # Git ignore rules
├── .mcp.json                   # MCP configuration (for Backstage)
├── .mcp.json.example
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI app entry point
│   ├── bootstrap.py            # Lifespan context manager, auth seed
│   │
│   ├── modules/                # Domain modules (DDD layering)
│   │   ├── auth/               # Authentication & RBAC
│   │   │   ├── domain/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── email.py
│   │   │   │   ├── exceptions.py
│   │   │   │   ├── senha.py    # Password hashing
│   │   │   │   └── tokens.py   # JWT generation
│   │   │   ├── application/
│   │   │   │   ├── __init__.py
│   │   │   │   └── casos_uso.py       # Use case orchestration
│   │   │   ├── infrastructure/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── repositorio_usuario.py
│   │   │   │   └── repositorio_otp.py
│   │   │   ├── bootstrap/
│   │   │   │   ├── __init__.py
│   │   │   │   └── seed.py     # Initialize default users
│   │   │   ├── models.py       # SQLAlchemy ORM (auth_users, auth_otp_challenges)
│   │   │   ├── routes.py       # FastAPI endpoints
│   │   │   ├── schemas.py      # Pydantic request/response schemas
│   │   │   ├── service.py      # Legacy service layer (being replaced by DDD)
│   │   │   ├── security.py     # JWT/bcrypt utilities
│   │   │   ├── roles.py        # RBAC role definitions
│   │   │   ├── dependencies.py # FastAPI dependency injection
│   │   │   └── __init__.py
│   │   │
│   │   ├── pedidos/            # Order management & matching
│   │   │   ├── domain/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── motor_adequacao.py  # Core matching logic
│   │   │   │   ├── relatorios.py       # Output formatting
│   │   │   │   └── value_objects.py    # Helpers (channel, size, stock key)
│   │   │   ├── application/
│   │   │   │   ├── __init__.py
│   │   │   │   └── casos_uso.py        # Use case orchestration
│   │   │   ├── infrastructure/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── repositorio_ingestao_readmodel.py  # Read orders/stock
│   │   │   │   └── repositorio_ordens.py              # Save/load ORs, processados
│   │   │   ├── models.py       # SQLAlchemy ORM (pedidos, estoque, ordens_reserva, etc.)
│   │   │   ├── routes.py       # FastAPI endpoints
│   │   │   ├── schemas.py      # Pydantic schemas
│   │   │   ├── service.py      # Legacy service layer
│   │   │   └── __init__.py
│   │   │
│   │   ├── ingestao/           # Databricks → Postgres sync
│   │   │   ├── domain/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── traducao_databricks.py  # Parse Databricks response
│   │   │   │   └── agregacao.py            # Group/deduplicate
│   │   │   ├── application/
│   │   │   │   ├── __init__.py
│   │   │   │   └── casos_uso.py            # Sync orchestration
│   │   │   ├── infrastructure/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── databricks_reader.py    # HTTP client to Databricks
│   │   │   │   └── repositorio_snapshot.py # Audit trail
│   │   │   ├── api_leitura.py  # Read logic for Databricks queries
│   │   │   ├── models.py       # SQLAlchemy (if any)
│   │   │   ├── routes.py       # FastAPI endpoints
│   │   │   ├── schemas.py      # Pydantic schemas
│   │   │   ├── service.py      # Legacy service layer
│   │   │   └── __init__.py
│   │   │
│   │   ├── parametros/         # Configuration parameters
│   │   │   ├── domain/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── coercao.py            # Type coercion
│   │   │   │   ├── exceptions.py
│   │   │   │   └── leitura_resiliente.py # Fallback read
│   │   │   ├── application/
│   │   │   │   ├── __init__.py
│   │   │   │   └── casos_uso.py          # Use case orchestration
│   │   │   ├── infrastructure/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── repositorio_parametro.py
│   │   │   │   └── repositorio_change_request.py
│   │   │   ├── models.py       # SQLAlchemy (parametros, change_requests)
│   │   │   ├── routes.py       # FastAPI endpoints
│   │   │   ├── schemas.py      # Pydantic schemas
│   │   │   ├── service.py      # Legacy service layer
│   │   │   └── __init__.py
│   │   │
│   │   ├── comunicacoes/       # Email notifications
│   │   │   ├── domain/
│   │   │   │   ├── __init__.py
│   │   │   │   └── assunto.py  # Email subject/body templates
│   │   │   ├── application/
│   │   │   │   ├── __init__.py
│   │   │   │   └── casos_uso.py # Sending orchestration
│   │   │   ├── infrastructure/
│   │   │   │   ├── __init__.py
│   │   │   │   └── repositorio_comunicacao.py
│   │   │   ├── email_service.py # SMTP client (aiosmtplib)
│   │   │   ├── models.py       # SQLAlchemy ORM
│   │   │   ├── routes.py       # FastAPI endpoints
│   │   │   ├── schemas.py      # Pydantic schemas
│   │   │   ├── service.py      # Legacy service layer
│   │   │   └── __init__.py
│   │   │
│   │   ├── core/               # System info endpoints
│   │   │   ├── routes.py       # GET /core/version, /core/info
│   │   │   └── __init__.py
│   │   │
│   │   ├── health/             # Health checks
│   │   │   ├── routes.py       # GET /health, /ready
│   │   │   └── __init__.py
│   │   │
│   │   ├── alertas/            # (DEPRECATED — empty directory)
│   │   └── history/            # (DEPRECATED — empty directory)
│   │
│   ├── shared/                 # Cross-cutting infrastructure
│   │   ├── config/
│   │   │   ├── __init__.py
│   │   │   └── settings.py     # Pydantic Settings from .env
│   │   │
│   │   ├── database/
│   │   │   ├── __init__.py
│   │   │   └── session.py      # Async SQLAlchemy engine, pool, dependency injector
│   │   │
│   │   ├── security/
│   │   │   ├── __init__.py     # JWT encode/decode, role guards (require_min_role, etc.)
│   │   │   └── ...
│   │   │
│   │   ├── infrastructure/
│   │   │   ├── __init__.py
│   │   │   └── databricks_client.py   # Async HTTP polling to Databricks API
│   │   │
│   │   ├── logging/
│   │   │   ├── __init__.py
│   │   │   └── setup.py        # Structured logging configuration
│   │   │
│   │   ├── errors/
│   │   │   ├── __init__.py
│   │   │   └── handlers.py     # FastAPI exception handlers
│   │   │
│   │   ├── metrics/
│   │   │   ├── __init__.py
│   │   │   └── router.py       # Prometheus metrics endpoint
│   │   │
│   │   └── __init__.py
│   │
│   └── workers/                # Celery tasks
│       ├── celery_app.py       # Task broker config, beat schedule
│       ├── tasks/
│       │   ├── __init__.py
│       │   ├── ingestao.py     # sincronizar_databricks() task
│       │   └── ping.py         # Test task
│       └── __init__.py
│
├── pyproject.toml              # Python dependencies (uv format)
├── uv.lock                     # Dependency lock file
├── README.md                   # Project documentation
├── catalog-info.yml            # Backstage catalog metadata
├── docs/                       # Documentation (optional)
│   └── mkdocs.yml
├── mkdocs.yml
└── .pytest_cache/              # (ignored)
```

**Key Backend Files:**

| File | Purpose |
|------|---------|
| `app/main.py` | FastAPI app entry point, router inclusion, middleware |
| `app/bootstrap.py` | Lifespan setup (logging, auth seed, cleanup) |
| `app/modules/__init__.py` | Export all routers via `all_routers` |
| `alembic/versions/` | Database migration files (numbered 001–028) |
| `pyproject.toml` | Project metadata, dependencies, tool config |

---

## Frontend Directory Structure (`frontend/`)

```
frontend/
├── app/                        # Next.js App Router
│   ├── layout.tsx              # Root layout (theme, session providers)
│   ├── globals.css             # Global styles
│   │
│   ├── login/                  # Login pages (public)
│   │   ├── page.tsx
│   │   ├── page.module.css
│   │   └── ...
│   │
│   ├── (app)/                  # Authenticated routes (layout group)
│   │   ├── layout.tsx          # App shell, sidebar, AppDataProvider
│   │   ├── pedidos/            # Order management
│   │   │   ├── page.tsx        # Order list page
│   │   │   └── layout.tsx
│   │   ├── parametros/         # Parameters
│   │   │   ├── page.tsx        # Parameter page
│   │   │   └── layout.tsx
│   │   ├── usuarios/           # User management
│   │   │   ├── page.tsx
│   │   │   └── layout.tsx
│   │   ├── historico/          # Order history
│   │   │   ├── page.tsx
│   │   │   └── layout.tsx
│   │   ├── alertas/            # Alerts
│   │   │   ├── page.tsx
│   │   │   └── layout.tsx
│   │   └── visao-geral/        # Dashboard overview
│   │       ├── page.tsx
│   │       └── layout.tsx
│   │
│   └── api/                    # Next.js API routes
│       └── auth/               # NextAuth.js routes
│           ├── [...]nextauth]/route.ts   # NextAuth handler
│           ├── signin/route.ts
│           ├── signup/route.ts
│           ├── password/
│           │   ├── recovery/route.ts
│           │   ├── recovery/confirm/route.ts
│           │   └── ...
│           └── ...
│
├── entities/                   # Domain objects (types only)
│   ├── pedido/
│   │   └── index.ts           # Pedido types
│   ├── parametro/
│   │   └── index.ts
│   └── usuario/
│       └── index.ts
│
├── features/                   # Domain features (logic + UI)
│   ├── auth/
│   │   ├── config/            # Auth config
│   │   │   ├── index.ts
│   │   │   └── ...
│   │   ├── model/             # Auth state hooks
│   │   │   └── ...
│   │   └── ui/                # Auth UI components
│   │       ├── login-form.tsx
│   │       └── ...
│   │
│   ├── pedidos/
│   │   ├── api/               # Pedidos API client
│   │   │   ├── index.ts       # Re-exports
│   │   │   ├── fetch-*.ts     # API calls (fetchPedidos, etc.)
│   │   │   └── post-*.ts
│   │   ├── model/             # Pedidos state
│   │   │   ├── app-data-provider.tsx  # Global orders/alerts/communications context
│   │   │   ├── use-*.ts               # Custom hooks
│   │   │   └── ...
│   │   ├── lib/               # Pedidos helpers
│   │   │   ├── formatters.ts
│   │   │   ├── transformers.ts
│   │   │   └── ...
│   │   └── ui/                # Pedidos UI
│   │       ├── order-detail-modal.tsx
│   │       ├── order-filter.tsx
│   │       ├── order-list.tsx
│   │       └── ...
│   │
│   ├── parametros/
│   │   ├── api/
│   │   │   └── ...            # Parametros API
│   │   ├── ui/
│   │   │   ├── parameter-form.tsx
│   │   │   └── ...
│   │   └── ...
│   │
│   ├── usuarios/
│   │   ├── api/
│   │   │   └── ...            # Usuarios API
│   │   ├── ui/
│   │   │   ├── user-table.tsx
│   │   │   └── ...
│   │   └── ...
│   │
│   └── shared/                # (deprecated: now in shared/)
│       └── ...
│
├── widgets/                    # Reusable UI containers
│   ├── app-shell/             # Main layout
│   │   └── ui/
│   │       ├── app-shell.tsx  # Sidebar + header + content frame
│   │       ├── sidebar.tsx
│   │       ├── header.tsx
│   │       └── ...
│   │
│   ├── auth-shell/            # Login/auth container
│   │   └── ui/
│   │       ├── auth-shell.tsx
│   │       └── ...
│   │
│   ├── pedido-dashboard/      # Order dashboard
│   │   ├── lib/
│   │   │   ├── formatters.ts
│   │   │   └── ...
│   │   └── ui/
│   │       ├── orders-list.tsx # Order card grid
│   │       ├── order-card.tsx
│   │       └── ...
│   │
│   └── ...
│
├── shared/                     # Cross-cutting infrastructure
│   ├── config/
│   │   ├── auth/
│   │   │   ├── automation-roles.ts         # Role definitions & helpers
│   │   │   └── automation-panel-roles.ts   # Role access control
│   │   ├── routes.ts                    # Path constants
│   │   └── ...
│   │
│   ├── hooks/
│   │   ├── use-session.ts
│   │   ├── use-mounted.ts
│   │   └── ...
│   │
│   ├── lib/
│   │   ├── api/
│   │   │   ├── config.ts              # Backend URL config
│   │   │   ├── http-client.ts         # Base fetch with auth
│   │   │   ├── auth-backend.ts        # Auth API calls
│   │   │   ├── normalize-auth-api.ts
│   │   │   ├── parse-api-error-message.ts
│   │   │   ├── types/
│   │   │   │   ├── auth.ts
│   │   │   │   └── index.ts
│   │   │   └── index.ts
│   │   │
│   │   ├── auth/
│   │   │   ├── automation-panel-access-denied.ts
│   │   │   ├── auth-secret.ts
│   │   │   ├── session-policy.ts
│   │   │   ├── session-expired.ts
│   │   │   ├── mock-auth-guard.ts
│   │   │   └── ...
│   │   │
│   │   ├── format/
│   │   │   ├── currency.ts            # BRL formatting
│   │   │   ├── percentage.ts
│   │   │   └── ...
│   │   │
│   │   ├── react/                     # React utilities
│   │   │   └── ...
│   │   │
│   │   ├── security/
│   │   │   └── ...
│   │   │
│   │   ├── validation/
│   │   │   └── ...
│   │   │
│   │   ├── fonts.ts                   # Font imports (Satoshi, etc.)
│   │   └── index.ts
│   │
│   ├── mocks/
│   │   ├── auth.ts                    # Mock credentials (dev only)
│   │   └── ...
│   │
│   ├── providers/
│   │   ├── SessionProvider.tsx        # NextAuth session wrapper
│   │   ├── SidebarProvider.tsx        # Sidebar state
│   │   └── ...
│   │
│   ├── types/
│   │   ├── api.ts                     # API types
│   │   ├── auth.ts                    # Auth types
│   │   ├── index.ts
│   │   └── ...
│   │
│   └── ui/
│       ├── primitives/                # Basic UI (from @system-automation/design-system)
│       │   ├── button.tsx
│       │   ├── input.tsx
│       │   ├── dialog.tsx
│       │   └── ...
│       │
│       └── composite/                 # Higher-level UI
│           └── ...
│
├── design-system/               # Git submodule (@system-automation/design-system)
│   ├── components/
│   │   ├── providers/
│   │   │   ├── CentralThemeProvider.tsx
│   │   │   ├── ThemeInitScript.tsx
│   │   │   └── ...
│   │   └── ...
│   └── ...
│
├── lib/                         # Misc utilities (legacy, being migrated to shared/)
│   ├── api/                     # API client utilities
│   │   └── ... (see above in shared/lib/api/)
│   ├── auth/                    # Auth utilities
│   │   └── ... (see above in shared/lib/auth/)
│   └── fonts.ts
│
├── public/                      # Static assets
│   ├── favicon.ico
│   ├── images/
│   └── ...
│
├── types/                       # Type definitions (legacy, see shared/types/)
│   └── ...
│
├── auth.ts                      # NextAuth.js configuration
├── middleware.ts                # Next.js middleware (route guards, auth checks)
├── next.config.ts               # Next.js configuration
├── next-auth.d.ts               # NextAuth type augmentation
├── next-env.d.ts                # Next.js generated types
├── tsconfig.json                # TypeScript configuration
├── eslint.config.mjs            # ESLint configuration
├── postcss.config.mjs           # PostCSS configuration
├── package.json                 # NPM dependencies
├── pnpm-lock.yaml               # Dependency lock file (pnpm)
├── pnpm-workspace.yaml          # pnpm workspace config
│
├── .env                         # Environment variables (DO NOT commit)
├── .env.example                 # Example .env
├── .gitignore                   # Git ignore rules
├── .gitmodules                  # Git submodule config (design-system)
├── .mcp.json                    # MCP configuration
├── .mcp.json.example
│
├── README.md                    # Project documentation
├── catalog-info.yml             # Backstage catalog metadata
├── AGENTS.md                    # Agent guidelines
├── CLAUDE.md                    # Claude context
├── .github/                     # GitHub Actions workflows
├── docs/                        # Documentation
│   └── mkdocs.yml
├── mkdocs.yml
│
└── .next/                       # (generated, ignored)
```

**Key Frontend Files:**

| File | Purpose |
|------|---------|
| `app/layout.tsx` | Root layout (theme, NextAuth session) |
| `app/(app)/layout.tsx` | App shell (sidebar, AppDataProvider) |
| `auth.ts` | NextAuth.js configuration (JWT, refresh, RBAC) |
| `middleware.ts` | Route guards, session validation, RBAC enforcement |
| `shared/lib/api/http-client.ts` | Base HTTP client with Bearer token injection |
| `shared/config/auth/automation-roles.ts` | Role definitions and access control |
| `features/pedidos/model/app-data-provider.tsx` | Global orders/alerts context |
| `widgets/app-shell/ui/` | Main layout (sidebar, header) |
| `next.config.ts` | Next.js configuration |
| `package.json` | Dependencies (pnpm workspaces) |

---

## Naming Conventions

### Backend (Python)

**Files:**
- Modules: lowercase with underscores (`motor_adequacao.py`, `repositorio_usuario.py`)
- Classes: PascalCase (aligns with ORM models like `User`, `Pedido`)
- Functions: snake_case (`processar_pedidos()`, `carregar_estoque()`)

**Directories:**
- Domain: `domain/` (pure business logic)
- Use cases: `application/casos_uso.py`
- Data access: `infrastructure/repositorio_*.py`
- Routes: `routes.py` (one per module)

**Database Tables:**
- snake_case: `auth_users`, `pedido_modificacoes`, `ordens_reserva`

---

### Frontend (TypeScript)

**Files:**
- Components: PascalCase (`.tsx`) — `OrderDetailModal.tsx`, `AppShell.tsx`
- Utilities/hooks: camelCase (`.ts`) — `useOrderData.ts`, `formatCurrency.ts`
- Styles: PascalCase with `.module.css` — `OrderDetailModal.module.css`

**Directories:**
- Features: kebab-case (`pedidos/`, `parametros/`, `usuarios/`)
- Sub-layers: lowercase (`api/`, `model/`, `ui/`, `lib/`)
- Components: PascalCase capitalization when referenced as directories

**Types:**
- Interfaces: PascalCase with `I` prefix (optional, not enforced) or plain PascalCase
- Enums: PascalCase

---

## Where to Add New Code

### New Backend Feature (Full DDD Module)

1. **Create module directory:**
   ```
   app/modules/seu_modulo/
   ├── domain/
   │   ├── __init__.py
   │   ├── agregado_principal.py
   │   └── ...
   ├── application/
   │   ├── __init__.py
   │   └── casos_uso.py
   ├── infrastructure/
   │   ├── __init__.py
   │   ├── repositorio_*.py
   │   └── ...
   ├── models.py                   # SQLAlchemy ORM
   ├── routes.py                   # FastAPI endpoints
   ├── schemas.py                  # Pydantic request/response
   ├── service.py                  # (legacy, being phased out)
   └── __init__.py
   ```

2. **Domain layer** (`domain/seu_arquivo.py`):
   - Write pure functions, no I/O
   - Example: `calcular_imposto(valor: float) -> float`
   - Import only from `value_objects.py`, `exceptions.py`

3. **Application layer** (`application/casos_uso.py`):
   - Orchestrate domain + infrastructure
   - Load data via repositories → call domain → save results
   - Use async/await throughout

4. **Infrastructure layer** (`infrastructure/repositorio_*.py`):
   - Database access via async SQLAlchemy
   - External API calls (Databricks, SMTP, etc.)
   - Pattern: `async def fetch_*(db: AsyncSession) -> T`

5. **Routes** (`routes.py`):
   - FastAPI endpoints with `@router.post()` etc.
   - Dependency injection: `Depends(get_db)`, `Depends(require_min_role())`
   - Call use case from `application/casos_uso.py`

6. **Register router** in `app/modules/__init__.py`:
   ```python
   from app.modules.seu_modulo.routes import router as seu_modulo_router
   
   all_routers = [
       ...,
       seu_modulo_router,
   ]
   ```

7. **Models & Schemas:**
   - `models.py` — SQLAlchemy `Base` classes
   - `schemas.py` — Pydantic request/response DTO

---

### New Backend Utility (Non-Domain Code)

- **Cross-module utility:** `app/shared/novo_servico/`
  - Example: New integration (Slack, external API)
  - Pattern: Async client + config from `app/shared/config/settings.py`
  - File: `app/shared/novo_servico/client.py`

- **Shared error type:** `app/shared/errors/novo_erro.py`
  - Exception class inheriting from `Exception`
  - Register handler in `app/shared/errors/handlers.py`

---

### New Frontend Feature (FSD Slice)

1. **Create feature directory:**
   ```
   features/seu_dominio/
   ├── api/
   │   ├── fetch-*.ts              # GET requests
   │   ├── post-*.ts               # POST requests
   │   ├── put-*.ts                # PUT requests
   │   └── index.ts                # Re-exports
   ├── model/
   │   ├── use-seu-dominio.ts      # Custom hooks
   │   ├── seu-dominio-context.ts  # React context (if needed)
   │   └── index.ts                # Re-exports
   ├── lib/
   │   ├── formatters.ts           # Transform data for display
   │   ├── transformers.ts         # Transform user input for API
   │   ├── helpers.ts              # Utility functions
   │   └── index.ts
   ├── ui/
   │   ├── SeuDominioList.tsx      # List component
   │   ├── SeuDominioForm.tsx      # Form component
   │   ├── SeuDominioModal.tsx     # Modal component
   │   └── index.ts                # Re-exports
   └── index.ts                    # Public API
   ```

2. **API layer** (`api/fetch-*.ts`):
   ```typescript
   import { apiFetch } from "@/lib/api/http-client";
   
   export async function fetchSeuDominio(): Promise<SeuDominioType[]> {
     return apiFetch<SeuDominioType[]>("/api/v1/seu-dominio");
   }
   ```

3. **Model layer** (`model/use-seu-dominio.ts`):
   - React hooks wrapping API calls
   - State management (useContext, useState)
   - Error handling, loading states

4. **UI layer** (`ui/SeuDominioList.tsx`):
   - React components for rendering
   - Call model hooks and API functions
   - Event handlers (onClick, onChange)

5. **Register in page:**
   ```typescript
   // app/(app)/seu-dominio/page.tsx
   import { SeuDominioList } from "@/features/seu_dominio/ui";
   
   export default function SeuDominioPage() {
     return <SeuDominioList />;
   }
   ```

---

### New Frontend Shared Utility

- **UI Primitive:** `shared/ui/primitives/novo-componente.tsx`
  - Reusable button, input, dialog, etc.
  - Wrapped from `@system-automation/design-system`

- **Hook:** `shared/hooks/use-novo-hook.ts`
  - Custom React hook for common pattern
  - Pattern: `export function useNovoHook(): T { ... }`

- **Formatter/Transformer:** `shared/lib/format/novo-formato.ts` or `shared/lib/security/novo-check.ts`
  - Pure function
  - Pattern: `export function formatNovoTipo(valor: T): string { ... }`

- **API Utility:** `shared/lib/api/novo-cliente.ts`
  - Extend `apiFetch()` for specific domain
  - Pattern: `export async function fetchNovoCliente(): Promise<T> { ... }`

---

## Special Directories

### Backend: `alembic/versions/`

**Purpose:** Database schema migrations

**Generated by:** `alembic revision --autogenerate -m "migration_name"`

**Current head:** 028 (confirme com `uv run alembic heads` — não confie em número anotado aqui)

**Pattern:**
- Each file is numbered: `001_init.py`, `002_add_column.py`, etc.
- Alembic auto-generates `upgrade()` and `downgrade()` functions
- Never edit by hand; always use `alembic` CLI for consistency

**When to add:**
- Schema change (new table, column, index)
- Constraint change
- Type change on existing column

**Run migrations:**
```bash
# Apply all pending
alembic upgrade head

# Downgrade one version
alembic downgrade -1
```

---

### Frontend: `design-system/`

**Purpose:** Git submodule pointing to `@system-automation/design-system`

**Checkout:** `git submodule update --init --recursive`

**Update:** `git submodule update --remote`

**When to change:** Upgrade design system version, PR review of design system changes

**Impact on frontend:**
- Import: `import { Button } from "@system-automation/design-system/components/ui"`
- Rebuild if changed: `rm -rf node_modules && pnpm install` (pnpm dependency issue with design-system)

---

### Frontend: `public/`

**Purpose:** Static assets (favicon, images, SVGs)

**Access:** Served at `/favicon.ico`, `/images/...` in browser

**Pattern:** Never commit large binaries; use CDN for images if possible

---

### Frontend: `.next/`

**Purpose:** Generated build output

**Committed:** No (in `.gitignore`)

**Regenerate:** `pnpm build`

---

## Testing Directories

**Backend:** `app/tests/` (if exists)
- Unit tests: mirror `app/modules/*/tests/`
- Integration tests: fixture data, DB setup

**Frontend:** Co-located `*.test.ts(x)` or `__tests__/` directories
- Example: `features/pedidos/api/__tests__/fetch-pedidos.test.ts`

---

## Entry Points for Code Navigation

**Backend:**
- API entry: `backend/app/main.py`
- Module routers: `backend/app/modules/*/routes.py`
- Use cases: `backend/app/modules/*/application/casos_uso.py`
- Domain logic: `backend/app/modules/*/domain/*.py`

**Frontend:**
- Root: `frontend/app/layout.tsx`
- App shell: `frontend/app/(app)/layout.tsx`
- Pages: `frontend/app/(app)/*/page.tsx`
- Auth: `frontend/auth.ts`
- Middleware: `frontend/middleware.ts`

---

## Import Path Aliases

**Backend:** (None explicitly configured; use relative or absolute imports)

**Frontend:** (from `tsconfig.json`)
- `@/` → project root (e.g., `@/features/pedidos`)
- `@system-automation/design-system` → npm package

---

## Shared Configuration

**Backend:**
- `.env` — Environment variables (database, Databricks, Redis, SMTP, secrets)
- `alembic.ini` — Database migration config
- `pyproject.toml` — Dependencies, tool config

**Frontend:**
- `.env` — Backend API URL, auth config
- `tsconfig.json` — TypeScript paths, compiler options
- `next.config.ts` — Next.js middleware, image optimization
- `package.json` — Dependencies (pnpm workspaces)

---

*Structure analysis: 2026-08-04*
