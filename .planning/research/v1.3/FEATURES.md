# Feature Research — Alocação/Rateio de Estoque em automation de Vestuário

**Domain:** Order allocation / inventory rationing em ERPs e plataformas de automation de moda (apparel wholesale allocation engines)
**Researched:** 2026-08-14
**Confidence:** MEDIUM

Nota de método: não existe Context7 para este domínio (é prática de negócio de ERP, não uma biblioteca). A pesquisa se apoiou em WebSearch/WebFetch sobre documentação de ERPs de moda (JD Edwards EnterpriseOne Apparel Management, SAP Fashion Management, Oracle/Infor Order Management), plataformas de automation de moda (JOOR, NuOrder, Brandboom, Uphance, ApparelMagic) e artigos de prática de assortment planning (RetailDogma, o9, SPS Commerce). Tratado como MEDIUM confidence: múltiplas fontes independentes convergem no vocabulário e nas políticas, mas nenhuma é uma especificação formal única e verificável como um standard ISO.

## Sumário executivo para quem vai escrever REQ-IDs

Das 5 regras de negócio ditadas pela mantenedora (`.planning/PROJECT.md` § Regras de negócio da alocação), **4 têm respaldo direto e nomeado na prática do setor** (crédito bloqueia tudo, furo de grade trava o par, entrega parcial é a norma em B2B, prioridade por valor do pedido é uma política reconhecida). **1 regra é uma composição não-convencional de duas práticas convencionais** (tolerância ±5% existe amplamente, mas normalmente como banda única, não como dois orçamentos que não se compensam) — não está errada, mas vale confirmar que a mantenedora entende a implicação antes de implementar (ver Pitfall 1 abaixo).

## Feature Landscape

### Table Stakes (todo sistema de alocação de automation de moda tem)

