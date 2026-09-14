---
phase: 03-escrita-da-or-no-formato-linx
plan: 02
subsystem: database
tags: [sqlalchemy, postgres, upsert, batch-read, cross-context]

# Dependency graph
requires:
  - phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
    provides: "produto_tamanho_posicao ingerida (569.726 linhas reais) e converter_grade_para_posicoes"
  - phase: 01-schema-linx-e-refer-ncia-de-posi-o
    provides: "model OrdemReservaLinx com UniqueConstraint (nr_pedido, cd_prod_cor)"
provides:
  - "obter_referencia_posicoes_por_produtos: leitura em lote da referência tamanho->posição, filtrada por produtos da rodada, em chunks de 500"
  - "carregar_referencia_posicoes: re-export cross-context para o módulo pedidos"
  - "salvar_linhas_linx: upsert de ordens_reserva_linx por (nr_pedido, cd_prod_cor), sem commit"
affects: ["03-03 (wiring nos fluxos de geração)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Leitura em lote sempre filtrada (WHERE cd_prod_cor IN (...)), nunca select(Model) sem where, em fatias de 500 para respeitar o limite de parâmetros do protocolo Postgres"
    - "Upsert por SELECT + scalar_one_or_none() + decide, nunca db.get, quando a PK real difere da chave natural (UniqueConstraint)"

key-files:
  created:
    - app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
  modified:
    - app/modules/ingestao/api_leitura.py
    - app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py
    - app/tests/test_ingestao_api_leitura.py
    - app/tests/test_pedidos_routes.py

key-decisions:
  - "obter_referencia_posicoes_por_produtos devolve {} silenciosamente para cd_prod_cor sem posição (sem KeyError) — quem decide o que fazer com a ausência é o chamador (D-01, Plano 03-03)"
  - "salvar_linhas_linx nunca usa db.get: SELECT + scalar_one_or_none() porque a PK de OrdemReservaLinx (id, autoincrement) é diferente da chave natural (nr_pedido, cd_prod_cor)"

patterns-established:
  - "Leitura cross-context em lote filtrada por produtos da rodada, chunk de 500 parâmetros"
  - "Upsert SELECT+decide quando PK != chave natural (UniqueConstraint)"

requirements-completed: [LINX-02]

# Metrics
duration: 6min
completed: 2026-08-05
---

# Phase 3 Plan 02: Leitura em lote da referência de posição + upsert de ordens_reserva_linx Summary

**`obter_referencia_posicoes_por_produtos` (chunk 500, nunca a tabela inteira) e `salvar_linhas_linx` (upsert SELECT+decide, sem `db.get`) — as 2 peças de infraestrutura que o Plano 03-03 vai conectar aos fluxos de geração.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-08-05T15:38:17-03:00 (Task 1 commit)
- **Completed:** 2026-08-05T15:42:56-03:00 (Task 2 commit)
- **Tasks:** 2/2
- **Files modified:** 4 modificados + 1 criado

## Accomplishments
- Leitura em lote da referência tamanho->posição, sempre filtrada por `cd_prod_cor IN (...)` em fatias de 500, provada por 3 testes com dados reais já ingeridos na Fase 2
- Upsert de `ordens_reserva_linx` por `(nr_pedido, cd_prod_cor)` sem `db.get`, provado por teste que grava o mesmo par duas vezes e confirma 1 linha só com os valores da 2ª gravação (critério 4 do ROADMAP)
- Suíte completa verde (279 passed) após as duas tasks, sem regressão

## Task Commits

Each task was committed atomically:

1. **Task 1: obter_referencia_posicoes_por_produtos (batch, chunk 500) + re-export** - `59ff877` (feat)
2. **Task 2: salvar_linhas_linx (upsert SELECT+decide) — critério 4** - `b645199` (feat)

_Nota: sem commit de metadata separado (plano orquestrado — STATE/ROADMAP atualizados pelo orquestrador ao fim da wave, conforme instrução do executor)._

## Files Created/Modified
- `app/modules/ingestao/api_leitura.py` - `obter_referencia_posicoes_por_produtos` (nova), `ProdutoTamanhoPosicao` no import, constante `_TAMANHO_CHUNK = 500`
- `app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py` - re-export `carregar_referencia_posicoes`
- `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` (novo) - `salvar_linhas_linx` (upsert, sem commit)
- `app/tests/test_ingestao_api_leitura.py` - 3 testes de `obter_referencia_posicoes_por_produtos`
- `app/tests/test_pedidos_routes.py` - 2 testes de `salvar_linhas_linx`, aditivos ao final da seção "salvar_ordens_reserva direto na camada de serviço" (arquivo compartilhado com o Plano 03-03, que editará depois)

## Assinaturas finais (referência para o Plano 03-03)

```python
# app/modules/ingestao/api_leitura.py
async def obter_referencia_posicoes_por_produtos(
    db: AsyncSession, cd_prod_cors: set[str]
) -> dict[str, dict[str, int]]: ...

# app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py
# re-export com alias:
from app.modules.pedidos.infrastructure.repositorio_ingestao_readmodel import (
    carregar_referencia_posicoes,  # = obter_referencia_posicoes_por_produtos
)

# app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
async def salvar_linhas_linx(db: AsyncSession, linhas: list[dict]) -> None: ...
```

## Decisions Made
Nenhuma decisão nova além das já travadas em 03-CONTEXT.md (D-01, D-02a) e 03-PATTERNS.md. Implementação seguiu os análogos indicados (`aprovar_ordem_reserva` para o upsert, funções existentes de `api_leitura.py` para a leitura em lote).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `carregar_referencia_posicoes` e `salvar_linhas_linx` prontos para import direto em `casos_uso.py` pelo Plano 03-03
- `app/tests/test_pedidos_routes.py` está com as 2 novas funções de teste no final da seção de upsert — próximo executor (03-03) deve seguir editando de forma aditiva a partir daqui
- Nenhum bloqueio conhecido

## Self-Check: PASSED
- FOUND: app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
- FOUND: def obter_referencia_posicoes_por_produtos (app/modules/ingestao/api_leitura.py)
- FOUND: carregar_referencia_posicoes (app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py)
- FOUND: commit 59ff877
- FOUND: commit b645199

---
*Phase: 03-escrita-da-or-no-formato-linx*
*Completed: 2026-08-05*
