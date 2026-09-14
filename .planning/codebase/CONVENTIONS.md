# Coding Conventions

**Analysis Date:** 2026-08-04

## Backend (Python/FastAPI)

### Naming Patterns

**Files:**
- `snake_case.py` — e.g., `routes.py`, `service.py`, `models.py`, `schemas.py`, `conftest.py`

**Functions:**
- `snake_case()` — e.g., `sign_in()`, `confirm_register()`, `seed_auth_users()`
- Private functions: Prefix with `_` — e.g., `_user_response()`, `_auth_success()`, `_store_otp()`

**Variables:**
- `snake_case` — e.g., `user_id`, `access_token`, `postgres_host`, `test_engine`

**Constants:**
- `UPPER_CASE` — e.g., `OTP_TTL_MINUTES = 15`, `OTP_SLOT_COUNT = 6`, `JWT_SECRET`

**Classes:**
- `PascalCase` — e.g., `AuthUser`, `Settings`, `AuthResponse`, `OtpPurpose`, `StrEnum`

**Enums:**
- `PascalCase` class name, `UPPER_CASE` enum members — e.g., `class OtpPurpose(StrEnum): REGISTER = "register"`

### Type Annotations

**SQLAlchemy 2.0 with Async:**
- Use `Mapped[type]` for ORM attributes — e.g., `id: Mapped[int]`, `roles: Mapped[list[str]]`
- Use union type syntax `type1 | type2` for optional — e.g., `email: Mapped[EmailStr | None]`
- Async methods: `async def func() -> ReturnType:`
- All functions have explicit return type hints

**Pydantic v2:**
- BaseModel classes with field validation — e.g., `password: str = Field(min_length=8, max_length=256)`
- Use `ConfigDict(extra="forbid")` to reject unknown fields
- `EmailStr` for email validation (from `pydantic`)
- Type hints on all fields: `name: str`, `age: int | None = None`

### Code Style

**Formatting:**
- No explicit formatter configured (no Black, Ruff, etc. in pyproject.toml)
- Follows Clean Code principles per CLAUDE.md — readable, well-named code is the standard
- Line length: Implicit; readability preferred over strict limits

**Import Order (observed):**
1. `__future__` imports (if any)
2. Standard library (`os`, `logging`, `datetime`, etc.)
3. Third-party (`fastapi`, `sqlalchemy`, `pydantic`, etc.)
4. Local app (`from app.modules`, `from app.shared`)

**Module Structure:**
Each feature module at `app/modules/{feature_name}/` contains:
- `routes.py` — FastAPI APIRouter with endpoint handlers
- `service.py` — Business logic, imported and re-exported
- `models.py` — SQLAlchemy ORM models
- `schemas.py` — Pydantic request/response schemas
- `application/casos_uso.py` — Application-layer use cases (business logic, data access)
- `domain/` — Domain models and exceptions (e.g., `exceptions.py`, `roles.py`)
- `bootstrap/` — Initialization (e.g., `seed.py`)

**Example from `app/modules/auth/`:**
- `app/modules/auth/routes.py` — Endpoint handlers, HTTP layer
- `app/modules/auth/service.py` — Re-exports from `application/casos_uso`
- `app/modules/auth/application/casos_uso.py` — Core business logic (sign_in, register, etc.)
- `app/modules/auth/models.py` — `AuthUser`, `AuthOtpChallenge` ORM models
- `app/modules/auth/schemas.py` — Pydantic request/response models
- `app/modules/auth/domain/exceptions.py` — Domain exceptions (e.g., `CredenciaisInvalidasError`)
- `app/modules/auth/domain/roles.py` — RBAC role definitions
- `app/modules/auth/bootstrap/seed.py` — Default user seeding

### Error Handling

**Exceptions:**
- `HTTPException` with status code for API errors — e.g., `raise HTTPException(status_code=401, detail="Invalid credentials")`
- Domain-specific exceptions in `app/modules/{feature}/domain/exceptions.py` — e.g., `CredenciaisInvalidasError`, `EmailJaRegistradoError`
- Global exception handler in `app/shared/errors/handlers.py` catches unhandled exceptions and returns 500

