# Roadmap: automation OR

## Active focus

**Milestone v1.3 — Motor de alocação de OR fiel às regras de negócio** (roadmap criado 2026-08-14).
Phases 13–19 planejadas, cobertura 21/21 requirements. Ver § Milestone v1.3 ao final deste arquivo.

> **Nota 2026-08-14:** Milestone v1.2 (Platform Hardening) **pausado** para dar precedência ao v1.3 — regra de negócio incorreta no motor tem impacto operacional direto. Phases 4–9 preservadas e retomáveis (ver `PROJECT.md` § Milestone pausado).
> **Nota 2026-08-10:** Milestone v1.3-Aurora (migração completa de dados) **cancelada** — preflight revelou que o Aurora já está configurado com schema Alembic 028 e dados ativos. Número v1.3 reaproveitado pelo motor de alocação.

---

## Milestone v1.1 — Saída de OR no formato Linx (histórico)

### Overview

Este milestone materializa as ORs geradas numa tabela Postgres no layout exato aceito pelo ERP Linx, pronta para o futuro load via barramento. O caminho é: primeiro o schema existe (as duas tabelas novas, vazias); depois a referência tamanho→posição passa a ser ingerida do Databricks e a lógica pura de conversão de grade transforma tamanhos em posições `e1..e48`; por fim o fluxo de geração (adequar/sem adequar) passa a gravar, na mesma transação, a linha Linx por cliente×produto×cor. `ordens_reserva` (JSONB) continua intocada como modelo interno da UI — a tabela Linx é só a "bandeja de saída".

**Status:** Phases 1–3 complete (2026-08-04 → 2026-08-06). Preservado para rastreio; não deletar.

### Phases (v1.1)

**Phase Numbering:**

- Integer phases (1, 2, 3): Milestone Linx
- Integer phases (4–9): Milestone Platform Hardening (abaixo)
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Schema Linx e referência de posição** - Migration 015 cria `produto_tamanho_posicao` e `ordens_reserva_linx`, ambas vazias, sem alterar nada existente (completed 2026-08-04)
- [x] **Phase 2: Ingestão da referência + conversão de grade** - Sync de 2h passa a ingerir a referência tamanho→posição; função pura converte grade interna em `e1..e48` (completed 2026-08-05)
- [x] **Phase 3: Escrita da OR no formato Linx** - Cada geração (com/sem adequação) grava/atualiza a linha Linx por cliente×produto×cor, na mesma transação (completed 2026-08-06)

## Phase Details (v1.1)

### Phase 1: Schema Linx e referência de posição

**Goal**: O banco tem as duas tabelas novas do milestone — `produto_tamanho_posicao` (referência tamanho→posição) e `ordens_reserva_linx` (layout Linx) — criadas por uma migration manual, ambas vazias, sem tocar em nenhuma tabela existente.
**Depends on**: Nothing (primeira fase)
**Requirements**: LINX-01, LINX-04
**Success Criteria** (what must be TRUE):

  1. `alembic upgrade head` (migration 015) cria `produto_tamanho_posicao` (`cd_prod_cor`, `sg_tamanho`, `nr_posicao`) e `ordens_reserva_linx` com as colunas do layout descoberto no CSV real (`nome_clifor`, `produto`, `cor_produto`, `pedido`, `preco1`, `valor_embalado`, `qtde_embalada`, `e1..e48`) mais as colunas de controle (`id`, `nr_pedido`, `cd_prod_cor`, `tipo`, `created_at`)
  2. Logo após a migration, `SELECT COUNT(*)` nas duas tabelas novas retorna 0 — nenhuma linha inserida por backfill
  3. `alembic downgrade -1` remove as duas tabelas sem erro, e um novo `upgrade head` as recria — migration reversível nos dois sentidos
  4. Nenhuma coluna de tabela existente (`ordens_reserva`, `pedidos`, `estoque`, ...) é alterada pela migration; `alembic revision --autogenerate` rodado depois da 015 não aponta diffs pendentes

**Plans**: 2 plans
Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Models ORM `ProdutoTamanhoPosicao` (ingestao) e `OrdemReservaLinx` (pedidos, 82 colunas) + registro em `alembic/env.py` e `test_schema_guard.py`

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Migration manual 015 cria as 2 tabelas, aplicação no dev_db e prova dos 4 critérios (layout, COUNT=0, reversibilidade, zero diff)

### Phase 2: Ingestão da referência + conversão de grade

**Goal**: A referência tamanho→posição chega ao Postgres a cada sync de 2h (5ª fonte, full refresh), e existe uma função pura — testada com dados reais já ingeridos — que converte a grade interna de tamanhos em posições `e1..e48`.
**Depends on**: Phase 1 (a tabela `produto_tamanho_posicao` precisa existir antes de receber INSERT; a conversão usa dados reais ingeridos nesta própria fase para um teste realista)
**Requirements**: ING-01, LINX-03
**Success Criteria** (what must be TRUE):

  1. A nova função de sincronização (5ª fonte) grava em `produto_tamanho_posicao` os registros da view Databricks `programa_estagio.refined.system_automation_prod_tamanho_ref` (`cd_prod_cor`, `sg_tamanho`, `nr_posicao`) — verificável por teste de integração análogo a `test_ingestao_sync.py`
  2. `sincronizar_tudo` chama a 5ª fonte dentro do mesmo commit único das outras 4 — uma falha na ingestão da referência não deixa as outras fontes parcialmente sincronizadas
  3. Duas chamadas sucessivas da sincronização substituem completamente as linhas antigas de `produto_tamanho_posicao` (DELETE+INSERT) — sem duplicar nem acumular linhas de sincronizações anteriores
  4. Uma função pura de conversão recebe a grade interna (`{sg_tamanho: qtd}`) mais o `cd_prod_cor` e devolve `{"e1": qtd, ..., "e48": qtd}`, testada sem acesso a banco usando os dados reais já ingeridos nesta fase
  5. Quando um `sg_tamanho` do pedido não tem `nr_posicao` correspondente na referência, a conversão emite um aviso no log e devolve o restante da grade convertida, sem lançar exceção nem interromper a geração

**Plans**: 5 plans
Plans:
**Wave 1**

- [x] 02-01-PLAN.md — Fundação da 5ª fonte: env var, parse tolerante de nr_posicao (D-01), agregação com conflito de posição (D-03/D-04), reader e repositório de substituição
- [x] 02-02-PLAN.md — Função pura `converter_grade_para_posicoes` (LINX-03) + testes sintéticos (critério 5, D-03)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 02-03-PLAN.md — `sincronizar_referencia_tamanhos` (guard D-02) + `sincronizar_tudo` estendido + contrato HTTP + testes de integração (critérios 1-3)

**Wave 3** *(blocked on Wave 2 completion)*

- [x] 02-04-PLAN.md — Checkpoint humano: sync real contra o Databricks, evidência dos critérios 1/3 fora de mock, captura da amostra real para a fixture do critério 4

**Wave 4** *(blocked on Wave 3 completion)*

