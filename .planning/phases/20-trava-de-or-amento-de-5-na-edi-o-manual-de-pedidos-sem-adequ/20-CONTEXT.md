# Phase 20: Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação - Context

**Gathered:** 2026-09-04
**Status:** Ready for planning

<domain>
## Phase Boundary

Hoje o orçamento de ±5% (`OrcamentoPedido`) só é aplicado pelo motor automático de adequação. A edição manual de grade (janela de 24h, `stage: 'edicao'`) nunca o consulta, e o modal atual (`product-grade-detail-modal.tsx`) proíbe qualquer mudança de total do cliente — só permite redistribuir tamanhos.

Esta fase entrega: para pedidos processados via **"Efetuar OR sem adequação"** (`ModoAdequacao.SEM_ADEQUAR`, que hoje não toca o ledger), a edição manual passa a poder alterar o **total do pedido inteiro** (não só redistribuir), respeitando o orçamento de ±5% do `nr_pedido` — com trava e aviso quando o limite é atingido, e com salvar+aprovar unificados num único clique quando a edição está dentro do limite.

Pedidos **"Com Adequação"** (motor já aplicou o ±5% automaticamente) **não mudam nesta fase** — a edição continua redistribuição-só, como hoje.

</domain>

<decisions>
## Implementation Decisions

### Escopo por tipo de OR

- **D-01:** Pedidos "Com Adequação" — zero mudança de comportamento (tela e endpoint intocados).
- **D-02:** Pedidos "Sem Adequação" — edição manual passa a permitir mudar o total do pedido, dentro de ±5% do total original do pedido inteiro (`nr_pedido`, somando todos os produtos/clientes daquele pedido — não por produto isolado).
- **D-03:** O tipo da OR ("com"/"sem" adequação) já é persistido no registro (`tipo` gravado em `processing/domain.py`) — o backend deve ler esse campo para decidir se a trava nova se aplica, não inferir por heurística.

### Cálculo do orçamento restante

- **D-04:** Reaproveitar `OrcamentoPedido` (`app/modules/pedidos/domain/orcamento_pedido.py`) — já testado (`test_pedidos_orcamento_pedido.py`, 13 casos). Não recriar a regra `floor(total_original × tolerancia)`, `consumir_adicao`/`consumir_corte`.
- **D-05:** Reaproveitar a lógica de `_PEDIDO_BUDGET_SQL` / `load_pedido_budget` (`processing/infrastructure/adapters.py:307`) — hoje só chamada em lote pelo motor; precisa virar consultável sob demanda para um `nr_pedido` específico. Essa SQL já calcula o consumido **ao vivo** (compara `qt_liquida` vs `qt_solicitada` nos itens de `ordens_reserva`), então não precisa de coluna nova no banco nem de estado persistido à parte — o restante já reflete automaticamente qualquer edição anterior já salva no mesmo pedido.
- **D-06:** Para "sem adequação", o `consumido_previo_adicao`/`consumido_previo_corte` inicial é sempre 0 (confirmado: `SEM_ADEQUAR` não toca o ledger) — mas isso só vale até a primeira edição salva; edições subsequentes do mesmo pedido devem ler o estado já gasto via D-05, não assumir 0 sempre.

### Validação e concorrência

- **D-07:** A validação do orçamento deve rodar **dentro da mesma transação/lock** que já existe em `executar_alteracao_grades_produto` (via `_adquirir_lock_processamento`, `app/modules/pedidos/application/casos_uso.py:242`) — não como um `if` solto antes de salvar. Motivo: evitar corrida entre edições concorrentes de produtos diferentes do mesmo pedido que, cada uma isoladamente, pareceria caber no orçamento mas juntas estourariam.
- **D-08:** Adição e corte são orçamentos independentes (regra já existente no `OrcamentoPedido` — não se compensam).

### Unificação salvar + aprovar

