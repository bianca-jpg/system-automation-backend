---
gsd_state_version: 1.0
milestone: v1.3
milestone_name: Motor de alocação de OR fiel às regras de negócio
current_phase: 20
current_phase_name: trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ
status: complete
last_updated: "2026-09-09T17:00:00.000Z"
last_activity: 2026-09-09
last_activity_desc: "Fix não relacionado (commit c579a55), achado ao validar a fase 21 na tela: GET /api/v1/pedidos/resumo estourava o timeout de 25s do frontend (~29s de execução) por um plano de execução catastrófico do Postgres — CTEs pending_items/pending_orders materializadas sem estatística real levavam o planner a escolher Nested Loop no JOIN da CTE demand (246 milhões de comparações de linha nesta escala de dados). SET LOCAL enable_nestloop/jit=off no request, sem mudar nenhum resultado: 29s → 1.7s. Bloqueava tanto o card de resumo geral quanto as ações em lote de pedidos em aberto (mesmo endpoint)."
progress:
  total_phases: 8
  completed_phases: 4
  total_plans: 22
  completed_plans: 22
  percent: 50
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-08-14)

**Core value:** Transformar pedidos em aberto em Ordens de Reserva corretas e rastreáveis — com a grade certa por cliente e respeitando o estoque do canal — prontas para o ERP Linx consumir.

**Current focus:** Phase 20 concluída (2026-09-08) — trava de orçamento ±5% ligada à edição manual "Sem Adequação", backend fechado. Frontend (D-12 a D-16, requirement UI-02) fica para quick-task separada no `frontend`. Milestone v1.3 segue com Phases 13, 17, 18, 19 ainda abertas.

## Milestone Transition

| Milestone | Status | Notes |
|-----------|--------|-------|
| **v1.1** Saída de OR no formato Linx (Phases 1–3) | **Complete / near-complete** | Schema + ingestão referência + escrita Linx entregues no ROADMAP (2026-08-04 → 2026-08-06). LINX-02 marcado complete no roadmap; conferir checkbox residual em REQUIREMENTS se necessário. |
| **v1.2** Platform Hardening / Audit Remediation (Phases 4–9) | **⏸ PAUSADO em 2026-08-14** | Pausado para dar precedência ao v1.3 (regra de negócio incorreta tem impacto operacional direto; hardening é dívida sem sintoma para o usuário). **Fase 5 concluída** (SEC-01..07), com 3 críticos abertos em `.planning/phases/05-auth-otp-seed-jwt-fail-fast-security/05-REVIEW.md`. Fases 4, 6, 7, 8, 9 pendentes (várias já parcialmente resolvidas fora do fluxo formal). **Nenhum diretório de fase foi apagado.** Retomar por `.planning/milestones/v1.2-PLATFORM-HARDENING.md` + `.planning/CLAUDE-EXECUTION-BRIEF.md`. |
| **v1.3** Motor de alocação de OR fiel às regras de negócio | **Roadmap criado (2026-08-14)** | Crédito no modo sem adequação, tudo-ou-nada por par, furo de grade, orçamentos ±5% separados por pedido, extras por sobra de estoque, guarda contra OR zerada, e tags de stand by na UI. 7 fases (13–19), 21/21 requirements mapeados. Fonte: investigação de 2026-08-14 + regras ditadas pela mantenedora. |

## Current Position

Phase: 20 (trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ) — **CONCLUÍDA (5/5 planos executados, verificada `passed`)**
Plan: 5 de 5 executados (20-01 a 20-05). Ordem: 20-01 (SQL/ledger) ∥ 20-02 (domínio, total variável) → 20-03 (trava 409, escrita) ∥ 20-04 (orçamento agregado, leitura) → 20-05 (salvar+aprovar fundido)
Status: **Fechada.** Code review achou 1 crítico (CR-01: rewind+recobrança bloqueava resalvamento de OR legada já acima do limite) + 2 avisos — todos corrigidos e commitados (`8e751c0`, `fd6e367`, `2676b45`). Verificação: 5/5 must-haves confirmados direto no código, suíte completa 981 passed/18 skipped/0 failed. Único item de `human_verification` (política de grandfathering do CR-01) aprovado pela mantenedora em 2026-09-08 — ver `20-VERIFICATION.md`. **Nota:** `REQUIREMENTS.md:37` (GRADE-03) ainda descreve o gap pré-fase ("nenhuma checagem explícita de ±5%") — ficou desatualizado porque é prosa livre, não formato checkbox, e a ferramenta de auto-marcação não conseguiu atualizá-lo; precisa de edição manual.
Next: `/gsd-plan-phase 13`, `17`, `18` ou `19` (paralelizáveis, nenhuma depende de 20) — ou a quick-task de frontend no `frontend` para D-12–D-16/UI-02.
Last activity: 2026-09-08 — Phase 20 complete

## Milestone v1.2 (paralelo, Victoria) — herdado do origin/develop em 2026-08-25

Estado de v1.2 no momento do merge (trabalho da Victoria, não tocado por esta sessão — preservado aqui só para não se perder):

Phase: 11 (par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito) — PLANEJADA, 1/5 EXECUTADA
Plan: 5 planos escritos; 1 executado
Status: 11-01 complete; 11-02..11-05 escritos e aguardando execução — mas ver conflito abaixo

**Correção 2026-08-18:** este bloco dizia "11-02..11-05 NÃO PLANEJADO" e estava errado.
Os quatro arquivos existem em disco desde antes (258–347 linhas cada, frontmatter completo).
Conferido com `wc -l` e leitura do frontmatter.

- 11-01 (registro de chaves do motor + validar_valor_de_parametro) — complete, 21 testes verdes
  (commits `95499bb` feat + `42290d8` test, em develop, sem push)

- 11-02 (migration 031: requested_by_role, reviewed_by_role, previous_payload, origem) — ESCRITO
- 11-03 (solicitação sem 422, justification obrigatória, selo consumido_pelo_motor no GET) — ESCRITO
- 11-04 (aprovação aplica o valor na mesma transação) — ESCRITO ← é o que fecha PARAM-02
- 11-05 (PUT como válvula de emergência restrita a admin_técnico) — ESCRITO, **INVÁLIDO**

⛔ **11-05 precisa ser reescrito.** Ele assume "administrador passa pelo formulário como todo
mundo" e restringe a escrita direta a admin_técnico. A decisão de 2026-08-18 revogou isso:
administrador(40)+ edita/exclui pelas Ações **com efeito imediato**, e `PUT`/`DELETE` viram o
caminho normal, não válvula de emergência. Ver ROADMAP § Phase 11 → "Decisões de negócio
(Victória, 2026-08-18)".

⚠️ **Buraco de cobertura:** os 5 planos são só de backend (todo `files_modified` é `app/...`).
O trabalho de **frontend** não está planejado em lugar nenhum — hoje o menu de Ações troca o
RÓTULO por papel mas chama sempre `requestParameterUpdate` (422), e nenhuma tela chama
`PUT`/`DELETE /parametros/{chave}`. Precisa de um plano novo (11-06).

Phase 12 (exceções de parâmetro por cliente + bloqueio de recebimento) — adicionada ao ROADMAP
em 2026-08-18, NÃO PLANEJADA. Depende da 11.

✅ **Resolvido no merge de 2026-08-25:** 11-02 reservava `alembic/versions/031_parametros_auditoria_de_solicitacao.py`
(`revision = "031"` / `down_revision = "030"`), que colidia com a migration 031 do SSO. Como a
Fase 16 (v1.3) já ocupa a `032` (`pedido_standby_motivo`, `down_revision = "031"`), quando a
Victoria retomar 11-02 ela precisa renumerar para **`033`** / `down_revision = "032"` — não mais
`032` como o texto antigo dizia. A referência em `11-01-PLAN.md` (linhas ~281 e ~287) e o nome do
arquivo de teste (`test_migration_031_parametros_auditoria.py`) também precisam acompanhar esse
número atualizado.

A tabela de cobertura dentro de `11-01-PLAN.md` é a fonte do escopo dos planos.

Phase 05 (auth-otp-seed-jwt-fail-fast-security) — PAUSED em 2 de 5 planos:

- 05-01 (seed default + runbook) — complete
- 05-02 (otp_hash + migration, hoje numerada 030) — complete
- 05-03 (hash em repouso + entrega de OTP por e-mail) — BLOQUEADO por credenciais SMTP
- 05-04 (revogação de sign-out), 05-05 (rate limit) — pendentes, encadeados após 05-03

Last activity: 2026-08-27 — Completada quick task 260827-f8o: GET /health/ready sanitizado (nome de classe de exceção não vaza mais na resposta pública, só no log do servidor); antes, quick task 260827-emo: GET /metrics fechado atrás de guard por API key (X-Metrics-Key)

**Nota do merge (2026-08-25):** esta sessão confirmou que 05-03/05-04/05-05 já foram entregues no
local (hash em repouso + entrega de OTP em PROD, revogação de sign-out via Redis, rate limit) —
ver `## Entrega fora do fluxo GSD — SSO Microsoft Entra ID` abaixo para o que ainda é exclusivo do
origin.

## Entrega fora do fluxo GSD — SSO Microsoft Entra ID (2026-08-20)

Não passou por fase nem quick task; registrado aqui em 2026-08-21 para o planning parar de
divergir do código. **Não existe requirement `AUTH-*` cobrindo SSO** em REQUIREMENTS.md — se
isso vira escopo formal de v1.2, precisa entrar lá.

**Backend — já em `develop`**, mergeado via PR #2 (`9b9fdab`), e confirmado presente no local
após o merge de 2026-08-25 (o local já havia convergido para o mesmo modelo de provisionamento
automático, num commit próprio anterior a este merge):

- `939302a` feat(auth): login via Microsoft Entra ID — **aditivo** (+192/−2). O login local
  segue inteiro (`/sign-in`, `/sign-in/confirm-new-password`, `/token/refresh`, `/register`…) —
  **mantido de propósito**, por decisão da mantenedora em 2026-08-25, para servir de comparação
  entre os níveis de acesso durante testes; não precisa enviar e-mail de verdade fora de
  produção (ver política de OTP abaixo).
  Novo: `POST /api/auth/sso/microsoft`, `app/modules/auth/infrastructure/microsoft_sso.py`.

- `faf3869` fix(auth): migration 031 (`auth_users.auth_provider` + `password_hash` nulo).
- Config nova: `MICROSOFT_TENANT_ID` / `MICROSOFT_CLIENT_ID`. Vazias = o endpoint responde
  **503**, em vez de tentar validar contra um tenant inexistente.

**Frontend — Correção 2026-08-24:** este bloco dizia "NÃO está em develop" e estava errado.
O merge do SSO (`cbb7c8f` + `5da8cd7`, de `origin/review/auth-microsoft-sso`) **já está no
`develop` local** desde antes desta sessão (commit de merge `196c9d4`) — só nunca foi dado
`push`; `origin/develop` do frontend seguia sem eles. Nenhum PR foi aberto (os PRs #1 e #2 do
repo são antigos e já mergeados).

`cbb7c8f` **substituía** o login local (−692 linhas em `LoginPage.tsx`), o que deixaria quem
subisse esse `develop` sem caminho de login caso `AUTH_MICROSOFT_ENTRA_ID_ID`/`_SECRET`/`_ISSUER`
não estivessem provisionados em algum ambiente. Corrigido em 2026-08-24 (commit `10bbe9f`,
ainda sem push): o login local (usuário/senha, cadastro, recuperação por OTP) voltou a
`LoginPage.tsx` como opção ao lado do botão "Entrar com Microsoft" — `auth.ts` agora registra
os dois providers do NextAuth (`Credentials` + `MicrosoftEntraID`) lado a lado, sem que um
dependa do outro. `design-system/` não foi tocado. 46 testes de auth passando; suíte completa
do frontend com 500/506 (as 6 falhas são pré-existentes, confirmado via `git stash` antes do
commit — não relacionadas a este trabalho).

**Atualização 2026-08-26 (verificado em sessão separada de frontend):** `develop` do frontend
está **sincronizado com `origin/develop`** — o push mencionado abaixo já foi feito em algum
momento entre 24/08 e 26/08. Confirmado via `git status -sb` no repo do frontend: `## develop...
origin/develop` sem "ahead"/"behind". A pendência de push descrita no parágrafo original não
existe mais.

