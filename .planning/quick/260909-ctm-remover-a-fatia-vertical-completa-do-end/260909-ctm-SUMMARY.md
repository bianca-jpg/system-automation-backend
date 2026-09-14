---
phase: quick/260909-ctm
plan: 01
subsystem: api
tags: [fastapi, openapi, pedidos, dead-code, endpoints-audit]

requires: []
provides:
  - Router de pedidos com 5 rotas de leitura (era 6) — GET /ordens-reserva removida por completo, ponta a ponta
  - SqlPedidosReadRepository com 5 projecoes (era 6); helpers compartilhados (_epoch_ms, _estoque_disponivel, PaginaCursor) preservados
  - Grupo B do inventario de .planning/research/ENDPOINTS-AUDIT-2026-08-26.md com 1 dos 3 itens resolvido
affects: [pedidos, api, docs]

tech-stack:
  added: []
  patterns: []

key-files:
  created: []
  modified:
    - app/modules/pedidos/infrastructure/http/routes.py
    - app/modules/pedidos/service.py
    - app/modules/pedidos/application/consultas.py
    - app/modules/pedidos/application/schemas.py
    - app/modules/pedidos/application/ports.py
    - app/modules/pedidos/infrastructure/repositorio_consultas.py
    - app/modules/pedidos/domain/consultas.py
    - app/modules/pedidos/domain/edicao_grade.py
    - app/tests/test_pedidos_routes.py
    - app/tests/test_pedidos_read_projection.py
    - README.md
    - docs/api.md
    - .planning/codebase/ARCHITECTURE.md
  deleted:
    - app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py

key-decisions:
  - "Remocao ponta a ponta em duas camadas: Task 1 tirou HTTP/service/caso de uso/schemas mantendo o repositorio/port temporariamente mortos mas validos; Task 2 fechou port/repositorio/SQL/dominio, garantindo que nenhum estado intermediario ficasse quebrado"
  - "Helpers compartilhados (_epoch_ms, _estoque_disponivel, PaginaCursor) e a tabela ordens_reserva preservados integralmente — sao donos de outras 5 projecoes e do motor de adequacao, nao exclusivos da fatia removida"
  - "Docstring de _qt_solicitada_ou_fallback reescrita para nao citar o arquivo SQL deletado, sem tocar no corpo da funcao (decisao PD-03 da Phase 20 intacta)"

patterns-established: []

requirements-completed: [QUICK-260909-CTM]

duration: ~25min
completed: 2026-09-09
---

# Quick Task 260909-ctm: Remover a fatia vertical completa do endpoint de listagem de OR Summary

**Removida por completo a fatia vertical de `GET /api/v1/pedidos/ordens-reserva` (rota, service, caso de uso, port, repositorio, SQL, schemas, dataclass de dominio e 3 testes dedicados) por nao ter consumidor no `frontend`; OpenAPI caiu de 36 para 35 paths.**

## Performance

- **Duration:** ~25min
- **Completed:** 2026-09-09T12:32:00Z
- **Tasks:** 3/3
- **Files modified:** 13 (12 modificados + 1 deletado)

## Accomplishments

- Router de pedidos reduzido de 6 para 5 rotas de leitura; OpenAPI de 36 para 35 paths, com as 5 rotas vizinhas (`/produtos`, `/produtos/clientes`, `/lookup`, `/resumo`, `/evolucao-faturamento`) intactas
- `SqlPedidosReadRepository` com 5 projecoes (era 6); `app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py` deletado via `git rm`; nenhum simbolo da fatia (`listar_ordens_reserva`, `ConsultaOrdensReserva`, `OrdensReservaPageOut`, `OrdemReservaOut`, `ClienteDaOrdem`, `_ordens_reserva_cursor_valido`) restou em `app/**/*.py`
- Suite completa nativa fechou em **996 passed / 18 skipped / 0 failed** (baseline 999/18/0 menos os 3 testes dedicados removidos), `ruff check` limpo
- `README.md`, `docs/api.md` e `.planning/codebase/ARCHITECTURE.md` nao mencionam mais o endpoint removido; docstring de `_qt_solicitada_ou_fallback` deixou de citar o arquivo SQL inexistente, com o corpo da funcao byte-identico

## Task Commits

Each task was committed atomically:

1. **Task 1: Remover a ponta HTTP — rota, service, caso de uso e schemas** - `cec12f3` (sem prefixo)
2. **Task 2: Remover port, repositorio, SQL, dataclass de dominio e testes de projecao** - `6a73c61` (sem prefixo)
3. **Task 3: Docs coerentes com a remocao, comentario orfao e suite completa** - `84a92a9` (sem prefixo)

