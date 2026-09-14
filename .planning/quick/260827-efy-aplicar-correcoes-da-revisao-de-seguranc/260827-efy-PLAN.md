---
phase: quick/260827-efy
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260827-EFY]
files_modified:
  - app/modules/auth/application/schemas.py
  - app/modules/parametros/application/schemas.py
  - app/shared/errors/handlers.py
  - app/shared/http/body_limit.py
  - app/main.py
  - app/shared/config/settings.py
  - .env.example
  - app/modules/auth/infrastructure/repositorio_otp.py
  - docs/seguranca.md
  - app/tests/conftest.py
  - app/tests/test_auth_flows.py
  - app/tests/test_parametros.py
  - app/tests/test_shared_errors_handlers.py
  - app/tests/test_request_body_limit.py
  - app/tests/test_auth_otp_email.py

must_haves:
  truths:
    - "POST /api/auth/register recusa com 422 um user_name acima de 255 caracteres e um phone acima de 20 caracteres, em vez de deixar o INSERT estourar no Postgres"
    - "ParametroCreate/ParametroUpdate recusam valor e descricao acima de 4000 caracteres; ChangeRequestCreate recusa justification acima de 2000 caracteres e proposed_payload cujo JSON serializado exceda 16 KiB"
    - "Uma excecao nao tratada em qualquer rota deixa um registro de nivel ERROR com traceback no log, e o corpo devolvido ao cliente continua exatamente {\"detail\": \"Internal server error\", \"type\": \"<Classe>\"} com status 500"
    - "Uma requisicao HTTP que declara Content-Length acima de REQUEST_MAX_BODY_BYTES (default 2 MiB) recebe 413 sem que a rota seja executada, e a resposta 413 sai com os cabecalhos de CORS"
    - "Conexoes WebSocket (/api/realtime/ws) continuam abrindo: o middleware de limite de corpo so age em scope[\"type\"] == \"http\""
    - "O codigo OTP em claro so vai para o log quando ENV == DEV; em PROD e em qualquer outro ambiente (STAGING/TEST/desconhecido) o caminho e envio por e-mail com degradacao graciosa via logger.exception, sem nunca logar o codigo"
  artifacts:
    - path: "app/shared/http/body_limit.py"
      provides: "Middleware ASGI puro que rejeita corpo declarado acima do teto configurado"
      exports: ["MaxBodySizeMiddleware"]
    - path: "app/tests/test_shared_errors_handlers.py"
      provides: "Prova de que o handler 500 loga o traceback e nao muda o contrato de resposta"
    - path: "app/tests/test_request_body_limit.py"
      provides: "Prova do 413, do passthrough sob o limite, do passthrough de scope nao-http e da presenca de CORS no 413"
  key_links:
    - from: "app/main.py"
      to: "app/shared/http/body_limit.py"
      via: "app.add_middleware(MaxBodySizeMiddleware, max_body_bytes=settings.request_max_body_bytes) declarado ANTES do add_middleware do CORSMiddleware"
      pattern: "MaxBodySizeMiddleware"
    - from: "app/shared/config/settings.py"
      to: ".env.example"
      via: "REQUEST_MAX_BODY_BYTES documentado no molde de configuracao"
      pattern: "REQUEST_MAX_BODY_BYTES"
    - from: "app/modules/auth/infrastructure/repositorio_otp.py"
      to: "docs/seguranca.md"
      via: "secao de Rotacao descreve a politica de entrega de OTP por ambiente"
      pattern: "ENV=DEV|ENV == \"DEV\""
---

<objective>
Aplicar as 5 correcoes da revisao de seguranca do backend: bounds de tamanho de input que faltavam (auth e parametros), log da excecao nao tratada antes do 500, teto de tamanho de corpo de requisicao no edge HTTP, e endurecimento da politica de log do OTP em claro (so DEV).

Purpose: fechar tres classes de risco reais — (a) input sem bound chegando ao Postgres ou a JSONB sem teto, (b) falha 500 invisivel no log (nenhum diagnostico de producao hoje), (c) vazamento de codigo OTP em claro no log de qualquer ambiente que nao seja PROD, incluindo um futuro STAGING.
Output: 9 arquivos de producao/config alterados, 2 arquivos de teste novos, 3 arquivos de teste ajustados, docs/seguranca.md atualizado, e a suite completa verde (baseline atual: 886 passed / 18 skipped / 0 failed).
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.claude/CLAUDE.md
@.planning/codebase/CONVENTIONS.md
@.planning/codebase/TESTING.md