- [x] 02-05-PLAN.md — Fixture de dados reais + teste do critério 4 (LINX-03), fechando a cobertura completa da Fase 2

### Phase 3: Escrita da OR no formato Linx

**Goal**: Cada geração de ordens de reserva (com ou sem adequação) grava/atualiza, na mesma transação, 1 linha em `ordens_reserva_linx` por cliente×produto×cor — usando a conversão da Fase 2 e o schema da Fase 1.
**Depends on**: Phase 1, Phase 2 (schema precisa existir; escrita usa a função de conversão de grade)
**Requirements**: LINX-02
**Success Criteria** (what must be TRUE):

  1. O fluxo de adequação ("adequar") grava em `ordens_reserva_linx`, para cada cliente×produto×cor, `nome_clifor`, `pedido` (= nº do pedido), `produto` e `cor_produto` (split de `cd_prod_cor` no `"|"`), `preco1`, `valor_embalado` e `qtde_embalada` — verificável por teste de integração no fluxo `/adequar`
  2. O fluxo sem adequação ("sem_adequar") grava a mesma linha Linx com os mesmos campos preenchidos — mesma garantia para os dois caminhos de geração
  3. A gravação Linx ocorre na mesma transação da gravação em `ordens_reserva` — um teste que força falha após salvar a linha Linx confirma rollback conjunto (nenhuma tabela fica com dado parcial)
  4. Rodar a geração duas vezes para o mesmo cliente×produto×cor atualiza (upsert) a linha existente em `ordens_reserva_linx` em vez de duplicá-la
  5. Colunas do layout Linx sem fonte conhecida hoje (`FILIAL`, `ROMANEIO`, `CAIXA`, etc.) permanecem NULL/vazias após a gravação — nenhum valor inventado

**Plans**: 3 plans
Plans:
**Wave 1**

- [x] 03-01-PLAN.md — `montar_linha_linx` (domínio puro): D-01 (zero posições → None), D-03 (soma exata + arredondamento), split defensivo, critério 5
- [x] 03-02-PLAN.md — Infraestrutura: leitura em lote da referência de posição (chunk 500) + repositório de upsert `salvar_linhas_linx` (critério 4)

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 03-03-PLAN.md — Wiring nos fluxos `/adequar` e `/sem_adequar` (critérios 1-3) + re-export D-02a em `service.py`, fechando LINX-02

## Progress (v1.1)

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Schema Linx e referência de posição | 2/2 | Complete    | 2026-08-05 |
| 2. Ingestão da referência + conversão de grade | 5/5 | Complete    | 2026-08-05 |
| 3. Escrita da OR no formato Linx | 3/3 | Complete   | 2026-08-06 |

---

## Milestone v1.2 — Platform Hardening / Audit Remediation

### Overview

Fecha gaps de plataforma descobertos no audit 2026-08-07: CI antes do ECS, auth/OTP/seed/JWT fail-fast, deploy de worker+beat+migrations, higiene Docker de prod, contratos front↔API + docs verdadeiras, observabilidade mínima (Sentry, metrics auth, CORS). Referência de implementação: Collab API (`collab-backend`).

**Status:** Planning scaffold only — planos executáveis (`*-PLAN.md`) ainda não criados via GSD. **Mas** (reverificado 2026-08-12 direto no código) commits regulares fora do fluxo formal já fecharam boa parte dos requirements destas fases — ver `.planning/REQUIREMENTS.md` § v1.2 para status por requirement. Os checkboxes abaixo marcam a fase como fechada só quando **todos** os requirements dela já estão Done; fases com Partial/Pending continuam abertas.
**Canonical:** `.planning/milestones/v1.2-PLATFORM-HARDENING.md`

**Pausado em 2026-08-14** para dar precedência ao v1.3 (motor de alocação) — ver `PROJECT.md` § Milestone pausado. Fases preservadas e retomáveis; nenhum diretório de fase foi apagado.

### Phases (v1.2)

- [ ] **Phase 4: CI quality gates before deploy** — ruff / pyright / pytest bloqueiam ECS (CI-01, CI-02, CI-03) — CI-02/03 done, CI-01 parcial (falta pyright)
- [x] **Phase 5: Auth / OTP / seed / JWT fail-fast** — defaults seguros + OTP entregue em PROD (SEC-01..07) — todos os 7 requisitos done (completed 2026-08-13)
- [x] **Phase 6: Deploy worker + beat + migrations** — pipeline ECS operacional completo (OPS-01..03) — todos done (OPS-03 com desenho diferente do planejado; ver nota em CLAUDE-EXECUTION-BRIEF.md)
- [ ] **Phase 7: Docker prod hygiene** — `.dockerignore` + sem `--group dev` (OPS-04, OPS-05) — OPS-05 done, OPS-04 pending
- [ ] **Phase 8: Contratos front↔API + docs truth** — parâmetros dinâmicos; docs sem módulos fantasma (CONTRACT-01..03) — CONTRACT-02 done, 01/03 pending
- [ ] **Phase 9: Observability** — Sentry, `/metrics` autenticado, CORS prod (OBS-01..03) — todos pending
- [ ] **Phase 11: Parâmetros — fluxo de solicitação, aplicação na aprovação e auditoria por papel** — solicitação chega ao backend, aprovação aplica o valor, e fica registrado quem autorizou e com qual papel (PARAM-01..05) — 1/5 plans executados (11-01 concluído; 11-02..11-05 planejados)
- [ ] **Phase 12: Exceções de parâmetro por cliente e bloqueio de recebimento** — tolerância diferente para um cliente e bloqueio de quem não pode receber mercadoria, ambos com vigência início/fim (PARAM-06, PARAM-07) — não planejada

### Phase Details (v1.2)

#### Phase 4: CI quality gates before deploy

**Goal**: Nenhum deploy ECS ocorre se lint, types ou testes falharem.
**Depends on**: Nothing
**Requirements**: CI-01, CI-02, CI-03
**Success Criteria**:

  1. Jobs de quality (ruff + pyright) e tests (pytest) existem no workflow
  2. Job `deploy` declara dependência desses jobs
  3. Falha em qualquer gate impede atualização do serviço ECS

**Plans:** TBD (`/gsd-plan-phase 4`)

#### Phase 5: Auth / OTP / seed / JWT fail-fast security

**Goal**: PROD não sobe com JWT/seed inseguros; OTP chega por e-mail.
**Depends on**: Nothing (pode paralelizar com Phase 4)
**Requirements**: SEC-01, SEC-02, SEC-03, SEC-04, SEC-05, SEC-06, SEC-07
**Success Criteria**:

  1. Boot PROD falha sem `JWT_SECRET` forte
  2. Seed default off / proibido em PROD
  3. OTP PROD via SMTP/comunicações
  4. Runbook para usuários seed legados
  5. Código OTP armazenado com hash, não plaintext
  6. Sign-out revoga o refresh token (ou todos os do usuário)
  7. Rate limit em sign-in e confirmação de OTP