- **D-09:** Para "sem adequação" dentro do limite: o botão "Salvar Alterações" passa a também aprovar/finalizar a OR num único clique — a pessoa não precisa mais do passo separado "Aprovar OR" depois.
- **D-10:** Pontos de entrada existentes no backend para aprovação, a mapear durante pesquisa/planejamento: `aprovar_produto_canal` e `aprovar_ordem_reserva` (`app/modules/pedidos/application/casos_uso.py:100` e `:128`, delegando a `PedidosWritePort`). A unificação com o salvamento (`executar_alteracao_grades_produto`) precisa decidir se chama esses casos de uso na mesma transação ou se funde a lógica.
- **D-11:** "Com adequação" mantém os dois botões separados, sem mudança.

### Frontend

- **D-12:** Buscar e exibir o orçamento do **pedido inteiro** ao abrir a edição de qualquer produto dele — mesmo estando na tela de um produto isolado, o indicador mostra o gasto/restante agregado do `nr_pedido`.
- **D-13:** Indicador visual permanente do orçamento (gasto/restante), visível antes de qualquer edição.
- **D-14 [deferred]:** Aviso explícito antes de confirmar, avisando que salvar dentro do limite finaliza a OR (não é mais um rascunho intermediário). Implementação adiada: fica para a quick-task de frontend no `frontend` (junto com D-12/D-13/D-15/D-16), a rodar depois que os planos de backend desta fase (20-04/20-05) estiverem executados — nenhum dos 5 planos de backend desta fase cobre isso, por design.
- **D-15:** Trava visual + alerta (`role="alert"`) quando a edição pretendida ultrapassa o restante do pedido.
- **D-16:** O cálculo no frontend é só para feedback imediato (UX) — o backend (D-07) continua sendo a fonte de verdade que pode rejeitar mesmo que o front achasse que cabia.

### Claude's Discretion

- Texto exato do aviso/rótulo do botão unificado (ex.: "Salvar e Aprovar OR") — copy final é decisão de implementação, não de negócio.
- Desenho exato do payload/shape do endpoint de leitura do orçamento (novo endpoint dedicado vs. campo embutido na resposta que já alimenta o modal) — decisão do planejamento, orientada por D-04/D-05.
- Como exatamente unificar o fluxo de `executar_alteracao_grades_produto` com `aprovar_produto_canal`/`aprovar_ordem_reserva` (D-10) — requer leitura mais profunda desses casos de uso durante a pesquisa/planejamento; não é uma decisão de visão do usuário, é implementação.

</decisions>

<canonical_refs>

## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Regra de negócio existente (ledger de orçamento)

- `app/modules/pedidos/domain/orcamento_pedido.py` — `OrcamentoPedido`, a classe de domínio a reaproveitar (limite/restante/consumir de adição e corte).
- `app/tests/test_pedidos_orcamento_pedido.py` — 13 casos de teste já cobrindo a regra do ledger; não retestar a regra em si, só a nova função de agregação por pedido.
- `app/modules/pedidos/domain/motor_adequacao.py` (linha ~866 em diante) — único consumidor atual de `OrcamentoPedido`; mostra o padrão de uso (`consumido_previo_adicao_por_pedido`/`consumido_previo_corte_por_pedido`).
- `app/modules/pedidos/processing/domain.py` — `ModoAdequacao.SEM_ADEQUAR`; comentário confirmando que esse modo não toca o ledger; também onde o campo `tipo` da OR é definido ("com"/"sem").
- `app/modules/pedidos/processing/infrastructure/adapters.py` (linha ~98, `_PEDIDO_BUDGET_SQL`; linha ~307, `load_pedido_budget`) — a SQL que calcula consumido ao vivo a partir de `ordens_reserva.itens` (compara `qt_liquida` vs `qt_solicitada`); reaproveitar/expor para consulta de pedido único.
- `app/modules/pedidos/processing/application/service.py` (linha ~228-252) — onde `load_pedido_budget` é chamado hoje, em lote.

### Endpoint de edição manual (a modificar)

