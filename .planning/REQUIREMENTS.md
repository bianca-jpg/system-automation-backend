# Requirements: automation OR — Milestone v1.1 Saída de OR no formato Linx

**Defined:** 2026-08-04
**Core Value:** Transformar pedidos em aberto em Ordens de Reserva corretas e rastreáveis — com a grade certa por cliente e respeitando o estoque do canal — prontas para o ERP Linx consumir.

## v1.1 Requirements

Requirements deste milestone. Cada um mapeia para uma fase do roadmap.

### Ingestão

- [x] **ING-01**: O sync de 2h ingere a referência tamanho→posição do Databricks (`programa_estagio.refined.system_automation_prod_tamanho_ref`) como 5ª fonte, em full refresh, na tabela `produto_tamanho_posicao`

### Saída Linx

- [x] **LINX-01**: Existe a tabela `ordens_reserva_linx` no PostgreSQL com o layout aceito pelo Linx (colunas do CSV real `Tabelas_de_OR.csv`); campos sem fonte conhecida ficam vazios
- [x] **LINX-02**: Cada geração (com ou sem adequação) grava/atualiza 1 linha por cliente×produto×cor com `nome_clifor`, `pedido` (nº do pedido = nº da OR), `produto`, `cor_produto`, `preco1`, `valor_embalado` e `qtde_embalada` — na mesma transação da geração *(closed 2026-08-07 audit — código + 03-03-SUMMARY)*
- [x] **LINX-03**: A grade interna (tamanho→quantidade) é convertida para as colunas posicionais `e1..e48` usando a referência de posição; tamanho sem posição na referência gera aviso no log
- [x] **LINX-04**: A tabela Linx começa vazia (sem backfill de reservas antigas)

## Future Requirements

Adiados. Rastreados mas fora do roadmap atual.

### Integração Linx

- **INTG-01**: Load das linhas de `ordens_reserva_linx` para o ERP Linx via barramento (aguarda acesso da equipe de TI)
- **INTG-02**: Status de envio (pendente/enviado/erro) e reconciliação de retorno do barramento
- **INTG-03**: Descobrir e preencher os campos hoje sem fonte (FILIAL, ROMANEIO, CAIXA, ENTREGA, REPRESENTANTE, PACKS, ITEM, etc.)

### Edição manual de grade

**Atualizado 2026-08-12** — o desenho descrito abaixo (descoberto no discuss da Fase 3, 2026-08-05) foi **substituído**: o fluxo de edição foi refeito e hoje só existe a versão pós-geração, com efeito imediato. Verificado no código (`app/modules/pedidos/application/casos_uso.py::executar_alteracao_grades_produto`, rota `PUT /api/v1/pedidos/produtos/grades`):

- Não é possível editar a grade **antes** da OR existir — a consulta que embasa a edição (`carregar_ordens_produto_para_update`) lê de `ordens_reserva`; sem OR, a chamada falha com "cliente não encontrado". A tela também só oferece "Editar grade por cliente" na etapa pós-geração (nunca em "Aguardando"). Por isso **GRADE-01/02 não se aplicam** ao desenho atual — não existe caminho onde a grade original do Databricks "vence" uma edição, porque a edição só é possível depois que a OR (com a grade final) já existe.
- **GRADE-04: implementado.** Editar a grade de uma OR já gerada atualiza `ordens_reserva`, `pedido_modificacoes` e a linha `ordens_reserva_linx` **na mesma transação** (`atualizar_grades_em_lote` + `upsert_modificacoes_em_lote` + `salvar_linhas_linx`, seguido de `db.commit()`).
- **GRADE-03: implementado (backend), 2026-09-08 — Fase 20.** A checagem explícita de tolerância ±5% que faltava (texto original desta linha, escrito antes da Fase 20) foi fechada: `executar_alteracao_grades_produto` agora valida o orçamento ±5% do pedido (`OrcamentoPedido`) antes de gravar, recusando com 409 quando adição ou corte estourariam o limite. Continua recusando quando a redistribuição excede o estoque disponível do canal, e a mudança de total só é permitida para OR "sem adequação" (D-07/D-01). Ver `.planning/phases/20-trava-de-or-amento-de-5-na-edi-o-manual-de-pedidos-sem-adequ/`.
- **UI-02** (travas visuais de ±5%/estoque no modal) permanece não verificado — a Fase 20 deliberadamente não tocou o frontend; fica para quick-task separada no `frontend` (indicador de orçamento, aviso antes de confirmar, trava visual — D-12 a D-15 do `20-CONTEXT.md`).

### Frontend

