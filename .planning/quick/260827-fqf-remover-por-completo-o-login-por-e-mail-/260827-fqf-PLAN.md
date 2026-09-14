---
phase: quick/260827-fqf
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: false
requirements: [QUICK-260827-FQF]
files_modified:
  - app/modules/auth/application/casos_uso.py
  - app/modules/auth/application/schemas.py
  - app/modules/auth/service.py
  - app/modules/auth/domain/exceptions.py
  - app/modules/auth/domain/tokens.py
  - app/modules/auth/domain/senha.py
  - app/modules/auth/domain/otp_hash.py
  - app/modules/auth/infrastructure/security.py
  - app/modules/auth/infrastructure/models.py
  - app/modules/auth/infrastructure/repositorio_usuario.py
  - app/modules/auth/infrastructure/repositorio_otp.py
  - app/modules/auth/infrastructure/otp_email.py
  - app/modules/auth/infrastructure/http/routes.py
  - app/modules/auth/bootstrap/__init__.py
  - app/modules/auth/bootstrap/seed.py
  - app/modules/auth/__init__.py
  - app/modules/pedidos/infrastructure/resumo/listar_alertas.py
  - app/shared/infrastructure/account_lockout.py
  - app/shared/config/settings.py
  - app/bootstrap.py
  - alembic/env.py
  - alembic/versions/035_auth_remove_login_local.py
  - pyproject.toml
  - .env.example
  - .docker/docker-compose.yml
  - .docker/docker-compose.prod.yml
  - .github/workflows/pipeline.yml
  - app/tests/conftest.py
  - app/tests/test_auth_flows.py
  - app/tests/test_auth_sessao_revogacao.py
  - app/tests/test_auth_reuso_refresh_token.py
  - app/tests/test_auth_sso_microsoft.py
  - app/tests/test_auth.py
  - app/tests/test_auth_otp_hash.py
  - app/tests/test_auth_otp_email.py
  - app/tests/test_auth_rate_limit.py
  - app/tests/test_settings_seed.py
  - app/tests/test_migration_030_auth_otp_code_hash.py
  - app/tests/test_migration_035_auth_remove_login_local.py
  - app/tests/test_migration_022_communication_delivery.py
  - app/tests/test_schema_guard.py
  - app/tests/test_docs_seguranca.py
  - app/tests/test_database_options.py
  - app/tests/test_metrics_guard.py
  - app/tests/test_parametros.py
  - app/tests/test_pedidos_routes.py
  - app/tests/test_comunicacoes.py
  - app/tests/test_comunicacoes_delivery_repository.py
  - app/tests/test_request_body_limit.py
  - docs/seguranca.md
  - docs/api.md
  - docs/index.md
  - docs/arquitetura.md
  - README.md
  - .claude/CLAUDE.md
  - .planning/REQUIREMENTS.md

must_haves:
  truths:
    - "POST /api/auth/sign-in, /sign-in/confirm-new-password, /register, /register/confirm, /password/recovery e /password/recovery/confirm não existem mais na aplicação (404 no TestClient), provado por teste automatizado"
    - "POST /api/auth/sso/microsoft é o único login e continua provisionando conta nova com papel basico e confirmed_at preenchido"
    - "POST /api/auth/token/refresh, POST /api/auth/sign-out, GET /api/auth/users, PUT /api/auth/users/{id}/roles e DELETE /api/auth/users/{id} continuam funcionando com token emitido fora do fluxo de senha"
    - "O login por SSO passa a ter cobertura automatizada — hoje não tem nenhuma, e ele vira o único caminho de entrada"
    - "Nenhum arquivo do backend importa bcrypt, hash_password, verify_password, create_session_token, decode_session_token, OtpPurpose, AuthOtpChallenge, repositorio_otp, otp_email, seed_auth_users ou DEFAULT_AUTH_USERS"
    - "auth_users não tem mais password_hash, user_name, cpf nem phone; a tabela auth_otp_challenges não existe mais no banco de aplicação nem no de teste"
    - "alembic check não acusa drift entre models e schema (é gate do CI)"
    - "A suíte completa fecha sem NENHUMA falha nova em relação ao baseline medido na Task 1"
    - "ruff check, ruff format --check e pyright limpos"
    - "Nenhuma senha das 5 contas seed sobrevive em qualquer arquivo versionado, e existe teste que impede a reintrodução delas em docs/seguranca.md"
    - "O trabalho não commitado de outras sessões que não pertence a esta task segue intocado no working tree"
  artifacts:
    - path: "alembic/versions/035_auth_remove_login_local.py"
      provides: "Drop de password_hash/user_name/cpf/phone + uq_auth_users_cpf, drop da tabela auth_otp_challenges e backfill de confirmed_at"
      contains: "auth_otp_challenges"
    - path: "app/tests/test_migration_035_auth_remove_login_local.py"
      provides: "Ciclo destrutivo isolado da 035 (upgrade, downgrade estrutural, re-upgrade) no banco descartável"
      min_lines: 100
    - path: "app/tests/test_auth_sso_microsoft.py"
      provides: "Cobertura ponta a ponta do único login que sobra: 503 não configurado, 401 token inválido, 403 sem e-mail, 200 provisiona, 200 conta existente preserva papéis"
      min_lines: 120
    - path: "docs/seguranca.md"
      provides: "Runbook reescrito para SSO-only: detecção e remoção das contas locais remanescentes (com as travas de FK), rotação da chave de métricas e controles vigentes"
      contains: "auth_provider"
  key_links:
    - from: "app/modules/auth/infrastructure/http/routes.py"
      to: "app.modules.auth.service.sign_in_microsoft"
      via: "POST /sso/microsoft — único endpoint de login restante"
      pattern: "sign_in_microsoft"
    - from: "app/tests/test_parametros.py e app/tests/test_pedidos_routes.py"
      to: "app.modules.auth.infrastructure.security.create_access_token"
      via: "emissão direta de token, substituindo o POST /api/auth/sign-in que sumiu"
      pattern: "create_access_token"
    - from: "alembic/env.py e app/tests/test_schema_guard.py"
      to: "app.modules.auth.infrastructure.models"
      via: "import de registro em Base.metadata, agora só com AuthUser"
      pattern: "AuthUser"
---

<objective>
Remover por completo o login por e-mail+senha do backend, deixando o SSO Microsoft Entra ID como
único caminho de autenticação. Isso reverte a decisão de 25/08/2026 (manter os dois métodos lado a
lado) por decisão explícita da mantenedora.

Purpose: o login local hoje existe "só para fins de teste/comparação de papéis" (STATE.md, política
de OTP de 2026-08-25) e carrega junto uma superfície de ataque inteira que ninguém usa em produção:
5 contas seed com senha em texto claro no repositório, cadastro público, OTP por e-mail, recuperação
de senha, rate limit e lockout de conta — tudo para proteger um fluxo que não deveria existir. Com
Entra ID single-tenant, quem autoriza é a Microsoft; o backend só precisa validar o ID token,
provisionar a conta com papel mínimo e deixar a promoção de papel para o endpoint de admin que já
existe.

Output: o módulo `auth` reduzido a SSO + sessão (refresh/sign-out/revogação) + RBAC + gestão de
usuários; 4 colunas e 1 tabela a menos no schema; ~6 arquivos de produção e 6 de teste apagados;
o único login que sobra ganha a cobertura automatizada que hoje não tem.

Não-objetivo (explícito, não reabrir):
- NÃO mexer em JWT (`domain/tokens.py`, exceto os dois session tokens órfãos), RBAC
  (`domain/roles.py`, `http/dependencies.py`, `app/shared/security/__init__.py`), sign-out/refresh/
  detecção de reuso de jti, nem em gestão de usuários (`/users*`).
- NÃO mexer no SSO em si (`microsoft_sso.py`, `sign_in_microsoft`, `MicrosoftSsoRequest`, as 3
  exceções de SSO, `MICROSOFT_TENANT_ID`/`MICROSOFT_CLIENT_ID`) além do que a remoção exigir.