**Política de envio do OTP por e-mail (decisão da mantenedora, 2026-08-25):** o local e o origin
implementaram isso de formas diferentes (local: só envia e-mail de verdade em `ENV=PROD`; origin:
envia sempre que houver SMTP configurado, mesmo fora de PROD). **Prevaleceu a versão local** —
fora de produção o código nunca sai por e-mail de verdade, só fica registrado internamente, já que
o login local existe agora só para fins de teste/comparação de papéis.

**Implicação operacional (migration 031):** vale de novo a lição do bloco de Blockers — o
`conftest.py` não roda alembic. `system_automation_test` precisa de `alembic upgrade head` à mão,
senão `test_schema_guard.py` acusa `auth_provider` ausente:

```
DATABASE_URL=...system_automation_test ./.venv/Scripts/python.exe -m alembic upgrade head
```

## Performance Metrics

**Velocity (v1.1):**

- Total plans completed: 10 (Phases 1–3)
- Average duration: —
- Total execution time: —

**By Phase (v1.1):**

| Phase | Plans | Status |
|-------|-------|--------|
| 1 Schema Linx | 2/2 | Complete |
| 2 Ingestão + conversão | 5/5 | Complete |
| 3 Escrita Linx | 3/3 | Complete |

**By Phase (v1.3):**

| Phase | Plans | Status |
|-------|-------|--------|
| 13 Guarda contra OR zerada | TBD | Not started |
| 14 Motor de alocação puro | 7/7 | **Complete** |
| 15 Roteamento global sem adequação | 5/5 | **Complete** |
| 16 Persistência do motivo de stand by | 5/5 | **Complete** |
| 17 Visibilidade do stand by na UI | TBD | Not started |
| 18 Peças extras nos tamanhos com mais sobra | TBD | Not started |
| 19 Validação final e shadow run | TBD | Not started |

**Plan Metrics (14-01):**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| 14-01 Extração de ratear_hamilton | 10min | 3 tasks | 3 files |
| 14-02 Factories, cenário da mantenedora e invariantes | ~35min (retomada) | 3 tasks | 6 files |
| 14-03 Desempate determinístico de prioridade por nr_pedido | ~20min | 2 tasks | 2 files |
| 14-04 Furo de grade (ALOC-04) | ~30min | 2 tasks | 3 files |
| 14-05 Política de quantidade por modo (ALOC-01/02/03) | ~30min | 4 tasks | 5 files |
| 14-06 Ledger de orçamento por pedido + duas passadas + rateio (ALOC-07/08/09/10, FIX-02) | ~90min | 2 tasks | 5 files |
| 14-07 Migração consciente da suíte de testes para a semântica pós-14-06 | ~45min | 2 tasks | 2 files |
| 15-01 Leitura de orçamento por pedido (aditivo, invariante J5) | ~30min | 2 tasks | 3 files |
| 15-02 FIX-01 — revalidação de estoque para tipo="sem" | ~15min | 1 task (TDD) | 2 files |
| 15-03 Roteamento global + fechamento ALOC-09 + remoção do streaming | retomado após interrupção (Tasks 1-2 de execução anterior + Tasks 3-4 desta sessão) | 4 tasks | 8 files (4 produção, 4 testes + 1 deletado) |
| 15-05 Atualização de docs/adequacao.md | ~25min | 3 tasks | 1 file |
| 15-04 Medição de pico de memória de sem_adequar (instrumentação nova) | ~15min | 2 tasks (1 commit) | 1 file |
| 16-01 Vocabulário canônico + preteridos_motivo (furo de grade vs sem estoque) | ~30min | 2 tasks | 4 files |
| 16-02 Migration 032 + model PedidoStandbyMotivo (retomado após queda de conexão) | ~20min (Task 3 desta sessão) | 3 tasks | 4 files |
| 16-03 PlanDraft ganha deferred_pairs/blocked_credit_pairs + fan-out de crédito | ~35min | 2 tasks (TDD) | 2 files |
| 16-04 record_standby_reasons (upsert chunkado) + wiring transacional + limpeza em apply_pairs | ~40min | 3 tasks (TDD) + 1 estilo | 4 files |
| 16-05 Limpeza de órfãos de pedido_standby_motivo no full refresh da ingestão | ~25min | 3 tasks | 2 files |

*Updated after each plan completion*
| Phase 20 P01 | ~15min | 3 tasks | 5 files |
| Phase 20 P02 | 25min | 2 tasks | 3 files |
| Phase 20 P03 | 45min | 3 tasks | 6 files |
| Phase 20 P04 | ~25min | 2 tasks | 3 files |
| Phase 20 P05 | 50min | 2 tasks | 4 files |

## Accumulated Context

### Decisions

#### v1.3 — Motor de alocação (decididas pela mantenedora em 2026-08-14)

| Decisão | Escolha | Razão |
|---|---|---|
| Semântica do modo sem adequação | **Tudo-ou-nada por par** cliente×produto | Não dá para reservar "quase" a grade; os demais produtos do mesmo pedido seguem elegíveis |
| Medida dos 5% | **Orçamentos separados** de +5% e −5%, sem se compensarem, sobre o pedido completo | Escolha consciente; a pesquisa apontou que o padrão do setor é banda única 95–105%, e a mantenedora manteve o desenho duplo |
| Furo de grade | Vale nos **dois modos**, medido só entre os tamanhos que o cliente pediu | Validado pela prática do setor (*size run integrity* / *broken assortment*) |
| Peças extras | Onde houver **maior sobra de estoque**, sem posição fixa | Substitui o hardcode atual de "tamanhos extremos" |
| Orçamento entre execuções | **Continua debitado** (não zera quando o pedido é alterado) | Impede o cliente ganhar 5% novos a cada rodada e estourar o limite no agregado |
| Tag do furo de grade | **Rótulo próprio**, distinto de falta de estoque | "Faltou só o M e travou a grade" pede ação diferente de "faltou quase tudo" |
| Filtro na aba Aguardando | **Criar**, reusando o padrão que o histórico já tem | Permite isolar quem está travado por crédito vs por estoque |
| `hypothesis` | Aprovada **só como dev-dependency** | Única forma de provar a invariante do orçamento ao longo de várias execuções; não vai para a imagem de produção |
| Starvation por prioridade | **Aceita** como consequência da regra 4, mas instrumentada | Corrigir contrariaria a regra ditada; o certo é dar visibilidade (STANDBY-06) |

**Correção de desenho durante a pesquisa:** a base do orçamento ±5% **não** pode sair do snapshot de pendentes (que encolhe a cada execução, causando estouro em um cenário e perda de orçamento em outro). Precisa do total **original** do pedido mais o acumulado já consumido em ORs anteriores — derivável de `qt_solicitada`/`qt_liquida` já gravados.

**Garantia verificada (não era explícita):** reprocessar o motor nunca sobrescreve OR editada manualmente — o SQL de pendentes exclui todo par já presente em `ordens_reserva` (`processing/infrastructure/adapters.py:55-59`).

**Regra permanente de processo:** nenhuma alteração de front-end sem antes consultar os arquivos do design-system e seguir à risca o que está prescrito.

#### v1.3 — Roadmap (decisões do roadmapper, 2026-08-14)

| Decisão | Escolha | Razão |
|---|---|---|
| Ordem das 7 fases (13–19) | Guarda OR zerada ∥ Motor puro → Roteamento global → Persistência standby → UI standby (∥ Peças extras) → Validação/shadow run | Segue literalmente a ordem de dependência real apurada em `ARCHITECTURE.md`/`PITFALLS.md`/`SUMMARY.md`; motor puro é o único bloqueio real de tudo que vem depois dele |
| Escrita (Phase 16) separada de leitura/UI (Phase 17) | STANDBY-01/06 na escrita; STANDBY-02/03/04/05 na UI | STANDBY-02..05 descrevem o que o analista vê (tag, rótulo, filtro) — comportamento observável de UI; STANDBY-01/06 são mecanismo de persistência sem UI própria |
| FIX-02 (drift de centavos) agrupado com a Phase 14 | Motor puro | O bug já existe em `adequar_grade_produto`, que é reescrito na mesma fase para o novo orçamento ±5%; corrigir junto evita reabrir o mesmo arquivo depois |
| FIX-01 (revalidação de capacidade para tipo "sem") agrupado com a Phase 15 | Roteamento global | Só vira gap real quando `sem_adequar` passa a reservar estoque compartilhado, o que só acontece quando é roteado pelo caminho global |

#### v1.1 / v1.2

Decisões Linx (v1.1) permanecem válidas — ver PROJECT.md e histórico abaixo. Decisões de hardening (v1.2, pausado) serão registradas quando as Phases 4–9 forem retomadas.

Relevantes para o próximo trabalho:

- Referência de padrão operacional: Collab API (`collab-backend`) — CI quality gates antes do ECS, task-defs versionadas para api/worker/beat, migrations no deploy, Docker sem `--group dev`.
- Auth local JWT HS256 + seed + OTP sem entrega em PROD são gaps P0 do audit — não “features futuras”; entram em Phase 5.
- INTG-* continua fora do escopo de v1.2 Platform Hardening. Edição de grade: GRADE-04 já implementado (fora do fluxo formal); GRADE-01/02 não se aplicam ao desenho atual; só GRADE-03 (parcial) e UI-02 restam abertos — ver STATE.md § Deferred Items.
- [Phase 20]: PD-01: correção do Pitfall 1 não remove o filtro de tipo — adiciona ramo próprio para 'sem' medido pelo total do par, preservando o ramo 'com' por item byte-idêntico (D-01)
- [Phase 20]: PD-02: cobrança de orçamento por ESTADO FINAL do par via rewind (subtrai contribuição atual antes de recobrar), não por delta da edição — evita cobrar adição ao desfazer corte e garante idempotência em re-salvamentos
- [Phase ?]: Fallback de qt_solicitada ausente/vazia preserva 0 explícito (não trata 0 como vazio) ao cair para qt_liquida — Torna alcançável o caso de borda de linha de base zero pedido pelo plano 20-02, sem reinflar silenciosamente a base para a quantidade nova
- [Phase ?]: 20-03: Linha de base do par (_linha_base_par) duplicada localmente em casos_uso.py em vez de extraída para edicao_grade.py, para respeitar o acceptance criteria que exige diff vazio naquele arquivo neste plano
- [Phase ?]: 20-03: contrato do 409 de orçamento excedido (code=orcamento_pedido_excedido, nrPedido, orcamento, restanteAdicao, restanteCorte) fixado em routes.py para o plano 20-06 consumir
- [Phase 20]: 20-04: campo orcamentoPedido embutido opcional (default None, chave ausente quando 'com adequacao') em PedidoCardOut, decidido em PD-08 para casar com o passthrough do frontend e o medidor por linha do 20-UI-SPEC.md
- [Phase 20]: 20-04: limite/restante do orcamento embutido sempre derivados de OrcamentoPedido (PD-09) - projecao nunca reescreve floor(total*tolerancia), gate de ruff/grep proibe literal 0.05/1.05 no arquivo
- [Phase 20]: 20-05: fusão salvar+aprovar em executar_alteracao_grades_produto escopada aos nr_pedido do lote + cd_prod_cor do produto (PD-10), só quando TODO o lote é sem adequação (PD-11), chamando o port de aprovação inline antes do único db.commit() (PD-12) — approved_count/approvedCount novo em AlterarGradesProdutoResponse

### Histórico de decisões v1.1 (preservado)

- O layout real do Linx (`Tabelas_de_OR.csv`) substitui o desenho anterior de cabeçalho com numeração sequencial própria.
- "Número da OR" = número do pedido (coluna `PEDIDO` do Linx).
- Migration 015 cria `produto_tamanho_posicao` e `ordens_reserva_linx`; `ordens_reserva` (JSONB) permanece intocada.
- [Phase 1–3]: ver SUMMARYs em `.planning/phases/01-*`, `02-*`, `03-*`.

### Roadmap Evolution