- **UI-01**: Substituir o `255315` hardcoded do modal de grade pela identificação real da OR (nº do pedido), quando o fluxo com o ERP estiver claro
- **UI-02**: Travas visuais no modal de edição de grade (limite de ±5% e bloqueio ao exceder o estoque) — o backend já recusa (409) redistribuição que exceda o estoque; não verificado se o modal impede o envio *antes* de tentar salvar, ou só exibe o erro do backend depois (par de GRADE-03)

## Out of Scope

Explicitamente excluído. Documentado para evitar scope creep.

| Feature | Reason |
|---------|--------|
| Cabeçalho de OR com número sequencial interno (255316+) | Substituído pelo layout real do Linx — o "número da OR" é o próprio nº do pedido |
| Mudanças no frontend neste milestone | Foco é o banco transacional pronto; a tela atual continua funcionando igual |
| Integração efetiva com o barramento/Linx | Sem acesso ao barramento (limitação da equipe de TI) |
| Persistência de pedidos em Stand By | Comportamento atual (reaparecem para nova tentativa) atende |
| ~~Ligar o frontend ao `PUT /alterar-grade`~~ | Obsoleto: rota aposentada (`410 Gone`); o frontend já liga ao fluxo atual, `PUT /api/v1/pedidos/produtos/grades` |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| ING-01 | Phase 2 — Ingestão da referência + conversão de grade | Complete |
| LINX-01 | Phase 1 — Schema Linx e referência de posição | Complete |
| LINX-02 | Phase 3 — Escrita da OR no formato Linx | Complete (2026-08-07 audit sync) |
| LINX-03 | Phase 2 — Ingestão da referência + conversão de grade | Complete |
| LINX-04 | Phase 1 — Schema Linx e referência de posição | Complete |

**Coverage:**

- v1.1 requirements: 5 total
- Mapped to phases: 5
- Unmapped: 0 ✓

---

## v1.2 Requirements — Platform Hardening / Audit Remediation

**Defined:** 2026-08-07  
**Core Value (hardening):** Impedir deploy cego, fechar defaults de auth perigosos, operar worker/beat/migrations no ECS, imagem Docker de prod, contratos/docs honestos e observabilidade mínima.  
**Fonte:** `.planning/research/AUDIT-2026-08-07.md` · `.planning/milestones/v1.2-PLATFORM-HARDENING.md`

Requirements deste milestone. Cada um mapeia para Phases 4–9. **Não substitui** os REQs Linx acima.

**Nota 2026-08-12:** nenhuma Phase 4–9 foi executada via GSD (sem `*-PLAN.md`/`*-SUMMARY.md` — `.planning/ROADMAP.md` ainda mostra "Planning scaffold only"). Mas commits regulares entre o audit (2026-08-07) e agora já fecharam boa parte dos gaps por fora do fluxo formal de fases. Os itens abaixo foram reverificados direto no código; ver evidência em cada um.

### CI / Quality gates

- [ ] **CI-01**: Workflow de CI executa `ruff check` + `ruff format --check` + `pyright` em PR e na branch de deploy — **parcial**: `.github/workflows/pipeline.yml` já roda `ruff check --select E4,E7,E9,F app alembic`; `ruff format --check` e `pyright` não existem em lugar nenhum (nem `pyproject.toml` configura pyright)
- [x] **CI-02**: Workflow de CI executa `pytest` (com serviços necessários) em PR e na branch de deploy — `pipeline.yml` cria banco isolado e roda `pytest app/tests/test_migration_*.py` + `pytest app/tests -q` antes do build da imagem
- [x] **CI-03**: Job de deploy ECS **depende** dos jobs de quality e tests — falha de gate impede push de imagem / update de serviço — **efeito equivalente, arquitetura diferente da descrita**: é um único job `deploy` (não jobs `quality`/`tests` separados com `needs:`), mas os steps de teste/lint rodam em sequência **antes** do `docker build`/push/ECS e nenhum tem `continue-on-error`, então uma falha já aborta o job antes de chegar ao deploy

### Security (Auth / OTP / seed / JWT)

