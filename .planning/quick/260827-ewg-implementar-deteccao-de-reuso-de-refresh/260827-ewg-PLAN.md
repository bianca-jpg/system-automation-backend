---
phase: quick/260827-ewg
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260827-EWG]
files_modified:
  - app/modules/auth/application/casos_uso.py
  - app/tests/test_auth_reuso_refresh_token.py

must_haves:
  truths:
    - "Um refresh token já rotacionado, reapresentado em POST /api/auth/token/refresh, recebe 401 com detail 'Invalid refresh token'"
    - "O reuso detectado revoga a sessão inteira pelo MESMO mecanismo do sign_out: a chave auth:signed_out_since:{user_id} passa a existir no Redis e o refresh token legítimo emitido na rotação anterior também passa a receber 401"
    - "Um refresh legítimo continua devolvendo 200 e passa a deixar em auth:refresh_jti:{user_id} exatamente o jti do refresh token novo devolvido no corpo da resposta"
    - "O primeiro refresh de um usuário que ainda não tem jti guardado no Redis é aceito e grava o jti novo (nenhum apagão de sessão no deploy desta mudança)"
    - "Um refresh token sem o claim jti é aceito mesmo havendo jti guardado — ausência de jti nunca é tratada como reuso"
    - "Com o Redis indisponível (RedisError em get e em set) o refresh continua devolvendo 200 — fail-open, sem 500 e sem exceção vazando"
    - "O corte de sign-out por iat e o teto absoluto por auth_time seguem intactos: nenhuma das duas checagens foi removida, reordenada nem afrouxada"
    - "Nenhum log novo contém sub, e-mail, refresh token ou o valor do jti"
    - "sign_in, sign_in_microsoft, register, confirm_register e os fluxos de recuperação de senha não foram tocados"
    - "app/modules/auth/domain/tokens.py não foi tocado — o jti continua nascendo em create_refresh_token, sem mudança de assinatura"
    - "A suíte completa no Docker fecha sem NENHUMA falha nova em relação ao baseline capturado na Task 1"
  artifacts:
    - path: "app/modules/auth/application/casos_uso.py"
      provides: "Detecção de reuso de refresh token por jti no Redis, dentro de refresh_token, com fail-open"
      contains: "auth:refresh_jti"
    - path: "app/tests/test_auth_reuso_refresh_token.py"
      provides: "Cobertura ponta a ponta da rotação com detecção de reuso (Redis real do banco 15 + TestClient)"
      min_lines: 150
  key_links:
    - from: "app/modules/auth/application/casos_uso.py::refresh_token"
      to: "Redis auth:refresh_jti:{user_id}"
      via: "GET do jti vigente e SET do jti novo, ambos best-effort"
      pattern: "_chave_refresh_jti"
    - from: "app/modules/auth/application/casos_uso.py::refresh_token"
      to: "Redis auth:signed_out_since:{user_id}"
      via: "SET do corte de revogação no ramo de reuso detectado (mesma chave que sign_out grava)"
      pattern: "_revogar_sessao_por_reuso"
    - from: "app/tests/test_auth_reuso_refresh_token.py"
      to: "POST /api/auth/token/refresh"
      via: "TestClient, fixture client do conftest"
      pattern: "token/refresh"
---

<objective>
Fechar a rotação de refresh token com detecção de reuso ("rotation with reuse detection"), usando
o campo `jti` que `create_refresh_token` já emite mas hoje ninguém persiste nem confere.

Purpose: hoje `/api/auth/token/refresh` já emite um refresh token novo a cada chamada, mas o token
antigo continua valendo até expirar (7 dias) ou até um sign-out. Um refresh token roubado dá ao
atacante uma sessão paralela de até 7 dias que ninguém detecta e nada derruba. Com esta mudança, a
segunda apresentação de um token já rotacionado é lida como sinal de roubo/replay: o refresh é
rejeitado com 401 e a sessão inteira do usuário é revogada reaproveitando o corte
`auth:signed_out_since:{user_id}` que o `sign_out` já grava (SEC-06).

Output: dois pontos de Redis novos dentro de `refresh_token` (ler o jti vigente, gravar o jti novo),
um ramo de revogação por reuso, e um arquivo de teste novo cobrindo os 7 cenários do plano — sem
tocar em `domain/tokens.py`, em rota nova, em exceção nova, nem em nenhum outro fluxo de auth.

