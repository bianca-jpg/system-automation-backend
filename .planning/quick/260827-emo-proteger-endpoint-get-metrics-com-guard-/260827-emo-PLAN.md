---
phase: quick/260827-emo
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/shared/config/settings.py
  - app/shared/metrics/security.py
  - app/shared/metrics/router.py
  - app/tests/test_metrics_guard.py
  - .env.example
  - docs/api.md
  - docs/seguranca.md
autonomous: true
requirements: [QUICK-260827-emo]
user_setup:
  - service: metrics-scraper
    why: "O scraper Prometheus precisa mandar o header X-Metrics-Key; o valor tem de existir no .env de cada ambiente"
    env_vars:
      - name: METRICS_API_KEY
        source: "Gerar com `openssl rand -hex 32` e guardar no .env (gitignored) / secret manager do ambiente. Sem ele, GET /metrics responde 503 e o boot em PROD falha."

must_haves:
  truths:
    - "GET /metrics sem o header X-Metrics-Key responde 401 e não devolve nenhum corpo de métricas"
    - "GET /metrics com X-Metrics-Key de valor errado responde 403"
    - "GET /metrics com X-Metrics-Key correto responde 200 com o content-type do Prometheus"
    - "Com METRICS_API_KEY vazio o endpoint responde 503 — nunca devolve métricas sem chave configurada"
    - "Boot em PROD falha (ValidationError) quando METRICS_API_KEY está ausente ou tem menos de 32 caracteres"
    - "A comparação da chave é timing-safe (hmac.compare_digest), não `==`"
    - "/health e /health/ready seguem públicos e inalterados"
  artifacts:
    - path: "app/shared/metrics/security.py"
      provides: "Dependência FastAPI require_metrics_key (guard por API key de header)"
      exports: ["require_metrics_key", "METRICS_API_KEY_HEADER"]
      min_lines: 25
    - path: "app/shared/metrics/router.py"
      provides: "Router de /metrics com o guard declarado no APIRouter"
      contains: "dependencies=[Depends(require_metrics_key)]"
    - path: "app/shared/config/settings.py"
      provides: "Campo metrics_api_key + fail-fast de PROD"
      contains: "metrics_api_key"
    - path: "app/tests/test_metrics_guard.py"
      provides: "Cobertura 401/403/200/503 + fail-fast de PROD + gate de compare_digest"
      min_lines: 60
    - path: ".env.example"
      provides: "Documentação da variável METRICS_API_KEY"
      contains: "METRICS_API_KEY"
    - path: "docs/api.md"
      provides: "Tabela de rotas com /metrics deixando de ser público"
      contains: "X-Metrics-Key"
  key_links:
    - from: "app/shared/metrics/router.py"
      to: "app/shared/metrics/security.py"
      via: "APIRouter(dependencies=[Depends(require_metrics_key)])"
      pattern: "dependencies=\\[Depends\\(require_metrics_key\\)\\]"
    - from: "app/shared/metrics/security.py"
      to: "app/shared/config/settings.py"
      via: "get_settings().metrics_api_key lido a cada request"
      pattern: "get_settings\\(\\)\\.metrics_api_key"
    - from: "app/shared/metrics/security.py"
      to: "hmac.compare_digest"
      via: "comparação timing-safe da chave"
      pattern: "compare_digest"
---

<objective>
Fechar o endpoint `GET /metrics` (Prometheus), hoje público em `app/shared/metrics/router.py`, atrás de um
guard leve por API key de header (`X-Metrics-Key`) validado contra `METRICS_API_KEY` vindo do `Settings`.

Purpose: as métricas expõem telemetria interna (conexões WebSocket do realtime, backlog do outbox,
resultados/duração de SMTP das comunicações) para qualquer cliente que alcance a porta 8000. É
reconhecimento gratuito de superfície interna. O scraper Prometheus não tem usuário/JWT, então os guards
RBAC hierárquicos (`require_viewer`…`require_tecnico`) não servem aqui — a via correta é chave fixa.

