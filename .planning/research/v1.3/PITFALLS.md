# Pitfalls Research — Motor de Alocação com Restrições Acopladas (v1.3)

**Domain:** Reescrita do núcleo de decisão de um motor de alocação de estoque em produção (greedy por prioridade, orçamento ±5% compartilhado, furo de grade dependente de estoque decrementado, duas passadas)
**Researched:** 2026-08-14
**Confidence:** HIGH para os pontos ancorados no código lido (`motor_adequacao.py`, `processing/domain.py`, `processing/application/service.py`); MEDIUM para os padrões gerais de indústria (greedy allocation, largest remainder, property-based testing) — verificados por busca, não por Context7 (não há biblioteca única "motor de alocação").

Arquivos lidos para fundamentar este documento: `.planning/PROJECT.md`, `app/modules/pedidos/domain/motor_adequacao.py`, `app/modules/pedidos/processing/domain.py`, `app/modules/pedidos/processing/application/service.py`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/TESTING.md`, `.planning/REQUIREMENTS.md`.

---

## Critical Pitfalls

### Pitfall 1: Estado mutável (orçamento) vazando entre iterações de produtos do mesmo pedido

**What goes wrong:**
Hoje `adequar_grade_produto` calcula `total_grade`, `limite_falta` e `orcamento_aumento` **dentro da própria função**, por par (pedido, produto) — cada chamada é hermética (linhas 134-136 de `motor_adequacao.py`). A regra nova exige o oposto: o orçamento ±5% é medido sobre o **pedido completo do cliente**, atravessando produtos, e adição/corte **não se compensam**. Isso obriga a introduzir um objeto de orçamento que é **criado uma vez por pedido** e **mutado a cada produto processado** dentro do mesmo pedido. Os bugs clássicos desse tipo de refactor:
- Uma função "parece pura" (recebe `itens`, devolve `itens` novos) mas na verdade lê/escreve um `orcamento` passado por referência — se um caminho de código (ex. crédito bloqueado, canal desconhecido, cancelamento cooperativo via `cancel_token`) pular a chamada sem decrementar/creditar o orçamento correspondente, o estado fica dessincronizado do que realmente foi decidido.
- Um refactor que reordena os produtos processados (ex.: passar de "iterar por produto, depois por pedido" para "iterar por pedido, depois por produto" — inversão que a regra nova praticamente exige, já que hoje o laço externo é por `cd_prod_cor` e o orçamento por pedido precisa do laço externo por pedido) muda **qual produto consome o orçamento primeiro**, e portanto muda silenciosamente quais tamanhos recebem a folga de 5% — sem nenhum teste acusando, porque cada teste unitário de produto isolado continua "passando".
- Fácil confundir "copiar o orçamento para não vazar entre pedidos diferentes" com "copiar o orçamento a cada produto do mesmo pedido" — o segundo é o bug que reseta o limite e permite estourar os 5% várias vezes dentro do mesmo pedido.

**Why it happens:**
O motor atual foi desenhado com a garantia "cada chamada de `adequar_grade_produto` é independente" (comentário implícito no design: `itens = [dict(i) for i in itens]` sempre copia). Ao introduzir estado compartilhado entre chamadas, essa garantia deixa de valer silenciosamente — nada no assinatura de tipos aponta que agora existe uma dependência de ordem entre chamadas do mesmo pedido.

**How to avoid:**
1. Modele o orçamento como um objeto explícito e nomeado (`OrcamentoPedido` — dataclass mutável ou com métodos `consumir_adicao(qtd)`/`consumir_corte(qtd)` que retornam o quanto efetivamente coube), nunca como inteiros soltos passados por parâmetro em múltiplos lugares.
2. Documente e **fixe por código** a ordem de iteração dos produtos dentro de um mesmo pedido (ex.: `sorted(produtos_do_pedido)` por `cd_prod_cor`) — não confie na ordem de chegada do snapshot. Trate a ordem como parte do contrato do motor, não como acidente de implementação.
3. Escreva um teste de "reordenação de laços": mesmo pedido, mesmos produtos, dataset construído em ordens diferentes (permutações da lista de produtos) → resultado idêntico (mesmas quantidades, mesmo orçamento restante). Se o teste variar o resultado, a ordem virou regra de negócio não-declarada — decida explicitamente qual critério de desempate manda (ex. maior valor primeiro, depois `cd_prod_cor`) e teste esse critério por nome.
4. Nunca deixe caminhos de saída antecipada (crédito bloqueado, canal desconhecido, cancelamento) tocarem o orçamento — o orçamento só é consumido no caminho que efetivamente decide quantidade.

**Warning signs:**
- Um teste que passa quando roda sozinho mas falha quando outro teste do mesmo pedido roda antes (indica objeto de orçamento reaproveitado entre testes/execuções).
- Resultado do motor muda ao trocar apenas a ordem das chaves de um dicionário de entrada (nenhuma regra de negócio deveria depender disso).

**Phase to address:** Fase de reescrita do núcleo de orçamento/prioridade (o coração do milestone) — antes de qualquer feature de UI. É a fundação de que todo o resto depende.

---

### Pitfall 2: Starvation por prioridade global de valor — real neste desenho, mitigação é observabilidade, não engenharia silenciosa

**What goes wrong:**
A regra 4 (prioridade por valor) é greedy puro: em disputa pela mesma peça, quem tem pedido de maior valor ganha sempre. Combinado com o fato de que pares em stand-by **voltam para a fila e reaparecem na próxima execução** (comportamento aceito, fora de escopo mudar — ver `.planning/PROJECT.md` § Out of Scope), um cliente pequeno cujo pedido nunca tem o maior valor pode, em tese, nunca vencer a disputa por um SKU popular — e isso se repete a cada ciclo de 2h indefinidamente, não é hipotético: é a consequência matemática direta do greedy sem nenhum mecanismo de aging.

**Why it happens:**
Greedy por score global não tem memória entre execuções: cada rodada recalcula a prioridade do zero a partir dos dados atuais, então "quantas vezes esse par já perdeu" não influencia a próxima decisão. Isso é exatamente o que a regra 4 pede (prioridade por valor do pedido, não por "quem está esperando há mais tempo") — então **a starvation aqui não é um bug do motor, é uma consequência aceita (ou não avaliada) da regra de negócio tal como ditada**.

**How to avoid:**
- **Não implemente aging/reserva mínima por conta própria** — isso contradiz a regra 4 tal como escrita ("na disputa pela mesma peça ganha o cliente cujo pedido total vale mais", sem exceção declarada). Mudar isso é decisão de negócio, não de engenharia; implementar uma salvaguarda "por baixo dos panos" seria dar ao motor um comportamento que a mantenedora e a usuária não pediram e não vão entender ao auditar por que um pedido pequeno foi atendido "fora de prioridade".
- **Instrumente para detectar, não para corrigir sozinho:** registre, por par (pedido, produto) em stand-by, um contador de "execuções consecutivas em stand-by por falta de estoque" (não confundir com "sem crédito", que é outro motivo). Exponha isso na visibilidade de stand-by já prevista no milestone (tag "aguardando estoque") e, se o contador passar de um limiar (ex. N execuções seguidas), eleve isso a um sinal para a usuária decidir — é uma decisão de produto/negócio, não uma correção automática no motor.
- Se e quando a mantenedora quiser mitigar, as opções documentadas na literatura de scheduling são: aging (score cresce com o tempo de espera), reserva mínima de estoque por rodada para os N pares mais antigos, ou rodadas alternadas (round-robin entre prioridade e antiguidade) — mas qualquer uma delas **muda o resultado financeiro esperado pela regra 1** (maximizar faturamento) e precisa ser negociada, não assumida.

**Warning signs:**
- Um mesmo par (pedido, produto) aparecendo em `preteridos` por 3+ execuções seguidas enquanto o estoque daquele produto não chega a zero (sinal de que outros pedidos de maior valor estão consistentemente na frente, não que falta estoque em absoluto).

**Phase to address:** Não é uma fase de "correção" — é uma fase de instrumentação/observabilidade (contador + exposição na UI de stand-by) que deveria acompanhar a fase que entrega a visibilidade de stand-by já planejada no milestone. Trate como um item de decisão a levantar com a mantenedora antes de fechar o milestone, não como uma feature nova a implementar sem aprovação.

---

### Pitfall 3: Double-spend do orçamento ±5% entre execuções (o par bug mais perigoso do milestone)

**What goes wrong:**
O snapshot de pedidos pendentes usado em cada execução **exclui** os pares (pedido, produto) já processados em execuções anteriores (`ja_processados` em `agrupar_por_produto`, linha 76-79 de `motor_adequacao.py`; a mesma lógica de exclusão por par aparece no nível de `processing/domain.py` via `pending_items`/`ProcessingPlannerSource`). Isso é correto para o que já existe (não reprocessar o que já virou OR). O risco introduzido pela regra nova: se o cálculo de "quantidade total do pedido completo do cliente" (a base sobre a qual os 5% são medidos) for recalculado **a cada execução usando apenas os itens ainda pendentes daquele pedido**, o denominador encolhe a cada rodada que aprova produtos — e o cliente ganha uma folga de 5% **nova, sobre uma base menor**, a cada execução, ao invés de consumir de um orçamento fixo. Ao longo de M execuções, o total efetivamente adicionado/cortado pode superar em muito o 5%/5% pretendido sobre o pedido original — o motor "parece" respeitar a regra em cada rodada isolada, mas viola a regra no agregado, que é exatamente o que a regra 2 pede ("medidos sobre a quantidade total do pedido completo").

**Why it happens:**
É a mesma classe de erro que rate-limiting mal feito: recalcular "5% do que resta" repetidamente é logicamente diferente de "5% do total original, e o que já foi gasto fica registrado". A diferença só aparece quando se olha o histórico de execuções, não uma execução isolada — por isso escapa fácil de testes unitários que só olham "uma chamada do motor de cada vez".

**How to avoid:**
1. **O denominador do ±5% precisa ser um valor imutável por pedido**, fixado no momento em que o pedido é visto pela primeira vez pelo sistema (soma da quantidade de TODOS os produtos daquele pedido, incluindo os que já viraram OR em execuções anteriores) — não a soma dos itens ainda pendentes na execução corrente. Se a fonte de dados (ingestão full refresh do Databricks) não preservar isso de forma estável, é preciso persistir esse total na primeira vez que o pedido aparece (ex. numa tabela de "orçamento por pedido" ou coluna calculada gravada junto com `pedidos_processados`).
2. **O orçamento consumido precisa ser um contador cumulativo, lido do banco a cada execução**, não recalculado do zero. Padrão de indústria equivalente: "quantidade já entregue vs. quantidade do pedido original" em ERPs de fulfillment parcial (nunca comparam contra "quantidade restante"), ou um token bucket que é decrementado e persistido, nunca resetado. Concretamente: antes de decidir quanto adicionar/cortar num produto do pedido nesta execução, consulte quanto já foi adicionado/cortado (separadamente) nas execuções anteriores para esse mesmo `nr_pedido` (via soma sobre `ordens_reserva` ou uma tabela de orçamento dedicada), subtraia do limite ±5% do total original, e só o que sobrar é o orçamento disponível nesta rodada.
3. Trate adição e corte como dois contadores cumulativos independentes (nunca subtraia um do outro) — é a mesma regra "não se compensam" aplicada agora também através do tempo, não só através de produtos.
4. Teste de regressão específico: simular 3+ execuções sequenciais do mesmo pedido (parte dos produtos aprovados a cada rodada, estoque mudando entre rodadas) e assertar que a soma de adições e a soma de cortes ao longo de TODAS as execuções nunca excede os limites calculados sobre o total original do pedido. Isso é um caso natural para **Hypothesis stateful testing** (`RuleBasedStateMachine` com uma regra "processar próxima execução" e um `@invariant()` que verifica os dois acumuladores) — ver Pitfall 7.

**Warning signs:**
- Qualquer lugar do código que computa `total_grade = sum(...)` a partir de `itens` que já vieram filtrados por "ainda pendente" — esse é o padrão exato do bug.
- Ausência de uma tabela/coluna que registre "quanto já foi adicionado" e "quanto já foi cortado" por pedido, persistente entre execuções.

**Phase to address:** Mesma fase do núcleo de orçamento/prioridade (Pitfall 1) — é a mesma peça de domínio, mas a mitigação aqui é sobretudo de **modelagem de dados** (persistência do total original e dos acumulados), então também toca a camada de aplicação/infraestrutura (`processing/`), não só o domínio puro.

---

### Pitfall 4: Aritmética de arredondamento — o bug do `ceil` já confirmado, e onde mais ele reaparece

**What goes wrong (bug já existente, confirmado no código):**
`motor_adequacao.py` linha 135: `limite_falta = math.ceil(total_grade * tolerancia)`. Para `total_grade=1`, `tolerancia=0.05`: `ceil(0.05) == 1`. A checagem é `status_grade = "Pedido em Stand By" if falta_total > limite_falta else "Gerar OR"` (linha 145). Se `falta_total` (o total que falta) também for `1` (estoque zero), `1 > 1` é `False` → o par vira **"Gerar OR"** mesmo com zero em estoque. Na sequência, `nova_qtd = min(qtd_antiga, est_disp) = min(1, 0) = 0` — grava uma OR de **0 peça**. A causa raiz é dupla: (a) `ceil` num limite de tolerância que deveria ser um teto estrito arredonda para cima justamente no caso em que o pedido é pequeno demais para ter qualquer tolerância real, e (b) a comparação `>` (estrita) permite que "faltar exatamente o limite" ainda gere OR, quando semanticamente faltar deveria bloquear a partir do limite, não depois dele. A correção já delineada no PROJECT.md (`ceil` → `floor` + guarda explícita no orquestrador) resolve o primeiro; a guarda explícita (nunca persistir `qt_liquida` total == 0 quando o status é "Gerar OR") resolve definitivamente, independente da fórmula.

**Why it happens (padrão geral, não específico deste bug):**
Para totais pequenos (aqui, `total_grade < 20`), `ceil(total * 0.05)` é **sempre ≥ 1** — ou seja, a tolerância deixa de ser "5% proporcional" e vira "1 unidade fixa de folga", desproporcional ao tamanho real do pedido. Esse é o padrão clássico de "arredondamento domina a porcentagem em N pequeno": qualquer sistema que aplica uma tolerância percentual sobre quantidades inteiras pequenas precisa de uma regra explícita para o caso `total < 1/tolerancia` (aqui, total < 20), e não pode confiar cegamente na fórmula genérica.

**Other places rounding bites (a mapear nesta reescrita):**
1. **Rateio das peças extras** (regra "peças extras entram onde há mais sobra, sem posição fixa"): se o orçamento de adição (ex. `floor(total*0.05) = 7` peças) precisa ser distribuído entre múltiplos tamanhos/produtos por "quem tem mais sobra", um rateio ingênuo (dividir igualmente e arredondar por item) pode fazer a soma das partes não bater com o total do orçamento (sobra ou falta 1-2 peças por arredondamento em cada tamanho). Use o **método do maior resto** (largest remainder / método de Hamilton): aloque o piso (`floor`) da parcela de cada tamanho, depois distribua as unidades restantes (a diferença entre o orçamento total e a soma dos pisos) para os tamanhos com maior parte fracionária restante, em ordem — isso garante por construção que a soma bate exatamente com o orçamento, sem sobra nem furo.
2. **Rateio financeiro (`vl_liquido`)**: o código já recalcula `preco_unit = val_antigo / qtd_antiga` e depois `round(nova_qtd * preco_unit, 2)` por item (linhas 154 e 173). Somar os `vl_liquido` de vários itens após arredondamento individual pode não bater com `nova_qtd_total * preco_unit` calculado de uma vez só (drift de centavos por item, mais itens = mais drift acumulado). Se o total financeiro do pedido/OR precisar bater exatamente com a soma dos itens (para conciliação com o Linx/ERP), aplique a mesma técnica de maior resto sobre os centavos, ou compute o valor total primeiro e distribua o resto para o último item / o item de maior valor, nunca deixe cada item arredondar independentemente sem reconciliação.
3. **Float vs. Decimal**: `tolerancia: float = 0.05` e a matemática de `total_grade * tolerancia` em float pode ter imprecisão de representação (`0.05` não é exato em binário). Para totais que são múltiplos exatos de 20 (onde `total*0.05` deveria cair em um inteiro exato), o resultado em float pode vir ligeiramente abaixo (ex. `19.999999999998` em vez de `20.0`), fazendo `math.floor` cortar 1 unidade a menos do que deveria. Prefira aritmética inteira (`(total * 5) // 100` para floor, `-(-total * 5 // 100)` para ceil) ou `Decimal`/`Fraction` para tolerância — nunca multiplique inteiros por um float de porcentagem quando o resultado alimenta um `floor`/`ceil` que decide um valor de negócio.

