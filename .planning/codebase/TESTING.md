# Testing Patterns

**Analysis Date:** 2026-08-04

## Backend (Python/FastAPI)

### Test Framework

**Runner:** `pytest` 8.3.0+
- **Config:** `backend/pyproject.toml`
  ```toml
  [tool.pytest.ini_options]
  pythonpath = ["."]
  testpaths = ["app/tests"]
  asyncio_mode = "auto"
  ```
- **Test path:** `backend/app/tests/`
- **Async mode:** `asyncio_mode = "auto"` (auto-detects and runs async fixtures/tests)
- **File discovery:** `test_*.py` pattern

**Async Support:** `pytest-asyncio` 0.24.0+ for async test execution

**HTTP Testing:** `fastapi.testclient.TestClient` for synchronous client testing of async endpoints

**Assertion Library:** Built-in `assert` statements (no additional library required)

### Run Commands

```bash
# Inside container or with uv
uv run pytest                          # Run all tests in app/tests/
uv run pytest -v                       # Verbose output
uv run pytest app/tests/test_auth.py   # Run specific test file
uv run pytest -k "test_name"           # Run tests matching pattern
```

### Test File Organization

**Location:** `backend/app/tests/` (co-located with `app/` source)

**Naming:** `test_*.py` — pytest auto-discovery pattern

**Current test files (11 total):**
- `test_auth.py` — Auth module: user seeding, JWT handling
- `test_auth_flows.py` — Auth flows: sign-in, signup, recovery
- `test_comunicacoes.py` — Email communication to commercial team
- `test_health.py` — Health check endpoints (liveness/readiness)
- `test_ingestao_api_leitura.py` — Databricks API data reading
- `test_ingestao_parse.py` — Databricks data parsing/normalization
- `test_ingestao_sync.py` — Databricks ingest synchronization
- `test_parametros.py` — Business parameter CRUD and change requests
- `test_pedidos_motor.py` — Order matching engine (adequação logic)
- `test_pedidos_routes.py` — Order endpoints (8 endpoints, 18+ test cases)
- `test_schema_guard.py` — Database schema integrity checks

**Layout:** Flat structure in `app/tests/`; no subdirectories per module

### Test Structure

**Basic Pattern (pytest):**

```python
# app/tests/test_auth.py
import pytest
from sqlalchemy import func, select

from app.modules.auth.models import AuthUser
from app.modules.auth.bootstrap.seed import DEFAULT_AUTH_USERS, seed_auth_users
from app.shared.database.session import async_session_factory

@pytest.mark.asyncio
async def test_seed_auth_users_is_idempotent():
    async with async_session_factory() as session:
        first = await seed_auth_users(session)
        second = await seed_auth_users(session)
        assert second == 0
```

**Patterns:**
- `@pytest.mark.asyncio` decorator marks async tests
- `async with` for database session/resource management
- Direct `assert` statements for assertions
- Fixtures defined in `conftest.py` for shared setup

**Conftest and Fixtures:**

Location: `backend/app/tests/conftest.py`

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

@pytest.fixture(autouse=True)
def _dispose_db_engine_after_test():
    """asyncpg prende a conexão ao event loop que a abriu; testes async diretos
    (pytest-asyncio) e testes via TestClient (anyio) usam loops diferentes, e o
    engine é um singleton global. Sem descartar o pool entre testes, o próximo
    teste pode reusar uma conexão presa a um loop já fechado."""
    yield
    from app.shared.database.session import engine
    asyncio.run(engine.dispose())
```

**Key fixtures:**
- `client` — FastAPI TestClient for HTTP testing
- `_dispose_db_engine_after_test` — Auto-cleanup of database engine between tests (prevents asyncpg connection reuse issues)

### HTTP Testing Pattern

From `backend/app/tests/test_pedidos_routes.py`:

```python
def test_listar_pedidos_retorna_lista_com_shape_esperado(client, viewer_token):
    resp = client.get("/api/v1/pedidos/lookup", headers=auth_header(viewer_token))

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    if body:
        primeiro = body[0]
        for campo in ("id", "client", "value", "status", "canal", ...):
            assert campo in primeiro
