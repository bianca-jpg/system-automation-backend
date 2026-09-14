# automation OR — Otimização de Ordens de Reserva

## What This Is

Sistema de otimização de OR (Ordem de Reserva) e automação da adequação de pedidos do automation. Ingere pedidos e estoque do Databricks para o PostgreSQL (banco transacional) a cada 2h e roda o motor de adequação (matching pedido × estoque, isolado por canal Franquia/Multimarca) que gera as ordens de reserva consumidas pela operação comercial. Dois repositórios: backend FastAPI (`backend`, onde vive este planejamento) e frontend Next.js (`frontend`).

## Core Value

Transformar pedidos em aberto em Ordens de Reserva corretas e rastreáveis — com a grade certa por cliente e respeitando o estoque do canal — prontas para o ERP Linx consumir.

## Current Milestone: v1.3 Motor de alocação de OR fiel às regras de negócio

**Goal:** Fazer o processamento de OR reservar exatamente o que as regras de distribuição mandam — só para quem tem crédito e estoque — deixando os demais em stand by, visíveis e rotulados na aba de pedidos em aberto.

**Target features:**
- Filtro de crédito no modo **sem adequação** (hoje não existe: o modo seleciona todo par elegível)
- **Sem adequação** = tudo-ou-nada por par cliente×produto, com prioridade por valor do pedido e estoque compartilhado
- **Furo de grade**: tamanho do meio da grade pedida com 0 reservável bloqueia o par — nos dois modos
- **Orçamentos ±5% separados** (adição e corte não se compensam), medidos sobre a quantidade total do pedido completo do cliente, não por produto
- **Peças extras** alocadas nos tamanhos com mais sobra de estoque, sem posição fixa
- Guarda contra **OR zerada** (`ceil` → `floor` + guarda explícita no orquestrador) — fecha o warning WR-05
- **Visibilidade do stand by na UI**: tag "sem crédito" em todos os produtos do cliente bloqueado; tag "aguardando estoque" só nos produtos em que faltou peça para ele

Fonte: investigação de 2026-08-14 (motor, planner e UI lidos linha a linha) e regras de negócio ditadas pela mantenedora nesta data.

**Nota sobre numeração:** o número v1.3 foi originalmente atribuído à migração Aurora, **cancelada em 2026-08-10** sem entregar nada (o preflight revelou o Aurora já no schema 028 com ~1.4M linhas). O número é reaproveitado aqui; o diretório `.planning/phases/10-preflight-e-backup-recuper-vel` é resíduo daquele milestone cancelado.

## Milestone pausado: v1.2 Platform Hardening / Audit Remediation

**Pausado em 2026-08-14** para dar precedência ao v1.3 — regra de negócio incorreta no motor tem impacto operacional direto (ORs sendo geradas sem estoque e sem crédito), enquanto o hardening é dívida de plataforma sem sintoma para o usuário final.

**Estado preservado:** Fases 4–9. A Fase 5 (auth/OTP/seed/JWT fail-fast, SEC-01..07) está **concluída**, com code review contendo 3 críticos abertos em `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-REVIEW.md`. As fases 4, 6, 7, 8 e 9 seguem pendentes — várias já parcialmente resolvidas por commits regulares fora do fluxo formal (ver § Status update em `.planning/milestones/v1.2-PLATFORM-HARDENING.md`).

**Para retomar:** `.planning/milestones/v1.2-PLATFORM-HARDENING.md` + `.planning/CLAUDE-EXECUTION-BRIEF.md`. Nenhum diretório de fase do v1.2 foi apagado na troca de milestone.

Fonte: `.planning/research/AUDIT-2026-08-07.md` · `.planning/milestones/v1.2-PLATFORM-HARDENING.md`

## Requirements

### Validated

<!-- Inferidos do código existente (brownfield) — funcionam em produção/dev hoje. -->

