---
quick_id: 260818-gaz
slug: paginacao-numerada-nas-listas-de-pedidos
date: 2026-08-18
mode: quick
repos:
  - backend
  - frontend
---

# Quick Task 260818-gaz: paginação numerada nas listas de pedidos

Trocar o rodapé "Carregar mais 25" das três listas de pedidos (aguardando
faturamento, em edição, histórico) pela paginação numerada
`← Anterior 1 2 3 … 42 Próxima →`, sem sair do design system.

## Por que a mudança não é só de frontend

`GET /api/v1/pedidos/produtos` é **keyset/cursor**: o SQL em
`repositorio_produtos.py::listar_produtos_sql` filtra por
`WHERE sort > :cursor_value ... LIMIT :limit_plus_one`. Cursor sabe responder
"o que vem depois desta linha", não "pule para a página 42". Números clicáveis
exigem `OFFSET` no backend.

O motivo real da tarefa (sobrecarga) vem de `useCursorResource`, que **acumula**
linhas via `mergeUnique` — a página 42 mantém 1050 linhas em memória e no DOM.
Paginação por página troca isso por 25 linhas fixas.

## O que já existe e NÃO deve ser reconstruído

| Peça | Onde | Papel |
|---|---|---|
| `PaginationControls` | `shared/ui/composite/PaginationControls.tsx` | Já é exatamente o desenho pedido (Anterior · números · elipse · Próxima), construído sobre o `Button` do DS. Usado por usuários e parâmetros. |
| `NumberedPage` / `parseNumberedPage` | `shared/types/numbered-page.ts` | Contrato do envelope `{rows,total,page,pageSize,totalPages}` |
| `skeletons.tsx` (`showPagination`) | `shared/ui/composite/skeletons.tsx` | Já reserva a faixa da paginação |

Nada de CSS novo de paginação. O `PaginationControls` entra como está.

## Tarefas

### Tarefa 1 — Backend: modo offset em `GET /api/v1/pedidos/produtos`

**Arquivos:**
- `app/modules/pedidos/domain/consultas.py`
- `app/modules/pedidos/infrastructure/repositorio_produtos.py`
- `app/modules/pedidos/application/consultas.py`
- `app/modules/pedidos/application/schemas.py`
- `app/modules/pedidos/service.py`
- `app/modules/pedidos/infrastructure/http/routes.py`
- `app/tests/test_pedidos_routes.py`

**Ação:**
1. `ConsultaProdutos`: campo `offset: int | None = None`.
2. `listar_produtos_sql`: quando `offset is not None`, o CTE `page` usa
   `WHERE true ... OFFSET :offset LIMIT :limit_plus_one`. `ORDER BY` e o
   desempate `code ASC, channel ASC` permanecem idênticos aos do modo cursor —
   sem tie-break estável a página 3 pode repetir/omitir linha.
3. `application/consultas.py::listar_produtos`: parâmetro `pagina: int | None`.
   Com `pagina` setada, **pula o decode do cursor** (não há cursor a validar),
   calcula `offset = (pagina - 1) * tamanho_pagina` e devolve também
   `page` e `total_pages = max(1, ceil(total / tamanho_pagina))`.
4. `ProdutosPageOut`: `page: int | None` e `total_pages: int | None`
   (`serialization_alias="totalPages"`). Opcionais: o modo cursor segue
   devolvendo `null` e nenhum consumidor atual quebra.
5. `routes.py`: `page: int | None = Query(default=None, ge=1)`. Enviar `page`
   **e** `cursor` juntos → 422 (as duas paginações são mutuamente exclusivas;
   aceitar as duas silenciosamente esconderia bug de chamador).
6. Testes: página 2 não repete linha da página 1; `page` + `cursor` → 422;
   `totalPages` bate com `ceil(total/pageSize)`; modo cursor inalterado.

**Verificar:** `./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_routes.py`
(NÃO usar `uv run` — o caminho com "Área" quebra o trampoline).

**Pronto quando:** `?page=2&pageSize=25` devolve a segunda fatia com
`page`/`totalPages`, e as chamadas com `cursor` continuam idênticas.

### Tarefa 2 — Frontend: `usePagedResource` + API por página

**Arquivos:**
- `features/pedidos/api/pedidos.api.ts`
- `features/pedidos/model/use-paged-resource.ts` (novo)
- `features/pedidos/model/use-pedidos-queues.ts`

**Ação:**
1. `fetchProductNumberedPage(...)`: mesma query de `fetchProductPage` trocando
   `cursor` por `page`, validando com `parseNumberedPage`, e mantendo o cálculo
   de `remainingWindowDeadlineAt` (o front ancora o deadline no instante da
   resposta — perder isso quebra o timer da janela de 24h).
2. `use-paged-resource.ts`: espelha o **contrato de retorno** de
   `useCursorResource` (`reload(): Promise<boolean>`, `loadedOnce`,
   `refreshing`, `error`, `rows`, `total`, `setRows`) trocando
   `loadMore`/`hasMore` por `page`/`totalPages`/`setPage`. Manter o contrato é o
   que permite não tocar em `useRealtimeResourceRefresh` nem no efeito de
   deadline de `usePedidosQueues`.
   - Guarda de request id + `AbortController` como no original.
   - Trocar busca/ordenação/canal volta para página 1.
   - Se a página pedida passou a não existir (deleção concorrente), voltar para
     `totalPages` em vez de mostrar tabela vazia.
3. `usePedidosQueues`: `useCursorResource` → `usePagedResource` nas duas filas.

**Verificar:** `pnpm typecheck`

**Pronto quando:** as filas expõem `page`/`totalPages`/`setPage` e o efeito de
deadline e o refresh por realtime continuam compilando sem alteração.

### Tarefa 3 — Frontend: trocar o rodapé nas três listas

**Arquivos:**
- `widgets/pedido-dashboard/ui/orders-list.tsx`
- `widgets/pedido-dashboard/ui/orders-list.pagination.test.tsx`

**Ação:**
1. Trocar os três `LoadMoreFooter` por `PaginationControls`
   (`noun={['produto','produtos']}`).
2. Histórico: `useCursorResource` → `usePagedResource`.
3. `janelaAguardando`/`janelaEmEdicao`: no caminho servidor, `visiveis` passa a
   ser a página corrente; o caminho de fixtures (`useLoadMore`) permanece — os
   testes que injetam `orders` dependem dele.
4. Não remover `LoadMoreFooter`, `useLoadMore` nem `useCursorResource`: alertas,
   comunicações e o modal de clientes do produto continuam usando cursor. Esta
   tarefa é só a lista de produtos.
5. Atualizar os testes de paginação para o novo rodapé.

**Verificar:** `pnpm vitest run widgets/pedido-dashboard` e `pnpm typecheck`

**Pronto quando:** as três listas mostram
`Mostrando 26–50 de 1043 produtos` + `Anterior 1 2 3 … 42 Próxima`, e clicar
num número troca a página sem acumular linhas.

## Fora de escopo

- Alertas, comunicações e o modal de clientes do produto (seguem em cursor).
- `PaginationControls` não é alterado — se precisar mudar, é sinal de que a
  tarefa cresceu.