- [x] **SEC-01**: Em ambiente PROD (ou equivalente de deploy real), a aplicação **falha ao iniciar** se `JWT_SECRET` estiver ausente ou igual ao placeholder inseguro — `app/shared/config/settings.py:588-592` faz `raise ValueError` se `ENV=="PROD"` e `jwt_secret` for o placeholder ou tiver menos de 32 caracteres
- [x] **SEC-02**: `SEED_AUTH_ON_STARTUP` tem default seguro (`False`); seed com senhas conhecidas **não** roda em PROD — fechado na Fase 5 plano 01: `settings.py:434-436` alterou o default global para `False` (nenhum ambiente sobe com seed ligado sem opt-in explícito) e `settings.py:594-596` (fail-fast em PROD) permanece intacto; provado por `test_settings_seed.py`. **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: o seed de usuários (`bootstrap/seed.py`) e a setting `seed_auth_on_startup` foram removidos por completo — SSO Microsoft Entra ID é o único caminho de autenticação.
- [x] **SEC-03**: OTP de registro/recuperação em PROD é **entregue por e-mail** (módulo de comunicações/SMTP); código OTP nunca é logado em PROD — fechado na Fase 5 plano 03: `repositorio_otp.py::criar_desafio` chama `otp_email.py::enviar_codigo_otp` (SMTP síncrono) quando `ENV == "PROD"`, com falha de SMTP capturada e logada sem nunca incluir o código. **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: não existe mais cadastro/recuperação por OTP; `repositorio_otp.py` e `otp_email.py` foram removidos.
- [x] **SEC-04**: Documentado/runbook: o que fazer com usuários seed já existentes em ambientes compartilhados (rotação ou remoção) — fechado na Fase 5 plano 01: `docs/seguranca.md` (runbook de detecção/rotação/remoção das 5 contas seed, linkado em `docs/index.md`), provado por `test_docs_seguranca.py`. **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: `docs/seguranca.md` foi reescrito para o runbook SSO-only (detecção/remoção de contas `auth_provider = 'local'`); não existe mais rotação de senha.
- [x] **SEC-05**: Código OTP é armazenado com hash (não plaintext) em `auth_otp_challenges` — fechado na Fase 5 planos 02+03: `AuthOtpChallenge.code` alargado para `String(64)` (migration 030) e `criar_desafio` grava `hash_otp_code(...)`; `verificar_desafio` compara com `verify_otp_code` em Python. **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: a tabela `auth_otp_challenges` foi dropada (migration 035); não existe mais OTP.
- [x] **SEC-06**: `POST /api/auth/sign-out` revoga o refresh token vigente (ou todos os do usuário); refresh token usado após sign-out é rejeitado — fechado na Fase 5 plano 04: `sign_out` grava corte por usuário (`auth:signed_out_since:{user_id}`) no Redis, rota exige `Depends(get_current_user)`, `refresh_token` rejeita (401) qualquer refresh com `iat` anterior ao corte
- [x] **SEC-07**: Rate limit (usuário+IP) nos endpoints de auth que aceitam segredo adivinhável — sign-in (senha), confirmação de OTP (registro/recovery) e, se possível, geração de OTP — fechado na Fase 5 plano 05: `_aplicar_rate_limit` (`enforce_dual_fixed_window`) ligado nos 5 endpoints (`sign-in` fail-open; `register`, `password/recovery`, `register/confirm`, `password/recovery/confirm` fail-closed), provado por `test_auth_rate_limit.py` (429/503/fail-open/chaves opacas). **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: os 5 endpoints protegidos (sign-in, register, register/confirm, password/recovery, password/recovery/confirm) foram removidos; `POST /api/auth/sso/microsoft` recebe um ID token assinado pela Microsoft, não um segredo adivinhável, então não precisa de rate limit dedicado. `test_auth_rate_limit.py` foi apagado.
- [x] **SEC-08**: Lockout de conta por falhas consecutivas em sign-in/confirmação de OTP, independente da janela de 1 min do rate limit (SEC-07) — fechado fora do fluxo formal de fases: `app/shared/infrastructure/account_lockout.py` (Redis, chave HMAC opaca igual ao rate limit) ligado em `sign-in` (limiar 10, bloqueio 15 min, fail-open) e `register/confirm`+`password/recovery/confirm` (namespace `auth_otp_confirm` compartilhado, limiar 5, bloqueio 30 min, fail-closed); sucesso reseta o contador, e confirmar recovery também reseta o lockout de sign-in da mesma conta; provado por `test_auth_rate_limit.py` (423 ao atingir o limiar, 423 com conta já bloqueada sem checar credencial/OTP, reset em sucesso, fail-open/closed). **N/A desde a remoção do login local (quick 260827-fqf, 2026-08-28)**: mesma razão do SEC-07 — sem segredo adivinhável, não há o que travar por tentativa; `account_lockout.py` e as 4 settings `AUTH_LOCKOUT_*` foram removidos.

### Operations (Deploy / Docker)