**Plans:** 5/5 plans complete
Plans:
**Wave 1** *(paralelos — arquivos disjuntos)*

- [x] 05-01-PLAN.md — Default seguro de `SEED_AUTH_ON_STARTUP` + runbook `docs/seguranca.md` das contas seed (SEC-02, SEC-04)
- [x] 05-02-PLAN.md — `otp_hash.py` (HMAC-SHA256) + `AuthOtpChallenge.code` para `String(64)` + migration **030** [BLOCKING] e teste destrutivo (SEC-05, fundação) — renumerada de 029 para 030: uma migration 029 concorrente (nome/cpf/telefone, outra sessão) chegou primeiro

**Wave 2** *(bloqueado pela migration do Wave 1)*

- [x] 05-03-PLAN.md — `repositorio_otp` grava hash e entrega o OTP por SMTP em PROD; helper `ler_otp` da suíte migrado para captura por log (SEC-03, SEC-05)

**Wave 3** *(bloqueado por 05-03 — mesmo arquivo de teste)*

- [x] 05-04-PLAN.md — `iat` no refresh token + corte `auth:signed_out_since:{user_id}` no Redis + sign-out autenticado (SEC-06)

**Wave 4** *(bloqueado por 05-04 — mesmo `routes.py`)*

- [x] 05-05-PLAN.md — Rate limit usuário+IP em sign-in/OTP: fail-open em sign-in, fail-closed nos endpoints de OTP (SEC-07)

**Fora do fluxo formal (adicionado após os 5/5 planos, sem plano próprio):** lockout de conta por falhas consecutivas em sign-in/confirmação de OTP, complementar ao rate limit por minuto (SEC-08) — ver `app/shared/infrastructure/account_lockout.py`.

#### Phase 6: Deploy worker + beat + migrations

**Goal**: Ingestão agendada e schema Alembic acompanham o release da API.
**Depends on**: Phase 4 recomendada
**Requirements**: OPS-01, OPS-02, OPS-03
**Success Criteria**:

  1. ECS worker atualizado no pipeline
  2. ECS beat atualizado no pipeline
  3. `alembic upgrade head` no deploy antes da estabilidade

**Plans:** TBD (`/gsd-plan-phase 6`)

#### Phase 7: Docker prod hygiene

**Goal**: Imagem ECR enxuta e contexto de build seguro.
**Depends on**: Pode paralelizar com 4–6
**Requirements**: OPS-04, OPS-05
**Success Criteria**:

  1. `.dockerignore` presente e efetivo
  2. `uv sync` de prod sem `--group dev`

**Plans:** TBD (`/gsd-plan-phase 7`)

#### Phase 8: Contratos front↔API (parâmetros) + docs truth

**Goal**: Front usa `GET /api/v1/parametros`; docs batem com o código.
**Depends on**: Coordenação com repo front
**Requirements**: CONTRACT-01, CONTRACT-02, CONTRACT-03
**Success Criteria**:

  1. Hardcodes de parâmetros cobertos pela API removidos no front
  2. Docs sem `alertas`/`history` fantasmas
  3. Contrato de parâmetros documentado

**Hardcodes confirmados no front (2026-08-14)** — alvo do critério 1:

- `adequacaoFactor = 1.05` em `features/pedidos/ui/order-detail-modal.tsx:76`
- `base * 1.05` e `sem * 1.05` em `widgets/pedido-dashboard/ui/orders-list.tsx:869,875`
- Rótulos "+5%" fixos (`orders-list.tsx:1551`, "Ajustado CD (+5%)")
- Visibilidade: `canViewTable = hasMinLevel(30)` esconde a tabela de operacional/básico, contrariando a regra "todos veem o que está valendo"; `canEdit`/`canRequest` devem colapsar em `canRequest = hasMinLevel(30)`

⛔ **Restrição dura (Victória, 2026-08-14): não alterar o design system.** `frontend/design-system` é cópia vendorizada (`file:./design-system`), não é submódulo e não se edita neste repo. Consumir só pelas fachadas de `shared/ui/` e resolver por composição no app. Se faltar componente, o pedido vai para o repo `do projeto-tt-design-system` — fora do escopo desta fase.

**Depende de**: Phase 11 (o selo "ativo no motor" e o valor dinâmico dependem do contrato criado lá)

**Plans:** TBD (`/gsd-plan-phase 8`)

#### Phase 9: Observability (Sentry, metrics auth, CORS)

**Goal**: Erros visíveis; métricas e CORS seguros em PROD.
**Depends on**: Phase 5 útil para secrets
**Requirements**: OBS-01, OBS-02, OBS-03
**Success Criteria**:

  1. Sentry via DSN opcional
  2. `/metrics` não público anônimo
  3. CORS PROD só com origens reais

**Plans:** TBD (`/gsd-plan-phase 9`)

#### Phase 11: Parâmetros — fluxo de solicitação, aplicação na aprovação e auditoria por papel

**Goal**: Uma alteração de parâmetro nasce como solicitação no painel, é aprovada por administrador ou admin_técnico, **passa a valer no motor de adequação** no mesmo ato, e deixa registrado quem pediu, quem autorizou, com qual papel, por quê e qual era o valor anterior.

**Depends on**: Nothing (independente das Phases 4–9). Phase 8 consome o resultado desta (contrato de parâmetros no front).

**Requirements**: PARAM-01, PARAM-02, PARAM-03, PARAM-04, PARAM-05

**Contexto descoberto (2026-08-14, verificado no código)**:

- A tela de Parâmetros nunca escreve em `parametros`: todo botão faz `POST /change-requests` ([page.tsx:136](../../../frontend/app/(app)/parametros/page.tsx))
- A solicitação de edição/exclusão nem chega ao backend: o payload manda `parameter_id` no nível raiz contra um schema `extra="forbid"` → 422; `target_chave` nunca é enviado
- Aprovar só muda `status` — `review_change_request` não aplica o valor (`repositorio_change_request.py:93`)
- O único caminho que altera o valor é `PUT /api/v1/parametros/{chave}`, que nenhuma tela chama e que não deixa rastro de auditoria
- `justification` é texto fixo do frontend ("Edição solicitada via painel"), nunca perguntado ao usuário
- O motor lê exatamente 2 chaves, num único ponto (`adapters.py:304`): `tolerancia_adequacao` e `criterio_selecao`. Chave nova criada pelo painel é inerte até existir leitura no código
- Sem validação de faixa: `tolerancia_adequacao = 10` (querendo 10%) grava 1000% e desliga o Stand By na prática

**Decisões de negócio (Victória, 2026-08-14)**:

- Todos os níveis **veem** os parâmetros vigentes; **solicitar** é gestor(30)+; **aprovar** é administrador(40)+ — o backend já reflete isso, o frontend não (esconde a tabela abaixo de 30)
- ~~Admin e admin_técnico também passam pelo formulário — caminho único de escrita, sem porta lateral~~ — **REVOGADA em 2026-08-18, ver abaixo**
- **Autoaprovação permitida**: quem abre pode aprovar a própria solicitação. O controle é de registro, não de dupla checagem
- Auditoria é requisito central: tem que ficar registrado se quem autorizou era admin ou admin_técnico

