---
phase: quick/260909-ctm
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260909-CTM]
files_modified:
  - app/modules/pedidos/infrastructure/http/routes.py
  - app/modules/pedidos/service.py
  - app/modules/pedidos/application/consultas.py
  - app/modules/pedidos/application/schemas.py
  - app/modules/pedidos/application/ports.py
  - app/modules/pedidos/infrastructure/repositorio_consultas.py
  - app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py
  - app/modules/pedidos/domain/consultas.py
  - app/modules/pedidos/domain/edicao_grade.py
  - app/tests/test_pedidos_routes.py
  - app/tests/test_pedidos_read_projection.py
  - README.md
  - docs/api.md
  - .planning/codebase/ARCHITECTURE.md

must_haves:
  truths:
    - "O OpenAPI do app tem 35 paths (era 36) e nao lista mais o path de listagem de OR sob /api/v1/pedidos"
    - "As demais rotas de pedidos continuam registradas: /produtos, /produtos/clientes, /lookup, /resumo, /evolucao-faturamento, /processamentos, /produtos/grades, /produtos/aprovar, /{nr_pedido}/aprovar"
    - "Nenhum arquivo .py em app/ referencia os simbolos da fatia removida (funcao de listagem, dataclass de consulta, os 3 schemas Pydantic, o validador de cursor privado)"
    - "A tabela ordens_reserva continua sendo lida pelas projecoes que sobrevivem (resumo, produtos, clientes do produto, lookup) e pelo motor de adequacao"
    - "Os helpers compartilhados _epoch_ms e _estoque_disponivel e a dataclass PaginaCursor continuam existindo e em uso"
    - "Suite: 996 passed / 18 skipped / 0 failed (baseline medida em 2026-09-09: 999 passed / 18 skipped / 0 failed, menos os 3 testes dedicados removidos)"
    - "ruff check passa (baseline: All checks passed)"
    - "README.md, docs/api.md e .planning/codebase/ARCHITECTURE.md nao documentam mais o endpoint removido"
    - "O corpo de _qt_solicitada_ou_fallback em domain/edicao_grade.py fica byte-identico; so o docstring muda"
  artifacts:
    - path: "app/modules/pedidos/infrastructure/http/routes.py"
      provides: "Router de pedidos sem o handler de listagem de OR"
      contains: "/evolucao-faturamento"
    - path: "app/modules/pedidos/application/ports.py"
      provides: "Protocol PedidosReadRepository com 5 membros (era 6)"
      contains: "async def listar_lookup"
    - path: "app/modules/pedidos/infrastructure/repositorio_consultas.py"
      provides: "Repositorio de leitura com as 5 projecoes que sobrevivem e o helper compartilhado de estoque"
      contains: "_estoque_disponivel"
    - path: "app/modules/pedidos/domain/consultas.py"
      provides: "Dataclasses de consulta sem a da fatia removida, PaginaCursor preservada"
      contains: "class PaginaCursor"
    - path: "app/modules/pedidos/domain/edicao_grade.py"
      provides: "Fallback de quantidade solicitada com logica intacta e docstring sem referencia a arquivo inexistente"
      contains: "return max(int(raw or 0), 0)"
  key_links:
    - from: "app/modules/pedidos/infrastructure/repositorio_consultas.py"
      to: "app/modules/pedidos/infrastructure/produtos/listar_produtos.py"
      via: "import tardio de listar_produtos_sql, que continua usando _epoch_ms e _estoque_disponivel"
      pattern: "listar_produtos_sql"
    - from: "app/modules/pedidos/infrastructure/http/routes.py"
      to: "app/modules/pedidos/application/schemas.py"
      via: "bloco de import de schemas sem o envelope removido, demais response_model intactos"
      pattern: "PedidosLookupPageOut"
---