- [x] **OPS-01**: Pipeline de deploy atualiza (ou cria) serviço ECS do **Celery worker** com a mesma imagem da API — `pipeline.yml` registra task definition e atualiza `ECS_WORKER_SERVICE` (e também `ECS_ORDERS_WORKER_SERVICE`, um terceiro serviço de worker não descrito em nenhum doc de planning) com a imagem da API
- [x] **OPS-02**: Pipeline de deploy atualiza (ou cria) serviço ECS do **Celery beat** com a mesma imagem da API — `pipeline.yml` atualiza `ECS_BEAT_SERVICE` do mesmo jeito
- [x] **OPS-03**: Deploy executa `alembic upgrade head` (task one-off ou step equivalente) **antes** de considerar o release estável — **implementado diferente do desenho**: não é uma task one-off separada; `.docker/Dockerfile` CMD roda `alembic upgrade head && exec uvicorn ...` a cada boot da task de API (o Alembic usa advisory lock para serializar boots concorrentes), e o pipeline configura `health-check-grace-period-seconds 300` para tolerar isso — é exatamente o padrão que `.planning/CLAUDE-EXECUTION-BRIEF.md` lista como "Não fazer" (ver nota nesse arquivo)
- [ ] **OPS-04**: Existe `.dockerignore` adequado (raiz e/ou `.docker/`) excluindo segredos, `.git`, caches e artefatos locais do contexto de build — confirmado ainda ausente (nem na raiz nem em `.docker/`)
- [x] **OPS-05**: Dockerfile usado no ECR faz `uv sync` de produção **sem** `--group dev` — `.docker/Dockerfile` tem `ARG INSTALL_DEV=false` (default) controlando `uv sync --frozen --no-dev` vs `--group dev`; o `docker build` do pipeline não passa `--build-arg INSTALL_DEV=true`, logo a imagem do ECR já é a variante sem dev

### Contracts / Docs

- [x] **CONTRACT-01**: Frontend consome parâmetros de negócio via `GET /api/v1/parametros` nos fluxos cobertos (sem hardcode dos valores geridos pela API) — fechado na quick task 260831-cf1 (repo `frontend`, commit `85d1d25`): `orders-list.tsx` trocou os dois `1.05` hardcoded pelo hook `useToleranciaAdequacaoFator()`, que lê `tolerancia_adequacao` via `GET /api/v1/parametros`. Decisão da mantenedora de corrigir antes da Fase 11 fechar, por não depender do fluxo de escrita (só leitura). `order-detail-modal.tsx` (citado junto no achado original) não existe mais na árvore principal do front — achado já não procede para ele
- [x] **CONTRACT-02**: Documentação (`docs/arquitetura.md`, `docs/api.md`) não afirma existência de módulos/rotas `alertas` ou `history` que não existam no código — reverificado 2026-08-12: `docs/arquitetura.md` lista só os módulos reais (`auth, comunicacoes, core, health, ingestao, parametros, pedidos, realtime`); `docs/api.md` documenta `/api/v1/alertas` como rota real dentro de `pedidos` (existe em código), e `history` é tópico de evento realtime, não módulo. A afirmação do audit de 08-07 sobre isso já não procede contra os docs atuais
- [ ] **CONTRACT-03**: Shape/contrato de parâmetros documentado para integração front (campos mínimos e auth necessária) — não verificado neste ciclo

### Parâmetros — fluxo, aplicação e auditoria (adicionado 2026-08-14)