Arquivos de producao a alterar (ja lidos no planejamento; releia apenas o trecho que for editar):
@app/modules/auth/application/schemas.py
@app/modules/parametros/application/schemas.py
@app/shared/errors/handlers.py
@app/main.py
@app/modules/auth/infrastructure/repositorio_otp.py

Referencias de estilo (nao editar):
@app/modules/pedidos/application/schemas.py
</context>

<preflight>
Antes da Task 1, e obrigatorio:

1. **Coding standards do projeto** — chamar `backstage_get_coding_standards` (MCP `backstage`), conforme `.claude/CLAUDE.md`. Se o MCP nao estiver configurado (`.mcp.json` ausente/PAT nao preenchido), registrar isso na SUMMARY e seguir com as convencoes do repositorio; nao interromper a tarefa por causa disso.
2. **Branch** — trabalhar em branch a partir de `develop` (`fix/` ou `chore/`), nunca em `main`.
3. **Baseline de suite** — nao rodar a suite completa agora; o baseline registrado no STATE.md e 886 passed / 18 skipped / 0 failed. A suite completa e o gate da Task 4.
4. **Nenhuma dependencia nova** — este plano nao instala nada (`pyproject.toml` intocado). Nao ha portao de legitimidade de pacote a executar.
5. **Commits** — Conventional Commits, um commit por mudanca logica. **Proibido** adicionar `Co-Authored-By` ou qualquer mencao a Claude na mensagem de commit (regra permanente deste projeto).
</preflight>

<tasks>

<task type="auto">
  <name>Task 1: Bounds de tamanho nos schemas de auth e parametros (Correcoes 1 e 2)</name>
  <files>app/modules/auth/application/schemas.py, app/modules/parametros/application/schemas.py, app/tests/test_auth_flows.py, app/tests/test_parametros.py</files>
  <action>
Duas mudancas logicas, dois commits, ambas usando exclusivamente o padrao que ja existe nos arquivos (`Field(min_length=..., max_length=...)` e `field_validator`); nao introduza `Annotated`, `constr` nem tipos novos.

**(a) `app/modules/auth/application/schemas.py` — `RegisterRequest`:**
- `user_name: str = Field(min_length=1, max_length=255)`.
- `phone: str | None = Field(default=None, max_length=20)`.
- O `max_length` de `phone` e **20, nao 32**: a coluna e `phone: Mapped[str | None] = mapped_column(String(20))` em `app/modules/auth/infrastructure/models.py:32`. Aceitar 32 no schema apenas moveria a falha para o INSERT (asyncpg `StringDataRightTruncation` -> 500). `user_name` = 255 casa com `String(255)` na linha 30 do mesmo arquivo. Deixe um comentario de uma linha em cada campo dizendo que o bound espelha a coluna.
- Nao mexa em nenhum outro campo nem em nenhuma outra classe do arquivo.

**(b) `app/modules/parametros/application/schemas.py`:**
- `import json` no topo (o bloco de stdlib, antes de `from typing import Any`; ruff `I` ordena isso) e `field_validator` adicionado ao import de `pydantic`.
- Constante de modulo `_PROPOSED_PAYLOAD_MAX_BYTES = 16_384` e `_VALOR_MAX_LENGTH = 4_000` / `_JUSTIFICATION_MAX_LENGTH = 2_000` se preferir nomear; usar literal inline tambem e aceitavel desde que consistente.
- `ParametroCreate.valor: str = Field(min_length=1, max_length=4_000)`; `ParametroCreate.descricao: str | None = Field(default=None, max_length=4_000)`.
- `ParametroUpdate.valor: str | None = Field(default=None, max_length=4_000)`; `ParametroUpdate.descricao: str | None = Field(default=None, max_length=4_000)`. Atencao: `ParametroUpdate` usa ausencia/`None` como "nao alterar" — os defaults `None` **precisam** ser preservados, senao a rota de update quebra.
- `ChangeRequestCreate.justification: str | None = Field(default=None, max_length=2_000)`.
- `ChangeRequestCreate`: `field_validator("proposed_payload")` classmethod chamado `validar_tamanho_do_payload`, no mesmo estilo de `validar_mapa_tamanhos` / `validar_grade_dinamica` em `app/modules/pedidos/application/schemas.py` (decorator `@field_validator` + `@classmethod`, assinatura `(cls, value: dict[str, Any]) -> dict[str, Any]`, `raise ValueError("mensagem em portugues")`, retorna o valor). Regra: serializar com `json.dumps(value, ensure_ascii=False, default=str)`, medir `len(serializado.encode("utf-8"))` e recusar acima de `_PROPOSED_PAYLOAD_MAX_BYTES` com mensagem tipo `"proposed_payload excede 16 KiB serializado"`. Medir em bytes UTF-8 (nao em caracteres) porque o teto de 16 KiB e sobre o que vai para o JSONB; `default=str` existe so para o schema nunca explodir com `TypeError` se for construido em Python com um valor nao serializavel.
- `valor` e `descricao` sao colunas `Text` (sem limite no banco), entao 4000/2000 sao tetos de aplicacao deliberados, nao espelhos de coluna — registre isso em um comentario curto.
- Nao toque em `ParametroOut`, `ChangeRequestOut` nem nas classes `*PageOut`: sao saida vinda do banco, nao input.