**How to avoid (resumo acionável):**
- Trocar `ceil` por `floor` no limite de estoque faltante **e** adicionar uma guarda explícita e independente: nunca persistir um item com `status_item == "Gerar OR"` e soma de `qt_liquida == 0` — a guarda deve existir mesmo que a fórmula de tolerância mude de novo no futuro.
- Fazer toda a matemática de tolerância com inteiros (multiplicação e divisão inteira) em vez de `float * 0.05`.
- Implementar o rateio de extras e a reconciliação financeira com o método do maior resto, testado com uma asserção de invariante: soma das partes == total esperado, sempre.

**Warning signs:**
- Qualquer `assert`/teste que passe usando `total_grade` grande (ex. 100+) mas nunca teste `total_grade` pequeno (1-19) — é exatamente a faixa onde o bug vive.
- `sum(item["qt_liquida"] for item in itens) != quantidade_esperada` em qualquer ponto do pipeline de rateio.
- `sum(item["vl_liquido"] for item in itens)` divergindo do valor total do pedido em mais de 1 centavo por item envolvido.

**Phase to address:** A guarda contra OR zerada (`ceil`→`floor` + guarda explícita) é citada no PROJECT.md como item isolado do milestone (fecha WR-05) — trate como fase própria e cedo no roadmap, pois é a correção mais barata e mais urgente (bug ativo em produção). O rateio de extras com maior resto entra na fase de "peças extras nos extremos com mais sobra".

