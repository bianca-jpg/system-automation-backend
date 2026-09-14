# Phase 14: Motor de alocação puro (regras de negócio) - Context

**Gathered:** 2026-08-14
**Status:** Ready for planning
**Source:** Decisões tomadas pela mantenedora na sessão de 2026-08-14 (investigação do código + ditado das regras de negócio + 8 decisões via questionário)

<domain>
## Phase Boundary

Esta fase reescreve o **núcleo de decisão** do motor de adequação em `app/modules/pedidos/domain/`, e **somente ali**: funções puras, sem I/O, testáveis sem banco. A orquestração durável (`app/modules/pedidos/processing/`), o roteamento de modos e qualquer migration ficam **fora** desta fase.

**Entra:** política de quantidade por modo (tudo-ou-nada vs adequação), ledger de orçamento ±5% por pedido, verificação de furo de grade, prioridade determinística, rateio financeiro sem drift.

**Não entra:**
- Rotear `SEM_ADEQUAR` pelo caminho global e remover o `StreamingPlanBuilder` → **Phase 15**
- Guarda contra OR zerada + troca de `ceil` por `floor` → **Phase 13** (paralela; ver nota de coordenação abaixo)
- Distribuição das peças extras nos tamanhos com mais sobra → **Phase 18**
- Persistência do motivo de stand by e qualquer tabela nova → **Phase 16**
- Qualquer alteração de frontend → **Phase 17**

**Contrato a preservar (inegociável):** as chaves de retorno de `processar_pedidos` — `resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados` — não mudam de forma. `build_processing_plan` e a suíte existente dependem delas.

**Fronteira de dados a respeitar (ALOC-09):** o orçamento acumulado entre execuções exige ler ORs anteriores, o que é I/O e **não pertence a esta fase**. O domínio deve **receber** o total original do pedido e o consumo já debitado como **parâmetros de entrada** e provar o comportamento com testes. Ligar a fonte real desses dados é trabalho da Phase 15, que já toca a camada de aplicação/infra. Se o planejador concluir que isso deixa ALOC-09 sem prova de ponta a ponta nesta fase, deve dizer isso explicitamente em vez de puxar SQL para dentro de `domain/`.

</domain>

<decisions>
## Implementation Decisions

Todas travadas pela mantenedora em 2026-08-14. Não reabrir sem consultá-la.

### Crédito (ALOC-01)
- Cliente sem crédito **nunca** entra na OR, em nenhum dos dois modos. O pedido dele continua em pedidos em aberto até o status mudar.
- Crédito é do **cliente**, não do par: um pedido bloqueado fica bloqueado em todos os seus produtos.
- Pedido bloqueado **não consome estoque** — a peça fica disponível para o próximo da fila.

### Modo sem adequação (ALOC-02, ALOC-03)
- **Tudo-ou-nada por par** cliente×produto: reserva a grade exatamente como pedida, em todos os tamanhos, ou o par inteiro fica em stand by. Nunca quantidade diferente da pedida.
- Os **demais produtos do mesmo pedido** seguem elegíveis normalmente — a rejeição é por par, não por pedido.
- Na disputa pelo mesmo produto, consome o estoque na **ordem de prioridade** e quem não couber fica em stand by.

### Furo de grade (ALOC-04)
- Vale nos **dois modos**.
- Considera **apenas os tamanhos que aquele cliente pediu** naquele produto. Tamanho não pedido não conta.
- Regra: ordenados por `get_tamanho_idx`, se algum tamanho **estritamente entre** o menor e o maior pedido tiver **0 reservável**, o par fica em stand by. Ter pelo menos 1 em cada tamanho do meio libera a reserva.
- Depende do estoque **no momento da alocação** (que é decrementado conforme os prioritários consomem), logo roda **dentro** do loop, não antes.
- Casos de borda decididos: 1 só tamanho → íntegra; 2 tamanhos adjacentes → íntegra; cliente pediu PP e GG sem pedir M → íntegra (M não foi pedido); tamanho desconhecido (`get_tamanho_idx` → 999) → descartado da ordenação, nunca define extremo nem conta como interior.

### Orçamento ±5% (ALOC-07, ALOC-08, ALOC-09, ALOC-10)
- Base = quantidade total do **pedido completo** do cliente (todos os produtos), **não** a grade de cada produto isolado.
- **Dois orçamentos separados**, de 5% cada: adições e cortes **não se compensam**. Decisão consciente da mantenedora — a pesquisa apontou que o padrão do setor é banda única 95–105%, e ela manteve o desenho duplo.
- `floor` nos dois orçamentos, nunca `ceil`.
- **Acumulado entre execuções:** o consumido em ORs anteriores continua debitado. O cliente não ganha 5% novos a cada rodada. Não zera quando o pedido é alterado.
- **Correção de desenho registrada:** a base **não** pode sair do snapshot de pendentes, que exclui pares já processados e portanto encolhe a cada execução — isso causa estouro do teto num cenário e perda de orçamento legítimo no outro. Precisa do total original + acumulado consumido.
- **Mínimo viável antes dos extras:** cada produto do pedido recebe o mínimo viável antes de qualquer peça extra ser distribuída, para o pedido não ficar com produtos zerados porque o primeiro da fila comeu o orçamento.

### Prioridade (ALOC-03, ALOC-06)
- Ordena por **valor total do pedido do cliente** — a entrega é agrupada fisicamente por cliente no CD, então pedido caro ganha a peça disputada.
- **Desempate total e explícito** (ex.: `nr_pedido` como último campo da chave). Hoje a ordem de desempate depende da ordem de chegada do snapshot, o que pode variar o `plan_hash` entre execuções empatadas e quebrar a idempotência.
- Nunca iterar `set`/`dict` onde a ordem afeta o resultado.

