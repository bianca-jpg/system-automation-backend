---
phase: quick/260826-iln
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260826-ILN]
files_modified:
  - app/modules/pedidos/infrastructure/http/routes.py
  - app/tests/test_pedidos_routes.py
  - app/tests/test_pedidos_processing_routes.py
  - docs/api.md
  - docs/adequacao.md
  - docs/arquitetura.md
  - .planning/research/ENDPOINTS-AUDIT-2026-08-26.md

must_haves:
  truths:
    - "O OpenAPI nao lista mais /api/v1/pedidos/adequar, /api/v1/pedidos/sem_adequar nem /api/v1/pedidos/{nr_pedido}/alterar-grade"
    - "As rotas ativas de nome parecido continuam: /api/v1/pedidos/produtos/aprovar, /api/v1/pedidos/{nr_pedido}/aprovar, /api/v1/pedidos/produtos/grades e /api/v1/pedidos/processamentos"
    - "A suite no Docker fica com exatamente 3 testes a menos que a baseline capturada antes da primeira edicao, e 0 failed"
    - "Nenhum arquivo do backend referencia adequar_pedidos, sem_adequar_pedidos ou alterar_grade_legacy"
    - "docs/api.md, docs/adequacao.md e docs/arquitetura.md nao descrevem mais os 3 paths como 410 Gone"
    - "Existe teste de OpenAPI que falha se qualquer um dos 3 paths legados voltar a ser registrado"
    - ".planning/research/ENDPOINTS-AUDIT-2026-08-26.md existe com os grupos A, B, C, D e E"
    - "Todo path citado nos grupos B, C e D do documento existe de fato no OpenAPI do app"
    - "Os 3 arquivos nao commitados da outra sessao seguem modificados e fora dos commits desta task"
  artifacts:
    - path: "app/modules/pedidos/infrastructure/http/routes.py"
      provides: "Router de pedidos sem os 3 handlers 410"
      contains: "/{nr_pedido}/aprovar"
    - path: "app/tests/test_pedidos_processing_routes.py"
      provides: "Guarda de regressao dos paths legados ausentes do OpenAPI"
      contains: "not in paths"
    - path: ".planning/research/ENDPOINTS-AUDIT-2026-08-26.md"
      provides: "Inventario auditavel das 43 rotas cruzadas com o consumo do frontend"
      contains: "Grupo D"
  key_links:
    - from: ".planning/research/ENDPOINTS-AUDIT-2026-08-26.md"
      to: ".planning/STATE.md"
      via: "Grupo D cita a Fase 11 (v1.2, Victoria) como razao de nao mexer em parametros"
      pattern: "Fase 11"
    - from: "app/tests/test_pedidos_processing_routes.py"
      to: "app/modules/pedidos/infrastructure/http/routes.py"
      via: "assert de ausencia dos paths legados em app.openapi()"
      pattern: "not in paths"
---

<objective>
Duas entregas de um inventario de rotas HTTP ja levantado e validado com a mantenedora:
(1) remover os 3 endpoints legados de pedidos que hoje sao so casca de `410 Gone`;
(2) registrar o resto do inventario como documento de auditoria, sem remover codigo.

Purpose: reduzir superficie HTTP morta e, ao mesmo tempo, nao perder o levantamento das outras 40
rotas — inclusive as que PARECEM orfas mas nao sao (parametros, Fase 11 em andamento) e as que sao
apenas drift de documentacao.

Output: router de pedidos com 11 rotas (era 14), testes e docs coerentes com a remocao, e
`.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` com os grupos A-E.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.claude/CLAUDE.md
@app/modules/pedidos/infrastructure/http/routes.py
@app/tests/test_pedidos_routes.py
@app/tests/test_pedidos_processing_routes.py

## Working tree (LEIA ANTES DE QUALQUER `git`)