- Phase 11 added (2026-08-14): Parâmetros — fluxo de solicitação, aplicação na aprovação e auditoria por papel. Investigação com Victória mostrou que o ciclo painel→aprovação→motor está cortado em três pontos (payload da solicitação dá 422, aprovação não aplica o valor, autorização não registra papel) e que o único caminho de escrita funcional (`PUT`) não tem auditoria. Decisões de negócio congeladas na entrada da fase no ROADMAP: todos veem, gestor+ solicita, admin+ aprova, admin também passa pelo formulário, autoaprovação permitida.
- Phase 8 recontextualizada (2026-08-14): hardcodes de parâmetro no front confirmados (`1.05` em order-detail-modal e orders-list) — o item que o audit deixou como "não verificado" está confirmado. Fase passa a depender da Phase 11 e carrega a restrição dura de **não alterar o design system** (vendor `file:./design-system`). **Atualização 2026-08-31 (quick task 260831-cf1):** o `1.05` de `orders-list.tsx` foi corrigido por decisão da mantenedora, sem esperar a Phase 11 fechar (só depende de leitura, `GET /api/v1/parametros`, não do fluxo de escrita que a Phase 11 endereça). `order-detail-modal.tsx` não existe mais na árvore principal do front.

- Phase 20 added (2026-09-04): Trava de orçamento de ±5% na edição manual de pedidos "Sem Adequação" — hoje `OrcamentoPedido` só é consultado pelo motor automático (`processar_pedidos`); a edição manual de grade (`executar_alteracao_grades_produto`) nunca o consulta, e o modal (`product-grade-detail-modal.tsx`) bloqueia qualquer mudança de total (`hasInvalidClientTotals`). Decisão de negócio: pedidos "Com Adequação" não mudam (continuam redistribuição só); pedidos "Sem Adequação" (`ModoAdequacao.SEM_ADEQUAR`, que não toca o ledger) passam a poder ter o total editado manualmente até 5% do pedido inteiro, com salvar+aprovar unificados num clique quando dentro do limite. Cross-repo (endpoint no backend, tela no `frontend`). Plano revisado em 3 passadas com a mantenedora antes de virar fase.

- Phase 21 added (2026-09-09): Avisos de integração crítica (banco de dados e view de estoque Databricks) exibidos na aba "Alertas" já existente (`alertas_router`, `app/modules/pedidos/infrastructure/http/routes.py`), sob categoria "Problemas de integração", com mensagem redigida para leitor não-técnico. Fora do escopo de negócio do milestone v1.3 — observabilidade/infra. Fora do fluxo discuss→plan→execute: executada via `/gsd-quick` a pedido da mantenedora. Cross-repo (backend gera o aviso e publica via realtime; consumo no `frontend` fica para quick-task separada, mesmo padrão da Phase 20).

### Pending Todos

Nenhum registrado em `.planning/todos/pending/`.

Próximos passos de processo (não execução de app):

1. `/gsd-plan-phase 13` e/ou `/gsd-plan-phase 14` — paralelizáveis, primeira onda do v1.3
2. Revisar `.planning/CLAUDE-EXECUTION-BRIEF.md` + `AUDIT-2026-08-07.md` quando o v1.2 for retomado
3. Não misturar GRADE-* / INTG-* neste milestone

### Quick Tasks Completed

| # | Description | Date | Commit | Status | Directory |
| --- | --- | --- | --- | --- | --- |
| 260831-cf1 | Fecha CONTRACT-01 (repo `frontend`): `orders-list.tsx` consome `tolerancia_adequacao` real via `GET /api/v1/parametros` (hook `useToleranciaAdequacaoFator`) em vez do fator `1.05` hardcoded; `order-detail-modal.tsx` do achado original não existe mais na árvore principal | 2026-08-31 | `85d1d25` (front) | | [260831-cf1-frontend-consumir-tolerancia-adequacao-real](./quick/260831-cf1-frontend-consumir-tolerancia-adequacao-real/) |
| 260831-fai | Fecha o achado `forwarded-allow-ips=*` no `.docker/Dockerfile`: troca pelos CIDRs reais das subnets privadas da VPC do ECS (30.1.1.0/24, 30.1.11.0/24, 30.1.21.0/24), configurável via `UVICORN_FORWARDED_ALLOW_IPS` | 2026-08-31 | `fa20ac4` | | [260831-fai-restringir-forwarded-allow-ips-do-uvicorn](./quick/260831-fai-restringir-forwarded-allow-ips-do-uvicorn/) |
| 260831-sen | Fecha OBS-01 (integração Sentry): sentry-sdk[fastapi], Settings.sentry_dsn/sentry_traces_sample_rate, setup_sentry() antes do FastAPI(); DSN vazio é no-op do SDK, sem fail-fast de PROD (audit não pediu) | 2026-08-31 | `abb46ba` | | [260831-sen-integrar-sentry-obs-01](./quick/260831-sen-integrar-sentry-obs-01/) |
| 260831-kic | Fecha OBS-03 (fail-fast de CORS_ORIGINS só-localhost em PROD) + achados correlatos do audit ainda abertos: CORS allow_methods/allow_headers deixam de ser `*` e viram listas explícitas, `/docs`+`/redoc`+`/openapi.json` fecham quando `ENV=="PROD"` via `_docs_urls()`; aproveitado para marcar OBS-02 (já fechado desde 260827-emo) no REQUIREMENTS.md, que nunca tinha sido atualizado | 2026-08-31 | `59cddc2` | | [260831-kic-fechar-hardening-de-cors-docs-obs-03-doc](./quick/260831-kic-fechar-hardening-de-cors-docs-obs-03-doc/) |
| 260831-jhj | Elimina a dívida da 260831-ieq: mensagem de stand-by por orçamento estourado passa a refletir a tolerância real configurada (deixa de dizer "5%" fixo), docs/adequacao.md idem | 2026-08-31 | `acd68ff`, `546cc20`, `6f0d2ea` | | [260831-jhj-eliminar-a-divida-deixada-de-proposito-n](./quick/260831-jhj-eliminar-a-divida-deixada-de-proposito-n/) |
| 260831-ieq | Conecta `tolerancia_adequacao` ao teto de ±5% do orçamento do pedido no motor de adequação (corrige regressão da Fase 14 onde o parâmetro parou de ter efeito) | 2026-08-31 | `6ce3ab4`, `ec50e5f` | | [260831-ieq-conectar-tolerancia-adequacao-parametro-](./quick/260831-ieq-conectar-tolerancia-adequacao-parametro-/) |
| 260818-gaz | Paginação numerada nas listas de pedidos (troca o "carregar mais"; adiciona `?page=` em `GET /pedidos/produtos`) | 2026-08-18 | `ea00db2` (back) · `a5b2a01` (front) | | [260818-gaz-paginacao-numerada-nas-listas-de-pedidos](./quick/260818-gaz-paginacao-numerada-nas-listas-de-pedidos/) |
| 260825-eii | Reordenar quadros da tela de gestão de usuários (pendentes → histórico → usuários e permissões) | 2026-08-25 | `80112a7` (front) | | [260825-eii-reordenar-quadros-na-tela-de-gestao-de-u](./quick/260825-eii-reordenar-quadros-na-tela-de-gestao-de-u/) |
| 260825-jhv | Exceção de blacklist no motor de adequação (migration 033 + propagação Databricks→motor + despacho tudo-ou-nada forçado + testes) | 2026-08-25 | `a2284cf`, `6396f5b`, `56c00ec`, `b7f9932`, `c2e64a1` | Verified | [260825-jhv-implementar-exce-o-de-blacklist-no-motor](./quick/260825-jhv-implementar-exce-o-de-blacklist-no-motor/) |
| 260826-fer | Consolidar cleanup de dead code no backend e expandir ruleset de ruff no pipeline de qualidade (2 símbolos mortos removidos; `[tool.ruff]` formalizado no pyproject.toml com B/UP/I/SIM/C4 além de E4/E7/E9/F; `alembic/script.py.mako` corrigido na raiz — 76/110 achados vinham do template; CI lê a seleção do pyproject em vez de `--select` na CLI) | 2026-08-26 | `cfbfb21`, `f127429`, `cb48480`, `50b4287` | | [260826-fer-consolidar-cleanup-de-dead-code-no-backe](./quick/260826-fer-consolidar-cleanup-de-dead-code-no-backe/) |
| 260825-f21 | Excluir acesso de usuário (`DELETE /api/auth/users/{id}`, restrito a admin) + migration 034 (FKs de `parametro_change_requests` viram `ON DELETE SET NULL`) | 2026-08-25 | `af79aa5`, `0c32809`, `42cf4b6` | | pendente de commit dos docs GSD desta tarefa |
| 260826-iln | Remover 3 endpoints legados de pedidos que só respondiam `410 Gone` (`POST /adequar`, `POST /sem_adequar`, `PUT /{nr_pedido}/alterar-grade`) + registrar inventário completo das 43 rotas do backend cruzadas com o consumo do frontend em `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` (Grupos B/C pendentes de confirmação com a mantenedora; Grupo D — parametros PUT/DELETE — proibido mexer até a Fase 11 fechar) | 2026-08-26 | `02ee402`, `bce5252` | | [260826-iln-remover-endpoints-legados-de-pedidos-em-](./quick/260826-iln-remover-endpoints-legados-de-pedidos-em-/) |
| 260826-kla | Fatiar 3 God Files de SQL de leitura do módulo pedidos (`repositorio_produtos.py` 1063 linhas, `repositorio_resumo.py` 909 linhas, `processing/infrastructure/repository.py` 583 linhas) em 18 módulos novos sem shim de compatibilidade — gate de fidelidade de literal SQL (AST-based) provou byte a byte que nenhum SQL mudou; suíte completa fechou em 886 passed/18 skipped/0 failed, igual ao baseline; `SqlPedidosReadRepository` manteve os 6 métodos públicos e assinaturas | 2026-08-26 | `afeb419`, `a9f349b`, `960be68`, `e5e4c92`, `d77768a` | | [260826-kla-fatiar-god-files-de-leitura-sql-do-modul](./quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/) |
| 260827-emo | Fechar `GET /metrics` (Prometheus) atrás de guard por API key de header (`X-Metrics-Key`, `require_metrics_key` em `app/shared/metrics/security.py`, comparação com `hmac.compare_digest`); `METRICS_API_KEY` fail-closed (503 sem chave configurada) e fail-fast no boot em PROD (mín. 32 chars, mesma régua do `JWT_SECRET`); `/health` e `/health/ready` intocados por decisão de escopo | 2026-08-27 | `5fb97d5`, `bc9818b`, `9a29d0b`, `2f0d034` | | [260827-emo-proteger-endpoint-get-metrics-com-guard-](./quick/260827-emo-proteger-endpoint-get-metrics-com-guard-/) |
| 260827-efy | Correções da revisão de segurança do backend: `RegisterRequest.user_name`/`.phone` bounded (255/20, espelhando as colunas); `ParametroCreate/Update.valor/descricao` (4000) e `ChangeRequestCreate.justification` (2000) bounded, `proposed_payload` limitado a 16 KiB serializado; `unhandled_exception_handler` agora loga `logger.exception` com traceback antes do 500; `MaxBodySizeMiddleware` novo (413 por `Content-Length`, antes do `CORSMiddleware`, passthrough de WebSocket) com `REQUEST_MAX_BODY_BYTES` (default 2 MiB); log do código OTP em claro restrito a `ENV=="DEV"` (PROD e ambientes intermediários entregam por e-mail e nunca logam o código) | 2026-08-27 | `6fb5015`, `fe82f27`, `7ae6c66`, `600f7e2`, `c583d35`, `b2ac258`, `b2c8df8` | | [260827-efy-aplicar-correcoes-da-revisao-de-seguranc](./quick/260827-efy-aplicar-correcoes-da-revisao-de-seguranc/) |
| 260827-ewg | Detecção de reuso de refresh token (rotation with reuse detection): jti vigente por usuário guardado no Redis (`auth:refresh_jti:{user_id}`, TTL = 7 dias); reapresentar um refresh token já rotacionado é lido como possível roubo e revoga a sessão inteira reaproveitando o mesmo corte que `sign_out` grava (`auth:signed_out_since:{user_id}`); fail-open se o Redis cair, igual à checagem de sign-out já existente. Chave por usuário (não por sessão) é decisão deliberada: duas sessões do mesmo usuário renovando em paralelo caem juntas — aceito pela mantenedora. Suíte fechou em 924 passed/18 skipped/2 failed (mesmas 2 falhas pré-existentes, +7 testes novos) | 2026-08-27 | `03eea89`, `78443bd` | | [260827-ewg-implementar-deteccao-de-reuso-de-refresh](./quick/260827-ewg-implementar-deteccao-de-reuso-de-refresh/) |
| 260827-f8o | Sanitizar `GET /health/ready`: `_run_check` novo em `app/modules/health/routes.py` retorna só `"ok"`/`"fail"`/`"unavailable"` no corpo da resposta para cada check (Postgres, Alembic head, Redis) — nome da classe de exceção (`ConnectionError`, `DatabaseSchemaNotReadyError`, etc.) some do JSON público e passa a ir só para `logger.error` no servidor; contrato de código HTTP (200/503) e `GET /health` (liveness) inalterados | 2026-08-27 | `632b995`, `f6ca053` | | [260827-f8o-sanitizar-get-health-ready-para-nao-vaza](./quick/260827-f8o-sanitizar-get-health-ready-para-nao-vaza/) |
| 260909-ek7 | Fase 21 (backend): avisos de integração crítica na aba Alertas existente (`alertas_router`) — bounded context `health` promovido a DDD (domain/application/infrastructure/service.py) para avisos de `banco_de_dados` (Postgres, via `/health/ready` e handler global de `OperationalError`/`InterfaceError`/`TimeoutError`) e `estoque` (view Databricks, via `DatabricksIngestionSource.estoque()`); estado deduplicado em Redis (D-05), copy 100% não-técnica com gate automatizado (D-04), `GET /api/v1/alertas` estendido com `kind="integracao"`; fonte `estoque` também publica live-update instantâneo no tópico realtime `alerts` numa sessão própria fora da transação da ingestão (D-07, exigência adicional da mantenedora — banco de dados não tem live-update por decisão deliberada, PD-05: publicar "o Postgres está instável" pelo próprio Postgres não se sustenta). Zero migration, zero dependência nova. Suíte fechou em 1022 passed/18 skipped (após merge com 5 commits concorrentes de outra frente — remoção de endpoint de listagem de OR + perfil de usuário via SSO — e migração manual de `system_automation_test` 035→036). Consumo no frontend (`kind`/`category`/`title`/`message`/`affectedCount`, mesmo tópico `alerts`) fica para quick-task separada no `frontend`. **Follow-up (commit `1d84968`, mesmo dia):** verificação ao vivo contra o Databricks real (rejeitando a sessão inteira desde 2026-09-03, `error_code=databricks_http_rejected` em `durable_jobs`) mostrou que o aviso nunca abria — `pedidos_em_aberto()` é a primeira leitura de `_coletar_snapshot` e falhava antes de `estoque()` ser chamado. Corrigido: `pedidos_em_aberto()` agora reporta na mesma fonte `estoque` (mesmo Databricks, mesmo texto de aviso). Sem isso a Fase 21 não detectava o cenário real que motivou o pedido. | 2026-09-09 | `774e55c`, `8de6148`, `dc133aa`, `4464edd`, `1d84968` | | [260909-ek7-avisos-de-integracao-critica-banco-de-da](./quick/260909-ek7-avisos-de-integracao-critica-banco-de-da/) |
| 260827-fqf | Remoção completa do login por e-mail+senha do backend (decisão da mantenedora, reverte a política de 25/08 de manter os dois lado a lado): apagados cadastro, confirmação, recuperação de senha, OTP e seed de 5 contas de teste; `POST /api/auth/sso/microsoft` (Microsoft Entra ID) vira o único login, agora com cobertura de teste que não existia; migration 035 dropa `password_hash`/`user_name`/`cpf`/`phone` de `auth_users` e a tabela `auth_otp_challenges` inteira (com backfill de `confirmed_at`); SEC-08 (lockout de conta, recém-commitado por outra sessão) removido junto por não sobrar segredo adivinhável; alerta "acesso pendente" removido por ficar morto por construção; `docs/seguranca.md` reescrito como runbook SSO-only. Colidiu com uma segunda execução concorrente do mesmo plano no mesmo working tree (sem worktree) — resolvido sem retrabalho, migration/testes da outra sessão eram byte-idênticos, ver SUMMARY. Suíte fechou em 881 passed/18 skipped/0 failed (baseline real 934/18/0, queda esperada pela remoção das suítes de senha/OTP). Frontend removido em paralelo, fora deste repo. | 2026-08-28 | `a8cde52`, `dba6112`, `de43349`, `5b57d44`, `66435e9`, `ecb9604`, `c88c03e`, `01ba144` | | [260827-fqf-remover-por-completo-o-login-por-e-mail-](./quick/260827-fqf-remover-por-completo-o-login-por-e-mail-/) |