---

### Pitfall 5: Não-determinismo silencioso — empates de prioridade resolvidos por ordem não garantida

**What goes wrong:**
O plano gerado é hasheado (`plan_hash` em `processing/domain.py`, via `_sha256`/`StreamingPlanBuilder`) e comparado após crash (`fingerprint_pair_source`, usado para "detectar plano obsoleto após crash" — comentário na linha 537). Esse desenho **pressupõe que, para o mesmo snapshot de entrada, o motor sempre produz o mesmo plano** — senão, um retry após crash pode legitimamente concluir "o snapshot não mudou" mas ainda assim aplicar um plano diferente do que teria sido decidido da primeira vez, porque o motor em si é não-determinístico, não porque os dados mudaram. As fontes de não-determinismo mais prováveis nesta reescrita:
1. **Desempate de prioridade dependente da ordem de chegada do snapshot.** Em `_processar_pedidos_canal`, `_prioridade(nr)` retorna `(score, len(prods_disp))` (linha 396) e `sorted(..., key=_prioridade, reverse=True)` (linha 399) é estável — ou seja, em caso de empate exato no `(score, len)`, a ordem final é a ordem de **inserção em `todos_pedidos_flat`**, que por sua vez segue a ordem de iteração de `dados` (a lista vinda do banco). Se a consulta que produz esse snapshot não tiver `ORDER BY` explícito e estável, a ordem de retorno do Postgres não é garantida entre execuções (mesmo sem nenhuma mudança de dado) — dois pedidos empatados em valor podem trocar de posição entre execuções, e a regra 4 (prioridade por valor) na prática se torna "prioridade por valor, com empate decidido por acaso".
2. **Iteração de `set`/`dict` com hash de string.** `produtos: set[str]` (linha 364-369) e `sem_credito_nrs` são sets — a ordem de iteração de um `set` de strings depende do hash, que em Python é aleatorizado por processo (`PYTHONHASHSEED`) a menos que fixado. Hoje isso não vaza para o resultado porque os únicos usos são para construir dicts/comparações de pertencimento (`in`), não para determinar ordem final — mas qualquer código novo que **itere diretamente sobre um desses sets para decidir algo posicional** (ex. "em qual ordem processar os produtos deste pedido para consumir o orçamento compartilhado", ver Pitfall 1) herda esse não-determinismo sem aviso.
3. **Soma de ponto flutuante sensível à ordem de iteração.** `score = sum(i[campo_score] for i in itens if i["cd_prod_cor"] in prods_disp)` (linha 395) itera sobre uma lista (ordem estável), mas se `campo_score` for `vl_liquido` armazenado como `float` (não confirmado no trecho lido — vale checar o schema), a soma pode variar no último bit dependendo da ordem dos itens, e em empates muito próximos isso pode flipar um `>` de comparação entre execuções idênticas.