**Testes:**
- `app/tests/test_auth_flows.py` — na secao `register / confirm-register` (apos `test_register_persiste_nome_cpf_telefone`), dois testes HTTP no estilo do arquivo (fixture `client`, `_email_unico`, `_cpf_unico`): `user_name` com 256 caracteres -> 422; `phone` com 21 caracteres -> 422. Nivel HTTP aqui porque o arquivo inteiro e HTTP e isso tambem prova a fiacao da rota. Confirme que `test_register_persiste_nome_cpf_telefone` continua verde (usa `+5511999999999`, 14 caracteres — cabe em 20).
- `app/tests/test_parametros.py` — testes de schema puro (sem `client`, sem banco): construir `ParametroCreate` / `ParametroUpdate` / `ChangeRequestCreate` diretamente e afirmar `pytest.raises(ValidationError)` para valor/descricao com 4001 caracteres, justification com 2001, e um `proposed_payload` grande (ex.: `{"k": "x" * 20_000}`); mais um caso positivo provando que um payload pequeno passa. Nivel de schema aqui porque o 422 do FastAPI e automatico e a plumbing de autenticacao do arquivo nao acrescenta informacao. Importe `ValidationError` de `pydantic`.

Commits: `fix(auth): limita tamanho de user_name e phone no cadastro` e `fix(parametros): limita tamanho de valor, descricao, justificativa e payload de solicitacao`.
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_auth_flows.py app/tests/test_parametros.py app/tests/test_parametros_registro.py -q</automated>
  </verify>
  <done>Os 5 campos ganharam bound; `proposed_payload` tem validator de 16 KiB; testes novos passam; nenhum teste pre-existente de auth/parametros regrediu; dois commits atomicos criados.</done>
</task>

<task type="auto">
  <name>Task 2: Log da excecao nao tratada + teto de corpo de requisicao (Correcoes 3 e 4)</name>
  <files>app/shared/errors/handlers.py, app/shared/http/body_limit.py, app/main.py, app/shared/config/settings.py, .env.example, app/tests/test_shared_errors_handlers.py, app/tests/test_request_body_limit.py</files>
  <action>
Duas mudancas logicas, dois commits.

**(a) `app/shared/errors/handlers.py` (Correcao 3):**
- `import logging` no topo e `logger = logging.getLogger(__name__)` em nivel de modulo, exatamente o padrao de `app/bootstrap.py` e `app/modules/auth/infrastructure/repositorio_otp.py:16`.
- Dentro de `unhandled_exception_handler`, antes do `return`, chamar `logger.exception("Excecao nao tratada em %s %s", request.method, request.url.path, exc_info=exc)`. Para poder usar `request`, renomeie o parametro `_request` para `request` (ele deixa de ser ignorado). Mantenha a assinatura async e o tipo de retorno.
- **Nao altere o corpo nem o status da resposta.** O contrato `{"detail": "Internal server error", "type": exc.__class__.__name__}` com 500 permanece byte a byte igual — nenhuma mensagem de excecao, nenhum traceback pode vazar para o cliente. `logger.exception` grava o traceback so no log do servidor.