3 arquivos modificados e nao commitados de OUTRA sessao: `app/modules/auth/infrastructure/http/routes.py`,
`app/modules/auth/infrastructure/repositorio_otp.py`, `app/tests/test_docs_seguranca.py`.
Nao editar, nao commitar, nao reverter. Proibido nesta task: `git add -A`, `git add .`,
`git commit -a`, `git stash`, `git reset --hard`, `git checkout -- <path>`, `git clean`.
Commits sempre com caminhos explicitos. Branch: `develop`. Sem worktree (quebra os testes em Docker).
Sem `Co-Authored-By` e sem mencao a Claude nas mensagens de commit — regra permanente do projeto.

## Fatos ja apurados (nao precisa redescobrir)

`app/modules/pedidos/infrastructure/http/routes.py` tem 596 linhas e 14 decorators `@router.*`.
Os 3 alvos ficam nas linhas ~520-568, entre o fim do handler de `GET /processamentos/{jobId}` (linha
517) e o decorator `@router.post("/{nr_pedido}/aprovar")` (linha 571):

| Linhas | Rota | Handler | Corpo |
|---|---|---|---|
| 520-533 | `POST /adequar` (`status_code=410`, `deprecated=True`) | `adequar_pedidos` | so `raise HTTPException(410)` |
| 536-549 | `POST /sem_adequar` (`status_code=410`, `deprecated=True`) | `sem_adequar_pedidos` | so `raise HTTPException(410)` |
| 552-568 | `PUT /{nr_pedido}/alterar-grade` (`deprecated=True`) | `alterar_grade_legacy` | `del` dos args + `raise HTTPException(410)` |

Nenhum dos 3 tem request body, entao nao ha schema Pydantic exclusivo deles. Nao existe rota
catch-all tipo `@router.post("/{algo}")` no arquivo: a remocao nao altera o roteamento de vizinhas.

Referencias em teste (as unicas do repo; `.pyc` de `__pycache__` nao contam):
`app/tests/test_pedidos_routes.py` — docstring do modulo (linhas 3-4), `test_adequar_pedidos_exige_actor`
(133-135), `test_processamentos_legacy_retorna_410` (138-147),
`test_alterar_grade_legacy_retorna_410_e_mantem_rbac` (150-157).
`app/tests/test_pedidos_processing_routes.py` — linhas 590-591, dentro de
`test_openapi_declara_header_bounded_legacy_410_e_nao_oferece_retry`.

Referencias em docs: `docs/adequacao.md` (paragrafo que comeca na linha ~19), `docs/api.md` (3 linhas
de tabela), `docs/arquitetura.md` (~127-128). Nenhum teste faz assert sobre o conteudo desses 3 docs
(`test_docs_seguranca.py` cobre so `docs/seguranca.md` e `docs/index.md` — e e intocavel, ver acima).

Imports: `Path` (3x), `Query` (31x), `require_actor` (9x), `HTTPException` (28x), `status.` (20x)
seguem usados por outros handlers do mesmo arquivo. Expectativa: nenhum import orfao. Confirmar
mesmo assim, com grep, referencia por referencia.

O OpenAPI tinha 43 paths em 2026-08-26 (antes desta task) e deve ficar com 40 depois da Task 1.

## Drift de documentacao ja confirmado por grep (Grupo E — nao e codigo morto)

`.planning/codebase/INTEGRATIONS.md:195` (`POST /api/auth/account/update`), `:196`
(`POST /api/auth/account/email/confirm`), `:269` (`POST /api/comunicacoes/comunicar-comercial`);
`.planning/codebase/STRUCTURE.md:152` e `.planning/codebase/ARCHITECTURE.md:128`
(`GET /core/version` e `/core/info`). Nenhum desses paths existe em nenhum `@router.*` do backend.
Hoje o modulo core expoe apenas `GET /v1/status` (`app/modules/core/routes.py`, `prefix="/v1"`,
protegido por `require_viewer`).
</context>

<tasks>

<task type="auto">
  <name>Task 1: Remover os 3 endpoints legados 410 Gone (codigo, testes e docs)</name>
  <files>app/modules/pedidos/infrastructure/http/routes.py, app/tests/test_pedidos_routes.py, app/tests/test_pedidos_processing_routes.py, docs/api.md, docs/adequacao.md, docs/arquitetura.md</files>
  <action>
