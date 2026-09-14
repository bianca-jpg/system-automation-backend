---
quick_id: 260818-gaz
slug: paginacao-numerada-nas-listas-de-pedidos
date: 2026-08-18
status: complete
commits:
  - repo: backend
    sha: ea00db2
    subject: "feat(pedidos): aceita paginacao numerada em GET /pedidos/produtos"
  - repo: frontend
    sha: a5b2a01
    subject: 'feat(pedidos): troca "carregar mais" por paginacao numerada nas listas'
---

# Quick Task 260818-gaz — Resumo

As três listas por produto (aguardando faturamento, em edição, histórico) saíram
do rodapé "Carregar mais 25" para a faixa numerada
`← Anterior 1 2 3 … 42 Próxima →`.

## O que mudou e por quê

O pedido era "só frontend", mas não dava: `GET /api/v1/pedidos/produtos` era
keyset puro (`WHERE sort > :cursor ... LIMIT`), e cursor não sabe saltar para uma
página arbitrária. Foram dois commits, um por repositório.

**Backend** — `?page=N` como modo alternativo ao `cursor`, mutuamente exclusivos
(mandar os dois é 422, em vez de escolher um em silêncio e esconder bug de
chamador). No modo numerado a resposta ganha `page` e `totalPages`; no modo
cursor os dois saem `null`, então nada dos consumidores keyset atuais mudou. O
`ORDER BY` com desempate `code ASC, channel ASC` é idêntico nos dois modos —
sem tie-break estável a página 2 por offset poderia repetir ou pular linha em
relação ao keyset, e o teste novo compara as três páginas das duas paginações
para travar isso.

**Frontend** — `PaginationControls` entrou **como está**, sem uma linha de CSS
nova: é o mesmo composite que usuários e parâmetros já usam, construído sobre o
`Button` do design system. `usePagedResource` (novo) é o irmão de
`useCursorResource` e mantém de propósito o mesmo contrato de retorno, trocando
`loadMore`/`hasMore` por `page`/`totalPages`/`setPage` — foi isso que permitiu
não tocar em `useRealtimeResourceRefresh` nem no efeito de deadline da janela de
24h.

O ganho real de carga não é a faixa em si: é que a página **substitui** as
linhas. O cursor acumulava (`mergeUnique`), então a página 42 mantinha 1050
produtos em memória e no DOM. Agora são 25 fixos.

## Dois efeitos colaterais que a troca obrigou a tratar

1. **Previsão de Faturamento** soma as linhas em tela — o backend não expõe esse
   agregado por canal. Com o acumulado a ressalva era "· lote carregado"; virou
   "· página atual". Sem isso o número passaria por total do canal.
2. **Esqueleto de carregamento** reservava o rodapé de "carregar mais" (~90px,
   contagem e botão empilhados no centro). Passou a reservar a faixa numerada
   (~40px, encostada à direita) — reservar a forma errada faz a tela saltar
   quando os dados chegam. O teste que travava o rodapé antigo foi invertido.

## Fora de escopo (segue em cursor)

Alertas, comunicações e o modal de clientes do produto. `LoadMoreFooter`,
`useLoadMore` e `useCursorResource` continuam em uso e não foram removidos. O
caminho de fixtures do `OrdersList` (prop `orders`) também segue no "carregar
mais" — os testes que injetam array completo dependem dele.

## Verificação

| Comando | Resultado |
|---|---|
| `pytest app/tests/test_pedidos_routes.py app/tests/test_pedidos_read_projection.py` | 34 passed, 1 failed |
| `pnpm typecheck` | limpo |
| `pnpm lint` | limpo |
| `pnpm vitest run widgets/pedido-dashboard features/pedidos` | 198 passed |
| `pnpm vitest run widgets/.../orders-list.pagination.test.tsx` ×3 | 10 passed nas 3 |

**A falha de backend é pré-existente**: `test_read_projection_empty_contracts_execute_real_sql`
(`RuntimeError: Event loop is closed`, teardown de asyncio no Windows). Confirmada
falhando idêntica no baseline com `git stash`.

A suíte **completa** do frontend (`pnpm vitest run`, 500 testes) é instável por
conta própria — o baseline falha 5 testes, com mix variando entre execuções, e
os mesmos testes de chave idempotente falham com e sem esta mudança. Os arquivos
tocados passam de forma estável quando rodados por escopo.

## Pendência conhecida

Nada bloqueia esta tarefa, mas vale registrar: a **Previsão de Faturamento**
continua sendo uma soma parcial (agora da página) porque não existe endpoint que
devolva o total faturável do canal. A ressalva no rótulo é honesta, mas a
solução de verdade é um agregado no backend.