```

**Patterns:**
- TestClient wraps sync around async app
- Headers passed via `headers={"Authorization": f"Bearer {token}"}`
- Response validation: `status_code`, `json()` body, shape assertions
- Fixtures for auth tokens: `viewer_token`, `actor_token` (created per test)

### Mocking

**Framework:** `unittest.mock` (Python standard library)

**Patterns observed in `test_pedidos_routes.py`:**

```python
from unittest.mock import AsyncMock, patch

async def test_adequar_pedidos_fluxo_completo_mockado(client, actor_token):
    with (
        patch("app.modules.pedidos.application.casos_uso.carregar_pedidos_itens", new=AsyncMock(return_value=dados_fake)),
        patch("app.modules.pedidos.application.casos_uso.carregar_estoque", new=AsyncMock(return_value=estoque_fake)),
        patch("app.modules.pedidos.application.casos_uso.salvar_ordens_reserva", new=AsyncMock()) as mock_salvar_or,
    ):
        resp = client.post("/api/v1/pedidos/adequar", headers=auth_header(actor_token))
    
    assert resp.status_code == 200
    mock_salvar_or.assert_awaited_once()
```

**What to Mock:**
- External service calls (Databricks, Redis health checks)
- Data access functions when testing HTTP behavior (avoid database writes)
- Async operations that would slow tests
- Use `AsyncMock` for coroutines, `patch()` context manager for injection
- Patch at import location in module namespace (e.g., `app.modules.pedidos.application.casos_uso`)

**What NOT to Mock:**
- SQLAlchemy ORM operations (use real async session in dedicated tests)
- FastAPI routing/dependency injection (test via TestClient)
- Pydantic validation (rely on schema tests)
- Actual database writes (use real session without commit)

### Database Testing

**Pattern for data writes (no mocking):**

```python
async def test_salvar_ordens_reserva_par_novo_insere_sem_erro():
    par = (999999010, "PROD_NOVO")
    async with async_session_factory() as session:
        await pedidos_service.salvar_ordens_reserva(
            session, {par: [{"nr_pedido": par[0], "cd_prod_cor": par[1]}]}, tipo="com"
        )
        await session.flush()  # antes da 014 isto levantava IntegrityError
```

**Key points:**
- Use `async_session_factory()` directly for isolated database tests
- Call `await session.flush()` to validate writes without committing
- Never `commit()` to keep test data isolated
- `_dispose_db_engine_after_test` fixture cleans up connections after each test

### Error Testing

**Pattern:**

```python
def test_health_readiness_degraded(client):
    with (
        patch("app.modules.health.routes.check_database", new_callable=AsyncMock) as mock_db,
        patch("app.modules.health.routes.check_redis", new_callable=AsyncMock) as mock_redis,
    ):
        mock_db.side_effect = ConnectionError("db down")
        mock_redis.return_value = True
        response = client.get("/health/ready")
    
    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["status"] == "degraded"