Output: novo módulo `app/shared/metrics/security.py` com a dependência `require_metrics_key`, campo
`metrics_api_key` no `Settings` com fail-fast de PROD, guard declarado no `APIRouter` de metrics, suíte
`app/tests/test_metrics_guard.py` e docs (`.env.example`, `docs/api.md`, `docs/seguranca.md`) atualizados.
</objective>

<decisoes_travadas>
Restrições vindas da investigação prévia (não renegociar durante a execução):

| ID | Decisão | Nota |
|----|---------|------|
| D-01 | Guard leve por **API key fixa em header** (`X-Metrics-Key`), não RBAC de usuário | Scraper Prometheus não tem JWT; `require_*` exigiria login |
| D-02 | O valor esperado vem de `settings.metrics_api_key` / env `METRICS_API_KEY`, seguindo o mesmo padrão `Field(default=..., validation_alias=...)` dos outros segredos | Consistência com `JWT_SECRET`, `SMTP_PASSWORD`, `DATABRICKS_TOKEN` |
| D-03 | **401** quando o header está ausente/vazio; **403** quando presente e inválido | Literal do pedido |
| D-04 | Comparação com `secrets`/`hmac.compare_digest` (timing-safe), nunca `==` | Precedente no repo: `app/modules/auth/domain/otp_hash.py`, `app/shared/pagination/cursor.py` |
| D-05 | **Sem rate limit** nesta tarefa | `app/shared/infrastructure/rate_limit.py` existe, mas está fora de escopo |
| D-06 | **Não tocar** em `/health` nem `/health/ready` | Tarefa separada; `app/modules/health/routes.py` fica intacto |

Discricionariedade do planner (documentada, não estava no pedido):

| ID | Decisão | Razão |
|----|---------|-------|
| D-07 | **Fail-closed**: chave não configurada (`METRICS_API_KEY` vazia) ⇒ **503**, jamais 200 | Precedente do próprio repo — `MICROSOFT_TENANT_ID`/`MICROSOFT_CLIENT_ID` vazios fazem `POST /api/auth/sso/microsoft` responder 503 (`settings.py:383-386`). Se o default fosse "sem chave = aberto", a proteção viraria opt-in e o buraco continuaria em qualquer ambiente que esquecesse a variável |
| D-08 | Fail-fast de boot em **PROD** exigindo `METRICS_API_KEY` com ≥ 32 caracteres | Mesma régua já aplicada a `JWT_SECRET` no `model_validator` (`settings.py:602-608`) |
| D-09 | O guard mora em `app/shared/metrics/security.py`, **não** em `app/shared/security/` | O docstring de `app/shared/security/__init__.py` declara que aquele pacote existe só para reexportar o RBAC do bounded context de auth; um guard de chave de infraestrutura não pertence a ele |
</decisoes_travadas>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.claude/CLAUDE.md

Arquivos de produção que esta tarefa toca:
@app/shared/metrics/router.py
@app/shared/config/settings.py

Padrões a imitar (ler antes de escrever):
@app/modules/auth/infrastructure/http/dependencies.py
@app/modules/core/routes.py
@app/tests/test_settings_seed.py
@app/tests/test_health.py

## Interfaces e fatos já confirmados (não reinvestigar)

**Estado atual do endpoint** — `app/shared/metrics/router.py` tem 9 linhas: cria
`router = APIRouter(tags=["metrics"])` e um `@router.get("/metrics")` sync que devolve
`Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)`. Sem prefix, sem `dependencies=`.
O router é importado em `app/modules/__init__.py:14` como `metrics_router` e entra em `all_routers`
(posição 2). `app/main.py:27-28` faz `app.include_router(router)` em loop, sem injetar dependências.

**`app/shared/metrics/` não tem `__init__.py`** (namespace package implícito — só `router.py` e
`__pycache__`). Criar `security.py` ali funciona pelo mesmo mecanismo; **não** criar `__init__.py`.

**Padrão de guard existente** (`app/modules/auth/infrastructure/http/dependencies.py`):
- scheme instanciado no módulo com `auto_error=False` — `bearer_scheme = HTTPBearer(auto_error=False)`
- helper privado `_unauthorized(detail)` devolve `HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, ...)`
- guards são `async def` que recebem o scheme via `Depends(...)` e levantam `HTTPException` com
  `status.HTTP_403_FORBIDDEN` quando autenticado mas sem permissão