### Blockers/Concerns

### ~~Banco de teste duas migrations atrás~~ — RESOLVIDO 2026-08-17

**Sintoma:** 26 falhas + 43 erros na suíte local; `test_schema_guard.py::test_colunas_do_model_existem_no_banco[auth_users]`
acusava `colunas no model e AUSENTES no banco: ['cpf', 'phone', 'user_name']`.

**Causa real:** existem **dois** bancos e eles estavam em versões diferentes. Os testes usam
`system_automation_test` (o `conftest.py` recusa banco cujo nome não termine em `_test`), e ninguém
tinha rodado `alembic upgrade head` nele — estava em **028**, enquanto o banco da aplicação
(`system_automation`) já estava em **030**. Ninguém corrompeu nada; o banco de teste só ficou atrás.

**Diagnóstico errado que foi descartado no caminho:** a hipótese inicial era corrupção causada
pela renumeração 029→030 da migration do hash de OTP — que `alembic_version` apontasse 030 com
as colunas da 029 faltando. Falso: `alembic current` estava lendo o banco da **aplicação** (via
`.env`) enquanto o teste que falhava lia o de **teste**. Bancos diferentes, comparação inválida.
Consequência prática: a correção proposta (`stamp 028 && upgrade head`) rodaria contra o banco da
aplicação e falharia com `DuplicateColumnError`, deixando `alembic_version` em 028 com schema em
030 — teria *criado* a corrupção que se acreditava consertar. Sempre confirmar **qual** banco a
conexão resolveu antes de concluir drift de schema.

**Correção aplicada:** `DATABASE_URL=...system_automation_test ./.venv/Scripts/python.exe -m alembic upgrade head`
→ rodou 028→029→030 só no banco de teste. As 167 linhas de `auth_users` foram preservadas
(029 é aditiva; `cpf` entra NULL e a unique aceita múltiplos NULL). Banco da aplicação intocado.
Depois: `test_schema_guard.py` + `test_parametros.py` + `test_parametros_registro.py` → 102 passed.

**Lição operacional:** o `conftest.py` não roda `create_all` nem alembic. Depois de qualquer
migration nova, é preciso migrar `system_automation_test` à mão — o plano 11-02 (migration 031) vai
precisar disso de novo.

**Reverificado 2026-08-12 direto no código** (não só contra os outros docs de planning — vários itens abaixo já foram fechados por commits regulares entre o audit e agora, fora do fluxo formal de fases):

- ~~Pipeline sem ruff/pyright/pytest~~ — **resolvido na prática**: `pipeline.yml` já roda `ruff check` + `pytest` antes do build/push, sem `continue-on-error`. Falta só `pyright`/`ruff format --check`.
- ~~`JWT_SECRET` default fraco sem fail-fast~~ — **resolvido**: boot PROD falha se fraco/placeholder (`settings.py:588-592`).
- `SEED_AUTH_ON_STARTUP=True` — **parcial**: boot PROD já falha se `True` (`settings.py:594-595`), mas o default global fora de PROD continua `True`. Risco reduzido, não eliminado.
- OTP em `ENV=PROD` não é entregue por e-mail (só log sem código) — **ainda aberto**, confirmado em `repositorio_otp.py:45-50`.
- ~~Celery worker/beat e migrations **não** estão no pipeline ECS~~ — **resolvido, desenho diferente do planejado**: pipeline já atualiza worker+orders-worker+beat; migrations rodam no boot de cada task de API (não em task one-off).
- ~~Docs listam `alertas/`/`history/` inexistentes~~ — **já não procede**: `docs/arquitetura.md`/`docs/api.md` atuais não têm esse problema. ~~Front hardcoda parâmetros vs `GET /api/v1/parametros`~~ — **resolvido** (260831-cf1): `orders-list.tsx` consome o valor real via `useToleranciaAdequacaoFator()`.
- `.dockerignore` ausente — **ainda aberto**. `/metrics` público — **resolvido** (260827-emo). Sentry — **resolvido** (260831-sen; falta só provisionar `SENTRY_DSN` real em PROD, item de infra fora deste repo). CORS sem fail-fast em PROD — **resolvido** (260831-kic, junto com `/docs` sempre exposto e `allow_methods`/`allow_headers` wildcard). `forwarded-allow-ips=*` no `.docker/Dockerfile` — **resolvido** (260831-fai): CIDRs reais da VPC do ECS.

Ver: `.planning/research/AUDIT-2026-08-07.md` § Status update 2026-08-12, `.planning/REQUIREMENTS.md` § v1.2 e `.planning/milestones/v1.2-PLATFORM-HARDENING.md` § Status update para o detalhe requirement-por-requirement.

**v1.3 (novo, 2026-08-14):**

- Phase 17 (UI standby) tem constraint dura: nenhuma alteração de front-end sem antes consultar `frontend/design-system/`. Se o componente necessário não existir lá, é decisão a levantar com a mantenedora, não a improvisar.
- ~~Phase 15 depende de medir o pico real de memória do modo tudo-ou-nada depois do roteamento global~~ — **medido em 15-04** (2026-08-20): `delta_total_mib=630.9 MiB` sobre 45.000 pares/112.500 itens sintéticos, dentro do teto de calibração de 700.0 MiB (margem real ~9.9%, mais apertada que a extrapolação de ~600 MiB do modo ADEQUAR — consistente com a Assumption A2 de que `sem_adequar` tem volume elegível estruturalmente maior por não filtrar crédito). Ver `15-04-SUMMARY.md`.
- Starvation por prioridade de valor (Pitfall 2) é aceita como consequência da regra 4 — não corrigir sozinho; só instrumentar (STANDBY-06, Phase 16).

**Handoff para a Phase 17 (UI de stand-by) — quick task 260825-jhv (2026-08-25):** o motivo canônico de stand-by `"blacklist"` já existe de ponta a ponta a partir desta quick task — na CHECK constraint do banco (`pedido_standby_motivo.ck_psm_motivo`, migration 033, agora com 4 valores: `sem_credito`, `sem_estoque`, `furo_grade`, `blacklist`) e no dict retornado pelo motor (`processar_pedidos()["preteridos_motivo"]`, via `app.modules.pedidos.domain.standby_motivo.BLACKLIST`). Contexto de negócio: cliente com `indica_blacklist="SIM"` (nova coluna vinda da view Databricks `system_automation_pedidos_em_aberto`) nunca pode ter a grade alterada pelo motor, mesmo em `modo=ADEQUAR` — o pedido é forçado para o caminho tudo-ou-nada por produto, e o motivo `blacklist` é gravado (nunca `furo_grade`/`sem_estoque`) quando falta estoque de algum tamanho pedido. **Quando a Phase 17 for construída, ela precisa tratar este 4º motivo visualmente** (tag/rótulo/filtro na aba Aguardando, mesmo padrão já previsto para `sem_credito`/`sem_estoque`/`furo_grade`) — isso é trabalho de **FRONTEND**, fora de escopo da quick task 260825-jhv: nenhum arquivo de `frontend` foi tocado. Ver `.planning/quick/260825-jhv-implementar-exce-o-de-blacklist-no-motor/260825-jhv-SUMMARY.md` para o detalhe completo da implementação de backend.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| Integração Linx | INTG-01/02/03 | Aguardando barramento TI | v1.1 (2026-08-04) |
| Frontend OR | UI-01 (nº pedido no modal) | Aguarda fluxo ERP | v1.1 (2026-08-04) |
| Edição de grade | GRADE-03 (parcial) + UI-02 | GRADE-04 já implementado (2026-08-12, fora do fluxo formal); GRADE-01/02 não se aplicam ao desenho atual (edição só existe pós-geração); GRADE-03 parcial (checagem de estoque existe, tolerância ±5% explícita não encontrada); UI-02 não verificado | Discuss Fase 3 / reverificado 2026-08-12 — ver REQUIREMENTS.md § Edição manual de grade |
| Frontend Phase 17 | Motivo `blacklist` (4º motivo canônico de stand-by) precisa de tag/rótulo/filtro na UI | Backend completo (260825-jhv); UI ainda não construída | Quick task 260825-jhv (2026-08-25) |

## Session Continuity

**Resume file:** None