Passo 0 — antes de editar qualquer arquivo, capture a BASELINE da suite e anote:
`docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q 2>&1 | tail -3`.
Guarde passed/skipped/failed. Gate final: exatamente 3 `passed` a menos, mesmo `skipped`, mesmo
`failed`. Nao presuma baseline de documento nenhum — ha 3 arquivos de outra sessao no working tree.

Passo 1 — em `routes.py`, apagar os 3 blocos completos (decorator + assinatura + docstring + corpo)
de `adequar_pedidos`, `sem_adequar_pedidos` e `alterar_grade_legacy` (linhas ~520-568), junto das
linhas em branco excedentes: devem sobrar exatamente 2 linhas em branco entre o fim do handler de
`GET /processamentos/{jobId}` e o decorator `@router.post("/{nr_pedido}/aprovar")`. Nao encostar em
nenhum outro handler — `POST /produtos/aprovar`, `POST /{nr_pedido}/aprovar`, `PUT /produtos/grades`
e `POST /processamentos` seguem ativos e tem nome parecido; confira o nome do handler, nao trecho de
string.

Passo 2 — imports orfaos: para cada simbolo usado pelos handlers removidos (`Path`, `Query`,
`Depends`, `HTTPException`, `status`, `require_actor`), contar com `grep -v '^\s*#' <arquivo> | grep -c
'<simbolo>'` e remover do bloco de import somente o que zerar. Pela contagem atual nada deve zerar;
se algo zerar, confirmar referencia por referencia antes de apagar. Mesmo criterio para
`application/schemas.py` — nenhum dos 3 tinha request/response model, entao so mexer la se o grep no
repo inteiro provar orfandade (por isso `schemas.py` nao esta em `files_modified`).

Passo 3 — em `app/tests/test_pedidos_routes.py`, remover as 3 funcoes que so testam a lapide
(`test_adequar_pedidos_exige_actor`, `test_processamentos_legacy_retorna_410`,
`test_alterar_grade_legacy_retorna_410_e_mantem_rbac`) e reescrever a docstring do modulo (linhas
3-4): em vez de dizer que `/adequar` e `/sem_adequar` "sao 410", dizer que foram removidos em
2026-08-26 e que o fluxo duravel e `POST /api/v1/pedidos/processamentos`, coberto em
`test_pedidos_processing_routes.py`. Manter as fixtures `viewer_token`/`actor_token` (seguem usadas
por outros testes) e o comentario de secao "Endpoints de escrita"; confirmar com grep que as duas
fixtures ainda tem uso no arquivo depois da remocao.

Passo 4 — em `app/tests/test_pedidos_processing_routes.py`, dentro de
`test_openapi_declara_header_bounded_legacy_410_e_nao_oferece_retry` (~577-592): trocar as duas
assertions das linhas 590-591 (que exigiam responses `{"410"}`) por assertions de AUSENCIA, uma para
cada um dos 3 paths legados — vira a guarda de regressao que impede a rota voltar. Renomear o teste
tirando o `legacy_410` do nome (sugestao: `test_openapi_declara_header_bounded_e_nao_expoe_rotas_legadas`),
depois de conferir com grep que o nome antigo nao e citado em outro lugar do repo. Manter intactas as
assertions do header `Idempotency-Key` e a de `retry` ausente.

Passo 5 — docs, trocando "aposentado, responde 410" por "removido":
`docs/api.md` — apagar as 3 linhas de tabela de `POST /adequar`, `POST /sem_adequar` e
`PUT /{nr_pedido}/alterar-grade`, sem tocar nas linhas de `/produtos/aprovar`, `/{nr_pedido}/aprovar`
e `/produtos/grades`.
`docs/adequacao.md` — reescrever o paragrafo que comeca em "Os antigos POST /adequar...": as 3 rotas
foram removidas em 2026-08-26 (antes disso respondiam 410 desde a migracao para o processamento
duravel), preservando a frase que explica o porque (nenhum atalho sincrono capaz de carregar a base
inteira nem de perder edicao concorrente) e a frase de que `POST /{nr}/aprovar` continua existindo.
`docs/arquitetura.md` (~127-128) — trocar "o antigo PUT /{nr}/alterar-grade retorna 410" por "foi
removido", mantendo o resto do paragrafo sobre escrita transacional.