| Feature | Por que é esperado | Complexidade | Notas |
|---------|--------------------|---------------|-------|
| Bloqueio de crédito no nível do cliente (**credit hold**), aplicado a TODOS os produtos do cliente, não por linha | Universal em Oracle Order Management, Infor, JD Edwards: crédito é verificado por conta/cliente, não por SKU — um cliente sem crédito não deveria ver nenhuma linha liberada | LOW–MEDIUM | Já existe no motor (Regra "Crédito"), mas falta no modo **sem adequação** — é o gap explícito do milestone. Reaproveitar a mesma checagem do modo com adequação. |
| Visibilidade separada de "sem estoque" (**backorder / awaiting stock / short ship**) vs. "sem crédito" (**credit hold**) como estados distintos na UI | Vocabulário padrão em Oracle Retail OMS, Microsoft Dynamics (`Sale Order Product on Hold` com `Status Reason = Backorder Hold` vs. crédito tratado à parte), Infor CSI (`About Credit Hold`) | MEDIUM | Já é a intenção da Regra de UI ditada: tag "sem crédito" (todos os produtos) e "aguardando estoque" (só onde faltou). Confirma o padrão do setor — granularidade certa. |
| Entrega parcial de pedido sem segurar o pedido inteiro por causa de um produto faltante (**partial shipment / ship-what's-available**) | Em B2B de alto volume, "partial orders and backorders are a reality, not an exception" (Uphance/BetterCommerce). Contrasta com o "ship complete" do varejo, que é a exceção, não a regra, no automation | LOW | Já é a Regra 5 ("entrega parcial"). Alinhado — não precisa questionar. |
| Bloqueio de par quando falta tamanho no MEIO da grade pedida (**broken size run / size run integrity**) | Citação direta da prática (Uphance): *"a style that's in stock overall but out of medium/large is functionally out of stock"*; JD Edwards Apparel Management trata isso via regras de exceção explícitas ("if one size cannot be shipped, you create exceptions... to determine when not to ship items") | MEDIUM | Isso é a Regra 3. **Valida** a regra da dona do produto — ver seção de análise abaixo para o porquê e para o limite da validação (tamanhos nas pontas). |
| Estado "stand by"/pendente reaparecendo para nova tentativa no próximo ciclo, em vez de exigir ação manual para reentrar na fila | Padrão em backorder management: pedido bloqueado permanece elegível e é reavaliado automaticamente quando estoque/crédito mudam (Dynamics 365 backorder/preorder) | LOW | Já é o comportamento atual (Out of Scope confirma: "reaparecem para nova tentativa" atende). |

### Differentiators (agregam valor real para o objetivo declarado — maximizar faturamento com entrega consolidada por cliente)

| Feature | Proposta de valor | Complexidade | Notas |
|---------|--------------------|---------------|-------|
| Prioridade por **valor do pedido do cliente** (não por data, não por tamanho de cliente) na disputa pela mesma peça (**value-based / margin-based allocation priority**) | Fontes confirmam que "giving scarce units to low-margin orders while high-value customers wait erodes contribution and goodwill" — ranking por valor é uma política real e reconhecida (não é invenção do negócio). Para o objetivo "maximizar faturamento com entrega consolidada por cliente", é mais direto que pro rata: cada peça escassa vai para quem paga mais por ela | MEDIUM | Regra 4. É a política mais alinhada ao objetivo declarado — ver comparação de políticas abaixo. Risco: pode sistematicamente sub-atender clientes pequenos/novos em produtos disputados — não é bug, é a consequência esperada e nomeada da política (fairness vs. revenue-maximization é um trade-off documentado, não um erro de design). |
| Orçamentos separados de adição/corte (±5% cada, não compensáveis) medidos sobre o pedido completo do cliente | Nenhuma fonte do setor descreve exatamente essa combinação (ver Pitfall 1) — mas o princípio geral, "shipping tolerance clauses allow ±5%", existe amplamente em contratos de fornecimento. A escolha de não compensar é uma decisão de negócio explícita para não "esconder" um corte grande atrás de um excesso em outro produto | MEDIUM–HIGH | Regra 2. Estruturalmente é a peça mais nova do domínio — orçamento acumulado por cliente (não por produto) exige estado compartilhado entre os pares do mesmo pedido durante o processamento. |
| Alocação de peças extras nos tamanhos com mais sobra (nas pontas da grade, que giram menos), sem posição fixa | Contraste interessante com JD Edwards "size weighting" (que pondera tamanhos mais vendidos como M/L mais alto) — aqui a lógica é invertida porque a peça extra não tem cliente esperando por ela; colocá-la onde há menos giro minimiza desperdício futuro em vez de maximizar a chance de vender rápido. É uma adaptação legítima da prática, não um desvio dela | LOW–MEDIUM | Já enunciada nas regras. Baixo risco — é lógica de "resto vai pro que sobra", fácil de explicar à mantenedora e ao time comercial. |
| Reservar mais ou menos que o pedido exato (over/under shipment) intencionalmente para maximizar faturamento (Regra 1) | "Shipping tolerance" para permitir isso é prática de contrato comum no automation, mas normalmente é vista como tolerância operacional (erro aceitável), não como alavanca deliberada de otimização de receita. Aqui o negócio assume isso conscientemente: "enviar com falta fatura mais que não enviar nada" | MEDIUM | Ponto a documentar bem no REQ: a UI/relatório para o time comercial deve deixar claro que a divergência é deliberada (dentro do orçamento), não erro operacional — evita retrabalho de explicação ao cliente. |

### Anti-Features (o setor tentou e se arrependeu — evitar)

| Feature | Por que parecia bom | Por que dá problema | Alternativa |
|---------|----------------------|----------------------|-------------|
| Otimização global (ex.: pro rata "fair share" cego, ou algoritmo que redistribui todas as peças da OR inteira ao mesmo tempo buscando o ótimo matemático) sem explicação por par cliente×produto | Maximiza uma métrica agregada (receita total, ou "justiça" percentual) | Time comercial não consegue explicar ao cliente por que ele recebeu 92% em vez de 95%, ou por que um cliente "menor" foi preterido — decisão vira caixa-preta. A prática documentada (Uphance) é o oposto do recomendado: regras não-escritas e exceções acumuladas ("cada exceção vira precedente e em duas temporadas a regra não significa mais nada") | Regras determinísticas, nomeadas e testáveis por camada (crédito → furo de grade → orçamento+prioridade), cada uma auditável isoladamente — que é exatamente a arquitetura já adotada (`domain/` puro, sem I/O) |
| Realocação automática que desfaz decisão manual já tomada (ex.: editor de grade pós-geração é sobrescrito por um novo rodar do motor sem aviso) | Mantém o sistema "sempre atualizado" com o estoque mais recente | Documentado como risco real em sistemas de warehouse (Dynamics 365 short-picking reallocation, SAP reallocation rule) — desfazer uma escolha humana silenciosamente é a queixa mais citada sobre automação de alocação | Reprocessamento deve respeitar edições manuais já persistidas (`pedido_modificacoes`) como um "pin", não como estado transitório a ser recalculado por cima — vale confirmar explicitamente esse comportamento como requirement, já que o milestone mexe no motor |
| Priorização opaca por critério não documentado (ex.: "cliente VIP" mantido na cabeça de alguém, aplicado via ajuste manual fora do sistema) | Flexibilidade para casos especiais | Setor descreve isso como o estado pré-maturidade mais comum e mais citado como dor: "policy enforced manually through Slack messages, spreadsheet flags" — não escala, não é auditável, quebra confiança do time comercial | A prioridade por valor do pedido (Regra 4) já é objetiva e calculável — não introduzir exceção manual de prioridade fora dela sem um mecanismo de override auditado |
| "Ship complete" rígido (não embarcar nada enquanto 100% do pedido não estiver disponível) | Reduz confusão do cliente (uma entrega só) e custo logístico de fracionar | É a exceção no automation B2B, não a regra — "para distribuidores/fabricantes de alto volume, pedidos parciais e backorders são realidade, não exceção". Aplicado aqui, destruiria o objetivo de maximizar faturamento | Regra 5 (entrega parcial) já evita essa armadilha corretamente — não retroceder para isso ao lidar com furo de grade |

## Análise por pergunta

### 1. Integridade de grade (size run integrity / broken assortment)

**Vocabulário do setor:** em inglês, os termos usados de forma intercambiável são **broken size(s)**, **broken assortment**, **size run integrity**, e o conceito adjacente **size curve** (a distribuição esperada de demanda entre tamanhos). Não existe um termo único "curve fill" consolidado nas fontes consultadas — "curve fill" aparece informalmente, mas **size run integrity** é o termo mais citado e mais próximo semanticamente do que a regra da mantenedora descreve. Em português, o equivalente natural já em uso no projeto é "furo de grade" (ou "grade quebrada") — ambos comunicam a mesma ideia de descontinuidade no meio da curva de tamanhos.

**Prática padrão:** a literatura do setor confirma que uma grade com um buraco no meio é tratada como **funcionalmente indisponível**, mesmo que o estoque agregado do produto pareça "em estoque". A citação mais direta encontrada: *"a style that's 'in stock' overall but out of medium/large is functionally out of stock."* JD Edwards Apparel Management (ERP dedicado a apparel, o achado mais próximo de um "concorrente" formal) resolve isso com **regras de exceção explícitas por tamanho** — a documentação diz literalmente: *"if one size cannot be shipped, then you create exceptions or overrides in the allocation rules to determine when not to ship items and when to deliver."* Ou seja, **segurar o par inteiro é a prática, não a exceção** — a alternativa citada de "consolidar tamanhos quebrados" (RetailDogma) é uma tática de **liquidação de estoque já em loja**, não de alocação de pedido de automation, então não se aplica aqui.

**Isso valida ou contesta a regra da dona do produto?** **Valida.** A regra ("se algum tamanho estritamente entre o menor e o maior pedido tem 0 reservável, o par fica em stand by; pelo menos 1 unidade em cada tamanho do meio libera") é uma operacionalização razoável e até mais permissiva que o "tudo-ou-nada" mais rígido que aparece em parte da literatura — aqui basta 1 peça no tamanho do meio, não a quantidade cheia pedida. Isso é coerente com a Regra 2 (tolerância de corte de até 5%): a grade pode vir cortada, só não pode ter buraco. Não há contradição a levantar com a mantenedora neste ponto — é a prática nomeada do setor, com um nome (size run integrity) que vale usar na UI/documentação em vez de inventar rótulo novo.

**Ponto de atenção não coberto pela regra atual:** a regra fala em tamanho "estritamente entre o menor e o maior pedido". Isso deixa as pontas (o próprio menor e o próprio maior tamanho pedidos) fora da verificação de furo — se PP zerar mas P, M, G, GG tiverem estoque, o par passa. Isso é consistente com a lógica de "curva" (as pontas historicamente têm menos giro, logo é aceitável que sejam as primeiras a faltar) e bate com o comportamento de "extras nas pontas" já definido — mas vale confirmar que é intencional e não uma lacuna esquecida, porque um cliente que pediu PP como parte relevante do mix pode notar a ausência mesmo sendo a ponta.

### 2. Rateio quando falta para todos (allocation/rationing)

Políticas nomeadas na prática do setor, com fonte:

| Política | Como funciona | Prós | Contras (para este objetivo: maximizar faturamento, entrega consolidada por cliente) |
|---|---|---|---|
| **Pro rata / fair share / sprinkling rate** (JD Edwards chama de "sprinkling rate or fair share processing"; Oracle chama "fair-share rules") | Cada pedido recebe a mesma % do que pediu, proporcional à demanda total | Percebido como "justo"; simples de explicar | Dilui a peça entre muitos clientes — cada um recebe uma fração pequena, o que pode empurrar vários pedidos para dentro do risco de furo de grade (Regra 3) ao mesmo tempo, sem garantir que ninguém fique com grade completa. Não maximiza receita por peça — trata um cliente grande e um pequeno como iguais |
| **Prioridade por valor/margem do cliente** ("customer priority", "channel margin", allocation por customer tier) | A peça escassa vai inteira (ou o máximo possível) para o pedido de maior valor primeiro, depois o próximo, até acabar o estoque | Alinhado diretamente com "maximizar faturamento": cada unidade vai para onde gera mais receita. Também favorece grades completas para menos clientes em vez de grades furadas para muitos, o que ajuda a Regra 3 | Pode sistematicamente sub-atender clientes menores/novos em produtos concorridos — é um trade-off de negócio conhecido (fairness vs. revenue), não um bug, mas deve ser comunicado ao time comercial para não parecer arbitrário |
| **FIFO por data do pedido** | Quem pediu primeiro é atendido primeiro | Simples, prazo-justo, fácil de auditar ("o pedido dele chegou antes") | Não tem relação com o objetivo de receita — um pedido pequeno antigo pode consumir estoque que geraria mais faturamento em um pedido maior mais recente. Contraria diretamente Regra 1/Regra 4 |
| **Tudo-ou-nada (ship complete) por par** | Só reserva se conseguir a grade cheia pedida | Nunca gera grade furada nem parcial — previsível | Desperdiça faturamento possível quando o estoque quase fecha o pedido (contraria a Regra 1 explicitamente, que diz "enviar com falta fatura mais que não enviar nada"). É a política do **modo "sem adequação"** do sistema hoje — nesse modo específico, tudo-ou-nada é aceitável porque a decisão de "sem adequação" já é do usuário: ele optou por não permitir grade diferente da pedida |

**Conclusão para o REQ:** a Regra 4 (prioridade por valor) é a política mais coerente com o objetivo declarado — está documentada no setor como estratégia legítima para "guiar suprimento escasso para o melhor mix de demanda, melhorando margem por unidade embarcada". Pro rata e FIFO são alternativas reais, mas ambas contrariam o objetivo de maximizar faturamento por razões diferentes (diluição vs. irrelevância de data). Vale que a mantenedora saiba nomear essa escolha ("value-based / priority allocation") para futuras conversas com o time comercial sobre por que um cliente ficou sem uma peça disputada.

### 3. Tolerância de over/under shipping

**Prática comum:** existe amplamente, e ±5% é de fato um número comum em cláusulas de "shipping tolerance" em contratos de fornecimento (a fonte jurídica genérica confirma: *"a shipping tolerance clause typically allows the seller to deliver slightly more or less than the agreed quantity, often within a set percentage range, such as plus or minus 5%"*). Formas comuns de expressar o limite, do mais ao menos comum:
- **% sobre a quantidade total do pedido** (o que este projeto usa) — comum em contratos de manufatura/têxtil
- **% por linha/produto** — também comum, e é o nível de granularidade mais citado em cláusulas de fornecimento (cada SKU tem sua própria banda)
- **Caixa fechada / closed-case quantity** — arredondamento para múltiplos de pack, comum quando o produto é vendido em caixas fixas (não parece se aplicar ao modelo por grade deste projeto)

**O que é INCOMUM aqui, e deve ser sinalizado explicitamente à mantenedora:** a prática de mercado observada nas fontes é de uma **banda única** (ex.: "entre 95% e 105% do pedido"), que efetivamente **compensa** excesso em um produto com falta em outro dentro do mesmo teto. A regra deste projeto usa **dois orçamentos que não se compensam** (até 5% adicionado E até 5% cortado, cada um contado à parte) — isso é estruturalmente diferente e **mais permissivo no agregado**: em tese, o cliente pode terminar com produto A 5% a menos E produto B 5% a mais, uma variação total maior do que uma banda única de ±5% permitiria se fosse líquida. Não há fonte do setor descrevendo exatamente esse desenho de "dois orçamentos separados" — não é necessariamente errado (é uma decisão de negócio deliberada, alinhada à Regra 1 de maximizar faturamento e à intenção de "sempre tentar entregar algo de cada produto"), mas é **não convencional** o suficiente para merecer uma confirmação explícita com a mantenedora antes de codificar, e para documentar bem o motivo no REQ (para quando alguém — auditoria, time comercial, ou a própria mantenedora meses depois — perguntar "por que não simplesmente ±5% líquido?").

Também vale registrar: medir sobre a **quantidade total do pedido completo do cliente** (não por produto) é uma escolha de agregação que favorece flexibilidade — um produto pode estourar seu próprio "peso" de tolerância individual desde que o total do pedido do cliente feche dentro do orçamento agregado. Isso é coerente com "sempre tentar entregar algo de cada produto" (Regra 2) só se houver uma trava adicional impedindo que um único produto consuma sozinho todo o orçamento de corte do pedido — vale confirmar se essa trava (mínimo por produto) é intencional ou se falta especificá-la.

### 4. Visibilidade de stand by / backorder

**Rótulos padrão encontrados na prática (ERPs formais — Oracle Order Management/Retail OMS, Microsoft Dynamics 365, Infor CSI):**
- **Backorder / Backordered** — linha sem estoque suficiente na alocação; é o rótulo mais universal para falta de estoque no nível da linha/SKU
- **On Hold** (genérico) com **Status Reason** específico: `Backorder Hold`, `Preorder Hold` (Dynamics 365) — o "motivo" é um campo separado do "estado", o que é uma boa prática de modelagem (estado + motivo, não um enum monolítico)
- **Credit Hold** — bloqueio no nível do **cliente/conta**, não da linha — aplicado antes mesmo de tentar alocar, e citado como algo que corre automaticamente "sempre que você aceita um novo pedido ou uma alteração" (Infor CSI)
- **Awaiting Stock / Short Ship** — usados mais em contexto operacional/logístico do que como nome de campo de sistema, mas descrevem exatamente a falta parcial de estoque em uma linha específica

**A regra deste projeto bate com o padrão:** tag "sem crédito" em **todos** os produtos do cliente bloqueado replica exatamente o comportamento observado de **Credit Hold no nível do cliente** (não por linha) em Oracle/Infor — é o padrão certo, porque crédito é uma propriedade do cliente, não do produto. Tag "aguardando estoque" **só** nos produtos que faltaram replica o padrão de **Backorder no nível da linha** — também correto. Recomenda-se adotar esse vocabulário em inglês como referência interna (credit hold / backorder ou awaiting stock) e, na UI em português, os rótulos já escolhidos pela mantenedora ("sem crédito", "aguardando estoque") são traduções fiéis e não fogem do padrão do setor.

**Gap potencial a levantar como pergunta de REQ:** nenhuma fonte trata do caso "furo de grade" como um terceiro estado distinto de "aguardando estoque" — na prática de Oracle/Dynamics, um furo de grade normalmente cairia dentro do mesmo guarda-chuva de "backorder" (afinal, tecnicamente é falta de estoque em um tamanho específico). Vale decidir explicitamente se o par bloqueado por furo de grade (Regra 3) recebe o mesmo rótulo "aguardando estoque" que o par bloqueado por falta geral de estoque, ou se merece um rótulo próprio (ex.: "grade incompleta") — isso muda a explicabilidade para o time comercial, que é justamente o que este milestone quer melhorar.

### 5. Anti-features (o que sistemas assim tentaram e se arrependeram)

Ver tabela "Anti-Features" acima. Resumo dos três padrões mais citados nas fontes, todos com paralelo direto no risco deste milestone:

1. **Otimização global opaca** — decisão de alocação que ninguém no time comercial consegue explicar linha a linha. O antídoto documentado (e já é o desenho deste projeto) é regra determinística e nomeada por camada, não um solver que maximiza uma métrica agregada sem rastro por par.
2. **Realocação automática por cima de decisão manual** — citado como risco real em reprocessamento de warehouse/allocation. Relevante aqui porque a Fase de edição de grade pós-geração (`pedido_modificacoes`) já existe — o motor de alocação novo **não deve** rodar por cima de uma OR já editada manualmente sem alguma forma explícita de proteção/aviso. Vale um REQ explícito sobre isso, mesmo que a resposta final seja "não se aplica porque o reprocessamento não toca OR aprovada".
3. **Prioridade não documentada / regra "na cabeça de alguém"** — o antídoto é exatamente o que este milestone está fazendo: tirar a regra de negócio da cabeça da mantenedora e da operação manual e colocá-la no domínio testável. Isso não é um risco a evitar, é a correção que o milestone já entrega.

## Feature Dependencies

```
Filtro de crédito (modo sem adequação)
    └──requires──> Checagem de crédito já existente no modo com adequação (reaproveitar)

Furo de grade (Regra 3, ambos os modos)
    └──requires──> Leitura do estoque reservável por tamanho já existe (estoque_virtual)
    └──enhances──> Orçamento ±5% (Regra 2) — furo de grade é checado ANTES do orçamento (precedência: regra 1 sempre → regra 4 → regras 2 e 3 juntas)

Orçamento ±5% separado por pedido completo (Regra 2)
    └──requires──> Estado agregado por PEDIDO (não por produto) durante o processamento — novo, não existe hoje
    └──conflicts com──> Medição por linha/produto isolada (padrão mais comum do setor) — decisão consciente do projeto, documentar o porquê

Prioridade por valor (Regra 4)
    └──requires──> Valor total do pedido do cliente já calculável (soma de preço × qtde)
    └──enhances──> Orçamento ±5% — desempate de "quem recebe a peça escassa" quando o orçamento permite mais de um destino

Tag "aguardando estoque" / "sem crédito" na UI
    └──requires──> Endpoint REST paginado de stand by (já definido como constraint: não inflar payload do GET de job)
    └──requires──> Contratos de retorno do motor preservados (`bloqueados_credito`, `preteridos`) — não mudar de forma
```

### Dependency Notes

- **Orçamento ±5% requer estado por pedido, não por produto:** essa é a mudança estrutural mais significativa do milestone dentro do domínio — hoje o motor processa por par (pedido, produto); a Regra 2 exige agregação através de todos os produtos do mesmo pedido antes de fechar quanto pode ser cortado/adicionado em cada um. Precisa ser resolvido antes de (ou junto com) a prioridade por valor, porque ambos operam no nível do pedido do cliente.
- **Furo de grade e orçamento andam juntos na precedência ("regras 2 e 3 juntas"):** conforme já declarado no PROJECT.md, a regra 1 (maximizar) vem sempre primeiro, depois a regra 4 (prioridade), e só então 2 e 3 são avaliadas em conjunto — isso importa para a ordem de implementação/teste: não dá para testar o orçamento isoladamente sem o furo de grade já resolvido para o mesmo par, e vice-versa.
- **Tag de stand by conflita em potencial com contrato do job preservado:** a visibilidade via REST paginado (não pelo payload do GET de job) é uma constraint arquitetural já decidida — qualquer REQ de UI deve respeitar isso, não reabrir a discussão.

## MVP Definition

### Launch With (v1.3 — já é o escopo declarado)

- [ ] Filtro de crédito no modo sem adequação — fecha o gap mais crítico e mais citado como "impacto operacional direto" no PROJECT.md
- [ ] Furo de grade bloqueando o par nos dois modos — table stakes confirmado pela prática do setor (size run integrity)
- [ ] Orçamento ±5% separado, agregado por pedido — diferenciador, mas é o núcleo do objetivo "maximizar faturamento sem incompletude oculta"
- [ ] Prioridade por valor do pedido — necessário para o rateio ter uma regra objetiva quando o orçamento não cobre todos
- [ ] Tags de stand by (sem crédito / aguardando estoque) na UI — sem isso o resto do milestone fica invisível para quem opera

### Add After Validation (v1.x)

- [ ] Rótulo distinto para "bloqueado por furo de grade" vs. "bloqueado por falta geral de estoque" — só vale a pena diferenciar na UI depois que o time comercial usar a tag genérica e sentir falta da distinção
- [ ] Trava de mínimo por produto dentro do orçamento agregado do pedido (evitar que 1 produto consuma sozinho todo o orçamento de corte) — depende de observar se isso acontece na prática

### Future Consideration (v2+)

- [ ] Exceções configuráveis por tipo de item (ex.: "top e bottom sempre juntos", ao estilo JD Edwards coordinate management) — não há evidência de que a do projeto precise disso hoje; citado aqui só para não ser reinventado como anti-feature de otimização global se alguém propuser depois
- [ ] Painel de auditoria explicando cada decisão de prioridade por valor para o time comercial — valioso, mas fora do escopo declarado deste milestone (que já define UI mínima de stand by)

## Feature Prioritization Matrix

| Feature | Valor para o objetivo | Custo de implementação | Prioridade |
|---------|------------------------|--------------------------|------------|
| Filtro de crédito no modo sem adequação | HIGH | LOW | P1 |
| Furo de grade (Regra 3) | HIGH | MEDIUM | P1 |
| Orçamento ±5% agregado por pedido (Regra 2) | HIGH | HIGH | P1 |
| Prioridade por valor (Regra 4) | HIGH | MEDIUM | P1 |
| Extras nas pontas de sobra | MEDIUM | LOW | P1 |
| Guarda contra OR zerada (ceil→floor) | HIGH | LOW | P1 |
| Tags de stand by na UI | HIGH | MEDIUM | P1 |
| Rótulo distinto furo de grade vs. falta geral | LOW–MEDIUM | LOW | P2 |
| Trava de mínimo por produto no orçamento agregado | MEDIUM | MEDIUM | P2 |
| Painel de auditoria de prioridade por valor | MEDIUM | HIGH | P3 |

## Competitor / Reference System Analysis

| Feature | JD Edwards Apparel Management | Oracle Order Management / Retail OMS | Este projeto |
|---------|-------------------------------|----------------------------------------|--------------|
| Furo de grade | Regras de exceção manuais por tamanho, configuráveis | Não é um ERP de apparel — não modela grade nativamente | Regra determinística no domínio: 0 reservável em tamanho do meio bloqueia o par |
| Rateio quando falta para todos | "Sprinkling rate / fair share" (pro rata) OU alocação manual | Fair-share rules configuráveis (Oracle Fusion Demand) | Prioridade por valor do pedido — mais alinhado ao objetivo de receita que o pro rata padrão desses dois |
| Crédito | Integra com fulfillment padrão, mas apparel usa fluxo próprio ("cannot use Order Fulfillment for style items") | Credit hold automático no aceite do pedido, no nível do cliente | Igual ao padrão: bloqueio no nível do cliente, antes da alocação |
| Backorder | Documentado como prática padrão, mas "pode não ser desejável para style items" | Backordered no nível da linha, com Status/Status Reason separados | Tag "aguardando estoque" por produto — alinhado |
| Tolerância de over/under | Não documentado explicitamente para apparel | Não é o foco do sistema | ±5% com dois orçamentos não compensáveis, agregados por pedido — desenho próprio, sem precedente direto encontrado (ver Pitfall) |

## Sources

- [How Size Curves and Pack Configurations Improve Inventory Performance — SPS Commerce](https://www.spscommerce.com/community/articles/how-size-curves-and-pack-configurations-improve-inventory-performance)
- [Broken Sizes in Fashion Retail — RetailDogma](https://www.retaildogma.com/broken-sizes/)
- [Size Curve: Definition, Planning & Factors — RetailDogma](https://www.retaildogma.com/size-curve/)
- [Size Run — RetailDogma](https://www.retaildogma.com/size-run/)
- [Elevating Retail, Apparel, and Footwear Industries with Advanced Size Curve Analysis — o9 Solutions](https://o9solutions.com/articles/advanced-size-curve-analysis)
- [What Is an Allocation Rule in Apparel and How to Write One That Holds Up — Uphance](https://www.uphance.com/blog/what-is-an-allocation-rule-and-how-to-write-a-good-one/)
- [Understanding Allocations for JD Edwards EnterpriseOne Apparel Management — Oracle Docs](https://docs.oracle.com/en/applications/jd-edwards/supply-chain-manufacturing/9.2/eoapp/understanding-allocations-for-jd-edwards-enterpriseone-apparel.html)
- [Use Fair-Share Rules to Allocate Scarce Supply — Oracle Cloud Demand Management](https://docs.oracle.com/en/cloud/saas/readiness/scm/26b/demand26b/26B-demand-wn-f42977.htm)
- [Inventory allocation methods: models, formulas, and best practices — Cleverence](https://www.cleverence.com/articles/for-business/inventory-allocation-methods-4829/)
- [Supply chain allocation: strategies, models, and software guide — Cleverence](https://www.cleverence.com/articles/for-business/for-business/supply-chain-allocation-4832/)
- [4 Ways to Optimize Allocation of Constrained Supply — Logility](https://www.logility.com/blog/4-ways-to-optimize-allocation-of-constrained-supply/)
- [Sales Order Management Implementation Guide — Oracle JD Edwards (Order Holds)](https://docs.oracle.com/en/applications/jd-edwards/supply-chain-manufacturing/9.2/eoaso/order-holds.html)
- [Set up backorder and preorder functionality — Microsoft Dynamics 365](https://learn.microsoft.com/en-us/dynamics365/intelligent-order-management/backorder-preorder)
- [About Credit Hold — Infor CSI](https://docs.infor.com/csi/9.01.x/en-us/csbiolh/lsm1454144036235.html)
- [Order Holds — Oracle Retail OMS](https://docs.oracle.com/en/industries/retail/retail-oms-suite-cloud/25.1.101.0/rommh/OrderHolds.htm)
- [Shipping Tolerance Clauses — Law Insider](https://www.lawinsider.com/clause/shipping-tolerance)
- [How to Handle Partial Orders and Backorders at Scale — BetterCommerce](https://www.bettercommerce.io/blog/how-to-handle-partial-orders-and-backorders-at-scale)
- [B2B Order Fulfillment: A Guide for Apparel Brands — Uphance](https://www.uphance.com/blog/b2b-order-fulfillment/)
- [Ship Complete — Logimax Glossary](https://www.logimaxwms.com/glossary/ship-complete)
- [Automatic and manual item reallocation during short picking — Microsoft Dynamics 365 Blog](https://www.microsoft.com/en-us/dynamics-365/blog/business-leader/2016/11/07/automatic-and-manual-item-reallocation-during-the-short-picking-dynamics-365-for-operations-1611/)
- [10 Wholesale Order Management KPIs for Apparel Brands — RepSpark](https://www.repspark.com/blog/10-wholesale-order-management-kpis-for-apparel-brands)
- [Wholesale Distribution Software: A Guide for Fashion Brands — Blastramp](https://blastramp.com/wholesale-distribution-software-a-guide-for-fashion-brands/)

---
*Feature research for: alocação/rateio de estoque em automation de vestuário (Ordem de Reserva)*
*Researched: 2026-08-14*
