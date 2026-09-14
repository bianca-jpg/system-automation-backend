---
phase: 01-schema-linx-e-refer-ncia-de-posi-o
plan: 02
subsystem: database
tags: [alembic, postgres, migration, sqlalchemy]

# Dependency graph
requires:
  - phase: 01-schema-linx-e-refer-ncia-de-posi-o
    provides: "Classes ORM ProdutoTamanhoPosicao e OrdemReservaLinx (Plano 01), fonte de verdade coluna a coluna do DDL desta migration"
provides:
  - "Migration Alembic 015 (manual, sem autogenerate) que cria produto_tamanho_posicao (5 colunas) e ordens_reserva_linx (82 colunas)"
  - "Tabela produto_tamanho_posicao aplicada no banco de dev, vazia, com uq_produto_tamanho_posicao_chave"
  - "Tabela ordens_reserva_linx aplicada no banco de dev, vazia, com uq_ordens_reserva_linx_chave"
  - "Prova de reversibilidade (downgrade -1 / upgrade head) e de ausência de diff pendente para as 2 tabelas novas"
affects: [fase-2-ingestao, fase-3-escrita-linx]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "create_table simples sem lógica condicional (padrão da 013), usado quando a tabela é 100% nova e não há drift possível"
    - "Atalho de list comprehension para colunas repetitivas (e1..e48) é aceitável só no arquivo de migration, nunca no model"
    - "alembic check em vez de revision --autogenerate descartável para provar ausência de diff pendente, evitando o risco de um segundo arquivo de migration temporário ser commitado por engano (precedente da migration 012 perdida)"

key-files:
  created:
    - alembic/versions/015_schema_linx_referencia_posicao.py
  modified: []

key-decisions:
  - "MCP backstage_get_coding_standards não estava disponível neste ambiente (mesma constatação do Plano 01) — seguido o fallback do plano: padrão literal da migration 013 (create_table simples) e 005 (duas tabelas num upgrade só)"
  - "alembic check (Alembic 1.18.4) usado para provar ausência de diff pendente em vez do autogenerate descartável sugerido como alternativa em 01-RESEARCH.md — evita gerar e ter que apagar um arquivo de migration temporário"
  - "Diff pré-existente e fora de escopo confirmado por alembic check: remove_index em ix_auth_otp_challenges_id e ix_auth_users_id (mesmo achado já registrado no Plano 01) — não corrigido, apenas documentado"

patterns-established: []

requirements-completed: [LINX-01, LINX-04]

# Metrics
duration: ~30min
completed: 2026-08-04
---

# Phase 1 Plan 2: Migration 015 (produto_tamanho_posicao e ordens_reserva_linx) Summary

**Migration Alembic manual 015 encadeada em 014, criando produto_tamanho_posicao (5 colunas) e ordens_reserva_linx (82 colunas, 5 NOT NULL + 77 do layout Linx nullable), aplicada no banco de dev, vazia, reversível nos dois sentidos e sem diff pendente.**

## Performance

- **Duration:** ~30 min
- **Tasks:** 3/3 completos
- **Files modified:** 1 (arquivo novo)

## Accomplishments
- `alembic/versions/015_schema_linx_referencia_posicao.py` criado com Write (não via `alembic revision`), revision `015`, `down_revision = "014"`, DDL espelhando coluna a coluna os models `ProdutoTamanhoPosicao` e `OrdemReservaLinx` do Plano 01
- Migration aplicada no banco de dev (`docker compose exec api uv run alembic upgrade head`): as 2 tabelas existem com 82 e 5 colunas respectivamente, `SELECT COUNT(*)` = 0 nas duas (LINX-04, sem backfill)
- Reversibilidade provada: `alembic downgrade -1` remove as 2 tabelas sem tocar nas 10 tabelas existentes; `alembic upgrade head` as recria idênticas (mesmas contagens de coluna, unique constraints e 0 linhas)
- `alembic check` não aponta nenhuma operação pendente para `produto_tamanho_posicao`/`ordens_reserva_linx`; o único diff reportado é pré-existente (índices de `auth_otp_challenges`/`auth_users`), fora do escopo desta fase
- Guard de drift (`test_schema_guard.py`) cobre as 2 tabelas novas sem skip: 4 testes passando; suíte completa: `215 passed, 1 warning` (211 pré-existentes + 4 das tabelas novas), rodada 2 vezes (antes e depois do ciclo downgrade/upgrade)

## Task Commits

1. **Task 1: Escrever a migration 015 (DDL manual das 2 tabelas)** - `0f7f47a` (feat)
2. **Task 2: Aplicar a migration e provar layout + tabelas vazias** - sem commit (task de verificação, nenhum arquivo alterado)
3. **Task 3: Provar reversibilidade e ausência de diff/impacto em tabelas existentes** - sem commit (task de verificação, nenhum arquivo alterado)

**Plan metadata:** commit ainda a ser criado (docs: complete plan)