**(b) Novo `app/shared/http/body_limit.py` (Correcao 4):**
- Sem `__init__.py`: `app/shared/errors/`, `config/`, `database/`, `logging/` e `metrics/` tambem nao tem (namespace packages implicitos). So `jobs/`, `pagination/` e `security/` tem, porque reexportam.
- Docstring curta explicando o que o middleware garante e o que ele **nao** garante.
- `from __future__ import annotations`; imports de `starlette.datastructures.Headers`, `starlette.responses.JSONResponse` e `starlette.types.ASGIApp, Receive, Scope, Send`. Starlette ja vem com o FastAPI — nenhuma dependencia nova.
- Classe `MaxBodySizeMiddleware` como middleware ASGI puro (nao `BaseHTTPMiddleware`, que buferiza o corpo e anularia o proposito): `__init__(self, app: ASGIApp, *, max_body_bytes: int) -> None` guardando os dois; `async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None`.
- Primeira coisa no `__call__`: se `scope["type"] != "http"`, delegar imediatamente para `self.app(scope, receive, send)` e retornar. **Isso e obrigatorio** — existe rota WebSocket em `app/modules/realtime/infrastructure/http/routes.py:339` (`@router.websocket("/ws")`), e um middleware que assume HTTP quebraria o realtime inteiro.
- Ler `Headers(scope=scope).get("content-length")`. Ausente -> passthrough. Presente mas nao convertivel para `int` -> passthrough (Content-Length malformado e rejeitado antes, no parser h11 do uvicorn; nao e responsabilidade daqui). Convertivel e maior que `max_body_bytes` -> `logger.warning` com metodo, path e o tamanho declarado (nunca o corpo) e responder `JSONResponse(status_code=413, content={"detail": "Request entity too large", "type": "RequestEntityTooLarge"})` chamando `await resposta(scope, receive, send)`, sem invocar `self.app`.
- Corpo da resposta 413 segue a mesma forma `{"detail", "type"}` de `handlers.py`, e em ingles como aquele arquivo (as mensagens em portugues do projeto ficam nos validators de dominio).
- Limitacao a registrar na docstring: um cliente com `Transfer-Encoding: chunked` nao envia `Content-Length` e passa pelo filtro. Este middleware e uma guarda barata de edge, nao um cap absoluto de streaming — o cap absoluto pertenceria ao proxy/ALB na frente da API.

**(c) `app/shared/config/settings.py`:**
- Novo campo, no bloco imediatamente acima de `cors_origins` (linha ~388), seguindo o padrao com bounds do arquivo: `request_max_body_bytes: int = Field(default=2_097_152, ge=65_536, le=67_108_864, validation_alias="REQUEST_MAX_BODY_BYTES")`.
- Comentario justificando o default de 2 MiB com o maior payload legitimo de hoje: `AlterarGradesProdutoRequest` e bounded em 100 changes, cada change com `expected_version` de 64 hex e no maximo 100 tamanhos de <=16 caracteres — pior caso ~290 KB, ~7x de folga. Os demais inputs sao muito menores (`MicrosoftSsoRequest.id_token` <= 8 KiB; `proposed_payload` <= 16 KiB depois da Task 1).
- Nao adicione regra em `validate_cross_field_constraints`: os bounds do `Field` bastam.

**(d) `app/main.py`:**
- `app.add_middleware(MaxBodySizeMiddleware, max_body_bytes=settings.request_max_body_bytes)` **antes** da chamada `app.add_middleware(CORSMiddleware, ...)` que ja existe.
- A ordem importa e nao e cosmetica: `Starlette.add_middleware` faz `insert(0, ...)`, entao o ultimo adicionado fica o mais externo. Adicionando o limite de corpo primeiro, o CORS fica por fora e injeta os cabecalhos de CORS tambem na resposta 413 — senao o navegador do frontend veria um erro opaco de CORS em vez de um 413 legivel. Deixe um comentario de uma linha registrando esse motivo.

**(e) `.env.example`:**
- Adicionar `REQUEST_MAX_BODY_BYTES=2097152` na secao do frontend/CORS (linha ~136), com um comentario de uma linha ("Teto do corpo da requisicao; acima disso a API responde 413 sem executar a rota"), no mesmo estilo dos outros blocos comentados do arquivo.