- NÃO mexer em `SMTP_*` nem no módulo `comunicacoes` (o SMTP também serve o "Comunicar Time
  Comercial" — só o adapter `auth/infrastructure/otp_email.py` sai).
- NÃO mexer em `app/shared/infrastructure/rate_limit.py` (genérico, também usado por `pedidos` e
  `comunicacoes`).
- NÃO mexer no frontend (removido à parte) nem em `LEGACY_ROLE_MAP`.
- NÃO criar fluxo novo de provisionamento: conta nova nasce no primeiro login SSO com papel
  `basico` e é promovida à mão por `PUT /api/auth/users/{id}/roles`, como já é hoje.

## Três decisões de contrato que o executor precisa conhecer ANTES de começar

Todas as três estão no checkpoint da Task 2 e não podem ser tomadas em silêncio durante a execução:

1. **`AuthResponse` encolhe.** `challenge`/`SignInChallengeResponse` só existem por causa do desafio
   `CONFIRM_SIGN_UP` do sign-in local (confirmado por grep: o SSO nunca preenche `challenge`).
   `AuthUserResponse.display_name`/`cpf`/`phone` só existem por causa das colunas que vão ser
   dropadas — sem elas viram campos permanentemente `None`. Os dois são mudança de contrato que o
   frontend consome.
2. **Colisão com o trabalho SEC-08 (lockout de conta) de outra sessão.** Ele está em voo no working
   tree AGORA e vive exatamente nos arquivos que esta task apaga. Ver o bloco de concorrência no
   `<context>`.
3. **O alerta operacional "acesso pendente" morre.** Ver a seção de zona cinzenta no `<context>`.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.claude/CLAUDE.md
@app/modules/auth/application/casos_uso.py
@app/modules/auth/infrastructure/http/routes.py
@app/modules/auth/application/schemas.py
@docs/seguranca.md

## Comandos canônicos deste projeto

Os testes NÃO rodam com `uv run pytest` direto no host. Rodam dentro do container que já está de pé
(o `/workspace/app` do container vem da cópia principal do repositório):

- Suíte completa: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- Subconjunto: o mesmo comando seguido dos caminhos dos arquivos e `-q`
- Lint: `... exec -T api uv run ruff check app alembic`
- Formato: `... exec -T api uv run ruff format --check app alembic`
- Tipos: `... exec -T api uv run pyright app alembic`
- Paridade model×schema: `... exec -T api uv run alembic check`

Se o container não estiver rodando: `docker compose -f .docker/docker-compose.yml up -d`.

`workflow.use_worktrees` está `false` em `.planning/config.json` de propósito: um worktree editaria
uma pasta e testaria outra (o container monta a cópia principal). **Não religar.**

## Dois bancos, e o de teste NÃO é migrado automaticamente

Lição já paga duas vezes neste projeto (bloco "Banco de teste duas migrations atrás" no STATE.md):
`conftest.py` não roda alembic nem `create_all`. Depois de criar a migration 035 é obrigatório
aplicá-la **nos dois** bancos, senão `test_schema_guard.py` acusa drift:

- aplicação (`system_automation`): o container `api` roda `alembic upgrade head` na subida — basta
  reiniciar o serviço, ou rodar `... exec -T api uv run alembic upgrade head`.
- teste (`system_automation_test`): à mão, com `DATABASE_URL` apontando explicitamente para ele. Do host:
  `DATABASE_URL=postgresql+asyncpg://automation:automation@localhost:5432/system_automation_test ./.venv/Scripts/python.exe -m alembic upgrade head`. <!-- pragma: allowlist secret -->
  <!-- credencial local de dev, mesmo default documentado em settings.py; não é segredo real -->

Sempre confirmar **qual** banco a conexão resolveu antes de concluir qualquer coisa sobre drift —
já houve diagnóstico errado por comparar `alembic current` de um banco com falha do outro.

## Working tree compartilhado — LEIA ANTES DE QUALQUER `git`

Várias sessões commitam neste mesmo checkout ao mesmo tempo, e o estado muda enquanto se planeja.
Duas fotos de `git status --short` na branch `develop`, tiradas com poucos minutos de diferença
durante o levantamento desta task:

Primeira:

```
 M .planning/STATE.md
 M app/modules/auth/infrastructure/http/routes.py
 M app/shared/config/settings.py
 M app/tests/conftest.py
 M app/tests/test_auth_rate_limit.py
?? app/shared/infrastructure/account_lockout.py
```

Segunda, minutos depois (cresceu):

```
 M .planning/REQUIREMENTS.md
 M .planning/ROADMAP.md
 M .planning/STATE.md
 M app/modules/auth/infrastructure/http/routes.py
 M app/shared/config/settings.py
 M app/tests/conftest.py
 M app/tests/test_auth_rate_limit.py
 M docs/seguranca.md
?? app/shared/infrastructure/account_lockout.py
```

Ou seja: **a outra sessão está editando, agora, 6 dos arquivos que esta task precisa reescrever ou
apagar** — incluindo `docs/seguranca.md` (Task 8) e `.planning/REQUIREMENTS.md` (Task 8).

O conteúdo dessas mudanças é a implementação de **SEC-08 (lockout de conta por falhas
consecutivas)**, que colide de frente com esta task: `account_lockout.py` só tem um chamador
(`auth/.../http/routes.py`), e os dois pontos onde é chamado são o sign-in por senha e a confirmação
de OTP — os dois endpoints que esta task apaga. Os testes de SEC-08 estão dentro de
`test_auth_rate_limit.py`, arquivo que esta task apaga inteiro.

Durante o planejamento, `routes.py` mudou **entre duas leituras** (os nomes das settings de lockout
passaram de `auth_lockout_threshold` para `auth_lockout_sign_in_threshold` no meio da varredura) —
prova de que a outra sessão estava editando naquele instante, e sinal de que a suíte pode estar
temporariamente vermelha por um estado intermediário que NÃO é desta task.

Por isso a Task 2 é um checkpoint bloqueante. Antes dele: **não editar, não commitar, não reverter,
não apagar nada** desses arquivos. A Task 1 tira uma foto nova de `git status --short` — é ela, não
as duas acima, que vale como referência na hora de provar no fim que nada alheio foi tocado.

PROIBIDO nesta task, do começo ao fim: `git add -A`, `git add .`, `git commit -a`, `git stash`,
`git reset --hard`, `git checkout -- <path>`, `git clean`. Todo commit com caminhos explícitos,
precedido de `git status --short` e conferido com `git show --stat`. Nenhum commit menciona Claude
nem leva `Co-Authored-By` (regra permanente do projeto). Não fazer `push`.

## Inventário levantado na varredura (confirmar, não re-investigar do zero)

**Exclusivo de senha/OTP — apagar arquivo inteiro:**
`domain/senha.py`, `domain/otp_hash.py`, `infrastructure/repositorio_otp.py`,
`infrastructure/otp_email.py`, `bootstrap/seed.py` + `bootstrap/__init__.py` (o pacote inteiro fica
vazio).

**Exclusivo de senha/OTP — remover de dentro de arquivo que fica:**
- `application/casos_uso.py`: `sign_in`, `register`, `confirm_register`, `request_password_recovery`,
  `confirm_password_recovery`, `confirm_new_password`, `_masked_email`, e os imports que ficarem
  órfãos. `_user_response` perde `display_name`/`cpf`/`phone`; `_auth_success` perde `challenge=None`.
- `application/schemas.py`: `SignInRequest`, `SignInChallengeResponse`, `RegisterRequest`,
  `RegisterDelivery`, `RegisterResponse`, `ConfirmRegisterRequest`, `ConfirmRegisterStatusResponse`,
  `PasswordRecoveryRequest`, `PasswordRecoveryResponse`, `ConfirmPasswordRecoveryRequest`,
  `ConfirmPasswordRecoveryResponse`, `ConfirmNewPasswordRequest`; <!-- pragma: allowlist secret --> campos `display_name`/`cpf`/`phone`
  de `AuthUserResponse`; campo `challenge` de `AuthResponse`.
- `domain/exceptions.py`: `CredenciaisInvalidasError`, `EmailJaRegistradoError`,
  `CpfJaRegistradoError`, `CodigoConfirmacaoInvalidoError`.
- `domain/tokens.py`: `create_session_token` e `decode_session_token` — grep confirma que os únicos
  chamadores são `sign_in` (desafio `CONFIRM_SIGN_UP`) e `confirm_new_password`. O SSO não usa.
- `infrastructure/security.py`: os 4 reexports correspondentes (`hash_password`, `verify_password`,
  `create_session_token`, `decode_session_token`) e o `__all__`.
- `infrastructure/models.py`: `OtpPurpose`, `AuthOtpChallenge`, e as colunas `password_hash`,
  `user_name`, `cpf`, `phone` de `AuthUser`. **`auth_provider` fica** (marca a origem da conta e é
  a base da nova query de detecção do runbook).
- `infrastructure/repositorio_usuario.py`: `buscar_por_cpf`.
- `infrastructure/http/routes.py`: os 6 endpoints de senha/OTP e TODO o aparato de rate limit
  (`_aplicar_rate_limit`, as 7 constantes `_SIGN_IN_*`/`_OTP_*`/`_RATE_LIMIT_UNAVAILABLE_*`) e de
  lockout (`_lockout_locked_response`, `_verificar_bloqueio`, `_registrar_falha_bloqueio`,
  `_resetar_bloqueio`), que só serviam a eles. Sobram 6 rotas: `POST /sso/microsoft`,
  `POST /token/refresh`, `POST /sign-out`, `GET /users`, `PUT /users/{id}/roles`,
  `DELETE /users/{id}`.
- `service.py`: o `__all__` cai para `delete_user`, `list_users_page`, `refresh_token`,
  `set_user_roles`, `sign_in_microsoft`, `sign_out`.

**Fiação/config a limpar:** `app/bootstrap.py` (chamada de `run_auth_seed`), `settings.py`
(`seed_auth_on_startup` + a checagem de PROD + as 4 settings de lockout, se SEC-08 for revertido),
`.env.example:184`, `.docker/docker-compose.yml:105`, `.docker/docker-compose.prod.yml:23`,
`.github/workflows/pipeline.yml:103`, `pyproject.toml` (dependência `bcrypt` — grep confirma que o
único importador é `domain/senha.py`), `alembic/env.py` (import de `AuthOtpChallenge`; **nunca**
mexer nos outros `# noqa: F401`).

**Compartilhado — NÃO tocar:** `domain/tokens.py` fora dos dois session tokens, `domain/roles.py`,
`domain/email.py`, `http/dependencies.py`, `app/shared/security/__init__.py`,
`app/shared/infrastructure/rate_limit.py`, `sign_out`/`refresh_token`/`_auth_success`/detecção de
reuso de jti, `list_users_page`/`set_user_roles`/`delete_user`, `AuthUser` fora das 4 colunas.

## Zona cinzenta já investigada — decisões tomadas, executar como está escrito

**1. Alerta "acesso pendente" (`pedidos/infrastructure/resumo/listar_alertas.py`, bloco 4,
categoria `acesso`).** Ele lista `auth_users WHERE confirmed_at IS NULL`. O provisionamento por SSO
grava `confirmed_at=datetime.now(UTC)` sempre (`casos_uso.sign_in_microsoft`), e depois desta task
não sobra nenhum outro caminho de INSERT em `auth_users`. Com o backfill da migration 035 (todas as
linhas legadas passam a ter `confirmed_at`), a categoria fica morta por construção.
**Decisão: remover o bloco 4 inteiro** e ajustar o docstring de "9 categorias" para 8, removendo
`acesso` da lista. Efeito colateral bem-vindo: some a armadilha de poluição de teste documentada em
dois planos anteriores (usuário `confirmed_at IS NULL` órfão quebrando
`test_pedidos_read_projection`).

**2. Guarda `confirmed_at is None` do realtime** (`realtime/.../http/routes.py`, 2 ocorrências).
**Decisão: NÃO tocar.** É re-checagem defensiva do estado do usuário no momento da conexão, sobre
uma coluna que continua nullable no schema; mexer em `realtime` abre risco de regressão em módulo
fora do escopo, e o custo da guarda é uma comparação.

**3. `create_session_token`/`decode_session_token` e `SignInChallengeResponse`.**
**Decisão: remover os três** — grep confirma zero chamadores fora dos fluxos de senha.

## Lacuna crítica descoberta na varredura

**O login por SSO não tem NENHUM teste automatizado hoje.** Os matches de `sso` em `app/tests/` são
falsos-positivos de "sucesso"/"acesso". Ou seja: esta task apaga toda a superfície de login testada
e deixa como único caminho de entrada um endpoint sem cobertura. Fechar isso não é opcional — é a
Task 6.

## Contexto de teste que muda de forma não óbvia

- `conftest.py`: a fixture autouse `auth_rate_limit_redis` (e a classe `_FakeAuthRateRedis`) faz
  `monkeypatch.setattr` de `...http.routes.get_redis`. Depois da remoção, `routes.py` não importa
  mais `get_redis` e o `setattr` levanta `AttributeError` **em todo teste da suíte**. A fixture tem
  de sair junto. Único outro consumidor é `test_auth_rate_limit.py`, que é apagado.
- `test_parametros.py` e `test_pedidos_routes.py` autenticam via `POST /sign-in` só para obter um
  token — não testam auth. O padrão de substituição já existe no projeto: `test_ingestao_routes.py`
  e `test_pedidos_processing_routes.py` usam `app.dependency_overrides[require_admin]` com
  `CurrentUser(...)`. Aqui, porém, **não serve override**: esses dois arquivos exercitam a fiação
  real de RBAC contra o banco. Use a outra metade do padrão — emitir o token direto com
  `create_access_token(user_id=..., email=..., roles=[...])`, mantendo o INSERT do usuário (o
  `get_current_user` relê os papéis do banco, então a linha precisa existir).
- `test_comunicacoes.py` e `test_comunicacoes_delivery_repository.py` só passam
  `password_hash="not-used-in-tests"` no INSERT — basta remover o kwarg. <!-- pragma: allowlist secret -->
- `test_request_body_limit.py` usa `/api/auth/sign-in` como um POST público qualquer para provar o
  413 — trocar por `/api/auth/sso/microsoft`, que também é público e também é POST.
- `test_migration_022_communication_delivery.py` monta um INSERT cru com a coluna `password_hash` —
  quebra depois do drop.
- `test_docs_seguranca.py` importa `DEFAULT_AUTH_USERS`.
- `test_database_options.py` e `test_metrics_guard.py` citam `SEED_AUTH_ON_STARTUP`.
- CI roda `uv run alembic check` como passo próprio ("Validar paridade entre models e schema
  migrado"): model e migration têm de bater exatamente, ou o pipeline quebra.

## Protocolo de execução em etapas (esta task é grande)

São 8 tasks e o orçamento de contexto de um agente não cobre todas com qualidade. Regra:
**terminou a task, commitou, reportou — se o contexto passou de ~50%, PARE e devolva o controle**
anotando no SUMMARY em que task parou e qual é a próxima. Cada task é fechada por um commit próprio
e o estado é sempre retomável.

As Tasks 3, 4 e 5 deixam a suíte VERMELHA de propósito (produção removida antes dos testes serem
adaptados). Isso é esperado; o gate de verde é a Task 8. Não fazer `push` em nenhum momento.
</context>

<tasks>

<task type="auto">
  <name>Task 1: Baseline medido e inventário reconferido (nenhuma mudança de código)</name>
  <files>(nenhum — só medição; anotações vão para o scratchpad e depois para o SUMMARY)</files>
  <action>
Medir o estado real ANTES de mudar qualquer coisa. Nada de editar arquivo nesta task, nada de
commit.

1. Baseline da suíte: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -15`.
   Registrar os números exatos de passed/skipped/failed **e os nomes dos testes que falham**. NÃO
   usar o número anotado no STATE.md (~926/18/2): há sessões concorrentes mexendo na suíte agora. Se
   a suíte estiver quebrada de um jeito que não é o baseline conhecido (ex.: `AttributeError` em
   `auth_lockout_*`, sintoma do SEC-08 pela metade), anotar isso — é insumo direto do checkpoint da
   Task 2.
2. Baseline dos gates: `ruff check app alembic`, `ruff format --check app alembic`,
   `pyright app alembic` e `alembic check`, todos pelo container. Registrar o resultado de cada um.
   Se algum já estiver sujo antes de começar, o critério final passa a ser "não piorou".
3. Confirmar `alembic heads` (esperado `034` — a migration nova será `035`; se vier outra coisa,
   outra sessão criou migration e o número muda).
4. Reconferir o inventário do `<context>` contra o código real, porque ele pode ter mudado desde o
   planejamento. Rodar e guardar a saída de:
   `grep -rn "hash_password\|verify_password\|password_hash\|AuthOtpChallenge\|OtpPurpose\|repositorio_otp\|otp_email\|seed_auth\|DEFAULT_AUTH_USERS\|create_session_token\|decode_session_token\|buscar_por_cpf" --include="*.py" app/ alembic/ | grep -v __pycache__`
   e `grep -rn "SEED_AUTH\|api/auth/sign-in\|register/confirm\|password/recovery\|confirm-new-password" --include="*.py" --include="*.md" --include="*.yml" --include="*.toml" --include="*.example" . | grep -v "__pycache__\|\.planning/\|\.venv\|node_modules"`.
   Comparar com a lista do `<context>` e anotar **toda divergência** (item novo que apareceu, item
   que já sumiu). Divergência não invalida o plano — invalida a suposição pontual.
5. Confirmar que `app/shared/infrastructure/rate_limit.py` continua tendo chamadores em `pedidos` e
   `comunicacoes` (ele NÃO sai) e que `app/shared/infrastructure/account_lockout.py` continua tendo
   um único chamador em `auth/.../http/routes.py` (ele sai, se o checkpoint autorizar).
6. `git status --short` e `git log --oneline -5`: registrar exatamente o que está modificado/não
   rastreado por outras sessões. Esta foto — e não as duas do `<context>`, que já estão velhas — é a
   referência para provar no fim que nada alheio foi tocado. Atenção especial a `docs/seguranca.md`
   e `.planning/REQUIREMENTS.md`: se continuarem modificados por outra sessão, a Task 8 mexe neles
   por cima de trabalho alheio e isso precisa entrar no checkpoint da Task 2.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -15</automated>
  </verify>
  <done>
Baseline da suíte registrado em número absoluto E com a lista de testes que falham. Resultado dos 4
gates registrado. `alembic heads` confirmado. Lista de divergências entre o inventário do plano e o
código real escrita (ou "nenhuma divergência"). Estado do working tree de outras sessões registrado,
com menção explícita ao status de `docs/seguranca.md` e `.planning/REQUIREMENTS.md`. Nenhum arquivo
modificado, nenhum commit.
  </done>
</task>

<task type="checkpoint:human-verify" gate="blocking">
  <what-built>
Nada ainda — este é o ponto de decisão antes de qualquer remoção. A Task 1 mediu o terreno; aqui a
mantenedora confirma quatro coisas que o executor não pode decidir sozinho.
  </what-built>
  <how-to-verify>
Apresentar, com os dados medidos na Task 1:

**(a) Colisão com o trabalho SEC-08 (lockout de conta) de outra sessão.**
`app/shared/infrastructure/account_lockout.py` (não rastreado), mais as modificações não commitadas
em `routes.py`, `settings.py`, `conftest.py` e `test_auth_rate_limit.py`, implementam bloqueio de
conta para o sign-in por senha e para a confirmação de OTP — exatamente os endpoints que esta task
apaga. Depois da remoção, SEC-08 fica sem nenhum chamador possível: não sobra segredo adivinhável em
nenhum endpoint (o ID token da Microsoft é assinado; o refresh token é assinado).
Perguntar, e não seguir sem resposta:
  1. A sessão do SEC-08 já terminou e commitou? (se ainda estiver em voo, ESTA task espera)
  2. Autoriza remover `account_lockout.py`, as 4 settings `AUTH_LOCKOUT_*` e os testes de lockout
     junto com o resto, registrando no SUMMARY que SEC-08 deixou de se aplicar por não haver mais
     segredo adivinhável? A alternativa (manter o arquivo sem chamador) contraria a regra "sem
     código morto" desta task.

**(b) Mudança de contrato na resposta de login.** `AuthResponse` perde o campo `challenge` e
`AuthUserResponse` perde `display_name`, `cpf` e `phone` (as três colunas somem do banco). O
frontend consome esse payload e é removido à parte. Confirmar que pode encolher agora, sabendo que
haverá uma janela em que o frontend antigo pode ler campos ausentes.

**(c) O alerta operacional "acesso pendente" (categoria `acesso`) sai da tela de alertas.** Sem
cadastro por senha, `auth_users.confirmed_at` nunca mais é nulo (o SSO preenche no provisionamento e
a migration faz backfill das linhas legadas), então a categoria nunca mais dispararia. Confirmar a
remoção do bloco — a alternativa é deixar código que não pode executar.

**(d) `docs/seguranca.md` e `.planning/REQUIREMENTS.md` estão modificados por outra sessão.** A
Task 8 reescreve os dois. Confirmar se aquelas mudanças já foram commitadas (então a Task 8 edita
por cima, normalmente) ou se ainda estão em voo (então a Task 8 espera, ou a mantenedora diz o que
prevalece).

Reportar também qualquer divergência encontrada no item 4 da Task 1 e o baseline (incluindo se a
suíte está quebrada agora por causa do SEC-08 pela metade).
  </how-to-verify>
  <resume-signal>Responder (a), (b), (c) e (d) — "aprovado" para os quatro, ou o que muda em cada um</resume-signal>
</task>

<task type="auto">
  <name>Task 3: Remover o código de produção do login local</name>
  <files>app/modules/auth/application/casos_uso.py, app/modules/auth/application/schemas.py, app/modules/auth/service.py, app/modules/auth/domain/exceptions.py, app/modules/auth/domain/tokens.py, app/modules/auth/domain/senha.py, app/modules/auth/domain/otp_hash.py, app/modules/auth/infrastructure/security.py, app/modules/auth/infrastructure/models.py, app/modules/auth/infrastructure/repositorio_usuario.py, app/modules/auth/infrastructure/repositorio_otp.py, app/modules/auth/infrastructure/otp_email.py, app/modules/auth/infrastructure/http/routes.py, app/modules/auth/bootstrap/__init__.py, app/modules/auth/bootstrap/seed.py, app/modules/auth/__init__.py, app/modules/pedidos/infrastructure/resumo/listar_alertas.py, app/shared/infrastructure/account_lockout.py, app/shared/config/settings.py, app/bootstrap.py, alembic/env.py, pyproject.toml, .env.example, .docker/docker-compose.yml, .docker/docker-compose.prod.yml, .github/workflows/pipeline.yml</files>
  <action>
Executar a remoção exatamente conforme o inventário do `<context>`, ajustado pelas divergências da
Task 1 e pelas respostas do checkpoint da Task 2. A suíte fica vermelha ao fim desta task — é
esperado, os testes são a Task 5/6/7.

**Apagar arquivos inteiros** (`git rm`): `app/modules/auth/domain/senha.py`,
`app/modules/auth/domain/otp_hash.py`, `app/modules/auth/infrastructure/repositorio_otp.py`,
`app/modules/auth/infrastructure/otp_email.py`, `app/modules/auth/bootstrap/seed.py`,
`app/modules/auth/bootstrap/__init__.py` (o pacote fica vazio; conferir que nada mais importa
`app.modules.auth.bootstrap`), e `app/shared/infrastructure/account_lockout.py` se a Task 2
autorizou.

**Editar, na ordem de dentro para fora** (domínio → aplicação → HTTP → fiação), rodando
`ruff check app alembic` depois de cada camada para caçar import órfão cedo:

1. `domain/exceptions.py`: remover as 4 exceções listadas no inventário. Não tocar nas outras.
2. `domain/tokens.py`: remover `create_session_token` e `decode_session_token`. O resto do arquivo
   (access/refresh, `jti`, `auth_time`) fica byte a byte.
3. `infrastructure/security.py`: remover os 4 reexports e as entradas correspondentes do `__all__`.
4. `infrastructure/models.py`: remover `OtpPurpose`, `AuthOtpChallenge` e as 4 colunas de
   `AuthUser`. Manter `auth_provider` com o `server_default="local"`. Ajustar o comentário que
   explicava por que `password_hash` era nulo para contas SSO — ele deixa de fazer sentido.
5. `infrastructure/repositorio_usuario.py`: remover `buscar_por_cpf`.
6. `application/schemas.py`: remover os 12 schemas listados; tirar `display_name`/`cpf`/`phone` de
   `AuthUserResponse` e `challenge` de `AuthResponse`. Como todo `AuthResponse` que sobra vem de
   `_auth_success` (que sempre emite token), tornar `token: TokenBlock` obrigatório (sem `| None`).
   Não mexer em `TokenBlock`, `AdminUserResponse`, `AdminUsersPageResponse`, `SetUserRolesRequest`,
   `MicrosoftSsoRequest`, `RefreshTokenRequest`, `SignOutResponse`.
7. `application/casos_uso.py`: remover as 6 funções de senha/OTP e `_masked_email`; ajustar
   `_user_response` e `_auth_success` (sem `challenge=None`); limpar os imports que ficarem órfãos
   (`hash_password`, `verify_password`, `create_session_token`, `decode_session_token`,
   `repositorio_otp`, `OtpPurpose`, as 4 exceções, e os schemas removidos). Em
   `sign_in_microsoft`, tirar o `password_hash=None` do construtor de `AuthUser`. `refresh_token`,
   `sign_out`, os helpers de Redis, `list_users_page`, `set_user_roles` e `delete_user` ficam
   intactos.
8. `service.py`: reduzir imports e `__all__` às 6 funções que sobram.
9. `infrastructure/http/routes.py`: remover os 6 endpoints, o aparato de rate limit e o de lockout,
   e todos os imports que ficarem órfãos (`Request`, `get_settings`, `normalizar_email`,
   `get_redis`, `rate_limit`, `account_lockout`, os schemas e exceções removidos). Conferir que
   sobram exatamente 6 decoradores `@router.` e que `HTTPException` continua importado (segue em uso
   por SSO/refresh/users).
10. `app/modules/auth/__init__.py`: o docstring diz "Módulo de autenticação local" — trocar para
    refletir que a autenticação é via Microsoft Entra ID.
11. `app/bootstrap.py`: remover o import de `run_auth_seed` e o bloco `if settings.seed_auth_on_startup`.
12. `app/shared/config/settings.py`: remover o campo `seed_auth_on_startup` (e o comentário acima
    dele) e a linha do bloco PROD que levanta `"SEED_AUTH_ON_STARTUP deve ser false em PROD"`. Se a
    Task 2 autorizou, remover também as 4 settings `auth_lockout_*`. NÃO tocar em nada de SMTP, JWT,
    Microsoft, realtime ou métricas.
13. `alembic/env.py`: tirar `AuthOtpChallenge` do import de models, mantendo `AuthUser` e o
    `# noqa: F401`. Não encostar nos outros imports de model — sem eles o autogenerate propõe
    `DROP TABLE`.
14. `pyproject.toml`: remover a dependência `bcrypt` (grep prova que o único importador era
    `domain/senha.py`). Rodar `uv lock` para atualizar o `uv.lock` — o CI usa `uv sync --frozen` e
    quebra se o lock não bater com o `pyproject.toml`.
15. `.env.example`, `.docker/docker-compose.yml`, `.docker/docker-compose.prod.yml`,
    `.github/workflows/pipeline.yml`: remover as 4 ocorrências de `SEED_AUTH_ON_STARTUP`.
16. `pedidos/infrastructure/resumo/listar_alertas.py`: remover o bloco 4 (`unconfirmed_user_rows` +
    o loop que gera `alert-user-*`), tirar `acesso` da lista de categorias do docstring e corrigir
    "9 categorias" para 8. Não renumerar os comentários dos outros blocos (ruído de diff sem valor);
    se renumerar, renumerar todos de uma vez.

Ao fim, provar que não sobrou referência: rodar de novo os dois greps do item 4 da Task 1 e
confirmar que só restam ocorrências em `app/tests/` (que a Task 5/6/7 resolve) e em
`docs/`/`README.md` (Task 8). Nenhuma ocorrência pode restar em `app/modules/`, `app/shared/`,
`app/bootstrap.py`, `alembic/env.py`, `pyproject.toml`, `.docker/`, `.github/` ou `.env.example`.

Commits (caminhos explícitos, `git status --short` antes de cada um, nada de outra sessão junto):
- `refactor(auth): remove login por e-mail e senha, cadastro, OTP e recuperacao`
- `chore(config): remove seed de usuarios e dependencia bcrypt`
- `refactor(pedidos): remove alerta de acesso pendente sem emissor possivel`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app alembic && docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "import app.main; print('import ok')"</automated>
  </verify>
  <done>
`ruff check app alembic` limpo e `import app.main` funciona (prova que não sobrou import quebrado na
produção). `grep -rn "hash_password\|verify_password\|OtpPurpose\|AuthOtpChallenge\|repositorio_otp\|otp_email\|seed_auth\|create_session_token\|buscar_por_cpf" --include="*.py" app/modules app/shared app/bootstrap.py alembic/ | grep -v __pycache__` sai vazio.
`grep -c "@router\." app/modules/auth/infrastructure/http/routes.py` retorna 6.
`grep -rn "SEED_AUTH" .env.example .docker/ .github/ app/` sai vazio fora de `app/tests/`.
`grep -c bcrypt pyproject.toml` retorna 0 e o `uv.lock` foi regenerado.
3 commits, caminhos explícitos, sem menção a Claude; `git status --short` mostra que os arquivos de
outras sessões seguem como estavam na foto da Task 1.
  </done>
</task>

<task type="auto">
  <name>Task 4: Migration 035 — dropar as 4 colunas, a tabela de OTP e fazer o backfill de confirmed_at</name>
  <files>alembic/versions/035_auth_remove_login_local.py, app/tests/test_migration_035_auth_remove_login_local.py</files>
  <action>
Criar `alembic/versions/035_auth_remove_login_local.py` (se a Task 1 mostrou head diferente de
`034`, usar o número seguinte ao head real e ajustar `down_revision`). Seguir o estilo dos vizinhos:
docstring de módulo explicando o PORQUÊ, os 4 identificadores de revisão (`revision`,
`down_revision`, `branch_labels`, `depends_on`), `upgrade()` e `downgrade()`.

`upgrade()`, nesta ordem:
1. Backfill primeiro, enquanto a tabela ainda está intacta:
   `UPDATE auth_users SET confirmed_at = now() WHERE confirmed_at IS NULL`. O docstring tem de
   explicar por quê: sem cadastro local não existe mais fluxo que confirme uma conta, o
   provisionamento por SSO já grava `confirmed_at`, e deixar linhas nulas manteria contas legadas
   permanentemente barradas no realtime (que rejeita `confirmed_at is None`) e alimentaria um alerta
   que não tem mais como ser resolvido.
2. `op.drop_constraint("uq_auth_users_cpf", "auth_users", type_="unique")` (criada na 029).
3. `op.drop_column` de `phone`, `cpf`, `user_name`, `password_hash`.
4. `op.drop_index(op.f("ix_auth_otp_challenges_email"), table_name="auth_otp_challenges")` e
   `op.drop_table("auth_otp_challenges")`. Conferir antes, no banco, quais índices a tabela ainda
   tem: a 027 já removeu `ix_auth_otp_challenges_id` e `ix_auth_otp_challenges_purpose`, então só o
   de `email` deve existir. Nenhuma FK de outra tabela aponta para `auth_otp_challenges` — confirmar
   com uma consulta a `pg_constraint` antes de escrever, e registrar a confirmação no SUMMARY.

`downgrade()` restaura a ESTRUTURA no formato em que ela estava na 034, não os dados (hashes de
senha e códigos OTP não voltam — dizer isso explicitamente no docstring): recria
`auth_otp_challenges` com `id` (PK autoincrement), `email` (String(255), not null) + índice
`ix_auth_otp_challenges_email`, `code` **String(64)** (a 030 alargou para caber o digest HMAC),
`purpose` (String(32), not null), `expires_at` (timestamptz not null), `created_at` (timestamptz not
null com `server_default=now()`); e recria em `auth_users` as colunas `password_hash` (String(255),
**nullable** — a 031 já a tinha liberado), `user_name` (String(255) nullable), `cpf` (String(11)
nullable), `phone` (String(20) nullable) e a unique `uq_auth_users_cpf`. O backfill de `confirmed_at`
não é revertido (não há como saber quais linhas eram nulas) — dizer isso no docstring.

Criar `app/tests/test_migration_035_auth_remove_login_local.py` copiando a estrutura de
`test_migration_033_pedido_indica_blacklist.py` (helpers `_isolated_database_url`, `_execute`,
`_reset`, `_alembic`, fixture `migration_database_url`; o arquivo pula sozinho sem
`MIGRATION_TEST_DATABASE_URL`, que é o que o CI injeta). Um teste só, com 4 fases:
1. `upgrade 034`, inserir duas linhas em `auth_users` (uma com `confirmed_at` nulo, outra
   preenchida) e uma linha em `auth_otp_challenges`; `upgrade 035`; assertar `version_num == 035`.
2. As 4 colunas sumiram de `information_schema.columns` para `auth_users`; `auth_otp_challenges`
   sumiu de `information_schema.tables`; a linha que tinha `confirmed_at` nulo agora tem valor e a
   que já tinha valor não foi alterada (provar o backfill sem falso-positivo).
3. `downgrade 034`: as 4 colunas voltam (todas nulláveis), `auth_otp_challenges` volta com a coluna
   `code` de tamanho 64 e o índice `ix_auth_otp_challenges_email` existe.
4. `upgrade 035` de novo é limpo (re-upgrade sem erro, `version_num == 035`).

Rodar a migration nos DOIS bancos (ver o bloco "Dois bancos" no `<context>`): o de aplicação e o
`system_automation_test`. Depois, `alembic check` tem de sair limpo — é o passo que prova que o model
editado na Task 3 e a migration escrita aqui contam a mesma história.

Commits:
- `feat(db): migration 035 remove colunas de senha e a tabela de OTP`
- `test(db): ciclo destrutivo da migration 035`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run alembic check && docker compose -f .docker/docker-compose.yml exec -T api env MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/automation_migration_test_local uv run pytest app/tests/test_migration_035_auth_remove_login_local.py -q 2>&1 | tail -15</automated> <!-- pragma: allowlist secret -->
  </verify>
  <done>
`alembic heads` retorna `035` e `alembic current` mostra `035` tanto no banco de aplicação quanto em
`system_automation_test` (verificado com `DATABASE_URL` explícito em cada um, não deduzido). `alembic check`
limpo. O teste de migration passa (ou pula com uma mensagem clara se o banco descartável não puder
ser criado localmente — nesse caso, anotar no SUMMARY que a validação real fica para o CI e provar
o upgrade/downgrade à mão pelo menos uma vez). `\d auth_users` não lista `password_hash`,
`user_name`, `cpf` nem `phone`; `auth_otp_challenges` não existe. 2 commits com caminhos explícitos.
  </done>
</task>

<task type="auto">
  <name>Task 5: Cirurgia nos testes de auth — apagar os exclusivos, adaptar os compartilhados</name>
  <files>app/tests/test_auth.py, app/tests/test_auth_otp_hash.py, app/tests/test_auth_otp_email.py, app/tests/test_auth_rate_limit.py, app/tests/test_settings_seed.py, app/tests/test_migration_030_auth_otp_code_hash.py, app/tests/conftest.py, app/tests/test_auth_flows.py, app/tests/test_auth_sessao_revogacao.py, app/tests/test_auth_reuso_refresh_token.py</files>
  <action>
**Apagar inteiros** (`git rm`), todos 100% exclusivos de senha/OTP/seed:
`test_auth.py` (seed), `test_auth_otp_hash.py`, `test_auth_otp_email.py`,
`test_migration_030_auth_otp_code_hash.py`, `test_settings_seed.py`, `test_auth_rate_limit.py`
(SEC-07 + SEC-08, todos contra endpoints que não existem mais — só apagar depois do OK da Task 2).

**`conftest.py`**: remover a classe `_FakeAuthRateRedis` e a fixture autouse `auth_rate_limit_redis`
(sem chamador, e o `monkeypatch.setattr` mira um símbolo que `routes.py` não importa mais — sem
remover, TODO teste da suíte quebra com `AttributeError`). Remover
`os.environ.setdefault("SEED_AUTH_ON_STARTUP", "false")`. **Manter** o
`os.environ.setdefault("ENV", "dev")`, mas reescrever o comentário: a justificativa atual é a
política de OTP, que deixou de existir; a razão que sobra é que `ENV` é resolvido em tempo de import
e governa `postgres_ssl_mode` e as travas de PROD. Manter intactas as fixtures `client` e
`_dispose_db_engine_after_test`.

**`test_auth_flows.py`** — o arquivo fica, bem menor. Remover:
- helpers `_cpf_unico`, `_otp_existe_async`, `otp_existe`, `ler_otp_do_log`,
  `_remover_usuarios_nao_confirmados_async` e a fixture autouse `_limpar_usuarios_nao_confirmados`
  (a armadilha que ela cobria deixa de existir: nada mais cria usuário não confirmado);
- os blocos de teste de sign-in (3), register (7), confirm_register (2), recuperação de senha (3) e
  confirm-new-password (2);
- `test_hash_password_roundtrip`;
- do `test_tokens_roundtrip_e_rejeicao_por_tipo`, só a parte de session token (o roundtrip de
  access/refresh e a rejeição por tipo ficam).
Adaptar o que fica:
- `_criar_usuario_async`/`criar_usuario` perdem o parâmetro `password` e o `password_hash`;
- criar um helper local `token_de(user_id, email, roles)` que devolve
  `create_access_token(user_id=user_id, email=email, roles=list(roles))[0]`, e trocar por ele todas
  as ~12 chamadas a `POST /api/auth/sign-in` que só serviam para obter token (refresh, sign-out,
  `/users`, papéis, exclusão de usuário). O INSERT do usuário continua obrigatório: `get_current_user`
  relê os papéis do banco.
- Acrescentar **um teste novo** de regressão, no topo do arquivo:
  `test_endpoints_de_login_local_nao_existem_mais` — faz POST nos 6 caminhos removidos e assere 404
  em todos. É o gate contra reintrodução acidental.

**`test_auth_sessao_revogacao.py`** e **`test_auth_reuso_refresh_token.py`** ficam (cobrem SEC-06 e a
detecção de reuso de jti, que continuam vivos), mas obtêm tokens por `POST /sign-in`. Trocar por
emissão direta: `create_refresh_token(user_id=..., email=..., roles=[...])` para o refresh e
`create_access_token(...)` para o access; tirar `hash_password`/`password_hash` dos helpers de
criação de usuário. Dois cuidados que não podem se perder na tradução:
- em `test_auth_sessao_revogacao.py`, o teste que provava "um sign-in NOVO continua liberado depois
  do corte" vira "um refresh token novo, emitido depois do corte, continua válido" — o ponto é que o
  corte de sign-out não bloqueia a conta, e esse ponto tem de continuar sendo provado. Renomear o
  teste e ajustar o docstring para não mentir sobre o que ele mede;
- os `time.sleep(1.1)` existentes ficam, com o comentário que explica o motivo (resolução de 1s do
  `iat` frente à comparação `<` estrita). Emitir o token direto não muda isso.

Rodar só os arquivos de auth ao fim e conferir que passam. Se algum teste que sobrou depender de
comportamento removido, **parar e reportar** — pode ser sinal de que algo compartilhado foi cortado
junto.

Commits:
- `test(auth): remove suites exclusivas de senha, OTP e seed`
- `test(auth): emite token direto no lugar do sign-in por senha`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_flows.py app/tests/test_auth_sessao_revogacao.py app/tests/test_auth_reuso_refresh_token.py -q 2>&1 | tail -20</automated>
  </verify>
  <done>
Os 3 arquivos de auth que sobraram passam inteiros. Os 6 arquivos exclusivos não existem mais.
`grep -rn "auth_rate_limit_redis\|_FakeAuthRateRedis" app/tests/` sai vazio.
`grep -rn "api/auth/sign-in\|api/auth/register\|password/recovery" app/tests/test_auth_flows.py`
só casa dentro do teste novo de 404. `test_endpoints_de_login_local_nao_existem_mais` existe e
passa. 2 commits com caminhos explícitos.
  </done>
</task>

<task type="auto">
  <name>Task 6: Cobertura do SSO — o único login que sobra não pode ficar sem teste</name>
  <files>app/tests/test_auth_sso_microsoft.py</files>
  <action>
Criar `app/tests/test_auth_sso_microsoft.py`. Justificativa no docstring do módulo: até esta task o
endpoint `POST /api/auth/sso/microsoft` não tinha nenhum teste (conferido por grep — os matches de
"sso" na suíte eram "sucesso"/"acesso"), e ele passa a ser o único caminho de autenticação da
aplicação.

Reaproveitar os helpers de setup dos outros arquivos de auth (`_email_unico`, `_nova_sessao_isolada`,
engine descartável com `NullPool` + `asyncio.run`) duplicando-os aqui com o comentário de sempre —
nenhum arquivo de teste importa de outro neste projeto, e extrair para um módulo comum sairia do
escopo.

A validação do ID token bate na Microsoft de verdade. Isolar com `monkeypatch.setattr` de
`app.modules.auth.application.casos_uso.validar_id_token_microsoft` por um fake async que devolve o
payload desejado ou levanta a exceção desejada — mirar o símbolo importado em `casos_uso`, não o
módulo `microsoft_sso`. Configurar `MICROSOFT_TENANT_ID`/`MICROSOFT_CLIENT_ID` via monkeypatch das
settings, lembrando de limpar o cache de `get_settings` se ele for `lru_cache` (conferir no
`settings.py`; se for, `get_settings.cache_clear()` no setup e no teardown).

Cinco testes:
1. `test_sso_sem_configuracao_retorna_503` — sem tenant/client id, resposta 503 com detail
   `"Microsoft SSO not configured"`, e o validador nunca é chamado.
2. `test_sso_com_id_token_invalido_retorna_401` — fake levanta `TokenMicrosoftInvalidoError`;
   espera 401 `"Invalid Microsoft token"`.
3. `test_sso_sem_email_no_token_retorna_403` — payload sem `email` e sem `preferred_username`;
   espera 403 `"Account not authorized"`.
4. `test_primeiro_login_provisiona_conta_com_papel_minimo` — e-mail inédito; espera 200, um
   `access_token` utilizável e `user.roles == ["basico"]`; ler a linha no banco e assertar
   `auth_provider == "microsoft"`, `confirmed_at` preenchido e e-mail normalizado (passar o e-mail
   em MAIÚSCULAS na claim para provar a normalização). Assertar também que a resposta **não** traz
   `challenge` (campo removido) — é o gate contra reintrodução do desafio de sign-up.
5. `test_login_de_conta_existente_preserva_papeis` — criar antes um usuário com papel
   `administrador`; logar por SSO com o mesmo e-mail; espera 200 e `roles == ["administrador"]` (o
   SSO não rebaixa quem já foi promovido). Usar o token devolvido em `GET /api/auth/users` para
   provar de ponta a ponta que o token do SSO abre uma rota protegida por RBAC.

Não chamar nenhum endpoint removido e não criar usuário com `confirmed_at` nulo.

Commit: `test(auth): cobre o login por SSO Microsoft, unico caminho de autenticacao`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_sso_microsoft.py -q 2>&1 | tail -20</automated>
  </verify>
  <done>
Os 5 testes passam. `grep -c "validar_id_token_microsoft" app/tests/test_auth_sso_microsoft.py` é
maior que 0 (o mock existe) e nenhum teste faz chamada de rede real. O teste 5 prova que o token do
SSO abre `GET /api/auth/users`. 1 commit com caminho explícito.
  </done>
</task>

<task type="auto">
  <name>Task 7: Adaptar os testes dos outros módulos que dependiam do sign-in por senha</name>
  <files>app/tests/test_parametros.py, app/tests/test_pedidos_routes.py, app/tests/test_comunicacoes.py, app/tests/test_comunicacoes_delivery_repository.py, app/tests/test_request_body_limit.py, app/tests/test_schema_guard.py, app/tests/test_migration_022_communication_delivery.py, app/tests/test_database_options.py, app/tests/test_metrics_guard.py</files>
  <action>
Nenhum destes testa autenticação — todos só precisavam de um token ou de uma linha em `auth_users`.
Mudança mínima em cada um, sem alterar a asserção que o teste realmente faz:

1. `test_parametros.py`: tirar o import de `hash_password` e o `password_hash` do INSERT; trocar
   `token_para(client, email)` por emissão direta com
   `create_access_token(user_id=..., email=..., roles=[papel])[0]`. O helper `criar_usuario` já
   devolve `(user_id, email)`, então a fixture `tokens` só precisa passar os dois adiante.
2. `test_pedidos_routes.py`: mesma troca; aqui `criar_usuario` devolve só o e-mail — passar a
   devolver `(user_id, email)` e ajustar as fixtures `viewer_token`/`actor_token`.
3. `test_comunicacoes.py` e `test_comunicacoes_delivery_repository.py`: remover o kwarg
   `password_hash="not-used-in-tests"` do INSERT de `AuthUser`. Nada mais. <!-- pragma: allowlist secret -->
4. `test_request_body_limit.py`: em `test_fiacao_real_413_sai_com_cabecalhos_de_cors`, trocar
   `/api/auth/sign-in` por `/api/auth/sso/microsoft` (também público, também POST — o middleware
   rejeita por tamanho antes de chegar à rota, então a asserção não muda). Ajustar o comentário se
   ele citar sign-in.
5. `test_schema_guard.py`: tirar `AuthOtpChallenge` do import de models, mantendo `AuthUser` e o
   `# noqa: F401`. Não tocar nos outros imports (é o mesmo registro de `Base.metadata` do
   `alembic/env.py`).
6. `test_migration_022_communication_delivery.py`: tirar `password_hash` do INSERT cru em
   `auth_users` (e o valor correspondente na lista de valores). Cuidado: esse teste roda contra o
   banco descartável de migrations, num ponto da linha do tempo em que a coluna AINDA EXISTE. Ler o
   teste antes de mudar: se ele faz `upgrade` até uma revisão anterior à 035, a coluna existe e é
   `NOT NULL` até a 031 — nesse caso o INSERT precisa continuar informando a coluna. Só remover se o
   teste chegar a rodar contra o schema pós-035. Decidir lendo o código, e registrar a decisão no
   SUMMARY.
7. `test_database_options.py`: tirar `"SEED_AUTH_ON_STARTUP"` do conjunto `exact` e o kwarg
   `SEED_AUTH_ON_STARTUP="false"` da chamada de `_settings_prod_process`.
8. `test_metrics_guard.py`: tirar o `monkeypatch.delenv("SEED_AUTH_ON_STARTUP", ...)` e a menção no
   docstring de `_monkeypatch_prod_remoto`.

Commit: `test: adapta testes de outros modulos a autenticacao so por SSO`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_parametros.py app/tests/test_pedidos_routes.py app/tests/test_comunicacoes.py app/tests/test_comunicacoes_delivery_repository.py app/tests/test_request_body_limit.py app/tests/test_schema_guard.py app/tests/test_database_options.py app/tests/test_metrics_guard.py -q 2>&1 | tail -20</automated>
  </verify>
  <done>
Os 8 arquivos passam. `grep -rn "api/auth/sign-in\|hash_password\|password_hash" app/tests/` só
retorna, no máximo, o INSERT justificado de `test_migration_022` (com a razão registrada no SUMMARY)
e o teste de 404 de `test_auth_flows.py`. Nenhuma asserção de negócio foi alterada — só a forma de
obter token e de criar a linha de usuário. 1 commit com caminhos explícitos.
  </done>
</task>

<task type="auto">
  <name>Task 8: Documentação, gate final da suíte e SUMMARY</name>
  <files>docs/seguranca.md, app/tests/test_docs_seguranca.py, docs/api.md, docs/index.md, docs/arquitetura.md, README.md, .claude/CLAUDE.md, .planning/REQUIREMENTS.md</files>
  <action>
Antes de tocar em `docs/seguranca.md` e `.planning/REQUIREMENTS.md`, reconferir `git status --short`:
os dois estavam modificados por outra sessão no momento do planejamento (ver item (d) do checkpoint).
Se ainda estiverem, seguir o que a mantenedora decidiu ali.

**`docs/seguranca.md` — reescrever, preservando o que continua verdade.** Hoje o documento é quase
todo sobre as 5 contas seed e a rotação de senha por OTP. Duas partes NÃO podem sumir: a seção
`## Rotação da chave de métricas (METRICS_API_KEY)` (entrou pela quick task 260827-emo, já commitada)
e o bloco de comportamento de FK antes de excluir um usuário (vale para qualquer usuário, de
qualquer provider). Manter os 4 títulos que `test_docs_seguranca.py` exige — `## Detecção`,
`## Rotação`, `## Remoção`, `## Prevenção` (o de métricas já começa com "## Rotação" e satisfaz o
gate). Estrutura nova:
- abertura: a autenticação é exclusivamente Microsoft Entra ID (single-tenant); não existe senha
  local, cadastro público, OTP nem recuperação de senha; conta nova nasce no primeiro login com
  papel `basico` e é promovida por `PUT /api/auth/users/{id}/roles`;
- `## Detecção`: a query passa a ser
  `SELECT id, email, roles, auth_provider, confirmed_at, created_at FROM auth_users WHERE auth_provider = 'local' ORDER BY id;`
  — são as contas legadas do tempo da senha, incluindo as 5 seed. **Sem repetir e-mail nem senha
  nenhuma no texto**;
- `## Remoção`: essas contas não conseguem mais autenticar (não há caminho de senha), mas devem ser
  removidas mesmo assim, porque o login por SSO casa por e-mail: se alguém no tenant controlar um
  endereço igual ao de uma conta legada com papel elevado, herda o papel dessa linha. Manter o
  `DELETE` e o bloco de FK a checar antes (`comunicacoes.requested_by_user_id` ON DELETE SET NULL;
  `realtime_read_cursors.user_id` ON DELETE CASCADE; `parametro_change_requests.requested_by` NOT
  NULL sem ON DELETE, que bloqueia o DELETE). Como a rotação de senha deixou de existir, a saída
  para o caso bloqueado por FK deixa de ser "rotacione" e passa a ser rebaixar o papel para `basico`
  via `PUT /users/{id}/roles`;
- `## Rotação`: só a da chave de métricas, preservada como está;
- `## Prevenção`: controles vigentes — `JWT_SECRET` forte obrigatório em PROD (SEC-01), revogação de
  sessão no sign-out (SEC-06), detecção de reuso de refresh token por `jti`, teto absoluto de
  sessão, e o fato de que a autorização de quem entra é do Entra ID. Registrar que SEC-02/03/04/05
  (seed, OTP por e-mail, runbook de seed, hash de OTP) deixaram de se aplicar porque o objeto que
  protegiam não existe mais, e o mesmo para SEC-07/SEC-08 se o lockout tiver saído;
- `## Checklist por ambiente`: adaptar aos passos novos.

**`app/tests/test_docs_seguranca.py`**: remover o import de `DEFAULT_AUTH_USERS` e o teste
`test_runbook_cobre_todas_as_contas_seed`. Manter os testes de seções obrigatórias e do link no
index. Acrescentar um teste **negativo** que impede a reintrodução: nenhuma das 5 senhas antigas
(`basico123`, `oper123`, `gestor123`, `admin123`, `admintec123`) pode aparecer em `docs/seguranca.md`.
Escrever as strings de forma que o `detect-secrets` do pre-commit não as trate como segredo novo (se
o hook reclamar, ajustar o baseline com `uv run detect-secrets scan --baseline .secrets.baseline`,
conferindo que nenhuma entrada de OUTRO arquivo foi perdida).

**Demais docs:**
- `docs/api.md:185`: a descrição de `/api/auth/*` passa a "Login por SSO Microsoft, refresh/logout e
  usuários/papéis" (sai OTP e recuperação);
- `docs/index.md:17`: a linha da tabela que descreve `seguranca.md` — trocar "Usuários seed,
  rotação/remoção, hash de OTP..." pelo conteúdo novo;
- `docs/arquitetura.md`: linha ~14 da árvore de diretórios (`auth/ # JWT, OTP, usuários e RBAC`) e o
  parágrafo ~200 que cita o fail-fast de PROD por seed de usuários;
- `README.md`: remover as 4 linhas de rota de senha/OTP da tabela de Auth, remover a seção
  `## Auth — seed automático` inteira (é a tabela de senhas em texto claro) substituindo por uma
  nota curta sobre SSO + promoção de papel, remover a frase "Códigos OTP de cadastro/recuperação são
  registrados no log da API", e limpar as menções a `SEED_AUTH_ON_STARTUP` nas seções de deploy;
- `.claude/CLAUDE.md`: a linha do stack ("Auth: JWT (python-jose, HS256) + bcrypt") e a descrição do
  módulo auth ("login, JWT sign/refresh, RBAC ...; seed de usuários e recuperação de senha") —
  atualizar as duas para o estado novo;
- `.planning/REQUIREMENTS.md`: **não apagar linha de requisito**; anotar em SEC-02, SEC-03, SEC-04,
  SEC-05 e SEC-07 (e SEC-08, se existir) que passaram a "N/A desde a remoção do login local (quick
  260827-fqf)", preservando o histórico do que foi feito. Ajustar a linha de contagem se ela ficar
  inconsistente.

**Gate final**, nesta ordem, tudo pelo container:
1. `uv run ruff check app alembic`
2. `uv run ruff format --check app alembic`
3. `uv run pyright app alembic`
4. `uv run alembic check`
5. `uv run pytest -q` — comparar com o baseline da Task 1. Critério: **nenhuma falha nova**. O
   número de `passed` cai (as suítes de senha saíram) e sobem 5 (SSO) + 1 (404 dos endpoints) + 1
   (migration 035) + 1 (senhas fora do doc). É plausível que uma falha do baseline (a de
   `test_pedidos_read_projection`, ligada a usuário `confirmed_at IS NULL` órfão) **desapareça** —
   se isso acontecer, registrar como efeito colateral positivo, não como anomalia.
   Se aparecer falha nova, diagnosticar antes de qualquer commit; a pista mais provável é um símbolo
   compartilhado removido junto por engano.
6. `git status --short`: provar que o trabalho não commitado de outras sessões segue como na foto da
   Task 1, e que nenhum commit desta task incluiu arquivo alheio (`git show --stat` em cada um).

**SUMMARY** em `.planning/quick/260827-fqf-remover-por-completo-o-login-por-e-mail-/260827-fqf-SUMMARY.md`,
registrando obrigatoriamente: (a) baseline e números finais da suíte, com a lista de falhas antes e
depois; (b) o destino do SEC-08/lockout e o que a mantenedora decidiu no checkpoint; (c) as mudanças
de contrato da API (`challenge`, `display_name`, `cpf`, `phone`) como pendência de coordenação com o
frontend; (d) que o alerta operacional `acesso` saiu e por quê; (e) que a migration 035 é
destrutiva e o downgrade restaura estrutura, não dados; (f) que `system_automation_test` precisou de
`alembic upgrade head` à mão; (g) que o SSO ganhou cobertura de teste que não existia; (h) o status
novo de SEC-02/03/04/05/07(/08) em REQUIREMENTS.md.

Commits:
- `docs(seguranca): reescreve runbook para autenticacao so por SSO`
- `docs: remove referencias a login por senha, seed e OTP`
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app alembic && docker compose -f .docker/docker-compose.yml exec -T api uv run ruff format --check app alembic && docker compose -f .docker/docker-compose.yml exec -T api uv run pyright app alembic && docker compose -f .docker/docker-compose.yml exec -T api uv run alembic check && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -15</automated>
  </verify>
  <done>
Os 5 gates passam e a suíte fecha sem NENHUMA falha nova em relação ao baseline da Task 1.
`grep -rn "basico123\|oper123\|gestor123\|admin123\|admintec123" . --exclude-dir=.git --exclude-dir=.planning --exclude-dir=.venv --exclude-dir=node_modules` só casa dentro do teste negativo novo.
`grep -rn "OTP\|recuperação de senha\|seed de usuários" docs/ README.md .claude/CLAUDE.md` sai vazio
(fora de menções históricas explicitamente datadas). `git status --short` confirma o trabalho de
outras sessões intacto. SUMMARY escrito com os 8 itens obrigatórios.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| cliente → `POST /api/auth/sso/microsoft` | Único ponto de entrada de identidade que sobra; o ID token é assinado pela Microsoft e validado contra o JWKS do tenant |
| cliente → `POST /api/auth/token/refresh` | Refresh token assinado em HS256 pelo próprio backend; a assinatura prova origem, não posse legítima |
| aplicação → Postgres (`auth_users`) | O e-mail da linha é a chave de correspondência entre a identidade do Entra ID e os papéis locais |
| operação → migration destrutiva 035 | Drop irreversível de colunas e tabela com dado de credencial |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-FQF-01 | Spoofing | sign-in por senha, cadastro, OTP e recuperação | mitigate | Removidos por completo: some toda a superfície de adivinhação de segredo do backend (senha e código de 6 dígitos). O que sobra como entrada é um ID token assinado pela Microsoft, validado por JWKS, emissor e audiência em `microsoft_sso.py` |
| T-FQF-02 | Elevation of Privilege | contas locais legadas com papel elevado + login SSO que casa por e-mail | mitigate | O SSO localiza a conta por e-mail: uma linha legada com papel `administrador` seria herdada por quem controlasse aquele endereço no tenant. Mitigação operacional obrigatória no runbook reescrito (`docs/seguranca.md`, seções Detecção e Remoção): listar `auth_provider = 'local'` e remover, ou rebaixar para `basico` quando a FK de `parametro_change_requests` bloquear o DELETE |
| T-FQF-03 | Elevation of Privilege | backfill `confirmed_at = now()` em contas nunca confirmadas | accept | Contas legadas não confirmadas passam a ser aceitas pelo guard de `confirmed_at` do realtime. Aceito porque elas continuam sem qualquer forma de autenticar sem uma identidade válida no Entra ID, e seus papéis não mudam. O risco residual real é o T-FQF-02, endereçado pelo runbook |
| T-FQF-04 | Denial of Service | fim do rate limit (SEC-07) e do lockout (SEC-08) nos endpoints de auth | accept | Os dois protegiam segredo adivinhável. Nenhum endpoint restante aceita segredo adivinhável: `/sso/microsoft` recebe token assinado pela Microsoft e `/token/refresh` recebe token assinado com o `JWT_SECRET`. `app/shared/infrastructure/rate_limit.py` fica no repositório (usado por `pedidos` e `comunicacoes`) e pode ser religado em auth numa tarefa própria se algum endpoint voltar a aceitar segredo adivinhável |
| T-FQF-05 | Tampering | migration 035 (drop de colunas e da tabela de OTP) | mitigate | Perda de dado é o objetivo (hashes de senha e códigos OTP), mas o drop é irreversível: `downgrade()` restaura estrutura, não conteúdo, e diz isso no docstring. Teste de ciclo destrutivo em banco descartável (Task 4) prova upgrade → downgrade → re-upgrade antes de tocar em ambiente real; `alembic check` prova a paridade com o model |
| T-FQF-06 | Information Disclosure | 5 senhas em texto claro versionadas em `seed.py`, `docs/seguranca.md` e `README.md` | mitigate | Todas saem do repositório. Teste negativo novo em `test_docs_seguranca.py` impede a reintrodução em `docs/seguranca.md`; `gitleaks` e `detect-secrets` seguem como gate de CI/pre-commit. As senhas continuam no histórico do git — fora do escopo desta task (existe `scripts/purge-secrets-from-history.sh` se virar decisão) |
| T-FQF-07 | Repudiation | remoção acidental de código compartilhado (RBAC, JWT, sessão) junto com o de senha | mitigate | Gates estruturais no `<done>` de cada task (contagem de rotas, greps de símbolo, `import app.main`), suíte de sessão/reuso de jti mantida e adaptada em vez de apagada, e cobertura nova de SSO ponta a ponta atravessando `get_current_user` numa rota de admin |
| T-FQF-SC | Tampering | npm/pip/cargo installs | n/a | Nenhuma dependência nova. A única mudança em `pyproject.toml` é **remover** `bcrypt`, com `uv lock` para manter o `uv.lock` coerente com o `uv sync --frozen` do CI. Nenhum pacote a auditar |
</threat_model>

<verification>
1. `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` — sem falha nova em relação ao baseline da Task 1.
2. `... uv run ruff check app alembic`, `... uv run ruff format --check app alembic`, `... uv run pyright app alembic` — limpos.
3. `... uv run alembic check` — sem drift entre model e schema (é gate do CI).
4. `... uv run pytest app/tests/test_auth_sso_microsoft.py -q` — 5 passed; o único login da aplicação está coberto.
5. `test_endpoints_de_login_local_nao_existem_mais` — 404 nos 6 caminhos removidos.
6. `grep -rn "hash_password\|verify_password\|OtpPurpose\|AuthOtpChallenge\|repositorio_otp\|otp_email\|seed_auth\|create_session_token\|buscar_por_cpf\|bcrypt" --include="*.py" --include="*.toml" app/ alembic/ pyproject.toml | grep -v __pycache__` — vazio.
7. `\d auth_users` sem `password_hash`/`user_name`/`cpf`/`phone` e `auth_otp_challenges` inexistente, confirmado nos DOIS bancos (`system_automation` e `system_automation_test`) com `DATABASE_URL` explícito em cada.
8. `git status --short` — o trabalho não commitado de outras sessões segue exatamente como registrado na Task 1; `git show --stat` de cada commit desta task lista só arquivos desta task.
</verification>

<success_criteria>
- Não existe mais nenhuma forma de autenticar por e-mail e senha no backend: os 6 endpoints somem, o
  domínio de senha/OTP some, o seed some, e há teste automatizado provando o 404.
- `POST /api/auth/sso/microsoft` é o único login, continua provisionando com papel mínimo e ganhou
  cobertura de teste ponta a ponta que não existia antes.
- Sessão (refresh, sign-out, revogação, detecção de reuso de jti), RBAC e gestão de usuários seguem
  funcionando, provados pelos testes existentes adaptados — não reescritos.
- Schema sem `password_hash`/`user_name`/`cpf`/`phone` e sem `auth_otp_challenges`, nos dois bancos,
  com `alembic check` limpo e ciclo de migration testado em banco descartável.
- Zero código morto: nenhum símbolo, import, setting, variável de ambiente, dependência, arquivo de
  teste ou trecho de documentação sobrevive apontando para o login local.
- Suíte sem falha nova em relação ao baseline medido; `ruff check`, `ruff format --check` e `pyright`
  limpos.
- Commits atômicos, Conventional Commits, caminhos explícitos, sem menção a Claude, sem `push`, e
  sem tocar no trabalho não commitado das outras sessões.
</success_criteria>

<output>
Criar `.planning/quick/260827-fqf-remover-por-completo-o-login-por-e-mail-/260827-fqf-SUMMARY.md`
ao terminar.
</output>
</content>