**Why it happens:**
Determinismo não é uma propriedade que "sobra de graça" de código Python correto — precisa ser desenhada deliberadamente sempre que há ordenação com possibilidade de empate, e sets/dicts de string são uma armadilha recorrente porque "parecem" determinísticos (dict preserva ordem de inserção desde 3.7) mas sets não preservam nada, e a ordem de inserção em dicts ainda depende da ordem de um upstream não controlado (a query).

**How to avoid:**
1. Toda chave de ordenação (`sorted(..., key=...)`) que decide algo de negócio precisa ser uma **tupla total** (sem empates possíveis) — acrescente `nr_pedido` (ou outro identificador estável e único) como último critério de desempate: `(score, len(prods_disp), nr_pedido)`. Isso garante que, para o mesmo conjunto de dados, o resultado do `sorted` é sempre o mesmo, independente da ordem de chegada.
2. Nunca itere diretamente sobre um `set` para produzir uma ordem que afeta o resultado — sempre `sorted(meu_set)` antes de iterar quando a ordem importa (o código já faz isso em vários pontos: `estrutura.items()` não, mas `sorted(pedidos_do_produto.keys(), key=...)` sim — mantenha esse padrão na parte nova).
3. Considere `Decimal` para `vl_liquido`/scores monetários acumulados (evita a sensibilidade de ordem do float summation) — ou, no mínimo, garanta que a lista somada está sempre na mesma ordem (ex. ordenada por uma chave estável) antes do `sum`.
4. **Teste de determinismo dedicado:** rodar `processar_pedidos`/`build_processing_plan` N vezes (ex. 50-100) sobre o **mesmo** snapshot, embaralhando a ordem da lista de entrada e, se possível, rodando com `PYTHONHASHSEED` diferente a cada execução (variável de ambiente, não controlável em runtime — mas pode ser testado via subprocesso ou via `pytest-randomly`/execução repetida do CI com seeds diferentes), e assertar que `plan_hash` e o conjunto `selecionados` são idênticos em todas as execuções.