- `app/modules/pedidos/application/casos_uso.py:242` — `executar_alteracao_grades_produto`, o caso de uso que o modal de edição chama hoje. Contém `_adquirir_lock_processamento` (linha ~250) — o lock a reaproveitar para a validação atômica do orçamento.
- `app/modules/pedidos/application/casos_uso.py:100` — `aprovar_produto_canal`.
- `app/modules/pedidos/application/casos_uso.py:128` — `aprovar_ordem_reserva`.
- `app/modules/pedidos/service.py:230` — outra camada de `executar_alteracao_grades_produto` (verificar relação com a de `application/casos_uso.py` durante planejamento — parecem ser duas definições, checar qual é a exposta pela rota).
- `app/modules/pedidos/infrastructure/http/routes.py` — rotas HTTP; localizar o endpoint exato de edição de grade e de aprovação.

### Frontend (a modificar)

- `frontend/features/pedidos/ui/modals/product-grade-detail-modal.tsx` — modal de edição; `hasInvalidClientTotals` (linha ~338) é a trava atual que proíbe mudança de total — precisa ser relaxada especificamente para pedidos "sem adequação"; `onApprove`/`approve()` (linha ~550-560) é o fluxo atual de aprovação separada a unificar (D-09).
- `frontend/features/pedidos/model/use-tolerancia-adequacao.ts` — hook existente que já busca `tolerancia_adequacao` real via `GET /api/v1/parametros`; padrão a seguir para buscar o orçamento do pedido.
- `frontend/widgets/pedido-dashboard/ui/orders-list.tsx` (linha ~773) — comentário confirmando que não existe endpoint de OR por produto isolado (processamento é em lote) — contexto de por que a OR já existe antes da edição manual, não é criada por ela.

Repositório frontend é irmão lado a lado (`../frontend` a partir da raiz do backend) — sem `.planning/ROADMAP.md` de produto próprio; mudanças de frontend desta fase devem ser executadas lá (ver `frontend/.claude/CLAUDE.md` para convenções desse repo).

</canonical_refs>

<code_context>

## Existing Code Insights

### Reusable Assets

- `OrcamentoPedido`: ledger completo e testado — só falta ser instanciado a partir de um contexto de edição manual, não recriado.
- `_PEDIDO_BUDGET_SQL`/`load_pedido_budget`: já resolve "quanto esse pedido já gastou" ao vivo — a peça que faltava (consulta sob demanda para 1 pedido) é extração/refatoração leve, não construção do zero.
- `use-tolerancia-adequacao.ts`: padrão de hook de frontend para buscar parâmetro real, a replicar para o orçamento do pedido.

### Established Patterns

- Lock de escrita: `_adquirir_lock_processamento` já é o padrão usado em `executar_alteracao_grades_produto` para seções críticas — a validação de orçamento deve entrar dentro dessa mesma seção.
- Erros de negócio no domínio de pedidos usam exceções dedicadas (`ProdutoBatchConflitoError`, `ProcessamentoEmAndamentoError`) — seguir o mesmo padrão para o erro de orçamento excedido.
- Frontend usa React Query (`useQuery`) com `staleTime` para dados de parâmetro que mudam raramente — mesmo padrão esperado para o orçamento do pedido (embora este mude a cada edição, então `staleTime` deve ser baixo ou invalidado ativamente após salvar).

### Integration Points

- `executar_alteracao_grades_produto` é o ponto único de entrada para a nova validação de orçamento no backend.
- `product-grade-detail-modal.tsx` é o ponto único de entrada para a nova UI de orçamento/trava no frontend — `hasInvalidClientTotals` é onde a exceção para "sem adequação" precisa ser introduzida.

</code_context>

<specifics>
## Specific Ideas

Discussão completa aconteceu em conversação livre com a mantenedora antes da criação formal desta fase, incluindo 3 passadas de revisão do plano contra o código (identificaram e corrigiram: premissa errada de que editar gera OR — a OR já existe antes da edição; necessidade de lock atômico contra corrida entre edições concorrentes do mesmo pedido; confirmação de que o campo `tipo` da OR já existe e pode ser lido em vez de inferido).

</specifics>

<deferred>
## Deferred Ideas

Nenhuma — a discussão ficou inteiramente dentro do escopo da fase (trava de orçamento na edição manual de "sem adequação"). Nenhum pedido de capacidade nova fora desse escopo surgiu durante a conversa.

### Reviewed Todos (not folded)

None — discussion stayed within phase scope.

</deferred>

---

_Phase: 20-Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação_
_Context gathered: 2026-09-04_