Não-objetivo (explícito): NÃO é para mexer em `sign_in`, `sign_in_microsoft`, `register`,
`confirm_register`, `request_password_recovery`, `confirm_password_recovery`,
`confirm_new_password`, nem em `_auth_success`. NÃO criar rota, exceção de domínio, tabela,
migration, setting nem camada nova. NÃO enfraquecer as duas checagens que `refresh_token` já faz
(corte de sign-out por `iat`, teto absoluto por `auth_time`) — esta mudança é estritamente aditiva
e entra DEPOIS das duas.

## Consequência aceita que o executor precisa conhecer antes de começar

A chave de jti é **por usuário** (uma única string), espelhando a decisão travada 2 do sign-out
(revoga todas as sessões do usuário, não uma sessão específica). Isso significa que **duas sessões
simultâneas do mesmo usuário** (dois navegadores, dois dispositivos) que renovem alternadamente
serão lidas como reuso, e as duas cairão — o usuário refaz o login. Isso é o desenho pedido, não um
bug a "consertar" durante a execução: o sign-out aqui já é por usuário (sair num dispositivo derruba
todos os outros), então a postura é consistente. Registrar isso como comentário WHY no código
(ver Task 2) e repetir no SUMMARY. Se aparecer a tentação de trocar para uma cadeia por sessão,
**pare e pergunte** — é mudança de desenho, não detalhe de implementação.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.claude/CLAUDE.md
@app/modules/auth/application/casos_uso.py
@app/tests/test_auth_sessao_revogacao.py

## Comandos canônicos deste projeto

Os testes NÃO rodam com `uv run pytest` direto no host. Rodam dentro do container que já está de
pé (o `/workspace/app` do container vem da cópia principal do repositório):

- Suíte completa: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- Subconjunto: o mesmo comando seguido dos caminhos dos arquivos de teste e `-q`
- Lint: `docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/`

Se o container não estiver rodando: `docker compose -f .docker/docker-compose.yml up -d`.

`workflow.use_worktrees` está `false` em `.planning/config.json` de propósito: um worktree editaria
uma pasta e testaria outra (o container monta a cópia principal). **Não religar.** Executar as
tasks em sequência, na cópia principal.

## Working tree compartilhado (LEIA ANTES DE QUALQUER `git`)

Na branch `develop` (6 commits à frente de `origin/develop`) existe trabalho não commitado de
OUTRAS sessões: `.env.example`, `app/main.py`, `app/shared/config/settings.py` modificados e
`app/shared/http/` novo, além de duas quick tasks em andamento (`260827-efy` — correções da revisão
de segurança; `260827-emo` — guard do `GET /metrics`). **Não editar, não commitar, não reverter
nada disso.**

Conferido: **zero sobreposição de arquivo** entre esta task e as outras duas. `260827-efy` toca
`app/modules/auth/application/schemas.py`, `app/tests/conftest.py`, `app/tests/test_auth_flows.py`
e `docs/seguranca.md`; `260827-emo` toca métricas e docs. Nenhuma das duas toca
`app/modules/auth/application/casos_uso.py`.

PROIBIDO nesta task: `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset --hard`,
`git checkout -- <path>`, `git clean`. Todo commit com caminhos explícitos e conferido com
`git show --stat`. Nenhum commit menciona Claude nem leva `Co-Authored-By` (regra permanente do
projeto).

`docs/seguranca.md` **fica fora do escopo desta task** justamente porque as duas quick tasks em voo
editam esse arquivo. `app/tests/test_docs_seguranca.py` não afirma nada sobre refresh/jti/sign-out
(conferido por grep), então não existe gate de doc a satisfazer. Anotar no SUMMARY como candidato
de follow-up: documentar a detecção de reuso em `docs/seguranca.md` depois que `efy` e `emo`
fecharem.

## Anatomia atual (levantada, não re-investigar)

`app/modules/auth/application/casos_uso.py`:
- Linhas 62-71: `_SIGNED_OUT_SINCE_KEY_TEMPLATE = "auth:signed_out_since:{user_id}"`,
  `_SIGNED_OUT_TTL_SECONDS = 7 * 86_400`, `def _chave_sign_out(user_id: int | str) -> str`.
