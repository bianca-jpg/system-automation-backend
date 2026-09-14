---
phase: quick/260826-kla
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260826-KLA]
files_modified:
  - app/modules/pedidos/infrastructure/sql/__init__.py
  - app/modules/pedidos/infrastructure/sql/estoque_cte.py
  - app/modules/pedidos/infrastructure/sql/cursor.py
  - app/modules/pedidos/infrastructure/sql/credito.py
  - app/modules/pedidos/infrastructure/sql/alertas_cte.py
  - app/modules/pedidos/infrastructure/produtos/__init__.py
  - app/modules/pedidos/infrastructure/produtos/listar_produtos.py
  - app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py
  - app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py
  - app/modules/pedidos/infrastructure/produtos/listar_lookup.py
  - app/modules/pedidos/infrastructure/resumo/__init__.py
  - app/modules/pedidos/infrastructure/resumo/obter_resumo.py
  - app/modules/pedidos/infrastructure/resumo/stats_erp.py
  - app/modules/pedidos/infrastructure/resumo/stats_canais.py
  - app/modules/pedidos/infrastructure/resumo/listar_alertas.py
  - app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py
  - app/modules/pedidos/infrastructure/repositorio_consultas.py
  - app/modules/pedidos/infrastructure/repositorio_produtos.py
  - app/modules/pedidos/infrastructure/repositorio_resumo.py
  - app/modules/pedidos/infrastructure/write_adapter.py
  - app/modules/pedidos/infrastructure/models.py
  - app/modules/ingestao/infrastructure/realtime_snapshot.py
  - app/modules/pedidos/processing/infrastructure/adapters.py
  - app/modules/pedidos/processing/infrastructure/chunking.py
  - app/modules/pedidos/processing/infrastructure/repository_read.py
  - app/modules/pedidos/processing/infrastructure/repository_writer.py
  - app/modules/pedidos/processing/infrastructure/unit_of_work.py
  - app/modules/pedidos/processing/infrastructure/repository.py
  - app/modules/pedidos/service.py
  - app/tests/test_pedidos_processing_repository.py
  - .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py

must_haves:
  truths:
    - "SqlPedidosReadRepository continua com os mesmos 6 metodos publicos e as mesmas assinaturas de antes (obter_resumo, listar_alertas, listar_produtos, listar_clientes_produto, listar_lookup, listar_ordens_reserva)"
    - "repositorio_produtos.py, repositorio_resumo.py e processing/infrastructure/repository.py nao existem mais, e nenhum arquivo .py do repositorio os importa"
    - "Nao existe arquivo de compatibilidade/re-export no lugar dos 3 arquivos apagados"
    - "Todo literal SQL multilinha dos 3 arquivos apagados aparece byte-identico em algum dos arquivos novos"
    - "Nenhum arquivo criado ou tocado por esta task passa de 310 linhas"
    - "A suite completa no Docker fecha com exatamente o mesmo numero de passed/skipped/failed da baseline capturada antes da primeira edicao"
    - "ruff check app/ passa limpo (nenhum F401 de import morto nem F821 de nome nao definido nos modulos novos)"
    - "Os 3 arquivos nao commitados de outra sessao (auth/routes.py, auth/repositorio_otp.py, test_docs_seguranca.py) seguem modificados e fora de todos os commits desta task"
  artifacts:
    - path: "app/modules/pedidos/infrastructure/sql/estoque_cte.py"
      provides: "Helpers de CTE de estoque disponivel e de pares (compartilhados pelas projecoes de produto)"
      contains: "_pairs_cte"
    - path: "app/modules/pedidos/infrastructure/sql/cursor.py"
      provides: "Helpers de paginacao por cursor, epoch e ordenacao de tamanhos"
      contains: "_cursor_parameter"
    - path: "app/modules/pedidos/infrastructure/sql/credito.py"
      provides: "Expressao SQL de credito bloqueado (equivalente a domain.is_sem_credito)"
      contains: "_credito_bloqueado_sql"
    - path: "app/modules/pedidos/infrastructure/sql/alertas_cte.py"
      provides: "CTE de alertas e suas duas variantes com escopo"
      contains: "_alerts_cte_pedidos"
    - path: "app/modules/pedidos/infrastructure/produtos/listar_produtos.py"
      provides: "listar_produtos_sql"
      contains: "async def listar_produtos_sql"
    - path: "app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py"
      provides: "listar_clientes_produto_sql"
      contains: "async def listar_clientes_produto_sql"
    - path: "app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py"
      provides: "listar_ordens_reserva_sql"
      contains: "async def listar_ordens_reserva_sql"
    - path: "app/modules/pedidos/infrastructure/produtos/listar_lookup.py"
      provides: "listar_lookup_sql"
      contains: "async def listar_lookup_sql"
    - path: "app/modules/pedidos/infrastructure/resumo/obter_resumo.py"
      provides: "obter_resumo_sql como orquestrador curto (sem os dois blocos SQL grandes embutidos)"
      contains: "async def obter_resumo_sql"
    - path: "app/modules/pedidos/infrastructure/resumo/stats_erp.py"
      provides: "_erp_orders_stats — bloco SQL de erp_orders + agregacao erp_count/erp_billing"
      contains: "async def _erp_orders_stats"
    - path: "app/modules/pedidos/infrastructure/resumo/stats_canais.py"
      provides: "_channel_stats — bloco SQL gigante de stats por canal + parsing das linhas"
      contains: "async def _channel_stats"
    - path: "app/modules/pedidos/infrastructure/resumo/listar_alertas.py"
      provides: "listar_alertas_sql (9 categorias de alerta operacional)"
      contains: "async def listar_alertas_sql"
    - path: "app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py"
      provides: "listar_chaves_alertas_ativos_sql — snapshot leve consumido por ingestao, write_adapter e processing"
      contains: "async def listar_chaves_alertas_ativos_sql"
    - path: "app/modules/pedidos/processing/infrastructure/repository_read.py"
      provides: "SqlAlchemyProcessingRepository + _to_spec/_to_pair"
      contains: "class SqlAlchemyProcessingRepository"
    - path: "app/modules/pedidos/processing/infrastructure/repository_writer.py"
      provides: "SqlAlchemyProcessingWriter"
      contains: "class SqlAlchemyProcessingWriter"
    - path: "app/modules/pedidos/processing/infrastructure/unit_of_work.py"
      provides: "SqlAlchemyProcessingUnitOfWork"
      contains: "class SqlAlchemyProcessingUnitOfWork"
    - path: "app/modules/pedidos/processing/infrastructure/chunking.py"
      provides: "_chunks compartilhado entre repository_read e repository_writer"
      contains: "def _chunks"
  key_links:
    - from: "app/modules/pedidos/infrastructure/repositorio_consultas.py"
      to: "app/modules/pedidos/infrastructure/produtos/"
      via: "imports lazy dentro dos metodos de SqlPedidosReadRepository"
      pattern: "infrastructure\\.produtos\\.listar_"
    - from: "app/modules/pedidos/infrastructure/repositorio_consultas.py"
      to: "app/modules/pedidos/infrastructure/resumo/"
      via: "imports lazy dentro dos metodos de SqlPedidosReadRepository"
      pattern: "infrastructure\\.resumo\\.(obter_resumo|listar_alertas)"
    - from: "app/modules/pedidos/infrastructure/write_adapter.py"
      to: "app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py"
      via: "import direto da funcao"
      pattern: "listar_chaves_alertas_ativos"
    - from: "app/modules/ingestao/infrastructure/realtime_snapshot.py"
      to: "app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py"
      via: "import direto da funcao"
      pattern: "listar_chaves_alertas_ativos"
    - from: "app/modules/pedidos/processing/infrastructure/adapters.py"
      to: "app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py"
      via: "import direto da funcao"
      pattern: "listar_chaves_alertas_ativos"
    - from: "app/modules/pedidos/service.py"
      to: "app/modules/pedidos/processing/infrastructure/repository_read.py"
      via: "import das 3 classes agora em 3 modulos"
      pattern: "repository_read|repository_writer|unit_of_work"
    - from: "app/tests/test_pedidos_processing_repository.py"
      to: "app/modules/pedidos/processing/infrastructure/repository_read.py"
      via: "monkeypatch de _chunks no modulo onde a chamada resolve o nome"
      pattern: "repository_module"