- [ ] **PARAM-01**: Solicitação de alteração criada pelo painel chega ao backend identificando o parâmetro alvo (`target_chave` preenchido) e é listada como pendente — hoje o payload manda `parameter_id` no nível raiz contra schema `extra="forbid"` e retorna 422, e `target_chave` nunca é enviado
- [ ] **PARAM-02**: Aprovar uma solicitação **aplica** a mudança em `parametros` na mesma transação que muda o status; falha na aplicação não deixa a solicitação marcada como aprovada. Autoaprovação é permitida (decisão de negócio, 2026-08-14) — quem abre pode aprovar a própria
- [ ] **PARAM-03**: O registro da decisão guarda snapshot do papel de quem pediu e de quem aprovou (`requested_by_role`, `reviewed_by_role`) e o valor anterior (`previous_payload`); mudança posterior de papel do usuário não altera o histórico. `justification` passa a ser obrigatória (hoje é texto fixo do frontend)
- [ ] **PARAM-04**: Existe um registro no backend das chaves consumidas pelo motor, com tipo e faixa válida; valor fora da faixa é rejeitado com 4xx (`tolerancia_adequacao` fora de `0 ≤ v ≤ 1`, `criterio_selecao` fora de `{valor, quantidade}`). `GET /api/v1/parametros` expõe, por linha, se a chave é consumida pelo motor
- [ ] **PARAM-05**: ~~`PUT /api/v1/parametros/{chave}` restrito a admin_técnico como válvula de emergência~~ — **REDEFINIDO em 2026-08-18**: `PUT`/`DELETE` viram o caminho **normal** de administrador(40)+ e admin_técnico, acionado pelas Ações do painel com efeito imediato (hoje nenhuma tela os chama). Gestor(30) continua só solicitando. As **duas** portas de escrita — aprovação e edição direta — gravam o mesmo registro auditável (quem, papel congelado, valor anterior, quando)
- [ ] **PARAM-06**: Um cliente pode ter tolerância de adequação diferente da global, com vigência de início e fim; expirada a vigência a regra global volta a valer na execução seguinte, sem redeploy nem ação manual. Só `tolerancia_adequacao` aceita exceção — outras chaves são rejeitadas com 4xx explícito
- [ ] **PARAM-07**: Um cliente bloqueado para recebimento (desastre natural, sinistro) tem seus pedidos postos em stand-by com motivo visível e **não gera OR**; o estoque não é reservado e sobra para os demais clientes do produto. Vigência com início e fim; o bloqueio não cancela OR já emitida

### Observability

- [x] **OBS-01**: Integração Sentry (ou APM aprovado) via DSN em env; ausência de DSN não derruba a app em non-prod — fechado na quick task 260831-sen (commit `abb46ba`): `sentry-sdk[fastapi]`, `Settings.sentry_dsn`/`sentry_traces_sample_rate`, `setup_sentry()` chamado antes da criação do `FastAPI()`; DSN vazio (default) mantém o SDK desabilitado sem erro em nenhum ambiente
- [x] **OBS-02**: Endpoint `/metrics` não é anonimamente público na internet (auth, rede privada ou equivalente) — fechado fora do fluxo formal (quick task 260827-emo, 2026-08-27): guard por API key de header (`X-Metrics-Key`), fail-closed sem chave configurada, fail-fast de boot em PROD
- [x] **OBS-03**: `CORS_ORIGINS` em PROD reflete apenas origens reais do front; configuração só-localhost em PROD é rejeitada ou alerta de forma inequívoca — fechado na quick task 260831-kic (commit `59cddc2`): `Settings.validate_cross_field_constraints` falha o boot em PROD se `CORS_ORIGINS` estiver vazio ou só localhost/127.0.0.1/::1. Mesma quick task também trocou `allow_methods`/`allow_headers` do CORSMiddleware de `["*"]` para listas explícitas e fechou `/docs`/`/redoc`/`/openapi.json` em PROD (achado correlato, sem requirement próprio no v1.2 original)

### Traceability v1.2

| Requirement | Phase | Status |
|-------------|-------|--------|
| CI-01 | Phase 4 — CI quality gates | Partial (ruff sim, pyright/format --check não) |
| CI-02 | Phase 4 — CI quality gates | Done (fora do fluxo formal de fases) |
| CI-03 | Phase 4 — CI quality gates | Done, efeito equivalente (sem jobs `needs:` separados) |
| SEC-01 | Phase 5 — Auth/OTP/seed/JWT | Done (fora do fluxo formal de fases) |
| SEC-02 | Phase 5 — Auth/OTP/seed/JWT | Done (plano 05-01) |
| SEC-03 | Phase 5 — Auth/OTP/seed/JWT | Done (plano 05-03) |
| SEC-04 | Phase 5 — Auth/OTP/seed/JWT | Done (plano 05-01) |
| SEC-05 | Phase 5 — Auth/OTP/seed/JWT | Done (planos 05-02+05-03; adicionado 2026-08-12) |
| SEC-06 | Phase 5 — Auth/OTP/seed/JWT | Done (plano 05-04) |
| SEC-07 | Phase 5 — Auth/OTP/seed/JWT | Done (plano 05-05) |
| SEC-08 | Phase 5 — Auth/OTP/seed/JWT | Done (fora do fluxo formal de fases) |
| OPS-01 | Phase 6 — Deploy worker+beat+migrations | Done (fora do fluxo formal de fases) |
| OPS-02 | Phase 6 — Deploy worker+beat+migrations | Done (fora do fluxo formal de fases) |
| OPS-03 | Phase 6 — Deploy worker+beat+migrations | Done, desenho diferente do planejado (migrate no boot, não one-off) |
| OPS-04 | Phase 7 — Docker prod hygiene | Pending |
| OPS-05 | Phase 7 — Docker prod hygiene | Done (fora do fluxo formal de fases) |
| CONTRACT-01 | Phase 8 — Contratos + docs | Done (quick task 260831-cf1, repo frontend, commit `85d1d25`) |
| CONTRACT-02 | Phase 8 — Contratos + docs | Done — docs já corretos hoje |
| CONTRACT-03 | Phase 8 — Contratos + docs | Pending (não verificado) |
| PARAM-01 | Phase 11 — Parâmetros: fluxo + auditoria | Pending (adicionado 2026-08-14) |
| PARAM-02 | Phase 11 — Parâmetros: fluxo + auditoria | Pending (adicionado 2026-08-14) |
| PARAM-03 | Phase 11 — Parâmetros: fluxo + auditoria | Pending (adicionado 2026-08-14) |
| PARAM-04 | Phase 11 — Parâmetros: fluxo + auditoria | Pending (adicionado 2026-08-14) |
| PARAM-05 | Phase 11 — Parâmetros: fluxo + auditoria | Pending (adicionado 2026-08-14; redefinido 2026-08-18) |
| PARAM-06 | Phase 12 — Exceções por cliente | Pending (adicionado 2026-08-18) |
| PARAM-07 | Phase 12 — Exceções por cliente | Pending (adicionado 2026-08-18) |
| OBS-01 | Phase 9 — Observability | Done (quick task 260831-sen, commit `abb46ba`) |
| OBS-02 | Phase 9 — Observability | Done (fora do fluxo formal de fases, quick task 260827-emo) |
| OBS-03 | Phase 9 — Observability | Done (quick task 260831-kic, commit `59cddc2`) |