<objective>
Remover a fatia vertical inteira do endpoint `GET /api/v1/pedidos/ordens-reserva` — rota, service,
caso de uso, port, repositorio, SQL, schemas Pydantic, dataclass de consulta e testes dedicados —
por nao ter nenhum consumidor no `frontend` (confirmado por investigacao previa;
ver `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md`, Grupo B).

Purpose: reduzir superficie HTTP e codigo morto num modulo que ainda vai ser mexido pelas Phases
13/17/18/19 do v1.3 — cada projecao morta em `repositorio_consultas.py` e um arquivo a mais que os
executores dessas fases precisam ler e nao usar.

Output: 5 rotas de leitura em pedidos (era 6), OpenAPI com 35 paths, `SqlPedidosReadRepository` com
5 projecoes, um arquivo SQL deletado, 3 testes a menos e docs coerentes com a remocao. Nada e
adicionado nem refatorado.
</objective>

<execution_context>
@$HOME/.claude/gsd-core/workflows/execute-plan.md
@$HOME/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@.claude/CLAUDE.md
@app/modules/pedidos/infrastructure/http/routes.py
@app/modules/pedidos/application/consultas.py
@app/modules/pedidos/application/ports.py
@app/modules/pedidos/infrastructure/repositorio_consultas.py

## Ambiente (medido em 2026-09-09, nao redescobrir)

Branch: `develop`, working tree limpo no momento do planejamento.

`uv run` **nao funciona neste repo** (o caminho contem "Área" e quebra o trampoline do uv).
Interprete sempre por caminho explicito: `./.venv/Scripts/python.exe -m pytest ...`,
`./.venv/Scripts/ruff.exe check`.

A suite completa **nao coleta** nativamente no Windows: `app/tests/test_pedidos_processing_sem_adequar_memory_024.py`
faz `import resource` (modulo somente Unix) e interrompe a coleta com `ModuleNotFoundError`. Esse
arquivo so roda em Docker/Linux e nao tem nada a ver com esta task. Baseline nativa capturada com
`--ignore` nele:

- `./.venv/Scripts/python.exe -m pytest -q --ignore=app/tests/test_pedidos_processing_sem_adequar_memory_024.py`
  -> **999 passed, 18 skipped, 0 failed** (106s)
- `./.venv/Scripts/ruff.exe check` -> **All checks passed**
- `app/tests/test_pedidos_routes.py` + `app/tests/test_pedidos_read_projection.py` juntos -> **39 passed** (8.5s)
- `app.openapi()["paths"]` -> **36 paths**, o path alvo presente

Depois desta task os numeros esperados sao: **996 passed / 18 skipped / 0 failed**, **35 paths**,
e os dois arquivos de teste juntos com **36 passed**.

## Regras de git desta task (obrigatorias)

Branch `develop`, sem branch nova, sem worktree, sem PR. **Nada vai para o GitHub**: nenhum `git push`
sem a mantenedora aprovar antes. Mensagens de commit em pt-BR, **sem prefixo convencional**
(`feat:`/`chore:`/`docs:` sao proibidos), sem `Co-Authored-By` e sem mencao a Claude.

Proibido: `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset --hard`,
`git checkout -- <path>`, `git clean`. Commits sempre com caminhos explicitos.

Atencao no Task 2: o arquivo deletado deve sair por `git rm <path>` (que ja o deixa staged).
Nao misturar um pathspec ja deletado dentro de um `git add` posterior — isso aborta o resto do
`git add` silenciosamente, sem mensagem de erro.

## Fatos ja apurados sobre a fatia (verificados por grep em todo o repo)

Exclusivos da fatia — podem sair:

