# Deferred Items — Phase 11

Issues discovered during execution that are out of scope for the current plan
(pre-existing, unrelated to files this plan touches) and therefore not fixed.

---

> **RESOLVIDO em 2026-08-17 — leia esta nota antes do item abaixo.**
>
> O item "Local test database missing `auth_users.user_name`" está **corrigido**, e o
> diagnóstico registrado nele estava **errado no ponto central**. Não havia drift nem
> corrupção: existem **dois** bancos, e só o de teste estava atrasado.
>
> - `system_automation` (aplicação, vem do `.env`) — já estava em `030`, correto, intocado.
> - `system_automation_test` (só os testes; `conftest.py::_configure_isolated_test_database`
>   recusa banco cujo nome não termine em `_test`) — estava em **028**.
>
> O `alembic current`/`heads` citado abaixo como prova de que "o banco se acredita migrado"
> leu o banco da **aplicação**, não o de teste. Bancos diferentes — a comparação não valia.
>
> Correção: `DATABASE_URL=postgresql+asyncpg://automation:automation@localhost:5432/system_automation_test
> ./.venv/Scripts/python.exe -m alembic upgrade head`. Rodou 028→029→030 apenas no banco de
> teste, preservando as 167 linhas de `auth_users`. Nenhum rebuild de container foi necessário
> — a recomendação de "rebuild the test DB" abaixo era mais destrutiva do que o problema pedia.
>
> Para a Phase 11: depois de criar a migration **031** no plano 11-02, será preciso rodar
> `upgrade head` no `system_automation_test` de novo. O `conftest.py` não faz isso sozinho.

---

## 11-01 — Local test database missing `auth_users.user_name` column

**Found during:** Task 1 verification (`uv run pytest app/tests/test_parametros.py -q` — well, `.venv/Scripts/python.exe -m pytest` due to a `uv run` trampoline path issue, see below).

**Symptom:** `sqlalchemy.exc.ProgrammingError: ... UndefinedColumnError: column "user_name" of relation "auth_users" does not exist` — fails 2 tests and errors 12 more in `app/tests/test_parametros.py` (any test that seeds an `AuthUser`).

**Why out of scope:** `alembic current` and `alembic heads` both report `030 (head)` — the local test Postgres believes it is fully migrated, yet the column referenced by the `AuthUser` model/insert is absent. This is a pre-existing local-DB/schema drift issue in `app/modules/auth/`, entirely unrelated to `app/modules/parametros/domain/registro.py` or `app/tests/test_parametros_registro.py` (11-01's only files). Confirmed: `test_parametros.py` does not import anything from `registro.py`, and `git status` before this plan started showed no pending changes to `auth` module files. Not touched, not fixed, per plan boundary (pure domain, no database, files_modified limited to `parametros/domain/*` and one test file).

**Action needed (outside this plan):** whoever owns the local dev/test Postgres container should confirm the `auth_users` table matches migration `030`'s expected schema (rebuild the test DB or re-run migrations from scratch) before phase 11's later plans (which do touch `auth`-adjacent code, e.g. requested_by_role) are executed and verified.

**Confirmed via full-suite run (`uv run pytest -q` equivalent, `./.venv/Scripts/python.exe -m pytest -q`, 2026-08-17):** `app/tests/test_schema_guard.py::test_colunas_do_model_existem_no_banco[auth_users]` itself fails — an existing guard test whose entire job is to detect exactly this class of drift. This independently confirms the local Postgres `auth_users` table is out of sync with the ORM model, and explains the cascading failures in `test_auth_flows.py`, `test_comunicacoes.py`, `test_comunicacoes_delivery_repository.py`, `test_parametros.py`, `test_pedidos_routes.py`, and `test_pedidos_read_projection.py` (26 failed, 43 errors total) — every one of them needs a seeded `AuthUser`/token and hits the same missing-column error. None of these files import anything from `app/modules/parametros/domain/registro.py` or `exceptions.py::ValorDeParametroInvalidoError`; the 685 passed / 16 skipped tests that don't need a real auth-seeded user all stay green, including the 21 new tests in `app/tests/test_parametros_registro.py`.

## `uv run pytest` trampoline failure on this workstation

**Symptom:** `uv run pytest ...` reliably fails with `error: uv trampoline failed to canonicalize script path` (uv 0.11.15, Windows). No output from pytest at all.

**Workaround used for all verification in 11-01:** `./.venv/Scripts/python.exe -m pytest ...` (equivalent invocation, same venv, same test discovery/config from `pyproject.toml`).

**Why out of scope:** environmental — path contains `Área` (non-ASCII) which is a known class of issue for uv's Windows trampoline scripts; unrelated to any code this plan touches. Not fixed here.
