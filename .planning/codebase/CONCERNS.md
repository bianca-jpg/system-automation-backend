# Codebase Concerns

**Analysis Date:** 2026-08-04

## Environment & Tooling Hazards

### Stray Git Repository at Desktop Level

**Issue:** An empty git repository exists at `C:\Users\bianca.teixeira\Desktop\.git` with no commits. Any git commands run from the project root may be intercepted by this repository depending on execution context.

**Files:** `C:\Users\bianca.teixeira\Desktop\.git/` (empty repository)

**Impact:**
- Risk of accidentally committing to the wrong repository
- Potential confusion with git status/log commands
- Tooling hazard for CI/CD or automation scripts

**Fix approach:**
- Delete `C:\Users\bianca.teixeira\Desktop\.git` directory
- Use absolute repository paths in scripts to avoid this ambiguity

---

### Design-System Submodule Brittleness

**Issue:** The `design-system` git submodule (at `frontend/design-system/`) requires explicit initialization and careful handling after clone/pull operations.

**Files:** `frontend/.gitmodules`, `frontend/design-system/`

**Current state:**
- Submodule is initialized but requires `git submodule update --init --recursive` after pull
- `git fetch --all` surface "upload-pack: not our ref" errors for stale refs
- npm dependency path: `"@system-automation/design-system": "file:./design-system"` (local monorepo pattern)

**Gotchas:**
- After pulling latest `develop`, submodule may point to stale commit
- Full `rm -rf node_modules && pnpm install` required after design-system changes
- Empty state snapshot issues when design-system changes (documented in memory)

**Safe modification:**
- Always run `git submodule update --init --recursive` after pulling
- Document submodule workflow in setup guide
- Consider adding pre-commit hook to verify submodule is at HEAD

---

### Empty Leftover Directory

**Issue:** `app/` directory exists at project root (not inside either repo) with 0 files. This is a leftover from a previous project structure migration.

**Files:** `C:\Users\bianca.teixeira\Desktop\PROJETO SYSTEM-AUTOMATION\app/` (empty)

**Impact:**
- Potential confusion about project structure
- No functional impact, but visual clutter

