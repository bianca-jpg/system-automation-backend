---
phase: 01-schema-linx-e-refer-ncia-de-posi-o
verified: 2026-08-04T19:14:57Z
status: passed
score: 11/11 must-haves verified
overrides_applied: 0
---

# Fase 1: Schema Linx e referência de posição — Relatório de Verificação

**Goal da Fase:** O banco tem as duas tabelas novas do milestone — `produto_tamanho_posicao` (referência tamanho→posição) e `ordens_reserva_linx` (layout Linx) — criadas por uma migration manual, ambas vazias, sem tocar em nenhuma tabela existente.

**Verificado:** 2026-08-04T19:14:57Z
**Status:** passed
**Re-verificação:** Não — verificação inicial

## Metodologia

Verificação executada contra o ambiente Docker real (`system_automation_api`, `system_automation_db`), não apenas leitura estática de código: todas as contagens de coluna, constraints e `COUNT(*)` foram reobtidas via `psql` neste momento, o ciclo `downgrade -1` / `upgrade head` foi **re-executado de forma independente** (não apenas confiado na SUMMARY), e a suíte completa de testes (`pytest -q`) e o guard de drift (`test_schema_guard.py`) foram rodados do zero.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `ProdutoTamanhoPosicao` existe em `ingestao/models.py` com 5 colunas NOT NULL + `uq_produto_tamanho_posicao_chave` | ✓ VERIFIED | Leitura do arquivo (linhas 125-149): `id, cd_prod_cor, sg_tamanho, nr_posicao, synced_at`, todas `nullable=False`; `UniqueConstraint` presente. Confirmado também via `python -c` no container: `len(columns)==5`. |
| 2 | `OrdemReservaLinx` existe em `pedidos/models.py` com 82 colunas (5 controle NOT NULL + 77 layout Linx nullable, `e1..e48` com `server_default="0"`) | ✓ VERIFIED | Leitura do arquivo (linhas 71-177): 5 colunas de controle + 77 do layout, `e1`..`e48` declaradas explicitamente uma por linha (não via loop); `foreign_keys=set()`, `indexes=set()`. Confirmado via execução Python no container: `len(columns)==82`. |
| 3 | `Base.metadata` conhece as duas tabelas novas via `alembic/env.py` (import explícito) | ✓ VERIFIED | `alembic/env.py` linhas 16-28: `ProdutoTamanhoPosicao` importado de `ingestao.models`, `OrdemReservaLinx` de `pedidos.models`. |
| 4 | `test_schema_guard.py` cobre as 2 tabelas novas (sem skip) | ✓ VERIFIED | `docker compose exec api uv run pytest app/tests/test_schema_guard.py -q -k "produto_tamanho_posicao or ordens_reserva_linx"` → `4 passed, 24 deselected` (sem `skipped`). Suíte completa do guard: `28 passed`. |
| 5 | `alembic upgrade head` cria `produto_tamanho_posicao` com `cd_prod_cor`, `sg_tamanho`, `nr_posicao` | ✓ VERIFIED | `alembic current` → `015 (head)`. `psql information_schema.columns` para `produto_tamanho_posicao` → `5` colunas. |
| 6 | `alembic upgrade head` cria `ordens_reserva_linx` com o layout do CSV Linx + colunas de controle | ✓ VERIFIED | `psql information_schema.columns` para `ordens_reserva_linx` → `82`; `is_nullable='NO'` → `5` (só as de controle); colunas `e[0-9]+` → `48`; unique constraints (`uq_produto_tamanho_posicao_chave`, `uq_ordens_reserva_linx_chave`) → `2`. |
| 7 | `SELECT COUNT(*)` = 0 nas duas tabelas logo após a migration (LINX-04, sem backfill) | ✓ VERIFIED | `psql` → `SELECT (SELECT count(*) FROM produto_tamanho_posicao) + (SELECT count(*) FROM ordens_reserva_linx);` → `0`. |
| 8 | `alembic downgrade -1` remove as duas tabelas sem erro; `upgrade head` as recria | ✓ VERIFIED | **Re-executado nesta verificação** (não apenas lido da SUMMARY): após `downgrade -1`, tabelas novas → `0`, tabelas existentes (10 nomeadas) → `10` (intactas); após `upgrade head`, tabelas novas → `2`, `alembic current` → `015 (head)`, `COUNT(*)` somado → `0`. |
| 9 | Nenhuma tabela existente é alterada pela migration 015 | ✓ VERIFIED | Migration contém apenas 2 `op.create_table` / 2 `op.drop_table`; nenhum `add_column`/`alter_column`/`drop_column` no arquivo. `psql` confirma as 10 tabelas pré-existentes intactas antes/depois do ciclo downgrade/upgrade. |
| 10 | `alembic check` não aponta operações pendentes relacionadas às 2 tabelas novas | ✓ VERIFIED (com nota) | `alembic check` sai com código ≠ 0, mas os únicos diffs reportados são `remove_index` em `ix_auth_otp_challenges_id` e `ix_auth_users_id` (tabelas pré-existentes, não relacionadas a esta fase). Nenhum diff menciona `produto_tamanho_posicao` ou `ordens_reserva_linx`. Este é exatamente o cenário de "exceção admitida" descrito no critério de aceite do 01-02-PLAN.md (Task 3) — diff pré-existente fora de escopo, documentado também no 01-REVIEW.md. Não é uma falha desta fase. |
| 11 | Nenhum model existente alterado e nenhuma dependência nova instalada | ✓ VERIFIED | `pyproject.toml`/`uv.lock` sem alteração nos commits da fase (`817bac1`, `69b7dc2`, `4e3a23b`, `0f7f47a`); classes `Pedido`, `PedidoProcessadoErp`, `Estoque`, `FaturamentoColecao` (ingestao) e `PedidoProcessado`, `OrdemReserva`, `PedidoModificacao` (pedidos) inalteradas — apenas adições ao final dos arquivos. |