**Patterns in Routes:**
```python
@router.post("/sign-in", response_model=AuthResponse)
async def sign_in(body: SignInRequest, db: AsyncSession = Depends(get_db)) -> AuthResponse:
    try:
        return await service.sign_in(db, body)
    except CredenciaisInvalidasError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials") from exc
```

### Database & Sessions

**Async patterns:**
- All I/O is async/await; no blocking calls in route handlers
- Database session via `Depends(get_db)` — FastAPI dependency injection
- Session cleanup via `asyncio.run(engine.dispose())` in tests to release connections between test runs

**Transaction management:**
- Explicit `await session.commit()` in use cases
- `await session.flush()` to validate writes without committing
- Tests with `async_session_factory()` — never committed to avoid data pollution

### Logging

**Framework:** Python `logging` module

**Setup:** Per-module logger — `logger = logging.getLogger(__name__)`

**Configuration:** Handled in `app/shared/logging/setup.py` (referenced in bootstrap)

**Level:** INFO/DEBUG; debug output like OTP codes logged at startup

### Comments

**Style:**
- Minimal; code should be self-documenting
- Explain *why*, not *what*
- Portuguese or English acceptable; be consistent within a file
- Block comments for complex algorithms or business rules
- Example: `# Databricks SQL Statement Execution API — ver doc técnica`
- Docstrings not enforced; type hints and clear naming preferred

**Multiline comments in tests:**
- Use `"""..."""` for documenting test intent — see `app/tests/test_pedidos_routes.py` for examples

---

## Frontend (TypeScript/React/Next.js)

### Naming Patterns

**Files:**
- Components: `PascalCase.tsx` — e.g., `LoginPage.tsx`, `OrderDetailModal.tsx`
- Utilities/hooks: `kebab-case.ts` — e.g., `use-caps-lock-state.ts`, `auth.ts`
- Config/types: `kebab-case.ts` — e.g., `login-copy.ts`, `permissions.test.tsx`

**React Components:**
- `PascalCase` — e.g., `LoginPage`, `OrderDetailModal`, `UserManagementTable`
- Exported as named or default; prefer matching file name

**Hooks:**
- `camelCase`, prefixed with `use` — e.g., `useCapsLockState`, `useLoginForm`, `usePermissions`
- Exported as named exports

**Functions/Utils:**
- `camelCase` — e.g., `buildUserFromNormalizedAuth()`, `getApiBaseUrl()`, `cn()` (classname merger)

**Variables:**
- `camelCase` — e.g., `isCapsLockOn`, `selectedChannel`, `refreshToken`

**Constants:**
- `UPPER_CASE` or `camelCase` depending on context:
  - Module-level: `UPPER_CASE` — e.g., `OTP_SLOT_COUNT = 6`, `AUTH_SESSION_MAX_AGE_SECONDS`
  - Inline: `camelCase` — e.g., `const loginFieldLabelClassName = "..."`

**Types/Interfaces:**
- `PascalCase` — e.g., `NormalizedAuthSession`, `automationRole`, `PedidoItem`

### Type Annotations

**TypeScript strict mode:**
- All functions have explicit parameter and return types
- Variables typed where not obvious from assignment
- React component props via interface — e.g., `interface Props { idPrefix: string }`

**Type imports:**
- Separate from value imports — e.g., `import type { FocusEventHandler } from "react"`
- Prefer type imports to keep bundle clean

**Union types:**
- Use `|` syntax — e.g., `type1 | type2 | null`
- Optional: `param?: Type` or `param: Type | null`

**Generic types:**
- Applied to hooks and components — e.g., `AsyncMock<T>`, `ReactNode`

### Code Style

**Formatting:**
- ESLint configured via `eslint.config.mjs` (Next.js + TypeScript rules)
- No explicit formatter (Prettier not found)
- Line length: Implicit; ESLint enforces linting rules