```

**Patterns:**
- `side_effect = Exception(...)` to make mock raise
- `status_code` assertions for HTTP error codes
- Response body validation (JSON keys/values)
- Domain exceptions caught in routes and converted to HTTPException

### Coverage

**Requirement:** Not enforced (no coverage config in pyproject.toml)

**Current test count:** 11 test files, 50+ test cases covering:
- Auth flows (sign-in, signup, recovery)
- Order processing (adequação, approval)
- Business parameters
- Data ingest from Databricks
- Health checks
- Schema integrity

**Gaps identified:**
- Limited integration tests between modules
- Some modules have minimal coverage (alertas, history)

---

## Frontend (TypeScript/React/Next.js)

### Test Framework

**Runner:** `vitest` 4.1.5+
- **Config:** `frontend/vitest.config.ts`
  ```typescript
  export default defineConfig({
    plugins: [react()],
    resolve: {
      alias: { "@": path.resolve(__dirname, ".") }
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./vitest.setup.ts"],
      include: ["**/*.test.{ts,tsx}"],
      exclude: ["**/node_modules/**", "**/.next/**", "**/design-system/**"],
      globals: true
    }
  });
  ```
- **Test path:** `frontend/**/*.test.{ts,tsx}`
- **Environment:** `jsdom` (browser-like DOM)
- **Setup file:** `frontend/vitest.setup.ts` — polyfills for ResizeObserver, IntersectionObserver, scrollIntoView

**Assertion Library:** `vitest` built-in `expect()` function

**Component Testing:** `@testing-library/react` (v16.3.2+) with `@testing-library/jest-dom` (v6.9.1+)

**User Interaction:** `@testing-library/user-event` (v14.6.1+) for realistic user input simulation

### Run Commands

```bash
# From frontend directory
pnpm test              # Run all tests once
pnpm test:watch        # Run tests in watch mode
vitest run             # Explicit run (same as pnpm test)
vitest                 # Interactive mode (watch + UI)
```

### Test File Organization

**Location:** Co-located with component/module — `*.test.tsx` files

**Naming:** `{component}.test.tsx` or `{module}.test.tsx`

**Current test files (13 main app tests, excluding design-system):**

Main app tests:
- `frontend/shared/config/auth/permissions.test.tsx` — RBAC permission checking
- `frontend/features/auth/ui/LoginPage.test.tsx` — Login page form navigation
- `frontend/features/parametros/ui/history-details-modal.test.tsx` — Parameter history modal
- `frontend/features/parametros/ui/parameter-request-modal.test.tsx` — Parameter change request
- `frontend/features/parametros/ui/parameter-history-table.test.tsx` — Parameter history table
- `frontend/features/pedidos/ui/order-detail-modal.test.tsx` — Order detail display
- `frontend/features/usuarios/ui/user-management-table.test.tsx` — User management table
- `frontend/widgets/app-shell/ui/app-sidebar.test.tsx` — App navigation sidebar
- `frontend/widgets/pedido-dashboard/ui/dashboard-overview.test.tsx` — Dashboard overview
- `frontend/widgets/pedido-dashboard/ui/orders-list.test.tsx` — Orders list widget
- `frontend/app/(app)/parametros/page.test.tsx` — Parameters page
- `frontend/app/(app)/parametros/page.roles.test.tsx` — Parameters page RBAC tests
- `frontend/app/(app)/usuarios/page.test.tsx` — Users page

Design-system tests (excluded from main count):
- Comprehensive test suite in `design-system/components/` (~70+ stories tested)
- Pattern: `__tests__/{component}.test.tsx` alongside component files

### Test Structure

**Basic Pattern (vitest + Testing Library):**

```typescript
// features/auth/ui/LoginPage.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { useSessionMock, signInMock } = vi.hoisted(() => ({
  useSessionMock: vi.fn(),
  signInMock: vi.fn(),
}));
vi.mock("next-auth/react", () => ({ useSession: useSessionMock, signIn: signInMock }));

beforeEach(() => {
  vi.clearAllMocks();
  useSessionMock.mockReturnValue({ data: null, status: "unauthenticated" });
});

describe("LoginPage — navegação entre modos", () => {
  it("renderiza o formulário de signin por padrão", () => {
    render(<LoginPage />);
    expect(screen.getByPlaceholderText("Digite seu login")).toBeInTheDocument();
  });

  it("Cadastre-se abre o formulário de signup com todos os campos", async () => {
    const user = userEvent.setup();
    render(<LoginPage />);
    await user.click(screen.getByRole("button", { name: "Cadastre-se" }));
    expect(screen.getByPlaceholderText("Nome")).toBeInTheDocument();
  });
});
```

**Patterns:**
- `vi.hoisted()` for module-level mocks setup
- `vi.mock()` for entire module replacement
- `beforeEach(vi.clearAllMocks())` to reset state between tests
- `render()` from Testing Library
- `screen.getByText()`, `screen.getByRole()`, `screen.getByPlaceholderText()` for queries
- `userEvent.setup()` for simulating user interactions (type, click, etc.)
- `expect(...).toBeInTheDocument()` for assertions

### Mocking

**Framework:** `vitest` mocking system (`vi` functions)

**Module mocking patterns:**

```typescript
// Mock entire module
const { useSessionMock } = vi.hoisted(() => ({ useSessionMock: vi.fn() }));
vi.mock("next-auth/react", () => ({ useSession: useSessionMock }));