**Score:** 11/11 truths verificadas

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/modules/ingestao/models.py` | classe `ProdutoTamanhoPosicao` (Mapped) | ✓ VERIFIED | Presente, 5 colunas, estilo `Mapped`/`mapped_column` idêntico às classes existentes. |
| `app/modules/pedidos/models.py` | classe `OrdemReservaLinx` (Column), 82 colunas | ✓ VERIFIED | Presente, estilo `Column` sem `Mapped`, 82 colunas na ordem especificada. |
| `alembic/env.py` | registro dos 2 models em `target_metadata` | ✓ VERIFIED | Imports presentes nas linhas 21 e 25; `target_metadata = Base.metadata` na linha 35. |
| `app/tests/test_schema_guard.py` | cobertura do guard de drift para as 2 tabelas | ✓ VERIFIED | Imports presentes; 4 testes coletados e passando para as 2 tabelas. |
| `alembic/versions/015_schema_linx_referencia_posicao.py` | DDL manual das 2 tabelas (upgrade) + drop em ordem inversa (downgrade) | ✓ VERIFIED | 104 linhas; `revision="015"`, `down_revision="014"`; 2×`op.create_table`, 2×`op.drop_table`; sem FK, sem DML, sem import de `app.*`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `alembic/env.py` | `ProdutoTamanhoPosicao` / `OrdemReservaLinx` | import explícito → `target_metadata` | ✓ WIRED | `grep` confirma 2 ocorrências nos imports; `alembic upgrade head` aplicou de fato as 2 tabelas (prova indireta de que `target_metadata` as conhece). |
| `app/tests/test_schema_guard.py` | `Base.metadata.tables` | `parametrize` sobre `sorted(Base.metadata.tables)` | ✓ WIRED | 4 testes coletados e `4 passed` (não `skipped`) — prova que o parametrize gerou os casos e que eles encontram as tabelas no banco. |
| `alembic/versions/015...py` | `014_or_por_produto.py` | `down_revision = "014"` | ✓ WIRED | `alembic heads` → única head `015`; cadeia não quebrada. |
| `alembic/versions/015...py` | models do Plano 01 | DDL espelha coluna a coluna | ✓ WIRED | Contagens de coluna, nullability, nomes de constraint e `e1..e48` no banco batem exatamente com os models (verificado via `psql` + leitura de código lado a lado). |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|--------------|--------|----------|
| LINX-01 | 01-01-PLAN.md, 01-02-PLAN.md | Existe a tabela `ordens_reserva_linx` no PostgreSQL com o layout aceito pelo Linx; campos sem fonte conhecida ficam vazios | ✓ SATISFIED | Tabela criada com 82 colunas no layout do CSV Linx; 77 colunas nullable (sem fonte hoje) confirmadas NULL por default; `REQUIREMENTS.md` já marca como Complete. |
| LINX-04 | 01-02-PLAN.md | A tabela Linx começa vazia (sem backfill de reservas antigas) | ✓ SATISFIED | `COUNT(*)` = 0 verificado logo após `upgrade head` e novamente após o ciclo `downgrade`/`upgrade` re-executado nesta verificação. |

Nenhum requirement órfão encontrado: `REQUIREMENTS.md` mapeia apenas LINX-01 e LINX-04 para a Fase 1, e ambos aparecem no campo `requirements` dos dois PLANs.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | Nenhum `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` encontrado nos 5 arquivos modificados pela fase | — | `grep` explícito não retornou nenhuma ocorrência. |

**Achados do code review (01-REVIEW.md), não bloqueantes para o goal desta fase:**
- ⚠️ WR-01: query de PK do schema guard não qualifica schema (`pg_class` sem filtro `public`) — pré-existente, risco de falso positivo se houver relação homônima em outro schema.
- ⚠️ WR-02: `nr_posicao` sem `CheckConstraint` de faixa (1..48) — risco de drift silencioso a ser resolvido antes da Fase 3.
- ℹ️ IN-01 a IN-04: comentário desatualizado, falta de unique adicional em `(cd_prod_cor, nr_posicao)`, cobertura parcial do guard (não compara tipo/default), `set_main_option` sem escape de `%` em `alembic/env.py` (pré-existente).

Nenhum destes é um `TBD`/`FIXME`/`XXX` sem referência de acompanhamento, e nenhum impede o goal desta fase (tabelas criadas, vazias, reversíveis, sem tocar em tabela existente). Ficam registrados como candidatos a débito técnico para a Fase 2/3, onde os dados passam a fluir de fato.

### Behavioral Spot-Checks / Probe Execution

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| `alembic upgrade head` aplica a migration 015 | `docker compose exec api uv run alembic current` | `015 (head)` | ✓ PASS |
| Tabelas novas existem e vazias | `psql -c "SELECT count(*) FROM information_schema.tables WHERE table_name IN (...)"` | `2`; `COUNT(*)` somado = `0` | ✓ PASS |
| Layout de colunas correto | `psql information_schema.columns` (contagens e nullability) | `82`/`5` colunas, `5` NOT NULL, `48` colunas `e*` | ✓ PASS |
| Reversibilidade (`downgrade -1` / `upgrade head`) | re-executado nesta verificação | tabelas novas → `0` → `2`; existentes sempre `10` | ✓ PASS |
| `alembic check` sem diff pendente para as 2 tabelas novas | `docker compose exec api uv run alembic check` | código ≠ 0, mas diffs só de `auth_otp_challenges`/`auth_users` (pré-existentes) | ✓ PASS (exceção admitida documentada no plano) |
| Guard de drift cobre as 2 tabelas sem skip | `pytest test_schema_guard.py -k "produto_tamanho_posicao or ordens_reserva_linx"` | `4 passed` | ✓ PASS |
| Suíte completa | `docker compose exec api uv run pytest -q` | `215 passed, 1 warning` (0 failed) | ✓ PASS |
| Guard de drift completo (todas as tabelas) | `docker compose exec api uv run pytest app/tests/test_schema_guard.py -q` | `28 passed` | ✓ PASS |

Não houve necessidade de rodar probes dedicados (`scripts/*/tests/probe-*.sh`) — nenhum foi declarado no PLAN/SUMMARY desta fase e o projeto não segue esse padrão de probe.

### Human Verification Required

Nenhum item. Todos os critérios são observáveis programaticamente (schema de banco, contagens, execução de comandos Alembic/pytest); não há UI, fluxo de usuário ou comportamento visual nesta fase.

### Gaps Summary

Nenhum gap encontrado. Os dois planos (01-01 modelos ORM + registro de imports, 01-02 migration 015 + aplicação + prova de reversibilidade) entregam exatamente o que o goal da fase exige: as duas tabelas novas existem no banco de dev, vazias, criadas por migration manual, sem alterar nenhuma tabela existente. Todas as evidências numéricas das SUMMARYs foram reproduzidas de forma independente nesta verificação (não apenas aceitas por citação), incluindo o re-teste do ciclo `downgrade`/`upgrade`.

O único ponto que merece atenção do desenvolvedor (não bloqueante) é o achado do code review WR-02 (`nr_posicao` sem `CheckConstraint` de faixa 1..48): como a tabela está vazia, corrigir agora é gratuito; adiar significa herdar o custo de uma migration extra quando a Fase 2 já estiver gravando dados.

---

_Verificado: 2026-08-04T19:14:57Z_
_Verificador: Claude (gsd-verifier)_