Passo 6 — commit unico com caminhos explicitos:
`git add app/modules/pedidos/infrastructure/http/routes.py app/tests/test_pedidos_routes.py app/tests/test_pedidos_processing_routes.py docs/api.md docs/adequacao.md docs/arquitetura.md`
e `git commit -m "refactor(pedidos): remover endpoints legados que so respondiam 410 Gone"`.
Depois rodar `git status --porcelain` e confirmar que os 3 arquivos da outra sessao seguem como
modificados e nao entraram no commit.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api python -c "from app.main import app; p=set(app.openapi()['paths']); bad=[x for x in ['/api/v1/pedidos/adequar','/api/v1/pedidos/sem_adequar','/api/v1/pedidos/{nr_pedido}/alterar-grade'] if x in p]; falta=[x for x in ['/api/v1/pedidos/produtos/aprovar','/api/v1/pedidos/{nr_pedido}/aprovar','/api/v1/pedidos/produtos/grades','/api/v1/pedidos/processamentos'] if x not in p]; print('LEGADOS',bad,'ATIVOS_SUMIDOS',falta,'TOTAL',len(p)); raise SystemExit(1 if bad or falta else 0)"</automated>
    <automated>grep -rn "adequar_pedidos\|sem_adequar_pedidos\|alterar_grade_legacy" app docs --include=*.py --include=*.md | grep -v __pycache__ | wc -l</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q 2>&1 | tail -3</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api python -m ruff check app 2>&1 | tail -3</automated>
    <automated>git status --porcelain</automated>
  </verify>
  <done>
`app.openapi()` tem 40 paths, nenhum dos 3 legados, e as 4 rotas ativas de nome parecido seguem la.
O grep de handlers legados retorna 0. A suite completa fecha com exatamente 3 `passed` a menos que a
baseline do Passo 0, mesmo `skipped` e 0 `failed`. `ruff check app` limpo. `git status --porcelain`
mostra os 3 arquivos da outra sessao ainda modificados e nao commitados. 1 commit novo em `develop`
com os 6 caminhos explicitos e sem mencao a Claude.
  </done>
</task>

<task type="auto">
  <name>Task 2: Registrar o inventario de rotas como documento de auditoria (Grupos A-E)</name>
  <files>.planning/research/ENDPOINTS-AUDIT-2026-08-26.md</files>
  <action>
Criar `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md`. Nesta task NAO alterar nenhum arquivo de
codigo nem os `.planning/codebase/*.md` — o drift deles e registrado como achado, nao corrigido aqui.

Antes de escrever, redump o OpenAPI ja pos-Task 1 e confira contra ele cada path citado nos Grupos
B, C e D (o documento nao pode citar path inexistente):
`docker compose -f .docker/docker-compose.yml exec -T api python -c "from app.main import app; [print(p) for p in sorted(app.openapi()['paths'])]"`.

Secoes obrigatorias:

Cabecalho — titulo, data 2026-08-26, origem (quick task `260826-iln`), escopo (rotas HTTP do
backend cruzadas com o consumo real do frontend) e status: levantamento
validado com a mantenedora; Grupos B e C sao decisao pendente; Grupo D e proibido.

Metodo e limites — 43 paths no OpenAPI antes desta task, 40 depois. O cruzamento cobre APENAS os dois
repositorios. Deixar em destaque que ausencia de consumidor no frontend nao prova ausencia de
consumidor: pode haver Postman, script de ops, job externo, monitoramento ou integracao de terceiro
fora do alcance da busca. Registrar tambem que `GET /` aparece no OpenAPI e nao foi classificado em
nenhum grupo — pendente de confirmacao com a mantenedora.