| Simbolo | Onde | Unico consumidor |
|---|---|---|
| handler da rota (linhas 299-314) | `infrastructure/http/routes.py` | HTTP |
| `listar_ordens_reserva_page` (331-336) + entrada no `__all__` (358) | `service.py` | o handler |
| caso de uso `listar_ordens_reserva` (324-359) | `application/consultas.py` | o service |
| `_ordens_reserva_cursor_valido` (95-102) | `application/consultas.py` | o caso de uso — **ruff nao flagra funcao privada de modulo nao usada, tem que sair na mao** |
| membro do `Protocol` (37-39) | `application/ports.py` | o caso de uso |
| metodo do repositorio (145-152) | `infrastructure/repositorio_consultas.py` | o Protocol |
| arquivo inteiro | `infrastructure/produtos/listar_ordens_reserva.py` | o repositorio |
| `OrdensReservaPageOut` (461-468) | `application/schemas.py` | o handler |
| `OrdemReservaOut` (446-458) | `application/schemas.py` | `OrdensReservaPageOut` |
| `ClienteDaOrdem` (432-443) | `application/schemas.py` | `OrdemReservaOut` |
| `ConsultaOrdensReserva` (74-77) | `domain/consultas.py` | ports + caso de uso + repositorio + os 2 testes de projecao |

**Compartilhados — NAO remover** (o arquivo SQL deletado os importava, mas nao e o dono deles):

- `_epoch_ms` (`infrastructure/sql/cursor.py`) — tambem usado por `listar_clientes_produto.py` e `listar_produtos.py`
- `_estoque_disponivel` (metodo de `SqlPedidosReadRepository`) — tambem usado por `listar_clientes_produto.py`, `listar_produtos.py`, `resumo/obter_resumo.py` e mockado em `test_pedidos_read_projection.py:133`
- `PaginaCursor` (`domain/consultas.py`) — assinatura de todas as projecoes
- tabela `ordens_reserva` e model `OrdemReserva` — lidos por todas as outras projecoes, pelo motor e por dezenas de testes

Testes dedicados (os unicos do repo que exercitam a fatia):

- `app/tests/test_pedidos_routes.py:575-606` — banner de secao + `test_listar_ordens_reserva_retorna_envelope_bounded`; e o **fim do arquivo** (606 linhas), depois da remocao o arquivo termina em `assert float(row.preco1) == 20.0` (linha 572)
- `app/tests/test_pedidos_read_projection.py:436-497` — `test_ordens_reserva_pagina_pares_e_nao_mistura_canais_do_mesmo_produto`, com helper `item` aninhado dentro dela
- `app/tests/test_pedidos_read_projection.py:727-774` — `test_ordens_reserva_cursor_ignora_or_sem_item_elegivel`
- o helper `_item_or` e usado por muitos outros testes desses arquivos: **preservar**
- em `test_pedidos_routes.py`, `AsyncMock`, `patch` e `auth_header` tem 18 outros usos cada: **imports ficam**

Nenhum teste quebra por tabela: `test_pedidos_processing_routes.py:577` (`test_openapi_declara_header_bounded_e_nao_expoe_rotas_legadas`) so faz assert de **ausencia** de paths legados e da presenca do POST de processamentos — nao menciona o path removido. Nenhum teste faz assert sobre o conteudo de `docs/api.md` (`test_docs_seguranca.py` cobre so `docs/seguranca.md` e `docs/index.md`).

Docs que descrevem o endpoint (as 3 unicas fora de historico de planning): `README.md:94` (uma linha de tabela), `docs/api.md:30` (linha de tabela) e `docs/api.md:70-81` (subsecao `### Ordens de reserva bounded` inteira; nada linka esse anchor), `.planning/codebase/ARCHITECTURE.md:124` (o path dentro da enumeracao de rotas de `pedidos/routes.py`).

Arquivos de historico de planning (`.planning/research/*`, `.planning/phases/*`, `.planning/quick/*`) mencionam a fatia e **nao devem ser editados** — sao registro do que era verdade na epoca.

## Ponto sensivel: `domain/edicao_grade.py`

O docstring de `_qt_solicitada_ou_fallback` (linhas 21-27) cita o arquivo SQL que vai ser deletado.
E **so comentario explicativo**. A logica de fallback dessa funcao e da Phase 20 (decisao PD-03,
preserva `0` explicito em vez de reinflar) e **nao pode ser tocada**. Edicao permitida ali: apenas
a frase do docstring que aponta para o arquivo inexistente.
</context>