- ✓ Ingestão Databricks → PostgreSQL a cada 2h (Celery Beat, full refresh transacional) — pré-v1.1
- ✓ Motor de adequação por grade com tolerância, isolamento por canal e bloqueio por crédito — pré-v1.1
- ✓ Geração de reservas com e sem adequação, persistidas por par (pedido, produto) em `ordens_reserva` — pré-v1.1
- ✓ Aprovação manual de OR e janela de 24h para histórico — pré-v1.1
- ✓ Edição de grade persistível via API (`pedido_modificacoes`) — refeita na Fase 3: `PUT /api/v1/pedidos/produtos/grades` grava OR + modificação + linha Linx na mesma transação; frontend usa (substitui o antigo `PUT /alterar-grade`, hoje `410 Gone`); só funciona pós-geração — GRADE-04 fechado, GRADE-01/02 não se aplicam ao desenho atual (ver REQUIREMENTS.md)
- ✓ Auth JWT + RBAC 5 níveis, parâmetros de negócio com change requests, comunicações por e-mail — pré-v1.1
- ✓ Tabelas `produto_tamanho_posicao` e `ordens_reserva_linx` (layout Linx) criadas pela migration 015, vazias, reversíveis, sem tocar tabelas existentes — Validated in Phase 1 (LINX-01, LINX-04)
- ✓ Referência tamanho→posição ingerida como 5ª fonte do sync (569.726 linhas em produção de dev, full refresh sem duplicar, guard de referência vazia) — Validated in Phase 2 (ING-01)
- ✓ Conversão pura da grade interna para as posições `e1..e48`, testada com dados reais já ingeridos — Validated in Phase 2 (LINX-03)
- ✓ Sincronizações serializadas por advisory lock: 2ª chamada recebe 409 (HTTP) ou `skipped` (beat) em vez de colidir no full refresh — correção fora de fase (commit 4e92aee)

### Active

<!-- Escopo do milestone v1.3. REQ-IDs detalhados em REQUIREMENTS.md. -->

Motor de alocação fiel às regras de negócio — REQ-IDs definidos em `.planning/REQUIREMENTS.md` § v1.3.

<!-- v1.1 (LINX-01..04, ING-01) concluído e movido para Validated acima. -->
<!-- v1.2 (CI/SEC/OPS/CONTRACT/OBS) pausado — ver § Milestone pausado. -->

### Regras de negócio da alocação (autoritativas, ditadas em 2026-08-14)

Fonte de verdade para o motor. Precedência declarada: regra 1 sempre → regra 4 → regras 2 e 3 juntas.

- **Crédito (ambos os modos):** cliente sem crédito nunca entra na OR; o pedido dele continua em pedidos em aberto até o status mudar.
- **Sem adequação:** tudo-ou-nada por par. Reserva exatamente a grade pedida ou o par fica em stand by; os demais produtos do mesmo pedido seguem elegíveis. Quando o estoque não dá para todos, atende na ordem de prioridade.
- **Regra 1 — maximizar faturamento:** pode reservar mais ou menos que o pedido, porque enviar com falta fatura mais que não enviar nada.
- **Regra 2 — ±5% com orçamentos separados:** até 5% de peças adicionadas E até 5% cortadas, contados **em separado** (não se compensam), medidos sobre a quantidade total do **pedido completo** do cliente. Sempre tentar entregar algo de cada produto.
- **Regra 3 — furo de grade:** considerando só os tamanhos que o cliente pediu, se algum tamanho estritamente entre o menor e o maior tiver 0 reservável, o par fica em stand by. Pelo menos 1 em cada tamanho do meio libera a reserva.
- **Regra 4 — prioridade por valor:** a entrega é agrupada fisicamente por cliente no CD, então na disputa pela mesma peça ganha o cliente cujo pedido total vale mais.
- **Regra 5 — entrega parcial:** pedido pode ser entregue incompleto; não se segura produto disponível só porque outro produto do mesmo pedido faltou.
- **Extras:** as peças adicionais entram onde há mais sobra de estoque (na prática os extremos, que giram menos), não em posição fixa.

### Out of Scope

- Cabeçalho de OR com número sequencial interno (255316+) — **substituído** pelo layout real do Linx; o "número da OR" é o próprio nº do pedido (coluna `PEDIDO`)
- Frontend/modal (o `255315` hardcoded) — fica para quando o fluxo com o ERP estiver claro; este milestone é só banco transacional
- Campos do Linx sem fonte conhecida (FILIAL, ROMANEIO, CAIXA, ENTREGA, REPRESENTANTE, PACKS, ITEM, etc.) — permanecem vazios até a usuária descobrir como obtê-los
- Integração efetiva com o barramento/Linx e status de envio — sem acesso ao barramento por limitação da equipe de TI; o banco fica preparado
- Persistência de pedidos em Stand By — comportamento atual (reaparecem para nova tentativa) atende
- ~~Ligar o frontend ao `PUT /alterar-grade`~~ — obsoleto: essa rota foi aposentada (`410 Gone`); o frontend já liga ao fluxo atual, `PUT /api/v1/pedidos/produtos/grades`

## Context

