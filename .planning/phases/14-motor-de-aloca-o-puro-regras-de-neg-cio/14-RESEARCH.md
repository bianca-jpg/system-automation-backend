# Phase 14: Motor de alocação puro (regras de negócio) - Research

**Researched:** 2026-08-14
**Domain:** Reescrita do núcleo de decisão do motor de adequação (`app/modules/pedidos/domain/`) — crédito, tudo-ou-nada por par, furo de grade, orçamento ±5% por pedido completo, prioridade determinística, rateio financeiro sem drift.
**Confidence:** HIGH (mapa de código e testes lidos linha a linha nesta sessão) / MEDIUM apenas onde o desenho é decisão de organização (Claude's Discretion), sinalizado explicitamente abaixo.

**Não repete** o veredito greedy-vs-solver (`STACK.md`), a discussão de streaming/roteamento global (`ARCHITECTURE.md`, é Phase 15) nem o catálogo geral de pitfalls (`PITFALLS.md`) — este documento é o complemento cirúrgico para **planejar a Phase 14 especificamente**: o que muda função por função, o que quebra na suíte, como dividir em planos, e a arquitetura de validação.

## Summary

O motor (`motor_adequacao.py`, 474 linhas) precisa de uma reescrita profunda, mas inteiramente contida em `domain/`: nenhuma função nova precisa de I/O, e o contrato de retorno de `processar_pedidos` (5 chaves) é preservado por constraint explícita. A mudança mais estrutural não é nenhuma regra individual — é que o **orçamento ±5% deixa de ser calculado por produto isolado e passa a ser um ledger compartilhado por pedido inteiro**, o que introduz estado mutável atravessando iterações e obriga a repensar a ordem dos laços (hoje produto-externo/pedido-interno). A pesquisa encontrou uma simplificação real e verificável: **inverter para pedido-externo/produto-interno não muda o resultado de alocação de estoque** (a partição de estoque por produto já é independente da ordem de visita) e torna o ledger de orçamento trivialmente local a cada iteração — recomendado, mas é discretion do planejador confirmar.

A suíte existente (`test_pedidos_motor.py`, 20 testes) tem uma mistura de testes que **verificam a regra antiga de adequação parcial ±5% por produto** (precisam ser reescritos, porque a regra mudou de "por produto" para "por pedido completo") e testes de infraestrutura pura do motor (`agrupar_por_produto`, `is_sem_credito`, `get_tamanho_idx`, particionamento por canal, crédito) que **devem continuar passando sem alteração** — são a rede de segurança contra regressão silenciosa. Dois arquivos adicionais (`test_pedidos_motor_performance_024.py`, `test_pedidos_processing_cancellation_024.py`) importam `motor_adequacao` diretamente e **fixam a assinatura atual de `adequar_grade_produto`/`_indexar_estoque_por_produto`** via monkeypatch — qualquer mudança de assinatura precisa atualizar esses dois arquivos também, não só o de negócio.

**Primary recommendation:** organizar a fase em 1 plano de preparação paralelizável (rateio Hamilton promovido a utilitário + fixtures/factories de teste), seguido de um bloco sequencial não-paralelizável (mesmo arquivo, mesma cadeia de decisão por par) que primeiro fixa o desempate determinístico, depois introduz o ledger de orçamento por pedido, depois tudo-ou-nada e furo de grade — e só então migrar a suíte de testes e amarrar `FIX-02`. Ver seção "Divisão em planos executáveis".

## Architectural Responsibility Map

Este é um subsistema de domínio puro (sem tiers de browser/CDN) — a tabela abaixo mapeia capacidade → camada dentro da arquitetura em 3 camadas do repositório (`domain/` puro, `application/`+`processing/` orquestração, `infrastructure/` I/O), para o plan-checker confirmar que nada vaza para fora de `domain/` nesta fase.

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Checagem de crédito por cliente | Domain (`motor_adequacao.py`) | — | Predicado puro sobre `status_credito` já carregado; `is_sem_credito` não muda |
| Furo de grade (tamanhos do meio) | Domain (novo, `motor_adequacao.py` ou módulo irmão) | — | Predicado local sobre itens+estoque já em memória; usa `get_tamanho_idx`/`RANKINGS` existentes |
| Ledger de orçamento ±5% por pedido | Domain (novo) | Application/Infra (Phase 15) | Cálculo puro dado `total_original`+`consumido_acumulado` como **parâmetros de entrada**; a leitura desses valores de ORs anteriores é I/O e pertence à Phase 15 |
| Política de quantidade por modo (tudo-ou-nada vs ±5%) | Domain (novo) | — | Estratégia pura, delegada por um orquestrador comum que já decide crédito/furo/prioridade |
| Prioridade determinística (desempate total) | Domain (`_processar_pedidos_canal` ou sucessora) | — | Ordenação pura sobre dados já carregados; não requer banco |
| Rateio financeiro sem drift (FIX-02) | Domain (promovido de `edicao_grade.py`) | — | Já existe como `_allocate`; só precisa virar utilitário compartilhado |
| Persistência do motivo de stand-by | — | Application/Infra (Phase 16) | Fora do escopo desta fase — o domínio só devolve a informação, não persiste |
| Roteamento do modo sem-adequação pelo caminho global | — | Application (`processing/`, Phase 15) | Fora do escopo desta fase — o motor só precisa **suportar** os dois modos |
| Leitura de ORs anteriores para o acumulado do orçamento | — | Infrastructure (Phase 15) | Explicitamente fora de `domain/` por decisão do CONTEXT — é I/O |

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

Todas travadas pela mantenedora em 2026-08-14. Não reabrir sem consultá-la.

**Crédito (ALOC-01)**
- Cliente sem crédito **nunca** entra na OR, em nenhum dos dois modos. O pedido dele continua em pedidos em aberto até o status mudar.
- Crédito é do **cliente**, não do par: um pedido bloqueado fica bloqueado em todos os seus produtos.
- Pedido bloqueado **não consome estoque** — a peça fica disponível para o próximo da fila.

**Modo sem adequação (ALOC-02, ALOC-03)**
- **Tudo-ou-nada por par** cliente×produto: reserva a grade exatamente como pedida, em todos os tamanhos, ou o par inteiro fica em stand by. Nunca quantidade diferente da pedida.
- Os **demais produtos do mesmo pedido** seguem elegíveis normalmente — a rejeição é por par, não por pedido.
- Na disputa pelo mesmo produto, consome o estoque na **ordem de prioridade** e quem não couber fica em stand by.

**Furo de grade (ALOC-04)**
- Vale nos **dois modos**.
- Considera **apenas os tamanhos que aquele cliente pediu** naquele produto. Tamanho não pedido não conta.
- Regra: ordenados por `get_tamanho_idx`, se algum tamanho **estritamente entre** o menor e o maior pedido tiver **0 reservável**, o par fica em stand by. Ter pelo menos 1 em cada tamanho do meio libera a reserva.
- Depende do estoque **no momento da alocação** (que é decrementado conforme os prioritários consomem), logo roda **dentro** do loop, não antes.
- Casos de borda decididos: 1 só tamanho → íntegra; 2 tamanhos adjacentes → íntegra; cliente pediu PP e GG sem pedir M → íntegra (M não foi pedido); tamanho desconhecido (`get_tamanho_idx` → 999) → descartado da ordenação, nunca define extremo nem conta como interior.

**Orçamento ±5% (ALOC-07, ALOC-08, ALOC-09, ALOC-10)**
- Base = quantidade total do **pedido completo** do cliente (todos os produtos), **não** a grade de cada produto isolado.
- **Dois orçamentos separados**, de 5% cada: adições e cortes **não se compensam**. Decisão consciente da mantenedora — a pesquisa apontou que o padrão do setor é banda única 95–105%, e ela manteve o desenho duplo.
- `floor` nos dois orçamentos, nunca `ceil`.
- **Acumulado entre execuções:** o consumido em ORs anteriores continua debitado. O cliente não ganha 5% novos a cada rodada. Não zera quando o pedido é alterado.
- **Correção de desenho registrada:** a base **não** pode sair do snapshot de pendentes, que exclui pares já processados e portanto encolhe a cada execução — isso causa estouro do teto num cenário e perda de orçamento legítimo no outro. Precisa do total original + acumulado consumido.
- **Mínimo viável antes dos extras:** cada produto do pedido recebe o mínimo viável antes de qualquer peça extra ser distribuída, para o pedido não ficar com produtos zerados porque o primeiro da fila comeu o orçamento.

**Prioridade (ALOC-03, ALOC-06)**
- Ordena por **valor total do pedido do cliente** — a entrega é agrupada fisicamente por cliente no CD, então pedido caro ganha a peça disputada.
- **Desempate total e explícito** (ex.: `nr_pedido` como último campo da chave). Hoje a ordem de desempate depende da ordem de chegada do snapshot, o que pode variar o `plan_hash` entre execuções empatadas e quebrar a idempotência.
- Nunca iterar `set`/`dict` onde a ordem afeta o resultado.

**Rateio financeiro (FIX-02)**
- Reusar o rateio de maior resto (Hamilton) que **já existe** em `app/modules/pedidos/domain/edicao_grade.py` (função interna `_allocate`), promovendo-o a utilitário compartilhado.
- Corrige um drift **já latente hoje**: `adequar_grade_produto` faz `round()` item a item sem garantir que a soma das partes bate com o total.

**Starvation**
- Cliente pequeno pode ficar sistematicamente atrás. **Aceito** como consequência da regra de prioridade — não "corrigir" com aging ou reserva mínima, porque contrariaria a regra ditada. A resposta é instrumentar (Phase 16, STANDBY-06).

### Claude's Discretion

- Forma concreta da abstração de política por modo (Protocol, enum, dataclass de estratégia) — desde que o orquestrador comum não duplique crédito/prioridade/decremento/furo entre os modos.
- Estrutura interna do ledger de orçamento, desde que use inteiros (não `float`).
- Se inverter os loops para pedido-externo/produto-interno ou manter produto-externo com estado por pedido — a pesquisa recomenda inverter, mas a decisão é do planejador desde que a semântica de disputa não mude.
- Ordem interna dos produtos dentro de um pedido na passada de mínimo viável (a pesquisa sugere corte necessário crescente, para maximizar quantos produtos são atendidos).
- Nomes de funções, arquivos e organização dos testes.

### Deferred Ideas (OUT OF SCOPE)

- **Peças extras nos tamanhos com mais sobra** (ALOC-11) → Phase 18. Esta fase calcula o orçamento de adição; quem distribui é a 18.
- **Guarda contra OR zerada e `ceil`→`floor`** (ALOC-05) → Phase 13, paralela. As duas fases tocam `adequar_grade_produto`. Se a 13 rodar antes, esta fase herda a correção; se rodar depois, a 13 precisa aplicar sobre o código já reescrito. O planejador deve assumir que a guarda de zero **existe no orquestrador** e não reimplementá-la.
- **Roteamento do modo sem adequação e remoção do streaming** (ARCH-01) → Phase 15.
- **Correção de `_assert_stock_capacity` para tipo `sem`** (FIX-01) → Phase 15.
- **Persistência do motivo de stand by e contador de starvation** (STANDBY-01/06) → Phase 16.
- **Solver de otimização** — descartado: o guloso ordenado é a implementação exata da regra de prioridade.
- **Aging / reserva mínima contra starvation** — descartado: contrariaria a regra de prioridade ditada.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Descrição | Suporte de pesquisa |
|----|-----------|----------------------|
| ALOC-01 | Cliente sem crédito nunca entra na OR (sem adequação) | `is_sem_credito` já existe e não muda; `sem_credito_nrs` já bloqueia por pedido inteiro em `_processar_pedidos_canal:349-358` — comportamento a preservar também no novo modo tudo-ou-nada |
| ALOC-02 | Tudo-ou-nada por par no modo sem adequação | Nova política de quantidade (`PoliticaTudoOuNada`), ver "Recomendação de desenho" |
| ALOC-03 | Estoque consumido por ordem de prioridade, quem não couber fica em stand by | Reaproveita `ordens_prioridade`/`ordem_idx` já existentes (`_processar_pedidos_canal:399-407`) — mecanismo de prioridade não muda, só o consumidor (política) muda por modo |
| ALOC-04 | Furo de grade nos dois modos | Nova função predicado, usa `get_tamanho_idx`/`RANKINGS` (`value_objects.py:41-109`, inalterados) |
| ALOC-06 | Desempate total e determinístico | Fix pequeno e isolado em `_prioridade()` (`motor_adequacao.py:387-396`) — acrescentar `nr_pedido` como último campo da tupla |
| ALOC-07 | Tolerância medida sobre o pedido completo | Novo ledger `OrcamentoPedido`, ver "Recomendação de desenho" |
| ALOC-08 | Orçamentos de adição/corte separados, não compensáveis | Dois contadores inteiros independentes no ledger, nunca subtraídos um do outro |
| ALOC-09 | Acumulado entre execuções continua debitado | Domínio **recebe** total original + consumido acumulado como parâmetros (I/O real é Phase 15) — ver nota de fronteira no CONTEXT |
| ALOC-10 | Mínimo viável antes de extras | Duas passadas por pedido: 1ª garante mínimo (consome só o orçamento de corte), 2ª distribui adição |
| FIX-02 | Rateio financeiro sem drift | Promover `_allocate` de `edicao_grade.py` para utilitário público (`STACK.md` §4 já valida a extração) |

`ALOC-05` (Phase 13, paralela), `ALOC-11` (Phase 18), `FIX-01`/`ARCH-01` (Phase 15), `STANDBY-*` (Phase 16/17) não são desta fase — citados aqui só para as fronteiras de integração.
</phase_requirements>

---

## 1. Mapa cirúrgico de `motor_adequacao.py` (474 linhas)

| Função | Linhas atuais | O que acontece nesta fase | Notas |
|---|---|---|---|
| `is_sem_credito` | 46-57 | **Permanece igual** | Predicado puro já correto; nenhuma regra nova o afeta |
| `agrupar_por_produto` | 60-86 | **Permanece igual** (possivelmente só de nome/posição se os loops forem invertidos) | Continua útil se o desenho mantiver o particionamento por produto; se os loops forem invertidos para pedido-externo (recomendado, ver abaixo), esta função pode ser mantida como está e usada só para construir `estoque_por_produto`, não mais como estrutura de iteração principal |
| `_indexar_estoque_por_produto` | 89-116 | **Permanece igual** | Já é testada de propriedade/paridade em `test_pedidos_processing_cancellation_024.py` (o nome do arquivo é histórico — o conteúdo é sobre o motor) via `_indice_legado`; não mexer na assinatura sem atualizar esse teste |
| `adequar_grade_produto` | 119-177 | **Muda de assinatura e de responsabilidade** | Deixa de calcular `limite_falta`/`orcamento_aumento` internamente a partir de `total_grade` local (linhas 134-136); passa a **receber** o orçamento restante (do ledger do pedido) como parâmetro, e a devolver quanto efetivamente consumiu (para o ledger decrementar). O cálculo de `vl_liquido` (linha 173) passa a usar o rateio Hamilton promovido (FIX-02) em vez de `round()` item a item. `is_ext`/extremos (linha 151, 160) muda de "só os extremos" para "onde a política de extras mandar" — mas a distribuição por tamanho em si é Phase 18 (ALOC-11); nesta fase o mais seguro é manter a mecânica de extremos como placeholder documentado, só desacoplando a *quantidade* de extras (que agora vem do ledger do pedido) da *distribuição* por tamanho (que continua simplista até a Phase 18 assumir) |
| `marcar_stand_by` | 180-197 | **Muda o parâmetro `motivo`** (não a forma) | Precisa de motivos novos e específicos por regra: `"Furo de grade: tamanho {X} sem estoque reservável"`, `"Sem adequação: grade completa indisponível"`, além dos já existentes `"Aguardando liberação de crédito"` e o de canal. Estrutura da função não muda |
| `processar_pedidos` | 200-300 | **Assinatura ganha novos parâmetros opcionais** (nunca muda as chaves de retorno, que são contrato protegido) | Precisa aceitar: `modo` (adequação vs tudo-ou-nada — hoje só existe o modo "±5%"), e os dados de orçamento por pedido (`total_original: Mapping[int, int]`, `consumido_previo: Mapping[int, tuple[int,int]]` ou equivalente) exigidos por ALOC-07/09. `criterio`/`tolerancia` continuam existindo. As 5 chaves do dict de saída **não mudam de forma** — constraint inegociável |
| `_processar_pedidos_canal` | 303-474 | **Reescrita mais profunda da fase** | É onde tudo se conecta: agrupamento, crédito (348-358, preservar), prioridade (382-400, só adicionar desempate ALOC-06), consumo de estoque por produto (402-453, precisa ganhar furo de grade + delegação de política de quantidade + consumo do ledger). Ver "Recomendação de desenho" para a proposta de inversão de loop |

**Funções que não existem hoje e precisam ser criadas** (nomes sugeridos, discretion do planejador):
- Predicado de furo de grade — ex. `tem_furo_de_grade(itens_pedido_produto, estoque_local, cd_prod_cor) -> bool`
- Ledger de orçamento — ex. `OrcamentoPedido` (dataclass) com `consumir_adicao(qtd) -> int` / `consumir_corte(qtd) -> int` (retornam o quanto efetivamente coube)
- Política de quantidade por modo — ex. `Protocol PoliticaQuantidade` com implementações `PoliticaTudoOuNada` e `PoliticaAdequacao`
- Rateio financeiro compartilhado — ex. `ratear_hamilton` (extraído de `_allocate`), em módulo próprio ou reaproveitado de `edicao_grade.py`

**Nenhuma função morre** nesta fase — todas continuam necessárias, só mudam de escopo/assinatura. `value_objects.py` (`get_tamanho_idx`, `RANKINGS`, `montar_chave_estoque`, `normalizar_canal`, `CANAIS`) **não precisa mudar** — o furo de grade e a prioridade só consomem o que já existe ali.

---

## 2. Impacto na suíte existente

### `app/tests/test_pedidos_motor.py` (20 testes, todos importam de `app.modules.pedidos.service`, não de `domain.motor_adequacao` diretamente — o `service.py` é um shim de re-export, ver Sources)

| Teste | Categoria | Por quê |
|---|---|---|
| `test_canal_bucket` | (b) infraestrutura — **continua passando** | `canal_bucket`/`normalizar_canal` não mudam |
| `test_is_sem_credito` | (b) infraestrutura — **continua passando** | Função inalterada |
| `test_get_tamanho_idx` | (b) infraestrutura — **continua passando** | `value_objects.py` inalterado |
| `test_adequar_grade_produto_estoque_suficiente_sem_ajuste` | (b) — **provavelmente continua passando**, mas a assinatura da chamada muda (`tolerancia` deixa de bastar sozinho se o orçamento vier de fora) — precisa adaptar a chamada no teste, não a asserção de negócio | Estoque suficiente para a grade inteira é um caso trivial que não depende de orçamento cross-produto |
| `test_adequar_grade_produto_aumenta_extremos_dentro_do_orcamento` | **(a) quebra por mudança de REGRA** — precisa ser reescrito | Hoje `orcamento_aumento` é calculado como `floor(total_grade_do_PRODUTO * tolerancia)`; a regra nova mede sobre o pedido completo. Este teste de exemplo (`tolerancia=0.1` sobre 30 peças de 1 produto) precisa virar um teste do **ledger do pedido**, não da função de produto isolada |
| `test_adequar_grade_produto_falta_alem_da_tolerancia_vira_stand_by` | **(a) quebra por mudança de REGRA** — `limite_falta` deixa de ser `ceil(total_grade*tolerancia)` por produto; passa a ser consumo do orçamento de corte do PEDIDO, com `floor` (não `ceil`) nos dois lados agora | Precisa reescrever para orçamento vindo de fora, com `floor` |
| `test_adequar_grade_produto_falta_dentro_da_tolerancia_reduz_parcial` | **(a) quebra por mudança de REGRA** — mesma razão acima | Idem |
| `test_marcar_stand_by_preserva_valores_e_remove_campos_transitorios` | (b) — **continua passando** se a assinatura de `marcar_stand_by` não mudar de forma (só o texto do motivo passado por quem chama) | Testa a função isoladamente com um `motivo` fixo — não depende da regra de orçamento |
| `test_agrupar_por_produto_ignora_pares_ja_processados` | (b) — **continua passando** | `agrupar_por_produto` não muda |
| `test_agrupar_por_produto_pedido_processado_num_produto_segue_elegivel_no_outro` | (b) — **continua passando** | Idem |
| `test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito` | **(a) parcialmente** — a asserção de prioridade/crédito continua válida, mas o item 2 (`resultado["resultados"][(2, "PROD1")][0]`) hoje espera `status_item == "Pedido em Stand By"` vindo de `adequar_grade_produto` (regra antiga de ±5% por produto); com o modo padrão precisando ser explicitado (`adequar` vs `tudo_ou_nada`) e o orçamento vindo de fora, os valores de `tolerancia=0.05` implícitos no teste podem produzir resultado diferente — **reescrever mantendo a intenção (prioridade + isolamento de crédito), ajustando os dados de entrada para o novo contrato de orçamento** |
| `test_processar_pedidos_particiona_por_canal_sem_misturar_estoque` | (b) — **continua passando** | Isolamento por canal não muda nesta fase |
| `test_processar_pedidos_sem_canal_vai_para_stand_by_sem_consumir_franquia` | (b) — **continua passando** | Canal desconhecido não muda de comportamento |
| `test_processar_pedidos_sem_canal_respeita_pares_ja_processados` | (b) — **continua passando** | Idem |
| `test_processar_pedidos_fatura_um_produto_e_deixa_o_outro_em_stand_by` | (b) — **provavelmente continua passando**, é o caso de faturamento parcial (Regra 5), que não muda nesta fase — mas a chamada precisa fornecer o novo parâmetro de orçamento (mesmo que trivial/no-op para este cenário de 1 tamanho, estoque exato) | Vale conferir se o novo `adequar_grade_produto` ainda devolve `"Gerar OR"` quando o estoque bate exato, sem orçamento nenhum envolvido |

**Resumo:** dos 20 testes, **~4-5 quebram por mudança de regra** (os que testam a semântica ±5% por produto isolado — precisam virar testes do ledger por pedido) e **~15-16 continuam válidos como estão ou só precisam de ajuste de chamada** (novo parâmetro obrigatório), não de nova asserção de negócio. Isso significa que a maior parte da migração de teste é **mecânica** (adaptar assinatura de chamada), e a parte de regra de negócio nova concentra-se nos poucos testes de `adequar_grade_produto`/orçamento — mas esses poucos precisam ser **substituídos por uma suíte nova e mais rica** (cenários de pedido com múltiplos produtos, execuções sequenciais, ver seção de Validação).

### Outros arquivos que importam do motor (via grep, não só por nome)

- **`app/tests/test_pedidos_motor_performance_024.py`** — `from app.modules.pedidos.domain import motor_adequacao as motor`. Testa: (1) paridade exata entre `_indexar_estoque_por_produto` otimizado e uma implementação de referência (`_indice_legado`) via `monkeypatch.setattr(motor, "_indexar_estoque_por_produto", ...)` chamando `motor.processar_pedidos(...)` — **continua válido sem mudança** desde que `_indexar_estoque_por_produto` não mude de assinatura; (2) contagem de visitas ao estoque via `monkeypatch.setattr(motor, "adequar_grade_produto", _adequar_observando_subset)` — **este monkeypatch fixa a assinatura atual de `adequar_grade_produto`** (`itens, estoque_local, cd_prod_cor, tolerancia=0.05`); se a assinatura ganhar um parâmetro obrigatório de orçamento, este teste **quebra por mudança de assinatura (categoria b, não de regra)** e precisa ser atualizado junto.
- **`app/tests/test_pedidos_processing_cancellation_024.py`** — importa `motor_adequacao` e `processing.domain.build_processing_plan`. Um dos testes (`test_cpu_planning_cancel_stops_thread_before_remaining_products`) faz `monkeypatch.setattr(motor, "adequar_grade_produto", _slow_grade)` com uma assinatura própria (`items, available_stock, product_code, tolerance=0.05, *, cancel_token=None`) que **replica a assinatura atual exatamente** — **quebra por mudança de assinatura (categoria b)** assim que `adequar_grade_produto` ganhar o parâmetro de orçamento; a correção é só atualizar a assinatura do fake, não a intenção do teste (ele testa cancelamento cooperativo, não regra de negócio).
- Nenhum outro arquivo de teste (`test_pedidos_routes.py`, `test_pedidos_processing_*.py` restantes) chama `motor_adequacao` diretamente — eles operam no nível de `processing/` ou HTTP, com mocks acima da camada de domínio, então não são afetados por mudança de assinatura interna, só por mudança de **comportamento observável** (ex. se `build_processing_plan` passar a exigir novos parâmetros de orçamento — mas isso é conectado pela Phase 15, fora desta fase).
- `app/modules/pedidos/service.py` é um shim de re-export (`from app.modules.pedidos.domain.motor_adequacao import (...)`) usado pelo teste de negócio antigo — **não precisa mudar de estrutura**, só continuar re-exportando os mesmos 5 símbolos (o shim não filtra nada, é passagem direta).

---

## 3. Divisão em planos executáveis

O arquivo `motor_adequacao.py` é o gargalo de paralelização: quase todas as regras novas tocam `_processar_pedidos_canal`/`adequar_grade_produto` na mesma sequência fixa e obrigatória (**crédito → furo de grade → política de quantidade → guarda de zero → commit**, ditada no CONTEXT como regra, não detalhe). Isso significa que **não é seguro paralelizar sub-tarefas que editem o corpo do laço principal** — dois planos mexendo simultaneamente em `_processar_pedidos_canal` vão conflitar em merge e, pior, podem produzir um estado intermediário que não implementa a sequência completa (ex. furo de grade sem o ledger de orçamento ainda not existir trava o teste de "mínimo viável").

**Recomendação de ondas** (nomes de plano ilustrativos — o planejador decide granularidade final):

| Onda | Plano(s) | Paralelizável? | Depende de | Arquivos tocados |
|---|---|---|---|---|
| 0 | **Rateio Hamilton → utilitário público** (FIX-02, parte 1) | Sim, independente de tudo | — | `domain/edicao_grade.py` (extrair `_allocate`), novo `domain/rateio.py` (ou nome equivalente), teste novo do utilitário |
| 0 | **Fixtures/factories de teste + scaffolding de propriedades** | Sim, paralelo ao plano acima (arquivos diferentes) | — | Novo módulo de test helpers (ex. `app/tests/factories_motor.py`), esqueleto vazio de `test_pedidos_motor_properties.py` (sem `hypothesis` ainda — só estrutura), fixture do cenário numérico ditado pela mantenedora (cliente A/B) |
| 1 | **Desempate determinístico (ALOC-06)** | Não paraleliza com a Onda 2 (mesmo arquivo, mesma função `_prioridade`), mas é pequeno e de baixo risco — vale isolar como o primeiro commit da cadeia sequencial | Onda 0 (opcional, não bloqueante) | `motor_adequacao.py` (`_processar_pedidos_canal`, só a chave de `sorted`) |
| 2 | **Núcleo: ledger de orçamento por pedido + duas passadas (mínimo viável / extras) — ALOC-07/08/09/10** | **Não**, é o corpo principal da reescrita | Onda 1 | `motor_adequacao.py` (reescrita de `adequar_grade_produto` + `_processar_pedidos_canal`), novo objeto `OrcamentoPedido` |
| 2 | **Tudo-ou-nada + política por modo — ALOC-02/03** | **Não**, mesma função/mesmo arquivo que a linha acima — recomenda-se um único plano cobrindo as duas (orçamento + tudo-ou-nada) para não reabrir a mesma função duas vezes em sequência, OU dois planos estritamente sequenciais (nunca em paralelo) | Onda 1 | `motor_adequacao.py` |
| 2 | **Furo de grade — ALOC-04** | **Não**, mesma razão — entra na mesma cadeia sequencial (é chamado dentro do mesmo laço, antes da política de quantidade) | Onda 1 | `motor_adequacao.py` |
| 3 | **Ligar FIX-02 no motor** (usar o utilitário da Onda 0 para `vl_liquido`) | Pode ser dobrado dentro do plano da Onda 2 (evita tocar a mesma função duas vezes) — se for plano separado, depende da Onda 2 estar fechada | Onda 0 + Onda 2 | `motor_adequacao.py` (só a linha do rateio financeiro) |
| 4 | **Migração da suíte de testes** (`test_pedidos_motor.py` reescrito por regra, + os 2 arquivos de assinatura fixa atualizados, + testes novos por regra) | Parcialmente — a migração dos testes de infraestrutura inalterada (categoria b) pode começar assim que a Onda 2 estabilizar a assinatura; os testes de regra nova (categoria a) dependem do comportamento final | Onda 2 e 3 completas | `test_pedidos_motor.py`, `test_pedidos_motor_performance_024.py`, `test_pedidos_processing_cancellation_024.py` |
| 5 | **Gate de fase: full suite verde + confirmação de que o domínio está "hypothesis-ready"** (sem adicionar a dependência ainda — isso é Phase 19) | Não é paralelizável, é o fechamento | Onda 4 | — (só execução de teste) |

**Por que não dividir mais:** a tentação de criar um plano por requirement (ALOC-02, ALOC-04, ALOC-07... cada um seu plano) parece paralelizável no papel, mas todos escrevem no mesmo laço de 70 linhas de `_processar_pedidos_canal` e na mesma função `adequar_grade_produto` — a ordem de decisão por par é regra de negócio explícita (crédito → furo → quantidade → zero → commit), então implementar furo antes do ledger de orçamento (ou vice-versa) como PRs paralelos e depois "juntar" é o cenário exato onde a Pitfall 1 (`PITFALLS.md`) se manifesta: cada plano isolado passa nos próprios testes, e o merge produz uma ordem de decisão diferente da ditada, sem nenhum teste unitário acusando.

---

## Recomendação de desenho (para orientar o planner, não uma implementação pronta)

### Inversão de loop: pedido-externo, produto-interno

O código atual (`_processar_pedidos_canal:402-453`) itera `cd_prod_cor` no laço externo e pedidos (por prioridade) no laço interno. Isso funciona bem para consumir estoque compartilhado por produto, mas obriga o ledger de orçamento (que é por pedido, atravessando produtos) a viver **fora** dos dois laços, como um dict mutável indexado por `nr_pedido` e tocado a cada passagem pelo laço interno — espalhado, fácil de esquecer de atualizar em um caminho de saída antecipada (exatamente o Pitfall 1).

**Verificação de que a inversão é segura:** a partição de estoque por produto (`estoque_por_produto[cd_prod_cor]`, `_indexar_estoque_por_produto`) já é independente entre produtos — processar o produto A não afeta o estoque do produto B. A ordem de prioridade (`ordens_prioridade`/`ordem_idx`) é calculada **uma vez, no início**, a partir do snapshot completo — não muda com a ordem de execução dos laços. Logo: **processar cada pedido inteiro (todos os seus produtos) em ordem de prioridade global, decrementando os dicts de estoque por produto conforme avança, produz exatamente o mesmo resultado de alocação de estoque** que o desenho atual produto-externo — a diferença é só ergonômica: com pedido-externo, o ledger de orçamento do pedido é **local à iteração** (criado no início do pedido, usado e descartado ao final dele), sem precisar de dict externo indexado por `nr_pedido`.

```python
# Esqueleto ilustrativo — não é a implementação final, é o formato recomendado.
for nr_pedido in ordens_prioridade:  # já ordenado, desempate por nr_pedido (ALOC-06)
    if nr_pedido in sem_credito_nrs:
        # ALOC-01: todos os produtos deste pedido -> stand by "sem crédito", sem tocar estoque
        continue

    itens_do_pedido = todos_pedidos_flat[nr_pedido]
    ledger = OrcamentoPedido(
        nr_pedido=nr_pedido,
        total_original=total_original_por_pedido[nr_pedido],       # parâmetro externo (ALOC-09)
        consumido_previo=consumido_previo_por_pedido[nr_pedido],   # parâmetro externo (ALOC-09)
    )

    # Passada 1 — mínimo viável, ordenado por corte necessário crescente (discretion)
    for cd_prod_cor in sorted(produtos_do_pedido, key=corte_necessario_crescente):
        if tem_furo_de_grade(itens_do_pedido[cd_prod_cor], estoque_por_produto[cd_prod_cor], cd_prod_cor):
            resultados[(nr_pedido, cd_prod_cor)] = marcar_stand_by(..., motivo="Furo de grade: ...")
            continue
        # política de quantidade decide, usando SÓ o orçamento de corte do ledger
        ...

    # Passada 2 — extras, ordenado por preço unitário decrescente (recomendação do STACK.md,
    # maximiza R$ por peça extra quando o ledger de adição é compartilhado entre produtos)
    for cd_prod_cor in sorted(produtos_atendidos_na_passada_1, key=preco_unitario_decrescente):
        ...  # consome o orçamento de adição do ledger; distribuição por tamanho é placeholder até Phase 18
```

### Abstração de política por modo

```python
from typing import Protocol

class PoliticaQuantidade(Protocol):
    def decidir(
        self,
        itens: list[dict],
        estoque_local: dict[str, int],
        ledger: "OrcamentoPedido | None",  # None no modo tudo-ou-nada
    ) -> list[dict]:
        """Devolve os itens com qt_liquida/status_item decididos. Nunca decrementa
        estoque nem toca o ledger diretamente — quem chama faz o commit."""
        ...
```

O orquestrador comum (`_processar_pedidos_canal` ou sucessora) resolve **crédito, furo de grade, prioridade e decremento de estoque** uma única vez, e delega só a decisão de quantidade para `PoliticaTudoOuNada`/`PoliticaAdequacao` — atende à constraint explícita do CONTEXT ("desde que o orquestrador comum não duplique crédito/prioridade/decremento/furo entre os modos").

### Ledger como dataclass imutável por leitura, com métodos de consumo

Alinhado com `STACK.md` §3: `@dataclass(frozen=True, slots=True)` para o "retrato" do orçamento restante é tentador, mas o ledger precisa de estado mutável ao longo da passada de um pedido (consumir adição/corte incrementalmente). Recomendação: um pequeno objeto com métodos `consumir_adicao(qtd) -> int` / `consumir_corte(qtd) -> int` que mutam campos internos (não `frozen`) mas expõem só leitura para o resto do código (`restante_adicao`, `restante_corte` como propriedades) — o ponto crítico não é imutabilidade total, é **nunca deixar um caminho de saída antecipada (crédito, canal desconhecido, cancelamento) tocar o ledger sem consumir**.

```python
from dataclasses import dataclass, field

@dataclass(slots=True)
class OrcamentoPedido:
    nr_pedido: int
    total_original: int   # int, nunca float — recebido de fora (ALOC-09)
    _restante_adicao: int = field(init=False)
    _restante_corte: int = field(init=False)

    def __post_init__(self, consumido_adicao_previo: int = 0, consumido_corte_previo: int = 0) -> None:
        limite = (self.total_original * 5) // 100  # floor inteiro, nunca ceil (regra nova)
        self._restante_adicao = max(0, limite - consumido_adicao_previo)
        self._restante_corte = max(0, limite - consumido_corte_previo)

    def consumir_adicao(self, desejado: int) -> int:
        usado = min(desejado, self._restante_adicao)
        self._restante_adicao -= usado
        return usado

    def consumir_corte(self, desejado: int) -> int:
        usado = min(desejado, self._restante_corte)
        self._restante_corte -= usado
        return usado
```

**Mudança de regra a não esquecer:** o código atual usa `math.ceil` para o limite de falta (corte) e `math.floor` para o de aumento (adição) — assimetria que o CONTEXT corrige explicitamente: **os dois orçamentos agora usam `floor`**, sempre, calculados sobre o pedido inteiro. Isso não é só o fix do Pitfall 4 (bug do `ceil` em totais pequenos) — é uma mudança de regra de negócio explícita (ALOC-08), então o teste antigo que dependia de `ceil` (`test_adequar_grade_produto_falta_alem_da_tolerancia_vira_stand_by`) precisa ser reescrito, não só ter a chamada ajustada.

---

## Don't Hand-Roll

| Problema | Não construa | Use em vez disso | Por quê |
|---|---|---|---|
| Rateio de `vl_liquido` sem drift de centavos | Uma nova função de "maior resto" do zero | `_allocate` de `edicao_grade.py`, promovida a utilitário público | Já existe, já é testado, já usa `Decimal` corretamente — reimplementar é retrabalho e risco de reintroduzir o próprio bug que se está corrigindo (FIX-02) |
| Aritmética de tolerância ±5% | `total * 0.05` em `float` | Aritmética inteira (`(total * 5) // 100`) | `float` já é sabidamente impreciso para múltiplos exatos de 20 (`STACK.md` §3); o ledger agora atravessa produtos, então qualquer imprecisão se acumula visivelmente |
| Hash do plano para idempotência (se esta fase precisar expor algo hasheável) | `hash()` builtin | `hashlib.sha256` sobre serialização canônica | `hash()` é randomizado por processo (PEP 456) — não é este código que hasheia hoje (`processing/domain.py` já faz certo), mas qualquer novo dado que entrar no hash (ex. motivo de stand-by) precisa seguir o mesmo padrão |
| Distribuição de peças extras por tamanho | Uma heurística nova e definitiva agora | Manter a mecânica atual de extremos como placeholder documentado | ALOC-11 (a distribuição real por "maior sobra") é Phase 18 — construir algo elaborado aqui é over-engineering para um comportamento que será substituído em 4 fases |

## Common Pitfalls (aplicados a esta fase especificamente)

Ver `.planning/research/v1.3/PITFALLS.md` para o catálogo completo (7 pitfalls, todos ainda válidos). Os que **nascem diretamente nesta fase** (não em fases posteriores):

1. **Estado mutável vazando entre produtos do mesmo pedido (Pitfall 1)** — é o risco central desta fase. Mitigação: ledger nomeado e explícito (`OrcamentoPedido`), nunca inteiros soltos por parâmetro solto; teste de permutação de ordem dos produtos.
2. **Double-spend do orçamento entre execuções (Pitfall 3)** — parcialmente desta fase: o domínio precisa **aceitar** `consumido_previo` como parâmetro e nunca resetá-lo; a persistência real entre execuções é Phase 15, mas o teste de propriedade "múltiplas chamadas sequenciais simulando execuções, acumulado nunca excede o limite sobre o total original" **pode e deve** ser escrito aqui, chamando a função pura repetidamente com o `consumido_previo` da chamada anterior.
3. **Não-determinismo em empates de prioridade (Pitfall 5)** — ALOC-06 é exatamente a correção; teste de determinismo (mesmo snapshot embaralhado → mesmo resultado) deve nascer junto com essa mudança pequena, não esperar o resto da fase.
4. **Arredondamento (Pitfall 4)** — a parte "orçamento em `float`" e "rateio financeiro sem soma-bate-com-total" são desta fase (ALOC-07/08 e FIX-02); a parte "`ceil`→`floor` na guarda de zero isolada" é Phase 13 (ALOC-05) — não confundir as duas correções de arredondamento, são fases diferentes tocando o mesmo sintoma histórico.

Pitfalls 2 (starvation), 6 (regressão silenciosa/shadow run) e 7 (Hypothesis) são tratados na seção de Validação abaixo e nas fases 16/19 — citados aqui só como fronteira.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` 8.3.0+ (`pytest-asyncio` não é necessário para estes testes — motor é síncrono e puro) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths = ["app/tests"]`) |
| Quick run command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py -q` (ambiente local Windows falha por `pyicu` — sempre via Docker, ver nota do CONTEXT) |
| Full suite command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |

### Phase Requirements → Test Map

| Req ID | Comportamento | Tipo de teste | Comando automatizado | Arquivo existe? |
|--------|----------|-----------|-------------------|-------------|
| ALOC-01 | Pedido sem crédito nunca gera OR, em nenhum produto, em nenhum modo | unit (exemplo) | `pytest app/tests/test_pedidos_motor.py -k credito -x` | ✅ (adaptar) — caso tudo-ou-nada é novo |
| ALOC-02 | Grade parcial nunca acontece no modo sem-adequação — ou tudo ou stand-by | unit (exemplo novo) | `pytest app/tests/test_pedidos_motor.py -k tudo_ou_nada -x` | ❌ Wave 0 |
| ALOC-03 | Disputa pelo mesmo produto respeita prioridade em ambos os modos | unit (exemplo, cenário do CONTEXT: cliente A 10pç/pedido 1000pç-R$50k, cliente B 15pç/pedido 50pç-R$1k, estoque 20) | `pytest app/tests/test_pedidos_motor.py -k prioridade_disputa -x` | ❌ Wave 0 (cenário ditado pela mantenedora, deve virar teste nomeado) |
| ALOC-04 | Furo de grade bloqueia em ambos os modos, só nos tamanhos pedidos, só nos "do meio" | unit (exemplo, casos de borda: 1 tamanho, 2 adjacentes, PP+GG sem M, tamanho 999) | `pytest app/tests/test_pedidos_motor.py -k furo_de_grade -x` | ❌ Wave 0 |
| ALOC-06 | Mesmo snapshot embaralhado → mesmo resultado/mesma ordem | unit + propriedade (candidato a Hypothesis, mas exemplo já cobre o essencial nesta fase) | `pytest app/tests/test_pedidos_motor.py -k determinismo -x` | ❌ Wave 0 |
| ALOC-07/08 | Orçamento medido sobre o pedido completo; adição e corte não se compensam | unit (exemplo, pedido com 2+ produtos) | `pytest app/tests/test_pedidos_motor.py -k orcamento_pedido -x` | ❌ Wave 0 |
| ALOC-09 | Acumulado entre chamadas nunca reseta (simulado via parâmetro `consumido_previo`) | unit sequencial (3+ chamadas simuladas) — **fronteira**: domínio prova só com dados injetados, não lê banco | `pytest app/tests/test_pedidos_motor.py -k acumulado_execucoes -x` | ❌ Wave 0 |
| ALOC-10 | Mínimo viável garantido antes de qualquer extra, mesmo com produto caro consumindo primeiro | unit (exemplo, pedido com produto caro + produto barato disputando o mesmo orçamento) | `pytest app/tests/test_pedidos_motor.py -k minimo_viavel -x` | ❌ Wave 0 |
| FIX-02 | Soma de `vl_liquido` dos itens sempre bate com o total esperado (sem drift de centavos) | unit (exemplo com tamanhos numéricos, valores que não dividem exato) | `pytest app/tests/test_pedidos_motor.py -k rateio_sem_drift -x` | ❌ Wave 0 |

### Sampling Rate
- **Por commit de tarefa:** `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py -q` (ciclo rápido, síncrono, sem banco)
- **Por merge de onda:** full suite (`pytest -q`) — importante porque `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` vivem fora do arquivo principal e só pegam regressão de assinatura na suíte completa
- **Gate de fase:** full suite verde antes de `/gsd-verify-work`, mais uma leitura manual do diff de `test_pedidos_motor.py` confirmando que nenhuma asserção de negócio foi apenas "ajustada para o novo valor" sem entender por quê (risco descrito na seção de riscos abaixo)

### Invariantes de propriedade (linguagem-alvo para Hypothesis na Phase 19; nesta fase, viram `assert` de exemplo)

Traduzidas para este domínio específico, a partir de `PITFALLS.md` Pitfall 7 — cada uma deve ter pelo menos um teste de exemplo nesta fase, mesmo sem `hypothesis` instalado:

```python
# 1. Nunca reservar mais que o estoque original disponível, por (produto, tamanho)
assert reservado[(produto, tamanho)] <= estoque_original[(produto, tamanho)]