**Warning signs:**
- Uma chave de `sorted()` que pode empatar (dois pedidos com exatamente o mesmo valor e mesma quantidade de produtos disponíveis) sem um desempate final por identificador único.
- `plan_hash` diferente para duas execuções do worker sobre o mesmo `job_id` reclamado após crash, sem nenhuma mudança no snapshot subjacente (deveria ser impossível e, se acontecer, é sintoma direto deste pitfall).

**Phase to address:** Mesma fase do núcleo de prioridade/orçamento — o determinismo é parte da correção da regra 4, não um extra de qualidade. Vale também um item específico na fase de testes (Pitfall 7) dedicado a fixar e testar o desempate total.

---

### Pitfall 6: Regressão silenciosa ao trocar o motor em produção — sem shadow run, o primeiro sinal é a reclamação do cliente errado

**What goes wrong:**
Trocar o núcleo de decisão de um motor que já roda em produção, sem comparação prévia, corre o risco clássico de "parece certo nos testes, quebra em produção" porque a superfície de combinações reais (grades variadas, prioridades próximas, estoques parciais) é maior do que qualquer suíte de exemplos manuais cobre. Como o resultado de negócio (quem recebe OR e quanto) impacta diretamente faturamento e a relação com clientes do automation, uma regressão aqui não é um bug técnico silencioso — é um cliente que deveria ter sido atendido e não foi, sem que ninguém saiba dizer por quê.

**Why it happens:**
O motor de hoje não registra o "porquê" da decisão em nível fino — os motivos existentes são genéricos (`"Preterido: estoque insuficiente para todos os pedidos do produto"`, `"Aguardando liberação de crédito"`). Com quatro regras novas se combinando (crédito, furo de grade, orçamento, prioridade, tudo-ou-nada), o número de razões possíveis para "esse par não foi atendido" cresce, e sem um motivo específico por regra, tanto o suporte quanto o dev não conseguem reconstruir a decisão depois do fato — e sem essa reconstrução, uma regressão de comportamento é indistinguível de "é assim que a regra nova funciona mesmo".

**How to avoid:**
1. **Motivo específico por regra, não genérico.** Cada bloqueio deve carregar qual regra o causou e, quando fizer sentido, contra quem/o quê ele perdeu: `"Furo de grade: tamanho M sem estoque reservável"`, `"Orçamento de corte esgotado: pedido já usou 5% de corte em execução anterior"`, `"Perdeu a peça do tamanho P para o pedido nº X (maior valor)"`, `"Sem adequação: grade completa não disponível — modo tudo-ou-nada"`. Isso não é só para debug — é o material que embasa a visibilidade de stand-by na UI já prevista no milestone (tag "aguardando estoque").
2. **Shadow run / dry run antes de ligar.** Dado o desenho já existente de "processing job" com plano hasheado e sem I/O externo no domínio puro, é barato rodar o motor novo em modo **plan-only** (o pipeline já suporta motor rodar sem aplicar — `_plan_once` monta o `PlanDraft` antes de `_apply_chunks` aplicar) sobre um ou mais ciclos reais de produção, sem nunca chamar `writer.apply_pairs`, e comparar o plano resultante com o que o motor atual geraria para o mesmo snapshot. Não precisa de infraestrutura nova pesada: um script/teste offline que carrega um snapshot real (sanitizado) e roda as duas versões do motor (antiga vs. nova) lado a lado, produzindo um diff categorizado.
3. **Diff categorizado, não diff bruto.** Comparar linha a linha vai gerar muito ruído (a reescrita *deveria* mudar comportamento — é o objetivo do milestone). O diff útil separa: (a) diferenças **explicadas** por uma das regras novas (ex. "esse par mudou porque agora furo de grade bloqueia, e antes não bloqueava" — esperado), de (b) diferenças **inesperadas** (mesmo cenário, nenhuma regra nova deveria ter mudado o resultado, mas mudou) — só a categoria (b) é regressão real e merece investigação antes de ligar.
4. Guarde o snapshot de comparação e o diff categorizado como evidência de validação pré-corte — é o equivalente a um "golden master" para este domínio, dado que a mantenedora precisa de algo simples e auditável (consistente com a preferência por soluções didáticas do projeto), não um sistema de shadow-traffic em produção real.