Last session: 2026-09-08T14:37:40.772Z
Stopped at: Plano 20-03 executado por completo — trava de orçamento ±5% ligada ao caminho de escrita (executar_alteracao_grades_produto), 409 estruturado, 8 testes novos (957 passed, 18 skipped). Próximo: 20-04.
Resume: `.planning/ROADMAP.md` § Phase 17 (ou § Phase 13/18 se preferir paralelizar antes)

## Session note 2026-08-07

Audit-only: Milestone v1.2 planning + CaCodes automationBE-1..8 for Victória. See CACODES-ISSUES.md. No app code changes.

## Session note 2026-08-10

Milestone v1.3 (migração Aurora) cancelada após preflight revelar que o Aurora já está configurado com schema Alembic 028 e dados ativos (~1.4M linhas). Migração desnecessária.

## Session note 2026-08-14 (roadmap v1.3)

Roadmap do milestone v1.3 criado: 7 fases (Phases 13–19), cobertura 21/21 requirements, 0 órfãos. Ordem segue a dependência real apurada na pesquisa (guarda contra OR zerada isolada + motor puro em paralelo → roteamento global do modo sem adequação → persistência do motivo de stand by → visibilidade na UI, paralelizável com peças extras → validação final/shadow run como gate de saída). Phase 17 (UI) carrega a constraint obrigatória de consulta ao design-system antes de qualquer alteração de front-end.

## Session note 2026-08-14 (execução 14-01)

Plano 14-01 (Phase 14) executado: `ratear_hamilton` extraída de `_allocate` (antes privada em `edicao_grade.py`) para `app/modules/pedidos/domain/rateio.py`, módulo folha com `__all__` explícito. `edicao_grade.py` religado via `from app.modules.pedidos.domain.rateio import ratear_hamilton as _allocate`, sem alterar call-sites. Teste dedicado `test_pedidos_rateio.py` cobre soma exata, sinal negativo, quantidade zero e caso sem remainder, isolado de `edicao_grade.py`. FIX-02 metade 1/2 fechada (a ligação no motor de adequação fica para `14-06`). Suíte completa via Docker: 762 passed (758 baseline + 4 novos), 16 skipped, 1 failed (falha conhecida e pré-existente, sem relação). Commits: `2faae22` (feat), `acb6bee` (refactor), `286c4f9` (test).

## Session note 2026-08-17 (execução 14-02, retomada)

Plano 14-02 (Phase 14) executado — retomando uma execução anterior interrompida entre a Task 2 (trabalho pronto, não commitado) e a Task 3 (não iniciada). Task 1 (`factories_pedidos_motor.py`) já estava concluída e commitada (`bd931af`). Task 2 (`cenarios_motor_adequacao.py` + `test_cenario_disputa_mantenedora.py`) foi revisada linha a linha contra o `14-02-PLAN.md`, confirmada correta sem lacunas, e commitada (`ce4a3d5`). Task 3 (`invariantes_motor_adequacao.py` + `test_invariantes_motor_adequacao.py`) foi implementada do zero: 10 helpers de asserção cobrindo I1-I9 + oráculo `calcular_furo_de_grade`, com 26 testes de self-teste (caso que passa + caso que falha para cada invariante, e os 4 casos de borda do 14-CONTEXT.md para o oráculo de furo de grade), commitados em `78a2f92`. Suíte completa via Docker: 792 passed, 16 skipped, 1 failed (a mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`, sem relação com esta fase). Nenhum arquivo de produção tocado. Próximo passo: `14-03`.

## Session note 2026-08-17 (execução 14-03)

Plano 14-03 (Phase 14) executado: **primeiro plano da fase a alterar código de produção do motor**. `_prioridade` (função aninhada em `_processar_pedidos_canal`, `motor_adequacao.py`) passou de `(score, len(prods_disp))` para `(score, len(prods_disp), -nr)` — desempate final e total por `nr_pedido` crescente sob `sorted(..., reverse=True)`; a negativação de `nr` é o que evita inverter a decisão travada (sem ela, o MAIOR `nr_pedido` venceria). Dois blocos de comentário atualizados. RED comprovado antes da edição (o vencedor da disputa mudava conforme a ordem de entrada de `dados`); GREEN depois. Teste novo em `test_pedidos_motor.py` prova a invariante I6 (reprodutibilidade completa do plano de seleção, ordens de entrada opostas). Commit único `255af40` (Task 1); Task 2 (suíte completa) não exigiu nenhum código — nenhuma regressão nos dois arquivos que fixam a assinatura de `adequar_grade_produto` via `monkeypatch`. Suíte completa via Docker: 793 passed (792 base + 1 novo), 16 skipped, 1 failed (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`). ALOC-06 concluído. Próximo passo: `14-04`.

## Session note 2026-08-17 (execução 14-04)