---

<objective>
Fatiamento mecanico de 3 God Files de SQL do modulo pedidos, sem mudar uma linha de comportamento:
`infrastructure/repositorio_produtos.py` (1063 linhas), `infrastructure/repositorio_resumo.py`
(908 linhas) e `processing/infrastructure/repository.py` (582 linhas).

Purpose: hoje qualquer alteracao em uma projecao de leitura obriga a abrir um arquivo de ~1000
linhas com 4 projecoes independentes dentro. Depois desta task cada query publica mora no seu
proprio modulo e os helpers SQL compartilhados moram em `infrastructure/sql/`, o que reduz o custo
de contexto de toda alteracao futura de leitura de pedidos.

Output: 16 modulos novos, 3 arquivos apagados sem shim de compatibilidade, 8 sites de import
religados, e nenhum arquivo tocado acima de 310 linhas — com a suite do Docker fechando com
exatamente os mesmos numeros da baseline.

Nao-objetivo (explicito): NAO e refatoracao de logica. Nenhum SQL reescrito, nenhuma coluna
reordenada, nenhuma variavel local renomeada, nenhum type hint novo, nenhum `# noqa` mexido,
nenhuma otimizacao. Se durante a execucao aparecer a tentacao de "melhorar" algo, anote no SUMMARY
como candidato futuro e siga sem mexer.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.claude/CLAUDE.md
@app/modules/pedidos/infrastructure/repositorio_consultas.py

## Comandos canonicos deste projeto

Os testes NAO rodam com `uv run pytest` direto no host. Eles rodam dentro do container que ja
esta de pe (o `/workspace/app` do container vem da copia principal do repositorio):

- Suite completa: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- Subconjunto: o mesmo comando seguido dos caminhos de arquivo de teste e `-q`
- Lint: `docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/`

Se o container nao estiver rodando: `docker compose -f .docker/docker-compose.yml up -d`.

## Working tree compartilhado (LEIA ANTES DE QUALQUER `git`)

3 arquivos modificados e nao commitados de OUTRA sessao, na branch `develop`:
`app/modules/auth/infrastructure/http/routes.py`, `app/modules/auth/infrastructure/repositorio_otp.py`,
`app/tests/test_docs_seguranca.py`. Nao editar, nao commitar, nao reverter.

PROIBIDO nesta task: `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset --hard`,
`git checkout -- <path>`, `git clean`. Todo commit com caminhos explicitos e conferido com
`git show --stat`. Nenhum commit menciona Claude nem leva `Co-Authored-By`.

## Regra dura de fidelidade do SQL

O texto de cada literal SQL tem que ficar **byte-identico**, incluindo indentacao. Isso e
possivel porque todo movimento aqui e de funcao de nivel de modulo para funcao de nivel de
modulo: a profundidade de aninhamento nao muda, entao a indentacao interna das strings tambem
nao. Consequencias praticas:

- NAO hoistar SQL inline para constante de modulo (mudaria a indentacao do literal).
- NAO reindentar, NAO reformatar, NAO trocar `"""` por `'''`.
- Manter as mesmas expressoes interpoladas nas f-strings, com os mesmos nomes.
- Docstring de modulo dos arquivos novos: uma linha só (um literal multilinha novo poluiria o
  gate de fidelidade descrito na Task 1).