- Mapa do codebase em `.planning/codebase/` (7 documentos, 2026-08-04). Consultar antes de varreduras amplas.
- **Layout do Linx descoberto em 2026-08-04** via amostra `Tabelas_de_OR.csv` (Downloads da usuária): colunas NOME_CLIFOR, PRODUTO, COR_PRODUTO, PEDIDO, PRECO1, VALOR_EMBALADO, QTDE_EMBALADA, E1..E48 (grade posicional) e outras. A conversão posição↔tamanho usa a view Databricks `programa_estagio.refined.system_automation_prod_tamanho_ref` (`cd_prod_cor`, `sg_tamanho`, `nr_posicao`) — exemplo de query com `posexplode` fornecido pela usuária.
- Mapeamento conhecido: NOME_CLIFOR=cliente; PRODUTO/COR_PRODUTO=split de `cd_prod_cor` no `'|'` (ex. `"CL.35.0126|021"`); PEDIDO=`nr_pedido`; PEDIDO_PRODUTO/PEDIDO_COR_PRODUTO=denormalizações; PRECO1=preço unitário; VALOR_EMBALADO/QTDE_EMBALADA=totais da grade.
- A tabela `ordens_reserva` (JSONB, grão par pedido×produto — migration 014) segue sendo o modelo interno que alimenta a UI; a tabela Linx é a "bandeja de saída".
- A pesquisa em `.planning/research/` foi feita para o desenho anterior (cabeçalho+Identity): as partes de Identity/backfill ficaram obsoletas; seguem válidos os achados de stack (migration manual, sem dependências novas), pitfalls gerais (locks, contrato HTTP, testes) e o padrão "campo de correlação nullable para integração futura".
- `docs/ingestao.md`, `docs/adequacao.md` e `.claude/CLAUDE.md` já foram corrigidos (não citam mais 5 min, funções removidas ou filtro de mês corrente). O que ainda ficava defasado era `.planning/codebase/{STACK,STRUCTURE,ARCHITECTURE}.md` (head do Alembic e intervalo de ingestão) — corrigido em 2026-08-12.

## Constraints

- **Tech stack**: Python 3.13 + FastAPI + SQLAlchemy 2.0 async + asyncpg + Alembic — padrão do repo, sem novas dependências para este milestone
- **⛔ Design-system é obrigatório no front**: nenhuma alteração de UI sem antes **consultar os arquivos do design-system** (`frontend/design-system/`, internalizado e consumido como `"@system-automation/design-system": "file:./design-system"`) e seguir à risca o que está prescrito. A consulta vem antes da alteração, não é validação posterior. Se o componente necessário não existir lá, é decisão a levantar com a mantenedora — não improvisar no app.
- **Domínio puro sem I/O**: as regras novas (furo de grade, orçamento, políticas de alocação) vivem em `app/modules/pedidos/domain/`, testáveis sem banco — é o padrão já estabelecido do motor
- **Contratos preservados**: as chaves de retorno do motor (`resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados`) não mudam de forma, para não quebrar `build_processing_plan` nem a suíte existente
- **Resultado do job continua limitado**: a decisão de não devolver listas de itens no GET do processamento se mantém; a visibilidade do stand by vem por consulta REST paginada, não inflando o payload do job
- **Banco**: toda mudança de schema via migration Alembic manual (autogenerate evitado por precedente do repo; para a head atual rode `uv run alembic heads` — não confie em número anotado)
- **Processo**: commits Conventional Commits em branch `feat/` a partir de `develop`; `main` só em release; sem coautoria de Claude nos commits
- **Mantenedora**: dev iniciante — soluções simples e didáticas têm prioridade sobre otimizações sofisticadas
- **Working tree compartilhado**: há trabalho de SSO Microsoft não commitado de outra frente (`microsoft_sso.py`, migration 031, arquivos de auth). Sempre `git add` com caminhos explícitos e conferir `git show --stat` antes de commitar.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Tabela de saída no layout exato do Linx substitui o desenho "cabeçalho + nº sequencial interno" | A usuária obteve o schema real aceito pelo Linx; espelhá-lo elimina transformação no load futuro | — Pending |
| "Número da OR" = nº do pedido (coluna PEDIDO do Linx) | Confirmado pela usuária a partir da amostra real; não existe sequência própria de OR | — Pending |
| Ingerir `system_automation_prod_tamanho_ref` como 5ª fonte do sync | Sem a referência tamanho→posição não há como preencher a grade E1..E48, o coração da OR | — Pending |
| Campos do Linx sem fonte ficam NULL/vazios | Melhor entregar o layout completo com lacunas explícitas do que inventar valores | — Pending |
| Tabela Linx começa vazia (sem backfill) | Reservas antigas são história da fase de testes e nunca serão enviadas ao Linx | — Pending |
| `ordens_reserva` (JSONB) permanece intocada como modelo interno da UI | Separa modelo interno de formato de parceiro; zero risco de regressão na tela | — Pending |
| Colunas internas de controle na tabela Linx (id, nr_pedido, cd_prod_cor, tipo, created_at) | Permitem upsert idempotente e auditoria; o load futuro seleciona só as colunas do layout | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-14 — início do milestone v1.3 (motor de alocação fiel às regras de negócio); v1.2 pausado e preservado*