**Testes (dois arquivos novos):**
- `app/tests/test_shared_errors_handlers.py`: montar um `FastAPI()` local no proprio teste, chamar `register_exception_handlers(app_local)`, declarar uma rota que faz `raise RuntimeError("boom")`, e usar `with TestClient(app_local, raise_server_exceptions=False) as c:`. `raise_server_exceptions=False` e obrigatorio — o `ServerErrorMiddleware` do Starlette re-levanta a excecao depois de gerar a resposta, e o default do TestClient e propagar. Afirmar: status 500; corpo exatamente `{"detail": "Internal server error", "type": "RuntimeError"}`; via `caplog` (nivel ERROR) que existe registro com `record.exc_info` preenchido e o texto "boom" no traceback; e que "boom" **nao** aparece no corpo da resposta.
- `app/tests/test_request_body_limit.py`: (1) app local com `MaxBodySizeMiddleware(max_body_bytes=1_024)` e uma rota POST — corpo de 2 KiB devolve 413 e a rota nao executa (use uma flag/lista fechada na closure para provar que o handler nao rodou); corpo de 512 bytes chega na rota (200). (2) GET sem corpo passa. (3) Passthrough de scope nao-http: instanciar `MaxBodySizeMiddleware` com um app-espia (`async def espia(scope, receive, send)` que registra a chamada) e chamar `await middleware({"type": "websocket", "headers": []}, receive, send)` — o espia foi chamado, nenhuma resposta foi enviada. (4) Fiacao real via fixture `client`: `client.post("/api/auth/sign-in", content=b"x" * (get_settings().request_max_body_bytes + 1), headers={"content-type": "application/json", "origin": <primeiro item de get_settings().cors_origins_list>})` -> 413 **e** cabecalho `access-control-allow-origin` presente (prova a ordem de (d)). Derive o Origin de `cors_origins_list` em vez de hardcodar `http://localhost:3000`, para o teste nao depender do `.env` da maquina.

Commits: `fix(errors): loga excecao nao tratada antes de responder 500` e `feat(http): rejeita requisicao com corpo acima do teto configurado`.
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_shared_errors_handlers.py app/tests/test_request_body_limit.py app/tests/test_health.py app/tests/test_realtime_routes.py -q</automated>
  </verify>
  <done>Excecao nao tratada aparece no log com traceback e a resposta 500 nao mudou; corpo acima de 2 MiB responde 413 com cabecalhos de CORS e sem executar a rota; WebSocket e scopes nao-http passam intactos; `REQUEST_MAX_BODY_BYTES` existe em settings e no `.env.example`; dois commits atomicos criados.</done>
</task>

<task type="auto">
  <name>Task 3: Codigo OTP em claro so em DEV (Correcao 5)</name>
  <files>app/modules/auth/infrastructure/repositorio_otp.py, app/tests/test_auth_otp_email.py, app/tests/conftest.py, docs/seguranca.md</files>
  <action>
**(a) `app/modules/auth/infrastructure/repositorio_otp.py` — inverter a guarda em `criar_desafio` (linhas 52-72):**
- Hoje: `if ENV == "PROD": <envia e-mail> else: logger.info(...codigo em claro...)`. Isso significa que qualquer ambiente que nao seja exatamente `PROD` — incluindo um futuro `STAGING`/`TEST`/uma variavel mal digitada — imprime o OTP em claro no log.
- Passar a: `if ENV == "DEV": <logger.info com o codigo> else: <try enviar_codigo_otp / except FalhaEntregaEmail: logger.exception / else: logger.info "enviado por e-mail">`.
- O bloco de envio e o `except FalhaEntregaEmail` devem ser **exatamente os mesmos** que existem hoje no ramo PROD (mesma chamada com `email=`, `codigo=`, `purpose=`, `settings=`; mesmo `logger.exception`; mesmo `else: logger.info` sem o codigo). Nao mude a politica de degradacao graciosa: falha de SMTP nao pode derrubar register/recovery, coerente com a postura anti-enumeracao de `request_password_recovery`.
- Atualizar os dois comentarios do bloco para refletir a nova regra: o codigo em claro sai no log **somente** em DEV; PROD e qualquer ambiente intermediario seguem o caminho de e-mail; em nenhum ramo do `else` o codigo pode ir para o log.
- Nao mexa em `verificar_desafio`, `remover_desafios`, `OTP_TTL_MINUTES` nem na assinatura/retorno de `criar_desafio` (continua devolvendo o codigo — e assim que a suite de testes o obtem, sem depender de log).

**(b) Lacuna documentada (nao inventar comportamento):**
Em um ambiente intermediario (ex.: `STAGING`) sem SMTP configurado, `SmtpEmailSender.enviar` levanta `FalhaTerminalEntrega("smtp_not_configured")` (`app/modules/comunicacoes/infrastructure/email_sender.py:34-35`), que herda de `FalhaEntregaEmail` (`app/modules/comunicacoes/application/ports.py:103,122`) — ou seja, cai no `except`, e logado sem o codigo, e o usuario **nao tem como obter o OTP**. O codigo existente **nao decide** o que fazer nesse caso: nao ha fallback, nao ha 503, `smtp_configured` nao e consultado em `repositorio_otp.py`. **Nao invente fallback.** Registre a lacuna em dois lugares: um comentario curto no `else` do arquivo e uma nota na SUMMARY, no formato "ao provisionar um ambiente que nao seja DEV nem PROD, SMTP_HOST/USER/PASSWORD precisam estar configurados, senao register/recovery ficam sem caminho de entrega; decidir entao entre exigir SMTP no boot (fail-fast em settings) ou responder 503 no endpoint".

