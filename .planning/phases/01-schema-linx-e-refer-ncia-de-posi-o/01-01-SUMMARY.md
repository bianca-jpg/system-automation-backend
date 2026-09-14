---
phase: 01-schema-linx-e-refer-ncia-de-posi-o
plan: 01
subsystem: database
tags: [sqlalchemy, alembic, postgres, orm, schema]

# Dependency graph
requires: []
provides:
  - "Classe ORM ProdutoTamanhoPosicao (app/modules/ingestao/models.py) — 5 colunas NOT NULL, uq_produto_tamanho_posicao_chave"
  - "Classe ORM OrdemReservaLinx (app/modules/pedidos/models.py) — 82 colunas (5 controle + 77 layout Linx), uq_ordens_reserva_linx_chave"
  - "Os 2 models registrados em alembic/env.py (target_metadata) e app/tests/test_schema_guard.py (guard de drift)"
affects: [01-02, fase-2-ingestao, fase-3-escrita-linx]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Estilo de model por arquivo, não por projeto: Mapped/mapped_column em ingestao, Column sem Mapped em pedidos (pré-existente, mantido)"
    - "Import explícito de models nos 2 pontos (alembic/env.py + test_schema_guard.py) — nenhum autodiscovery"
    - "UniqueConstraint de chave natural serve de índice pelo prefixo esquerdo — sem index=True redundante"
    - "Colunas e1..e48 declaradas explicitamente uma por linha no model (loop/comprehension só é aceitável em migration)"

key-files:
  created: []
  modified:
    - app/modules/ingestao/models.py
    - app/modules/pedidos/models.py
    - alembic/env.py
    - app/tests/test_schema_guard.py

key-decisions:
  - "MCP backstage_get_coding_standards não estava disponível neste ambiente (nenhuma ferramenta mcp__backstage__* exposta, apesar de .mcp.json existir no repo) — seguido o fallback do plano: padrões dos arquivos análogo do próprio repositório (Estoque/FaturamentoColecao para o estilo Mapped, OrdemReserva para o estilo Column)"
  - "e1..e48 com server_default='0' e nullable=True, conforme decisão travada do plano (prevalece sobre a recomendação de adiar da 01-RESEARCH.md)"
  - "Sem index=True em nr_pedido/OrdemReservaLinx — o UniqueConstraint(nr_pedido, cd_prod_cor) já serve de índice pelo prefixo esquerdo (precedente FaturamentoColecao)"

patterns-established:
  - "Novo model ORM: sempre registrar nos 2 pontos de import explícito (alembic/env.py e test_schema_guard.py) antes de considerar a declaração completa"

requirements-completed: [LINX-01]

# Metrics
duration: ~20min
completed: 2026-08-04
---

# Phase 1 Plan 1: Models ORM Linx e referência de posição Summary

**Duas classes ORM novas (ProdutoTamanhoPosicao no estilo Mapped, OrdemReservaLinx com 82 colunas no estilo Column) registradas nos 2 pontos de import explícito que alimentam o autogenerate-diff-check do Alembic e o guard de drift model↔banco.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 3/3 completos
- **Files modified:** 4

## Accomplishments
- `ProdutoTamanhoPosicao` declarada em `app/modules/ingestao/models.py`: 5 colunas NOT NULL (`id`, `cd_prod_cor`, `sg_tamanho`, `nr_posicao`, `synced_at`) + `uq_produto_tamanho_posicao_chave`
- `OrdemReservaLinx` declarada em `app/modules/pedidos/models.py`: 82 colunas (5 de controle NOT NULL + 77 do layout Linx nullable, `e1..e48` `Integer` com `server_default="0"`) + `uq_ordens_reserva_linx_chave`, sem FK e sem índice extra
- Os 2 models registrados em `alembic/env.py` (popula `target_metadata`) e `app/tests/test_schema_guard.py` (guard parametrizado sobre `Base.metadata.tables`) — os 2 pontos que, se esquecidos, deixam a proteção contra drift cega (incidente da migration 012 documentado na docstring do próprio guard)

## Task Commits

1. **Task 1: Model ProdutoTamanhoPosicao no módulo ingestao** - `817bac1` (feat)
2. **Task 2: Model OrdemReservaLinx no módulo pedidos (layout Linx completo, 82 colunas)** - `69b7dc2` (feat)
3. **Task 3: Registrar os 2 models em alembic/env.py e em test_schema_guard.py** - `4e3a23b` (feat)

**Plan metadata:** commit ainda a ser criado (docs: complete plan)