- Linhas 87-106: `_auth_success(user, *, auth_time=None) -> AuthResponse` — compartilhado por
  sign-in/SSO/confirm/refresh. Chama `create_refresh_token` e devolve o token no
  `AuthResponse.token.refresh_token`. **Não mudar a assinatura** (é o que amarra o escopo).
- Linhas 180-224: `refresh_token(session, body)`, na ordem: decode → GET do corte de sign-out
  (fail-open, `except (RedisError, OSError, TimeoutError)`) → comparação `iat < cutoff` (`<`
  estrito, de propósito) → teto absoluto por `auth_time` → `buscar_por_id` → `return
  _auth_success(user, auth_time=auth_time)`.
- Linhas 227-239: `sign_out(current_user)` — grava o corte, fail-**closed**
  (`RevogacaoIndisponivelError` → 503).

`app/modules/auth/domain/tokens.py` linha 48: `"jti": uuid4().hex` já existe em
`create_refresh_token`. Campo morto hoje. **Este arquivo não é tocado por esta task.**

`app/modules/auth/infrastructure/http/routes.py` linhas 189-202: `/token/refresh` mapeia
`SessaoInvalidaError` → 401 `"Invalid refresh token"` e `UsuarioNaoEncontradoError` → 401
`"User not found"`. **Nenhuma outra exceção é mapeada** — qualquer coisa fora dessas duas vira 500
pelo handler global. É por isso que o ramo de reuso NÃO pode levantar `RevogacaoIndisponivelError`.

`app/tests/conftest.py`: Redis de teste travado no banco 15 (`REDIS_URL` setado antes de importar
o app); fixture autouse `auth_rate_limit_redis` faz monkeypatch de `get_redis` **no módulo de rotas**
(`...infrastructure.http.routes.get_redis`), de propósito — o `get_redis` de `casos_uso` continua
falando com o Redis real do banco 15. Fixture autouse `_dispose_db_engine_after_test` descarta o
engine entre testes.

`app/tests/test_auth_sessao_revogacao.py`: helpers `_email_unico`, `_nova_sessao_isolada`,
`_criar_usuario_async`, `criar_usuario` (engine descartável com `NullPool` + `asyncio.run`), e o
padrão `time.sleep(1.1)` com o comentário que explica por que o intervalo existe (resolução de 1s
do `iat` frente à comparação `<`). Nenhum teste existente faz dois refreshes consecutivos da mesma
sessão — conferido por grep em `token/refresh` em todo `app/tests/` — então nenhum teste atual
esbarra na detecção nova.

## Armadilha de poluição de teste (já custou uma falha intermitente na suíte)

Qualquer teste que chame `POST /api/auth/register` sem confirmar deixa uma linha
`auth_users.confirmed_at IS NULL` viva no banco de teste, e `listar_alertas` gera um alerta de
"acesso pendente" para ela — quebrando testes de OUTROS arquivos que esperam zero alertas.
**Regra dura para o arquivo de teste desta task: nunca chamar `/api/auth/register`.** Criar usuário
sempre pelo helper `criar_usuario` (INSERT direto, `confirmed_at` preenchido).
</context>

<tasks>

<task type="auto">
  <name>Task 1: RED — arquivo de teste novo com os 7 cenários de rotação/reuso</name>
  <files>app/tests/test_auth_reuso_refresh_token.py</files>
  <action>