**Coverage v1.2:**

- requirements: 21 total (18 originais + SEC-05/06/07 adicionados 2026-08-12 a partir de achados de auditoria de segurança)
- Mapped to phases 4–9: 21
- Unmapped: 0 ✓
- Status real (atualizado 2026-08-31 após quick task 260831-cf1): 18 Done, 1 Partial, 2 Pending — contagem recalculada diretamente da tabela acima (adiciona OBS-02/OBS-03 aos 14 Done já registrados em 2026-08-13). **Fase 5 (Auth/OTP/seed/JWT) 100% completa: os 7 requisitos SEC-01..07 estão todos Done.**

---

## v1.3-CANCELADA Requirements — Migração completa de dados para Aurora

> ⚠️ **Milestone cancelado, número v1.3 reaproveitado.** O v1.3 ativo é o **Motor de alocação de OR** (seção seguinte). Esta seção fica só para rastreio histórico das Phases 10–12, que nunca executaram.

**Defined:** 2026-08-10
**Cancelada em:** 2026-08-10 — preflight revelou que o Aurora já estava configurado com o schema Alembic head (028) e dados ativos (~1.4M linhas); migração desnecessária. Ver `.planning/STATE.md` § Session note 2026-08-10. Requisitos abaixo preservados apenas para rastreio; não executar.
**Core Value:** O Aurora passa a conter uma cópia íntegra e verificável do banco local, e a aplicação continua operando e ingerindo dados no destino AWS.

### Backup e preparo

- [ ] **MIG-01**: Operador pode inventariar tabelas, contagens, extensões e sequências do banco local e produzir um backup recuperável antes da migração
- [ ] **MIG-02**: Operador pode confirmar que o schema do Aurora está no head compatível do Alembic antes de restaurar dados

### Cópia e integridade

- [ ] **MIG-03**: Operador pode restaurar no Aurora todas as tabelas e todos os dados do PostgreSQL local sem apagar a origem
- [ ] **MIG-04**: Operador pode comparar origem e destino por estrutura, contagens, dados críticos, relações e sequências e receber resultado inequívoco de aprovação ou falha

### Corte e operação

- [ ] **MIG-05**: Operador pode executar o corte em janela controlada, sem novas escritas durante a cópia final, com retorno documentado ao banco local se a validação falhar
- [ ] **MIG-06**: API, worker e Celery Beat passam a usar o Aurora e uma ingestão Databricks → Aurora é comprovada manualmente e no agendamento de 2 horas
- [ ] **MIG-07**: Operador cria snapshot do Aurora depois da validação final da migração

### Traceability v1.3-CANCELADA (Aurora)