**(c) `app/tests/conftest.py` — fixar o ambiente da suite:**
- Adicionar `os.environ.setdefault("ENV", "dev")` junto ao bloco de `os.environ.setdefault(...)` existente (linhas ~90-104), **antes** de `from app.main import app` na linha 106. Motivo: `ENV` e resolvido em tempo de import em `app/shared/config/settings.py:11` (`os.getenv("ENV", "dev").upper()`), e com a Correcao 5 uma shell com `ENV=test` faria a suite tentar SMTP de verdade. Nada acima da linha 90 do conftest importa `app.*`, entao o `setdefault` chega antes do import de settings. Hoje o default ja e `dev` (o `.docker/docker-compose.yml` injeta `ENV: dev` e o CI nao define `ENV`), entao isso e uma travar-o-invariante, nao uma mudanca de comportamento.

**(d) `app/tests/test_auth_otp_email.py`:**
- Renomear `test_fora_de_prod_o_codigo_continua_no_log` para `test_em_dev_o_codigo_continua_no_log` e ajustar a docstring/comentario: o nome atual passa a ser semanticamente falso. O corpo continua valido (nao faz monkeypatch de `ENV`, roda com o `DEV` ambiente) — mas torne explicito com `monkeypatch.setattr("app.modules.auth.infrastructure.repositorio_otp.ENV", "DEV")` para o teste nao depender do ambiente da shell.
- Teste novo `test_em_ambiente_intermediario_envia_email_e_nao_loga_o_codigo`: `monkeypatch.setattr(...ENV, "STAGING")`, substituir `enviar_codigo_otp` por um coletor async (mesmo padrao de `test_em_prod_envia_email_e_nao_loga_o_codigo`, linhas 241-260) e afirmar que houve 1 chamada com o codigo correto **e** `codigo not in caplog.text`.
- Teste novo `test_em_ambiente_intermediario_falha_de_smtp_nao_quebra_criar_desafio`: espelha `test_em_prod_falha_de_smtp_nao_quebra_criar_desafio` (linhas 263-281) com `ENV="STAGING"` — o desafio e gravado, a falha e logada, o codigo nao aparece no log.
- Os dois testes de PROD existentes devem continuar passando sem alteracao.

**(e) `docs/seguranca.md`:**
- Na secao `## Rotacao`, passo 2 (linhas 59-61): o texto "fora de PROD o codigo sai no log" ficou incorreto. Trocar por: em `ENV=DEV` o codigo sai no log de `app/modules/auth/infrastructure/repositorio_otp.py`; em `ENV=PROD` e em qualquer outro ambiente o codigo so sai por e-mail e nunca e logado — nesses ambientes a rotacao depende de SMTP configurado.
- Na secao `## Prevencao` (linha 104-105): ajustar "entrega do OTP por e-mail em PROD" para refletir que o log em claro e restrito a DEV e que todo ambiente nao-DEV usa e-mail.
- Nao remova nem renomeie as secoes `## Deteccao`, `## Rotacao`, `## Remocao`, `## Prevencao`, e nao remova nenhum e-mail/senha das contas seed da tabela: `app/tests/test_docs_seguranca.py` afirma a presenca de todas as 4 secoes e de cada par e-mail/senha de `DEFAULT_AUTH_USERS`.

Commits: `fix(auth): restringe log do codigo OTP em claro ao ambiente DEV` (produção + conftest + testes) e `docs(seguranca): atualiza politica de entrega de OTP por ambiente`.
  </action>
  <verify>
    <automated>uv run pytest app/tests/test_auth_otp_email.py app/tests/test_docs_seguranca.py app/tests/test_auth_flows.py -q</automated>
  </verify>
  <done>O log com o codigo em claro so e alcancavel com `ENV == "DEV"`; STAGING/TEST/qualquer outro valor seguem o caminho de e-mail com `logger.exception` na falha e sem logar o codigo; 2 testes novos + 1 renomeado passam; `docs/seguranca.md` descreve a politica nova e `test_docs_seguranca.py` continua verde; lacuna de SMTP em ambiente intermediario documentada sem comportamento inventado.</done>
</task>

