---
quick_id: 260825-eii
status: planned
---

# Quick Task 260825-eii: Reordenar quadros da tela de gestão de usuários

## Objetivo

Em `frontend/app/(app)/usuarios/page.tsx`, reordenar os três blocos exibidos na tela `/usuarios` sem alterar estilo, props ou lógica interna de cada componente — só a ordem em que aparecem no JSX.

Ordem atual: (1) Solicitações pendentes de parâmetros, (2) Usuários e permissões, (3) Histórico de decisões de parâmetro.

Ordem desejada: (1) Solicitações pendentes de parâmetros, (2) Histórico de decisões de parâmetro, (3) Usuários e permissões.

## Tarefas

1. **files:** `frontend/app/(app)/usuarios/page.tsx`
   **action:** Mover o bloco `<UserManagementTable rows={users.rows...} title="Usuários e permissões" .../>` (linhas ~249-266) para depois do bloco `<ParameterHistoryTable .../>` (linhas ~279-291), mantendo `<ParameterRequestModal .../>` e a mensagem `{actionError && ...}` nas suas posições relativas atuais (o modal e o erro dependem do handler de troca de role, que passa a vir associado ao bloco de usuários já movido).
   **verify:** Rodar `pnpm typecheck` e `pnpm lint` no frontend; conferir visualmente que a ordem renderizada é: pendentes → histórico → usuários.
   **done:** JSX reordenado, sem mudança de props/estilo, typecheck e lint passando.