Grupo A (resolvido nesta quick task) — tabela com `POST /api/v1/pedidos/adequar`,
`POST /api/v1/pedidos/sem_adequar` e `PUT /api/v1/pedidos/{nr_pedido}/alterar-grade`: handler antigo,
por que era seguro remover (respondiam so `410 Gone`; nenhum consumidor pode legitimamente depender
de um 410) e o substituto (`POST /api/v1/pedidos/processamentos` com `mode=adequar`/`mode=sem_adequar`;
`PUT /api/v1/pedidos/produtos/grades` com `expectedVersion`). Citar o hash do commit da Task 1.

Grupo B (sem consumidor achado no frontend, decisao de negocio pendente) —
`GET /api/v1/pedidos/ordens-reserva`, `POST /api/v1/pedidos/{nr_pedido}/aprovar` e
`GET /api/v1/comunicacoes/{communication_id}`: para cada um, o que faz, por que aparece como orfao e
o que precisa ser confirmado antes de qualquer remocao. Registrar de forma inequivoca que
`POST /api/v1/pedidos/{nr_pedido}/aprovar` (pedido inteiro) NAO e o mesmo que
`POST /api/v1/pedidos/produtos/aprovar` (por produto), e que este segundo E usado pelo front e nao
entra em discussao de remocao.

Grupo C (sem consumidor no frontend, plausivelmente infra/ops, nao confirmado) — `GET /health`,
`GET /health/ready`, `GET /metrics` (provavel ALB/ECS/Prometheus); `GET /v1/status` (unico path do
modulo core hoje, protegido por `require_viewer`, possivel consumidor de monitoramento externo);
`POST /api/v1/ingestao/sincronizar` e `GET /api/v1/ingestao/sincronizar/{jobId}` (gatilho manual sem
tela no front, possivel uso via Swagger ou script de ops). Deixar claro que "plausivel" e hipotese,
nao verificacao: nada foi confirmado contra ALB/ECS/Prometheus nesta rodada.

Grupo D (NAO e legado, proibido remover) — `POST /api/v1/parametros`, `PUT /api/v1/parametros/{chave}`
e `DELETE /api/v1/parametros/{chave}`. Explicar que nao ha consumidor no frontend hoje porque o front
so chama o fluxo de change-requests, e que o wiring direto para administrador+ e lacuna conhecida e
documentada: trabalho em andamento da Victoria na Fase 11 do milestone v1.2
(`par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito`), com 1/5 planos executados, o 11-05
invalidado pela decisao de 2026-08-18 e o plano de frontend (11-06) ainda inexistente. Referenciar
`.planning/STATE.md`, secao "Milestone v1.2 (paralelo, Victoria)". Frase explicita: nada do Grupo D
pode ser removido antes de a Fase 11 fechar.

Grupo E (drift de documentacao, nao e codigo morto) — tabela com os 5 achados ja confirmados por grep
(INTEGRATIONS.md linhas 195, 196 e 269; STRUCTURE.md linha 152; ARCHITECTURE.md linha 128), cada um
com o path documentado, o motivo (nenhum `@router.*` correspondente no backend) e a realidade atual
(o modulo core expoe so `GET /v1/status`). Registrar que a correcao natural e regenerar o mapa com
`/gsd-map-codebase` e que isso ficou FORA do escopo desta quick task — nenhum arquivo de
`.planning/codebase/` foi alterado.

Proximos passos — lista curta e acionavel por grupo: B, confirmar com a mantenedora se ha consumidor
fora dos dois repos antes de remover; C, confirmar com quem cuida de infra; D, nao mexer ate a Fase
11 fechar; E, regenerar o mapa do codebase.