<task type="auto">
  <name>Task 4: Gate de qualidade e suite completa</name>
  <files>(nenhum arquivo de producao; so correcao de lint/tipo/teste se o gate apontar)</files>
  <action>
Rodar os quatro gates que o CI (`.github/workflows/pipeline.yml`) executa, na mesma ordem, e corrigir o que aparecer:
1. `uv run ruff check app alembic` — a regua inclui `E4,E7,E9,F,B,UP,I,SIM,C4`; `I` (isort) e o mais provavel de reclamar dos imports novos (`import json`, `import logging`, imports de starlette).
2. `uv run ruff format --check app alembic`; se falhar, `uv run ruff format app alembic` e inclua o resultado no commit da tarefa correspondente (nao crie commit de formatacao separado se der para emendar).
3. `uv run pyright app alembic` — atencao aos tipos ASGI (`Scope`, `Receive`, `Send`) em `body_limit.py` e ao `exc_info=exc` em `handlers.py`.
4. `uv run pytest -q` — suite completa.

Baseline registrado no STATE.md: **886 passed / 18 skipped / 0 failed**. O esperado ao final e 886 + os testes novos (aprox. 2 em auth_flows + 5 em parametros + 2 no arquivo de handlers + 5 no arquivo de body limit + 2 em otp_email), 18 skipped, **0 failed**. Qualquer teste que passe a falhar tem de ser explicado na SUMMARY como causado por esta tarefa ou provado pre-existente (`git stash` + rerun) antes de ser aceito.

Nao rodar em worktree: conforme a licao registrada na memoria do projeto, worktree quebra os testes que dependem do Docker/banco `system_automation_test`. Nenhuma migration nova e criada aqui, entao o banco de teste nao precisa de `alembic upgrade`; se `test_schema_guard.py` reclamar, o banco de teste ja estava atrasado antes desta tarefa (ver Blockers do STATE.md).
  </action>
  <verify>
    <automated>uv run ruff check app alembic && uv run ruff format --check app alembic && uv run pyright app alembic && uv run pytest -q</automated>
  </verify>
  <done>Os quatro gates passam; suite completa com >=886 passed e 0 failed; qualquer desvio do baseline explicado na SUMMARY.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| navegador/cliente HTTP -> FastAPI | Todo input nao confiavel entra aqui (body JSON, headers). Correcoes 1, 2 e 4 atuam nesta fronteira. |
| FastAPI -> Postgres | String sem bound no schema chega como INSERT em coluna `String(n)` ou JSONB sem teto. |
| processo da API -> log/observabilidade | O log e lido por mais gente do que o banco; segredo que cai no log vaza para essa audiencia. Correcoes 3 e 5 atuam aqui, em direcoes opostas (uma acrescenta diagnostico, a outra remove segredo). |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-efy-01 | Denial of Service | `POST /api/auth/register` (`RegisterRequest`) | mitigate | `max_length` 255/20 espelhando as colunas `String(255)`/`String(20)`, evitando tanto payload inflado quanto 500 por truncamento no asyncpg (Task 1a) |
| T-efy-02 | Denial of Service | `POST /parametros` e solicitacoes de mudanca (`ParametroCreate/Update`, `ChangeRequestCreate`) | mitigate | `max_length` 4000/2000 e validator de 16 KiB serializados em `proposed_payload`, impedindo linha `Text`/JSONB sem teto (Task 1b) |
| T-efy-03 | Denial of Service | edge HTTP de toda a API | mitigate | `MaxBodySizeMiddleware` responde 413 pelo `Content-Length` declarado antes de qualquer parse; default 2 MiB via `REQUEST_MAX_BODY_BYTES` (Task 2b/2d) |
| T-efy-04 | Denial of Service | requisicao `Transfer-Encoding: chunked` (sem `Content-Length`) | accept | Fora do alcance de uma guarda de header; cap absoluto pertence ao proxy/ALB. Limitacao registrada na docstring de `body_limit.py` |
| T-efy-05 | Repudiation | `unhandled_exception_handler` | mitigate | `logger.exception` com metodo+path antes do 500: hoje uma falha 500 nao deixa rastro nenhum, o que impede investigar incidente (Task 2a) |
| T-efy-06 | Information Disclosure | resposta 500 ao cliente | mitigate | Corpo `{"detail","type"}` mantido byte a byte; traceback so no log do servidor — teste afirma que a mensagem da excecao nao aparece no corpo (Task 2a) |
| T-efy-07 | Information Disclosure | log de `criar_desafio` (codigo OTP em claro) | mitigate | Ramo de log em claro restrito a `ENV == "DEV"`; PROD e qualquer ambiente intermediario usam e-mail e nunca logam o codigo (Task 3a) |
| T-efy-08 | Elevation of Privilege | fluxo de recovery em ambiente intermediario sem SMTP | accept (lacuna documentada) | O codigo atual nao define fallback; a tarefa **nao** inventa um. Registrada como decisao pendente (exigir SMTP no boot vs 503 no endpoint) na SUMMARY e em comentario no codigo (Task 3b) |
| T-efy-09 | Spoofing | resposta 413 sem cabecalho de CORS | mitigate | Middleware de limite adicionado **antes** do CORS para que o CORS fique externo e o frontend leia um 413 real em vez de um erro opaco de CORS; teste afirma o cabecalho (Task 2d) |
| T-efy-10 | Denial of Service | `@router.websocket("/ws")` do realtime | mitigate | Passthrough obrigatorio de `scope["type"] != "http"` no middleware, com teste dedicado (Task 2b) |
| T-efy-SC | Tampering | npm/pip/cargo installs | n/a | Nenhuma dependencia nova: `pyproject.toml` nao e tocado e o middleware usa Starlette, que ja vem com o FastAPI. Portao de legitimidade de pacote nao se aplica. |
</threat_model>