Antes de escrever qualquer linha, capturar o baseline da suíte e guardá-lo no scratchpad ou no
corpo do SUMMARY: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -5`.
Registrar os números exatos de passed/skipped/failed. NÃO usar o número anotado no STATE.md: há duas
quick tasks em voo adicionando arquivos de teste, então o baseline tem de ser medido agora. Se o
baseline já vier com falha, anotar quais são — a Task 3 compara "nenhuma falha NOVA", não "zero
falhas".

Criar `app/tests/test_auth_reuso_refresh_token.py` com docstring de módulo explicando que o arquivo
cobre a rotação de refresh token com detecção de reuso por `jti`, complementando
`test_auth_sessao_revogacao.py` (que cobre o corte de sign-out, SEC-06).

Duplicar os 4 helpers de setup de `test_auth_sessao_revogacao.py` (`_email_unico`,
`_nova_sessao_isolada`, `_criar_usuario_async`, `criar_usuario`) com um comentário de uma linha
dizendo que é o mesmo padrão dos outros arquivos de auth. NÃO importar de outro módulo `test_*.py`
e NÃO refatorar os helpers para um módulo compartilhado — os arquivos de auth já duplicam esse
setup entre si de propósito, e extrair mexeria em arquivo fora do escopo.

Acrescentar dois helpers locais para falar com o Redis de teste sem prender conexão a event loop
alheio (mesmo raciocínio do engine descartável): `_ler_redis(chave)` e, se for útil,
`_apagar_redis(chave)` — cada um abre um cliente novo com
`redis.asyncio.from_url(get_settings().effective_redis_url, decode_responses=True)` dentro de uma
corrotina rodada por `asyncio.run`, e fecha com `await cliente.aclose()` no `finally`. Não usar o
`get_redis()` singleton da aplicação dentro do teste.

Escrever exatamente estes testes, cada um criando seu próprio usuário com `criar_usuario` (id único
por execução, então não há colisão de chave no Redis e não é preciso limpar):

1. `test_refresh_legitimo_grava_o_jti_do_token_novo` — sign-in, um refresh, assert 200, decodificar
   com `decode_refresh_token` o `refresh_token` devolvido e assertar que
   `_ler_redis(f"auth:refresh_jti:{user_id}")` é igual ao `jti` desse token novo. **Falha hoje**
   (chave inexistente → None).
2. `test_reapresentar_refresh_token_ja_rotacionado_retorna_401` — sign-in → token A; refresh(A) →
   200; refresh(A) de novo → assert 401 e `detail == "Invalid refresh token"`. **Falha hoje**
   (devolve 200).
3. `test_reuso_revoga_a_sessao_inteira` — sign-in → token A; refresh(A) → 200 → token B;
   `time.sleep(1.1)`; refresh(A) → 401; assert que `_ler_redis(f"auth:signed_out_since:{user_id}")`
   deixou de ser None (prova que reusou o mecanismo do sign_out, não inventou outro); refresh(B) →
   401. O `sleep(1.1)` é obrigatório e leva comentário explicando o motivo: o corte é gravado em
   segundos e a comparação em `refresh_token` é `iat < cutoff` estrita — sem o intervalo, o token B
   emitido no MESMO segundo do corte sobreviveria por coincidência de relógio e o teste passaria a
   medir sorte em vez de comportamento. **Falha hoje** (o segundo refresh(A) devolve 200).
4. `test_primeiro_refresh_sem_jti_guardado_e_aceito_e_passa_a_guardar` — construir o refresh token
   direto por `create_refresh_token` (simula token emitido antes deste deploy, sem nada no Redis);
   assert 200 e que a chave `auth:refresh_jti:{user_id}` passou a existir. Prova que a mudança não
   invalida token nenhum ao subir. **Falha hoje** apenas na parte da chave.
5. `test_refresh_de_token_sem_claim_jti_nao_e_tratado_como_reuso` — fazer um refresh legítimo
   primeiro (para POPULAR a chave de jti), depois montar à mão um refresh token **sem** o claim
   `jti` (mesmo payload dos outros: `typ="refresh"`, `sub`, `email`, `roles`, `iat`, `auth_time`,
   `exp`) usando `jose.jwt.encode` com `get_settings().jwt_secret` e
   `get_settings().jwt_algorithm`; assert 200. Cobre o "tokens antigos sem jti não quebram".
   **Teste de guarda: passa antes e depois da implementação** — anotar isso no docstring do teste
   para ninguém achar que o RED falhou.
6. `test_fail_open_quando_redis_indisponivel` — sign-in normal; então `monkeypatch.setattr` de
   `app.modules.auth.application.casos_uso.get_redis` para um fake cujos `get` e `set` são `async`
   e levantam `RedisError("redis indisponível")`; refresh → assert 200. Mirar o `get_redis` de
   `casos_uso` (não o de `routes`, que a fixture autouse já ocupa). **Teste de guarda: passa antes
   e depois** — anotar no docstring.
7. `test_reuso_de_um_usuario_nao_afeta_outro` — usuários A e B; provocar o reuso em A (refresh(A1)
   → refresh(A1) → 401); em seguida refresh do token de B → assert 200. Prova que a chave é por
   usuário e o corte não vaza entre contas. **Teste de guarda: passa antes e depois.**

Rodar o arquivo e confirmar o RED esperado: os testes 1, 2, 3 e 4 falham; 5, 6 e 7 passam. Se algum
dos 1-4 passar, a implementação já existe em algum lugar ou o teste está frouxo — investigar antes
de seguir, não "consertar" o teste para ficar vermelho.

Commit só do arquivo de teste, com caminho explícito:
`test(auth): cobertura RED da detecção de reuso de refresh token`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_reuso_refresh_token.py -q 2>&1 | tail -20</automated>
  </verify>
  <done>
Arquivo `app/tests/test_auth_reuso_refresh_token.py` existe com os 7 testes nomeados acima. A rodada
mostra 4 failed / 3 passed, e as 4 falhas são exatamente os testes 1, 2, 3 e 4. Baseline da suíte
completa registrado em número absoluto (passed/skipped/failed). `grep -c "api/auth/register"
app/tests/test_auth_reuso_refresh_token.py` retorna 0. Commit feito com caminho explícito, sem
menção a Claude.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: GREEN — jti vigente no Redis, detecção de reuso e revogação da sessão</name>
  <files>app/modules/auth/application/casos_uso.py</files>
  <behavior>
    - jti apresentado == jti guardado → aceita, rotaciona, e a chave passa a valer o jti novo
    - nada guardado no Redis (primeiro refresh após o deploy) → aceita e grava
    - token sem claim jti → aceita, mesmo havendo jti guardado (nunca é reuso)
    - jti apresentado != jti guardado, havendo jti guardado → 401 `Invalid refresh token` + a chave
      `auth:signed_out_since:{user_id}` passa a existir, o que derruba também o token legítimo da
      rotação anterior
    - RedisError/OSError/TimeoutError na leitura ou na escrita do jti → aceita (fail-open), com
      log de nível WARNING contendo só `type(exc).__name__`
    - corte de sign-out por `iat` e teto absoluto por `auth_time` seguem rejeitando exatamente
      como antes, e continuam sendo avaliados ANTES da checagem de jti
  </behavior>
  <action>
Editar somente `app/modules/auth/application/casos_uso.py`. Nada de arquivo novo, nada em
`domain/tokens.py`, nada em `routes.py`.

**Constantes** — logo depois de `_SIGNED_OUT_TTL_SECONDS` (linha 67), acrescentar
`_REFRESH_JTI_KEY_TEMPLATE = "auth:refresh_jti:{user_id}"` e
`_REFRESH_JTI_TTL_SECONDS = _SIGNED_OUT_TTL_SECONDS`. O TTL é alias do constante existente em vez
de repetir o literal `7 * 86_400`: é a mesma vida de 7 dias do refresh token, e o comentário WHY
tem de dizer isso. **Não renomear `_SIGNED_OUT_TTL_SECONDS`** — o nome é citado em plano e SUMMARY
históricos da Fase 5.

**Helper de chave** — ao lado de `_chave_sign_out`, acrescentar
`def _chave_refresh_jti(user_id: int | str) -> str` devolvendo
`_REFRESH_JTI_KEY_TEMPLATE.format(user_id=user_id)`. Mesma assinatura `int | str` do vizinho, e pelo
mesmo motivo: `str.format` renderiza `42` e `"42"` igual, então a chave escrita a partir de
`current_user.id` (int) e a partir de `payload["sub"]` (str) coincidem. Isso é load-bearing no ramo
de reuso: ele grava o corte com `_chave_sign_out(payload["sub"])` e precisa acertar exatamente a
mesma chave que `sign_out` grava com `_chave_sign_out(current_user.id)`.

**Três helpers async**, imediatamente antes de `async def refresh_token`, cada um com um só
trabalho e cada um capturando `(RedisError, OSError, TimeoutError)` — a mesma tupla já usada na
leitura do corte:

- `_jti_vigente(user_id: str) -> str | None` — `await get_redis().get(_chave_refresh_jti(user_id))`.
  No except: `logger.warning` com mensagem fixa + `type(exc).__name__`, e devolve `None`. Comentário
  WHY: fail-open, `None` significa "não há jti guardado" e leva a aceitar — queda de Redis não pode
  virar apagão de login, mesma postura da leitura do corte de sign-out logo acima.
- `_registrar_jti_vigente(user_id: str, jti: str) -> None` —
  `await get_redis().set(_chave_refresh_jti(user_id), jti, ex=_REFRESH_JTI_TTL_SECONDS)`. No except:
  `logger.warning` com mensagem fixa + `type(exc).__name__` e retorna. Comentário WHY: best-effort;
  falhar em gravar não pode derrubar uma renovação legítima que já foi autorizada.
- `_revogar_sessao_por_reuso(user_id: str) -> None` — grava o corte de sign-out:
  `await get_redis().set(_chave_sign_out(user_id), str(int(datetime.now(UTC).timestamp())), ex=_SIGNED_OUT_TTL_SECONDS)`
  — exatamente o mesmo SET que `sign_out` faz. No except: `logger.exception` com mensagem fixa.
  Comentário WHY obrigatório: aqui NÃO se levanta `RevogacaoIndisponivelError` (como `sign_out` faz)
  porque `/token/refresh` só mapeia `SessaoInvalidaError` e `UsuarioNaoEncontradoError` — qualquer
  outra viraria 500; e a rejeição do token reapresentado acontece de todo modo.

**Mudança dentro de `refresh_token`** — não remover, não reordenar e não afrouxar nada do que já
está lá. As quatro etapas atuais (decode, corte de sign-out, teto absoluto, `buscar_por_id`) ficam
byte a byte como estão. Depois do `if user is None: raise UsuarioNaoEncontradoError()`, acrescentar,
nesta ordem:

1. `jti_apresentado = payload.get("jti")` e `jti_guardado = await _jti_vigente(payload["sub"])`.
2. Se `jti_guardado is not None and jti_apresentado is not None and jti_apresentado != jti_guardado`:
   chamar `await _revogar_sessao_por_reuso(payload["sub"])`, logar
   `logger.warning("Reuso de refresh token detectado; sessão revogada")` (mensagem fixa, sem `sub`,
   sem e-mail, sem token, sem o valor do jti) e `raise SessaoInvalidaError()`.
3. `resposta = _auth_success(user, auth_time=auth_time)`.
4. `await _registrar_jti_vigente(payload["sub"], decode_refresh_token(resposta.token.refresh_token)["jti"])`.
5. `return resposta`.

Comentário WHY obrigatório no passo 4, explicando por que se decodifica um token que acabou de ser
emitido aqui: `create_refresh_token` não devolve o jti e `_auth_success` é compartilhado com
sign-in/SSO/confirm — mudar a assinatura de qualquer um dos dois sairia do escopo e afetaria fluxos
que esta task não deve tocar; decodificar o token recém-emitido é o jeito mais barato de obter o jti
sem mexer em fluxo alheio. `decode_refresh_token` já está importado no arquivo.

Comentário WHY obrigatório no passo 2, registrando as duas escolhas de desenho: (a) a chave é por
usuário, espelhando a decisão travada 2 do sign-out, e a consequência aceita é que duas sessões
simultâneas do mesmo usuário renovando alternadamente derrubam uma à outra; (b) `jti_apresentado is
None` é aceito de propósito, para não invalidar token emitido antes deste deploy — forjar um token
sem jti exigiria o `JWT_SECRET`, que já é o segredo raiz de toda a autenticação, então não há
bypass novo.

Seguir o estilo de comentário do arquivo: só o POR QUÊ, nunca o O QUÊ, em português, blocos curtos
acima do trecho que explicam. Sem docstring novo nos helpers privados (o arquivo não usa).

Rodar o arquivo de teste da Task 1 e confirmar os 7 verdes. Depois rodar
`app/tests/test_auth_sessao_revogacao.py`, `app/tests/test_auth_flows.py`,
`app/tests/test_auth.py` e `app/tests/test_auth_rate_limit.py` — nenhum deles faz dois refreshes da
mesma sessão, então todos têm de continuar verdes sem edição. Se algum quebrar, a causa é
regressão na implementação; não editar teste existente para acomodar.

Commit só do arquivo de produção, caminho explícito:
`feat(auth): detecta reuso de refresh token por jti e revoga a sessão`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/ && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_reuso_refresh_token.py app/tests/test_auth_sessao_revogacao.py app/tests/test_auth_flows.py app/tests/test_auth.py app/tests/test_auth_rate_limit.py -q 2>&1 | tail -20</automated>
  </verify>
  <done>
Os 7 testes do arquivo novo passam e os 4 arquivos de auth existentes seguem verdes sem uma linha
editada. `ruff check app/` limpo. Invariantes estruturais (rodar cada uma e conferir o número):
`grep -v '^\s*#' app/modules/auth/application/casos_uso.py | grep -c 'auth:refresh_jti'` retorna 1
(só o template);
`grep -c '_chave_sign_out' app/modules/auth/application/casos_uso.py` retorna 4 ou mais (definição,
leitura do corte em refresh, escrita em sign_out, escrita no ramo de reuso);
`grep -c 'auth_absolute_session_seconds' app/modules/auth/application/casos_uso.py` retorna 1 (teto
absoluto intacto);
`grep -c '_SIGNED_OUT_TTL_SECONDS = 7 \* 86_400' app/modules/auth/application/casos_uso.py` retorna 1
(constante histórica não renomeada);
`git diff --stat HEAD~1 -- app/modules/auth/domain/tokens.py app/modules/auth/infrastructure/http/routes.py`
sai vazio. Nenhuma ocorrência de `payload["sub"]` ou `user.email` dentro de chamada de `logger.*`
nas linhas novas. Commit feito com caminho explícito, sem menção a Claude.
  </done>
</task>

<task type="auto">
  <name>Task 3: Gate de regressão da suíte completa</name>
  <files>(nenhum — só verificação)</files>
  <action>
Rodar a suíte completa no Docker e comparar com o baseline capturado na Task 1:
`docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -10`.

Critério: `passed` cresce em exatamente 7 (os testes novos) e o conjunto de `failed` é **idêntico**
ao do baseline — nenhuma falha nova. Se aparecer falha nova, diagnosticar antes de qualquer commit
novo. Duas pistas prováveis, nesta ordem:
1. Algum teste de outro arquivo que faça dois refreshes da mesma sessão (o grep da fase de
   planejamento não achou nenhum, mas as duas quick tasks em voo podem ter adicionado testes de auth
   depois — `260827-efy` toca `app/tests/test_auth_flows.py`). Nesse caso a falha é legítima e
   revela um consumidor real do comportamento antigo: **parar e reportar**, não silenciar.
2. Alerta de "acesso pendente" quebrando teste de outro arquivo, sintoma clássico de usuário
   `confirmed_at IS NULL` órfão. O arquivo desta task não chama `/api/auth/register`; conferir com
   `grep -c "api/auth/register" app/tests/test_auth_reuso_refresh_token.py` (tem de dar 0) antes de
   suspeitar dele.

Conferir a higiene do working tree antes de fechar: `git status -sb` tem de mostrar ainda
modificados/não rastreados os itens de outras sessões (`.env.example`, `app/main.py`,
`app/shared/config/settings.py`, `app/shared/http/`, as pastas de `260827-efy` e `260827-emo`) e
`git show --stat HEAD` / `HEAD~1` têm de listar exatamente um arquivo cada (produção e teste).

Escrever o SUMMARY em
`.planning/quick/260827-ewg-implementar-deteccao-de-reuso-de-refresh/260827-ewg-SUMMARY.md`,
registrando obrigatoriamente: (a) os números de baseline e final da suíte; (b) a consequência
aceita de multi-sessão por usuário; (c) que a detecção não opera enquanto o Redis estiver fora
(fail-open deliberado); (d) o follow-up de documentar em `docs/seguranca.md` depois que `efy` e
`emo` fecharem; (e) que não existe requirement `SEC-*` cobrindo rotação com detecção de reuso —
SEC-06 cobre só o sign-out —, então vale avaliar abrir um se isso virar escopo formal do v1.2.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -10</automated>
  </verify>
  <done>
Suíte completa fecha com `passed` do baseline + 7 e com o mesmo conjunto de `failed` do baseline
(nenhuma falha nova). `git status -sb` confirma que o trabalho não commitado das outras sessões
segue intacto e que os dois commits desta task têm um arquivo cada. SUMMARY escrito com os 5 itens
obrigatórios.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| cliente → `POST /api/auth/token/refresh` | Refresh token não confiável cruza aqui; a assinatura HS256 prova a origem mas não prova a posse legítima (um token roubado é criptograficamente válido) |
| aplicação → Redis (banco de sessão) | Estado de revogação e o jti vigente vivem fora do processo; a indisponibilidade do Redis muda o comportamento de segurança |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-EWG-01 | Spoofing | `refresh_token` com refresh token roubado | mitigate | Reuso do token já rotacionado passa a ser detectado por `jti` e derruba a sessão inteira via o corte `auth:signed_out_since:{user_id}`. A janela útil de um token roubado cai de 7 dias para "até o cliente legítimo renovar uma vez" |
| T-EWG-02 | Denial of Service | Redis indisponível na checagem de jti | accept | Fail-open deliberado (`_jti_vigente` devolve `None`, `_registrar_jti_vigente` engole o erro): perder o Redis não pode virar apagão de login. Consequência aceita e registrada no SUMMARY: enquanto o Redis estiver fora, a detecção de reuso não opera — mesma postura já aceita para o corte de sign-out na linha acima |
| T-EWG-03 | Denial of Service | Duas sessões simultâneas do mesmo usuário | accept | A chave de jti é por usuário, espelhando a decisão travada 2 do sign-out. Duas sessões que renovem alternadamente serão lidas como reuso e as duas cairão; o usuário refaz login. Aceito porque o sign-out aqui já é por usuário. Documentado em comentário WHY no código, no `<objective>` e no SUMMARY |
| T-EWG-04 | Information Disclosure | Logs do caminho de reuso e dos dois helpers de Redis | mitigate | Mensagens fixas + `type(exc).__name__`; nunca `sub`, e-mail, refresh token nem o valor do jti. Gate no `<done>` da Task 2 (nenhum `payload["sub"]`/`user.email` dentro de `logger.*` nas linhas novas) |
| T-EWG-05 | Elevation of Privilege | Token sem claim `jti` contornando a checagem | accept | Ausência de `jti` é aceita de propósito, para não invalidar tokens pré-deploy. Forjar um token sem `jti` exige o `JWT_SECRET`, que já é o segredo raiz de toda a autenticação — não há elevação nova, e o corte de sign-out e o teto absoluto continuam valendo para esse token |
| T-EWG-06 | Repudiation | Reuso detectado sem rastro | mitigate | `logger.warning("Reuso de refresh token detectado; sessão revogada")` no ramo de reuso, e `logger.exception` se a gravação do corte falhar — dá sinal operacional de possível roubo de token sem vazar identificador |
| T-EWG-SC | Tampering | npm/pip/cargo installs | n/a | Nenhuma dependência nova: `redis`, `python-jose` e `pytest` já estão no `pyproject.toml`. Nada a instalar, nenhum gate de legitimidade de pacote aplicável |
</threat_model>

<verification>
1. `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_reuso_refresh_token.py -q` — 7 passed.
2. `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_auth_sessao_revogacao.py app/tests/test_auth_flows.py app/tests/test_auth.py app/tests/test_auth_rate_limit.py -q` — verde, sem uma linha editada nesses arquivos.
3. `docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/` — limpo.
4. `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` — baseline + 7 passed, mesmo conjunto de failed do baseline.
5. `git diff --stat HEAD~2 -- app/` lista exatamente `app/modules/auth/application/casos_uso.py` e `app/tests/test_auth_reuso_refresh_token.py`.
6. `git status -sb` — o trabalho não commitado das outras sessões segue intocado.
</verification>

<success_criteria>
- Reapresentar um refresh token já rotacionado devolve 401 `Invalid refresh token`, e o token
  legítimo da rotação anterior passa a devolver 401 também (sessão inteira revogada pelo corte
  `auth:signed_out_since:{user_id}`).
- Refresh legítimo segue em 200 e deixa em `auth:refresh_jti:{user_id}` o jti do token novo.
- Nenhum token existente é invalidado pelo deploy: sem jti guardado, ou sem claim `jti`, o refresh é
  aceito.
- Redis fora do ar mantém o refresh em 200 (fail-open), sem 500.
- Corte de sign-out e teto absoluto intactos e ainda avaliados antes da checagem de jti.
- `domain/tokens.py`, `routes.py`, `_auth_success` e todos os outros fluxos de auth não foram
  tocados; nenhuma exceção, rota, setting, tabela ou migration nova.
- Suíte completa sem falha nova; 2 commits, um arquivo cada, caminhos explícitos, sem menção a
  Claude.
</success_criteria>

<output>
Criar `.planning/quick/260827-ewg-implementar-deteccao-de-reuso-de-refresh/260827-ewg-SUMMARY.md`
ao terminar.
</output>