**Decisões de negócio (Victória, 2026-08-18)** — revisão do fluxo de escrita:

- **REVOGA "caminho único de escrita, sem porta lateral" (14/08).** Passam a existir **dois** caminhos, e ambos são de primeira classe:
  - **gestor(30)+** → "Solicitar alteração"/"Solicitar exclusão" → vira solicitação pendente → **administrador(40)+ aprova** → só então passa a valer
  - **administrador(40)+ e admin_técnico** → "Editar"/"Excluir" nas Ações → **muda na hora, sem aprovação**
- Consequência direta: o `PUT`/`DELETE` direto deixa de ser válvula de emergência de admin_técnico e vira **caminho normal de administrador(40)+**. Isso **substitui o Success Criteria 9** original.
- Consequência de auditoria: como agora há duas portas de escrita, **as duas** precisam do mesmo registro (quem, papel congelado, valor anterior, quando). Auditar só o fluxo de aprovação deixaria de fora o caminho que o admin vai usar no dia a dia.
- "Aprovado = passa a valer" continua valendo para o caminho do gestor: solicitação **não** tem efeito nenhum enquanto pendente.

**Contexto adicional descoberto (2026-08-18, verificado no código e no banco em execução)**:

- O ciclo foi reproduzido ponta a ponta contra `system_automation_test` com as funções de produção: solicitar → aprovar → ler pelo motor. Resultado: solicitação consta `approved` e o motor segue lendo `0.05`. **Confirmado que aprovar não aplica.**
- A **leitura** funciona: gravando direto na tabela, `load_adequation_config` devolve o valor novo na hora, **sem cache e sem reiniciar a aplicação**. O defeito está isolado no elo aprovação→escrita.
- No banco da aplicação existe uma solicitação **aprovada em 2026-07-20** (`id=2`, "Mudança de adequação para 2%") que nunca foi aplicada — `tolerancia_adequacao` segue `0.05` com `updated_at` de 24/06. **Decisão (18/08): não aplicar retroativamente**; o admin técnico aplica à mão o que ainda fizer sentido.
- O menu de Ações do painel **já diferencia o papel no rótulo** ("Editar" para admin(40)+, "Solicitar alteração" para gestor) em `parametros-table.tsx:198`, mas **o handler não diferencia**: os dois chamam `requestParameterUpdate`, que toma 422. O admin lê "Editar" e recebe "Erro ao enviar solicitação."
- **Nenhuma função do frontend chama `PUT` ou `DELETE /parametros/{chave}`** — os endpoints existem, com RBAC correto, e nunca são acionados.
- `require_admin = require_min_role(ADMINISTRADOR)` já cobre administrador **e** admin_técnico (hierarquia `basico < operacional < gestor < administrador < admin_tecnico`). O RBAC de backend para o caminho direto **já está correto**; falta só a tela usá-lo.
- Há 4 parâmetros de teste poluindo o banco de dev desde 2026-07-30 (`rbac-criar-e32fbe9f`, `duplicada-b6b12faf`, `atualizar-dae00b9e`, `listar-cc71f49f`) — limpar faz parte desta fase.

**Success Criteria** (what must be TRUE):

  1. Uma solicitação de edição criada pelo painel chega ao backend com o parâmetro identificado (`target_chave` preenchido) e é listada como pendente — sem 422
  2. Aprovar uma solicitação aplica o valor em `parametros` na **mesma transação** que muda o status; uma falha na aplicação não deixa a solicitação marcada como aprovada
  3. Depois de aprovada uma mudança de `tolerancia_adequacao` para 0.10, a adequação seguinte usa 10% — verificável sem redeploy e sem reiniciar worker
  4. O registro da decisão guarda snapshot de papel de quem pediu e de quem aprovou (`requested_by_role`, `reviewed_by_role`), congelado no momento do ato — uma mudança posterior de papel do usuário não altera o histórico
  5. O registro guarda o valor anterior (`previous_payload`), permitindo reconstruir antes→depois de qualquer parâmetro
  6. `justification` é obrigatória na criação da solicitação — o backend rejeita solicitação sem motivo
  7. Existe um registro no backend das chaves que o motor consome, com tipo e faixa válida; gravar `tolerancia_adequacao` fora de `0 ≤ v ≤ 1` ou `criterio_selecao` fora de `{valor, quantidade}` é rejeitado com 4xx
  8. `GET /api/v1/parametros` devolve, por linha, se aquela chave é consumida pelo motor (base do selo "ativo no motor" na tela)
  9. ~~`PUT /api/v1/parametros/{chave}` fica restrito a admin_técnico como válvula de emergência~~ — **SUBSTITUÍDO em 18/08 pelos critérios 10–12 abaixo**
  10. Administrador(40)+ e admin_técnico editam/excluem pelas Ações do painel e o valor passa a valer **na hora**, sem solicitação — a tela chama de fato `PUT`/`DELETE /api/v1/parametros/{chave}` (hoje nenhuma chama)
  11. Gestor(30) **não** vê "Editar"/"Excluir": vê "Solicitar alteração"/"Solicitar exclusão", e a solicitação **não tem efeito** até um administrador aprovar
  12. As **duas** portas de escrita (aprovação e edição direta) gravam o mesmo registro auditável: quem, papel congelado no ato, valor anterior e quando — nenhuma mudança de `parametros` acontece sem rastro

**Plans:** 5 plans (1/5 executed)
Plans:
**Wave 1** *(concluída)*

- [x] 11-01-PLAN.md — Registro das chaves que o motor honra (`domain/registro.py`) + validação de faixa pura + guarda de drift contra `load_adequation_config` (PARAM-04)

**Wave 2** *(schema — bloqueia todo o resto: 11-03/11-04/11-05 gravam nas colunas novas)*

- [ ] 11-02-PLAN.md — Migration **031** com `requested_by_role`, `reviewed_by_role`, `previous_payload`, `origem` + `OrigemSolicitacao` + ciclo destrutivo provado (PARAM-03, PARAM-05)

**Wave 3** *(caminho de entrada)*

- [ ] 11-03-PLAN.md — `parameter_id` aceito (fim do 422), `target_chave` resolvido, `justification` obrigatória, `max_role` + snapshot do solicitante, selo `consumido_pelo_motor` no `GET` (PARAM-01, PARAM-03, PARAM-04)

**Wave 4** *(núcleo — aprovar aplica)*

- [ ] 11-04-PLAN.md — `revisar_solicitacao`: aplica o valor na MESMA transação, faixa validada no choke point, `reviewed_by_role`/`previous_payload`, autoaprovação permitida, e prova de que o motor usa 10% sem redeploy (PARAM-02, PARAM-03, PARAM-04)

**Wave 5** *(fecha a porta lateral)*

- [ ] 11-05-PLAN.md — `POST`/`PUT`/`DELETE` diretos restritos a admin_técnico, auditados via `origem=emergencia` e validados pela mesma faixa + `docs/parametros.md` (PARAM-04, PARAM-05)