**Warning signs:**
- Ausência de qualquer comparação "antes vs. depois" sobre dados reais antes do merge/deploy da reescrita.
- Motivos de stand-by genéricos demais para a usuária explicar a um cliente específico por que ele não foi atendido.

**Phase to address:** Fase final antes de "ligar" o motor novo (troca do `criterio`/engine em produção) — depende de todas as regras (crédito, furo, orçamento, tudo-ou-nada, extras) já estarem implementadas. Trate como gate de saída do milestone, não como nice-to-have.

---

### Pitfall 7: Suíte de testes virando espelho da implementação — onde property-based testing (Hypothesis) realmente ajuda aqui

**What goes wrong:**
Testar um motor de alocação só com exemplos manuais tende a duas falhas opostas: (a) a suíte cobre só os casos que o autor já pensou (e o bug do `ceil`/`total=1` é a prova viva disso — não estava coberto), ou (b) os testes reimplementam a fórmula dentro do teste (`assert resultado == ceil(total*0.05)...`) e viram um espelho 1:1 do código, que "prova" a implementação contra si mesma e nunca pega um erro conceitual na fórmula.

**Why it happens:**
Motores de alocação com restrições acopladas (orçamento, furo de grade, prioridade, duas passadas) têm um espaço de combinações grande demais para enumerar à mão, mas têm **invariantes de negócio pequenas e estáveis** (nunca reservar mais que o estoque, nunca estourar o orçamento, nunca vender grade furada) que são exatamente o tipo de propriedade que Hypothesis foi desenhado para verificar — e o shrinking automático da biblioteca converge para o menor contra-exemplo (ex. teria encontrado `total=1` sozinho, sem alguém precisar pensar nesse caso).

**Verdict: SIM, vale a pena — com escopo deliberadamente pequeno.**
Hypothesis não está nas dependências hoje (`pyproject.toml` não lista `hypothesis`; confirmado por busca) — precisa ser adicionada como dev dependency (`uv add --group dev hypothesis`) e, dado que a mantenedora é dev iniciante e o projeto prioriza soluções simples e didáticas, a recomendação é:
- Um módulo **separado** e pequeno (ex. `app/tests/test_pedidos_motor_properties.py`), não misturado com os testes de exemplo existentes (`test_pedidos_motor.py` continua sendo a suíte legível caso a caso, por nome de regra).
- Poucas propriedades (5-8), cada uma com docstring explicando **qual regra de negócio** ela verifica, não a fórmula interna.
- Geradores (`strategies`) restritos a cenários realistas e pequenos (2-6 tamanhos, quantidades 0-50, estoque 0-50, 1-5 pedidos) para manter o shrinking rápido e as falhas fáceis de ler por alguém não especialista em Hypothesis.
- Para o Pitfall 3 (double-spend entre execuções), Hypothesis oferece **stateful testing** (`RuleBasedStateMachine` com `@rule()` para "rodar a próxima execução do motor" e `@invariant()` para checar os acumuladores de orçamento) — é o encaixe mais natural da biblioteca para esse bug específico, porque o problema só aparece através de uma sequência de execuções, não numa chamada isolada.

**Invariantes concretas a assertar (traduzíveis 1:1 para `assert` em testes, com ou sem Hypothesis):**

```python
# 1. Nunca reservar mais que o estoque disponível, por (produto, tamanho)
assert reservado[(produto, tamanho)] <= estoque_original[(produto, tamanho)]

# 2. Soma das reservas de todos os pedidos por tamanho nunca excede o disponível
assert sum(reservas_por_pedido[tamanho].values()) <= estoque_disponivel[tamanho]

# 3. Orçamento de adição e de corte NUNCA excede os limites, cada um isoladamente,
#    acumulados ao longo de TODAS as execuções de um mesmo pedido (não só a atual)
assert total_adicionado_historico[pedido] <= floor(total_original[pedido] * tolerancia)
assert total_cortado_historico[pedido] <= ceil(total_original[pedido] * tolerancia)  # ajustado ao fix do Pitfall 4
# E os dois nunca se compensam: nunca "adicionado - cortado <= limite" no lugar dos dois separados

# 4. Par com furo de grade nunca é selecionado (nunca vira "Gerar OR")
for par in selecionados:
    tamanhos_pedidos = tamanhos_entre_min_e_max(par)
    assert all(estoque_reservavel(par, tam) > 0 for tam in tamanhos_pedidos[1:-1])

# 5. Tudo-ou-nada: ou reserva a grade completa, ou o par inteiro fica em stand-by
for par in selecionados_modo_tudo_ou_nada:
    assert all(item["qt_liquida"] == item["qt_solicitada"] for item in itens[par])

# 6. Soma das partes bate com o total (rateio de extras e financeiro)
assert sum(item["qt_liquida"] for item in itens[par]) == quantidade_total_esperada(par)
assert abs(sum(item["vl_liquido"] for item in itens[par]) - valor_total_esperado(par)) <= tolerancia_centavos

# 7. Nunca persistir uma OR com quantidade total zero (guarda do Pitfall 4)
if status_grade == "Gerar OR":
    assert sum(item["qt_liquida"] for item in itens) > 0

# 8. Determinismo: mesmo snapshot, em qualquer ordem de entrada, mesmo plan_hash
assert plan_hash(shuffle(snapshot)) == plan_hash(snapshot)
```