<!-- planner-discipline-allow: listar_ordens_reserva -->
<!-- planner-discipline-allow: ConsultaOrdensReserva -->
<!-- planner-discipline-allow: OrdensReservaPageOut -->
<!-- planner-discipline-allow: OrdemReservaOut -->
<!-- planner-discipline-allow: ClienteDaOrdem -->
<!-- planner-discipline-allow: _ordens_reserva_cursor_valido -->
<!-- planner-discipline-allow: /api/v1/pedidos/ordens-reserva -->
<!-- planner-discipline-allow: ordens-reserva -->

<tasks>

<task type="auto">
  <name>Task 1: Remover a ponta HTTP — rota, service, caso de uso e schemas</name>
  <files>app/modules/pedidos/infrastructure/http/routes.py, app/modules/pedidos/service.py, app/modules/pedidos/application/consultas.py, app/modules/pedidos/application/schemas.py, app/tests/test_pedidos_routes.py</files>
  <action>
Remover a metade de cima da fatia, na ordem consumidor -> produtor. Nao tocar em ports, repositorio,
SQL, dataclass de dominio nem nos testes de projecao — eles saem no Task 2, e o estado intermediario
depois deste task continua importavel e verde (as camadas de baixo ficam mortas mas validas).

`infrastructure/http/routes.py`: apagar o handler decorado com o path de listagem de OR (linhas
299-314 hoje, entre o handler de alertas e o de `/evolucao-faturamento`) e tirar `OrdensReservaPageOut`
do bloco de import de schemas (linha 36). Todos os outros decorators, handlers e imports ficam —
`HTTPException`, `Query`, `CursorInvalidoError` e `AsyncSession` seguem usados por vizinhos.

`service.py`: apagar `listar_ordens_reserva_page` (331-336) e a entrada correspondente no `__all__`
(358). O import `consultas_app` continua usado pelas outras 4 funcoes de pagina.

`application/consultas.py`: apagar o caso de uso `listar_ordens_reserva` (324-359); apagar tambem
`_ordens_reserva_cursor_valido` (95-102), que fica orfao e nao e detectado por lint (ruff nao acusa
funcao privada de modulo sem uso — se ficar, e codigo morto silencioso); tirar `ConsultaOrdensReserva`
do bloco de import de `domain.consultas` (linha 16), mantendo os outros 8 nomes importados. Os
outros validadores privados de cursor (`_alerta_cursor_valido`, `_produto_cursor_valido`,
`_cliente_cursor_valido`, `_lookup_cursor_valido`) ficam.

`application/schemas.py`: apagar os 3 modelos da cadeia exclusiva da rota — `OrdensReservaPageOut`
(461-468), `OrdemReservaOut` (446-458) e `ClienteDaOrdem` (432-443). Confirmado por grep que nada
mais em `app/` os referencia. `AprovarProdutoResponse` (acima) e `EvolucaoFaturamentoItem` (abaixo)
ficam intactos; nao existe `__all__` neste arquivo para atualizar.

`app/tests/test_pedidos_routes.py`: apagar o banner de secao de 3 linhas de comentario e o teste
`test_listar_ordens_reserva_retorna_envelope_bounded` (575-606, fim do arquivo). O arquivo passa a
terminar na linha 572 (`assert float(row.preco1) == 20.0`), sem linhas em branco sobrando no fim.
Nao mexer nos imports do modulo: `AsyncMock`, `patch` e `auth_header` tem 18 outros usos cada.