#### Phase 12: Exceções de parâmetro por cliente e bloqueio de recebimento

**Goal**: Um cliente pode operar com tolerância de adequação diferente da global, e um cliente que não pode receber mercadoria (desastre natural, sinistro) para de gerar OR sem travar estoque — ambos com vigência de início e fim, entrando em vigor pelo mesmo ciclo de solicitação/aprovação da Phase 11.

**Depends on**: **Phase 11** — a exceção entra em vigor pela aprovação, e hoje aprovar não aplica nada. Construir antes seria erguer em cima de um elo quebrado.

**Requirements**: PARAM-06, PARAM-07

**Decisões de negócio (Victória, 2026-08-18)**:

- Escopo da exceção é **por cliente** (`pedidos.client`). Cidade foi citada só como exemplo e **não é viável hoje** — ver restrição abaixo.
- **Bloqueio de recebimento não gera OR nenhuma.** O pedido vai para stand-by com motivo explícito, no mesmo molde de `sem_credito_nrs`, e o **estoque não é reservado** — a peça sobra para quem pode receber. Tolerância `0` foi rejeitada como alternativa justamente porque ainda geraria OR e travaria estoque.
- **Vigência com início e fim** nos dois recursos: a exceção expira sozinha e a regra global volta a valer, sem ninguém precisar lembrar de desfazer.
- **Só `tolerancia_adequacao` aceita exceção por cliente.** Pedir exceção para `criterio_selecao` deve ser recusado com mensagem clara, nunca aceito em silêncio.
- Bloqueio vale **daqui para frente**: não cancela OR já emitida. Desfazer reserva já enviada ao ERP é outro problema, com contrato externo.

**Restrições descobertas (2026-08-18, verificado no dado real)**:

- **Cidade não existe no modelo.** `pedidos` tem `client` e `canal`, e o leitor do Databricks (`databricks_reader.py:25`) nem seleciona cidade/UF. Escopo por cidade exige antes confirmar se a view `system_automation_pedidos_em_aberto` expõe o campo e trazê-lo na ingestão. Deduzir cidade do nome do cliente é chute ("FASHION TRADE", "GFG COMERCIO FILIAL" não têm cidade no nome) e não é aceitável numa regra que decide faturamento.
- **Não há código de cliente**, só `nm_cliente` (texto, 2284 valores distintos). Qualidade medida no banco real: 0 nomes com espaço sobrando, 0 grafias divergentes do mesmo cliente, praticamente tudo maiúsculo — **serve como chave hoje**. Risco residual: rename na origem desliga a exceção em silêncio. Mitigação exigida: guardar a contagem de pedidos que casaram no ato da aprovação e alertar quando cair a zero.
- **5.648 pedidos (1,3%) têm `client` nulo** e nunca casarão com exceção — caem sempre na regra global. Aceito, mas tem que estar visível.
- **O motor já está pronto para isso**: `adequar_grade_produto` recebe `tolerancia` por grade e o laço que o chama já itera por pedido (`motor_adequacao.py:406`). O desvio de `sem_credito_nrs` faz `continue` **antes** da chamada, então não consome `estoque_local` — é exatamente o comportamento que o bloqueio precisa. A regra de adequação **não muda**; muda só quem a alimenta.

**Success Criteria** (what must be TRUE):

  1. Uma exceção de `tolerancia_adequacao` aprovada para o cliente X faz a adequação seguinte usar a % da exceção **só** para os pedidos de X; todos os outros seguem a global
  2. Um bloqueio de recebimento aprovado para o cliente X põe os pedidos de X em stand-by com motivo visível na tela e **não gera OR**; o estoque que sobra é alocado para os demais clientes do mesmo produto
  3. Passada a data de fim, a exceção deixa de valer na execução seguinte **sem redeploy, sem reiniciar worker e sem ação manual**
  4. Tentar criar exceção para uma chave diferente de `tolerancia_adequacao` é rejeitado com 4xx e mensagem explícita
  5. Exceção e bloqueio nascem como solicitação e **não têm efeito** enquanto pendentes, igual a qualquer parâmetro (Phase 11)
  6. Uma exceção cujo cliente deixou de existir na origem é sinalizada (contagem de pedidos casados = 0), em vez de silenciosamente parar de valer
  7. Bloqueio criado depois de uma OR já emitida **não** cancela essa OR

**Plans:** não planejados

### Progress (v1.2)

**Suggested waves:** 4∥5∥7 → 6 → 8∥9

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 4. CI quality gates | 0/— (sem plano formal) | Requirements 2/3 done fora de fase | — |
| 5. Auth/OTP/seed/JWT | 5/5 | Complete   | 2026-08-13 |
| 6. Worker+beat+migrations | 0/— (sem plano formal) | Requirements 3/3 done fora de fase | 2026-08-12 (verificado) |
| 7. Docker prod hygiene | 0/— (sem plano formal) | Requirements 1/2 done fora de fase | — |
| 8. Contratos + docs | 0/— (sem plano formal) | Requirements 1/3 done fora de fase | — |
| 9. Observability | 0/— | Planning | — |
| 11. Parâmetros: fluxo + auditoria | 1/5 | In Progress (planejada 2026-08-17; waves 2→5 sequenciais) | — |

*Reverificado direto no código em 2026-08-12 — "done fora de fase" significa que o requirement já está satisfeito no código atual, mas sem `*-PLAN.md`/`*-SUMMARY.md` de GSD por trás (chegou por commits regulares, não pelo fluxo discuss→plan→execute). Ver `.planning/REQUIREMENTS.md` § v1.2 para o detalhe por requirement.*

### Phase 20: Trava de orçamento de ±5% na edição manual de pedidos Sem Adequação

**Goal:** Ligar o ledger de orçamento ±5% (`OrcamentoPedido`) à edição manual de grade de pedidos "Sem Adequação" (`ModoAdequacao.SEM_ADEQUAR`) — hoje essa edição nunca consulta o orçamento e o domínio bloqueia qualquer mudança de total. Corrige antes 2 bugs pré-existentes no ledger que quebrariam a trava silenciosamente (filtro `WHERE op.tipo = 'com'` na SQL de orçamento escondendo consumo "sem adequação"; reset de `qt_solicitada` a cada edição apagando consumo registrado). Escopo desta fase é só backend — o indicador visual e a trava no modal (`product-grade-detail-modal.tsx`, requirement UI-02) ficam para uma quick-task separada no `frontend`, depois que Waves 2 e 3 estiverem executadas.
**Requirements**: GRADE-03
**Depends on:** Phase 19
**Success Criteria** (what must be TRUE):

  1. O orçamento de um pedido reflete corretamente o consumo de ORs "sem adequação" (bug do filtro `tipo='com'` corrigido), e uma segunda edição sucessiva não apaga o consumo registrado pela primeira (bug do reset de `qt_solicitada` corrigido) — "Com Adequação" permanece byte-idêntico.
  2. Editar a grade de uma OR "sem adequação" pode mudar o total do par; "Com Adequação" continua recusando qualquer mudança de total exatamente como hoje.
  3. Salvar uma edição "sem adequação" que ultrapasse o orçamento de adição ou de corte do pedido é recusado com 409 e nenhuma escrita acontece — inclusive quando o lote mistura um pedido dentro e outro fora do limite.
  4. Abrir a edição de um produto devolve o orçamento agregado do **pedido inteiro** (todos os produtos, não só o aberto) para clientes "sem adequação"; linhas "com adequação" continuam com payload idêntico ao de antes desta fase.
  5. Salvar uma edição "sem adequação" dentro do limite também aprova e finaliza a OR no mesmo clique, escopado só aos pares efetivamente editados — nenhuma OR "Com Adequação", nem de outro pedido/produto, é aprovada de carona.