# 2. Par com furo de grade nunca é selecionado
for par in selecionados:
    tamanhos_pedidos_no_par = tamanhos_entre_min_e_max(par)
    assert all(estoque_reservavel(par, tam) > 0 for tam in tamanhos_pedidos_no_par[1:-1])

# 3. Tudo-ou-nada: ou grade completa, ou stand-by inteiro — nunca parcial
for par in selecionados_modo_tudo_ou_nada:
    assert all(item["qt_liquida"] == item["qt_solicitada"] for item in itens[par])

# 4. Orçamento de adição e de corte nunca excedem o limite, cada um isoladamente,
#    acumulados ao longo de N chamadas simuladas (não só uma)
assert total_adicionado_ao_longo_das_chamadas[pedido] <= (total_original[pedido] * 5) // 100
assert total_cortado_ao_longo_das_chamadas[pedido] <= (total_original[pedido] * 5) // 100
# E nunca "adicionado - cortado <= limite" no lugar dos dois separados (ALOC-08)

# 5. Soma das partes do rateio financeiro bate exatamente com o total do produto/pedido
assert sum(item["vl_liquido"] for item in itens[par]) == valor_total_esperado(par)

# 6. Mínimo viável: nenhum produto do pedido fica zerado só porque outro produto
#    do mesmo pedido consumiu o orçamento primeiro (dado orçamento suficiente para
#    o mínimo de todos, mas insuficiente para os extras de todos)
assert all(qtd_alocada(produto) > 0 for produto in produtos_do_pedido_com_estoque_minimo)