Commit ao fim do task, caminhos explicitos, mensagem em pt-BR sem prefixo (ex.: "Remove a ponta HTTP
do endpoint de listagem de OR, sem consumidor no frontend").
  </action>
  <verify>
    <automated>./.venv/Scripts/ruff.exe check && ./.venv/Scripts/python.exe -c "from app.main import app; p=app.openapi()['paths']; assert '/api/v1/pedidos/ordens-reserva' not in p, 'rota ainda registrada'; assert len(p) == 35, len(p); assert '/api/v1/pedidos/produtos/clientes' in p; assert '/api/v1/pedidos/lookup' in p; assert '/api/v1/pedidos/evolucao-faturamento' in p; print('openapi ok', len(p))" && ./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_routes.py app/tests/test_pedidos_read_projection.py -q</automated>
  </verify>
  <done>
`ruff check` limpo; OpenAPI com 35 paths e sem o path removido, com as 3 rotas vizinhas ainda
presentes; os dois arquivos de teste somam **38 passed** (era 39). Nenhum import orfao (ruff `F`
esta no `select` do pyproject e pegaria).
  </done>
</task>

<task type="auto">
  <name>Task 2: Remover port, repositorio, SQL, dataclass de dominio e testes de projecao</name>
  <files>app/modules/pedidos/application/ports.py, app/modules/pedidos/infrastructure/repositorio_consultas.py, app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py, app/modules/pedidos/domain/consultas.py, app/tests/test_pedidos_read_projection.py</files>
  <action>
Remover a metade de baixo da fatia. Depois deste task nao sobra nenhuma referencia a fatia em `app/`.

`application/ports.py`: apagar o membro `listar_ordens_reserva` do `Protocol PedidosReadRepository`
(37-39) e tirar `ConsultaOrdensReserva` do bloco de import de `domain.consultas` (linha 17). O
Protocol fica com 5 membros: resumo, alertas, produtos, clientes do produto e lookup. Os dataclasses
de escrita (`OrdemProdutoState`, `ResultadoAprovacao*`) e o `PedidosWritePort` nao sao tocados.

`infrastructure/repositorio_consultas.py`: apagar o metodo `listar_ordens_reserva` (145-152) e tirar
`ConsultaOrdensReserva` do bloco de import (linha 19). No docstring do modulo (linhas 3-7), a lista
de projecoes compartilhadas deve deixar de enumerar a que foi removida, ficando resumo, alertas,
produtos e lookup. **Nao tocar em `_estoque_disponivel`** (29-108): ele e o helper de estoque
compartilhado por `listar_produtos.py`, `listar_clientes_produto.py` e `resumo/obter_resumo.py`, e e
mockado em `test_pedidos_read_projection.py:133`.

Deletar o arquivo `app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py` inteiro com
`git rm` (nao apagar por fora do git). Os simbolos que ele importava — `_epoch_ms` de
`infrastructure/sql/cursor.py` e `PaginaCursor` de `domain/consultas.py` — **permanecem**, porque
tem outros consumidores.

`domain/consultas.py`: apagar somente a dataclass `ConsultaOrdensReserva` (74-77). `PaginaCursor`
(80-85) e as outras `Consulta*` ficam.

`app/tests/test_pedidos_read_projection.py`: apagar as duas funcoes de teste dedicadas por inteiro —
`test_ordens_reserva_pagina_pares_e_nao_mistura_canais_do_mesmo_produto` (436-497, incluindo o helper
`item` aninhado dentro dela) e `test_ordens_reserva_cursor_ignora_or_sem_item_elegivel` (727-774) — e
tirar `ConsultaOrdensReserva` do bloco de import de `domain.consultas` (linha 22), mantendo os outros
4 nomes. Preservar `_item_or`, o mock de `_estoque_disponivel` na linha 133 e todos os outros testes,
inclusive os vizinhos imediatos (`test_alertas_operacionais_retorna_categorias_esperadas` e
`test_read_projection_orcamento_pedido_cobre_todos_os_produtos_do_pedido`). Manter exatamente 2 linhas
em branco entre as funcoes que passam a ser vizinhas.

Commit ao fim do task, caminhos explicitos, mensagem em pt-BR sem prefixo. Lembrar: o arquivo
deletado ja esta staged pelo `git rm`; nao repetir esse caminho num `git add` junto com os outros.
  </action>
  <verify>
    <automated>./.venv/Scripts/ruff.exe check && ! grep -rq "listar_ordens_reserva" app/ --include="*.py" && ! grep -rqE "ConsultaOrdensReserva|OrdensReservaPageOut|OrdemReservaOut|ClienteDaOrdem|_ordens_reserva_cursor_valido" app/ --include="*.py" && test ! -f app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py && grep -q "class PaginaCursor" app/modules/pedidos/domain/consultas.py && grep -q "_estoque_disponivel" app/modules/pedidos/infrastructure/repositorio_consultas.py && grep -q "_epoch_ms" app/modules/pedidos/infrastructure/produtos/listar_produtos.py && grep -q "_epoch_ms" app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py && ./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_routes.py app/tests/test_pedidos_read_projection.py -q</automated>
  </verify>
  <done>
`ruff check` limpo; zero ocorrencias dos simbolos da fatia em `app/**/*.py`; o arquivo SQL nao existe
mais; `PaginaCursor`, `_estoque_disponivel` e os dois consumidores de `_epoch_ms` seguem no lugar; os
dois arquivos de teste somam **36 passed** (39 na baseline, 3 testes removidos).
  </done>
</task>

<task type="auto">
  <name>Task 3: Docs coerentes com a remocao, comentario orfao e suite completa</name>
  <files>README.md, docs/api.md, .planning/codebase/ARCHITECTURE.md, app/modules/pedidos/domain/edicao_grade.py</files>
  <action>
Fechar as pontas de documentacao que passariam a descrever um endpoint inexistente, e a unica
referencia em codigo a um arquivo que nao existe mais.

`README.md`: apagar a linha da tabela **Pedidos** que descreve o endpoint removido (linha 94, entre
`GET /resumo` e `GET /evolucao-faturamento`). Nenhuma outra linha do README menciona a fatia.

`docs/api.md`: apagar a linha da tabela de rotas de pedidos (linha 30) e a subsecao inteira
`### Ordens de reserva bounded` (70-81, do cabecalho ate a linha em branco antes de
`### Edicao versionada de grade`). Verificado: nenhum teste faz assert sobre este arquivo e nada
linka esse anchor.

`.planning/codebase/ARCHITECTURE.md`: na linha 124, tirar o path removido da enumeracao de rotas de
`pedidos/routes.py` e registrar a remocao no parenteses que ja existe no fim da linha (mesmo estilo
usado pela quick task 260826-iln para as rotas legadas). Nao editar nenhum outro arquivo de
`.planning/` — `research/`, `phases/` e `quick/` sao historico e ficam como estao.

`app/modules/pedidos/domain/edicao_grade.py`: **edicao somente de comentario.** No docstring de
`_qt_solicitada_ou_fallback` (21-27), reescrever apenas a frase que aponta para o arquivo SQL
deletado, de modo que ela explique o motivo do fallback (cobrir ORs gravadas antes do campo existir)
sem nomear arquivo nenhum. Preservar palavra por palavra a frase seguinte, sobre `0` explicito ser
valor valido e preservado como zero (e a decisao PD-03 da Phase 20). O corpo da funcao (4 linhas, de
`raw = item.get(...)` ate o `return`), a assinatura, e todo o resto do arquivo ficam byte-identicos.
Nao mexer em `montar_grade_atualizada` nem em `_money`.

Rodar a suite completa nativa com o `--ignore` do teste que so roda em Linux (justificativa em
`<context>`) e o `ruff check` final antes do commit. Commit ao fim do task, caminhos explicitos,
mensagem em pt-BR sem prefixo.

Ao gerar o SUMMARY, registrar: numeros antes/depois (36 -> 35 paths, 999 -> 996 passed), que
`.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` Grupo B fica com um item resolvido, e que
`POST /{nr_pedido}/aprovar` e as comunicacoes por id (os outros dois itens do Grupo B) seguem em
aberto e fora do escopo desta task.
  </action>
  <verify>
    <automated>! grep -rq "ordens-reserva" README.md docs/ .planning/codebase/ && grep -q 'raw = item.get("qt_solicitada")' app/modules/pedidos/domain/edicao_grade.py && grep -q "return max(int(raw or 0), 0)" app/modules/pedidos/domain/edicao_grade.py && grep -q "preservado" app/modules/pedidos/domain/edicao_grade.py && ./.venv/Scripts/ruff.exe check && ./.venv/Scripts/python.exe -m pytest -q --ignore=app/tests/test_pedidos_processing_sem_adequar_memory_024.py</automated>
  </verify>
  <done>
Nenhuma mencao ao path removido em `README.md`, `docs/` ou `.planning/codebase/`; as 2 linhas de
corpo de `_qt_solicitada_ou_fallback` e a frase do `0` preservado seguem intactas no arquivo;
`ruff check` limpo; suite completa em **996 passed / 18 skipped / 0 failed** (baseline 999/18/0
menos os 3 testes removidos).
  </done>
</task>

</tasks>

<threat_model>

## Trust Boundaries

| Boundary | Description |
|---|---|
| cliente HTTP -> API `/api/v1/pedidos` | superficie autenticada por `require_viewer`; esta task **reduz** a superficie em 1 rota, nao abre nenhuma |
| repositorio de leitura -> Postgres | SQL parametrizado; o arquivo removido nao era o dono de nenhum helper compartilhado |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|---|---|---|---|---|
| T-CTM-01 | Information Disclosure | rota de leitura de OR removida | mitigate | remocao total elimina a exposicao de `codigo_linx`/grade por HTTP levantada em `.planning/research/PITFALLS.md:231` |
| T-CTM-02 | Denial of Service | remocao excessiva de helper compartilhado (`_estoque_disponivel`, `_epoch_ms`, `PaginaCursor`) quebrando as outras 5 projecoes | mitigate | greps positivos obrigatorios no verify do Task 2 + suite completa no Task 3 (996 passed) |
| T-CTM-03 | Tampering | edicao acidental da logica de orcamento em `domain/edicao_grade.py` (regra de negocio ±5% da Phase 20) | mitigate | edicao restrita a comentario; verify do Task 3 exige as 2 linhas de corpo e a frase do `0` preservado intactas |
| T-CTM-SC | Tampering | dependencias | accept | nenhum `install` de pacote nesta task — e remocao pura, sem npm/pip/cargo |

</threat_model>

<verification>
1. `./.venv/Scripts/ruff.exe check` -> All checks passed
2. `./.venv/Scripts/python.exe -m pytest -q --ignore=app/tests/test_pedidos_processing_sem_adequar_memory_024.py` -> 996 passed, 18 skipped, 0 failed
3. OpenAPI: 35 paths, path removido ausente, vizinhas presentes
4. Zero ocorrencias dos simbolos da fatia em `app/**/*.py`
5. `git log --oneline -3` -> 3 commits em pt-BR, sem prefixo convencional, sem `Co-Authored-By`
6. `git status --porcelain` -> vazio ao fim (nada nao commitado, nada nao rastreado alem do esperado)
7. Nenhum `git push` executado
</verification>

<success_criteria>
- 3 commits atomicos em `develop`, cada um com a suite/lint do seu task verdes
- OpenAPI: 36 -> 35 paths; suite: 999 -> 996 passed, 18 skipped, 0 failed; ruff limpo
- `app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py` deletado via `git rm`
- Zero simbolos orfaos e zero imports quebrados (garantido por ruff `F` + suite)
- `domain/edicao_grade.py` com diff exclusivamente de docstring
- Nada adicionado: nenhum teste novo, nenhum helper novo, nenhuma refatoracao alem da remocao
</success_criteria>

<output>
Create `.planning/quick/260909-ctm-remover-a-fatia-vertical-completa-do-end/260909-ctm-SUMMARY.md` when done
</output>
