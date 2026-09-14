---
phase: 1
slug: schema-linx-e-refer-ncia-de-posi-o
status: ready
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-04
updated: 2026-08-04
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `uv run pytest app/tests/test_schema_guard.py -q` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~30 seconds |

**Nota importante deste repo:** `app/tests/conftest.py` conecta num Postgres REAL (`postgresql+asyncpg://automation:automation@localhost:5432/system_automation` fora do container; `dev_db` dentro). Não existe banco de teste efêmero, e não há CI rodando testes (`.github/workflows/pipeline.yml` só faz deploy). Portanto: (a) a validação é local/manual-triggered, (b) os testes de schema só são significativos depois de `alembic upgrade head` no banco apontado por `DATABASE_URL`, e (c) `test_schema_guard.py` faz `pytest.skip` para tabela que ainda não existe no banco — um skip aqui é sinal de migration não aplicada, não de sucesso.

---

## Sampling Rate

- **After every task commit:** `uv run pytest app/tests/test_schema_guard.py -q`
- **After every plan wave:** `uv run pytest`
- **Before `/gsd-verify-work`:** suíte completa verde + `alembic check` sem operações pendentes
- **Max feedback latency:** 60 segundos (o comando por task mais lento é `alembic upgrade head` no container, ~10s)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| T1 Model `ProdutoTamanhoPosicao` | 01-01 | 1 | LINX-01 | — | — (declaração ORM, sem input) | unit (assert de metadata) | `uv run python -c "from app.modules.ingestao.models import ProdutoTamanhoPosicao as P; assert len(P.__table__.columns)==5; print('OK')"` | ✅ n/a (comando, não arquivo) | ⬜ pending |
| T2 Model `OrdemReservaLinx` | 01-01 | 1 | LINX-01 | T-01-03 (sem FK, aceito) | Sem `ForeignKeyConstraint`; layout nullable, controle NOT NULL | unit (assert de metadata) | `uv run python -c "from app.modules.pedidos.models import OrdemReservaLinx as O; assert len(O.__table__.columns)==82; assert not O.__table__.foreign_keys; print('OK')"` | ✅ n/a | ⬜ pending |
| T3 Imports em `env.py` + `test_schema_guard.py` | 01-01 | 1 | LINX-01 | T-01-01 (drift silencioso) | Guard de drift deixa de ser cego às 2 tabelas novas | integration (coleta pytest) | `uv run pytest app/tests/test_schema_guard.py --collect-only -q -k "produto_tamanho_posicao or ordens_reserva_linx"` → 4 testes | ✅ `app/tests/test_schema_guard.py` | ⬜ pending |
| T1 Migration 015 (DDL) | 01-02 | 2 | LINX-01, LINX-04 | T-01-05 (DDL sem interpolação externa), T-01-06 (não perder a migration) | Nomes literais; zero DML; zero `op.execute`; zero import de `app.*` | unit (import do módulo de migration) | `uv run python -c "import importlib.util as u; s=u.spec_from_file_location('m','alembic/versions/015_schema_linx_referencia_posicao.py'); m=u.module_from_spec(s); s.loader.exec_module(m); assert m.revision=='015' and m.down_revision=='014'; print('OK')"` | ⬜ criado pela task | ⬜ pending |
| T2 Aplicar + provar layout e tabelas vazias | 01-02 | 2 | LINX-01, LINX-04 | T-01-01 | Guard de drift cobre as 2 tabelas sem skip | integration (pytest contra Postgres real) | `uv run pytest app/tests/test_schema_guard.py -q -k "produto_tamanho_posicao or ordens_reserva_linx"` → `4 passed`, 0 skipped | ✅ `app/tests/test_schema_guard.py` | ⬜ pending |
| T2 (evidência LINX-04) | 01-02 | 2 | LINX-04 | — | Tabelas nascem vazias | integration (CLI/psql) | `docker compose -f .docker/docker-compose.yml exec -T dev_db psql -U automation -d system_automation -tA -c "SELECT (SELECT count(*) FROM produto_tamanho_posicao) + (SELECT count(*) FROM ordens_reserva_linx);"` → `0` | ✅ n/a | ⬜ pending |
| T3 Reversibilidade + ausência de diff | 01-02 | 2 | LINX-01 | T-01-04 (downgrade destrutivo), T-01-06 | `downgrade` exercitado só no `dev_db`; sem arquivo de autogenerate residual | integration (CLI Alembic) | `docker compose -f .docker/docker-compose.yml exec -T api uv run alembic check` → exit 0 | ✅ n/a | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Nenhuma criação de infraestrutura de teste é necessária nesta fase:

- [x] `app/tests/test_schema_guard.py` **já existe** e já é genérico (parametrizado sobre `sorted(Base.metadata.tables)`) — cobre colunas, nullability e PK das 2 tabelas novas automaticamente assim que os imports do Plano 01 (task 3) existirem. Não criar teste paralelo (01-RESEARCH.md, "Don't Hand-Roll").
- [x] Os gaps listados em 01-RESEARCH.md "Wave 0 Gaps" (reversibilidade e diff de autogenerate sem teste automatizado) foram resolvidos **sem** infraestrutura nova, promovendo-os a comandos CLI determinísticos com saída assertável: `alembic downgrade -1` / `alembic upgrade head` / `alembic check` e consultas a `information_schema` via `psql` (Plano 02, tasks 2 e 3). Não são pytest, mas são automatizados, repetíveis e com critério de aceite numérico.

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | Nenhum comportamento desta fase depende de julgamento humano (é DDL puro, sem UI e sem fluxo interativo) | — |

*Todos os comportamentos da fase têm comando automatizado. Ressalva honesta: não há CI que rode esses comandos — o disparo é local, pelo executor do plano, e a evidência numérica é registrada nos SUMMARYs.*

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (6/6 tasks têm comando automatizado)
- [x] Wave 0 covers all MISSING references (nada faltando: guard existente + comandos CLI)
- [x] No watch-mode flags
- [x] Feedback latency < 60s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** ready (planner, 2026-08-04)