- o módulo usa `from __future__ import annotations` e docstring em português explicando o porquê

**Padrão de declaração de guard em router** (`app/modules/core/routes.py:5`):
`router = APIRouter(prefix="/v1", tags=["core"], dependencies=[Depends(require_viewer)])` — a dependência
fica no construtor do `APIRouter`, não em cada decorator. É o padrão a seguir (protege por construção
qualquer rota futura adicionada ao router de metrics).

**Padrão de campo no `Settings`** (`app/shared/config/settings.py`) —
`Settings(BaseSettings)` com `model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")`.
Cada campo é `Field(default=..., validation_alias="NOME_DA_ENV")`. O bloco de segurança fica em
`settings.py:370-386` (`jwt_secret`, `jwt_algorithm`, `jwt_expire_minutes`, `auth_absolute_session_seconds`,
`microsoft_tenant_id`, `microsoft_client_id`), imediatamente antes de `cors_origins` (linha 388).
`get_settings()` é `lru_cache`d no fim do arquivo.

**Fail-fast de PROD** — `@model_validator(mode="after") def validate_cross_field_constraints(self)`
começa em `settings.py:528`. O bloco `if ENV == "PROD":` vai de 570 a 610, na ordem: DATABASE_URL/hosts
proibidos → Redis/Celery remotos → `jwt_secret` forte (≥ 32) → `seed_auth_on_startup` false.
`ENV` é módulo-level: `ENV = os.getenv("ENV", "dev").upper()` (`settings.py:11`).

⚠️ **Ordem importa:** `app/tests/test_settings_seed.py:34-49` monta um `Settings` em PROD **sem**
`METRICS_API_KEY` e espera `"SEED_AUTH_ON_STARTUP deve ser false em PROD"`. A nova checagem tem de vir
**depois** do `if self.seed_auth_on_startup:` para o teste existente continuar verde sem ser editado.

**Padrão de teste de settings** (`app/tests/test_settings_seed.py`): helper
`_settings(**overrides)` que faz `cast(Callable[..., Settings], Settings)(_env_file=None, **overrides)`,
`monkeypatch.setattr("app.shared.config.settings.ENV", "PROD")`, `monkeypatch.setenv(...)` para
DATABASE_URL/REDIS_URL/CELERY_*/JWT_SECRET e `pytest.raises(ValidationError)`. Copiar esse helper.

**Padrão de teste HTTP** — fixture `client` (TestClient) definida em `app/tests/conftest.py`;
`app/tests/test_health.py` usa `client.get(...)` + `patch("app.modules.health.routes.check_database", ...)`
**dentro** de um `with` ao redor da chamada. Patch sempre no *local de import* do módulo sob teste
(precedente: `patch("app.modules.ingestao.infrastructure.databricks_reader.get_settings", ...)`) — por isso
o guard tem de chamar `get_settings()` **dentro** da função, não no import.

**Gate de fonte para timing-safety** — precedente em `app/tests/test_auth_otp_hash.py:51`:
`assert "compare_digest" in inspect.getsource(verify_otp_code)`. Replicar para `require_metrics_key`.

**Nenhum teste atual bate em `/metrics`** (confirmado por grep em `app/tests/`): as ocorrências em
`test_comunicacoes_delivery.py` e `test_realtime_relay_ordering.py` são objetos locais chamados `metrics`,
não requisições HTTP. Ou seja, fechar o endpoint não quebra teste existente.

**Docs a corrigir** — `docs/api.md:189` hoje diz literalmente
``| `/metrics` | público | Métricas Prometheus, incluindo realtime |`` na tabela "Auth, saúde e métricas"
(colunas Prefixo | Papel | Descrição). `docs/seguranca.md` tem os títulos `## Detecção`,
`## Rotação (ambiente que ainda depende das contas)`, `## Remoção (recomendado em ambientes compartilhados)`,
`## Prevenção`, `## Checklist por ambiente` — `app/tests/test_docs_seguranca.py:18-26` afirma que existem
linhas começando com `## Detecção`, `## Rotação`, `## Remoção` e `## Prevenção`; **não renomear nenhum `##`**.
`.env.example` tem a seção `# Segurança (gere um JWT_SECRET forte em produção)` na linha 124, seguida de
`JWT_SECRET`/`JWT_ALGORITHM`/`JWT_EXPIRE_MINUTES` e do bloco de comentário do Microsoft SSO.