**How to avoid a suíte virar espelho da implementação:**
- Nunca reescrever a fórmula de tolerância dentro do teste e comparar com o resultado — assertar a invariante externa (estoque nunca oversold, orçamento nunca estourado), que é verdadeira independente de como a fórmula é implementada.
- Preferir testes de exemplo nomeados por regra de negócio (ex. `test_furo_de_grade_bloqueia_par_com_tamanho_meio_zerado`) para os casos didáticos e de regressão, e reservar Hypothesis só para as invariantes que precisam de muitos casos para serem confiáveis (estoque, orçamento cumulativo, determinismo).
- Todo teste de propriedade deve falhar de forma legível (mensagem/asserção que diz qual invariante de negócio quebrou, não um traceback genérico) — importante dado o perfil de dev iniciante do time.

**Phase to address:** Fase de testes/validação, em paralelo com a implementação de cada regra (cada invariante nasce junto com a regra que ela protege) — mas a decisão de adotar Hypothesis como dependência nova deve ser levantada explicitamente com a mantenedora antes (é uma ferramenta nova no stack, e o projeto já tem um padrão declarado de "sem novas dependências para este milestone" nas Constraints do PROJECT.md — vale confirmar se Hypothesis como dev-dependency de teste é aceitável nessa constraint, já que ela não entra na imagem de produção).

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Manter o orçamento ±5% como variáveis locais por chamada (não persistido entre execuções) | Menos migration/schema novo agora | Double-spend do Pitfall 3 garantido em produção assim que houver mais de 1 execução por pedido | Nunca — é o coração da regra 2 |
| Motivo de stand-by genérico ("sem estoque") sem detalhar qual regra bloqueou | Menos código de string/logging agora | Suporte e usuária não conseguem explicar decisões específicas; regressões silenciosas viram "achamos que era assim mesmo" | Só em protótipo interno, nunca no que vai para a UI de stand-by |
| Testar só com totais grandes (>=20 unidades) nos casos de exemplo | Testes mais rápidos de escrever | Bugs de arredondamento em pedidos pequenos (o próprio WR-05) passam despercebidos | Nunca — pedidos pequenos são o caso mais provável de erro, não o mais raro |
| Comparar motor antigo vs. novo só "por olho", sem diff categorizado | Economiza tempo antes do deploy | Regressão só aparece quando um cliente real reclama, sem trilha para investigar | Aceitável só se o volume de pedidos for pequeno o bastante para revisão manual completa — não é o caso aqui (milhares de pares por ciclo) |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Orçamento por pedido implementado como consulta ao banco a cada produto (N+1) dentro do mesmo pedido | Latência do job de processamento cresce proporcionalmente ao nº de produtos por pedido | Carregar o total original e o acumulado consumido **uma vez por pedido** no início do processamento daquele pedido (já no snapshot ou numa query agregada), não por produto | Perceptível já com pedidos de 10+ produtos distintos; crítico no `PLAN_ITEM_LIMIT` de 200k itens já existente no domínio |
| Rateio de extras com maior resto implementado com sort repetido a cada unidade distribuída (O(n²)) em vez de ordenar uma vez | Lentidão visível só em pedidos com muitos tamanhos/produtos elegíveis a extras | Ordenar por parte fracionária uma única vez, distribuir em uma passada | Grades grandes (muitos tamanhos) combinadas com orçamento de adição alto |

## "Looks Done But Isn't" Checklist

- [ ] **Orçamento ±5% separado:** parece implementado se um teste de execução única passa — verificar se o teste cobre **múltiplas execuções sequenciais do mesmo pedido** (Pitfall 3); sem isso, "separado e não compensado" pode estar correto só dentro de uma rodada.
- [ ] **Guarda contra OR zerada:** parece resolvido só trocando `ceil` por `floor` — verificar se existe também a guarda explícita e independente (nunca persistir `qt_liquida` total 0 com status "Gerar OR"), porque a fórmula sozinha ainda pode falhar em outro caso de borda não previsto.
- [ ] **Furo de grade:** parece implementado se bloqueia quando o tamanho do meio já chega com 0 no snapshot — verificar se também bloqueia quando o tamanho do meio **fica em 0 durante a própria execução**, por causa do estoque decrementado por pedidos de maior prioridade processados antes (é dependente de ordem, não de estado inicial).
- [ ] **Tudo-ou-nada:** parece implementado se reserva a grade completa quando há estoque total suficiente — verificar o caso em que há estoque total suficiente somado, mas insuficiente number por tamanho específico (ex. sobra no P, falta no M) — tudo-ou-nada deve bloquear aqui também, não só quando falta soma total.
- [ ] **Determinismo do plano:** parece garantido porque o `plan_hash` existe — verificar se o hash é estável rodando o **mesmo snapshot em ordens de entrada diferentes**, não só confirmando que o hash é calculado de forma canônica (canonicalização do JSON não resolve empates de `sorted()` não determinísticos antes dela).

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|-----------------|
| Double-spend de orçamento já em produção (Pitfall 3) | MEDIUM | Auditoria pontual: para cada pedido com múltiplas execuções, recalcular o acumulado real de adição/corte contra o total original e sinalizar os que excederam; corrigir o cálculo do denominador/acumulador; não é necessário reverter ORs já entregues fisicamente, mas travar novas adições para pedidos que já estouraram |
| OR zerada gravada (bug atual, WR-05) | LOW | Consulta simples identifica ORs com soma de `qt_liquida` igual a 0 e status "Gerar OR"; cancelar/remover essas linhas específicas não tem efeito colateral em estoque (nada foi de fato decrementado de real) |
| Regressão descoberta só depois de ligar em produção (Pitfall 6) | HIGH | Reverter para o motor antigo via toggle/feature flag (se existir) enquanto se investiga; sem isso, é preciso reprocessar manualmente os pares afetados após o fix, o que exige saber quais pares foram tocados pela regressão — daí a importância do diff categorizado como evidência retida |
| Starvation não instrumentada descoberta por reclamação de cliente (Pitfall 2) | LOW | Adicionar o contador de execuções consecutivas em stand-by retroativamente a partir dos logs existentes (se logs guardarem histórico suficiente) ou começar a contar dali para frente; comunicar à usuária que é comportamento esperado da regra 4, salvo decisão de mudar a regra |