**Fix approach:**
- Delete `app/` directory from project root (it's already completely migrated to `backend/app/`)

---

## Backend Architecture Concerns

### Missing Database Indexes

**Issue:** Core database tables used in heavy queries lack indexes. Tables with frequent lookups by primary key or sequential scans will degrade under load.

**Files:**
- `backend/app/modules/pedidos/models.py` (lines 19-69)
- `backend/app/modules/ingestao/models.py` (referenced but not reviewed)

**Current state:**
- `ordens_reserva`, `pedidos_processados`, `pedido_modificacoes` have composite primary keys `(nr_pedido, cd_prod_cor)` but no additional indexes
- `Pedido`, `Estoque` tables (from ingestao module) likely lack indexes on `(cd_prod_cor, canal)` or `(nr_pedido)`

**Queries affected:**
- `SELECT * FROM ordens_reserva WHERE nr_pedido = ?` (called by orders lookup)
- `SELECT * FROM estoque WHERE cd_prod_cor = ? AND canal = ?` (called by adequation)
- `SELECT * FROM pedidos WHERE status = ?` (filtering in API)

**Improvement path:**
- Profile actual query patterns with `EXPLAIN ANALYZE` against production-scale data
- Add indexes on foreign keys and frequently-filtered columns
- Add indexes on `(created_at, aprovado_em)` for time-range queries in history

**Confidence:** High — This is a common performance gap in early-stage systems

---

### Branch Divergence: `main` vs `develop`

**Issue:** The `main` branch is far behind `develop` in both repositories, creating a long-lived branch split that delays releases and increases merge complexity.

**Current state:**
- **Backend:** `develop` is 35 commits ahead of `main` (no commits from main ahead)
- **Frontend:** `develop` is 64 commits ahead of `main` (no commits from main ahead)

**Impact:**
- Release process is blocked until PRs from `develop` → `main` are created and merged
- `main` does not represent deployed code; developers may check out `develop` by mistake
- Hotfix workflows are unclear

**Pattern observed:**
```
main:    205e84d ─── 25c3e58 ─── ... (35 or 64 commits old)
           │
develop: (HEAD) ─── fb589d5 ─── f04cc1c ─── ... ─── (most recent)
```

**Fix approach:**
- Define a release process: create a release PR from `develop` → `main` when ready to deploy
- Tag releases on `main` (e.g., `v1.0.0`)
- Rebase or merge `develop` → `main` after each release
- Document release SLA (e.g., weekly, on-demand, etc.)

**Confidence:** High — Observed in both repos

---

## Backend Data & Query Concerns

### Full Refresh Sync Without Transaction Isolation

**Issue:** Ingestão syncs pedidos and estoque in separate transactions. If one fails after the other succeeds, data consistency is lost.

**Files:** `backend/app/modules/ingestao/application/casos_uso.py` (see `sincronizar_tudo`)

**Current approach:**
```python
async def sincronizar_tudo(db: AsyncSession):
    resultado_pedidos = await sincronizar_pedidos(db)    # Commit 1
    resultado_estoque = await sincronizar_estoque(db)    # Commit 2
    # If estoque fails, pedidos are already committed with old estoque
```

**Risk:**
- Order matching uses old stock data if stock sync fails silently
- Full refreshes are unidirectional — no automatic retry or rollback
- Celery workers catch `DatabricksError` but swallow other exceptions silently

**Improvement path:**
- Wrap both syncs in single transaction with savepoint
- On any failure, rollback both and retry as atomic unit
- Add detailed logging of transaction boundaries
- Consider eventual consistency pattern if atomic transactions are too expensive

**Confidence:** Medium — Observed in code structure, but full-refresh pattern may be intentional for idempotency

---

### Databricks Token Configuration Risk

**Issue:** Databricks API token critical for data ingestion. Token expiration, misconfiguration, or network issues will silently block data sync every 2 hours.

**Files:**
- `backend/app/shared/infrastructure/databricks_client.py`
- `backend/app/workers/tasks/ingestao.py` (lines 39-43)

**Current mitigation:**
- Token stored in `.env` (not versioned, correct)
- Errors logged to Celery worker logs
- No alerting or monitoring configured

**Gotchas:**
- Databricks tokens may expire after N days (check token policy)
- Network timeout appears identical to auth failure in logs
- Celery beat continues scheduling syncs even if all attempts fail

**Improvement path:**
- Set up metrics/alerts: log `DatabricksError` as critical
- Monitor sync success rate (target: >95% per day)
- Document token rotation procedure and expiration policy
- Test token refresh scenario in staging

**Confidence:** High — Token management is critical infrastructure

---

### Migration 014: Complex Conditional Schema Migration

**Issue:** Migration `014_or_por_produto.py` contains conditional logic to handle schema drift caused by a lost migration (012) that was applied to dev but never committed.

**Files:** `backend/alembic/versions/014_or_por_produto.py` (lines 50-93)

**What happened:**
1. Migration 012 was applied to dev database but never committed to git
2. Code was reverted to use old schema, creating "schema drift"
3. Migration 014 now detects this and adapts at runtime

**Current approach:**
```python
def _tem_cd_prod_cor(conn, tabela: str) -> bool:
    # Check if migration 012 was already applied to this database
    return conn.execute(...).first() is not None

def upgrade() -> None:
    for tabela in _TABELAS:
        if _tem_cd_prod_cor(conn, tabela):
            # Dev path: schema already has cd_prod_cor, just verify
            continue
        else:
            # CI/Prod path: migrate from old schema
            # DELETE FROM tabela (data loss warning!)
            op.execute(f"DELETE FROM {tabela}")
            # Add column and change primary key
```

**Risk:**
- Downgrade path deletes all production data (no restore mechanism)
- Conditional logic is fragile: future migrations might not account for this schema state
- Test coverage gap: no tests for downgrade with live data

**Safe modification:**
- Mark this migration as non-reversible in documentation
- Add explicit pre-migration backup step if data exists
- Test downgrade thoroughly in staging before deploying
- Consider splitting into separate migrations (one per schema state)

**Confidence:** High — Code is well-documented but risky pattern

---

## Frontend Architecture Concerns

### Global State in HTTP Client

**Issue:** `lib/api/http-client.ts` uses module-level global variables to store authentication token and callback. This pattern is fragile in concurrent scenarios and hard to test.

**Files:** `frontend/lib/api/http-client.ts` (lines 3-4)

**Current implementation:**
```typescript
let authToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
}
```

**Problems:**
- Race condition if `setAuthToken` is called while requests are in-flight
- `onUnauthorized` callback is not cleared after firing, could trigger multiple times
- No way to guarantee token freshness in concurrent requests
- Tests that set auth token don't reset it (test isolation risk)

**Safe modification:**
- Move to React Context for component-level token management
- Use `useCallback` hooks instead of module-level state
- Or: use TanStack Query / SWR for request-level token injection
- Ensure each request gets the current token at call-time, not setup-time

**Confidence:** Medium — Works in current sequential flow, but risky for future concurrent features

---

### NextAuth Beta Version in Production

**Issue:** Frontend uses `next-auth@5.0.0-beta.30`, a beta version that may introduce breaking changes.

**Files:** `frontend/package.json` (line 24)

**Current fragility:**
- Token refresh logic in `auth.ts` depends on specific callback behavior
- Custom JWT expiration handling (comparing `Date.now()` to token `exp`)
- Session callback modifies token fields which may change in stable release

**Gotchas:**
- NextAuth v5 stable might deprecate or change callback signatures
- Token field names or expiration logic could shift
- No migration guide from beta to stable in codebase

**Improvement path:**
- Pin to `5.0.0-beta.30` explicitly (not `^5.0.0-beta.30`) to prevent auto-upgrade
- Monitor NextAuth v5 stable release schedule
- Plan 2-week migration window: test against stable release, update `auth.ts`, redeploy
- Isolate token refresh logic so it can be updated independently

**Confidence:** High — Beta software is inherently risky

---

## Security Concerns

### JWT Secret Default in Settings

**Risk:** `JWT_SECRET` defaults to `"change-me-in-production"` if not set in `.env`. If deployment forgets this, weak key is used.

**Files:** `backend/app/shared/config/settings.py` (line 103-104)

**Current mitigation:**
- `.env` is gitignored (correct)
- Setting is environment-variable-driven (correct)
- Dockerfile and compose files don't show hardcoded values

**Recommendation:**
- Add startup validation: raise exception if `JWT_SECRET == "change-me-in-production"` in non-dev environments
- Document in deployment guide: "Generate a random 32-char string for JWT_SECRET"
- Consider rotating key periodically (add key versioning if tokens need to be valid across rotations)

**Confidence:** Medium — Good intention, but no runtime guard

---

### CORS Configuration Too Permissive

**Issue:** CORS middleware allows all methods and all headers, which is overly broad.

**Files:** `backend/app/main.py` (lines 17-22)

**Current configuration:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],       # ← All methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],       # ← All headers
)
```

**Why permissive:**
- Allows unauthenticated CORS preflight requests for any HTTP method
- Broadcasts to clients what methods are allowed
- Reduces flexibility for future security policies

**Better approach:**
```python
allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
allow_headers=["Content-Type", "Authorization"],
```

**Improvement path:**
- Restrict to specific methods needed by frontend
- Restrict to specific headers (Content-Type, Authorization)
- Document CORS policy in security runbook

**Confidence:** Medium — Current configuration works but violates least-privilege principle

---

## Testing & Quality Gaps

### Test Coverage Gaps (Backend)

**Areas undertested:**
- OR matching algorithm with edge-case sizes (unknown sizes in RANKINGS)
- Boundary conditions in tolerance adequation (exactly at threshold)
- Concurrent adequation across channels
- Celery task retry/failure scenarios
- Email send failures and fallback behavior

**Files:**
- `backend/app/tests/test_pedidos_motor.py` (needs expansion)
- `backend/app/modules/comunicacoes/email_service.py` (no tests visible)

**Priority:** High — Core business logic (OR adequation) lacks edge-case coverage

---

### Test Coverage Gaps (Frontend)

**Areas undertested:**
- Token refresh race conditions (concurrent requests during token expiration)
- NextAuth session callback with missing/invalid tokens
- Order list re-fetch after modal close (state consistency)
- Submodule import failures (if design-system is missing)
- Network retry behavior with partial failures

**Files:**
- `frontend/features/auth/` (token refresh tests)
- `frontend/widgets/pedido-dashboard/` (concurrent state tests)

**Priority:** Medium — Integration tests would catch these, but unit tests are primarily focused on UI components

---

## Performance & Scalability Concerns

### Complex OR Adequation Algorithm Without Profiling Data

**Problem:** Order matching uses multi-step greedy heuristic with size-based adjustments. Algorithm performance with production-scale data (thousands of orders, hundreds of SKUs) is unknown.

**Files:**
- `backend/app/modules/pedidos/domain/motor_adequacao.py` (lines 57-120)
- `backend/app/modules/pedidos/application/casos_uso.py`

**Algorithm breakdown:**
1. Group by product
2. For each (product, pedido) pair: adequar_grade_produto()
3. Within each: check tolerance, compute budget, adjust sizes

**Unknown bottlenecks:**
- How long does adequation take for 1000 orders?
- Does algorithm scale linearly or worse?
- Where are the hot paths?

**Improvement path:**
- Add timing instrumentation (measure adequation duration per batch)
- Profile with production data volume
- If slow, optimize: cache size rankings, batch tolerance checks, parallelize by channel

**Confidence:** Medium — Algorithm is reasonable but untested at scale

---

### Databricks Query Size Limits

**Current capacity:**
- INLINE disposition: 25 MiB JSON response
- EXTERNAL_LINKS: Unlimited (slower, multi-step fetch)

**Limit:** If a month of orders exceeds 25 MiB, queries automatically degrade to EXTERNAL_LINKS

**Scaling path:**
- Monitor actual query sizes in logs
- If approaching 25 MiB regularly, consider:
  - Filtering more aggressively (e.g., only last 2 weeks instead of full month)
  - Splitting queries by product category
  - Moving to EXTERNAL_LINKS as default

**Confidence:** Medium — Limit is documented in code, but no monitoring of approach rate

---

## Missing Instrumentation

### No Monitoring of Critical Processes

**Issue:** No built-in metrics, alerts, or dashboards for:
- Databricks sync success/failure rate
- Email send success rate
- Order processing latency
- Database query performance

**Files:**
- `backend/app/workers/tasks/ingestao.py` (silent success/fail)
- `backend/app/modules/comunicacoes/email_service.py` (errors logged but not tracked)

**Current mitigation:**
- Prometheus client is installed (`prometheus-client>=0.21.0`) but not used
- Celery logs are written to stdout but no aggregation

**Improvement path:**
- Add Prometheus metrics for:
  - `ingestao_sync_duration_seconds`
  - `ingestao_sync_success_total`
  - `email_send_success_total`
  - `order_processing_duration_seconds`
- Set up alerting rules: alert if sync fails for 2 consecutive runs
- Create Grafana dashboard for ops team

**Confidence:** High — Instrumentation gap is clear, solution is straightforward

---

### Limited Error Context in Order Processing

**Issue:** When an order fails to generate an OR, there's limited information about why.

**Files:** `backend/app/modules/pedidos/application/casos_uso.py`

**What's missing:**
- No audit trail of which orders were attempted
- No detailed logging of why adequation failed for a specific order
- No retry mechanism for transient failures

**Improvement path:**
- Add detailed logging before/after each adequation step
- Consider adding an `order_processing_log` table to track attempts and outcomes
- Implement retry logic for orders that fail due to transient errors (e.g., stock race conditions)

**Confidence:** Medium — Gap is observable but may not be critical if data is always valid

---

## Celery Worker Concerns

### Celery Worker Creates New Database Engine Per Sync

**Issue:** `app/workers/tasks/ingestao.py` (lines 32-33) creates a new engine inside each task execution to work around asyncio event loop attachment issues.

**Files:** `backend/app/workers/tasks/ingestao.py`

**Current pattern:**
```python
@celery_app.task
def sincronizar_databricks() -> dict:
    return asyncio.run(_sincronizar())

