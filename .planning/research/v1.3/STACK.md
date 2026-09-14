# Stack Research

**Domain:** Motor de alocação de estoque com restrições de negócio acopladas (v1.3)
**Researched:** 2026-08-14
**Confidence:** HIGH na recomendação stack (verificada por execução local e por evidência pública) · MEDIUM no dimensionamento do "custo de faturamento" (não há série histórica para calcular R$ exato — a resposta é analítica, não estatística)

## Veredito em uma frase

**Não introduzir solver.** A stdlib do Python 3.13 (mais o utilitário Hamilton que já existe em `edicao_grade.py`) resolve o problema inteiro — e resolve melhor que um solver, porque a regra de negócio ditada (Regra 4: "ganha o cliente cujo pedido total vale mais") é uma **ordem estrita de prioridade**, não uma função-objetivo de maximização global. Greedy bem construído não é uma aproximação da regra: é a regra.

---

## 1. Solver se justifica? Não. Por quê.

### O problema não é uma otimização combinatória — é um livro-razão com prioridade fixa

As quatro travas novas (orçamento ±5% por pedido, furo de grade, tudo-ou-nada, prioridade por valor) têm uma característica em comum: **cada uma é decidível localmente, em ordem, sem precisar re-explorar decisões passadas**:

- **Prioridade por valor (Regra 4):** define uma ordem total determinística *antes* de qualquer alocação começar. Não é um critério a otimizar — é o índice do loop.
- **Furo de grade (Regra 3):** é um predicado local sobre o par (pedido, produto): "existe tamanho do meio com 0 reservável?" Não depende de nenhuma decisão de outro par.
- **Orçamento ±5% (Regra 2):** é um contador (ledger) por pedido que só decresce à medida que o pedido consome peças. É estado sequencial, não uma restrição que acopla pares entre si de forma bidirecional — o acoplamento é *unidirecional no tempo* (decisões de um produto restringem o que sobra de orçamento para o próximo produto do mesmo pedido, nunca o contrário).
- **Tudo-ou-nada:** é o mesmo teste de "cabe tudo?" que o motor atual já faz em `adequar_grade_produto`, só que sem a adequação parcial.

Um problema onde toda restrição é decidível em uma varredura ordenada, sem *backtracking*, é exatamente a classe para a qual greedy é não só adequado como **provadamente ótimo em relação ao objetivo declarado** (exchange argument clássico: trocar a ordem de atendimento violaria a Regra 4 por definição). Um solver de programação por restrições (CP-SAT) ou MILP (PuLP/CBC, python-mip) existe para explorar espaços onde a ordem de decisão importa e não há uma resposta "correta" óbvia — não é este caso.

### Um solver otimizaria a coisa errada

Isto é o ponto mais importante e o motivo pelo qual "greedy é subótimo" é a pergunta errada aqui: um MILP/CP-SAT que maximizasse receita agregada do lote inteiro **precisaria violar a Regra 4** sempre que tirar uma peça do cliente de maior valor e dar a dois clientes menores aumentasse a soma total. Isso é exatamente o que a regra proíbe ("a entrega é agrupada fisicamente por cliente no CD... ganha o cliente cujo pedido total vale mais" — não "ganha a alocação que maximiza a soma"). Para forçar um solver a respeitar prioridade estrita em vez de otimizar globalmente, seria preciso modelar a Regra 4 como uma cascata de objetivos lexicográficos (um por cliente, na ordem de prioridade) — o que é notoriamente difícil de expressar em CP-SAT/MILP de forma que escale, e que, mesmo bem-sucedida, teria calculado, com muito mais código e muito mais risco, exatamente o que o greedy calcula em uma passada.

### Determinismo: o solver não elimina o trabalho de blindagem — ele adiciona uma segunda camada

O requisito "plano hasheado, idêntico entre execuções" é o argumento mais forte contra o solver, não a favor de um método mais "rigoroso":