## Anatomia atual (levantada, nao re-investigar)

`SqlPedidosReadRepository` (em `repositorio_consultas.py`) e a UNICA API publica: rotas, casos de
uso e testes passam por ela. Os modulos `repositorio_produtos.py`/`repositorio_resumo.py` nao tem
classes — sao funcoes soltas chamadas por import lazy DENTRO dos metodos da classe. Nenhum teste
importa esses dois arquivos direto.

Consumidores externos confirmados por grep (sao 3, nao 2 — `write_adapter.py` foi descoberto no
planejamento e todos os 3 usam apenas `listar_chaves_alertas_ativos_sql`):

| Arquivo | Linha | Forma hoje |
|---|---|---|
| `app/modules/ingestao/infrastructure/realtime_snapshot.py` | 6-8, 41 | `from ...repositorio_resumo import listar_chaves_alertas_ativos_sql` |
| `app/modules/pedidos/infrastructure/write_adapter.py` | 32-34, 70 | `repositorio_resumo as resumo_repo` + `resumo_repo.listar_chaves_alertas_ativos_sql(...)` |
| `app/modules/pedidos/processing/infrastructure/adapters.py` | 20, 365 | `repositorio_resumo as resumo_repo` + `resumo_repo.listar_chaves_alertas_ativos_sql(...)` |

Nenhum teste faz monkeypatch de `resumo_repo` nem de qualquer simbolo de `repositorio_produtos`
(verificado). Os testes de arquitetura (`test_pedidos_architecture.py`,
`test_pedidos_processing_architecture.py`) so varrem `application/*.py` — esta reorganizacao de
`infrastructure/` nao os afeta.

## Fora de escopo (declarado, nao e lacuna esquecida)

Dois arquivos de pedidos passam de 300 linhas e NAO entram nesta task, porque nao estao nos 7
itens levantados: `processing/infrastructure/adapters.py` (400) e
`infrastructure/repositorio_estoque_virtual.py` (354). Registrar no SUMMARY como candidatos de
uma proxima quick task.
</context>

<tasks>

<task type="auto">
  <name>Task 1: sql/estoque_cte.py + sql/cursor.py e quebra de repositorio_produtos.py em produtos/</name>
  <files>app/modules/pedidos/infrastructure/sql/__init__.py, app/modules/pedidos/infrastructure/sql/estoque_cte.py, app/modules/pedidos/infrastructure/sql/cursor.py, app/modules/pedidos/infrastructure/produtos/__init__.py, app/modules/pedidos/infrastructure/produtos/listar_produtos.py, app/modules/pedidos/infrastructure/produtos/listar_clientes_produto.py, app/modules/pedidos/infrastructure/produtos/listar_ordens_reserva.py, app/modules/pedidos/infrastructure/produtos/listar_lookup.py, app/modules/pedidos/infrastructure/repositorio_consultas.py, app/modules/pedidos/infrastructure/models.py, app/modules/pedidos/infrastructure/repositorio_produtos.py, .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py</files>
  <action>
PASSO 0 — baseline. Antes de qualquer edicao: rodar a suite completa no Docker e anotar os numeros
exatos (passed/skipped/failed) no seu scratch e depois no SUMMARY. Sabe-se que existe 1 falha
conhecida e pre-existente em `test_pedidos_read_projection.py` (`Event loop is closed` no teardown,
sem relacao com este trabalho) — ela e esperada e nao deve ser investigada. Copiar tambem
`app/modules/pedidos/infrastructure/repositorio_produtos.py` para o seu diretorio de scratchpad
(fora do repositorio) como `baseline_produtos.py`; ele e a referencia do gate de fidelidade.

PASSO 1 — ferramenta de gate. Criar
`.planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py`:
script stdlib-only (ast, hashlib, pathlib, sys) que recebe caminhos de arquivo `.py` em argv e
imprime, uma por linha e em ordem, `sha1(texto)` + numero de linhas de cada literal de string
multilinha encontrado — ou seja, cada `ast.Constant` de `str` cujo valor contem `\n`, mais cada
`ast.JoinedStr` (f-string) cujo `ast.unparse` contem `\n`, usando o `ast.unparse` como texto no
caso da f-string. Sem dependencia externa, sem argparse, saida deterministica. Ele sera reusado
nas Tasks 2, 3 e 4.

PASSO 2 — `sql/__init__.py` e `produtos/__init__.py`: apenas docstring de UMA linha, no idioma e
formato dos `__init__.py` que ja existem em `infrastructure/`. PROIBIDO re-exportar as funcoes nos
`__init__.py`: os imports do `repositorio_consultas.py` sao lazy de proposito e devem apontar
direto para o modulo folha.

PASSO 3 — `sql/estoque_cte.py`: mover verbatim de `repositorio_produtos.py`, nesta ordem:
`_STATUS_SQL` (linhas 19-25), `_AVAILABLE_STOCK_CTE` (40-96), `_available_stock_cte` (99-107),
`_or_pairs_sql` (110-193) e `_pairs_cte` (196-317). `_STATUS_SQL` vem para ca porque `_pairs_cte`
o consome no mesmo modulo — e tambem e lido por duas das projecoes. Esperado: ~290 linhas; nao
subdividir, `~300` e o teto do critério de pronto e o arquivo fica abaixo dele.

PASSO 4 — `sql/cursor.py` (helpers de paginacao por cursor e de ordenacao de tamanhos): mover
verbatim `_cursor_value` (319-325), `_cursor_parameter` (327-341), `_epoch_ms` (343-349),
`_size_sort_key` (351-358) e `_size_positions` (360-377). Esperado ~75 linhas.