## Files Created/Modified
- `alembic/versions/015_schema_linx_referencia_posicao.py` - migration manual, 2 `op.create_table` (produto_tamanho_posicao, ordens_reserva_linx) e 2 `op.drop_table` na ordem inversa; zero DML, zero FK, zero import de `app.*`

## Decisions Made
- MCP `backstage_get_coding_standards` não estava disponível neste ambiente (nenhuma ferramenta `mcp__backstage__*` exposta) — seguido o fallback documentado no plano: estrutura literal da migration 013 (cabeçalho, `create_table` simples) e 005 (duas tabelas num único `upgrade()`), sem a lógica condicional de `information_schema` da migration 014 (que só se aplica a tabelas já populadas com drift histórico).
- `alembic check` (disponível no Alembic 1.18.4 instalado) usado em vez do fallback de `alembic revision --autogenerate -m check_diff_015` sugerido como alternativa em 01-RESEARCH.md — evita o risco de um arquivo de migration temporário ser commitado por engano, o mesmo tipo de incidente que perdeu a migration 012.

## Deviations from Plan

None - plano executado exatamente como escrito. As 7 verdades do `must_haves` foram provadas sem necessidade de fix automático (Regras 1-3), sem mudança arquitetural (Regra 4) e sem gate de autenticação.

## Issues Encountered
- Ambiente local Windows não roda `uv run` diretamente (mesma limitação de `pyicu` já documentada no Plano 01). Todas as verificações desta plano (migration, psql, pytest) foram executadas via `docker compose -f .docker/docker-compose.yml exec -T api uv run ...` / `exec -T dev_db psql ...`, aproveitando a stack já em execução (`system_automation_api`, `system_automation_db`) — nenhuma mudança de código foi necessária, apenas o canal de execução.
- `alembic check` reportou um diff pré-existente e fora de escopo (`remove_index` em `ix_auth_otp_challenges_id` e `ix_auth_users_id`) — o mesmo achado já registrado no Plano 01 via verificação cruzada opcional. Confirmado por `grep` que os diffs reais (linhas `FAILED`/`ERROR`) não mencionam `produto_tamanho_posicao` nem `ordens_reserva_linx` (a busca ampla na saída completa do comando retorna 2 ocorrências, mas são apenas linhas informativas `INFO ... Detected sequence named '...id_seq' as owned by ... assuming SERIAL and omitting`, não diffs pendentes). Não corrigido nesta fase — registrado aqui para visibilidade, candidato a todo futuro.

## Evidência numérica (critérios do ROADMAP)

- Tabelas novas em `information_schema.tables`: `2`
- Colunas de `ordens_reserva_linx`: `82` (5 `NOT NULL` de controle + 77 `nullable` do layout Linx, confirmado por `is_nullable='NO'` = `5`)
- Colunas de `produto_tamanho_posicao`: `5`
- Colunas `e1..e48`: `48`
- Unique constraints (`uq_produto_tamanho_posicao_chave`, `uq_ordens_reserva_linx_chave`): `2`
- `SELECT COUNT(*)` somado nas 2 tabelas, logo após `upgrade head`: `0` (LINX-04)
- Ciclo `downgrade -1`: tabelas novas → `0`; tabelas existentes (`ordens_reserva`, `pedidos`, `estoque`, `pedidos_processados`, `pedido_modificacoes`, `parametros`, `auth_users`, `faturamento_colecao`, `pedidos_processados_erp`, `comunicacoes`) → `10` (intactas)
- Ciclo `upgrade head` (recriação): tabelas novas → `2`, `alembic current` → `015 (head)`, `SELECT COUNT(*)` somado → `0`
- `test_schema_guard.py -k "produto_tamanho_posicao or ordens_reserva_linx"`: `4 passed`, sem `skipped`
- `test_schema_guard.py` completo: `28 passed`
- `pytest -q` (suíte completa), rodada antes e depois do ciclo downgrade/upgrade: `215 passed, 1 warning` nas duas vezes

## User Setup Required

None - nenhuma configuração de serviço externo necessária. Nenhuma dependência nova foi instalada.

## Next Phase Readiness
- Fase 1 completa: as 2 tabelas do milestone existem no banco de dev, vazias, reversíveis e sem drift pendente. Fase 2 (ingestão) pode gravar em `produto_tamanho_posicao`; Fase 3 (escrita Linx) pode fazer upsert em `ordens_reserva_linx` usando `uq_ordens_reserva_linx_chave` como target de `ON CONFLICT`.
- Nenhum bloqueio identificado. O diff pré-existente de índices (`auth_otp_challenges`/`auth_users`) permanece fora de escopo, sem impacto nas próximas fases.

---
*Phase: 01-schema-linx-e-refer-ncia-de-posi-o*
*Completed: 2026-08-04*

## Self-Check: PASSED

`alembic/versions/015_schema_linx_referencia_posicao.py` e o próprio `01-02-SUMMARY.md` confirmados no disco (`FOUND`). O commit da Task 1 (`0f7f47a`) confirmado em `git log --oneline --all`. Tasks 2 e 3 não geraram commit próprio (nenhum arquivo alterado — tasks de aplicação/verificação).