_Convencao deste repositorio: mensagens em pt-BR sem prefixo Conventional Commits (feat:/chore:/docs:)._

## Files Created/Modified

- `app/modules/pedidos/infrastructure/http/routes.py` - Remove o handler `GET /ordens-reserva` e `OrdensReservaPageOut` do bloco de import de schemas
- `app/modules/pedidos/service.py` - Remove `listar_ordens_reserva_page` e a entrada em `__all__`
- `app/modules/pedidos/application/consultas.py` - Remove o caso de uso `listar_ordens_reserva` e o validador privado `_ordens_reserva_cursor_valido` (orfao, nao detectado por ruff); tira `ConsultaOrdensReserva` do import
- `app/modules/pedidos/application/schemas.py` - Remove `OrdensReservaPageOut`, `OrdemReservaOut`, `ClienteDaOrdem`
- `app/modules/pedidos/application/ports.py` - Remove o membro `listar_ordens_reserva` do `Protocol PedidosReadRepository` (fica com 5 membros)
- `app/modules/pedidos/infrastructure/repositorio_consultas.py` - Remove o metodo `listar_ordens_reserva`; docstring do modulo deixa de enumerar a projecao removida; `_estoque_disponivel` preservado intacto
- `app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py` - Deletado via `git rm`; os simbolos que importava (`_epoch_ms`, `PaginaCursor`) permanecem, usados por outros consumidores
- `app/modules/pedidos/domain/consultas.py` - Remove a dataclass `ConsultaOrdensReserva`; `PaginaCursor` e as demais `Consulta*` intactas
- `app/modules/pedidos/domain/edicao_grade.py` - Reescreve so a frase do docstring de `_qt_solicitada_ou_fallback` que citava o arquivo SQL deletado; corpo da funcao e a frase sobre `0` preservado (PD-03) byte-identicos
- `app/tests/test_pedidos_routes.py` - Remove o banner de secao e `test_listar_ordens_reserva_retorna_envelope_bounded` (fim do arquivo)
- `app/tests/test_pedidos_read_projection.py` - Remove `test_ordens_reserva_pagina_pares_e_nao_mistura_canais_do_mesmo_produto` e `test_ordens_reserva_cursor_ignora_or_sem_item_elegivel`; `_item_or`, o mock de `_estoque_disponivel` e os vizinhos preservados
- `README.md` - Remove a linha da tabela **Pedidos** do endpoint removido
- `docs/api.md` - Remove a linha da tabela de rotas e a subsecao `### Ordens de reserva bounded` inteira
- `.planning/codebase/ARCHITECTURE.md` - Tira o path removido da enumeracao de rotas de `pedidos/routes.py`, registrando a remocao no parenteses ja existente na linha

## Decisions Made

- Remocao em duas camadas (Task 1 topo, Task 2 base) para manter cada commit verde e importavel independentemente, seguindo a ordem consumidor -> produtor especificada no plano
- Nenhuma refatoracao ou teste novo adicionado — escopo estritamente de remocao, conforme `success_criteria` do plano

## Deviations from Plan

None - plan executado exatamente como escrito, incluindo os numeros de gate (35 paths, 996 passed, 38/36 passed nos testes intermediarios das Tasks 1 e 2).

Uma unica ajustagem de redacao (nao um desvio de codigo): ao redigir a nota parentetica em `.planning/codebase/ARCHITECTURE.md` sobre a rota removida, a primeira formulacao repetia o literal `ordens-reserva`, o que teria falhado o proprio grep de verificacao do Task 3 (`! grep -rq "ordens-reserva" README.md docs/ .planning/codebase/`). Reescrita para "a rota de listagem de OR bounded" sem citar o path, satisfazendo o gate.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Modulo `pedidos` com uma projecao morta a menos para as Phases 13/17/18/19 do v1.3 lerem
- `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` Grupo B fica com 1 dos 3 itens resolvido; `POST /{nr_pedido}/aprovar` e as comunicacoes por id (os outros dois itens do Grupo B) seguem em aberto, fora do escopo desta task
- Nada pendente de merge/push: 3 commits locais na branch `develop`, nenhum `git push` executado

---
*Phase: quick/260909-ctm*
*Completed: 2026-09-09*

## Self-Check: PASSED

Os 3 commits (`cec12f3`, `6a73c61`, `84a92a9`) confirmados em `git log`. Arquivos citados
(`routes.py`, `edicao_grade.py`, este SUMMARY.md) confirmados em disco. O arquivo
`app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py` confirmado deletado.