Commit unico: `git add .planning/research/ENDPOINTS-AUDIT-2026-08-26.md` e
`git commit -m "docs(planning): registrar inventario de rotas HTTP sem consumidor no frontend"`.
Depois `git status --porcelain` para confirmar que os 3 arquivos da outra sessao seguem intactos e
fora do commit.
  </action>
  <verify>
    <automated>test -f .planning/research/ENDPOINTS-AUDIT-2026-08-26.md && for g in "Grupo A" "Grupo B" "Grupo C" "Grupo D" "Grupo E"; do grep -q "$g" .planning/research/ENDPOINTS-AUDIT-2026-08-26.md || { echo "FALTA $g"; exit 1; }; done; echo OK</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api python -c "from app.main import app; p=set(app.openapi()['paths']); alvo=['/api/v1/pedidos/ordens-reserva','/api/v1/pedidos/{nr_pedido}/aprovar','/api/v1/comunicacoes/{communication_id}','/health','/health/ready','/metrics','/v1/status','/api/v1/ingestao/sincronizar','/api/v1/ingestao/sincronizar/{jobId}','/api/v1/parametros','/api/v1/parametros/{chave}']; miss=[x for x in alvo if x not in p]; print('CITADOS_QUE_NAO_EXISTEM',miss); raise SystemExit(1 if miss else 0)"</automated>
    <automated>grep -c "produtos/aprovar" .planning/research/ENDPOINTS-AUDIT-2026-08-26.md</automated>
    <automated>git status --porcelain</automated>
  </verify>
  <done>
O documento existe com os 5 grupos; todo path citado nos Grupos B/C/D existe no OpenAPI; o documento
distingue explicitamente `POST /produtos/aprovar` (ativo, usado pelo front) de
`POST /{nr_pedido}/aprovar` (Grupo B); o Grupo D referencia a Fase 11 e proibe remocao; nenhum
arquivo de codigo nem de `.planning/codebase/` foi alterado nesta task; 1 commit novo com o caminho
explicito do documento e os 3 arquivos da outra sessao seguem fora dele.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| cliente HTTP -> API de pedidos | Remocao de rota muda a resposta de `410` para `404`/`405` para quem ainda chamar |
| sessao de trabalho -> git | Working tree compartilhado com 3 arquivos nao commitados de outra sessao |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-iln-01 | Denial of Service | Rota ativa removida por engano (`/produtos/aprovar`, `/{nr}/aprovar`, `/produtos/grades`, `/processamentos`) | mitigate | Verify da Task 1 falha se qualquer uma das 4 sumir do OpenAPI |
| T-iln-02 | Tampering | Commit acidental dos 3 arquivos da outra sessao | mitigate | `git add` so com caminhos explicitos; `-A`/`.`/`-a`/`stash`/`reset --hard` proibidos; `git status --porcelain` como verify em ambas as tasks |
| T-iln-03 | Elevation of Privilege | Remocao de `require_actor` junto com os handlers, afrouxando RBAC de rota vizinha | mitigate | Passo 2 so remove import que zerar por grep; `require_actor` tem 9 usos hoje |
| T-iln-04 | Repudiation | Remocao futura de rota do Grupo B/C/D sem registro de decisao | mitigate | Documento de auditoria registra grupo, razao e o que confirmar antes de remover |
| T-iln-SC | Tampering | npm/pip/cargo installs | n/a | Nenhuma dependencia nova e instalada nesta task |
</threat_model>

<verification>
1. OpenAPI pos-task: 40 paths, sem os 3 legados, com as 4 rotas ativas de nome parecido.
2. Suite completa no Docker: baseline menos 3 `passed`, mesmo `skipped`, 0 `failed`.
3. `ruff check app` limpo.
4. Grep de `adequar_pedidos|sem_adequar_pedidos|alterar_grade_legacy` em `app/` e `docs/` retorna 0.
5. Documento de auditoria com os 5 grupos e sem citar path inexistente.
6. `git log --oneline -2` mostra exatamente os 2 commits desta task; `git status --porcelain` segue
   com os 3 arquivos da outra sessao modificados e nao commitados.
</verification>

<success_criteria>
- 3 endpoints legados fora do codigo, dos testes e dos docs, sem tocar em rota ativa.
- Guarda de regressao no teste de OpenAPI impedindo o retorno dos 3 paths.
- `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` com Grupos A-E, limites do metodo explicitos e
  a proibicao do Grupo D amarrada a Fase 11.
- 2 commits atomicos em `develop`, caminhos explicitos, sem mencao a Claude, sem arrastar os 3
  arquivos da outra sessao.
</success_criteria>

<output>
Create `.planning/quick/260826-iln-remover-endpoints-legados-de-pedidos-em-/260826-iln-SUMMARY.md` when done
</output>
