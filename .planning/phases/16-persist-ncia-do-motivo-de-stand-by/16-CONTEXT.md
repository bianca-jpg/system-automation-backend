# Phase 16: Persistência do motivo de stand by - Context

**Gathered:** 2026-08-20
**Status:** Ready for planning
**Source:** Decisões da mantenedora (2026-08-14), pesquisa do milestone (`.planning/research/v1.3/ARCHITECTURE.md` § 3), o que as Phases 14/15 entregaram, e um gap de desenho achado nesta sessão (motivo de furo de grade).

<domain>
## Phase Boundary

Esta fase persiste **por que** cada par ficou de fora de uma OR, sem inflar o contrato do job, e conta há quantas execuções seguidas um par permanece parado.

**Entra:**
1. **STANDBY-01** — tabela nova, grão `(nr_pedido, cd_prod_cor)`, alimentada a partir do que `processar_pedidos` já calcula (`preteridos`, `bloqueados_credito`) mas que `build_processing_plan` hoje descarta.
2. **STANDBY-06** — contador de execuções consecutivas em stand by, por par, para dar visibilidade ao starvation aceito pela regra 4 (prioridade por valor).
3. **Escrita** dentro da mesma transação de `store_plan`; **limpeza** quando o par finalmente vira OR (`apply_pairs`) e quando sai do recorte de pendentes por outro caminho (full refresh da ingestão).

**Não entra:**
- Tags e filtro na UI (STANDBY-02, 03, 05) → **Phase 17**
- Rótulo próprio do furo de grade **na tela** (STANDBY-04, nominalmente Phase 17) → mas **o dado que sustenta esse rótulo precisa nascer aqui**, ver a seção "Gap de desenho" abaixo. Sem isso, a Phase 17 não tem o que renderizar.
- Peças extras por sobra de estoque (ALOC-11) → Phase 18
- Testes de propriedade (`hypothesis`) → Phase 19
- Qualquer alteração de frontend — esta fase é 100% backend

**Contratos protegidos (não mudam de forma):**
- As chaves de retorno de `processar_pedidos` (`resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados`).
- O resultado do job continua limitado a 4 contadores (`plannedCount`, `appliedCount`, `deferredCount`, `blockedCreditCount`) — sem listas de itens no payload do job. A visibilidade nova vem por **consulta REST** (que a Phase 17 usa), não pelo job.

</domain>

<decisions>
## Implementation Decisions

### Onde persistir — travado pela pesquisa do milestone
Tabela nova e dedicada, **fora** do ciclo de vida do job durável (`durable_jobs`/`pedido_processamento_plan` são efêmeros, com `ON DELETE CASCADE` e sujeitos a expurgo — amarrar a visibilidade a eles quebraria quando o job antigo fosse varrido). Segue o padrão já estabelecido no projeto para projeções recalculáveis (`estoque_virtual`, `pedido_produto_read`).

Grão: **par** `(nr_pedido, cd_prod_cor)`, com `PRIMARY KEY` composta. **Upsert** (`ON CONFLICT DO UPDATE`), nunca delete-then-insert — se o par continuar em stand by em rodadas sucessivas, a linha só atualiza (o motivo pode até mudar, ex. estoque→crédito).

### Ponto de extensão — sem tocar os contratos protegidos
`PlanDraft` (`processing/domain.py:264-296`) ganha campos novos que capturam o que `build_processing_plan` já tem em mãos e hoje descarta (linha ~662-666, por causa de `resultados_apenas_selecionados=True`):
- `deferred_pairs` — espelha `result["preteridos"]`
- `blocked_credit_pairs` — fan-out de `result["bloqueados_credito"]` × pares elegíveis daquele pedido (crédito é do cliente, então vira uma linha por produto que ele tentou reservar — implementa literalmente "tag em todos os produtos", que é o que STANDBY-02 vai precisar na Phase 17)

Novo método no port `ProcessingRepository` (`record_standby_reasons`, nome sugerido), chamado **dentro da mesma transação** de `store_plan`.

