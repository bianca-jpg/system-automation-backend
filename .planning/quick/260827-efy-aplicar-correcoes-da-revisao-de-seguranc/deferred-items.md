# Deferred Items — 260827-efy

## Pre-existing failures (out of scope, not fixed) — system_automation_test atrasado na migration 034

Both confirmed caused by the `system_automation_test` database not having `alembic upgrade head` run
(missing migration 034 — `ON DELETE SET NULL` on `parametro_change_requests` FKs, per quick task
260825-f21). Not caused by this plan's changes. Fix: `DATABASE_URL=...system_automation_test
./.venv/Scripts/python.exe -m alembic upgrade head` (documented pattern in STATE.md Blockers).

1. `app/tests/test_auth_flows.py::test_delete_user_preserva_change_request_zerando_requested_by`
   — `ForeignKeyViolationError` on `parametro_change_requests_requested_by_fkey`. Confirmed
   pre-existing via `git stash` + rerun before any change from this plan was applied.
2. `app/tests/test_schema_guard.py::test_colunas_do_model_existem_no_banco[parametro_change_requests]`
   — nullability divergente: `requested_by` é `nullable=True` no model (migration 034) mas
   `NOT NULL` ainda no banco de teste. Mesma causa raiz do item 1.

## Pre-existing ruff format divergences (out of scope, not fixed)

`uv run ruff format --check app alembic` reports 2 files that predate this plan:
- `alembic/versions/034_parametro_change_requests_fk_set_null.py` (quotes single→double,
  blank line) — confirmed via `git show <pre-plan-commit>:<path> | ruff format --check`.
- `app/tests/test_auth_flows.py:781` and `:793` (two `client.post(...)` calls untouched by
  this plan's edits, which only touched lines ~282-330) — confirmed via `git show
  <pre-plan-commit>:app/tests/test_auth_flows.py` at those line numbers.

Not fixed: out of scope (files/lines this plan did not touch).

## Windows collection error (environment, not a code defect)

`app/tests/test_pedidos_processing_sem_adequar_memory_024.py` fails to collect natively on
Windows (`ModuleNotFoundError: No module named 'resource'` — Unix-only stdlib module). The
project's documented way to run the full suite is via Docker; this session ran natively on
Windows. Full suite run in Task 4 used `--ignore` for this one file; excluded from the pass/fail
count reported in the SUMMARY.

## Concurrent uncommitted work observed in working tree (not touched)

While executing this task, the working tree also contained uncommitted changes belonging to a
different, unrelated quick task (`260827-emo` — metrics endpoint guard): modifications to
`app/shared/config/settings.py` (METRICS_API_KEY) and a new `app/tests/test_metrics_guard.py`,
plus `.planning/quick/260827-emo-proteger-endpoint-get-metrics-com-guard-/`. These were left
untouched and excluded from every commit made by this task (commits stage only the specific
files listed in each task of `260827-efy-PLAN.md`).