| Requirement | Phase | Status |
|-------------|-------|--------|
| MIG-01 | Phase 10 — Preflight e backup recuperável | Cancelled (2026-08-10) |
| MIG-02 | Phase 11 — Schema Aurora e restauração completa | Cancelled (2026-08-10) |
| MIG-03 | Phase 11 — Schema Aurora e restauração completa | Cancelled (2026-08-10) |
| MIG-04 | Phase 12 — Validação, corte e ingestão no Aurora | Cancelled (2026-08-10) |
| MIG-05 | Phase 12 — Validação, corte e ingestão no Aurora | Cancelled (2026-08-10) |
| MIG-06 | Phase 12 — Validação, corte e ingestão no Aurora | Cancelled (2026-08-10) |
| MIG-07 | Phase 12 — Validação, corte e ingestão no Aurora | Cancelled (2026-08-10) |

**Coverage v1.3:**

- requirements: 7 total
- Mapped to phases: 7
- Unmapped: 0 ✓
- Milestone cancelled 2026-08-10 (Aurora já populado com schema 028) — nenhuma phase executada

---

## v1.3 Requirements — Motor de alocação de OR fiel às regras de negócio

**Defined:** 2026-08-14
**Core Value:** O processamento de OR reserva exatamente o que as regras de distribuição mandam — só para quem tem crédito e estoque — e quem fica de fora permanece visível e rotulado na aba de pedidos em aberto.

**Fonte:** regras ditadas pela mantenedora em 2026-08-14 (registradas em `PROJECT.md` § Regras de negócio da alocação) + investigação do código e pesquisa em `.planning/research/v1.3/`.

**Numeração de fases:** começa na **Phase 13** (1–3 = v1.1; 4–9 = v1.2 pausado; 10–12 = v1.3-Aurora cancelada).

### Modo sem adequação

- [x] **ALOC-01**: Cliente sem crédito nunca entra na OR do modo sem adequação — o pedido dele continua na aba de pedidos em aberto até o status mudar
- [x] **ALOC-02**: No modo sem adequação, o analista recebe a grade exatamente como o cliente pediu ou o par fica em stand by — nunca uma quantidade diferente da pedida
- [x] **ALOC-03**: Quando vários clientes disputam o mesmo produto no modo sem adequação, o estoque é consumido na ordem de prioridade por valor do pedido, e quem não couber fica em stand by

### Regras comuns aos dois modos

- [x] **ALOC-04**: Um par cliente×produto cuja grade pedida tenha algum tamanho **do meio** com 0 reservável fica em stand by, mesmo que os demais tamanhos tenham estoque (integridade de grade / *size run*)
- [ ] **ALOC-05**: Nenhum par com alocação final de 0 peça é marcado como processado nem gera OR — o par volta para a fila em vez de virar uma OR vazia
- [x] **ALOC-06**: Empates na prioridade são desempatados por critério total e explícito, de modo que duas execuções sobre a mesma foto produzam o mesmo plano

### Modo com adequação

- [x] **ALOC-07**: A tolerância é medida sobre a quantidade total do **pedido completo** do cliente (todos os produtos), não sobre a grade de cada produto isolado
- [x] **ALOC-08**: Adição e corte têm orçamentos **separados** de 5% cada, que não se compensam entre si
- [~] **ALOC-09**: O orçamento já consumido em ORs anteriores continua debitado em execuções seguintes — o cliente não ganha 5% novos a cada rodada (mecanismo provado por teste em 14-06 — o ledger nunca reseta nem excede o limite entre construções sucessivas; a prova ponta a ponta via `processar_pedidos` fica para a Phase 15, que liga os parâmetros opcionais aos dados reais lidos do banco. Enquanto isso, o fallback é ruidoso: `logger.warning` citando ALOC-09 sempre que o placeholder de execução única é usado)
- [x] **ALOC-10**: A alocação garante o mínimo viável de cada produto antes de distribuir peças extras, para que o pedido não fique com produtos zerados por ter gasto o orçamento no primeiro produto da fila
- [ ] **ALOC-11**: As peças extras entram nos tamanhos com maior sobra de estoque, não em posição fixa da grade

### Correções acopladas (bugs que a mudança expõe)

- [x] **FIX-01**: A revalidação de capacidade de estoque antes da escrita passa a valer também para OR do tipo `sem` — hoje só roda para `com` (`processing/infrastructure/repository.py:489-513`)
- [x] **FIX-02**: O valor financeiro recalculado quando a quantidade muda fecha exatamente com o total, sem drift de centavos, reusando o rateio Hamilton de `domain/edicao_grade.py`

### Arquitetura

- [x] **ARCH-01**: O modo sem adequação passa a ser planejado pelo caminho global (prioridade e estoque compartilhado exigem visão global), e o caminho de streaming órfão é removido na mesma entrega

### Visibilidade do stand by