async def _sincronizar() -> dict:
    engine = create_async_engine(settings.database_url, echo=False)  # New engine each run
    # ...
    await engine.dispose()
```

**Why done this way:**
- Each `asyncio.run()` creates a new event loop
- Database connection pools are tied to the event loop
- Reusing an engine across loops causes "Future attached to a different loop" errors

**Risk:**
- Connection pool warm-up penalty on each sync
- If two syncs run concurrently (beat + manual API call), they don't share connections
- Engine cleanup might miss connections if disposal is not awaited properly

**Improvement path:**
- Consider using `contextvars` to manage per-task engine lifecycle
- Or: Restructure to use single event loop shared across workers
- Or: Keep current pattern but document as intentional trade-off

**Confidence:** Low — Pattern is explained in comments and appears intentional, but could be optimized

---

## Data Quality Concerns

### No Validation of Databricks Data on Ingestion

**Issue:** Data from Databricks is assumed valid. If upstream data quality degrades, garbage propagates to the database.

**Files:** `backend/app/modules/ingestao/application/casos_uso.py`

**What's missing:**
- No schema validation (columns must exist in expected format)
- No data quality checks (e.g., negative quantities, missing required fields)
- Silent discard of invalid rows (logged but not alerting)
- No monitoring of discard rate

**Improvement path:**
- Add explicit column existence checks before parsing
- Add data quality validators:
  - `qt_entregar >= 0`
  - `vl_liquido >= 0`
  - `cd_prod_cor` matches known format
  - `canal` is in ["Franquia", "Multimarca"]
- Log and alert if discard rate exceeds threshold (e.g., >5% of rows)
- Consider a `data_quality` validation layer

**Confidence:** Medium — Upstream data is trusted, but no guard rails

---

## Audit Remediation Pointer — 2026-08-07

**Issue:** Concerns de plataforma (CI sem gates, auth/OTP/seed/JWT, Celery/migrations fora do ECS, Docker com `--group dev`, contratos/docs, observabilidade) foram consolidados num audit dedicado e no milestone **v1.2 Platform Hardening**.

**Não reescreve** os concerns históricos acima — aponta para a fonte nova.

**Canonical artifacts:**

| Artifact | Path |
|----------|------|
| Audit P0/P1/P2 | `.planning/research/AUDIT-2026-08-07.md` |
| Milestone phases 4–9 | `.planning/milestones/v1.2-PLATFORM-HARDENING.md` |
| Requirements `CI-*` `SEC-*` `OPS-*` `CONTRACT-*` `OBS-*` | `.planning/REQUIREMENTS.md` § v1.2 |
| Execution brief (Claude Code) | `.planning/CLAUDE-EXECUTION-BRIEF.md` |
| Roadmap index | `.planning/ROADMAP.md` § Milestone v1.2 |
| Project state | `.planning/STATE.md` (focus = v1.2 planning only) |

**Highest-severity deltas vs este CONCERNS (2026-08-04):**

1. **Deploy cego** — `pipeline.yml` sem ruff/pyright/pytest (Collab já barra deploy).
2. **Auth defaults** — `SEED_AUTH_ON_STARTUP=True`, senhas seed conhecidas, `JWT_SECRET` placeholder, OTP sem e-mail em PROD.
3. **Ops incompleto** — worker/beat/migrations ausentes do pipeline ECS.
4. **Imagem suja** — `uv sync --group dev` + ausência de `.dockerignore`.
5. **Contrato/docs** — parâmetros hardcoded no front; docs listam `alertas`/`history` inexistentes.
6. **Obs** — sem Sentry; `/metrics` público; CORS default localhost.

**Improvement path:** executar Phases 4→9 via GSD (`/gsd-plan-phase` / `/gsd-execute-phase`) seguindo o brief — **ainda não iniciado** na data do audit.

**Confidence:** High — validado contra código e contra Collab API em 2026-08-07

**Status update 2026-08-12** (reverificado direto no código, sem executar as Phases via GSD — commits regulares fecharam parte disso por fora do fluxo formal):

1. **Deploy cego** — parcial: `pipeline.yml` já roda ruff+pytest antes do build/push; falta pyright.
2. **Auth defaults** — `JWT_SECRET` e `SEED_AUTH_ON_STARTUP=True` já falham o boot em PROD; default global de seed fora de PROD continua `True`; OTP em PROD ainda sem e-mail.
3. **Ops incompleto** — resolvido, desenho diferente: worker+beat+orders-worker no pipeline; migrations no boot de cada task (não one-off).
4. **Imagem suja** — `--group dev` já não vai para a imagem do ECR; `.dockerignore` continua ausente.
5. **Contrato/docs** — docs não citam mais `alertas`/`history` fantasma; hardcode de parâmetros no front não verificado.
6. **Obs** — sem mudança (Sentry ausente, `/metrics` público, CORS sem fail-fast).

Detalhe em `.planning/research/AUDIT-2026-08-07.md` § Status update e `.planning/REQUIREMENTS.md` § v1.2.

---

*Concerns audit: 2026-08-04*  
*Append platform hardening pointer: 2026-08-07*  
*Append status update: 2026-08-12*