**Sem dependência nova:** `hmac`/`secrets`/`inspect` são stdlib; `fastapi.security.APIKeyHeader` já vem
com o FastAPI 0.115 instalado. Nenhum `uv add` nesta tarefa — logo não há gate de legitimidade de pacote.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: METRICS_API_KEY no Settings + fail-fast de PROD</name>
  <files>app/shared/config/settings.py, app/tests/test_metrics_guard.py</files>
  <behavior>
    - Sem a env `METRICS_API_KEY`, `Settings(_env_file=None).metrics_api_key` é `""` (default vazio, não quebra dev)
    - Com `METRICS_API_KEY` setada, o campo recebe o valor exato da env
    - Em PROD (via `monkeypatch.setattr("app.shared.config.settings.ENV", "PROD")` + as envs remotas que
      `test_settings_seed.py` já usa + `SEED_AUTH_ON_STARTUP` ausente/false), `METRICS_API_KEY` ausente
      levanta `ValidationError` com a mensagem "PROD exige METRICS_API_KEY com pelo menos 32 caracteres"
    - Mesmo cenário PROD com `METRICS_API_KEY` de 31 caracteres também levanta `ValidationError`
    - Mesmo cenário PROD com `METRICS_API_KEY` de 64 caracteres constrói o `Settings` sem erro
    - `app/tests/test_settings_seed.py` continua verde sem edição (a checagem nova vem depois da de seed)
  </behavior>
  <action>
    Adicionar em `Settings` o campo `metrics_api_key: str = Field(default="", validation_alias="METRICS_API_KEY")`
    logo depois de `microsoft_client_id` e antes de `cors_origins` (bloco de segurança, ~linha 386), com
    comentário curto em português no estilo do arquivo explicando: chave do scraper Prometheus, vazia =
    `GET /metrics` responde 503 (D-07), e que o valor mora só no `.env`/secret manager.

    No `validate_cross_field_constraints`, dentro do `if ENV == "PROD":`, **depois** da checagem
    `if self.seed_auth_on_startup:` (para não alterar a ordem de falha que `test_settings_seed.py` já
    afirma), acrescentar: se `len(self.metrics_api_key) < 32`, levantar `ValueError` com a mensagem
    "PROD exige METRICS_API_KEY com pelo menos 32 caracteres" (D-08). Um único `len(...) < 32` cobre também
    o caso vazio — não escrever duas condições.

    Criar `app/tests/test_metrics_guard.py` com docstring de módulo explicando o porquê da tarefa (endpoint
    era público) e a primeira metade dos testes: os cinco casos de `<behavior>` acima. Copiar literalmente o
    helper `_settings(**overrides)` de `test_settings_seed.py` (incluindo o `cast(Callable[..., Settings], Settings)`
    e o comentário sobre pyright) e o conjunto de `monkeypatch.setenv` de DATABASE_URL / REDIS_URL /
    CELERY_BROKER_URL / CELERY_RESULT_BACKEND / JWT_SECRET usado lá — sem esses valores o validator falha
    antes de chegar na checagem nova. Em cada teste de PROD, `monkeypatch.delenv("METRICS_API_KEY", raising=False)`
    antes de decidir o valor, porque o `.env` da máquina pode já ter a variável.

    Não editar `test_settings_seed.py`. Não tocar em `.env` (gitignored, é da máquina).
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_metrics_guard.py app/tests/test_settings_seed.py -q</automated>
  </verify>
  <done>`metrics_api_key` existe no Settings com default vazio; boot em PROD sem chave (ou com chave &lt; 32 chars) falha com a mensagem esperada; `test_settings_seed.py` passa sem ter sido editado.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: guard require_metrics_key e fechamento do router de metrics</name>
  <files>app/shared/metrics/security.py, app/shared/metrics/router.py, app/tests/test_metrics_guard.py</files>
  <behavior>
    - `client.get("/metrics")` sem header, com `METRICS_API_KEY` configurada → 401, e o corpo NÃO contém
      texto de métrica Prometheus (asserção explícita, ex.: `"python_info" not in resp.text`)
    - `client.get("/metrics", headers={"X-Metrics-Key": ""})` → 401 (header presente mas vazio conta como ausente)
    - `client.get("/metrics", headers={"X-Metrics-Key": "chave-errada"})` → 403
    - `client.get("/metrics", headers={"X-Metrics-Key": <chave correta>})` → 200, `Content-Type` começa com
      `text/plain` e o corpo tem conteúdo (`len(resp.content) > 0`)
    - Com `metrics_api_key=""` no settings, qualquer requisição (com ou sem header) → 503
    - `inspect.getsource(require_metrics_key)` contém `compare_digest` (gate de timing-safety, D-04)
    - O `APIRouter` de metrics carrega o guard por construção: `require_metrics_key` aparece nos
      `router.dependencies` (comparar as `dependency` dos `Depends` em `router.dependencies`)
    - `client.get("/health")` continua 200 e `client.get("/")` continua 200 (D-06 — nada de regressão nos públicos)
  </behavior>
  <action>
    Criar `app/shared/metrics/security.py`, imitando o estilo de
    `app/modules/auth/infrastructure/http/dependencies.py`: `from __future__ import annotations`, docstring em
    português explicando que o scraper Prometheus não tem JWT e por isso o guard é chave fixa em header
    (D-01) e por que o módulo não mora em `app/shared/security` (D-09).

    Conteúdo: constante `METRICS_API_KEY_HEADER = "X-Metrics-Key"`; scheme de módulo
    `metrics_key_scheme = APIKeyHeader(name=METRICS_API_KEY_HEADER, auto_error=False)` (importado de
    `fastapi.security`, `auto_error=False` no mesmo espírito do `HTTPBearer(auto_error=False)` já usado);
    e `async def require_metrics_key(chave: str | None = Depends(metrics_key_scheme)) -> None`.

    Corpo do guard, nesta ordem: (1) `esperada = get_settings().metrics_api_key` — a chamada tem de ser
    **dentro** da função, para o teste poder patchar `app.shared.metrics.security.get_settings`;
    (2) se `not esperada`, levantar `HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE)` com detalhe dizendo
    que METRICS_API_KEY não está configurada (D-07) — fail-closed, antes de qualquer olhada no header;
    (3) se `not chave`, levantar `HTTPException(status.HTTP_401_UNAUTHORIZED)` com detalhe tipo
    "Missing X-Metrics-Key header" (D-03) — sem header `WWW-Authenticate`, porque API key de header não é um
    challenge de HTTP auth; (4) comparar com `hmac.compare_digest(chave.encode("utf-8"), esperada.encode("utf-8"))`
    e, se não bater, levantar `HTTPException(status.HTTP_403_FORBIDDEN)` (D-03/D-04). Codificar para bytes
    antes de comparar: `compare_digest` levanta `TypeError` com `str` não-ASCII, e um header UTF-8 arbitrário
    viraria 500 em vez de 403. Nada de logar a chave recebida.

    Em `app/shared/metrics/router.py`, mover o guard para o construtor do `APIRouter` —
    `APIRouter(tags=["metrics"], dependencies=[Depends(require_metrics_key)])` — seguindo o padrão de
    `app/modules/core/routes.py:5`, para que qualquer rota futura no router de metrics nasça protegida.
    Não mudar a assinatura nem o corpo da função `metrics()`, nem o path `/metrics`. Nada muda em
    `app/modules/__init__.py` nem em `app/main.py`.

    Estender `app/tests/test_metrics_guard.py` com os casos de `<behavior>`: usar a fixture `client`, e
    `patch("app.shared.metrics.security.get_settings", return_value=SimpleNamespace(metrics_api_key=CHAVE))`
    num `with` ao redor de cada `client.get`, no mesmo formato de `test_health.py`. Definir uma constante de
    chave de teste com ≥ 32 caracteres no topo do arquivo. O teste do `router.dependencies` importa
    `router` de `app.shared.metrics.router` e `require_metrics_key` de `app.shared.metrics.security`.
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_metrics_guard.py app/tests/test_health.py app/tests/test_realtime_routes.py -q</automated>
  </verify>
  <done>`/metrics` responde 401 sem header, 403 com header errado, 200 com header certo, 503 sem chave configurada; o guard está no `APIRouter`; `/`, `/health` e `/health/ready` seguem 200/públicos.</done>
