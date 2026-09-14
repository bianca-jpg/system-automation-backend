---
quick_id: 260831-cf1
status: complete
---

# Quick Task 260831-cf1 — SUMMARY

Fechado `CONTRACT-01` (front não consumia `GET /api/v1/parametros`) no repo
`frontend`. **Este quick task documenta uma mudança feita no
repositório do frontend** — o `.planning/` mora só no backend, mesmo padrão
já usado em 260827-fqf.

## O que mudou (`frontend`)

- `features/pedidos/model/use-tolerancia-adequacao.ts` (novo):
  `useToleranciaAdequacaoFator()` lê `tolerancia_adequacao` via
  `fetchParametros` (já existente) e devolve `1 + tolerancia`, com fallback
  `1.05` (mesmo default do backend) enquanto carrega/em erro.
- `widgets/pedido-dashboard/ui/orders-list.tsx`: os dois `* 1.05` hardcoded
  (projeção de faturamento "com adequação" da fila Aguardando) viram
  `* fatorAdequacao`.
- 4 arquivos de teste que renderizam `OrdersList` (`orders-list.test.tsx`,
  `.pagination.test.tsx`, `.processing.test.tsx`,
  `orders-list-skeleton.test.tsx`) ganharam
  `vi.mock('@/features/pedidos/model/use-tolerancia-adequacao', ...)`
  retornando `1.05`, para preservar as expectativas numéricas já existentes
  sem precisar de `QueryClientProvider` nesses testes (nenhum deles usava
  react-query antes).

## Achado que já não procede

`order-detail-modal.tsx` (citado no achado original de 2026-08-14 junto com
`orders-list.tsx`) não existe mais na árvore principal do front — só
sobrevive numa worktree órfã (`.claude/worktrees/fervent-goodall-a7cbaa/`).
Foi substituído por `features/pedidos/ui/modals/product-grade-detail-modal.tsx`,
que não tem o hardcode. Nada a corrigir ali.

## Testes

- `use-tolerancia-adequacao.test.tsx` (novo, 4 casos): fallback pré-fetch,
  valor real (0.1 → 1.1), fallback sem a chave na resposta, fallback em erro
  de rede.
- `pnpm typecheck`: limpo.
- `pnpm test` (suíte completa, 59 arquivos / 494 testes): **492 passed, 2
  failed** — as mesmas 2 falhas existem na baseline (confirmado via
  `git stash` escopado a `orders-list.tsx` +
  `orders-list.pagination.test.tsx`, rodando os testes contra o HEAD anterior
  antes de restaurar): timeouts em
  `orders-list.pagination.test.tsx` (paginação "histórico" e reenvio de
  comunicação), pré-existentes e não relacionados a esta mudança.

## Commit

`85d1d25` (repo `frontend`) —
`fix(pedidos): consome tolerancia_adequacao real em vez do fator 1.05 hardcoded`
