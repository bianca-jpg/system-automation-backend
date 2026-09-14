---
quick_id: 260825-eii
status: complete
---

# Quick Task 260825-eii: Reordenar quadros da tela de gestão de usuários

## O que foi feito

Em `frontend/app/(app)/usuarios/page.tsx`, reordenados os três blocos da tela `/usuarios` (só ordem de renderização, sem tocar em props/estilo/lógica):

1. Solicitações pendentes de parâmetros (mantido em primeiro)
2. Histórico de decisões de parâmetro (movido para o meio)
3. Usuários e permissões (movido para o final, junto com o modal de decisão e a mensagem de erro de troca de papel, que dependem dele)

## Verificação

- `pnpm typecheck` — passou sem erros
- `pnpm lint` — passou sem erros