**Plans**: 5/5 plans complete — **fase concluída em 2026-09-08**, verificada `passed` (5/5 must-haves; ver `20-VERIFICATION.md`). Code review achou 1 crítico (CR-01, corrigido) + 2 avisos (corrigidos) — ver `20-REVIEW.md`/`20-REVIEW-FIX.md`. Frontend (D-12–D-16, requirement UI-02) segue como quick-task pendente no `frontend`.

Plans:
**Wave 1**

- [x] 20-01-PLAN.md — Corrige `_PEDIDO_BUDGET_SQL` (filtro de tipo escondia consumo "sem adequação"); `orcamento_edicao.py` (domínio puro) + `repositorio_orcamento_pedido.py`
- [x] 20-02-PLAN.md — `edicao_grade.py`/`rateio.py`: permite mudança de total só para `tipo='sem'`, corrigindo o reset de `qt_solicitada` que apagava consumo entre edições sucessivas

**Wave 2** _(depende da Wave 1)_

- [x] 20-03-PLAN.md — Valida o orçamento dentro do lock existente em `executar_alteracao_grades_produto`, devolvendo 409 se estourar adição ou corte (depende de 20-01, 20-02)
- [x] 20-04-PLAN.md — Expõe o orçamento agregado do pedido inteiro na projeção de leitura que o modal de edição consome (depende de 20-01)

**Wave 3** _(depende da Wave 2)_

- [x] 20-05-PLAN.md — Funde salvar + aprovar num único clique para "sem adequação" dentro do limite, escopado aos pares editados e ao produto — nunca ao escopo produto/canal de `aprovar_produto_canal` (D-09/D-10/D-11) (depende de 20-03, 20-04)

Cross-cutting constraints (aparecem em 2+ planos): "Com Adequação" permanece byte-idêntico em comportamento e payload em todos os 5 planos — cada um tem teste de regressão dedicado provando isso.

### Phase 21: Avisos de integração crítica (banco de dados e view de estoque) na aba Alertas

**Goal:** [To be planned]
**Requirements**: TBD
**Depends on:** Phase 20
**Plans:** 0 plans

Plans:

- [ ] TBD (run /gsd-plan-phase 21 to break down)

---

## Milestone v1.3 — Motor de alocação de OR fiel às regras de negócio

### Overview

O motor de adequação passa a reservar exatamente o que as regras de negócio ditadas pela mantenedora em 2026-08-14 mandam: crédito bloqueia sempre, o modo sem adequação é tudo-ou-nada por par com prioridade por valor e estoque compartilhado, furo de grade trava o par nos dois modos, os orçamentos ±5% de adição/corte são separados e medidos sobre o pedido completo do cliente (persistindo entre execuções), e as peças extras entram onde há mais sobra de estoque. Quem fica de fora passa a ser visível e rotulado por motivo na aba de pedidos em aberto.

A ordem de construção segue a dependência real apurada em `research/v1.3/ARCHITECTURE.md` e `PITFALLS.md`: a guarda contra OR zerada é isolada e paralelizável; o motor puro (domínio, sem banco) é a base de tudo que vem depois; só então o modo sem adequação pode ser roteado pelo caminho global (removendo o streaming órfão); a persistência do motivo de stand by depende dos dois anteriores; a visibilidade na UI depende da persistência; peças extras paralelizam com a UI (depende só do motor puro); e a validação final (testes de propriedade + shadow run) fecha o milestone como gate de saída.

**Fonte:** `.planning/PROJECT.md` § Regras de negócio da alocação · `.planning/REQUIREMENTS.md` § v1.3 · `.planning/research/v1.3/SUMMARY.md`, `ARCHITECTURE.md`, `PITFALLS.md` (2026-08-14).

### Phase Numbering (v1.3)

Numeração continua de onde os outros milestones pararam: **Phases 13–19**. Não reutiliza 1–3 (v1.1, concluído), 4–9 (v1.2, pausado e retomável) nem 10–12 (v1.3-Aurora, cancelada, reservada só para rastreio).

### Phases (v1.3)

- [ ] **Phase 13: Guarda contra OR zerada** - Nenhuma OR de zero peças é gerada, mesmo em pedidos pequenos com estoque zerado (fecha WR-05)
- [x] **Phase 14: Motor de alocação puro** - Crédito, tudo-ou-nada, furo de grade, orçamento ±5% separado por pedido completo e prioridade determinística, testável sem banco
- [x] **Phase 15: Roteamento global do modo sem adequação** - Sem adequação passa a decidir com visão completa do canal; streaming órfão removido; revalidação de estoque cobre também o tipo "sem"
- [x] **Phase 16: Persistência do motivo de stand by** - Motivo de cada par fora da OR é gravado (crédito, estoque, furo de grade) sem inflar o resultado do job
- [ ] **Phase 17: Visibilidade do stand by na UI** - Tags de motivo e filtro na aba de pedidos em aberto, seguindo o design-system
- [ ] **Phase 18: Peças extras nos tamanhos com mais sobra** - Extras alocados por sobra de estoque, sem posição fixa, com soma exata
- [ ] **Phase 19: Validação final e shadow run** - Invariantes provadas por teste de propriedade; diff categorizado contra o motor antigo antes de substituir em produção

### Phase Details (v1.3)

#### Phase 13: Guarda contra OR zerada

**Goal**: Nenhuma OR com soma de quantidade líquida igual a zero é persistida pelo processamento, mesmo em pedidos pequenos (grade total < 20 peças) onde a fórmula de tolerância atual (`ceil`) falha.
**Depends on**: Nothing — correção isolada, paralelizável com a Phase 14
**Requirements**: ALOC-05
**Success Criteria** (what must be TRUE):

  1. Processar um pedido pequeno com estoque zerado no tamanho pedido deixa o par em stand by, em vez de gerar uma OR com zero peças.
  2. A checagem do limite de tolerância usa `floor` em vez de `ceil`, e existe uma guarda explícita e independente que nunca persiste um item com status "Gerar OR" e soma de quantidade líquida igual a zero — mesmo que a fórmula de tolerância mude de novo no futuro.
  3. Um teste automatizado cobrindo pedidos de 1 a 19 unidades, com estoque zero ou parcial, comprova que nenhuma OR de zero peça chega a ser gravada.