PASSO 5 — quatro modulos em `produtos/`, cada um com UMA funcao publica movida verbatim:
- `listar_produtos.py`: `listar_produtos_sql` (378-551) MAIS a constante `_SORT_SQL` (27-38), que
  e usada exclusivamente por ela (conferido) e por isso fica privada aqui, nao em `sql/`.
- `listar_clientes_produto.py`: `listar_clientes_produto_sql` (552-743).
- `listar_ordens_reserva.py`: `listar_ordens_reserva_sql` (744-902).
- `listar_lookup.py`: `listar_lookup_sql` (903-1063). Este NAO importa nada de `sql/` — conferido
  que o trecho nao usa nenhum helper compartilhado; se aparecer um import de `sql/` aqui, e sinal
  de que o corte saiu errado.
Distribuir os imports de topo por uso real (nao copiar o bloco de imports inteiro em todos):
`text` do sqlalchemy onde ha SQL; `datetime`/`UTC`/`timedelta`/`Decimal`/`date` so onde o trecho
usa; os tipos de `domain.consultas` que a assinatura exige; `calcular_versao_ordem` so em
`listar_clientes_produto.py`. O gate de `ruff check` (F401 import morto, F821 nome nao definido) e
quem prova que a distribuicao ficou certa — nao chutar.

PASSO 6 — religar os 4 imports lazy de produtos em `repositorio_consultas.py` (metodos
`listar_produtos`, `listar_clientes_produto`, `listar_lookup`, `listar_ordens_reserva`, hoje nas
linhas 141-169) para os modulos novos. As assinaturas publicas e os corpos dos metodos ficam
identicos — muda so o caminho do import lazy.

PASSO 7 — atualizar o comentario de `infrastructure/models.py:64`, que cita
`repositorio_produtos.py (listar_produtos)`, para o caminho novo. Só o comentario.

PASSO 8 — apagar `app/modules/pedidos/infrastructure/repositorio_produtos.py`. Sem shim, sem
re-export, sem arquivo de compatibilidade.