## Pitfall-to-Phase Mapping

Fases nomeadas por função lógica (o roadmap final define a numeração) — mapeadas aos "Target features" já listados em `.planning/PROJECT.md` § v1.3.

| Pitfall | Fase sugerida | Verification |
|---------|-----------------|---------------|
| 4 — Guarda contra OR zerada (`ceil`→`floor` + guarda) | Fase 1 — correção isolada, mais barata e mais urgente (bug ativo) | Teste com `total_grade` em 1-19 unidades e estoque zero/parcial; assert `sum(qt_liquida) > 0` sempre que `status=="Gerar OR"` |
| 1 — Estado mutável de orçamento entre produtos do mesmo pedido | Fase 2 — núcleo de orçamento ±5% separado sobre pedido completo | Teste de permutação de ordem dos produtos do mesmo pedido → resultado idêntico |
| 3 — Double-spend do orçamento entre execuções | Fase 2 (mesma fase — é a mesma peça de domínio + persistência) | Teste stateful (múltiplas execuções sequenciais) assertando acumulado ≤ limite sobre o total original |
| 5 — Não-determinismo em empates de prioridade | Fase 2 (desempate total na chave de `sorted`) | Teste de determinismo: mesmo snapshot embaralhado → mesmo `plan_hash` |
| 3 (furo de grade dependente de estoque decrementado em runtime) | Fase 3 — furo de grade nos dois modos | Teste com 3+ pedidos concorrendo pelo mesmo tamanho, checando que o furo é avaliado **após** o consumo dos pedidos de maior prioridade, não só no estoque inicial |
| Tudo-ou-nada (regra do modo sem adequação) | Fase 4 — sem adequação: crédito + tudo-ou-nada | Teste de "sobra no total, falta por tamanho" → par bloqueado |
| 4 (rateio de extras com maior resto) | Fase 5 — peças extras nos extremos com mais sobra | Assert soma das partes == orçamento de adição total, em todos os casos |
| 6 — Regressão silenciosa ao ligar em produção | Fase 6 — validação final / shadow run antes do corte | Diff categorizado (esperado vs. inesperado) sobre snapshot real, revisado antes do deploy |
| 2 — Starvation (instrumentação, não correção) | Acompanha a fase de visibilidade de stand-by na UI já planejada | Contador de execuções consecutivas em stand-by exposto na tag de UI |
| 7 — Suíte de testes / Hypothesis | Transversal — nasce junto com cada fase acima, não é uma fase isolada | Cada invariante crítica (lista da seção Pitfall 7) tem pelo menos um teste antes do merge da fase correspondente |

## Sources

- Leitura direta do código: `app/modules/pedidos/domain/motor_adequacao.py`, `app/modules/pedidos/processing/domain.py`, `app/modules/pedidos/processing/application/service.py` (confiança HIGH — linhas citadas).
- `.planning/PROJECT.md` (regras de negócio autoritativas, ditadas 2026-08-14) e `.planning/REQUIREMENTS.md` (histórico de requisitos e gaps de tolerância já identificados em GRADE-03).
- `.planning/codebase/CONCERNS.md` (gap de teste de "boundary conditions in tolerance adequation" já sinalizado antes desta pesquisa) e `.planning/codebase/TESTING.md` (convenções de teste do repo, confirma ausência de Hypothesis no stack atual).
- [Apportionment / Largest Remainder Method — sum-equals-total invariant](https://dominik-peters.de/lectures/2023_comsoc_apportionment.pdf) — método de Hamilton/maior resto para garantir que a soma das partes arredondadas bate com o total (confiança MEDIUM, conceito estabelecido, não específico deste projeto).
- [largest-remainder · PyPI](https://pypi.org/project/largest-remainder/) — implementação de referência do padrão de rateio sem drift.
- [Hypothesis — Stateful testing documentation](https://hypothesis.readthedocs.io/en/latest/stateful.html) — `RuleBasedStateMachine` + `@invariant()`, o encaixe direto para testar o double-spend de orçamento através de múltiplas execuções (confiança MEDIUM-HIGH, documentação oficial da biblioteca).
- [HypothesisWorks/hypothesis — stateful.rst](https://github.com/HypothesisWorks/hypothesis/blob/master/hypothesis-python/docs/stateful.rst) — fonte primária do padrão acima.

---
*Pitfalls research for: motor de alocação de OR com restrições acopladas (v1.3)*
*Researched: 2026-08-14*