**Plans**: TBD (`/gsd-plan-phase 13`)

#### Phase 14: Motor de alocação puro (regras de negócio)

**Goal**: O motor de adequação decide corretamente crédito, tudo-ou-nada por par, furo de grade, orçamento ±5% separado sobre o pedido completo (persistente entre execuções, com mínimo viável garantido antes de extras) e prioridade determinística — inteiramente em `domain/`, testável sem banco e sem tocar a orquestração.
**Depends on**: Nothing estrutural (paralelizável com a Phase 13) — mas é a base de que todas as fases seguintes dependem
**Requirements**: ALOC-01, ALOC-02, ALOC-03, ALOC-04, ALOC-06, ALOC-07, ALOC-08, ALOC-09, ALOC-10, FIX-02
**Success Criteria** (what must be TRUE):

  1. Um pedido de cliente sem crédito nunca tem nenhum produto reservado, em nenhum dos dois modos de processamento.
  2. No modo sem adequação, um par cliente×produto só é atendido com a grade exatamente pedida; quando não há estoque para a grade completa, o par inteiro fica em stand by — nunca uma quantidade parcial.
  3. Quando dois clientes disputam o mesmo produto, quem tem o pedido de maior valor total recebe a peça primeiro, e rodar o mesmo cenário várias vezes produz sempre o mesmo plano (mesmo hash) — desempates nunca dependem de ordem de iteração de dict/set.
  4. Um par cujo tamanho do meio da grade pedida chega a zero fica em stand by nos dois modos, mesmo com estoque sobrando nos demais tamanhos.
  5. No modo com adequação, cada produto do pedido recebe ao menos a quantidade mínima viável antes de qualquer peça extra ser distribuída; a soma de adições e a soma de cortes ao longo de execuções sucessivas do mesmo pedido nunca ultrapassam, cada uma isoladamente, 5% da quantidade total do pedido completo original (o orçamento não reseta a cada rodada); e o valor financeiro recalculado quando a quantidade muda fecha exatamente com o total, sem drift de centavos.

**Plans**: 7 plans (14-01 a 14-06 concluídos; 14-07 pendente)

#### Phase 15: Roteamento global do modo sem adequação

**Goal**: O modo sem adequação passa a ser planejado pelo mesmo caminho global que já serve o modo com adequação (prioridade e estoque compartilhado exigem visão do canal inteiro), o código de streaming que fica órfão é removido na mesma entrega, e a revalidação de capacidade de estoque no momento de aplicar o plano volta a cobrir também o tipo "sem".
**Depends on**: Phase 14 (o motor precisa saber decidir tudo-ou-nada antes de ser roteado pelo caminho global)
**Requirements**: ARCH-01, FIX-01
**Success Criteria** (what must be TRUE):

  1. Processar um canal inteiro no modo sem adequação usa a mesma pipeline que hoje só atende o modo com adequação — nenhuma decisão de prioridade ou estoque compartilhado depende mais de em qual página (250 pares) um par caiu.
  2. O construtor de plano por streaming (e tudo que só existia para alimentá-lo — leitura paginada, testes dedicados) não existe mais no repositório depois da mudança.
  3. Aplicar um plano do modo sem adequação que exceda o estoque disponível no momento da escrita é rejeitado, do mesmo jeito que já acontece hoje no modo com adequação.
  4. O pico de memória medido ao processar um canal inteiro no modo sem adequação permanece dentro do orçamento já reservado para o worker dedicado.
  5. O orçamento ±5% passa a usar dados reais: `processar_pedidos` é chamado com o total original de cada pedido e o consumido em ORs anteriores, lidos do banco — nunca com o placeholder de execução única da Phase 14. Verificação: processar o mesmo pedido em duas execuções sucessivas não concede 5% novos na segunda, e o log de aviso `ALOC-09` da Phase 14 deixa de ser emitido.

**Plans**: 5 plans (15-01, 15-02, 15-03, 15-04, 15-05 concluídos — fase completa)

Plans:
**Wave 1**

- [x] 15-01-PLAN.md — Leitura de orçamento por pedido (aditivo, invariante J5): `load_pedido_budget` no port + implementação SQL agregada, 3 testes de integração

**Wave 2**

- [x] 15-02-PLAN.md — FIX-01: `_assert_stock_capacity` revalida capacidade de estoque também para `tipo="sem"`, com teste novo

**Wave 3**

- [x] 15-03-PLAN.md — `SEM_ADEQUAR` roteado pelo caminho global (`build_processing_plan`→`processar_pedidos`), orçamento real ligado nos dois modos (ALOC-09 fechado, provado por comparação discriminante contra o placeholder) e streaming removido do repositório (J7)

**Wave 4**

- [x] 15-04-PLAN.md — Medição do pico de memória de `sem_adequar` roteado pelo caminho global: primeira instrumentação automatizada (`resource.getrusage`) do repositório, dataset sintético de 45.000 pares/112.500 itens; pico real observado `delta_total_mib=630.9 MiB`, dentro do teto de calibração de 700.0 MiB (margem ~9.9%)

**Wave 5**

- [x] 15-05-PLAN.md — `docs/adequacao.md` atualizado para o estado real pós-Fase 14/15-03: ledger `OrcamentoPedido`/duas passadas em vez de tolerância `ceil`/`floor` por produto isolado, caminho global único sem streaming, `deferredCount`/`blockedCreditCount` reais nos dois modos desde ARCH-01. Executado sem esperar `15-04` (dependência formal só de `15-03`).

> **Herança da Phase 14 (não perder de vista):** a Phase 14 deixou `total_original_por_pedido` e `consumido_previo_*_por_pedido` como parâmetros **opcionais** em `processar_pedidos`, com fallback para um placeholder de execução única que **não** respeita o orçamento acumulado entre execuções. O fallback é ruidoso (emite `logger.warning` citando ALOC-09), mas só a Phase 15 fecha o buraco de verdade. Enquanto o critério 5 acima não passar, **ALOC-09 não está entregue**, ainda que a Phase 14 esteja fechada.

#### Phase 16: Persistência do motivo de stand by

**Goal**: Todo par que fica de fora de uma OR (por crédito, estoque insuficiente ou furo de grade) tem o motivo registrado, sem inflar o retorno do job de processamento, e o sistema acumula quantas execuções seguidas cada par permanece parado.
**Depends on**: Phase 14, Phase 15 (só depois dos dois modos produzirem `preteridos`/`bloqueados_credito` de verdade há o que persistir)
**Requirements**: STANDBY-01, STANDBY-06
**Success Criteria** (what must be TRUE):

  1. Depois de um processamento, é possível consultar o motivo de qualquer par que não recebeu OR, sem que o retorno do job (ainda limitado a contadores) tenha crescido.
  2. Um par que volta a ficar em stand by em execuções consecutivas tem seu registro atualizado (não duplicado), e a contagem de execuções consecutivas aumenta a cada rodada em que ele continua parado.
  3. Assim que um par finalmente recebe uma OR, seu registro de motivo de stand by some da consulta — nenhum par atendido continua marcado como pendente.
  4. Um full refresh de ingestão limpa os motivos de pares que deixaram de ser pendentes por qualquer outro caminho (ex. confirmado no ERP).