Commits: 1) `refactor(pedidos): extrai helpers SQL compartilhados de leitura para infrastructure/sql`
2) `refactor(pedidos): um modulo por query em infrastructure/produtos`. Caminhos explicitos,
`git show --stat` em cada um.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/ && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_read_projection.py app/tests/test_pedidos_routes.py app/tests/test_pedidos_architecture.py -q</automated>
    <automated>./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py "$SCRATCH/baseline_produtos.py" > "$SCRATCH/lit_old.txt"; ./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py app/modules/pedidos/infrastructure/sql/estoque_cte.py app/modules/pedidos/infrastructure/sql/cursor.py app/modules/pedidos/infrastructure/produtos/*.py | sort > "$SCRATCH/lit_new.txt"; comm -23 <(sort "$SCRATCH/lit_old.txt") "$SCRATCH/lit_new.txt"</automated>
    <automated>grep -rn "repositorio_produtos" --include="*.py" app/ | grep -v "^Binary"; test ! -f app/modules/pedidos/infrastructure/repositorio_produtos.py</automated>
    <automated>wc -l app/modules/pedidos/infrastructure/sql/*.py app/modules/pedidos/infrastructure/produtos/*.py | sort -rn | head -3</automated>
  </verify>
  <done>
`repositorio_produtos.py` nao existe mais e nenhum `.py` de `app/` o menciona. O `comm -23` do gate
de literais sai VAZIO (todo literal SQL multilinha do arquivo antigo existe byte-identico em algum
arquivo novo). `ruff check app/` limpo. Os 3 arquivos de teste rodados fecham com o mesmo resultado
do PASSO 0 (a falha conhecida de `test_pedidos_read_projection.py` pode continuar; nenhuma falha
nova). Nenhum arquivo novo passa de 310 linhas.
  </done>
</task>

<task type="auto">
  <name>Task 2: sql/credito.py + sql/alertas_cte.py, resumo/listar_alertas.py, resumo/listar_chaves_alertas_ativos.py e os 3 consumidores externos</name>
  <files>app/modules/pedidos/infrastructure/sql/credito.py, app/modules/pedidos/infrastructure/sql/alertas_cte.py, app/modules/pedidos/infrastructure/resumo/__init__.py, app/modules/pedidos/infrastructure/resumo/listar_alertas.py, app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py, app/modules/pedidos/infrastructure/repositorio_consultas.py, app/modules/pedidos/infrastructure/repositorio_resumo.py, app/modules/pedidos/infrastructure/write_adapter.py, app/modules/ingestao/infrastructure/realtime_snapshot.py, app/modules/pedidos/processing/infrastructure/adapters.py</files>
  <action>
PASSO 0 — copiar `app/modules/pedidos/infrastructure/repositorio_resumo.py` para o scratchpad como
`baseline_resumo.py`. Ele e a referencia do gate de fidelidade das Tasks 2 e 3 (a Task 3 termina de
esvaziar o arquivo, entao o baseline tem que ser tirado agora, antes de qualquer edicao dele).

PASSO 1 — `sql/credito.py`: MOVER `_credito_bloqueado_sql` (com a docstring) de
`repositorio_consultas.py:25-39` para ca, e removê-la de `repositorio_consultas.py`. Justificativa
da decisao (nao estava no levantamento original): a funcao esta definida em `repositorio_consultas.py`
mas nao e usada por ele — quem usa e so o resumo. Deixá-la lá obrigaria um modulo folha de `sql/` a
importar de volta o repositorio de cima, invertendo a direcao das dependencias. O texto SQL
produzido e identico.

PASSO 2 — `sql/alertas_cte.py`: mover verbatim de `repositorio_resumo.py` o `_ALERTS_CTE` (21-147),
`_limitar_estoque_alertas_ao_escopo` (148-166), `_alerts_cte_produto` (168-210) e
`_alerts_cte_pedidos` (212-230), importando `_credito_bloqueado_sql` de `sql/credito.py` (o
`_ALERTS_CTE` o interpola na linha 105). NAO trazer `_percent` para ca: apesar de estar no meio
desse trecho no arquivo antigo, ele e usado exclusivamente por `obter_resumo_sql` (conferido:
linhas 557-589, todas dentro dela) e por isso vai ser helper privado de `resumo/obter_resumo.py`
na Task 3 — `sql/` e para o que e de fato compartilhado. Esperado ~215 linhas.

PASSO 3 — `resumo/__init__.py`: docstring de uma linha, sem re-exports (mesma regra da Task 1).

PASSO 4 — `resumo/listar_alertas.py`: mover verbatim `listar_alertas_sql` (611-860), com a docstring
das 9 categorias e o helper local de execucao rapida que vive dentro dela. Este modulo e
auto-contido: nao usa nenhum helper de `sql/` (conferido). Imports: `text`, `ConsultaAlertas`,
`PaginaCursor`. Esperado ~260 linhas.

PASSO 5 — `resumo/listar_chaves_alertas_ativos.py`: mover verbatim
`listar_chaves_alertas_ativos_sql` (861-908), importando `_ALERTS_CTE`, `_alerts_cte_produto` e
`_alerts_cte_pedidos` de `sql/alertas_cte.py`. Assinatura intocada (`db` posicional +
keyword-only `cd_prod_cor`/`channel`/`order_ids`). Esperado ~60 linhas.

PASSO 6 — religar o import lazy do metodo `listar_alertas` de `repositorio_consultas.py`
(linhas 134-139) para `resumo/listar_alertas.py`. O import lazy de `obter_resumo` fica apontando
para `repositorio_resumo.py` ainda nesta task — quem o move e a Task 3.

PASSO 7 — os 3 consumidores externos passam a importar a funcao direto do modulo folha
`app.modules.pedidos.infrastructure.resumo.listar_chaves_alertas_ativos` (mesmo estilo que
`realtime_snapshot.py` ja usa hoje), o que elimina o alias `resumo_repo` de `write_adapter.py`
(linhas 32-34) e de `processing/infrastructure/adapters.py` (linha 20) e torna as duas chamadas
(`write_adapter.py:70`, `adapters.py:365`) nao-qualificadas. Conferido que nenhum teste faz
monkeypatch desse alias, entao a troca e segura. Cuidado no `write_adapter.py`: ele importa varios
outros `repositorio_*` no mesmo bloco — remover so a entrada do resumo.

PASSO 8 — `repositorio_resumo.py` fica temporariamente com imports + `_percent` + `obter_resumo_sql`
(~395 linhas). Isso e estado intermediario esperado, resolvido na Task 3; ele ainda precisa
importar `_credito_bloqueado_sql`, agora de `sql/credito.py`.

Commit: `refactor(pedidos): extrai CTE de alertas e credito para infrastructure/sql e move as
projecoes de alerta para infrastructure/resumo`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/ && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_read_projection.py app/tests/test_pedidos_routes.py app/tests/test_ingestao_realtime.py app/tests/test_realtime_active_ledger.py app/tests/test_auth_flows.py app/tests/test_auth_rate_limit.py -q</automated>
    <automated>./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py app/modules/pedidos/infrastructure/sql/alertas_cte.py app/modules/pedidos/infrastructure/sql/credito.py app/modules/pedidos/infrastructure/resumo/listar_alertas.py app/modules/pedidos/infrastructure/resumo/listar_chaves_alertas_ativos.py app/modules/pedidos/infrastructure/repositorio_resumo.py app/modules/pedidos/infrastructure/repositorio_consultas.py | sort > "$SCRATCH/lit_new2.txt"; ./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py "$SCRATCH/baseline_resumo.py" | sort > "$SCRATCH/lit_old2.txt"; comm -23 "$SCRATCH/lit_old2.txt" "$SCRATCH/lit_new2.txt"</automated>
    <automated>grep -rn "resumo_repo" --include="*.py" app/; wc -l app/modules/pedidos/infrastructure/sql/*.py app/modules/pedidos/infrastructure/resumo/*.py | sort -rn | head -3</automated>
  </verify>
  <done>
`ruff check app/` limpo e os 6 arquivos de teste com o mesmo resultado da baseline. O `comm -23`
sai vazio (todos os literais SQL do `repositorio_resumo.py` original seguem presentes, agora
distribuidos entre os arquivos novos e o resto que ainda esta no arquivo antigo). `grep resumo_repo`
nao retorna nada. `_percent` NAO esta em `sql/alertas_cte.py`. Nenhum arquivo novo passa de 310
linhas.
  </done>
</task>

<task type="auto">
  <name>Task 3: quebra de obter_resumo_sql em resumo/obter_resumo.py + stats_erp.py + stats_canais.py e remocao de repositorio_resumo.py</name>
  <files>app/modules/pedidos/infrastructure/resumo/obter_resumo.py, app/modules/pedidos/infrastructure/resumo/stats_erp.py, app/modules/pedidos/infrastructure/resumo/stats_canais.py, app/modules/pedidos/infrastructure/repositorio_consultas.py, app/modules/pedidos/infrastructure/repositorio_resumo.py</files>
  <action>
Aqui a instrucao e explicita: NAO basta mudar a funcao de arquivo mantendo o monolito. Os dois
blocos SQL gigantes saem para modulos proprios, cada um com o seu helper. Numeracao de linhas e do
`repositorio_resumo.py` ORIGINAL (o baseline copiado na Task 2).

PASSO 1 — `resumo/stats_erp.py`: `async def _erp_orders_stats(repo)` contendo verbatim o bloco das
linhas 249-292 (o `erp_rows = (...)` com o CTE `erp_orders`, mais o preenchimento de `erp_count` /
`erp_billing`, incluindo as duas linhas que somam a chave `"Todos"`), retornando a tupla
`(erp_count, erp_billing)`. O `text("""...""")` fica INLINE dentro da funcao, na mesma profundidade
de aninhamento de antes (`erp_rows = (` no corpo de uma funcao de modulo) — e isso que preserva a
indentacao do literal byte a byte. Esperado ~55 linhas.

PASSO 2 — `resumo/stats_canais.py`: `async def _channel_stats(repo)` contendo verbatim o bloco das
linhas 294-538, ou seja o `stats_rows = (...)` com o CTE gigante (`pending_items`,
`pending_orders`, `pending_counts`, `stock`, `reservas`, ... `demand_counts`, `editing_all`) MAIS a
inicializacao e o laco de parsing das linhas 516-538, retornando a tupla
`(base_stats, blocked_pieces, total_demand, editing_all)`. Esse SQL e uma f-string com UMA unica
interpolacao — `_credito_bloqueado_sql("status_credito")` na linha 325 — importada de
`sql/credito.py`. Mesma regra de indentacao do PASSO 1. Esperado ~250 linhas.

PASSO 3 — `resumo/obter_resumo.py`: `obter_resumo_sql(repo, *, include_stock=False)` com a mesma
assinatura de antes, agora curta (~130 linhas): o bloco de estoque opt-in (235-247, que usa
`defaultdict` e `repo._estoque_disponivel`), as duas chamadas aos helpers das Tasks acima, a funcao
interna `build` + o `total_values` (540-597) e o `return` (599-608), verbatim. O helper `_percent`
(15-19) vem para ca como privado do modulo (decidido na Task 2). A ordem de execucao das duas
queries tem que ser a mesma de antes: erp primeiro, stats por canal depois.

PASSO 4 — religar o import lazy do metodo `obter_resumo` de `repositorio_consultas.py`
(linhas 127-132) para `resumo/obter_resumo.py`.

PASSO 5 — apagar `app/modules/pedidos/infrastructure/repositorio_resumo.py`. Sem shim.

Commit: `refactor(pedidos): quebra obter_resumo_sql em orquestrador + stats de erp e de canal`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/ && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_read_projection.py app/tests/test_pedidos_routes.py app/tests/test_ingestao_realtime.py -q</automated>
    <automated>./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py app/modules/pedidos/infrastructure/sql/*.py app/modules/pedidos/infrastructure/resumo/*.py app/modules/pedidos/infrastructure/repositorio_consultas.py | sort > "$SCRATCH/lit_new3.txt"; comm -23 "$SCRATCH/lit_old2.txt" "$SCRATCH/lit_new3.txt"</automated>
    <automated>grep -rn "repositorio_resumo" --include="*.py" app/; test ! -f app/modules/pedidos/infrastructure/repositorio_resumo.py; wc -l app/modules/pedidos/infrastructure/resumo/*.py | sort -rn | head -3</automated>
  </verify>
  <done>
`repositorio_resumo.py` nao existe e nenhum `.py` de `app/` o menciona. O `comm -23` contra o
baseline do arquivo original sai VAZIO — prova de que os dois blocos SQL gigantes atravessaram a
quebra byte-identicos. `obter_resumo.py` tem no maximo ~150 linhas e nenhum bloco SQL grande
embutido. `ruff check app/` limpo, testes iguais a baseline.
  </done>
</task>

<task type="auto">
  <name>Task 4: separacao de processing/infrastructure/repository.py por classe</name>
  <files>app/modules/pedidos/processing/infrastructure/chunking.py, app/modules/pedidos/processing/infrastructure/repository_read.py, app/modules/pedidos/processing/infrastructure/repository_writer.py, app/modules/pedidos/processing/infrastructure/unit_of_work.py, app/modules/pedidos/processing/infrastructure/repository.py, app/modules/pedidos/service.py, app/tests/test_pedidos_processing_repository.py</files>
  <action>
PASSO 0 — copiar `app/modules/pedidos/processing/infrastructure/repository.py` para o scratchpad
como `baseline_repository.py`.

PASSO 1 — `chunking.py`: mover `_chunks` (linhas 46-48) para ca. Ele e o unico simbolo de topo
usado pelas DUAS classes (linhas 138 e 222 na de leitura; 483 e 560 na de escrita), entao nao pode
ficar em nenhuma das duas. Manter o nome com underscore e a assinatura generica atual
(`_chunks[T](values: Sequence[T], size: int)`) — nao unificar com os `_chunks` homonimos de
`repositorio_ordens.py`/`repositorio_ordens_linx.py`, que tem assinatura diferente e estao fora de
escopo.

PASSO 2 — `repository_read.py`: `_to_spec` (51-65), `_to_pair` (68-75) — ambos usados so pela
classe de leitura — e `class SqlAlchemyProcessingRepository` (78-334) verbatim, importando `_chunks`
de `chunking.py`.

PASSO 3 — `repository_writer.py`: `class SqlAlchemyProcessingWriter` (337-564) verbatim,
importando `_chunks` de `chunking.py`.

PASSO 4 — `unit_of_work.py`: `class SqlAlchemyProcessingUnitOfWork` (567-575) verbatim.

Nos 3 modulos de classe, manter o idioma que o arquivo antigo ja usava: `__all__` explicito, com a
classe daquele modulo. Distribuir os imports de topo (json, defaultdict, Iterable/Mapping/Sequence,
datetime, Any, UUID, os simbolos de sqlalchemy, `SEM_CREDITO`, `estoque_repo`, `ordens_repo`,
`linx_repo`, os models e os simbolos de `processing.domain`) por uso real de cada arquivo; `ruff
check` (F401/F821) e o juiz.

PASSO 5 — apagar `repository.py`. Sem shim: os dois unicos importadores sao ajustados aqui.

PASSO 6 — `app/modules/pedidos/service.py`: o import unico de `...infrastructure.repository`
(linhas 76-79) vira 3 imports, um por modulo novo. Os 8 sites de instanciacao (linhas 111, 112,
165, 186, 208, 216, 218, 222) nao mudam, porque os nomes das classes nao mudam.

PASSO 7 — `app/tests/test_pedidos_processing_repository.py`: (a) o bloco `from
...processing.infrastructure import repository as repository_module` (linhas 49-51) passa a
importar `repository_read as repository_module` — manter o ALIAS `repository_module` para nao mexer
nas 4 linhas que fazem `repository_module._chunks` (334, 341, 523, 530); os dois testes que
monkeypatcham `_chunks` exercitam metodos da classe de LEITURA, e o patch tem que ser no modulo
onde a chamada resolve o nome, que agora e `repository_read`; (b) o bloco de import das 3 classes
(linhas 58-61) vira 3 imports, um por modulo novo. Nenhuma assercao de teste muda.

Commit: `refactor(pedidos): separa os adapters de processamento em repository_read, repository_writer e unit_of_work`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/ && docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_processing_repository.py app/tests/test_pedidos_processing_service.py app/tests/test_pedidos_processing_routes.py app/tests/test_pedidos_processing_worker.py app/tests/test_pedidos_processing_domain.py app/tests/test_pedidos_processing_lease.py app/tests/test_pedidos_processing_cancellation_024.py app/tests/test_pedidos_processing_architecture.py -q</automated>
    <automated>./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py "$SCRATCH/baseline_repository.py" | sort > "$SCRATCH/lit_old4.txt"; ./.venv/Scripts/python.exe .planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/verificar_sql_literals.py app/modules/pedidos/processing/infrastructure/chunking.py app/modules/pedidos/processing/infrastructure/repository_read.py app/modules/pedidos/processing/infrastructure/repository_writer.py app/modules/pedidos/processing/infrastructure/unit_of_work.py | sort > "$SCRATCH/lit_new4.txt"; comm -23 "$SCRATCH/lit_old4.txt" "$SCRATCH/lit_new4.txt"</automated>
    <automated>grep -rn "infrastructure.repository\b\|infrastructure import repository\b" --include="*.py" app/; test ! -f app/modules/pedidos/processing/infrastructure/repository.py; wc -l app/modules/pedidos/processing/infrastructure/*.py | sort -rn | head -4</automated>
  </verify>
  <done>
`repository.py` nao existe; nenhum `.py` de `app/` importa `processing.infrastructure.repository`.
Os 8 arquivos de teste de processing passam com o mesmo resultado da baseline — em especial os dois
testes que contam chunks via `monkeypatch` continuam efetivos (nao viraram no-op silencioso: se o
alias tivesse ficado no modulo errado, o `_chunks` observado nunca seria chamado e a assercao de
contagem falharia). `comm -23` vazio. Nenhum dos 3 modulos novos passa de 310 linhas.
  </done>
</task>

<task type="auto">
  <name>Task 5: gate final da task inteira</name>
  <files>.planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/260826-kla-SUMMARY.md, .planning/STATE.md</files>
  <action>
Fechamento. Nenhuma edicao de codigo de producao aqui — se algum gate falhar, corrigir e voltar a
rodar, mas sem aproveitar a passagem para mexer em nada fora do escopo.

1. Suite COMPLETA no Docker, sem `-k` e sem `--deselect`. Comparar passed/skipped/failed com a
   baseline do PASSO 0 da Task 1: tem que ser identico, numero por numero. Qualquer teste novo a
   mais ou a menos e sinal de erro (esta task nao cria nem remove teste).
2. `ruff check app/` limpo.
3. Gate de tamanho: listar as linhas de todos os `.py` de
   `app/modules/pedidos/infrastructure/` (incluindo `sql/`, `produtos/`, `resumo/`) e de
   `app/modules/pedidos/processing/infrastructure/`, ordenado decrescente. Todo arquivo CRIADO ou
   TOCADO por esta task tem que estar em <= 310 linhas. Os dois arquivos declarados fora de escopo
   (`processing/infrastructure/adapters.py`, `infrastructure/repositorio_estoque_virtual.py`)
   continuam acima e devem ser citados como tal no SUMMARY, nao "consertados" aqui.
4. Gate de import quebrado no repositorio inteiro: garantir que nenhum `.py` de `app/` menciona
   `repositorio_produtos`, `repositorio_resumo` ou `processing.infrastructure.repository`, e que
   nenhum dos 3 arquivos existe em disco. Confirmar tambem que o app importa de pe (o boot do
   FastAPI dentro do container ja e exercitado pelos testes de rota, entao isso ja esta coberto —
   nao inventar comando novo se a suite completa passou).
5. Gate de API publica: `SqlPedidosReadRepository` continua com os 6 metodos publicos e as mesmas
   assinaturas (`obter_resumo`, `listar_alertas`, `listar_produtos`, `listar_clientes_produto`,
   `listar_lookup`, `listar_ordens_reserva`), e `repositorio_consultas.py` nao ganhou nem perdeu
   metodo. Conferir por leitura do arquivo (ele ficou com ~130 linhas depois da saida de
   `_credito_bloqueado_sql`).
6. Auditoria de working tree: `git status --short` tem que mostrar EXATAMENTE os 3 arquivos de
   outra sessao (`auth/infrastructure/http/routes.py`, `auth/infrastructure/repositorio_otp.py`,
   `test_docs_seguranca.py`) ainda modificados e nao commitados. `git log --oneline` dos commits
   desta task + `git show --stat` de cada um, confirmando que nenhum deles carrega esses 3
   arquivos e que nenhuma mensagem menciona Claude.
7. SUMMARY + atualizacao do `.planning/STATE.md` (tabela "Quick Tasks Completed" e
   `last_activity`) usando a ferramenta Edit — NUNCA `gsd-tools query state.advance-plan`, que
   corrompe a frontmatter deste STATE.md.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q 2>&1 | tail -5</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec -T api uv run ruff check app/</automated>
    <automated>wc -l app/modules/pedidos/infrastructure/*.py app/modules/pedidos/infrastructure/sql/*.py app/modules/pedidos/infrastructure/produtos/*.py app/modules/pedidos/infrastructure/resumo/*.py app/modules/pedidos/processing/infrastructure/*.py | sort -rn | head -12</automated>
    <automated>grep -rln "repositorio_produtos\|repositorio_resumo\|processing.infrastructure.repository\b" --include="*.py" app/; git status --short</automated>
  </verify>
  <done>
Suite completa com os mesmos numeros da baseline (nenhuma falha nova; a falha conhecida de
`test_pedidos_read_projection.py` pode permanecer). `ruff check app/` limpo. Nenhum arquivo criado
ou tocado acima de 310 linhas. Zero mencoes aos 3 modulos apagados em `app/`. `git status --short`
com exatamente os 3 arquivos de outra sessao. SUMMARY escrito e STATE.md atualizado via Edit.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| (inalterada) cliente HTTP -> rotas de pedidos | Esta task nao cria, remove nem altera rota, schema, validacao ou parametro de query |
| (inalterada) app -> Postgres | O texto de toda query e as vinculacoes de parametro sao movidos verbatim; nenhuma string SQL passa a receber valor novo |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-KLA-01 | Tampering | literais SQL movidos entre arquivos | mitigate | Gate automatizado por task: `verificar_sql_literals.py` compara o hash de cada literal multilinha do arquivo antigo com os dos arquivos novos; `comm -23` vazio e condicao de pronto. Regra dura de nao reindentar/nao hoistar SQL |
| T-KLA-02 | Elevation of Privilege | filtro de escopo por produto/canal/pedido (`_pairs_cte`, `_alerts_cte_*`) | mitigate | Os helpers de escopo vao inteiros e verbatim para `sql/`; cada projecao importa so o que usa, e o gate T-KLA-01 detecta qualquer alteracao no texto do WHERE de escopo |
| T-KLA-03 | Tampering | teste de contagem de chunks virar no-op silencioso | mitigate | Task 4 exige que o alias `repository_module` aponte para o modulo onde a chamada resolve `_chunks` (`repository_read`); o proprio teste falha se o patch nao pegar, e isso esta declarado no `<done>` |
| T-KLA-04 | Denial of Service | ordem/quantidade de round-trips ao banco em `obter_resumo` | accept | A quebra em `_erp_orders_stats` + `_channel_stats` preserva as mesmas 2 (ou 3, com `include_stock`) consultas na mesma ordem; nenhuma query e adicionada. Coberto pelos testes de projecao |
| T-KLA-SC | Tampering | instalacao de pacote | accept | Nenhuma dependencia nova (npm/pip/cargo). `pyproject.toml` intocado, nada a auditar |
</threat_model>

<verification>
1. Suite completa no Docker identica a baseline capturada antes da primeira edicao.
2. `ruff check app/` limpo (prova a distribuicao correta dos imports em 16 modulos novos).
3. Gate de fidelidade de literal SQL vazio nas Tasks 1, 2, 3 e 4.
4. Nenhum `.py` de `app/` mencionando `repositorio_produtos`, `repositorio_resumo` ou
   `processing.infrastructure.repository`; os 3 arquivos ausentes do disco.
5. Nenhum arquivo criado ou tocado acima de 310 linhas.
6. `SqlPedidosReadRepository` com os mesmos 6 metodos publicos e assinaturas.
7. Os 3 arquivos nao commitados de outra sessao intactos e fora dos commits.
</verification>

<success_criteria>
- 16 modulos novos: 5 em `infrastructure/sql/`, 4 em `infrastructure/produtos/`, 5 em
  `infrastructure/resumo/` (+ os 3 `__init__.py`), 4 em `processing/infrastructure/`.
- 3 God Files apagados sem shim de compatibilidade.
- 8 sites de import religados: 6 lazy em `repositorio_consultas.py`, 3 consumidores externos de
  `listar_chaves_alertas_ativos_sql`, 1 em `service.py`, 2 blocos em
  `test_pedidos_processing_repository.py` (contagem por arquivo, nao por linha).
- Zero mudanca de comportamento: mesmo SQL byte a byte, mesma API publica, mesmos numeros de suite.
- `obter_resumo_sql` deixou de ser monolito: os dois blocos SQL grandes vivem em `stats_erp.py` e
  `stats_canais.py`.
</success_criteria>

<output>
Create `.planning/quick/260826-kla-fatiar-god-files-de-leitura-sql-do-modul/260826-kla-SUMMARY.md` when done.

Registrar no SUMMARY, obrigatoriamente:
- os numeros da baseline (PASSO 0 da Task 1) e os numeros finais, lado a lado;
- a tabela de linhas por arquivo depois do fatiamento;
- as 2 decisoes de desenho tomadas fora do levantamento original: `_credito_bloqueado_sql` movido
  para `sql/credito.py` (era de `repositorio_consultas.py`, que nao o usava) e `_percent` mantido
  privado em `resumo/obter_resumo.py` em vez de `sql/alertas_cte.py` (nao e compartilhado);
- o consumidor externo a mais descoberto no planejamento (`write_adapter.py`, totalizando 3);
- os 2 arquivos declarados fora de escopo e ainda acima de 300 linhas
  (`processing/infrastructure/adapters.py`, `infrastructure/repositorio_estoque_virtual.py`) como
  candidatos de proxima quick task.
</output>
