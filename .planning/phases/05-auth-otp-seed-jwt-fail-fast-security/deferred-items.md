# Deferred Items — Phase 05

Out-of-scope issues discovered during execution of plans in this phase. Not fixed here per the executor's scope-boundary rule (only auto-fix issues directly caused by the current task's changes).

## Plan 05-04

### 1. `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` — RuntimeError: Event loop is closed

- **Module:** `pedidos` (out of scope — actively being edited by a concurrent session)
- **Status:** Pre-existing, confirmed by the orchestrator's execution briefing before this plan started. Not introduced by any change in this plan (this plan only touches `app/modules/auth/*`).
- **Action:** None. Left untouched per explicit instruction.

### 2. `test_parametros.py::test_listar_parametros_contem_criado` — pagination assertion fails

- **Module:** `parametros` (out of scope — this plan does not touch `app/modules/parametros/`)
- **Symptom:** `assert any(p["chave"] == chave for p in body["rows"])` fails because the newly created parameter isn't on page 1 (page_size=25).
- **Root cause:** The local test Postgres database (`system_automation_test`) has accumulated 222 rows in the `parametros` table from repeated local test runs across sessions — `test_parametros.py` has no cleanup/teardown for rows it creates, so `chave`s pile up indefinitely and eventually push new rows off page 1 of the default-sorted listing.
- **Confirmed unrelated to this plan:** `git diff` for this plan touches only `app/modules/auth/*` and its tests; `test_parametros.py` was not modified. Counted 222 pre-existing rows in the `parametros` table via a direct query before drawing this conclusion.
- **Action:** None taken (would require either a DB reset/truncate of the local test database, or adding row cleanup to `test_parametros.py` — both out of scope for SEC-06 auth work). Flagging for a future test-infra pass or a `TRUNCATE parametros` before the next local full-suite run.
