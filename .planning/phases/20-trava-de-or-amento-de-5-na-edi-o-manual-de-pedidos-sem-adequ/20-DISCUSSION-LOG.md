# Phase 20: Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-04
**Phase:** 20-Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação
**Areas discussed:** Status da trava de 5% hoje, Escopo da mudança (o que a edição passa a permitir), Como contar o gasto do 5%, Comportamento ao reabrir pedido no limite, Onde exibir o orçamento, Salvar = Aprovar junto

> Nota de processo: esta discussão aconteceu em conversação livre com a mantenedora, incluindo levantamento de código e 3 passadas de revisão do plano, **antes** da fase ser formalmente criada no ROADMAP. O log abaixo reconstrói as perguntas e respostas efetivas dessa conversa.

---

## Status da trava de 5% hoje (levantamento inicial)

Levantamento (não uma pergunta de múltipla escolha): confirmado que `OrcamentoPedido` existe e é usado apenas pelo motor automático (`motor_adequacao.py`); a edição manual (`executar_alteracao_grades_produto`) nunca o consulta; o modal (`product-grade-detail-modal.tsx`) bloqueia qualquer mudança de total via `hasInvalidClientTotals`.

**Conclusão da mantenedora:** confirmado como gap real — "deveria ter uma trava de 5% na hora de edição de grade".

---

## O que a edição passa a permitir

| Opção | Descrição | Selecionada |
| --- | --- | --- |
| Sim, a edição vai poder mudar o total | Modal passa a aceitar aumentar/diminuir o total, respeitando o que sobrar dos 5% | |
| Não, o total continua travado | Edição continua só redistribuindo tamanho; trava ficaria em outro lugar | |

**Resposta da mantenedora (livre, mais precisa que as opções oferecidas):** "O total vai continuar igual se caso ele escolheu a opção com adequação, aí o total tem que continuar o mesmo. Se caso ele escolheu sem adequação ele pode modificar até 5%."

**Notas:** Isso corrigiu a pergunta binária original — a resposta certa depende do **tipo de OR** (com/sem adequação), não é uma escolha única para toda a edição. Virou D-01/D-02 em CONTEXT.md.

---

## Como contar o gasto do 5%

| Opção | Descrição | Selecionada |
| --- | --- | --- |
| Sim, soma o que o robô já gastou + o que a edição gasta | Um orçamento único de 5%, motor e edição manual descontam do mesmo total | |
| Não, edição manual tem orçamento próprio separado | Dois orçamentos de 5% independentes | |

**Resposta da mantenedora:** "quando der as 19h esses pedidos vão ser considerados que não vão ter adequação e aí na janela de edição poderá editar até 5%."

**Notas:** Revelou um fato do domínio que nenhuma das duas opções capturava bem: para "sem adequação" o motor **nunca gasta nada** do orçamento (confirmado depois no código: `SEM_ADEQUAR` não toca o ledger) — logo não há de fato "soma" a fazer nesse caminho, o orçamento da edição manual já é o orçamento inteiro do pedido. Virou D-06 em CONTEXT.md.

---

## Comportamento ao reabrir pedido no limite

| Opção | Descrição | Selecionada |
| --- | --- | --- |
| Abrir travada com aviso | Campos desabilitados, aviso visível, dados continuam visíveis | |
| Nem deixar abrir a edição | Botão de editar nem fica clicável | |

**Resposta da mantenedora:** "Ela editou uma vez ela vira OR automaticamente. Por isso já na hora de editar a grade, na parte de salvar as mudanças pode colocar um aviso de que vai ser gerado OR automaticamente."

**Notas:** A pergunta partia de uma premissa técnica errada (achava que dava para reabrir e editar de novo o mesmo pedido depois de bater o limite). Investigação subsequente (passada de revisão 1) mostrou que a OR **já existe antes** da edição manual (criada em lote pelo botão "Efetuar OR sem adequação" — não existe endpoint de OR por produto isolado, comentário confirmado em `orders-list.tsx:773`). A resposta da mantenedora, relida à luz disso, não era sobre "reabrir" — era sobre unificar salvar+aprovar num clique. Pergunta de acompanhamento (abaixo) resolveu a ambiguidade.

---

## Onde exibir o orçamento

| Opção | Descrição | Selecionada |
| --- | --- | --- |
| Sim, mostrar sempre o total do pedido | Mesmo editando um produto isolado, mostra o orçamento agregado do pedido inteiro | ✓ |
| Decidir depois | Só garantir que o backend recuse; exibição fica para depois | |

**User's choice:** Sim, mostrar sempre o total do pedido.

**Notas:** Virou D-12 em CONTEXT.md.

---

## Salvar = Aprovar junto?

| Opção | Descrição | Selecionada |
| --- | --- | --- |
| Sim, unificar num clique só | Para "sem adequação" dentro do limite, salvar já aprova/finaliza a OR | ✓ |
| Não, continuam dois passos | Salvar só grava; aprovar continua separado | |

**User's choice:** Sim, unificar num clique só.

**Notas:** Resolveu a ambiguidade deixada pela pergunta anterior. Virou D-09 em CONTEXT.md.

---

## Correções encontradas nas 3 passadas de revisão do plano (antes desta fase)

1. **Passada 1:** premissa errada de que "editar gera OR automaticamente" — corrigida (a OR já existe; editar só a altera dentro da janela de 24h).
2. **Passada 2:** o "consumido prévio" não é sempre 0 para reedições do mesmo pedido — é sempre 0 só na primeira edição de um pedido "sem adequação"; edições subsequentes devem ler o estado ao vivo via `_PEDIDO_BUDGET_SQL`. Confirmação positiva: o campo `tipo` da OR já existe e pode ser lido diretamente.
3. **Passada 3:** faltava proteção contra corrida entre edições concorrentes de produtos diferentes do mesmo pedido — adicionada como exigência de lock atômico (D-07).

## Claude's Discretion

- Texto exato do aviso/rótulo do botão unificado.
- Desenho exato do payload do endpoint de leitura do orçamento.
- Como unificar tecnicamente `executar_alteracao_grades_produto` com `aprovar_produto_canal`/`aprovar_ordem_reserva`.

## Deferred Ideas

Nenhuma — discussão ficou inteiramente dentro do escopo desta fase.
