---
quick_id: 260825-jhv
verified: 2026-08-25T00:00:00Z
status: passed
score: 9/9 must-haves verified
overrides_applied: 0
---

# Quick Task 260825-jhv: Exceção de blacklist no motor de adequação — Verification Report

**Task Goal:** Implementar exceção de blacklist no motor de adequação — cliente com
`indica_blacklist="SIM"` na origem NUNCA pode ter a grade alterada pelo motor, mesmo em modo
"com adequação": só pode ser reservado exatamente o que ele pediu por tamanho (tudo-ou-nada por
par), e se faltar qualquer tamanho pedido, o produto inteiro daquele pedido fica em stand-by com
motivo "blacklist" — nunca recebendo peças de tamanhos não pedidos.

**Verified:** 2026-08-25
**Status:** passed

## Goal Achievement

### Observable Truths / Must-Haves

| # | Must-have | Status | Evidence |
|---|-----------|--------|----------|
| 1 | `standby_motivo.py` has `BLACKLIST = "blacklist"` in `MOTIVOS_VALIDOS` and `__all__` | ✓ VERIFIED | Read file: `BLACKLIST = "blacklist"` (line 23), `MOTIVOS_VALIDOS = frozenset({SEM_CREDITO, SEM_ESTOQUE, FURO_GRADE, BLACKLIST})` (line 25), included in `__all__` (line 31) |
| 2 | Migration `033_pedido_indica_blacklist.py` exists, `down_revision="032"`, adds `indica_blacklist` to `pedidos`/`pedido_produto_read`, widens `ck_psm_motivo`, symmetric downgrade | ✓ VERIFIED | Read file: `revision="033"`, `down_revision="032"` (lines 37-38); `upgrade()` adds both columns with `server_default=sa.text("false")` and drops/recreates `ck_psm_motivo` with 4 values; `downgrade()` reverses CHECK first, then drops columns (lines 43-79) |
| 3 | `databricks_reader.py::ler_pedidos_em_aberto()` SELECT includes `indica_blacklist`, excludes `bloqueio_faturamento` | ✓ VERIFIED | Line 27: `"status_credito, dt_emissao, indica_blacklist "`. Grep for `bloqueio_faturamento` across `app/` returns zero matches |
| 4 | `agregacao.py::agregar_itens_pedidos()` captures `"indica_blacklist": _parse_bool(...)` | ✓ VERIFIED | Line 231: `"indica_blacklist": _parse_bool(linha.get("indica_blacklist"))` |
| 5 | `repositorio_snapshot.py` propagates `indica_blacklist` into `pedido_produto_read` via pending-read insert SQL | ✓ VERIFIED | `bool_or(p.indica_blacklist) AS indica_blacklist` in `products` CTE (line 127); column present in INSERT column list (line 150) and SELECT (line 161) |
| 6 | `adapters.py::_PENDING_BASE_SQL` and `load_pending_items()` select and return `indica_blacklist` | ✓ VERIFIED | `p.indica_blacklist` in `pending_base` CTE (line 35); SELECT in `load_pending_items` (line 216); dict key `"indica_blacklist": bool(row["indica_blacklist"])` (line 239) |
| 7 | `motor_adequacao.py::_processar_pedidos_canal`: `blacklist_nrs` computed, dispatch condition `if modo == ModoAdequacao.SEM_ADEQUAR or nr in blacklist_nrs:` forces blacklisted `nr_pedido` into tudo-ou-nada regardless of `modo` | ✓ VERIFIED | `blacklist_nrs = {nr for nr, itens in todos_pedidos_flat.items() if any(i.get("indica_blacklist") for i in itens)}` (lines 668-672); dispatch condition literally matches at line 785 |
| 8 | Standby motivo written for blacklisted pair in stand-by is `BLACKLIST`, not `FURO_GRADE`/`SEM_ESTOQUE`, at both write sites (furo-de-grade early-continue and `_commitar_par`'s no-stock path) | ✓ VERIFIED | Line 796-798: `preteridos_motivo[par] = (BLACKLIST if nr in blacklist_nrs else FURO_GRADE)` inside tudo-ou-nada branch; line 826: `motivo_sem_estoque=BLACKLIST if nr in blacklist_nrs else SEM_ESTOQUE` passed to `_commitar_par`, which writes `preteridos_motivo[par] = motivo_sem_estoque` (line 460). Second `FURO_GRADE` write site (line 872, ADEQUAR branch) deliberately untouched — blacklisted `nr` never reaches it because of the `continue` at line 828 |
| 9 | Invariant `len(preteridos) == len(preteridos_motivo)` holds at all 4 write sites | ✓ VERIFIED | Site 1 (`processar_pedidos`, sem-canal defensive branch, lines 557/562); Site 2 (tudo-ou-nada/blacklist furo branch, lines 795/796-798); Site 3 (ADEQUAR furo branch, lines 871/872); Site 4 (`_commitar_par`, shared by both dispatch branches, lines 459/460) — every `append` has a matching same-branch `preteridos_motivo` write. Confirmed further by explicit `len(...) == len(...)` asserts in all 6 scenarios of `test_pedidos_motor_blacklist.py` |

**Score:** 9/9 must-haves verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `alembic/versions/033_pedido_indica_blacklist.py` | New migration, down_revision=032 | ✓ VERIFIED | Exists, content matches spec exactly, symmetric downgrade |
| `app/modules/pedidos/domain/standby_motivo.py` | `BLACKLIST` in vocabulary | ✓ VERIFIED | 4-value frozenset, exported |
| `app/modules/pedidos/domain/motor_adequacao.py` | `blacklist_nrs` + forced dispatch + motive relabeling | ✓ VERIFIED | All 3 changes present and wired |
| `app/modules/pedidos/processing/infrastructure/adapters.py` | `load_pending_items` propagates `indica_blacklist` | ✓ VERIFIED | Present in SQL and returned dict |
| `app/tests/test_pedidos_motor_blacklist.py` | Scenarios (a)-(e) + regression | ✓ VERIFIED | 282 lines, 6 test functions, all scenarios substantively implemented with correct assertions |
| `app/tests/test_migration_033_pedido_indica_blacklist.py` | Destructive upgrade/downgrade cycle | ✓ VERIFIED | 207 lines, full upgrade→downgrade→re-upgrade cycle against isolated Postgres DB, CHECK constraint tested both directions |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `standby_motivo.py` | `motor_adequacao.py` | `from ... import BLACKLIST` | ✓ WIRED | Line 18: `from app.modules.pedidos.domain.standby_motivo import BLACKLIST, FURO_GRADE, SEM_ESTOQUE` |
| `033_pedido_indica_blacklist.py` | `pedido_standby_motivo.ck_psm_motivo` | DROP+CREATE CONSTRAINT | ✓ WIRED | Confirmed in migration body |
| `motor_adequacao.py::blacklist_nrs` | dispatch by mode | `nr in blacklist_nrs` | ✓ WIRED | Line 785, literal match |
| `adapters.py::load_pending_items` | `motor_adequacao.py` (`todos_pedidos_flat`) | dict key `indica_blacklist`, no projection loss | ✓ WIRED | Confirmed end-to-end: SQL → dict → `agrupar_por_produto` → `todos_pedidos_flat` (no field projection in between) |
| `repository.py::_assert_plan_still_current` | staleness fingerprint | SQL + dict reconstruction include `indica_blacklist` | ✓ WIRED | Auto-fixed during Task 3 (outside original plan file list); confirmed present in both SELECT (line 410) and dict (line 455) — necessary because fingerprint hash now includes this key |

### Data-Flow Trace (Level 4)

Not applicable in the strict UI-rendering sense (this phase is backend-only, no React components). The relevant data-flow chain (Databricks column → ingestion → snapshot → motor input → motor decision → persisted standby reason) was traced file-by-file above and confirmed intact at every hop, with no static/hardcoded fallback masking real data.

### Behavioral Spot-Checks / Test Execution

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `alembic heads` shows single head `033` | `uv run alembic heads` | `033 (head)` | ✓ PASS |
| Test database (`system_automation_test`) is fully migrated | `DATABASE_URL=...system_automation_test uv run alembic current` | `033 (head)` | ✓ PASS |
| Full test suite passes with no new failures | `DATABASE_URL=...system_automation_test uv run pytest -q --ignore=.../test_pedidos_processing_sem_adequar_memory_024.py` | `884 passed, 18 skipped in 224.73s` | ✓ PASS (matches SUMMARY claim exactly, 0 failed) |
| 5 business scenarios (a-e) + regression test all pass | inspected `test_pedidos_motor_blacklist.py`, part of full suite run above | all 6 functions present, ran clean in the 884-pass run | ✓ PASS |

**Note on baseline discrepancy:** STATE.md documents the historical Docker-run baseline as 849 passed/17 skipped/1 failed. This verification ran natively on Windows (same environment the executor used) and independently reproduced 884 passed/18 skipped/0 failed — i.e., zero failures, more tests than baseline (35 new from this task), and the one previously-known failure did not reproduce outside Docker. This is a net improvement, not a regression, and was independently confirmed by this verifier (not just trusted from SUMMARY).

### Requirements Coverage

No formal `requirements` IDs declared in PLAN frontmatter (`requirements-completed: []` in SUMMARY) — this is a quick task cross-cutting Phases 14/15/16, not tied to a REQUIREMENTS.md line item. N/A.

### Anti-Patterns Found

None found. No `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` markers introduced in any of the 17 files touched by this task's 5 commits. No stub returns, no hardcoded empty-data patterns in the production code paths (`standby_motivo.py`, `motor_adequacao.py`, `adapters.py`, `repositorio_snapshot.py`, `databricks_reader.py`, `agregacao.py`, `models.py`, `repository.py`).

### Additional Checks

- **`bloqueio_faturamento` not touched anywhere:** confirmed via `grep -rn "bloqueio_faturamento" app/` — zero matches in the entire codebase.
- **No frontend files touched:** confirmed via `git diff --stat a2284cf~1 c2e64a1` — all 17 changed files are within `alembic/`, `app/modules/`, or `app/tests/`. No `frontend` directory exists inside this repo, and no commit references it.
- **STATE.md handoff for Phase 17:** confirmed via `git diff .planning/STATE.md` — an uncommitted working-tree edit adds (1) a row in the "Quick Tasks Completed" table referencing 260825-jhv and its 5 commit hashes, (2) a full handoff paragraph in "Blockers/Concerns" explaining the new `blacklist` standby motivo and that Phase 17 (frontend, not yet built) needs to add UI treatment for it, and (3) a row in "Deferred Items" pointing to the same. This matches the SUMMARY's claim that the STATE.md edit was left uncommitted for the orchestrator.
- **Working tree state:** `git status --short` shows only `.planning/STATE.md` (modified, expected) and the untracked `.planning/quick/260825-jhv-.../` directory (this task's own planning artifacts) — no stray production file changes outside the 5 committed commits.

### Human Verification Required

None. All must-haves are verifiable by direct code inspection, migration execution, and automated test run — no visual/UX/real-time/external-service behavior is in scope for this backend-only quick task.

### Gaps Summary

No gaps found. Every must-have listed in the verification brief was independently confirmed against the actual file contents (not inferred from SUMMARY.md prose), the migration was executed and inspected against both the dev and isolated test databases, and the full test suite was independently re-run by this verifier reproducing the exact pass/fail counts claimed in the SUMMARY (884 passed / 18 skipped / 0 failed). The one apparent anomaly — `alembic current` against the default (dev) database showing `032` instead of `033` — was investigated and resolved: that command was hitting the `system_automation` dev database (which Task 5 never claimed to migrate), not the `system_automation_test` database that the plan explicitly required migrating; the test database itself is confirmed at head `033`.

---

_Verified: 2026-08-25_
_Verifier: Claude (gsd-verifier)_