### Limpeza — dois pontos, ambos com precedente
1. **Em `apply_pairs`** (`repository.py:515-571`): ao gravar a OR de um par selecionado, apaga a linha correspondente de stand by — simétrico ao que já acontece ali com `pedidos_processados`/`ordens_reserva`/`estoque_virtual`.
2. **No full refresh da ingestão** (`reconstruir_pedido_produto_read`, já roda a cada 2h): apaga linhas cujo par não exista mais em `pedido_produto_read` com `source='pending'` — cobre o caso do pedido sair de "em aberto" por outro caminho (ex. confirmado direto no ERP).

Mesmo sem as duas limpezas rodarem no mesmo instante, a leitura nunca mostra uma tag errada: a Phase 17 vai sempre fazer `JOIN`/`LEFT JOIN` a partir da fonte autoritativa de "ainda pendente" — uma linha órfã simplesmente não casa com nada.

### ⚠ GAP DE DESENHO — motivo do furo de grade precisa ser distinto, e a pesquisa não previu isso

**Verificado no código nesta sessão:** o motor (`motor_adequacao.py`) já sabe diferenciar furo de grade de falta de estoque genérica — `tem_furo_de_grade` devolve um `motivo_furo` próprio, usado em `marcar_stand_by`. Mas isso só é escrito em `resultados[par]`, e `build_processing_plan` chama `processar_pedidos(..., resultados_apenas_selecionados=True)` — então **hoje esse motivo específico nunca chega a `processing/`**. O que sobrevive é só `preteridos: list[tuple[int, str]]`, uma lista de pares nus, sem o motivo anexado.

A pesquisa do milestone desenhou a coluna `motivo` com só dois valores (`'sem_credito' | 'sem_estoque'`, ver DDL em `ARCHITECTURE.md` § 3.2). **Isso não é suficiente**: o requirement STANDBY-04 (rótulo próprio para furo de grade, distinto de falta geral de estoque) está mapeado para a Phase 17, mas a Phase 17 é só UI — ela não pode inventar um dado que não existe. **Se esta fase persistir só dois motivos, o furo de grade fica indistinguível de falta de estoque genérica para sempre**, e a Phase 17 não tem como cumprir STANDBY-04.

**Decisão a ser fechada no planejamento desta fase (não travada ainda — é trabalho de design desta fase):** o `preteridos` do motor precisa, de alguma forma, carregar a distinção furo vs. estoque insuficiente até `pedido_standby_motivo`. Duas rotas possíveis, cabe ao planner escolher com justificativa:
1. **Aditiva no motor:** `processar_pedidos` ganha uma nova chave de retorno (ex. `preteridos_motivo: dict[tuple[int,str], str]`) que mapeia cada par preterido ao motivo específico (`'furo_grade'` vs `'sem_estoque'`), **sem alterar** a lista `preteridos` existente nem as demais chaves — só adiciona uma. Isso respeita "as chaves não mudam de forma" (nenhuma chave existente muda; uma nova é aditiva).
2. **Derivação em `processing/`:** reconstituir a distinção fora do motor, comparando `preteridos` contra uma segunda chamada ou contra dado já disponível em `produtos_elegiveis`/`falta_por_produto` (variáveis internas do motor, não expostas hoje).

A opção 1 é mais barata e mais robusta (a fonte da verdade continua sendo o motor, que já fez o cálculo). Registrar a escolha e a razão no `RESEARCH.md`/plano desta fase.

**A coluna `motivo` da tabela precisa dos três valores desde o início:** `'sem_credito' | 'sem_estoque' | 'furo_grade'` — mudar o `CHECK` constraint depois de dados em produção é migration extra evitável.

### STANDBY-06 — contador de execuções consecutivas
Ainda não desenhado em detalhe pela pesquisa do milestone (ela cobriu principalmente STANDBY-01). Requisito: "o sistema registra há quantas execuções consecutivas um par está em stand by". Decisão de negócio já travada (`STATE.md`): starvation por prioridade é **aceita** como consequência da regra 4, não corrigida — o contador existe para dar **visibilidade**, não para acionar nenhuma lógica de correção automática.

**Claude's Discretion:** desenho exato da coluna (`execucoes_consecutivas INTEGER`, incrementada no upsert quando o par já existia com o mesmo grão; resetada — ou removida — quando o par sai da tabela por ter sido atendido). Definir se o contador reseta ao mudar de motivo (ex. estava sem estoque, virou sem crédito) ou se conta "em stand by" de forma agnóstica ao motivo específico — a redação do requirement ("um par está em stand by") sugere agnóstico ao motivo, mas confirmar durante o planejamento é aceitável.