Plano 14-04 (Phase 14) executado: furo de grade (ALOC-04) como predicado puro `tem_furo_de_grade`, em módulo de domínio novo e folha `app/modules/pedidos/domain/furo_de_grade.py` (sem import de `motor_adequacao.py`, evitando dependência circular). Considera só tamanhos com `qt_liquida > 0`, descarta `get_tamanho_idx == 999` ANTES de calcular extremos, e reporta o motivo do primeiro tamanho interior zerado em ordem crescente de idx. Task 1 (RED/GREEN): 8 testes unitários cobrindo os 5 casos de borda travados no CONTEXT.md (tamanho único, dois adjacentes, tamanho não pedido, tamanho desconhecido nunca interior nem extremo — provado com o MESMO tamanho desconhecido em posições estruturalmente diferentes — e `qt_liquida <= 0`), mais determinismo do motivo relatado. Commit `4fd0920`. Task 2: guarda ligada dentro de `_processar_pedidos_canal`, imediatamente após a guarda de crédito e antes de `adequar_grade_produto` — par com furo entra em `preteridos` + `marcar_stand_by`, sem decrementar `estoque_local` nem chamar `adequar_grade_produto` (nenhuma chave nova no contrato de retorno). Teste de sensibilidade à ordem (`test_processar_pedidos_furo_sensivel_a_ordem_prova_checagem_dentro_do_laco`) prova que a checagem lê o estoque já decrementado pelo pedido prioritário dentro do mesmo laço — não uma foto anterior a ele. Assinatura de `adequar_grade_produto` inalterada; os dois arquivos que fazem `monkeypatch` dela (`test_pedidos_motor_performance_024.py`, `test_pedidos_processing_cancellation_024.py`) permanecem verdes. Commit `fba2939`. Suíte completa via Docker: 803 passed (793 base + 10 novos), 16 skipped, 1 failed (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`, sem relação). ALOC-04 concluído. Próximo passo: `14-05`.

## Session note 2026-08-17 (execução 14-05)

Plano 14-05 (Phase 14) executado: política de quantidade por modo (ALOC-01/02/03). `ModoAdequacao(StrEnum)` criado como enum PRÓPRIO do domínio (`politica_quantidade.py`), espelhando os valores `adequar`/`sem_adequar` de `ProcessingMode` (de `processing/`) em vez de importá-lo — direção de dependência DDD (`processing/` depende de `domain/`, nunca o inverso); a paridade entre os dois enums é garantida por teste dedicado, não por import compartilhado. `aplicar_tudo_ou_nada` (nova função) implementa o modo SEM_ADEQUAR: tudo-ou-nada por par, grade exatamente como pedida em todos os tamanhos ou o par inteiro em stand-by, nunca quantidade parcial — sem ledger de orçamento (ALOC-02/03 é binário, sem tolerância). Despacho por modo ligado em `processar_pedidos`/`_processar_pedidos_canal` (`if/else` explícito, não Protocol — as duas políticas têm assinaturas divergentes e o idioma do pacote é função simples). Cenário da mantenedora reexecutado em SEM_ADEQUAR, confirmando A atendido / B em stand-by com quantidade preservada (não reduzida). Commits: `350dbbe`, `946dae2`, `e3f8681`, `f5ae688`. Suíte completa via Docker: 811 passed, 16 skipped, 2 failed (a falha conhecida de `test_pedidos_read_projection.py` + `test_parametros.py::test_listar_parametros_contem_criado`, diagnosticada como acúmulo de dados no banco de dev — 220 artefatos de execuções anteriores da suíte empurrando a chave criada para além da página 1 — sem relação com o motor de adequação; não reapareceu na execução do 14-06). **Nota de execução:** o agente executor caiu com erro de API depois de commitar as 4 tasks, no momento exato em que ia escrever o SUMMARY; o SUMMARY foi escrito pelo orquestrador no fechamento, a partir dos commits, e o STATE.md não havia sido atualizado até a execução do 14-06 (catch-up feito aqui). ALOC-01/02/03 concluídos. Próximo passo: `14-06`.

## Session note 2026-08-17 (execução 14-06)

Plano 14-06 (Phase 14) executado: núcleo da fase — ledger de orçamento ±5% do PEDIDO COMPLETO, alocação em duas passadas e rateio Hamilton ligado ao recálculo financeiro (ALOC-07/08/09/10, FIX-02). Task 1 (RED/GREEN): `OrcamentoPedido` (`app/modules/pedidos/domain/orcamento_pedido.py`, novo), `@dataclass(slots=True)` mutável (não `frozen`), 4 campos obrigatórios sem default (`nr_pedido`, `total_original`, `consumido_previo_adicao`, `consumido_previo_corte` — a ausência de default é a proteção documentada contra double-spend silencioso), `limite_adicao`/`limite_corte` via `(total * 5) // 100` (floor inteiro, nunca `ceil`/`float`), `consumir_corte` tudo-ou-nada e `consumir_adicao` parcial saturando em 0. 10 testes cobrindo floor, os dois orçamentos não se compensando, e o acumulado entre 3 construções sucessivas nunca resetando nem excedendo o limite. Commits `fb023d1` (RED) e `f6cabad` (GREEN). Task 2: laço de `_processar_pedidos_canal` invertido para pedido-externo/produto-interno (a partição de estoque por produto já era independente, então a inversão não muda a alocação — só torna o ledger local à iteração do pedido); `adequar_grade_produto` reescrita para receber `ledger: OrcamentoPedido` em vez de `tolerancia: float`; nova `conceder_adicao_pedido` para a passada 2 (extras, só entre produtos com folga total, ponto de extensão ALOC-11/Phase 18 comentado explicitamente); `_recalcular_financeiro_produto` liga `ratear_hamilton` no lugar do `round()` item a item (FIX-02). `processar_pedidos`/`_processar_pedidos_canal` ganham 3 parâmetros opcionais keyword-only para o total original/consumido prévio por pedido (ALOC-09); ausentes, caem no placeholder documentado com `logger.warning` citando ALOC-09 uma vez por execução de canal — provado por teste com `caplog` (sai sem os parâmetros, não sai com eles). Commit `b90695e`. Suíte completa via Docker: 820 passed, 16 skipped, 6 failed — exatamente as antecipadas por `14-VALIDATION.md`/`14-RESEARCH.md` (3 testes de regra antiga sobre tolerância `ceil` por produto isolado — regra que deixou de existir —, 2 testes de assinatura fixa via `monkeypatch` de `adequar_grade_produto`, e a falha conhecida de `test_pedidos_read_projection.py`); `test_cenario_disputa_mantenedora.py` foi atualizado (não é uma das 6) porque a regra nova de ALOC-07 (base = pedido completo, não produto isolado) muda corretamente o resultado esperado do cenário — documentado como decisão, não regressão. ALOC-07/08/09/10 e FIX-02 concluídos (FIX-02 fecha a segunda metade aberta pelo 14-01). Próximo passo: `14-07` (conserto consciente dos 5 testes de quebra esperada).

## Session note 2026-08-17 (execução 14-07 — Phase 14 fechada)

Plano 14-07 (Phase 14, último plano) executado: migração consciente da suíte de testes para a semântica pós-14-06. Task 1: removidos os 2 testes restantes de tolerância `ceil`/`floor` por PRODUTO ISOLADO (`test_adequar_grade_produto_aumenta_extremos_dentro_do_orcamento`, `test_adequar_grade_produto_falta_dentro_da_tolerancia_reduz_parcial`) de `test_pedidos_motor.py` — regra que deixou de existir com ALOC-07/08; renomeado o teste de corte-atravessa-produtos (herdado do 14-06) para `test_processar_pedidos_orcamento_pedido_corte_atravessa_produtos_respeita_teto`; adicionados 2 testes novos via `processar_pedidos` provando "não se compensam" (ALOC-08) e "acumulado entre execuções" (ALOC-09/double-spend) a nível de integração, reforçando a cobertura que já existia a nível de ledger isolado desde o 14-06; ajustada a asserção pontual de `test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito` (motivo_stand_by sempre presente sob a regra nova). Commit `34dffa0`. Task 2: `test_pedidos_motor_performance_024.py` sincronizado — `_adequar_observando_subset` recebia `tolerancia: float` como 4º parâmetro posicional, atualizado para `ledger: OrcamentoPedido`; `test_pedidos_processing_cancellation_024.py` confirmado verde sem necessidade de edição (seu substituto já repassava o argumento posicionalmente ao `original_grade` capturado antes do monkeypatch). Commit `b5a0ed7`. Task 3 (verificação/fechamento): confirmados os 11 cenários nomeados de `14-VALIDATION.md` cobertos por leitura; `git status --short -- app/modules app/shared alembic` mostra só o trabalho pré-existente de SSO Microsoft (não tocado por este plano); `hypothesis` ausente de `pyproject.toml`/`app/tests/`; helper de invariantes I1/I5 reusado em pelo menos 2 testes de negócio novos. Suíte completa via Docker: **825 passed, 16 skipped, 1 failed** — a única falha é a pré-existente e conhecida de `test_pedidos_read_projection.py` (Event loop is closed no teardown, sem relação com o motor), exatamente o critério de saída da fase, sem `--deselect` nem `-k`. **Phase 14 fechada (7/7 planos)**: ALOC-01 a ALOC-04, ALOC-06 a ALOC-10 e FIX-02 provados por teste automatizado. Próximo passo: `/gsd-plan-phase 15` (roteamento global do modo sem adequação).

## Session note 2026-08-17 (execução 15-02 — FIX-01)

Plano 15-02 (Phase 15) executado: TDD estrito para fechar o gap de revalidação de estoque no modo `sem_adequar`. RED: `test_sem_adequar_revalidates_stock_before_writes` adicionado em `test_pedidos_processing_repository.py` (espelho de `test_adequation_revalidates_stock_before_writes`, trocando só `tipo="com"` por `tipo="sem"`); confirmado falhando contra o código antigo com `DID NOT RAISE ProcessingPlanConflict` (estoque `qty=1` disponível vs. `qty=2` pedidos, escrita passava sem checagem). Commit `58e38e3` (test). GREEN: em `repository.py::_assert_stock_capacity`, removidas as duas linhas `if tipo != "com": continue`; variável de desestruturação renomeada `tipo` → `_tipo` (não lida). Nenhuma outra linha da função mudou. Commit `52fd2c7` (fix). Nenhum substituto necessário para o `if` removido — `_payload` já rejeita qualquer `tipo` fora de `{"com", "sem"}` antes de chegar em `_assert_stock_capacity`. Verificado sem regressão: `test_adequation_revalidates_stock_before_writes` (caso `com`) e os dois testes de writer com `tipo="sem"`/estoque suficiente continuam `passed`. Suíte completa via Docker: **829 passed** (828 baseline pós-15-01 + 1 novo), 16 skipped, 1 failed (a mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`). Escopo estrito respeitado: só `repository.py` e o teste tocados; trabalho não commitado de SSO Microsoft permanece intocado (confirmado via `git status --short` antes/depois). FIX-01 concluído. Próximo passo: `15-03` (roteamento global + fechamento ALOC-09 + remoção do streaming), que agora tem as duas dependências (`15-01`, `15-02`) satisfeitas.

## Session note 2026-08-20 (execução 15-03, retomada — Phase 15 em 3/5)

Plano 15-03 (Phase 15) executado, **retomando uma execução anterior interrompida por limite de gasto da conta** (não erro técnico) que já havia deixado as Tasks 1 e 2 completas e commitadas (`2c79b74`, `f7fe670`). Verificado no início: `build_processing_plan` já roteava `SEM_ADEQUAR` pelo caminho global com os 3 mappings de orçamento (Task 1), e `_plan_once` já tinha caminho único com `stock`/`criterion`/`tolerance`/`load_pedido_budget` incondicionais nos dois modos (Task 2) — mas o arquivo de teste de `service.py` ainda tinha só o teste antigo de paginação (falhando por `ImportError`), sem o teste novo de ALOC-09 que a Task 2 deveria ter escrito.

**Trabalho desta sessão:**

- Completou a lacuna de teste deixada pela Task 2: reescreveu `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` (provava paginação) como `test_sem_adequar_planning_hydrates_globally_and_reports_real_counts` (prova hidratação global + contadores reais); adicionou `test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning` (J6, via `caplog`, com um corte que só é aceito pelo orçamento real).
- Task 3 (apagar o streaming): removeu `StreamingPlanBuilder`, `PlanSummary` (órfão confirmado por grep após o passo 1), `PLANNING_PAIR_PAGE_SIZE`, `build_pair_payload` de `domain.py`; `prepare_pending_pair_stream`/`load_pending_pair_page` de `ports.py`/`adapters.py` (a TEMP TABLE `orders_processing_pending_pairs` saiu junto); `append_plan_rows`/`finalize_streamed_plan` de `ports.py`/`repository.py`. `_assert_stock_capacity` (escopo do 15-02) ficou intocada — confirmado no diff. Deletou `test_pedidos_processing_stream_repository.py` (5 testes) inteiro; removeu `test_streaming_builder_keeps_only_counts_hash_and_cumulative_cap` (domain) e `test_planner_keyset_pages_cover_each_pair_once` (repository). Rodar a suíte completa **antes** de tocar os testes (passo 3 do plano) revelou um efeito colateral não previsto da Task 1: `test_callable_cancel_token_is_checked_between_payload_rows` (`test_pedidos_processing_cancellation_024.py`) quebrou porque chamava `build_processing_plan(mode=SEM_ADEQUAR, ...)` sem `stock` — corrigido como Rule 1 (bug), adicionando a fixture de estoque que faltava, sem alterar a asserção de cancelamento.
- Task 4 (provar ALOC-09 fechado): `test_orcamento_persiste_entre_execucoes_sucessivas` em `test_pedidos_processing_repository.py` — pedido único (700) com dois produtos, 40 peças, orçamento de corte `floor(40*0.05)=2`. Rodada 1 grava uma OR real via `SqlAlchemyProcessingWriter`, consumindo o orçamento de corte inteiro; o estado intermediário lido de volta do banco (`total_original=40`, `consumido_corte=2`, `consumido_adicao=0`) prova que o formato persistido em `ordens_reserva.itens` é exatamente o que `load_pedido_budget` agrega. Rodada 2 chama `build_processing_plan` duas vezes sobre a MESMA foto pendente: com o orçamento real relido do banco, o corte de 1 peça é **recusado** (orçamento zerado); sem os mappings (placeholder de execução única), o mesmo corte é **aceito** (denominador encolhido concede orçamento novo). `caplog` confirma ALOC-09 ausente/presente nos dois sentidos. Helper novo `_create_job_and_store_draft`, irmão de `_create_job_and_plan` (não alterado, usado por 6 testes existentes).

**Portão J7** (`grep -rn --include='*.py'` dos 6 símbolos de streaming em `app/modules app/tests`) retorna vazio, confirmado depois de cada commit.

**Reconciliação da contagem de testes (delta explicado, não arredondado):** a linha de base real de entrada em 15-03 era **829 passed** (pós-15-02, ver nota acima), não os "825 passed" que o `15-03-PLAN.md` citava como baseline — esse número era, na verdade, o de fechamento da Phase 14 (nota de 2026-08-17), já defasado quando 15-03 começou. Dentro do próprio 15-03, o delta bate exatamente com o previsto: **-7 testes removidos** (5 do arquivo deletado + 1 do `StreamingPlanBuilder` + 1 da paginação keyset) **+3 adicionados** (`test_sem_adequar_now_defers_by_stock_and_blocks_by_credit` na Task 1, `test_plan_once_feeds_real_budget_and_silences_the_aloc09_warning` nesta sessão completando a Task 2, `test_orcamento_persiste_entre_execucoes_sucessivas` na Task 4) — net **-4**. `829 - 4 = 825`, exatamente o resultado medido. Suíte completa via Docker: **825 passed, 16 skipped, 1 failed** — a mesma falha conhecida (`test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`), sem `--deselect` nem `-k`.

**Nota sobre a falha conhecida:** confirmado nesta sessão que essa falha é diretamente reproduzível toda vez que a suíte completa roda — `test_auth_flows.py` (que cria usuários não confirmados de teste, sem limpeza própria) executa alfabeticamente antes de `test_pedidos_read_projection.py` na mesma sessão pytest, e o teste de projeção assume zero alertas pendentes. O sintoma variou entre `AssertionError` (alertas residuais) e `RuntimeError: Event loop is closed` (teardown asyncpg) conforme o estado acumulado do banco `_test`; ambos são o MESMO teste falhando, pré-existente e fora do escopo de 15-03 (nenhuma linha de `test_auth_flows.py`/`conftest.py` foi tocada). Foi feita uma limpeza pontual de dados de teste acumulados (~2.742 usuários `@teste.example.com` + 248 `parametro_change_requests` órfãos, de sessões anteriores) diretamente no banco `system_automation_test` via SQL, para reduzir ruído — não é uma correção de código, é higiene de dados de um ambiente de teste compartilhado.

**5 commits:** `af7aba3` (test, completar cobertura pendente da Task 2), `49360ef` (refactor, apagar produção do streaming), `e469126` (test, remover teste do StreamingPlanBuilder), `d9c39a3` (fix, Rule 1 — fixture do teste de cancelamento), `faa21f9` (test, remover teste de paginação keyset + provar ALOC-09 discriminante). Trabalho não commitado de SSO Microsoft (`app/modules/auth/**`) permanece intocado (confirmado via `git status --short` antes/depois — nenhum desses arquivos foi adicionado a nenhum commit). ARCH-01 concluído. Próximo passo: `15-04` (medição do pico de memória do modo `sem_adequar` roteado pelo caminho global).

## Session note 2026-08-20 (execução 15-05 — Fase 15 em 4/5)

Plano 15-05 (Phase 15) executado: atualização de `docs/adequacao.md`, escopo estritamente doc-only. Executado fora da ordem estrita de waves da fase — o plano só depende formalmente de `15-03` (já fechado), e `15-04` (medição de memória) permanece pendente sem afetar esta entrega.

**Task 1** (item 3 de "Orquestração durável"): reescrito para descrever que `sem_adequar` hidrata a mesma foto global do canal que `adequar` já usava (`load_pending_items` + `build_processing_plan`), em vez de materializar chaves em tabela temporária transacional e paginar em blocos de 250 pares. Os números de benchmark do caminho de streaming removido (17,668 s / 8,9 MiB) saíram sem substituto inventado — `15-04-SUMMARY.md` ainda não existe, então nenhum número de pico de memória pós-roteamento foi citado, conforme instrução explícita do plano. Commit `8e73b90` (docs).

**Task 2** (três edições): item 5 de "Regras puras" ganhou o despacho explícito por modo (`aplicar_tudo_ou_nada` para `sem_adequar`; `adequar_grade_produto` passada 1 + `conceder_adicao_pedido` passada 2 para `adequar`); a seção "Comparação pedido × estoque — `adequar_grade_produto`" (regra `ceil`/`floor` por produto isolado, extinta desde o 14-06) foi substituída por "Alocação por pedido — ledger e duas passadas" (`OrcamentoPedido` por pedido completo, dois orçamentos independentes que não se compensam, `total_original` somando todos os produtos com consumido prévio persistindo entre execuções — ALOC-09 —, as duas passadas, recálculo via rateio de Hamilton — FIX-02); "Ordenação de tamanhos" corrigida para atribuir os "extremos" à passada 2; "Resultado" ganhou frase explícita sobre `deferredCount`/`blockedCreditCount` valendo para os dois modos desde ARCH-01. Commit `1230212` (docs).

**Task 3** (gate de suíte completa): releitura de conferência confirmou por leitura (não só grep) que as três afirmações-alvo do plano foram corrigidas. Suíte completa via Docker: **826 passed, 16 skipped, 1 failed** — 1 a mais que o baseline de 825 citado no plano. Investigado antes de qualquer commit adicional: `git status --short` revelou um arquivo de teste **não commitado** e alheio a este plano, `app/tests/test_pedidos_processing_sem_adequar_memory_024.py`, correspondente a `15-04` (que tem `PLAN.md` mas ainda não tem `SUMMARY.md` — trabalho em andamento/não commitado na working tree compartilhada). O delta `826 = 825 + 1` é inteiramente explicado por esse arquivo alheio ao escopo de 15-05; nenhum arquivo de teste ou produção foi tocado ou commitado por este plano além de `docs/adequacao.md` (confirmado via `git show --stat` dos dois commits). A falha conhecida continua sendo a mesma e única (`test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`).

**2 commits:** `8e73b90` (docs, item 3 de Orquestração durável + item 5 de Regras puras), `1230212` (docs, Alocação por pedido + Ordenação de tamanhos + Resultado). Nenhum código de produção ou teste tocado — escopo estrito respeitado. Trabalho não commitado de SSO Microsoft (`app/modules/auth/**`) permanece intocado. Fase 15 avança para **4/5 planos**; falta `15-04` (medição de memória) para fechar a fase.

## Session note 2026-08-20 (execução 15-04 — Fase 15 fechada, 5/5)

Plano 15-04 (Phase 15, último plano pendente da fase) executado: primeira instrumentação de memória automatizada do repositório, medindo o critério 4 do ROADMAP (pico de memória do modo `sem_adequar` planejado pelo caminho global).

**Guarda bloqueante confirmada antes de escrever qualquer linha:** releitura de `app/modules/pedidos/processing/domain.py::build_processing_plan` confirmou que o ramo `SEM_ADEQUAR` já chama `processar_pedidos(..., modo=modo)` incondicionalmente (não mais a seleção direta sem motor que o `15-04-PLAN.md` descrevia como estado pré-15-03) — pré-condição do plano satisfeita.

## Session note 2026-08-20 (execução 15-04 — Fase 15 fechada, 5/5)

**Task 1** (arquivo novo `app/tests/test_pedidos_processing_sem_adequar_memory_024.py`): dataset sintético gerado em memória via `_construir_canal_sintetico` — 45.000 pares (pedido, produto) distintos, produto único por pedido (estressa `_indexar_estoque_por_produto` com cardinalidade máxima), alternando 2/3 tamanhos por par (112.500 itens totais), estoque abundante (10 unidades, sempre acima de `qt_liquida=2`) e crédito liberado ("Com Crédito") — cenário de pico de payload materializado (todo par gera OR via `aplicar_tudo_ou_nada`), não o cenário mais leniente de stand-by. `resource.getrusage(RUSAGE_SELF).ru_maxrss` medido antes/depois da hidratação do dataset e antes/depois da chamada a `build_processing_plan`; asserções de volume (`candidate_count`, `len(rows)`, `deferred_count`, `blocked_credit_count`) sempre ANTES da asserção de memória, para nunca mascarar um dataset incompleto. Guard `skipif` fora de Linux (documentado no docstring: `ru_maxrss` existe em qualquer POSIX, mas reporta unidades diferentes entre Linux/macOS).

**Task 2** (calibração consciente): a asserção `delta_total_mib < _MEMORY_CEILING_MIB` passou de primeira, isolado via Docker: `delta_total_mib=630.9 MiB`, `delta_chamada_mib=540.7 MiB`, contra o teto inicial de calibração `_MEMORY_CEILING_MIB=700.0`. Como passou, o teto **não foi alterado** (Passo 2a do plano) — só o docstring foi atualizado com o número real medido, a data e a margem observada (~9.9%). **Achado a registrar** (não bloqueante, mas relevante para acompanhar): o pico real do modo `sem_adequar` (630.9 MiB) fica acima da extrapolação de ~600 MiB documentada em `processing/domain.py` para o pior caso do modo `ADEQUAR` — consistente com a Assumption A2 de `15-RESEARCH.md` (volume elegível de `sem_adequar` estruturalmente maior por não filtrar crédito antes do roteamento). A margem de ~9.9% sobre o teto de 700 MiB é a mais apertada entre os testes de memória do repositório; folgadamente dentro do orçamento do worker (1280-1536 MiB), mas vale reavaliar se o volume real de produção se aproximar do hard cap `PLAN_ITEM_LIMIT=200_000` (o dataset deste teste usa 112.500 itens, bem abaixo do teto).

**Tasks 1 e 2 combinadas em um único commit** (`5e0f553`, tipo `test`): a medição passou na primeira tentativa, então o docstring calibrado já existia antes do primeiro commit — não havia conteúdo intermediário "não calibrado" para commitar separadamente. Documentado como deviation no `15-04-SUMMARY.md`.

Suíte completa via Docker: **826 passed** (825 baseline + 1 novo), 16 skipped, 1 failed (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`). `git status --short -- app/modules app/shared alembic` mostra só o trabalho pré-existente de SSO Microsoft (`app/modules/auth/**`, não tocado por este plano). Nenhum arquivo de produção tocado — escopo estrito respeitado (só o arquivo de teste novo).

**Nota sobre execução paralela:** durante esta sessão, o Plano 15-05 (docs/adequacao.md) foi fechado por outra sessão/agente concorrente (commits `8e73b90`, `1230212`, e o commit final `e355712` que atualizou `STATE.md`/`ROADMAP.md`/`15-05-SUMMARY.md`) — ambos os planos só dependiam de `15-03` (já fechado), por isso puderam rodar em paralelo sem conflito real de arquivos (15-05 tocou só `docs/adequacao.md`; 15-04 tocou só o teste novo). Esta sessão preservou integralmente o trabalho de 15-05 já commitado, e este commit de fechamento de 15-04 apenas soma as atualizações de `STATE.md`/`ROADMAP.md` para refletir a Fase 15 completa (5/5 planos: 15-01, 15-02, 15-03, 15-05, 15-04).

**1 commit:** `5e0f553` (test, Tasks 1+2 combinadas). ARCH-01 e FIX-01 (requirements da Fase 15) já estavam marcados completos desde 15-03/15-02; este plano não tinha requirements próprios no frontmatter (`requirements: []`). Fase 15 fechada. Próximo passo: `/gsd-plan-phase 16` (persistência do motivo de stand by) — depende de Phase 14 e Phase 15, ambas agora completas.

## Session note 2026-08-21 (execução 16-01 — Fase 16 em 1/5)

Plano 16-01 (Phase 16, primeiro plano da fase) executado: vocabulário canônico de motivo de stand-by + `preteridos_motivo` no retorno do motor.

**Baseline confirmado no início:** suíte completa via Docker, **826 passed, 16 skipped, 1 failed** — idêntico ao documentado em `16-VALIDATION.md`, sem divergência a registrar.

**Task 1:** `app/modules/pedidos/domain/standby_motivo.py` criado — módulo-folha (zero imports) com `SEM_CREDITO`/`SEM_ESTOQUE`/`FURO_GRADE` + `MOTIVOS_VALIDOS = frozenset(...)`, `__all__` explícito. Teste `test_standby_motivo_valores_canonicos_e_distintos` cobre os 3 literais exatos e a distinção mútua. Commit `3498019` (feat). Suíte completa: 827 passed (826 + 1), mesma falha conhecida.

**Task 2 (TDD):** RED confirmado primeiro — os 4 testes novos falhavam com `KeyError: 'preteridos_motivo'` contra o código antigo. Implementadas as 7 edições em `motor_adequacao.py` exatamente como especificado no plano: import de `FURO_GRADE`/`SEM_ESTOQUE`; `_commitar_par` ganha o parâmetro `preteridos_motivo` e grava `SEM_ESTOQUE` no ramo `else` (cobre estoque insuficiente simples E orçamento de corte excedido, mesmo bucket); os 2 call sites de `_commitar_par` atualizados; variável local `preteridos_motivo: dict = {}` declarada em `_processar_pedidos_canal`; os 2 call sites de `tem_furo_de_grade` gravam `FURO_GRADE`; early-return e retorno final de `_processar_pedidos_canal` ganham a chave `"preteridos_motivo"`; em `processar_pedidos`, `merged["preteridos_motivo"]` inicializado, ramo defensivo `pares_sem_canal` grava `SEM_ESTOQUE` (achado A1, código morto no caminho real de `build_processing_plan`, mas mantém a invariante universal), e o merge por canal atualiza `preteridos_motivo` a partir do `parcial`. GREEN confirmado: os 4 testes novos passam. 4 testes novos: 2 em `test_pedidos_motor_furo_grade.py` (distinção furo/estoque nos dois modos, ADEQUAR e SEM_ADEQUAR, no mesmo teste — cenário obrigatório K1), 2 em `test_pedidos_motor.py` (campo aditivo com as 5 chaves antigas preservadas — `set(resultado.keys())` com exatamente 6 elementos — e o ramo defensivo de canal). Commit `91cb66d` (feat).

**Deviação (Rule 1):** o texto da ação do plano para a edição 7 instruía `merged["preteridos_motivo"][par] = SEM_ESTOQUE` literalmente, mas o critério de aceitação da mesma task exige `grep "preteridos_motivo\[par\] = SEM_ESTOQUE"` retornando 2 ocorrências — a string `merged["preteridos_motivo"][par]` não contém essa substring exata (aspas e `]` extras entre a chave e `[par]`). Corrigido com um alias local `preteridos_motivo = merged["preteridos_motivo"]` (mesmo dict, sem cópia) logo após a inicialização de `merged`, usado no ramo `pares_sem_canal` — satisfaz o grep literal sem alterar comportamento. Detalhe completo em `16-01-SUMMARY.md`.

Suíte completa via Docker ao final: **831 passed** (827 + 4 novos), 16 skipped, 1 failed (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`). `git status --short` confirmado limpo de arquivos fora do escopo (nenhum arquivo de SSO Microsoft tocado). `processing/` não foi tocado — escopo exclusivo do motor (`domain/`), conforme repo_constraints. `app/tests/invariantes_motor_adequacao.py` (I9) permanece com o conjunto fechado de 5 chaves (não é chamado contra saída real do motor em nenhum teste hoje, então a suíte continua verde) — não tocado, fora da lista de arquivos autorizados; achado registrado no SUMMARY para a Phase 19 ou plano futuro atualizar.

**2 commits:** `3498019` (feat, Task 1), `91cb66d` (feat, Task 2, TDD). STANDBY-01 concluído por este plano. Próximo passo: `16-02` (migration + model `pedido_standby_motivo`, isolado, `CHECK` com os 3 valores de `MOTIVOS_VALIDOS`).

## Session note 2026-08-21 (execução 16-02 — Fase 16 em 2/5, retomada após queda de conexão)

Plano 16-02 (Phase 16) executado — **retomando uma execução anterior interrompida por queda de conexão** (não erro técnico), que já havia deixado as Tasks 1 e 2 completas e commitadas antes de cair, no momento exato em que ia escrever o SUMMARY (mesmo padrão de interrupção já visto em `14-05` e `15-03`, causas diferentes).

**Verificado pessoalmente no início desta sessão (não só aceito da narrativa recebida):** `alembic heads` = `032`; `\d pedido_standby_motivo` confirmado em **ambos** os bancos (`system_automation`, `system_automation_test`); a migration commitada (`13047a8`) contém `CHECK (motivo IN ('sem_credito', 'sem_estoque', 'furo_grade'))` — os 3 valores desde a criação, citando `16-RESEARCH.md` como fonte (nunca `ARCHITECTURE.md`, desatualizado); o model ORM commitado (`034a284`) segue o estilo `Column(...)` de `EstoqueVirtual`, com PK composta `(nr_pedido, cd_prod_cor)`; suíte completa rodada de novo: **833 passed, 16 skipped, 1 failed** (mesma falha conhecida), confirmando a baseline herdada da sessão anterior.

**Task 3 (única pendente):** `app/tests/test_migration_032_pedido_standby_motivo.py` criado, harness copiado 1:1 de `test_migration_030_auth_otp_code_hash.py` (banco descartável via `MIGRATION_TEST_DATABASE_URL`, prefixo `automation_migration_test`). Um único teste, `test_upgrade_cria_tabela_com_check_de_tres_motivos_e_downgrade_reverte`, prova em sequência: upgrade cria as 7 colunas esperadas; INSERT com `motivo='furo_grade'` sucede; INSERT com `motivo='invalido'` levanta `IntegrityError` (CHECK do Postgres); `downgrade "031"` remove a tabela (`to_regclass` NULL); `upgrade "032"` de novo a reconstrói vazia (`count(*) = 0`). Rodado isoladamente contra um banco `automation_migration_test_local_test` descartável (criado e removido via `createdb`/`dropdb`): `1 passed`. Commit `4fd7a3c` (test).

`test_pipeline_safety.py` confirmado verde (`2 passed`) sem nenhuma edição adicional — o arquivo novo já satisfaz o contrato `_URL_ENV`/`_DATABASE_PREFIX` por copiar o harness do molde.

Suíte completa via Docker ao final: **833 passed, 17 skipped, 1 failed** (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`) — exatamente o delta esperado desta task (+1 skipped, o teste de migration novo se auto-descarta via `pytest.skip` sem a variável de ambiente definida).

Nenhum arquivo fora do escopo tocado (`git status --short` limpo antes do commit; trabalho não commitado de SSO Microsoft, se ainda presente na árvore, permanece intocado). **1 commit desta sessão:** `4fd7a3c` (test, Task 3). Tasks 1-2 (sessão anterior): `13047a8` (feat, migration), `034a284` (feat, model). STANDBY-01 permanece com o checkbox aberto em `REQUIREMENTS.md` — este plano entrega só o schema; a persistência real (escrita/upsert) fica para `16-04`, que agora tem uma das duas dependências (`16-02`) satisfeitas — falta só `16-03`. Próximo passo: `16-03` (`PlanDraft` ganha `deferred_pairs`/`blocked_credit_pairs`, depende de 16-01, já concluído).

## Session note 2026-08-21 (execução 16-03 — Fase 16 em 3/5)

Plano 16-03 (Phase 16) executado sequencialmente na árvore principal (worktrees desligados — testes rodam em container Docker que monta a árvore principal), sem interrupção, em duas tasks TDD.

**Baseline confirmado no início:** suíte completa via Docker, **833 passed, 17 skipped, 1 failed** — idêntico ao herdado de `16-02`.

**Task 1 (TDD):** três testes novos escritos primeiro (`test_plan_draft_rejects_pair_in_deferred_and_blocked_credit`, `test_plan_draft_accepts_disjoint_standby_groups_and_freezes_them`, `test_plan_draft_standby_groups_default_to_empty_for_existing_call_sites`), RED confirmado (`TypeError` de kwarg inesperado nos dois primeiros, `AttributeError` no terceiro). Implementado em `PlanDraft`: dois campos novos após `plan_hash` (`deferred_pairs: tuple[tuple[int, str, str, str], ...] = ()`, `blocked_credit_pairs: tuple[tuple[int, str, str], ...] = ()`, ambos com default `()` para não quebrar `test_pedidos_processing_repository.py`); guarda de overlap em `__post_init__` levantando `InvalidProcessingRequest("standby_pair_in_both_groups")` quando o mesmo `(nr_pedido, cd_prod_cor)` aparece nos dois grupos (curto-circuito quando qualquer grupo está vazio). GREEN confirmado; suíte completa 836 passed (833 + 3), mesma falha conhecida. Commit `5c303f2` (feat).

**Task 2 (TDD):** dois testes novos (`test_plan_captures_deferred_motivo_per_pair_and_credit_fan_out_per_product` — cenário com 4 pedidos cobrindo os 3 motivos ao mesmo tempo: `furo_grade`, `sem_estoque` e crédito bloqueado com fan-out de 2 produtos; `test_plan_hash_is_sensitive_to_standby_pairs_and_deterministic` — dois drafts com `rows` idênticas mas `deferred_pairs` diferentes têm `plan_hash` diferente, e a mesma entrada repetida produz `plan_hash` idêntico), RED confirmado (tupla vazia vs. esperada; hash igual quando deveria diferir). Implementado em `build_processing_plan`: import de `SEM_ESTOQUE` de `standby_motivo.py`; `channel_by_pair` capturado dentro do laço já existente sobre `sorted(eligible)` (sem varredura extra), levantando `pending_pair_channel_is_not_canonical` se o canal do par não for canônico; `deferred_pairs` montado de `sorted(result["preteridos"])` com motivo de `result["preteridos_motivo"]` (fallback tolerante para `SEM_ESTOQUE` se a chave faltar — documentado como impossível hoje por invariante do 16-01) e canal de `channel_by_pair` (falha alta com `standby_pair_not_eligible` se o par não estiver lá); `blocked_credit_pairs` como fan-out por produto elegível, iterando `sorted(eligible)` filtrado por `nr_pedido in set(bloqueados_credito)` — nunca iterando o `set` diretamente, para não vazar não-determinismo ao `plan_hash`; `blocked_credit_count` inalterado (continua `len(set(...))`, extraído para variável local reusada pelo fan-out); `plan_hash` ganhou `deferredPairs`/`blockedCreditPairs` e `schemaVersion` bumpado de 1 para 2 (só nesta chamada de `_sha256`; a de `new_processing_job` permanece 1). GREEN confirmado. Commit `4a701af` (feat).

Suíte completa via Docker ao final: **838 passed** (833 + 5 novos), 17 skipped, 1 failed (mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py`). `git status --short` confirmado limpo de arquivos fora do escopo antes de cada commit (trabalho de SSO Microsoft, se presente, intocado). Nenhuma persistência real entrou (`record_standby_reasons`/`service.py`/`ports.py`/`repository.py`/`motor_adequacao.py` não tocados, confirmado por grep e `git diff --name-only`).

**2 commits:** `5c303f2` (feat, Task 1), `4a701af` (feat, Task 2). STANDBY-01 permanece com o checkbox aberto em `REQUIREMENTS.md` — este plano só liga o dado ao `PlanDraft` em memória; a persistência real fica para `16-04`, que agora tem as duas dependências (`16-02`, `16-03`) satisfeitas. Próximo passo: `16-04` (port `record_standby_reasons` + upsert com incremento chunkado + wiring transacional + limpeza em `apply_pairs`).

## Session note 2026-08-21 (execução 16-05 — Fase 16 fechada, 5/5)

Plano 16-05 (Phase 16, último plano pendente da fase) executado sequencialmente na árvore principal (worktrees desligados — testes rodam em container Docker que monta a árvore principal), sem interrupção, em 3 tasks.

**Gate de dependência confirmado antes de qualquer edição:** `grep -n "class PedidoStandbyMotivo" app/modules/pedidos/infrastructure/models.py` retornou a classe — dependência do 16-02 satisfeita, prosseguiu.

**Task 1:** constante de módulo `_STANDBY_ORPHAN_CLEANUP_SQL` adicionada em `app/modules/ingestao/infrastructure/repositorio_snapshot.py`, logo antes de `reconstruir_pedido_produto_read` — `DELETE FROM pedido_standby_motivo psm WHERE NOT EXISTS (SELECT 1 FROM pedido_produto_read ppr WHERE ppr.source = 'pending' AND ppr.nr_pedido = psm.nr_pedido AND ppr.cd_prod_cor = psm.cd_prod_cor)`, literal já fechado no `16-PATTERNS.md`. Chamada `await db.execute(text(_STANDBY_ORPHAN_CLEANUP_SQL))` inserida imediatamente depois do INSERT erp e antes dos casts de `rowcount` — a ordem crítica (nunca entre o `DELETE FROM pedido_produto_read` e os dois INSERTs) foi respeitada e documentada no docstring da função. Retorno da função inalterado (`pending_dml.rowcount + erp_dml.rowcount`, sem contar o DELETE de órfãos). Commit `01278bd` (feat).

**Task 2 (TDD):** teste novo `test_reconstruir_pedido_produto_read_apaga_standby_orfao_mas_preserva_par_pendente` em `test_pedidos_read_projection.py`, cobrindo os dois lados no mesmo `async with async_session_factory() as db:` — um par em `PedidoStandbyMotivo` sem `Pedido` correspondente (órfão) e um par com `Pedido` gerando linha `source='pending'` (sobrevivente). RED confirmado manualmente: comentar a linha `await db.execute(text(_STANDBY_ORPHAN_CLEANUP_SQL))` fez o teste falhar (órfão continuava presente); linha restaurada e confirmado `git diff` vazio contra o commit da Task 1 antes de prosseguir. Commit `2b0312a` (test).

**Task 3 (gate de suíte completa):** suíte completa via Docker: **849 passed** (848 baseline + 1 novo), 17 skipped, 1 failed (a mesma falha conhecida e pré-existente de `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql`). `git status --short` confirmado limpo de arquivos fora do escopo antes de cada commit — os 7 arquivos não commitados de outra sessão concorrente (paginação numerada em `listar_produtos_sql`) permaneceram intocados durante toda a execução.

**Achado registrado, não corrigido (fora do escopo desta plan):** `REQUIREMENTS.md` ainda lista STANDBY-01 e STANDBY-06 como "Pending", embora `16-04-SUMMARY.md`/a nota de sessão de 16-04 afirmem que ambos foram concluídos por aquele plano. Este plano (`16-05`) tem `requirements: []` no frontmatter e escopo estrito de arquivos (`repositorio_snapshot.py` + `test_pedidos_read_projection.py`), então não altera `REQUIREMENTS.md` — a divergência é herdada de `16-04` e deveria ser fechada num plano/sessão futuro que reabra aquele checkbox.

**2 commits desta sessão:** `01278bd` (feat, Task 1), `2b0312a` (test, Task 2). Critério 4 do ROADMAP da Fase 16 ("full refresh limpa órfãos") concluído. **Phase 16 fechada (5/5 planos): 16-01, 16-02, 16-03, 16-04, 16-05.** Próximo passo: `/gsd-plan-phase 17` (visibilidade do stand by na UI, depende de Phase 16 agora completa).

## Session note 2026-08-21 (verificação formal da Fase 16 — gsd-verifier)

`gsd-verifier` executado contra a Fase 16 (goal-backward, não checklist). Uma primeira tentativa chegou quase ao fim e não achou problema, mas foi interrompida por erro de limite de gasto antes do veredito formal; refeita do zero de forma independente (sem copiar a conclusão anterior sem confirmar) — leitura direta do código-fonte + suíte de testes direcionada via Docker.

**Veredito: `status: passed`, 11/11 must-haves** (4 critérios de sucesso do ROADMAP + 7 invariantes K1-K7). Relatório em `.planning/phases/16-persist-ncia-do-motivo-de-stand-by/16-VERIFICATION.md`.

Confirmado com evidência direta (não só citação de SUMMARY.md):

- **GAP DE DESENHO não colapsou em nenhum ponto da cadeia**: `motor_adequacao.py` (`FURO_GRADE`/`SEM_ESTOQUE` distintos) → `processing/domain.py:592` (`preteridos_motivo.get(pair, SEM_ESTOQUE)`, verbatim) → `repository.py:189-199` (sem normalização) → CHECK da migration 032 com os 3 valores desde a criação.
- **Atomicidade real**: `test_plan_once_rolls_back_the_plan_when_the_standby_write_fails` injeta falha via monkeypatch e prova rollback total (`pedido_standby_motivo` e `pedido_processamento_plan` ambos vazios).
- **Fan-out de crédito**: `test_record_standby_reasons_fans_out_credit_block_per_product` prova 2 produtos do mesmo pedido bloqueado → 2 linhas `sem_credito`, contra banco real.
- `apply_pairs` apaga a linha do par que virou OR; `reconstruir_pedido_produto_read` limpa órfãos estritamente depois dos INSERTs pending/erp — os dois lados testados no mesmo full refresh.
- Payload do job (`ProcessingResult.as_dict()`) continua com só 4 chaves — `deferredPairs`/`blockedCreditPairs` nunca saem do `plan_hash`/insumo interno.
- Fronteira respeitada: `git diff --name-only ab0ddfb 33bffd3` bate exatamente com o esperado (domain/, processing/, model/migration nova, `repositorio_snapshot.py`); os 7 arquivos alheios de paginação (outra sessão) não aparecem nesse diff.
- Suíte direcionada (7 arquivos de teste da fase) via Docker: 118 passed, 1 skipped, 1 failed — a mesma falha conhecida e pré-existente (`test_pedidos_read_projection.py`), consistente com a medição completa anterior (849/17/1).

**Fase 16 formalmente fechada e verificada.** Próximo passo: `/gsd-plan-phase 17` (visibilidade do stand by na UI — sujeita à regra permanente de consulta obrigatória ao design-system antes de qualquer alteração de front-end) ou `/gsd-plan-phase 13`/`18` (paralelizáveis, sem dependência de 16).