</task>

<task type="auto">
  <name>Task 3: documentar a chave (.env.example, api.md, seguranca.md)</name>
  <files>.env.example, docs/api.md, docs/seguranca.md</files>
  <action>
    `.env.example` — na seção `# Segurança (gere um JWT_SECRET forte em produção)` (linha ~124), depois do
    bloco do Microsoft SSO, adicionar comentário + `METRICS_API_KEY=` (vazio, sem valor de exemplo plausível,
    para não plantar segredo fraco no git). O comentário deve dizer, no estilo telegráfico do arquivo: chave
    que o scraper Prometheus manda no header `X-Metrics-Key`; gerar com `openssl rand -hex 32`; vazia =
    `GET /metrics` responde 503 (nunca abre); PROD exige ≥ 32 caracteres ou o boot falha.

    `docs/api.md` — na tabela da seção "Auth, saúde e métricas", trocar a linha do `/metrics` (hoje
    `| `/metrics` | público | Métricas Prometheus, incluindo realtime |`): a coluna "Papel" passa a citar o
    header `X-Metrics-Key` em vez de "público", e a Descrição registra os códigos — 401 sem header, 403 com
    chave inválida, 503 quando `METRICS_API_KEY` não está configurada. Não mexer nas linhas de `/health` e
    `/health/ready` (D-06). Se `docs/realtime.md` ou `docs/comunicacoes.md` afirmarem que `/metrics` é aberto,
    ajustar só a frase afetada — as menções conhecidas (`docs/comunicacoes.md:92`, `docs/realtime.md:263`)
    descrevem o *conteúdo* das métricas, não o acesso, e nesse caso não precisam de mudança.

    `docs/seguranca.md` — adicionar uma subseção `###` (nunca um `##` novo, e sem renomear nenhum `##`
    existente: `app/tests/test_docs_seguranca.py` afirma que linhas começando com `## Detecção`,
    `## Rotação`, `## Remoção` e `## Prevenção` existem) dentro de `## Rotação`, cobrindo a chave de
    métricas: como gerar, onde mora, que rodar a rotação exige atualizar o `.env`/secret do ambiente **e** o
    scraper juntos, e que a janela de troca derruba o scrape (403) até os dois lados baterem. Mencionar
    explicitamente que não há rate limit em `/metrics` (D-05) — é dívida conhecida, não descuido.
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_docs_seguranca.py -q && grep -q 'X-Metrics-Key' docs/api.md && grep -q 'X-Metrics-Key' docs/seguranca.md && grep -q '^METRICS_API_KEY=' .env.example && ! grep -q '| `/metrics` | público' docs/api.md</automated>
  </verify>
  <done>`.env.example` documenta `METRICS_API_KEY`; `docs/api.md` não chama mais `/metrics` de público e cita o header; `docs/seguranca.md` tem a rotação da chave sob `## Rotação` com os títulos obrigatórios intactos.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| rede externa → API (porta 8000) | Qualquer cliente que alcance a porta pode chamar `GET /metrics`; hoje sem nenhuma credencial |
