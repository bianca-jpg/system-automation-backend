<!-- refreshed: 2026-08-04 -->
# Architecture

**Analysis Date:** 2026-08-04

## System Overview

SYSTEM-AUTOMATION is a two-tier order (OR) fulfillment platform for wholesale operations. The system ingests orders and stock from Databricks, runs an adequação (matching) engine to allocate inventory across orders, manages authentication with RBAC, and provides a dashboard for order management.

```text
┌────────────────────────────────────────────────────────────────────┐
│              FRONTEND: Next.js App Router (FSD/DDD)                │
│           `frontend/` (app/, features/, etc.)          │
├────────────────────────────────────────────────────────────────────┤
│  Route Layer: Pages (pedidos, parametros, usuarios, visao-geral)   │
│  Widget Layer: UI containers (app-shell, pedido-dashboard)         │
│  Feature Layer: Domain logic (auth, pedidos, parametros, usuarios) │
│  Entity Layer: Domain objects (pedido, parametro, usuario)         │
│  Shared Layer: UI primitives, utilities, auth, providers           │
│  Auth: NextAuth.js + JWT tokens from backend                       │
└───────┬──────────────────────────────────────────────────────────┘
        │ HTTP REST API (JSON)
        │ Bearer token in Authorization header
        ▼
┌────────────────────────────────────────────────────────────────────┐
│       BACKEND: FastAPI with DDD Layering (modular monolith)        │
│         `backend/app/modules/` (domain-driven)         │
├────────────────────────────────────────────────────────────────────┤
│ ROUTES (HTTP entry points) ← SERVICES (old layer, being replaced) │
│ ↓                                                                   │
│ APPLICATION/CASOS_USO (use case orchestration)                     │
│ ↓                                                                   │
│ DOMAIN (pure business logic, no I/O)                               │
│ ↓                                                                   │
│ INFRASTRUCTURE (repositories, external APIs, DB access)            │
│                                                                     │
│ Modules: auth, pedidos, ingestao, parametros, comunicacoes         │
│ (core, health = simple route-only; alertas, history = deprecated)  │
│                                                                     │
│ SHARED (cross-cutting: config, database, security, logging)        │
└─────────────────────────────────────────────────────────────────┬─┘
        │ (async SQLAlchemy ORM)
        ▼
┌────────────────────────────────────────────────────────────────────┐
│                  POSTGRES Database                                  │
│        async SQLAlchemy + asyncpg + Alembic migrations             │
├────────────────────────────────────────────────────────────────────┤
│ Tables: auth_users, pedidos, estoque, ordens_reserva,              │
│         pedido_modificacoes, pedidos_processados, parametros       │
└────────────────────────────────────────────────────────────────────┘
        ▲
        │ (async worker tasks)
        │
┌────────────────────────────────────────────────────────────────────┐
│  CELERY Beat + Workers (background task queue)                     │
│  `app/workers/` — Periodic Databricks sync every 2h (default)      │
├────────────────────────────────────────────────────────────────────┤
│ Broker: Redis (localhost:6379/0)                                   │
│ Backend: Redis (localhost:6379/1)                                  │
│ Beat schedule: sincronizar_databricks (every INGESTAO_INTERVALO)   │
└────────────────────────────────────────────────────────────────────┘
        ▲
        │ (SQL query + polling)
        │
┌────────────────────────────────────────────────────────────────────┐
│           DATABRICKS SQL Statement Execution API                   │
│    `app/shared/infrastructure/databricks_client.py` — async HTTP   │
├────────────────────────────────────────────────────────────────────┤
│ Disposition: INLINE (< 25 MiB) or EXTERNAL_LINKS (> 25 MiB)        │
│ Polling: up to 60 attempts, 2s interval per statement              │
│ Auto-fallback: INLINE fails → retry with EXTERNAL_LINKS            │
└────────────────────────────────────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **FastAPI App** | HTTP server, CORS, exception handlers, lifespan | `backend/app/main.py` |
| **Auth Module (DDD)** | Login, JWT, RBAC, user seed | `backend/app/modules/auth/**` |
| **Pedidos Module (DDD)** | Order management, adequação matching | `backend/app/modules/pedidos/**` |
| **Ingestão Module (DDD)** | Databricks→Postgres full refresh sync | `backend/app/modules/ingestao/**` |
| **Parametros Module (DDD)** | Configuration knobs (criterio, tolerancia) | `backend/app/modules/parametros/**` |
| **Comunicacoes Module (DDD)** | Email sending via SMTP | `backend/app/modules/comunicacoes/**` |
| **Core Module** | System info, version endpoints | `backend/app/modules/core/` |
| **Health Module** | Health check, readiness probe | `backend/app/modules/health/` |
| **Shared: Database** | Async SQLAlchemy session, engine, pool | `backend/app/shared/database/**` |
| **Shared: Security** | JWT decode, RBAC dependency injectors | `backend/app/shared/security/**` |
| **Shared: Config** | Settings from env, Databricks config | `backend/app/shared/config/**` |
| **Shared: Infrastructure** | Databricks client, external integrations | `backend/app/shared/infrastructure/**` |
| **Shared: Logging/Metrics** | Structured logging, Prometheus metrics | `backend/app/shared/logging/**`, `metrics/` |
| **Celery App** | Task broker, beat schedule | `backend/app/workers/celery_app.py` |
| **Frontend: Pages** | Route handlers, UI, user interaction | `frontend/app/(app)/**` |
| **Frontend: Features** | Domain feature logic (auth, orders, etc.) | `frontend/features/**` |
| **Frontend: Entities** | Domain objects, type definitions | `frontend/entities/**` |
| **Frontend: Shared** | Global state, auth providers, UI kit | `frontend/shared/**` |
| **Frontend: Widgets** | Reusable UI containers, dashboards | `frontend/widgets/**` |

## Pattern Overview

**Overall:** Modular monolith backend (with emerging DDD structure) + FSD frontend architecture

**Key Characteristics:**
- **DDD Layering (Backend):** Each domain module has `domain/` (pure logic), `application/casos_uso.py` (orchestration), `infrastructure/` (repositories, external APIs)
- **FSD Layering (Frontend):** Clear separation: pages (routes) → widgets (containers) → features (domain) → entities (types) → shared (utilities)
- **Full Refresh Strategy:** Databricks → Postgres data replaced entirely on sync (no incremental)
- **Async-First:** FastAPI + asyncio + asyncpg for non-blocking I/O throughout backend
- **Hierarchical RBAC:** 5 backend roles (basico < operacional < gestor < administrador < admin_tecnico)
- **Channel Isolation:** Orders/stock partitioned by `canal` (Franquia vs. Multimarca); matching respects boundaries
- **Stateful Matching:** Adequação tracks processed order pairs (nr_pedido, cd_prod_cor) to prevent re-processing
- **JWT Token Refresh:** Frontend auto-refreshes when < 60s to expiry; backend validates roles on every call
- **Dual-Disposition Databricks:** Auto-fallback from INLINE (25 MiB limit) → EXTERNAL_LINKS for large result sets

## Backend: Layered Architecture (DDD)

### Layer 1: HTTP Routes

**Purpose:** REST endpoints, request/response parsing, FastAPI dependency injection

**Location:** `backend/app/modules/*/routes.py` (plus simple ones in `core/`, `health/`)

**Key Routes:**
- `auth/routes.py` — POST /api/v1/auth/signin, /refresh, /logout; signup (OTP flow)
- `pedidos/routes.py` — GET /api/v1/pedidos/produtos, /produtos/clientes, /lookup, /resumo, /evolucao-faturamento; POST /processamentos; PUT /produtos/grades; POST /produtos/aprovar (as rotas legadas GET /api/v1/pedidos e /historico foram removidas; a rota de listagem de OR bounded também foi removida por não ter consumidor no frontend)
- `ingestao/routes.py` — POST /api/v1/ingestao/sincronizar, GET /status
- `parametros/routes.py` — GET/PUT /api/v1/parametros/{key}
- `comunicacoes/routes.py` — POST /api/v1/comunicacoes/email
- `core/routes.py` — GET /core/version, /core/info
- `health/routes.py` — GET /health, /ready

**Pattern:** FastAPI `@router.post()` + dependency injection for `Depends(get_db)`, auth, validation

**Depends on:** Application layer (`casos_uso`), FastAPI, Pydantic for schemas

---

### Layer 2: Application / Use Cases

**Purpose:** Orchestrate domain logic and infrastructure (repositories, services), no business logic

**Location:** `backend/app/modules/*/application/casos_uso.py`

**Responsibilities:**
- Load data from infrastructure (repositories)
- Call domain functions with loaded data
- Save results back to infrastructure
- Handle transaction boundaries
- Return DTOs/schemas for HTTP response

**Example: `pedidos/application/casos_uso.py`**
```
gerar_ordem_reserva(nr_pedido, criterio, tolerancia) →
  1. carregar_pedidos_itens() [from infrastructure]
  2. carregar_estoque() [from infrastructure]
  3. processar_pedidos() [domain function]
  4. salvar_ordens_reserva() [to infrastructure]
  5. return lista_itens_ou
```

**Depends on:** Domain layer, infrastructure layer (repositories)

---

### Layer 3: Domain

**Purpose:** Pure business logic, no I/O, no framework code, easily testable

**Location:** `backend/app/modules/*/domain/**`

**Key Abstractions per Module:**

**auth/domain:**
- `email.py` — Email value object validation
- `senha.py` — Password hashing logic
- `tokens.py` — JWT/refresh token generation (algorithm, expiry)

**pedidos/domain:**
- `motor_adequacao.py` — Core matching engine: `processar_pedidos()` → `_processar_pedidos_canal()` → `adequar_grade_produto()`
- `value_objects.py` — `canal_bucket()`, `get_tamanho_idx()`, size ranking system, stock key generation
- `relatorios.py` — Transform matched orders into billing/history format

**ingestao/domain:**
- `traducao_databricks.py` — Parse strings → typed objects, handle Databricks quirks
- `agregacao.py` — Group/deduplicate (nr_pedido, cd_prod_cor, sg_tamanho, canal)

**parametros/domain:**
- `coercao.py` — Type coercion (str → float, int, bool)
- `leitura_resiliente.py` — Fallback read logic for missing params
- `exceptions.py` — Domain-level exceptions

**comunicacoes/domain:**
- `assunto.py` — Email subject/body templates

**Pattern:** Pure functions, value objects, no external dependencies (no SQLAlchemy ORM, no HTTP, no file I/O)

---

### Layer 4: Infrastructure / Repositories

**Purpose:** Database access, external service calls, transaction management

**Location:** `backend/app/modules/*/infrastructure/**`

**Key Repositories:**

**pedidos/infrastructure:**
- `repositorio_ordens.py` — Save/load ordens_reserva, pedidos_processados, pedido_modificacoes from DB
- `repositorio_ingestao_readmodel.py` — Read pedidos, estoque, status_credito (read-only, populated by ingestao module)

**ingestao/infrastructure:**
- `databricks_reader.py` — HTTP client to Databricks SQL API
- `repositorio_snapshot.py` — Save snapshots of synced data (audit trail)

**auth/infrastructure:**
- `repositorio_usuario.py` — Load/save auth_users, password verification
- `repositorio_otp.py` — Save/load OTP challenges

**parametros/infrastructure:**
- `repositorio_parametro.py` — CRUD on parametros table
- `repositorio_change_request.py` — Track parameter change requests (audit)

**comunicacoes/infrastructure:**
- `repositorio_comunicacao.py` — Log sent communications

**Pattern:** Async methods, `async with db.begin()` for transactions, exception raising for business rules

**Depends on:** SQLAlchemy 2.0 async ORM models, Databricks client, shared database session

---

### Layer 5: Shared Infrastructure

**Purpose:** Cross-cutting services (database, security, logging, config)

**Location:** `backend/app/shared/**`

**`database/session.py`:**
- Async SQLAlchemy engine creation, connection pooling
- `get_db()` dependency injector for FastAPI
- Transaction context managers
- Alembic migration tracking (head currently at 028 — confirme com `uv run alembic heads`)

**`security/__init__.py`:**
- JWT encode/decode (HS256)
- Shim que reexporta `app.modules.auth.dependencies` (a fábrica `require_min_role()` e o guard `require_tecnico` continuam disponíveis só pelo caminho profundo)
- `require_viewer()`, `require_actor()`, `require_gestor()`, `require_admin()` shorthand guards
- Role hierarchy (basico=10, operacional=20, gestor=30, administrador=40, admin_tecnico=50)

**`config/settings.py`:**
- `Settings` Pydantic model from `.env`
- Databricks host, token, table names
- Redis broker URL, Celery config
- SMTP config (for email)
- Database DSN, secrets (JWT key)

**`infrastructure/databricks_client.py`:**
- Async HTTP client for Databricks SQL Statement Execution API
- POST to `/api/2.0/sql/statements/` with disposition (INLINE or EXTERNAL_LINKS)
- Poll `/api/2.0/sql/statements/{statement_id}` until SUCCEEDED/FAILED
- Auto-fallback: INLINE → EXTERNAL_LINKS on size overflow
- Collects results from `data_array` or pre-signed URLs

**`logging/setup.py`:**
- Structured logging (JSON format for cloud aggregation)
- Per-module loggers

**`errors/handlers.py`:**
- Exception-to-HTTP-response mapping
- 400, 401, 403, 404, 422, 500 handlers

---

## Frontend: FSD Architecture

### Layer 1: Pages & Routes

**Purpose:** Next.js App Router routes, data loading, page composition

**Location:** `frontend/app/`

**Structure:**
- `app/layout.tsx` — Root layout, NextAuth session init, theme/providers
- `app/login/` — Login pages
- `app/(app)/` — Authenticated routes (dashboard)
  - `(app)/pedidos/page.tsx` — Order management
  - `(app)/parametros/page.tsx` — Parameter tuning
  - `(app)/usuarios/page.tsx` — User management
  - `(app)/visao-geral/page.tsx` — Dashboard overview
  - `(app)/historico/page.tsx` — Order history
  - `(app)/alertas/page.tsx` — Alerts

**Pattern:** Server components by default, client boundary at provider injection and interactive features

**Depends on:** Widgets, features, shared providers

---

### Layer 2: Widgets

**Purpose:** Reusable UI containers, layout components, dashboard elements

**Location:** `frontend/widgets/`

**Key Widgets:**
- `app-shell/ui/` — Main layout frame, sidebar, header
- `auth-shell/ui/` — Login/signup page container
- `pedido-dashboard/ui/orders-list.tsx` — Order card grid, filtering, action buttons
- `pedido-dashboard/lib/` — Dashboard logic (formatting, helpers)

**Pattern:** Composition of entities and shared UI, event handlers → feature API calls

---

### Layer 3: Features

**Purpose:** Domain feature implementation, API integration, state management

**Location:** `frontend/features/`

**Structure per Feature (e.g., `pedidos/`):**
- `pedidos/api/` — API client functions (`fetch*`, `post*`, `put*`)
  - Example: `fetchPedidos()`, `postAdequacao()`, `putGrade()`
- `pedidos/model/` — React context, hooks, state management
  - Example: `AppDataProvider` (global orders/alerts/communications state)
- `pedidos/lib/` — Domain helpers, formatters, validators
  - Example: order filtering, column transforms
- `pedidos/ui/` — Feature-specific UI components
  - Example: `order-detail-modal.tsx`, `order-filter.tsx`

**Key Features:**
- **auth/config/** — Auth configuration, role mapping
- **auth/model/** — Auth state hooks
- **auth/ui/** — Login/signup/recovery forms
- **pedidos/api/** — Orders API (list, detail, adequacao, grade update)
- **pedidos/model/** — `AppDataProvider` (global state)
- **pedidos/ui/** — Order modals, filters, lists
- **pedidos/lib/** — Order transformers, status helpers
- **parametros/api/** — Parameter CRUD
- **parametros/ui/** — Parameter forms
- **usuarios/api/** — User CRUD
- **usuarios/ui/** — User management pages

**Pattern:** React hooks (useContext, useState, useCallback), async/await API calls, error handling

---

### Layer 4: Entities

**Purpose:** Domain object types and schemas

**Location:** `frontend/entities/`

**Key Entities:**
- `pedido/` — Order types, schemas, constants
- `parametro/` — Parameter types
- `usuario/` — User types

**Pattern:** TypeScript interfaces/types, zero runtime code, data contracts only

---

### Layer 5: Shared Infrastructure

**Purpose:** Cross-cutting utilities, UI kit, auth context, API client base

**Location:** `frontend/shared/`

**`shared/config/auth/`:**
- `automation-roles.ts` — Role level definitions, role mapping functions
- `automation-panel-roles.ts` — `canAccessautomationPanel()`, role access rules

**`shared/config/routes.ts`:**
- Route path constants (prevents typos, centralizes path logic)

**`shared/providers/`:**
- `SessionProvider.tsx` — NextAuth session context wrapper
- `SidebarProvider.tsx` — Sidebar state (open/closed)
- `AppDataProvider.tsx` — Global orders/alerts/communications context (moved to features/pedidos/model/)

**`shared/lib/`:**
- `api/config.ts` — `getApiBaseUrl()`, `isBackendApiConfigured()`
- `api/http-client.ts` — `apiFetch<T>()` with Bearer token injection
- `api/auth-backend.ts` — `postSignIn()`, `postRefresh()`, `postSignOut()`
- `api/normalize-auth-api.ts` — Response normalization
- `api/parse-api-error-message.ts` — Error parsing
- `auth/automation-panel-access-denied.ts` — Access denied error
- `auth/session-policy.ts` — Session timeout, token expiry logic
- `format/` — Number/currency formatting (BRL, percentages)
- `react/` — Custom React hooks
- `security/` — Security utilities
- `validation/` — Input validation

**`shared/ui/`:**
- `primitives/` — Basic components (Button, Input, Dialog, etc.) from `@system-automation/design-system`
- `composite/` — Higher-level components built on primitives

**`shared/types/`:**
- `index.ts` — Global type exports
- `api.ts` — API response types, error types
- `auth.ts` — Authentication types

**`shared/mocks/`:**
- `auth.ts` — Mock credentials for development

**Pattern:** Context providers, hooks, utility functions, no page-specific logic

---

## Data Flow: Primary Request Path (Generate OR)

1. **Frontend User Action** — User clicks "Gerar Ordem de Reserva" on order card
   - File: `frontend/widgets/pedido-dashboard/ui/orders-list.tsx`
   - Calls: `postAdequacao(nr_pedido, criterio, tolerancia)`

2. **HTTP Request** — POST `/api/v1/pedidos/{nr}/adequacao`
   - Client: `frontend/features/pedidos/api/` → `frontend/lib/api/http-client.ts`
   - Bearer token injected from NextAuth session

3. **Backend Route Handler** — FastAPI endpoint
   - File: `backend/app/modules/pedidos/routes.py:adequacao_endpoint()`
   - Parses request, validates JWT via `Depends(require_min_role(...))`
   - Calls use case: `gerar_ordem_reserva_por_pedido()`

4. **Application Use Case** — Orchestration layer
   - File: `backend/app/modules/pedidos/application/casos_uso.py`
   - Loads: `carregar_pedidos_itens()`, `carregar_estoque()`, `carregar_parametros()`, `carregar_processados()`
   - Calls: Domain function `processar_pedidos(dados, estoque, ...)`

5. **Domain Business Logic** — Pure matching engine
   - File: `backend/app/modules/pedidos/domain/motor_adequacao.py`
   - Partitions by channel → greedy selection → grade adequation
   - Returns: `{ nr_pedido -> [items with status, qty, value, diff] }`

6. **Infrastructure Save** — Persist results
   - File: `backend/app/modules/pedidos/infrastructure/repositorio_ordens.py`
   - Inserts: `ordens_reserva` table (tipo='com')
   - Marks: `pedidos_processados` (nr_pedido, cd_prod_cor pair)

7. **HTTP Response** — Return to frontend
   - Status: 200 OK
   - Body: `{ items: [...], status: "success", ... }`

8. **Frontend State Update** — Update React context
   - File: `frontend/features/pedidos/model/app-data-provider.tsx`
   - Re-fetches: `AppDataProvider.refetchOrders()`
   - UI: Order card moves to "Grado com OR" or "Stand By" state

---

## Data Flow: Secondary Flow (Databricks → Postgres Sync)

**Triggered:** Celery beat every 2h (default `INGESTAO_INTERVALO_SEGUNDOS=7200`) OR manual POST /api/v1/ingestao/sincronizar

1. **Celery Beat Trigger**
   - File: `backend/app/workers/celery_app.py`
   - Task: `app.workers.tasks.ingestao.sincronizar_databricks()`

2. **Task Execution** — Create new event loop + asyncpg engine per run
   - File: `backend/app/workers/tasks/ingestao.py:sincronizar_databricks()`
   - Calls: `_sincronizar()`

3. **Fetch Pedidos from Databricks**
   - Query: `SELECT ... FROM tabela_pedidos WHERE qt_entregar > 0 AND dt_emissao >= month_start`
   - Client: `backend/app/shared/infrastructure/databricks_client.py:executar_consulta()`
   - Returns: List of dicts (all string values)

4. **Parse + Deduplicate**
   - File: `backend/app/modules/ingestao/domain/traducao_databricks.py`
   - Converts: String values → typed (int, float, date)
   - File: `backend/app/modules/ingestao/domain/agregacao.py`
   - Groups by: (nr_pedido, cd_prod_cor, sg_tamanho, canal)
   - Deduplicates: Keep first entry per group

5. **Fetch Estoque from Databricks**
   - Query: `SELECT ... FROM tabela_estoque`
   - Aggregates: Group by (cd_prod_cor, sg_tamanho, canal), sum quantities

6. **Upsert to Postgres**
   - File: `backend/app/modules/ingestao/service.py:sincronizar_tudo()`
   - Transaction: `async with db.begin()`
   - Deletes: All rows from `pedidos` table
   - Inserts: New rows (full refresh, no incremental)
   - Same for `estoque` table

7. **Error Handling** — Graceful failure
   - If Databricks offline or token missing → log warning, return error dict
   - Celery beat continues (no crash)

---

## Key Abstractions

### Adequação Matching Engine

**Purpose:** Allocate limited stock across orders, respecting channel isolation + tolerance rules

**Location:** `backend/app/modules/pedidos/domain/motor_adequacao.py`

**Flow:**
1. **Channel Partition** — Separate orders by canal (Franquia / Multimarca)
2. **Per-Channel Greedy** —
   - Group orders by product (cd_prod_cor)
   - Rank orders by value (descending) — highest value first
   - For each order, apply grade adequation:
     - **Status Grade:** Total shortage > tolerance? → "Stand By"; else "Gerar OR"
     - **Qty Adjust:** Per item, min/max by available stock + tolerance budget
     - **Value Adjust:** Recalculate based on adjusted qty
3. **Extreme Size Preference** — When have budget to increase qty, prefer sizes at extremes (smallest/largest)
4. **Output:**
   - `status_item`: "Gerar OR" or "Pedido em Stand By"
   - `qt_liquida`, `vl_liquido`: Adjusted quantities/values
   - `diff_valor`: Change from original
   - `motivo_stand_by`: Why deferred (if applicable)

---

### Size Ranking System

**Purpose:** Map size strings to numeric indices (detect extremes)

**Location:** `backend/app/modules/pedidos/domain/value_objects.py`

**Pattern:**
```python
RANKINGS = {
    "ALFABETICO": ["XPP", "PP", "P", "M", "G", "GG", ...],
    "CAMISA_NUMERICA": ["1", "2", "3", ...],
    ...
}

get_tamanho_idx(tamanho: str, tipo: str) → int
```

- Pure numeric sizes → sort by int value
- Lookup in ranking → position index
- Fallback → 999 (unknown)

---

### Role-Based Access Control (RBAC)

**Purpose:** Enforce authorization at endpoint and UI level

**Backend Files:**
- `backend/app/modules/auth/roles.py` — Role definitions, hierarchy
- `backend/app/shared/security/__init__.py` — Dependency injectors

**Frontend Files:**
- `frontend/shared/config/auth/automation-roles.ts` — Role level definitions
- `frontend/shared/config/auth/automation-panel-roles.ts` — Role mapping and access rules

**Hierarchy:**
```
basico (10) < operacional (20) < gestor (30) < administrador (40) < admin_tecnico (50)
```

**Enforcement:**
- Backend: FastAPI dependency `Depends(require_min_role(operacional))`
- Frontend: Middleware validation of `session.user.role`
- Role re-check on every request (DB lookup) — not just JWT claim

---

### Channel Isolation

**Purpose:** Keep Franquia and Multimarca orders/stock separate

**Implementation:**
- Data Model: Both `pedidos` and `estoque` tables have `canal` column
- Load Time: `carregar_estoque()` returns `{ "Franquia": {...}, "Multimarca": {...} }`
- Matching: Each order's items consume from same canal bucket
- Default: `canal=None` → map to "Franquia" (fallback)

---

## Entry Points

### Backend FastAPI

**Location:** `backend/app/main.py`

**Triggers:** Docker container start, local `uvicorn app.main:app --reload`

**Responsibilities:**
- FastAPI app initialization with lifespan context manager
- CORS middleware
- Exception handler registration
- Router inclusion from `app/modules/__init__.py`
- Auth seed on startup (if `SEED_AUTH_ON_STARTUP=true`)

---

### Frontend Root Layout

**Location:** `frontend/app/layout.tsx`

**Triggers:** Browser navigation to `/`

**Responsibilities:**
- Load NextAuth session (server-side)
- Inject theme bootstrap script (prevent flash)
- Wrap children in providers (Theme, Session)

---

### Frontend App Layout

**Location:** `frontend/app/(app)/layout.tsx`

**Triggers:** Navigation to `/` (authenticated route)

**Responsibilities:**
- Wrap in `SidebarProvider` (sidebar state)
- Wrap in `AppDataProvider` (global orders/alerts/communications context)
- Render `AppShell` (sidebar, header, content frame)

---

### Celery Beat

**Location:** `backend/app/workers/celery_app.py`

**Triggers:** `celery -A app.workers.celery_app beat`

**Responsibilities:**
- Load beat schedule configuration
- Run periodic task: `sincronizar_databricks` every `INGESTAO_INTERVALO_SEGUNDOS`

---

## Architectural Constraints

- **Threading:** Single-threaded event loop (asyncio); Celery tasks run in separate processes (avoid "Future attached to different loop" error)
- **Global State:** No module-level singletons except SQLAlchemy engine (created once per process, thread-safe)
- **Circular Imports:** Resolved via deferred imports in use-case layer; domain → no upward dependencies
- **Database Transactions:** All writes use `async with db.begin()` context manager for ACID guarantees
- **Full Refresh:** No incremental sync; each Databricks fetch replaces entire table (simplifies state consistency)
- **Channel Isolation:** Enforced at matching engine (no cross-channel stock allocation); database constraint recommended

---

## Communication Between Frontend and Backend

**Protocol:** HTTP REST API (JSON) + JWT Bearer token authentication

**Frontend → Backend:**
1. Client calls `apiFetch(path, init)`
2. Injects `Authorization: Bearer {token}` from NextAuth session
3. Backend validates JWT + role before executing use case
4. Response returned as JSON

**Token Lifecycle:**
- User logs in via POST `/api/v1/auth/signin` → get access + refresh tokens
- NextAuth stores tokens in encrypted cookie (server-side)
- On every request, frontend checks: if < 60s to expiry → POST `/api/v1/auth/refresh`
- Backend returns new tokens, NextAuth updates cookie
- If token invalid (401) → middleware redirects to login

**Error Handling:**
- Backend raises domain exceptions → FastAPI exception handlers → HTTP 400/401/403/422/500
- Frontend catches `ApiError` (has `.status` property) → user notification or redirect

---

*Architecture analysis: 2026-08-04*