<verification>
1. `uv run pytest -q` — suite completa, >=886 passed, 0 failed.
2. `uv run ruff check app alembic` e `uv run ruff format --check app alembic` — limpos.
3. `uv run pyright app alembic` — sem erro novo.
4. Grep de nao-regressao: `grep -n "ENV ==" app/modules/auth/infrastructure/repositorio_otp.py` mostra a comparacao com `"DEV"` e nenhum `logger.info` com `code`/`codigo` fora desse ramo.
5. Grep de fiacao: em `app/main.py`, a linha do `MaxBodySizeMiddleware` aparece **antes** da linha do `CORSMiddleware`.
6. `git log --oneline` da branch: commits atomicos em Conventional Commits, nenhum com `Co-Authored-By` ou mencao a Claude.
</verification>

<success_criteria>
- As 5 correcoes implementadas usando apenas os padroes ja presentes nos arquivos (`Field` com bounds, `field_validator` + `@classmethod`, `logging.getLogger(__name__)`, middleware ASGI puro sem dependencia nova).
- `phone` limitado a 20 (coluna `String(20)`), nao 32 — divergencia deliberada do exemplo do pedido, justificada no codigo.
- Contrato de resposta do 500 inalterado; nenhum traceback ou mensagem de excecao no corpo.
- 413 devolvido antes da rota, com CORS, e WebSocket intacto.
- Codigo OTP em claro inalcancavel fora de `ENV == "DEV"`.
- Lacuna de SMTP em ambiente intermediario documentada, sem comportamento novo inventado.
- `docs/seguranca.md` coerente com o codigo e `test_docs_seguranca.py` verde.
- Suite completa e os tres gates de qualidade do CI passando.
</success_criteria>

<notes>
**Achado fora do escopo (nao implementar aqui, registrar na SUMMARY):** `SetUserRolesRequest.roles: list[str] = Field(min_length=1)` em `app/modules/auth/application/schemas.py:143` nao tem `max_length` na lista nem bound no tamanho de cada item — e a mesma classe de problema das Correcoes 1 e 2, mas nao esta entre as 5 correcoes pedidas. Vale um follow-up (`max_length` na lista + `Literal`/enum dos papeis validos, ja que os papeis sao um conjunto fechado de 5).

**Verificacao de que 2 MiB nao corta nada legitimo (feita no planejamento):** o maior input da API e `AlterarGradesProdutoRequest` — `changes` bounded em 100 itens, cada um com `expected_version` de 64 hex e `sizes` de no maximo 100 chaves de <=16 caracteres (`min_length=1, max_length=100`), mais o validator de 100 tamanhos distintos por lote. Pior caso ~2,9 KB por change, ~290 KB por requisicao. Nenhum endpoint aceita upload de arquivo (`grep` por `UploadFile`/`File(` no `app/` nao retorna nada). Folga de ~7x.
</notes>

<output>
Crie `.planning/quick/260827-efy-aplicar-correcoes-da-revisao-de-seguranc/260827-efy-SUMMARY.md` ao terminar, incluindo: numeros da suite antes/depois, os commits criados, a lacuna de SMTP em ambiente intermediario (T-efy-08) e o achado fora de escopo de `SetUserRolesRequest`.
</output>