| `.env` / secret manager → processo da API | `METRICS_API_KEY` cruza a fronteira de configuração; valor nunca deve ser logado nem versionado |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-emo-01 | Information Disclosure | `GET /metrics` (`app/shared/metrics/router.py`) | mitigate | `require_metrics_key` declarado no construtor do `APIRouter` (Task 2), protegendo por construção qualquer rota futura do router; teste afirma que o 401 não devolve corpo de métrica |
| T-emo-02 | Spoofing | `require_metrics_key` (`app/shared/metrics/security.py`) | mitigate | `hmac.compare_digest` sobre bytes (sem `==`, sem short-circuit por caractere) + PROD exige chave ≥ 32 caracteres no boot; gate de fonte `compare_digest in inspect.getsource` impede regressão silenciosa |
| T-emo-03 | Tampering (misconfiguração de deploy) | `Settings.metrics_api_key` | mitigate | Fail-closed: chave vazia ⇒ 503, nunca 200 (D-07); fail-fast de PROD levanta `ValidationError` no boot (D-08) — o ambiente não consegue subir "aberto por esquecimento" |
| T-emo-04 | Denial of Service | `GET /metrics` | accept | Sem rate limit por decisão explícita (D-05). Risco residual aceito: brute force da chave e scraping abusivo não são limitados. `app/shared/infrastructure/rate_limit.py` já existe e pode ser aplicado numa tarefa própria; registrar como dívida em `docs/seguranca.md` (Task 3) |
| T-emo-05 | Information Disclosure (log) | `require_metrics_key` | mitigate | O guard não loga a chave recebida nem a esperada, e o `detail` do 403 não ecoa o valor enviado |
| T-emo-06 | Elevation of Privilege | `/health`, `/health/ready`, `/`, `/v1/status` | accept | Fora de escopo (D-06); `/health*` seguem públicos de propósito para o probe do ECS. Testes de não-regressão em Task 2 garantem que nada foi fechado por acidente |

