# Project Research Summary

**Project:** automation OR — Motor de alocação de OR fiel às regras de negócio (v1.3)
**Domain:** Motor de alocação de estoque com restrições de negócio acopladas (order allocation / inventory rationing, automation de vestuário)
**Researched:** 2026-08-14
**Confidence:** HIGH

## Executive Summary

O v1.3 reescreve o núcleo de decisão do motor de adequação para implementar as 5 regras de negócio ditadas pela mantenedora: crédito bloqueia tudo, tudo-ou-nada no modo sem adequação com prioridade por valor e estoque compartilhado, furo de grade trava o par, orçamentos ±5% de adição/corte não-compensáveis medidos sobre o pedido completo do cliente, e peças extras onde há mais sobra.

Quatro pesquisas independentes convergiram num veredito único: **nenhuma dependência de produção é necessária**. A stdlib do Python 3.13 (`heapq`, aritmética inteira, `dataclasses(frozen, slots)`, `hashlib.sha256`) mais o utilitário Hamilton já existente em `edicao_grade.py` resolvem o problema inteiro. E o ponto mais importante: uma heurística gulosa ordenada **não é uma aproximação da Regra 4** ("ganha o cliente cujo pedido total vale mais") — é a implementação exata dela. Um solver (CP-SAT/MILP) otimizaria a coisa errada: maximizaria receita agregada **violando** a prioridade estrita que o negócio pediu, e ainda traria bugs de não-determinismo documentados e peso de dependência incompatível com o repo (25–108 MB vs ~3 MB da mais pesada hoje).

A pesquisa de features confirma que as regras centrais têm respaldo nomeado na prática do setor — *credit hold* no nível do cliente, *backorder* no nível da linha, *size run integrity* / *broken assortment* para furo de grade, *value-based allocation priority* para escassez. Não são invenções do negócio, são políticas reconhecidas de ERP de moda.

O maior risco não é escolha de tecnologia, é **desenho de estado**: arquitetura e pitfalls, de forma independente, chegaram à mesma correção crítica — o denominador do orçamento ±5% precisa ser o total **original** do pedido mais o acumulado consumido em execuções anteriores, nunca a soma dos itens ainda pendentes (que encolhe a cada rodada aprovada). Usar o snapshot corrente causa estouro do teto num cenário e perda de orçamento legítimo no outro, e nenhum teste de execução única detectaria.

## Key Findings

### Recommended Stack

Nenhuma dependência de produção nova. `heapq` para escolher o tamanho com mais sobra ao distribuir extras; aritmética inteira para o orçamento ±5% como contagem exata, eliminando o `float * 0.05` que hoje é seguro só por acidente de magnitude; `decimal.Decimal` (já em uso) para valores financeiros; `dataclasses(frozen=True, slots=True)` para o novo objeto de ledger por pedido; `hashlib.sha256` para o hash do plano — **já é o que o código faz** (`processing/domain.py:163`), confirmado nesta sessão.

A função `_allocate` (Hamilton / maior resto) de `edicao_grade.py:17-40` deve ser promovida a utilitário público e reaproveitada tanto no rateio de extras quanto para corrigir um drift de centavos **já latente hoje** em `adequar_grade_produto` (`round()` item a item sem garantir que a soma bate com o total).

**Core technologies:**
- `heapq` (stdlib) — distribuir peças extras nos tamanhos com mais sobra, O(log n) por operação
- Aritmética inteira (stdlib) — orçamento ±5% sem drift de float
- `dataclasses(frozen=True, slots=True)` — value object imutável do ledger por pedido
- `hashlib.sha256` — hash do plano (nunca `hash()` builtin, randomizado por processo)
- `_allocate`/Hamilton (já existe, promover a público) — rateio de extras e de `vl_liquido`

### Expected Features

**Table stakes, validados pela prática do setor:**
- Filtro de crédito no modo sem adequação — *credit hold* a nível de cliente (padrão Oracle/Infor/Dynamics)
- Furo de grade bloqueia o par nos dois modos — *size run integrity* (JD Edwards Apparel Management trata como exceção que trava o par; uma grade sem os tamanhos do meio é "functionally out of stock")
- Orçamento ±5% agregado por pedido completo
- Prioridade por valor do pedido — mais alinhada ao objetivo de receita do que pro rata ou FIFO
- Tags de stand-by na UI — sem isso o resto do milestone fica invisível

**Anti-features confirmadas a evitar:**
- Otimização global opaca (contraria a exigência de explicar por par cliente×produto)
- Realocação automática por cima de edição manual já persistida — **risco já neutralizado**: o SQL de pendentes exclui todo par presente em `ordens_reserva` (`processing/infrastructure/adapters.py:55-59`)
- Prioridade não-documentada "na cabeça de alguém" — que é exatamente o que este milestone corrige
- "Ship complete" rígido — a Regra 5 (entrega parcial) já evita

### Architecture Approach

O processamento durável (`app/modules/pedidos/processing/`) não muda de estrutura — ele só entende "recebo um `PlanDraft` determinístico e aplico em chunks resumíveis". O trabalho está em três frentes:

1. **Convergir `SEM_ADEQUAR` para o caminho global** (`build_processing_plan`). Prioridade global e estoque compartilhado são **incompatíveis por estrutura** com decisão página-a-página: uma página de 250 pares não sabe se, na página seguinte, existe pedido de maior prioridade disputando o mesmo produto. Memória medida (434 MiB reais / 1280 MiB reservados / 1536 MiB de limite) sustenta com ~900 MiB de folga.
2. **Remover o `StreamingPlanBuilder`** e tudo que só existe para alimentá-lo, na mesma entrega — não deixar código órfão divergindo em silêncio (este projeto já teve incidente exatamente assim).
3. **Canal lateral para o motivo do stand by**: `PlanDraft` passa a carregar `preteridos`/`bloqueados_credito` → tabela nova `pedido_standby_motivo`, grão `(nr_pedido, cd_prod_cor)`. Respeita os dois contratos protegidos (chaves do motor e contadores do job).