# 7. Determinismo: mesmo snapshot em qualquer ordem de entrada, mesmo conjunto `selecionados`
assert processar_pedidos(shuffle(dados), ...)["selecionados"] == processar_pedidos(dados, ...)["selecionados"]
```

### Fixtures/factories necessárias (Wave 0)

- [ ] Factory de item de grade (`_item(...)`, já existe em `test_pedidos_motor.py` — estender para aceitar `nr_pedido`/`cd_prod_cor` múltiplos por chamada, e opcionalmente `total_original_pedido`/`consumido_previo`)
- [ ] Factory de "pedido completo" (múltiplos produtos, mesmo `nr_pedido`) — não existe hoje, os testes atuais são de 1 produto por vez
- [ ] Fixture do cenário numérico ditado pela mantenedora (cliente A 10pç/1000pç-R$50k, cliente B 15pç/50pç-R$1k, estoque 20) — nomear como teste de regressão explícito, não genérico
- [ ] Helper de "simular N execuções sequenciais" (chama `processar_pedidos` repetidamente passando o `consumido_previo` da chamada anterior) — usado pelo teste de ALOC-09
- [ ] `test_pedidos_motor_properties.py` — esqueleto vazio nesta fase (sem `hypothesis` como dependência ainda), só a estrutura de arquivo e os `assert` de exemplo acima documentados como "candidatos a Hypothesis na Phase 19"

### Wave 0 Gaps
- [ ] `app/tests/factories_motor.py` (ou nome equivalente) — fixtures de pedido completo/execuções sequenciais
- [ ] Extração de `_allocate` para módulo público testável isoladamente (novo arquivo de teste do rateio)
- [ ] Nenhuma instalação de framework nova — `pytest` já cobre tudo nesta fase; `hypothesis` é Phase 19

---

## Security Domain

Este é um subsistema de domínio puro sem superfície de rede, autenticação ou criptografia — a maior parte das categorias ASVS não se aplica.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Não | Fora do domínio — motor não autentica |
| V3 Session Management | Não | Idem |
| V4 Access Control | Não | Idem — controle de acesso é da camada de rotas, já existente e inalterada |
| V5 Input Validation | Parcial | O domínio já assume dados pré-validados pela camada de ingestão/aplicação; esta fase não deve adicionar validação de schema (é responsabilidade de outra camada) — mas deve manter defesas já existentes contra dados malformados (`get_tamanho_idx` devolvendo 999 para tamanho desconhecido, em vez de lançar exceção) |
| V6 Cryptography | Não | Nenhum dado sensível é hasheado/criptografado nesta fase (o hash do plano é `processing/domain.py`, fora de escopo) |

### Known Threat Patterns for este domínio

Não aplicável no sentido STRIDE clássico (sem rede/autenticação) — o "risco" real aqui é de **integridade de negócio** (alocação incorreta), coberto extensivamente pelas seções de Pitfalls e Validação acima, não por controles de segurança tradicionais.

---

## Riscos concretos de execução

1. **Mudar a assinatura de `adequar_grade_produto` silenciosamente quebra 2 testes fora do arquivo principal, sem nenhum aviso até rodar a suíte completa.** `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` fixam a assinatura atual via `monkeypatch.setattr`. **Prova a acrescentar:** rodar a suíte completa (não só `test_pedidos_motor.py`) a cada tarefa da Onda 2, não só no gate final — o custo de descobrir a quebra cedo é muito menor que no merge.

2. **A inversão de loop (pedido-externo) pode silenciosamente mudar QUEM ganha a disputa se a implementação decrementar o estoque em um dict compartilhado por referência em vez de reconstruir por produto.** O desenho atual usa `estoque_local = dict(estoque_por_produto[cd_prod_cor])` — uma cópia por produto. Se a reescrita passar a mutar `estoque_por_produto[cd_prod_cor]` diretamente (sem cópia) e o loop externo mudar de produto para pedido, um bug sutil de aliasing pode fazer dois pedidos "verem" o estoque de formas inconsistentes. **Prova a acrescentar:** teste de paridade explícito entre a implementação nova (pedido-externo) e uma implementação de referência simples (a atual, produto-externo) sobre o MESMO snapshot com 3+ pedidos disputando o mesmo produto — exatamente o padrão que `test_pedidos_motor_performance_024.py` já usa para `_indexar_estoque_por_produto` (comparar otimizado vs. referência), aplicado agora à alocação inteira.

3. **O ledger de orçamento pode "parecer" correto testando só 1 execução, mas violar ALOC-09 (acumulado entre execuções) sem nenhum teste unitário isolado acusando** — é o Pitfall 3 do `PITFALLS.md`, e a mitigação (teste de múltiplas chamadas sequenciais) precisa nascer na MESMA tarefa que introduz o ledger, não ser adiado para "depois, na fase de validação" — se for adiado, o risco é a Onda 2 ser dada como "pronta" com um teste que só cobre uma chamada.

4. **A guarda contra OR zerada (ALOC-05, Phase 13) e o novo ledger de orçamento (ALOC-07/10, esta fase) podem interagir de forma não óbvia**: se o `floor` do orçamento de corte permitir `qt_liquida == 0` para um produto específico (ex. orçamento de corte do pedido é grande o suficiente para zerar um produto barato inteiro em troca de manter os caros), a guarda "nunca persistir OR com soma zero" (Phase 13, no nível do PAR/produto) pode não ser suficiente se a regra pretendida for "nunca zerar um PRODUTO inteiro", não só "nunca zerar a OR inteira". **Risco a levantar explicitamente com a mantenedora antes de fechar o plano**, não assumir — ver Open Questions.

5. **Manter a mecânica de "extremos" para extras (placeholder até Phase 18) pode ser confundido com "ALOC-11 já implementado"** por quem revisar o código depois — nomear a função/constante de forma que deixe explícito que é provisório (ex. `_distribuir_extras_extremos_PLACEHOLDER_ALOC11` ou comentário de bloco explícito) evita que a Phase 18 (ou uma revisão futura) assuma que a distribuição por sobra já existe.

---

## Package Legitimacy Audit

Esta fase **não instala nenhum pacote novo** — toda a implementação usa stdlib do Python 3.13 já disponível (`dataclasses`, `math`/aritmética inteira, `decimal.Decimal` já em uso via `edicao_grade.py`) e o utilitário Hamilton já existente no repositório. `hypothesis` foi aprovada pela mantenedora como dev-dependency (ver `STATE.md` § Decisions), mas sua instalação e uso pertencem à **Phase 19** — nesta fase o objetivo é só deixar o domínio testável para ela (funções puras, deterministas, aritmética inteira), não adicionar a dependência.

**Packages removidos por veredito `[SLOP]`:** nenhum (nenhum pacote avaliado).
**Packages sinalizados como suspeitos `[SUS]`:** nenhum.

## Assumptions Log

| # | Claim | Seção | Risco se errado |
|---|-------|-------|------------------|
| A1 | A inversão de loop (pedido-externo/produto-interno) não muda o resultado de alocação de estoque, porque a partição por produto já é independente de ordem de visita | Recomendação de desenho | Se houver algum acoplamento entre produtos não identificado nesta pesquisa (ex. um limite agregado por CD além do canal, não visto no código lido), a inversão poderia mudar quem ganha a disputa — mitigar com o teste de paridade do Risco 2 acima antes de confiar na inversão |
| A2 | Os únicos dois arquivos de teste fora de `test_pedidos_motor.py` que quebram por mudança de assinatura são `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` | Impacto na suíte existente | Baseado em grep por `motor_adequacao` em `app/tests/` — se outro arquivo importar indiretamente via `service.py` e monkeypatchar uma função interna do motor sem que o grep tenha pego o padrão exato, pode haver uma terceira quebra silenciosa; rodar a suíte completa cedo (Risco 1) cobre esta lacuna |
| A3 | "Mínimo viável antes de extras" (ALOC-10) deve ser implementado como duas passadas explícitas (corte então adição), ordenadas por corte-necessário-crescente na 1ª e preço-unitário-decrescente na 2ª | Recomendação de desenho | É a leitura da pesquisa anterior (`STACK.md`/CONTEXT discretion note) traduzida em desenho concreto — a mantenedora não travou a ordem exata dentro do pedido, então é discretion; se o planejador escolher outra ordem, precisa justificar por que maximiza produtos atendidos (critério que a mantenedora deu como objetivo) |

**Se esta tabela parecer pequena:** é porque a maior parte das regras desta fase já veio como decisão travada e verificada em código pela pesquisa anterior (`ARCHITECTURE.md`/`PITFALLS.md`/`STACK.md`), não como hipótese nova desta pesquisa.

## Open Questions

1. **A guarda contra zero (ALOC-05, Phase 13) cobre "produto zerado dentro de um pedido com outros produtos atendidos" ou só "OR inteira com soma zero"?**
   - O que se sabe: o CONTEXT desta fase diz para assumir que a guarda "existe no orquestrador" e não reimplementá-la; o `PITFALLS.md` (Pitfall 4) descreve a guarda original como "nunca persistir `qt_liquida` total == 0 quando o status é Gerar OR" — no nível do PAR (pedido, produto), não do pedido inteiro.
   - O que fica pouco claro: se o ledger de orçamento (ALOC-10, "mínimo viável") já garante que nenhum produto fica zerado por falta de orçamento, então a guarda de zero da Phase 13 e o mínimo viável desta fase são complementares (uma pega zero por falta de ESTOQUE, a outra por falta de ORÇAMENTO) — mas se a Phase 13 rodar DEPOIS desta fase, ela precisa saber que existe uma segunda razão possível para `qt_liquida == 0` a cobrir.
   - Recomendação: o plano desta fase deve deixar explícito, no código e na comunicação com a Phase 13 (via `STATE.md`), que "mínimo viável" é uma garantia de **orçamento**, não de **estoque** — os dois continuam podendo zerar um produto por razões diferentes, e cada guarda cobre a sua.

2. **A ordem de desempate (`nr_pedido` como último campo de `_prioridade`) deve ser crescente ou decrescente?**
   - O que se sabe: o CONTEXT só exige que seja "total e explícito", sem especificar direção.
   - O que fica pouco claro: como `nr_pedido` não tem significado de negócio na ordenação (não é "mais antigo primeiro"), qualquer direção é igualmente correta para o requisito — mas a direção escolhida vira parte do contrato de determinismo (muda o `plan_hash` uma única vez, na migração).
   - Recomendação: escolher crescente (mais simples, sem necessidade de negar) e documentar como decisão arbitrária mas fixa.

3. **O parâmetro de orçamento (`total_original`/`consumido_previo`) deve ser obrigatório ou ter default que preserva o comportamento de "execução única" (equivalente a `consumido_previo=0`)?**
   - O que se sabe: a Phase 15 é quem liga a fonte real de dados; esta fase só define a forma do parâmetro.
   - O que fica pouco claro: se o parâmetro for obrigatório sem default, todo teste (e todo chamador futuro) precisa fornecê-lo explicitamente — mais verboso, mas mais seguro contra o "esquecimento" que é exatamente o Pitfall 3. Se tiver default `0`, fica mais ergonômico para testes simples, mas um chamador real que esquecer de passar o acumulado real cai silenciosamente no bug do double-spend.
   - Recomendação: **sem default** — obrigar o chamador (inclusive os testes) a decidir explicitamente é a defesa mais barata contra o pitfall mais perigoso do milestone.

## Environment Availability

SKIPPED — fase de domínio puro, sem dependências externas além de stdlib Python 3.13 e do próprio código do repositório já disponível. `uv run pytest` local falha no Windows por `pyicu` (nota já registrada no CONTEXT) — usar Docker (`docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`), que já está confirmado disponível pelo ambiente de desenvolvimento do projeto.

## Sources

### Primary (HIGH confidence — leitura direta de código nesta sessão)
- `app/modules/pedidos/domain/motor_adequacao.py` (lido integralmente, 474 linhas)
- `app/modules/pedidos/domain/value_objects.py` (lido integralmente)
- `app/modules/pedidos/domain/edicao_grade.py` (lido integralmente — `_allocate`, `montar_grade_atualizada`)
- `app/tests/test_pedidos_motor.py` (lido integralmente, 20 testes)
- `app/tests/test_pedidos_motor_performance_024.py` (lido integralmente)
- `app/tests/test_pedidos_processing_cancellation_024.py` (lido nos trechos relevantes ao motor)
- `app/modules/pedidos/service.py` (confirmado shim de re-export)
- `app/modules/pedidos/processing/domain.py` (trecho `build_pair_payload`/`build_processing_plan`, linhas 598-716 — confirma que `processar_pedidos` só é chamado a partir daqui, com `resultados_apenas_selecionados=True`)
- `pyproject.toml` (confirma ausência de `hypothesis`, Python `>=3.13`, dev group atual)
- `.planning/phases/14-motor-de-aloca-o-puro-regras-de-neg-cio/14-CONTEXT.md`
- `.planning/PROJECT.md` § "Regras de negócio da alocação"
- `.planning/STATE.md` § Decisions v1.3
- `.planning/REQUIREMENTS.md` § v1.3

### Secondary (pesquisa já feita nesta sessão pelo milestone, reaproveitada por referência)
- `.planning/research/v1.3/ARCHITECTURE.md`
- `.planning/research/v1.3/PITFALLS.md`
- `.planning/research/v1.3/STACK.md`
- `.planning/codebase/TESTING.md`
- `.planning/codebase/CONVENTIONS.md`

## Metadata

**Confidence breakdown:**
- Mapa cirúrgico de funções: HIGH — leitura linha a linha do arquivo atual
- Impacto na suíte: HIGH para os testes lidos integralmente; MEDIUM para a extrapolação de "quais asserções especificamente mudam" já que a implementação final ainda não existe
- Divisão em planos: MEDIUM — é recomendação de organização (parte é Claude's Discretion por definição do CONTEXT), não fato verificável
- Validation Architecture: HIGH para o framework/comandos (verificados em `pyproject.toml`/CONTEXT); MEDIUM para as invariantes específicas (traduzidas do `PITFALLS.md`, ainda não testadas nesta sessão)

**Research date:** 2026-08-14
**Valid until:** válido até a reescrita começar (o mapa de linhas fica desatualizado assim que o primeiro plano editar `motor_adequacao.py`) — reconferir números de linha se o plano for retomado depois de qualquer commit nesse arquivo.

---
*Research for: Phase 14 — Motor de alocação puro (regras de negócio)*
*Researched: 2026-08-14*