- [x] **STANDBY-01**: O motivo pelo qual cada par ficou de fora é persistido a cada processamento, sem inflar o resultado do job (que continua limitado a contadores) — `pedido_standby_motivo` (migration 032), populada via `record_standby_reasons` na mesma transação de `store_plan` (Phases 16-01 a 16-04)
- [ ] **STANDBY-02**: Cliente bloqueado por crédito aparece com tag de falta de crédito em **todos** os produtos que ele tentou reservar
- [ ] **STANDBY-03**: Cliente sem estoque aparece com tag de aguardando estoque **apenas** nos produtos em que faltou peça para ele
- [ ] **STANDBY-04**: Par barrado por grade incompleta recebe rótulo próprio, distinto de falta geral de estoque
- [ ] **STANDBY-05**: O analista pode filtrar a aba de pedidos em aberto por motivo do stand by, seguindo o padrão de filtro que o histórico já usa
- [x] **STANDBY-06**: O sistema registra há quantas execuções consecutivas um par está em stand by, para tornar visível se algum cliente está sendo sistematicamente preterido — `execucoes_consecutivas`, agnóstico ao motivo, incrementado por upsert (16-04)

### Testes

- [ ] **TEST-01**: As invariantes do motor (nunca reservar acima do disponível, orçamento nunca estourado somando execuções, par com furo nunca selecionado, par zerado nunca selecionado) são provadas por testes baseados em propriedade, com `hypothesis` como dependência **apenas de desenvolvimento**

### Traceability v1.3

| Requirement | Phase | Status |
|-------------|-------|--------|
| ALOC-01 | Phase 14 — Motor de alocação puro | Complete (14-05) |
| ALOC-02 | Phase 14 — Motor de alocação puro | Complete (14-05) |
| ALOC-03 | Phase 14 — Motor de alocação puro | Complete (14-05) |
| ALOC-04 | Phase 14 — Motor de alocação puro | Complete (14-04) |
| ALOC-05 | Phase 13 — Guarda contra OR zerada | Pending |
| ALOC-06 | Phase 14 — Motor de alocação puro | Complete (14-03) |
| ALOC-07 | Phase 14 — Motor de alocação puro | Complete (14-06) |
| ALOC-08 | Phase 14 — Motor de alocação puro | Complete (14-06) |
| ALOC-09 | Phase 14 — Motor de alocação puro | Partial (14-06: mecanismo do ledger provado por teste, nunca reseta nem excede o limite; fallback ruidoso via `logger.warning`. Prova ponta a ponta via `processar_pedidos` com dados reais fica para a Phase 15) |
| ALOC-10 | Phase 14 — Motor de alocação puro | Complete (14-06) |
| ALOC-11 | Phase 18 — Peças extras nos tamanhos com mais sobra | Pending |
| FIX-01 | Phase 15 — Roteamento global do modo sem adequação | Complete (15-02) |
| FIX-02 | Phase 14 — Motor de alocação puro | Complete (14-01 + 14-06: `ratear_hamilton` extraída para `domain/rateio.py` em 14-01, ligada ao recálculo financeiro do motor em 14-06) |
| ARCH-01 | Phase 15 — Roteamento global do modo sem adequação | Complete (15-03) |
| STANDBY-01 | Phase 16 — Persistência do motivo de stand by | Complete |
| STANDBY-02 | Phase 17 — Visibilidade do stand by na UI | Pending |
| STANDBY-03 | Phase 17 — Visibilidade do stand by na UI | Pending |
| STANDBY-04 | Phase 17 — Visibilidade do stand by na UI | Pending |
| STANDBY-05 | Phase 17 — Visibilidade do stand by na UI | Pending |
| STANDBY-06 | Phase 16 — Persistência do motivo de stand by | Complete |
| TEST-01 | Phase 19 — Validação final e shadow run | Pending |

**Coverage v1.3:**

- requirements: 21 total
- Mapped to phases: 21
- Unmapped: 0 ✓
- Roadmap criado 2026-08-14 — 7 fases (13–19). Ordem segue a dependência real apurada em `research/v1.3/ARCHITECTURE.md`/`PITFALLS.md`: guarda isolada (13) e motor puro (14) em paralelo → roteamento global (15) → persistência do motivo (16) → UI (17, paralelizável com peças extras em 18, que só depende de 14) → validação final/shadow run (19) como gate de saída.

---
*Requirements defined: 2026-08-04 (v1.1)*  
*Last updated: 2026-08-14 — roadmap v1.3 criado (Phases 13–19); traceability v1.3 preenchida, 21/21 requirements mapeados, 0 órfãos*