**Plans**: 5 plans (16-01, 16-02, 16-03, 16-04, 16-05 concluídos — fase completa)

Plans:
**Wave 1**

- [x] 16-01-PLAN.md — Vocabulário canônico de motivo (`standby_motivo.py`: `SEM_CREDITO`/`SEM_ESTOQUE`/`FURO_GRADE`/`MOTIVOS_VALIDOS`) + `preteridos_motivo` como 6ª chave aditiva do retorno de `processar_pedidos`, distinguindo furo de grade de falta de estoque genérica nos dois modos (K1 de `16-VALIDATION.md`)

**Wave 2**

- [x] 16-02-PLAN.md — Migration `032_pedido_standby_motivo` — `CHECK` com os 3 motivos desde a criação, model ORM `PedidoStandbyMotivo` e teste de round-trip upgrade/CHECK/downgrade/re-upgrade (retomado após queda de conexão do executor anterior; Tasks 1-2 herdadas, Task 3 desta sessão)

**Wave 3**

- [x] 16-03-PLAN.md — `PlanDraft` ganha `deferred_pairs`/`blocked_credit_pairs` + guarda de overlap (depende de 16-01); `build_processing_plan` popula os dois campos (motivo por par + fan-out de crédito por produto elegível) e `plan_hash` (schemaVersion 2) passa a cobri-los

**Wave 4**

- [x] 16-04-PLAN.md — Port `record_standby_reasons` + upsert com incremento (chunkado) + wiring transacional + limpeza em `apply_pairs` (depende de 16-02 e 16-03) — atômico com `store_plan`, provado por teste de falha injetada (rollback total)

**Wave 5**

- [x] 16-05-PLAN.md — Limpeza de linhas órfãs de `pedido_standby_motivo` no full refresh da ingestão (depende de 16-02): `_STANDBY_ORPHAN_CLEANUP_SQL` (`DELETE ... NOT EXISTS`) executado dentro de `reconstruir_pedido_produto_read`, estritamente depois dos dois INSERTs pending/erp; teste dedicado prova par órfão apagado e par pendente sobrevivendo ao mesmo full refresh

#### Phase 17: Visibilidade do stand by na UI

**Goal**: O analista enxerga, na aba de pedidos em aberto, por que cada cliente não recebeu OR, e consegue filtrar por esse motivo — seguindo à risca o design-system do frontend.
**Depends on**: Phase 16 (a tabela de motivo precisa existir e estar populada; ler antes disso só mostraria vazio)
**Requirements**: STANDBY-02, STANDBY-03, STANDBY-04, STANDBY-05
**Success Criteria** (what must be TRUE):

  1. Um cliente bloqueado por falta de crédito aparece com a tag "sem crédito" em todos os produtos que ele tentou reservar naquele canal, na aba de pedidos em aberto.
  2. Um cliente sem estoque suficiente aparece com a tag "aguardando estoque" apenas nos produtos em que realmente faltou peça para ele — os demais produtos do mesmo pedido não recebem a tag.
  3. Um par bloqueado por furo de grade recebe um rótulo próprio, visualmente distinto de "aguardando estoque" e de "sem crédito".
  4. O analista pode filtrar a aba de pedidos em aberto por motivo de stand by, seguindo o mesmo padrão de filtro que o histórico já usa.
  5. Nenhuma alteração de componente de front-end nesta fase foi feita sem antes consultar `frontend/design-system/`; qualquer componente necessário que não exista lá foi levantado como decisão com a mantenedora, nunca improvisado no app.

**Plans**: TBD (`/gsd-plan-phase 17`)
**UI hint**: yes

#### Phase 18: Peças extras nos tamanhos com mais sobra

**Goal**: Quando o motor decide adicionar peças além do pedido, elas entram nos tamanhos com mais sobra de estoque no momento da decisão, nunca em posição fixa, com a soma batendo exatamente com o orçamento de adição.
**Depends on**: Phase 14 (usa o orçamento de adição já calculado pelo motor puro) — paralelizável com as Phases 15–17
**Requirements**: ALOC-11
**Success Criteria** (what must be TRUE):

  1. Ao adicionar peças extras a um pedido, elas aparecem nos tamanhos que têm mais sobra de estoque no momento da decisão — não sempre nos tamanhos extremos da grade.
  2. A soma das peças extras distribuídas bate exatamente com o orçamento de adição calculado para aquele pedido, sem sobra nem falta por arredondamento.
  3. Rodar duas vezes o mesmo cenário de estoque produz a mesma distribuição de extras — determinístico, sem depender de ordem de iteração de estruturas internas.

**Plans**: TBD (`/gsd-plan-phase 18`)

#### Phase 19: Validação final e shadow run

**Goal**: As invariantes centrais do motor estão provadas por teste de propriedade (não só por inspeção), e o motor novo foi comparado ao antigo sobre dados reais antes de substituir em produção.
**Depends on**: Phases 13, 14, 15, 16, 17, 18 (gate de saída — precisa de todas as regras já implementadas para comparar de verdade)
**Requirements**: TEST-01
**Success Criteria** (what must be TRUE):

  1. Um teste baseado em propriedade (`hypothesis`, dependência só de desenvolvimento) gera dezenas de cenários e comprova que o motor nunca reserva acima do estoque disponível, nunca estoura o orçamento ±5% somando execuções sucessivas, nunca seleciona um par com furo de grade e nunca seleciona um par com alocação final zero.
  2. Rodar o motor novo em modo plan-only sobre um snapshot real de produção produz um diff comparado ao motor antigo, separando mudanças esperadas (explicadas por uma das regras novas) de mudanças inesperadas.
  3. Nenhuma diferença da categoria "inesperada" fica sem explicação antes do motor novo substituir o antigo em produção.

**Plans**: TBD (`/gsd-plan-phase 19`)

### Progress (v1.3)

**Execution Order:**
Phases 13 e 14 podem paralelizar (guarda isolada + motor puro). 15 depende de 14. 16 depende de 14+15. 17 depende de 16. 18 paralelizável com 15–17 (depende só de 14). 19 fecha o milestone, depende de todas.

Ordem sugerida: 13∥14 → 15 → 16 → 17 (∥ 18, que só precisa de 14) → 19

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 13. Guarda contra OR zerada | 0/— | Not started | - |
| 14. Motor de alocação puro | 7/7 | Complete | 2026-08-17 |
| 15. Roteamento global sem adequação | 4/5 | In progress | - |
| 16. Persistência do motivo de stand by | 2/5 | In progress | - |
| 17. Visibilidade do stand by na UI | 0/— | Not started | - |
| 18. Peças extras nos tamanhos com mais sobra | 0/— | Not started | - |
| 19. Validação final e shadow run | 0/— | Not started | - |