### Claude's Discretion (geral)
- Nome exato da tabela, dos métodos do port, e da migration.
- Se o fan-out de crédito (uma linha por produto do pedido bloqueado) roda no writer ou já vem pronto de `blocked_credit_pairs`.
- Formato exato dos textos de motivo (a pesquisa já sugere um mapeamento `sem_credito → "Bloqueado sem crédito"` etc., mas isso é consumido pela Phase 17, não fixado aqui).

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pesquisa e decisões
- `.planning/research/v1.3/ARCHITECTURE.md` § 3 (3.1 a 3.5) — o desenho mais detalhado já feito, incluindo DDL de referência, ponto de extensão do `PlanDraft`, limpeza e a mudança na consulta paginada (essa parte é Phase 17, mas ajuda a entender o contrato de leitura que esta fase precisa deixar pronto)
- `.planning/ROADMAP.md` § Phase 16 — goal e os 4 critérios de sucesso
- `.planning/STATE.md` § Decisions → "v1.3 — Motor de alocação" — starvation aceita, instrumentada não corrigida

### O que as Phases 14/15 entregaram (base desta fase)
- `app/modules/pedidos/domain/motor_adequacao.py` — `preteridos`, `bloqueados_credito`, `marcar_stand_by`, `tem_furo_de_grade` (via `furo_de_grade.py`)
- `app/modules/pedidos/processing/domain.py` — `build_processing_plan`, `PlanDraft` (linhas ~264-296, ~618-716)
- `app/modules/pedidos/processing/application/ports.py` / `infrastructure/repository.py` — `store_plan`, `apply_pairs`
- `app/modules/pedidos/infrastructure/repositorio_produtos.py` — CTE `pairs` do estágio `aguardando` (hoje `motivo=''` fixo) e `historico` (heurística pós-hoc a substituir na Phase 17)
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` — `reconstruir_pedido_produto_read` (full refresh, ponto de limpeza)

### Convenções
- `.claude/CLAUDE.md` · `.planning/codebase/CONVENTIONS.md` · `.planning/codebase/TESTING.md`
- Migrations manuais via Alembic (autogenerate evitado por precedente do repo)

</canonical_refs>

<specifics>
## Specific Ideas

### Ambiente e armadilhas conhecidas
- `uv run pytest` local falha no Windows (pyicu). Testes **sempre** por Docker: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- **Verde = exatamente 1 falha conhecida**: `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (teardown asyncpg pré-existente). Linha de base ao fim da Phase 15: **826 passed, 16 skipped, 1 failed**.
- **NÃO usar `gsd-tools query state.advance-plan`** — corrompe a frontmatter YAML do STATE.md deste projeto mesmo retornando erro. Atualizar com Edit.
- Worktrees desligados (`workflow.use_worktrees: false`) — os testes rodam num container que monta a árvore principal.
- Working tree compartilhado com trabalho de SSO Microsoft não commitado de outra frente. `git add` sempre com caminhos explícitos.
- Migration nova: rodar `uv run alembic heads` para a head real, não confiar em número anotado em documento.

</specifics>

<deferred>
## Deferred Ideas

- **Tags visuais e filtro** (STANDBY-02, 03, 05) → Phase 17, com a regra do design-system como critério de aceite obrigatório.
- **Extensão do filtro por status ao estágio `aguardando`** (hoje só existe no `historico`) — sinalizada pela pesquisa como decisão de UX a confirmar com a mantenedora, não bloqueia esta fase.
- **Substituição da heurística pós-hoc do estágio `historico`** pela fonte real (`EXISTS (SELECT 1 FROM pedido_standby_motivo ...)`) → Phase 17, junto com a leitura.
- **Peças extras por sobra** (ALOC-11) → Phase 18.
- **Testes de propriedade** (`hypothesis`) → Phase 19.

</deferred>

---

*Phase: 16-persist-ncia-do-motivo-de-stand-by*
*Context gathered: 2026-08-20, incluindo um gap de desenho (motivo de furo de grade) achado por leitura direta do código nesta sessão*