## Files Created/Modified
- `app/modules/ingestao/models.py` - adiciona `class ProdutoTamanhoPosicao(Base)` ao final do arquivo
- `app/modules/pedidos/models.py` - estende o import do topo (`Boolean`, `Numeric`, `UniqueConstraint`) e adiciona `class OrdemReservaLinx(Base)` ao final do arquivo
- `alembic/env.py` - estende os 2 blocos de import (`ingestao.models`, `pedidos.models`) com os 2 símbolos novos
- `app/tests/test_schema_guard.py` - mesma extensão de import, sem nenhuma outra mudança de código (o teste já é parametrizado sobre `sorted(Base.metadata.tables)`)

## Decisions Made
- MCP `backstage_get_coding_standards` não estava disponível neste ambiente — nenhuma ferramenta `mcp__backstage__*` foi exposta ao agente, apesar do `.mcp.json` existir no repositório. Seguido o fallback documentado no plano: padrões dos arquivos análogos do próprio repositório (`Estoque`/`FaturamentoColecao` para o estilo `Mapped`; `OrdemReserva` para o estilo `Column`).
- `uv run` local falhou ao tentar buildar `pyicu` no ambiente Windows do host (falta de ICU/pkg-config nativo). Todas as verificações e a suíte de testes foram executadas via `docker compose -f .docker/docker-compose.yml exec api uv run ...`, aproveitando os volumes `../app` e `../alembic` já montados no container `api` (bind mount, refletindo as edições do host em tempo real). Isso não é um desvio de escopo do plano — é apenas o mecanismo de execução das verificações `<automated>` já especificadas.

## Deviations from Plan

None - plano executado exatamente como especificado. As 5 verdades do `must_haves` e as 3 tasks foram implementadas sem necessidade de fix automático (Regras 1-3), sem mudança arquitetural (Regra 4) e sem gate de autenticação.

## Issues Encountered
- Ambiente local Windows não conseguia rodar `uv run` diretamente (build de `pyicu` falha por falta de ICU nativo). Resolvido rodando todas as verificações dentro do container `api` já em execução via `docker compose exec`, que tem o `.venv` funcional e os diretórios `app/` e `alembic/` montados como bind mount do host — nenhuma mudança de código foi necessária, apenas o canal de execução dos comandos de verificação.
- Verificação cruzada opcional (`uv run alembic check`) confirmou que o Alembic agora vê os 2 models novos (`Detected added table 'ordens_reserva_linx'` e `'produto_tamanho_posicao'`), provando que `alembic/env.py` está correto. O mesmo comando também reportou 2 diffs pré-existentes e não relacionados a este plano (`remove_index` em `ix_auth_otp_challenges_id` e `ix_auth_users_id`) — fora do escopo desta task (Scope Boundary), não corrigidos; registrados aqui apenas para visibilidade.

## User Setup Required

None - nenhuma configuração de serviço externo necessária. Nenhuma dependência nova foi instalada.

## Confirmações para o Plano 02

- As tabelas `produto_tamanho_posicao` e `ordens_reserva_linx` ainda **não existem no banco**: os 4 casos novos do `test_schema_guard.py` (2 tabelas × 2 funções de teste) entram em `SKIPPED` com a mensagem "não existe no banco (migration pendente?)" — comportamento esperado antes da migration 015.
- Contagem final de colunas confirmada via verificação do plano: `OrdemReservaLinx` = 82 colunas, `ProdutoTamanhoPosicao` = 5 colunas.
- Suíte completa: `211 passed, 4 skipped, 0 failed` (via `docker compose exec api uv run pytest -q`).
- `uv run alembic check` (dentro do container) reporta `add_table` para as 2 tabelas novas — confirma que `env.py` está correto e que a migration 015 (Plano 02) tem os 2 `create_table` pendentes para resolver.

## Next Phase Readiness
- Plano 02 (migration 015) pode prosseguir: os 2 models são a fonte de verdade coluna a coluna que a migration precisa espelhar.
- Nenhum bloqueio identificado.

---
*Phase: 01-schema-linx-e-refer-ncia-de-posi-o*
*Completed: 2026-08-04*

## Self-Check: PASSED

Todos os 4 arquivos de `files_modified` do plano e o próprio `01-01-SUMMARY.md` foram confirmados no disco (`FOUND`). Os 4 commits de task/summary (`817bac1`, `69b7dc2`, `4e3a23b`, `a12aa87`) foram confirmados em `git log --oneline --all`.