- OR-Tools CP-SAT tem **bugs de não-determinismo documentados mesmo com `num_search_workers=1`** em versões 9.4/9.5 (google/or-tools#3943, #3948), e não-determinismo entre contagens diferentes de workers é uma queixa recorrente (#3842, #3590). A própria equipe do OR-Tools afirma que, para ser determinístico, "o código que constrói o modelo também precisa ser determinístico" — ou seja, mesmo usando o solver, ainda seria necessário fazer toda a blindagem de ordenação descrita na seção 5 abaixo, e *além* disso confiar que o solver internamente não introduz sua própria variação (batch size fixo entre workers, seed fixado, sem paralelismo com corrida de LNS). É estritamente mais superfície de risco pelo mesmo trabalho de determinismo, não menos.
- CBC (o backend padrão de PuLP e do python-mip) tem histórico de variar ordem de corte/branch-and-bound entre builds e ambientes; garantir bit-a-bit igual exigiria fixar threads=1 e parâmetros internos de estratégia, algo frágil e mal documentado.

### Custo de manutenção incompatível com as constraints do repo

- **Peso da dependência:** o wheel do `ortools` no PyPI (versão atual `9.15.6755`, jan/2026) tem ~25-30 MB em Linux/macOS e **~108 MB no Windows** (a extensão binária estática levou a equipe do PyPI a pedir aumento do limite de arquivo para 125 MB — pypi/support#3714). Isso é ordens de grandeza maior que qualquer dependência hoje no `pyproject.toml` (a mais pesada, `sqlalchemy[asyncio]`, é ~3 MB).
- **`python-mip` está órfão:** último release no PyPI é `1.15.0` (jan/2023); o post "Goodbye python-mip?" (nov/2024) documenta o projeto como efetivamente parado. Não é uma dependência que se traz para um projeto com "sem over-engineering" e mantenedora júnior.
- **PuLP** é mantido, mas embarca CBC (binário C++) e introduz um vocabulário inteiro novo (variáveis, restrições lineares, status de solução, infeasibility) para resolver um problema que a mantenedora já resolve hoje lendo 474 linhas de Python puro linha a linha.
- **Escala não pede solver:** 100.000 pares e 200.000 itens é trivial para `sorted()`/laços O(n log n) em Python puro — não é o regime (milhões de variáveis inteiras, restrições combinatórias reais) onde CP-SAT compensa o custo de modelagem. O teto de memória (1536 MiB, pico medido hoje 434 MiB) tem folga de sobra para dobrar a estrutura de dados do motor atual sem chegar perto do limite; não há pressão de memória que justifique um solver mais eficiente em espaço de busca.

**Conclusão:** a hipótese da mantenedora está certa. Duas (ou três) passadas de heurística determinística em stdlib pura é a escolha certa, não uma concessão.

---

## 2. Onde o greedy é "subótimo" — e o que isso custa

Resposta honesta: **não há trade-off de faturamento mensurável a aceitar aqui**, e vale explicar por quê em vez de inventar um número.

- O único grau de liberdade que sobra depois de aplicar prioridade estrita, furo de grade e tudo-ou-nada é: **em quais tamanhos entram as peças extras**. E essa escolha tem **impacto zero em receita**, porque `vl_liquido = qtd × preço_unitário` e o preço unitário é do produto/cor, não do tamanho — não importa qual extremidade da grade recebe a peça extra, o valor faturado é idêntico. A regra "extras nos tamanhos com mais sobra" é uma decisão logística (giro/estoque parado), não financeira.
- O ponto onde existe uma escolha real com impacto em R$ é outro, e **greedy ingênuo pode errar nele se não for desenhado com cuidado** (isto não é uma limitação do greedy como método — é um detalhe de implementação a acertar, coberto na seção 3): quando o **orçamento de adição ±5% é compartilhado entre vários produtos do mesmo pedido**, a ordem em que os produtos são visitados importa, porque produtos diferentes do mesmo pedido podem ter preços unitários diferentes. Gastar o orçamento de adição no primeiro produto encontrado (ordem arbitrária de iteração) em vez de no produto de maior preço unitário do cliente deixa menos R$ em cada peça extra do que poderia. Isso **não exige um solver** — exige ordenar os produtos do pedido por preço unitário decrescente antes de consumir o ledger de adição (ver desenho recomendado abaixo). Uma vez corrigido isso, não sobra nenhuma decisão remanescente onde um solver encontraria algo a mais.
- Em ordem de grandeza: mesmo sem essa correção, o desvio está limitado ao próprio teto de ±5% de **um único pedido** (a tolerância já é o limite que o negócio aceitou como "diferença tolerável"), nunca se acumula entre pedidos, e só se manifesta quando o mesmo cliente tem múltiplos produtos disputando o mesmo ledger — não é um efeito sistêmico sobre os 100.000 pares.

**Resumo para o roadmap:** o "custo" de não usar solver é zero em receita, uma vez que o desenho de duas passadas trate o ledger por pedido ordenando por preço unitário (passe 2) em vez de por ordem de chegada dos produtos. Documentar isso como decisão de design da fase, não como pitfall a monitorar.

---

## 3. Ferramentas da stdlib e do que já está instalado

Nenhuma dependência nova. Tudo abaixo é stdlib do Python 3.13 (repo já roda `>=3.13`; testado localmente contra o interpretador disponível no ambiente, 3.14.5, sem diferença de API relevante) ou reaproveitamento do que já existe no repo.

### `heapq` — "peças extras nos tamanhos com mais sobra"

API: `heapq.heapify`, `heapq.heappush`, `heapq.heappop` (min-heap; negar valores para simular max-heap) — [docs.python.org/3.13/library/heapq.html](https://docs.python.org/3.13/library/heapq.html). Para extras distribuídos peça a peça (round-robin pelo tamanho com mais sobra a cada iteração, sem posição fixa):

```python
import heapq

# sobra: dict[tamanho, int] já calculado (estoque - qtd_alocada por tamanho)
heap = [(-sobra, get_tamanho_idx(tam)) for tam, sobra in sobras.items() if sobra > 0]
heapq.heapify(heap)
for _ in range(pecas_extras_a_distribuir):
    neg_sobra, idx_tam = heapq.heappop(heap)
    alocar_extra(idx_tam, 1)
    neg_sobra += 1
    if neg_sobra < 0:
        heapq.heappush(heap, (neg_sobra, idx_tam))
```

Desempate deliberado por `get_tamanho_idx` (já existe em `value_objects.py`) em vez do texto cru do tamanho — é um índice numérico estável, então o heap nunca depende da ordem de inserção para desempatar sobras iguais. Se só é preciso o top-k de uma vez (sem consumo peça a peça), `heapq.nlargest(k, sobras.items(), key=lambda kv: (kv[1], -get_tamanho_idx(kv[0])))` evita ordenar a lista inteira quando k é pequeno.

### `fractions.Fraction` — orçamento ±5% em peças (inteiro, sem drift)

Verificado no interpretador local: `Fraction` implementa `__floor__`/`__ceil__` nativamente, então `math.floor`/`math.ceil` funcionam direto sobre frações exatas — sem passar por `float`:

```python
>>> from fractions import Fraction
>>> import math
>>> f = Fraction(505, 100)
>>> math.floor(f), math.ceil(f)
(5, 6)
```

Uso recomendado para o **ledger por pedido** (que agora atravessa produtos, ao contrário do `orcamento_aumento`/`limite_falta` de hoje, calculados por produto com `float * 0.05`):

```python
from fractions import Fraction
import math

tolerancia = Fraction(5, 100)  # em vez de 0.05 float
total_pedido = sum(item["qt_liquida"] for item in itens_do_pedido)  # int
orcamento_max_adicao = math.floor(total_pedido * tolerancia)
orcamento_max_corte = math.ceil(total_pedido * tolerancia)
```

Isso é estritamente mais barato e mais simples do que parece: como `total_pedido` é sempre `int`, a alternativa também legítima e mais direta é aritmética inteira pura (`(total_pedido * 5) // 100` para floor, `-(-total_pedido * 5 // 100)` para ceil), sem importar nada. **Recomendação: aritmética inteira simples, sem `Fraction`, a menos que o código vá manipular a fração em mais de um lugar** — `Fraction` só compensa se a tolerância virar um parâmetro de negócio com denominador variável (hoje é uma constante 5%, então inteiro puro é mais legível para a mantenedora). Vale a pena sinalizar como alternativa porque `total_grade * 0.05` com `float` (o padrão hoje em `motor_adequacao.py`) é seguro na prática para as magnitudes de pedido do domínio (dezenas/centenas de peças, longe da faixa de perda de precisão do `float64`), mas deixa de ser auto-evidentemente correto quando o valor passa a ser um contador acumulado ao longo de vários produtos — trocar para inteiro (ou `Fraction`) remove a dúvida de vez.

### `decimal.Decimal` — já em uso, é o certo para dinheiro

Nenhuma mudança de ferramenta aqui: o repo já usa `Decimal` com `ROUND_HALF_UP`/`ROUND_DOWN` e `quantize(Decimal("0.01"))` em `edicao_grade.py`. Reaproveitar (ver seção 4) em vez de reintroduzir arredondamento com `round()` (o que `adequar_grade_produto` faz hoje: `round(nova_qtd * preco_unit, 2)`, item a item, sem garantir que a soma bate com o total).

### `dataclasses(frozen=True, slots=True)` — value objects do ledger

`slots=True` no decorator `@dataclass` existe desde Python 3.10; combinado com `frozen=True` (sempre disponível), dá objetos imutáveis e compactos — verificado localmente:

```python
>>> from dataclasses import dataclass
>>> @dataclass(frozen=True, slots=True)
... class X:
...     a: int
...     b: str
>>> x = X(1, "a")
>>> x.a = 2
FrozenInstanceError
>>> X.__slots__
('a', 'b')
```

Uso recomendado: **só para o novo objeto de ledger por pedido** (ex.: `OrcamentoPedido(nr_pedido: int, restante_adicao: int, restante_corte: int)`), não para reescrever os itens de grade existentes (que continuam `dict`, como o resto do motor já faz — trocar tudo para dataclass seria reescrita sem necessidade, contra a constraint de "sem over-engineering"). `frozen=True` blinda contra o bug clássico de determinismo "alguém mutou o item depois de ele já estar ordenado/no heap"; `slots=True` reduz footprint por instância, relevante em memória com até ~200.000 itens por execução, embora o ganho real de memória seja secundário — o motivo principal é correção, não performance.

### `hashlib` — o requisito "plano hasheado" NÃO deve usar `hash()` builtin

Ponto crítico à parte, coberto em detalhe na seção 5: se "hasheado" significa comparar/persistir entre execuções e processos (idempotência, resume após crash), a função builtin `hash()` do Python é a ferramenta errada — ela é intencionalmente randomizada por processo para `str`/`bytes` (PEP 456) e não tem garantia de estabilidade nem entre processos nem entre versões do Python. A ferramenta certa é `hashlib.sha256` (ou `.md5`, mais barato, aceitável para hash de idempotência sem exigência criptográfica) sobre uma serialização canônica do plano — string ordenada e sem ambiguidade, não `repr()` de um dict cuja ordem dependeu de iteração de `set`.

---

## 4. Reaproveitar o rateio Hamilton de `edicao_grade.py`?

**Sim — a função `_allocate(total: Decimal, sizes: dict[str, int]) -> dict[str, Decimal]` (linhas 17-40 de `app/modules/pedidos/domain/edicao_grade.py`) é exatamente o utilitário certo para recalcular `vl_liquido` quando a quantidade muda na alocação**, e resolve um problema que existe hoje no motor atual.

O que `_allocate` faz, isolado da função que a envolve: dado um total em dinheiro (`Decimal`) e um mapa tamanho→quantidade, devolve tamanho→valor em `Decimal`, tal que a soma bate exatamente com o total em centavos — via maior resto (Hamilton), com desempate determinístico por nome do tamanho (`sorted(..., key=lambda item: (-item[2], item[0]))`, tudo `Decimal`/`int`, sem ponto flutuante). Isso é literalmente "distribuir uma quantia total entre categorias proporcionalmente ao peso de cada uma, sem perder nem sobrar centavo" — o mesmo problema de recalcular `vl_liquido` por tamanho depois que `qt_liquida` muda por adequação.

**O que NÃO reaproveitar:** a função-wrapper `montar_grade_atualizada` (que expõe `_allocate` hoje) tem uma invariante que **não vale** para o motor de alocação: `if new_qty != current_qty: raise ValueError(...)` — ela assume que edição de grade só redistribui tamanhos, nunca muda o total pedido. No motor de alocação isso é falso por definição (adequação existe para mudar a quantidade). Então o alvo do reaproveitamento é a função interna `_allocate`, não o wrapper.

**Ação concreta recomendada para a fase:** promover `_allocate` de função privada (`_allocate`, sem `__all__`) para utilitário público e compartilhado — por exemplo, extrair para `app/modules/pedidos/domain/rateio.py` (nome público, ex. `ratear_hamilton(total: Decimal, pesos: dict[str, int]) -> dict[str, Decimal]`) importado tanto por `edicao_grade.py` quanto pelo motor de alocação novo. Uso no motor novo:

```python
from decimal import Decimal
from app.modules.pedidos.domain.rateio import ratear_hamilton  # extraído de _allocate

qtd_alocada_por_tamanho: dict[str, int] = {...}  # resultado da alocação
qtd_total_alocada = sum(qtd_alocada_por_tamanho.values())
valor_total = (preco_unitario * qtd_total_alocada).quantize(Decimal("0.01"))
vl_liquido_por_tamanho = ratear_hamilton(valor_total, qtd_alocada_por_tamanho)
```

**Achado colateral que vale registrar para a fase:** o motor atual (`adequar_grade_produto`) calcula `vl_liquido` item a item com `round(nova_qtd * preco_unit, 2)`, **sem** a garantia de que a soma dos itens bate com o total da grade — é exatamente o tipo de drift que `_allocate`/Hamilton existe para eliminar. Migrar para o utilitário compartilhado não é só "reaproveitar código pronto": corrige um bug de arredondamento latente que já existe hoje, antes mesmo de a nova regra de negócio entrar.

---

## 5. Armadilhas de determinismo no Python 3.13 e como blindar

Ordenadas por risco real para este motor, não por ordem alfabética de tópico.

### `hash()` builtin para "hashear o plano" — CRÍTICO, verificar antes de mais nada

Se o requisito de idempotência/resume depende de comparar um hash do plano entre execuções (inclusive entre processos/workers, o que é o cenário de "resume após crash"), usar a função `hash()` embutida do Python é **ativamente errado**: por padrão, `str`/`bytes` têm hash randomizado por processo (PEP 456, `PYTHONHASHSEED` aleatório a cada start), então o mesmo plano lógico produz `hash()` diferente em processos diferentes mesmo sem nenhuma mudança de dado. **Prática correta:** serializar o plano em uma forma canônica (lista/tupla ordenada de forma explícita e total — nunca a ordem "natural" de um `dict` construído a partir de um `set`) e hashear essa serialização com `hashlib.sha256` (ou `.md5`, mais rápido, adequado para idempotência sem necessidade de resistência criptográfica) — não com `hash()`.

### `set` — ordem de iteração não é garantida, e depende de hash randomization

Qualquer `set` de `str` (ex.: `produtos: set[str]` em `_processar_pedidos_canal`, hoje só usado para inicializar `estoque_por_produto`/`disponivel_por_produto`, mas ponto de atenção se o motor novo usar sets de forma parecida) tem ordem de iteração dependente de `PYTHONHASHSEED`, que é aleatório por processo por padrão. **Prática:** nunca iterar um `set` (ou um `dict` construído a partir da iteração de um `set`) em qualquer trecho de código cujo resultado afeta a saída ou o consumo sequencial de um ledger — sempre `sorted(meu_set)` (com chave explícita se os elementos não tiverem ordem natural óbvia) antes de iterar.

### `dict` preserva ordem de inserção — mas a ordem de inserção em si pode não ser determinística

Desde Python 3.7, `dict` preserva ordem de inserção (isso não é o problema). O problema é **de onde vem** a ordem de inserção: se um `dict` é populado iterando um `set`, herda o não-determinismo do `set`; se é populado iterando uma lista vinda de uma query SQL **sem `ORDER BY` explícito**, herda o não-determinismo (real, ainda que raro) da ordem de retorno de linhas do banco. Isso já é uma dependência oculta do motor atual (`agrupar_por_produto` itera `dados`, que vem da leitura do banco) e fica mais crítico agora porque o ledger por pedido é **sensível à ordem de consumo** entre produtos do mesmo pedido. **Prática:** confirmar (ou adicionar) `ORDER BY` determinístico na query que popula `dados` (por `nr_pedido, cd_prod_cor` ou equivalente), e nunca assumir que a ordem "como chegou do banco" é estável entre execuções sem essa garantia explícita.

### `sorted()` é estável, não determinístico — a chave precisa desempatar até o fim

Estabilidade (Timsort) significa que elementos empatados na chave preservam a ordem relativa **de entrada** — não que a saída independe da ordem de entrada. Se a entrada já não é determinística (ver item acima), `sorted()` estável não resgata nada. Além disso, o código atual tem um ponto concreto de risco: `_prioridade()` em `_processar_pedidos_canal` retorna `(score, len(prods_disp))` — dois pedidos com mesmo valor total E mesma contagem de produtos disponíveis (raro, mas possível, especialmente em dados de teste/staging com poucos pedidos) caem no empate e a ordem final depende de `ordens_credito`, que vem de iterar `todos_pedidos_flat` (um `dict` construído por `dict.setdefault` iterando `dados` — de novo, a ordem do banco). **Prática recomendada para a fase nova:** adicionar `nr_pedido` como **último critério de desempate explícito** na chave de prioridade — `(score, len(prods_disp), nr_pedido)` — porque `nr_pedido` é sempre único, então essa chave nunca chega a depender de estabilidade de `sorted()` para desempatar. Isso é uma correção pequena e de baixo risco a incluir na mesma fase, já que a Regra 4 agora precisa ser uma ordem **total**, não só "quase total".

### `PYTHONHASHSEED` como rede de segurança, não como mecanismo principal

Fixar `PYTHONHASHSEED=0` (ou qualquer valor fixo) no ambiente do worker Celery torna a ordem de iteração de `set`/`dict`-de-set reproduzível *para a mesma sequência de inserções/remoções* — mas não substitui a disciplina acima, porque a ordem de um `set` no CPython depende também do histórico de inserção/remoção e de resize da tabela hash, não só do conteúdo final. **Prática:** tratar `PYTHONHASHSEED` fixo como camada extra de defesa (barato, sem custo, vale configurar), mas a garantia real de determinismo vem de nunca depender de ordem de `set`/dict-de-set no algoritmo, com `sorted()` de chave total em todo ponto onde a ordem afeta o plano.

### Soma de `float` é sensível à ordem — já mitigado pelo padrão `Decimal` do repo

`sum()` de `float` pode diferir no último bit dependendo da ordem de adição — irrelevante para R$ exibido ao usuário, mas relevante se o plano for hasheado byte a byte. Como o repo já usa `Decimal` para dinheiro (via Hamilton) e inteiros para quantidade, este risco já está coberto **desde que** a extensão para o ledger por pedido também use `Decimal`/`int` (não `float`) — reforça a recomendação da seção 3 de trocar `total_grade * 0.05` (float) por aritmética inteira no cálculo do orçamento.

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|------------------|
| Python stdlib (`heapq`) | 3.13 (stdlib, sem versão própria) | Selecionar tamanho com maior sobra para peças extras | Min-heap pronto, O(log n) por operação; evita reordenar a lista inteira a cada peça extra distribuída |
| Python stdlib (`fractions`/int puro) | 3.13 | Orçamento ±5% por pedido como contagem inteira exata | Elimina qualquer dúvida de arredondamento de `float * 0.05` num contador que agora atravessa produtos |
| `decimal.Decimal` (já em uso) | stdlib | Recalcular `vl_liquido` por tamanho sem drift | Já é o padrão do repo (`edicao_grade.py`); reutilizar, não substituir |
| `dataclasses(frozen=True, slots=True)` | 3.10+ (repo roda 3.13) | Value object do ledger por pedido | Imutabilidade evita bug clássico de item mutado após ordenado/no heap; `slots` reduz footprint em escala de ~200k itens |
| `hashlib.sha256` | stdlib | Hash do plano para idempotência/resume | `hash()` builtin é randomizado por processo (PEP 456) e não serve para comparação entre execuções — troca obrigatória |

### Supporting Libraries

Nenhuma. Este é o ponto central da pesquisa: a stdlib e o utilitário Hamilton já existente cobrem os cinco requisitos novos sem adicionar superfície de dependência.

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `PYTHONHASHSEED` (variável de ambiente) | Fixar ordem de iteração de `set`/dict-de-set como rede de segurança extra | Configurar no ambiente do worker Celery; não é substituto de `sorted()` com chave total |

## Installation

```bash
# Nenhuma dependência nova.
# Tudo usado (heapq, fractions, decimal, dataclasses, hashlib, math) é stdlib de Python 3.13.
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| Heurística gulosa determinística (stdlib) | OR-Tools CP-SAT | Se a Regra 4 mudar de "ordem estrita de prioridade" para "maximizar receita agregada ignorando prioridade individual" — um objetivo genuinamente combinatório, diferente do que foi ditado hoje |
| Heurística gulosa determinística (stdlib) | PuLP (CBC/HiGHS) | Se surgir uma restrição realmente acoplada nos dois sentidos entre pares (ex.: trocas/swaps entre pedidos para maximizar um critério global) que um passe ordenado não consiga expressar — não é o caso das 4 regras atuais |
| Heurística gulosa determinística (stdlib) | python-mip | Não recomendado em nenhum cenário deste projeto — projeto com manutenção efetivamente parada (último release PyPI em jan/2023) |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| OR-Tools CP-SAT | Dependência binária pesada (25-108 MB conforme plataforma), bugs de não-determinismo documentados mesmo com 1 worker em versões passadas, e resolveria um objetivo (maximização global) que viola a Regra 4 como ditada | Heurística de duas passadas em stdlib, com ordem de prioridade explícita |
| PuLP / python-mip (CBC) | Overhead de modelagem MILP para um problema sem restrições combinatórias reais; `python-mip` sem manutenção ativa desde 2023 | Idem acima |
| `round()` item a item para `vl_liquido` (padrão atual em `adequar_grade_produto`) | Não garante que a soma dos itens bate com o total da grade — drift de centavos já latente hoje | `ratear_hamilton` (extraído de `_allocate` em `edicao_grade.py`) |
| `hash()` builtin para hash de idempotência do plano | Randomizado por processo (PEP 456); não é estável entre execuções nem entre versões do Python | `hashlib.sha256` sobre serialização canônica |
| Iterar `set`/dict-construído-de-set em qualquer trecho que afete a saída ou o consumo do ledger | Ordem de iteração depende de `PYTHONHASHSEED`, não garantida entre execuções | `sorted(...)` com chave total (incluindo `nr_pedido` como desempate final) |
| `total * 0.05` em `float` para orçamentos de peças (contagem inteira) | Ponto flutuante para um contador que agora atravessa múltiplos produtos por pedido — risco crescente à medida que o ledger fica mais central | Aritmética inteira (`// 100`) ou `fractions.Fraction` |

## Stack Patterns by Variant

**Se o ledger de orçamento ±5% precisar ser compartilhado entre produtos do mesmo pedido:**
- Usar duas passadas: (1) alocação obrigatória por par respeitando furo de grade/tudo-ou-nada/estoque, na ordem de prioridade dos pedidos; (2) consumo do ledger de adição/corte, iterando os **produtos do próprio pedido ordenados por preço unitário decrescente** — não pela ordem em que os produtos aparecem nos dados.
- Porque é o único ponto onde a ordem de iteração tem impacto real em R$ (ver seção 2); todo o resto do desenho já é robusto à ordem de iteração desde que a Regra 4 seja aplicada como ordem total.

**Se no futuro a regra de negócio mudar para "maximizar receita agregada" (uma função-objetivo real, não uma ordem de prioridade):**
- Só então reabrir a discussão de solver — e nesse ponto o critério de decisão muda (não é mais sobre escala, é sobre a forma do objetivo).

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-------------------|-------|
| `dataclasses(slots=True)` | Python ≥ 3.10 | Repo roda `>=3.13` — disponível sem ressalva |
| `Fraction.__floor__`/`__ceil__` | Python 3.x moderno (verificado em 3.14.5 local) | `math.floor`/`math.ceil` despacham nativamente, sem conversão para `float` |
| `_allocate`/Hamilton (`edicao_grade.py`) | Já usa só `decimal` — nenhuma dependência de versão além do stdlib atual do repo | Extrair para módulo público sem mudar assinatura interna |

## Sources

- `app/modules/pedidos/domain/motor_adequacao.py` (lido integralmente) — motor atual, `_prioridade`, `adequar_grade_produto`
- `app/modules/pedidos/domain/edicao_grade.py` (lido integralmente) — `_allocate` (Hamilton), `montar_grade_atualizada`
- `.planning/PROJECT.md` § "Regras de negócio da alocação" — fonte autoritativa das 5 regras de negócio citadas
- Verificação local (Bash, Python 3.14.5): `Fraction.__floor__`/`__ceil__`, `dataclass(frozen=True, slots=True)` comportamento e `FrozenInstanceError`
- [ortools · PyPI](https://pypi.org/project/ortools/) — versão atual `9.15.6755` (jan/2026)
- [CP-SAT produces nondeterministic results · google/or-tools#3590](https://github.com/google/or-tools/issues/3590) — MEDIUM (issue de usuário, sem confirmação oficial de fix)
- [Non-determinism for CP-SAT with num_workers=1 · google/or-tools#3948](https://github.com/google/or-tools/issues/3948) — MEDIUM
- [Non-deterministic Behavior for CP-SAT with num_workers=1 · google/or-tools#3943](https://github.com/google/or-tools/issues/3943) — MEDIUM
- [Get nondeterministic results with different number of workers · google/or-tools#3842](https://github.com/google/or-tools/issues/3842) — MEDIUM
- [File Limit Request: ortools - 125 MB (Windows wheel) · pypi/support#3714](https://github.com/pypi/support/issues/3714) — HIGH (dado factual de infraestrutura, não opinião)
- [Goodbye python-mip? — Richard Oberdieck](https://oberdieck.dk/2024/11/12/goodbye-python-mip/) — MEDIUM, cruzado com o histórico de releases do próprio projeto (`coin-or/python-mip`, último release PyPI jan/2023)
- [heapq — docs.python.org/3.13](https://docs.python.org/3.13/library/heapq.html) — HIGH, documentação oficial
- [dataclasses — docs.python.org/3.13](https://docs.python.org/3.13/library/dataclasses.html) — HIGH, documentação oficial
- [fractions — docs.python.org/3.13](https://docs.python.org/3.13/library/fractions.html) — HIGH, documentação oficial

---
*Stack research for: motor de alocação de estoque com restrições de negócio (v1.3)*
*Researched: 2026-08-14*