### Rateio financeiro (FIX-02)
- Reusar o rateio de maior resto (Hamilton) que **já existe** em `app/modules/pedidos/domain/edicao_grade.py` (função interna `_allocate`), promovendo-o a utilitário compartilhado.
- Corrige um drift **já latente hoje**: `adequar_grade_produto` faz `round()` item a item sem garantir que a soma das partes bate com o total.

### Starvation
- Cliente pequeno pode ficar sistematicamente atrás. **Aceito** como consequência da regra de prioridade — não "corrigir" com aging ou reserva mínima, porque contrariaria a regra ditada. A resposta é instrumentar (Phase 16, STANDBY-06).

### Claude's Discretion
- Forma concreta da abstração de política por modo (Protocol, enum, dataclass de estratégia) — desde que o orquestrador comum não duplique crédito/prioridade/decremento/furo entre os modos.
- Estrutura interna do ledger de orçamento, desde que use inteiros (não `float`).
- Se inverter os loops para pedido-externo/produto-interno ou manter produto-externo com estado por pedido — a pesquisa recomenda inverter, mas a decisão é do planejador desde que a semântica de disputa não mude.
- Ordem interna dos produtos dentro de um pedido na passada de mínimo viável (a pesquisa sugere corte necessário crescente, para maximizar quantos produtos são atendidos).
- Nomes de funções, arquivos e organização dos testes.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Regras de negócio autoritativas
- `.planning/PROJECT.md` § "Regras de negócio da alocação" — as 5 regras e a precedência entre elas
- `.planning/STATE.md` § Decisions → "v1.3 — Motor de alocação" — tabela das 9 decisões com o porquê de cada uma

### Pesquisa do milestone
- `.planning/research/v1.3/ARCHITECTURE.md` — pontos de integração com arquivo:linha, novo vs modificado vs removido, ordem de construção
- `.planning/research/v1.3/PITFALLS.md` — 7 armadilhas com invariantes testáveis (estado mutável, double-spend, arredondamento, determinismo)
- `.planning/research/v1.3/STACK.md` — veredito greedy vs solver, APIs de stdlib, reaproveitamento do Hamilton
- `.planning/research/v1.3/SUMMARY.md` — síntese

### Código a modificar
- `app/modules/pedidos/domain/motor_adequacao.py` — o motor (474 linhas): `processar_pedidos`, `_processar_pedidos_canal`, `adequar_grade_produto`, `is_sem_credito`, `marcar_stand_by`
- `app/modules/pedidos/domain/value_objects.py` — `get_tamanho_idx`, `RANKINGS`, `montar_chave_estoque`, `normalizar_canal`, `CANAIS`
- `app/modules/pedidos/domain/edicao_grade.py` — rateio Hamilton a promover

### Código a NÃO modificar nesta fase (mas a respeitar)
- `app/modules/pedidos/processing/domain.py` § `build_processing_plan` — consumidor do motor; o contrato de retorno não pode quebrar
- `app/tests/test_pedidos_motor.py` — suíte existente do motor

### Convenções do repositório
- `.claude/CLAUDE.md` — arquitetura DDD, placebos, convenções de commit
- `.planning/codebase/CONVENTIONS.md` — estilo, nomenclatura, tipagem
- `.planning/codebase/TESTING.md` — como escrever e rodar testes

</canonical_refs>

<specifics>
## Specific Ideas

### Cenário numérico ditado pela mantenedora (deve virar teste)
Dois clientes disputam o mesmo produto. Cliente A quer 10 peças, dentro de um pedido global de 1.000 peças e R$ 50.000. Cliente B quer 15 peças, dentro de um pedido de 50 peças e R$ 1.000. Estoque = 20 peças.
**Resultado esperado:** A é atendido; B fica em stand by. Vale nos dois modos.

### Sequência fixa por par (a ordem é regra, não detalhe de implementação)
`crédito → furo de grade → política de quantidade → guarda de zero → commit (decrementa estoque, debita orçamento)`.

### Ambiente
`uv run` local falha no Windows (pyicu). Rodar testes por Docker:
`docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`

### Working tree compartilhado
Há trabalho não commitado de SSO Microsoft de outra frente (`app/modules/auth/`, `alembic/versions/031_*`, `microsoft_sso.py`). Sempre `git add` com caminhos explícitos e conferir `git show --stat` antes de commitar.

</specifics>

<deferred>
## Deferred Ideas

- **Peças extras nos tamanhos com mais sobra** (ALOC-11) → Phase 18. Esta fase calcula o orçamento de adição; quem distribui é a 18.
- **Guarda contra OR zerada e `ceil`→`floor`** (ALOC-05) → Phase 13, paralela. **Coordenação:** as duas fases tocam `adequar_grade_produto`. Se a 13 rodar antes, esta fase herda a correção; se rodar depois, a 13 precisa aplicar sobre o código já reescrito. O planejador deve assumir que a guarda de zero **existe no orquestrador** e não reimplementá-la.
- **Roteamento do modo sem adequação e remoção do streaming** (ARCH-01) → Phase 15.
- **Correção de `_assert_stock_capacity` para tipo `sem`** (FIX-01) → Phase 15.
- **Persistência do motivo de stand by e contador de starvation** (STANDBY-01/06) → Phase 16.
- **Solver de otimização** — descartado na pesquisa: o guloso ordenado é a implementação exata da regra de prioridade, não uma aproximação; um solver maximizaria receita agregada violando a regra.
- **Aging / reserva mínima contra starvation** — descartado: contrariaria a regra de prioridade ditada.

</deferred>

---

*Phase: 14-motor-de-aloca-o-puro-regras-de-neg-cio*
*Context gathered: 2026-08-14 a partir das decisões da sessão (sem discuss-phase — as perguntas já haviam sido respondidas)*