Sem instalação de pacote nesta tarefa (`hmac`/`secrets`/`inspect` são stdlib; `fastapi.security.APIKeyHeader`
já vem no FastAPI instalado) — nenhum gate de legitimidade de pacote (`T-*-SC`) se aplica.
</threat_model>

<verification>
Rodar na raiz do backend (`backend/`). A suíte completa precisa do `system_automation_test` em
`alembic upgrade head` — ver STATE.md § Blockers ("o `conftest.py` não roda alembic"). Se a suíte completa
falhar por schema, os gates por arquivo das Tasks 1–3 são o que vale; registrar a divergência no SUMMARY em
vez de "consertar" o banco.

1. `uv run ruff check app alembic` — sem achado novo (a régua do pyproject inclui B/UP/I/SIM/C4)
2. `uv run ruff format --check app alembic` — sem arquivo desformatado
3. `uv run pytest app/tests/test_metrics_guard.py -v` — todos os casos das Tasks 1 e 2 passam
4. `uv run pytest app/tests -q` — comparar com o baseline de STATE.md (886 passed / 18 skipped / 0 failed);
   esperado: 886 + os novos testes de `test_metrics_guard.py`, 0 failed
5. Grep de fiação: `dependencies=[Depends(require_metrics_key)]` presente em `app/shared/metrics/router.py`
6. Confirmar que `app/modules/health/routes.py`, `app/main.py` e `app/modules/__init__.py` estão intocados
   (`git diff --stat` não deve listá-los)
</verification>

<success_criteria>
- `GET /metrics` só devolve 200 com o header `X-Metrics-Key` casando com `METRICS_API_KEY`; 401 sem header, 403 com chave errada, 503 sem chave configurada
- Comparação da chave é `hmac.compare_digest` sobre bytes, com gate de teste sobre a fonte
- Boot em PROD falha se `METRICS_API_KEY` estiver ausente ou com menos de 32 caracteres, sem alterar a ordem de falha que `test_settings_seed.py` já afirma
- Guard declarado no construtor do `APIRouter` de metrics (protege rotas futuras por construção)
- `/`, `/health`, `/health/ready` e o RBAC das demais rotas inalterados
- `.env.example`, `docs/api.md` e `docs/seguranca.md` refletem o novo acesso; `/metrics` não é mais descrito como público
- `ruff check` + `ruff format --check` limpos; suíte sem falha nova
</success_criteria>

<output>
Criar `.planning/quick/260827-emo-proteger-endpoint-get-metrics-com-guard-/260827-emo-SUMMARY.md` ao terminar.

Commits (Conventional Commits, escopo `metrics`, **sem** menção/coautoria de Claude — regra permanente
deste projeto), em `develop`:
- Task 1: `feat(metrics): exige METRICS_API_KEY e falha o boot em PROD sem chave`
- Task 2: `feat(metrics): fecha GET /metrics atras de guard por API key de header`
- Task 3: `docs(metrics): documenta METRICS_API_KEY e rotacao da chave de metricas`
</output>