**Linting Rules:**
- Unused variables: Warning if not prefixed with `_` — e.g., `const _unusedParam = props.x` is allowed
- Ignores: `.next/`, `design-system/`, `.claude/worktrees/`
- Extends: `eslint-config-next/core-web-vitals`, `eslint-config-next/typescript`

**Import Order (observed):**
1. Third-party packages — e.g., `import { getLoginHelperText } from "@/features/auth/config/login-copy"`
2. Type imports — e.g., `import type { KeyboardEventHandler } from "react"`
3. Local imports with `@/` alias — e.g., `import { useCapsLockState } from "@/shared/lib/react/use-caps-lock-state"`
4. Local relative imports — rare; @ alias preferred

**Path Aliases:**
- `@/*` maps to project root — defined in `tsconfig.json`
- Enables `@/features`, `@/shared`, `@/widgets`, `@/lib`, etc.

### Component Patterns

**Functional Components:**
- `"use client"` at top for client-side interactivity
- Props via destructuring in signature — e.g., `function OtpSlots({ idPrefix }: { idPrefix: string })`
- Event handlers: `camelCase` — e.g., `handleSubmit`, `onKeyDown`

**State Management:**
- `useState` for local state
- Context (`useAppData()`) for global state — e.g., `app-data-provider`
- No Redux; Context API used for cross-page state

**FSD (Feature-Sliced Design) Layers:**
- `features/{feature}` — Feature-specific UI, hooks, logic
- `widgets/{widget}` — Reusable UI bundles (multiple features)
- `shared/` — Shared utilities, UI primitives, config
- `entities/` — Domain models/types
- `app/` — Next.js routes (App Router)

### Error Handling

**Patterns:**
- Try-catch with fallback — e.g., `try { ... } catch(err) { console.error(...); }`
- Return null/undefined on error (no throw in most cases)
- Custom error classes: `automationPanelAccessDenied` for auth failures
- No error boundaries detected; error state via component state

**Logging:**
- `console.error()` for runtime errors
- `console.warn()` for deprecations/issues
- No centralized logging; inline at call site

### Comments

**Style:**
- Minimal; intent should be clear from component/function names
- Explain *why*, not *what*
- Portuguese or English acceptable
- Example: `// Ao entrar na aba de Alertas, recarrega alertas...` (explaining business logic)

**JSDoc/TSDoc:**
- Not consistently used; type annotations + clear naming preferred
- For complex logic or edge cases: brief comment above

### Testing Patterns (Frontend)

**Test file naming:**
- `*.test.tsx` — e.g., `LoginPage.test.tsx`, `permissions.test.tsx`

**Vitest + Testing Library patterns:**
```typescript
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

describe("Component", () => {
  it("renders correctly", () => {
    render(<Component />);
    expect(screen.getByText("text")).toBeInTheDocument();
  });
});
```

**Mocking:**
- `vi.hoisted()` for module-level mocks
- `vi.mock("module", () => ({ ... }))` for entire module replacement
- Prefer `userEvent.setup()` over `fireEvent` for user interactions

---

## Shared Conventions

### Commit Conventions

Per CLAUDE.md in both projects:
- **Format:** [Conventional Commits](https://www.conventionalcommits.org/) with optional scope
- **Types:** `feat`, `fix`, `chore`, `refactor`, `docs`
- **Scope:** Include when domain is clear — e.g., `feat(auth): add password recovery`
- **Examples:**
  - `feat(comunicacoes): envio de e-mail para time comercial`
  - `fix(pedidos): corrigir isolamento por canal na adequação`
  - `chore: atualizar dependências`

### Branches

Per CLAUDE.md:
- **Format:** `feat/`, `fix/`, `chore/`, `refactor/`, `docs/` prefixed
- **Base:** Always branch from `develop`
- **Push:** Always to `develop` (never main except via release PR)
- **Example:** `feat/adicionar-alertas-dashboard`

---

*Convention analysis: 2026-08-04*