// Mock functions called during component render
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// Setup return values
beforeEach(() => {
  useSessionMock.mockReturnValue({ data: { user: { roles: ["gestor"] } } });
});

// Verify mock was called
expect(mockFn).toHaveBeenCalledWith(expectedArgs);
```

**What to Mock:**
- External modules: `next-auth/react`, `next/navigation`, API endpoints
- Hooks that interact with auth/navigation
- Heavy computations or async operations

**What NOT to Mock:**
- React components from `@testing-library/react`
- Custom hooks from the same feature (import and use real)
- Rendering logic — test the actual component behavior

### Setup and Polyfills

**File:** `frontend/vitest.setup.ts`

```typescript
import "@testing-library/jest-dom/vitest";

// jsdom polyfills — jsdom doesn't implement ResizeObserver/IntersectionObserver
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

class IntersectionObserverStub {
  // ... stub methods
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof globalThis.ResizeObserver;
}
// ... more polyfills
```

**Purpose:**
- Register jsdom-compatible stubs for browser APIs used by components
- Prevents "ReferenceError: ResizeObserver is not defined" in tests
- Allows design-system components to mount without errors

### Testing Async Components

**Pattern with async data:**

```typescript
it("Cadastre-se abre o formulário", async () => {
  const user = userEvent.setup();
  render(<LoginPage />);
  
  // userEvent handles async automatically
  await user.click(screen.getByRole("button", { name: "Cadastre-se" }));
  
  // Wait for async operations (re-renders)
  expect(screen.getByPlaceholderText("Nome")).toBeInTheDocument();
});
```

**Key points:**
- `userEvent` operations are async; await them
- Component re-renders automatically after state changes
- `expect()` waits for DOM updates implicitly

### Hooks Testing

**Pattern with `renderHook`:**

```typescript
import { renderHook } from "@testing-library/react";

it("usePermissions returns correct role level", () => {
  const { result } = renderHook(() => usePermissions());
  
  expect(result.current.currentRole).toBe("basico");
  expect(result.current.currentLevel).toBe(10);
});
```

**For hooks that depend on context:**
- Wrap hook in a provider using `renderHook(..., { wrapper: ContextProvider })`
- or mock context values before calling the hook

### Design-System Test Pattern

**Storybook-driven testing (design-system only):**

```typescript
// Not used in main app; pattern shown for reference
import { composeStories } from "@storybook/react";
import * as stories from "../metric-card.stories";

const composed = composeStories(stories);

describe("MetricCard", () => {
  Object.entries(composed).forEach(([name, Story]) => {
    test(`renders ${name}`, () => {
      const { container } = render(<Story />);
      expect(container).toBeInstanceOf(Node);
    });
  });
});
```

**Note:** Main app tests use component rendering directly, not Storybook composition.

### Coverage

**Requirement:** Not enforced (no coverage config in vitest.config.ts)

**Current test count:** 13 main app test files, covering:
- Auth flows and permissions (RBAC)
- Parameter management (CRUD, history, change requests)
- Order detail modal
- User management
- Dashboard and order list views
- Page-level RBAC guards

**Gaps identified:**
- No integration tests between components
- Limited coverage of complex features (order adequação flow)
- No end-to-end tests (noted in MEMORY.md as Plan 38-4 future work via Playwright)

---

## Common Testing Patterns

### Async/Await

**Backend (pytest):**
```python
@pytest.mark.asyncio
async def test_something():
    result = await some_async_function()
    assert result == expected
```

**Frontend (vitest):**
```typescript
it("does something async", async () => {
  const user = userEvent.setup();
  await user.click(button);
  expect(screen.getByText("done")).toBeInTheDocument();
});
```

### Test Data Isolation

**Backend:**
- Use `async_session_factory()` directly for test data
- Call `flush()` but never `commit()` to avoid pollution
- `_dispose_db_engine_after_test` fixture cleans connections between tests
- Auth test helpers create unique users per test

**Frontend:**
- Mock external APIs/modules
- Each test is isolated; no shared DOM state
- `beforeEach(vi.clearAllMocks())` resets mocks between tests

---

*Testing analysis: 2026-08-04*