**Componentes principais:**
1. `domain/motor_adequacao.py` — modo tudo-ou-nada, orçamento por pedido completo, furo de grade, extras sem posição fixa
2. `processing/domain.py` — roteamento único por modo, captura dos preteridos em vez de só contá-los
3. `processing/infrastructure/repository.py` — correção de `_assert_stock_capacity`, novo `record_standby_reasons`
4. `infrastructure/repositorio_produtos.py` — leitura via `LEFT JOIN`, substituindo a heurística pós-hoc atual, que não conhece furo de grade nem prioridade

### Critical Pitfalls

1. **Double-spend do orçamento ±5% entre execuções** — denominador recalculado sobre itens pendentes (que encolhem) dá folga nova a cada rodada. Corrigir com total original imutável + acumulado consumido persistido, dois contadores independentes.
2. **Bug de OR zerada já ativo** — `ceil(total × 0,05)` com `total=1` dá 1, deixando `falta_total=1` passar como "Gerar OR" com estoque zero. Trocar por `floor` **e** adicionar guarda independente: nunca persistir par com `qt_liquida` total 0.
3. **Estado mutável vazando entre iterações** — o refactor de "por produto" para "por pedido" precisa de ledger nomeado e explícito, ordem de iteração fixada em código, e teste de permutação (mesmo pedido, produtos em ordens diferentes → resultado idêntico).
4. **Não-determinismo em empates** — `sorted()` é estável, não determinístico. A chave de prioridade precisa de `nr_pedido` como desempate final. `set` nunca deve ser iterado onde a ordem afeta resultado.
5. **`_assert_stock_capacity` pula revalidação para `tipo != "com"`** (`repository.py:489-513`) — hoje inofensivo porque `sem_adequar` não reserva estoque; vira gap real no instante em que passar a reservar. Nenhum teste existente falharia se for esquecido.
6. **Starvation por prioridade de valor** — real e **aceita** como consequência da regra 4. A resposta correta é instrumentar (contador de execuções consecutivas em stand by), não "corrigir" — corrigir contrariaria a regra ditada.

## Implications for Roadmap

Numeração começa na **Phase 13** (1–3 = v1.1; 4–9 = v1.2 pausado; 10–12 = v1.3-Aurora cancelada).

| Ordem | Entrega | Razão |
|---|---|---|
| 1 | Guarda contra OR zerada (`floor` + guarda independente) | Bug ativo, correção isolada e barata; paralelizável |
| 2 | Motor puro: ledger de orçamento por pedido, prioridade determinística, furo de grade | Dependência de todo o resto |
| 3 | Roteamento de `SEM_ADEQUAR` pelo global + remoção do streaming + correção de `_assert_stock_capacity` | Depende de 2 |
| 4 | Migration + tabela `pedido_standby_motivo` + escrita | Depende de 2 e 3 |
| 5 | Leitura do motivo real na UI (tags + filtro) | Depende de 4; **exige consulta prévia ao design-system** |
| 6 | Peças extras nos tamanhos com mais sobra | Paralelizável com 3–5 |
| 7 | Validação final / shadow run | Gate de saída |

**Research flags:** nenhuma fase precisa de pesquisa externa adicional.

## Decisões já tomadas pela mantenedora (fecham gaps da pesquisa)

| Gap levantado | Decisão (2026-08-14) |
|---|---|
| Dois orçamentos ±5% não-compensáveis são incomuns face à banda única do setor | **Mantido** o desenho duplo, com ciência da divergência |
| Rótulo próprio para furo de grade vs falta geral de estoque | **Rótulo próprio** |
| Filtro por motivo na aba Aguardando (hoje só no histórico) | **Criar**, reusando o padrão do histórico |
| `hypothesis` como dependência nova | **Aprovada**, só como dev-dependency |
| Orçamento quando o pedido é alterado no meio do processo | **Continua debitado**, não zera |
| Starvation por prioridade | **Aceita**, com instrumentação (STANDBY-06) |

**Verificado nesta sessão, não é mais questão em aberto:** o hash do plano já usa `hashlib.sha256` sobre JSON canônico (`processing/domain.py:163-164`); a consulta de pendentes já tem ordenação total determinística (`ORDER BY nr_pedido DESC, cd_prod_cor, sg_tamanho, id`, `adapters.py:148`); reprocessamento nunca sobrescreve OR editada manualmente (`adapters.py:55-59`).

**Resta medir, não decidir:** o pico real de memória do modo tudo-ou-nada depois do roteamento — o volume elegível de `sem_adequar` pode ser maior que o medido para `adequar`, já que hoje ele aceita todo par sem filtro.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Verificado por execução local e evidência pública |
| Features | MEDIUM | Sem Context7 aplicável ao domínio; múltiplas fontes de ERP de moda convergem |
| Architecture | HIGH | Conferido linha a linha no código, com arquivo:linha |
| Pitfalls | HIGH / MEDIUM | Bugs concretos confirmados por leitura; padrões gerais MEDIUM |

**Overall confidence:** HIGH

## Sources

Ver `STACK.md`, `FEATURES.md`, `ARCHITECTURE.md` e `PITFALLS.md` nesta mesma pasta para a lista completa e o detalhe de cada achado.

---
*Research completed: 2026-08-14*
*Ready for roadmap: yes*
