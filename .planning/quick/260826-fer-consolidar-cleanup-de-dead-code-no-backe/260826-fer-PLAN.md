---
phase: quick/260826-fer
plan: 01
type: execute
wave: 1
depends_on: []
autonomous: true
requirements: [QUICK-260826-FER]
files_modified:
  - app/modules/pedidos/infrastructure/repositorio_consultas.py
  - app/modules/pedidos/infrastructure/repositorio_ordens.py
  - pyproject.toml
  - alembic/script.py.mako
  - alembic/env.py
  - alembic/versions/001_initial_schema.py
  - alembic/versions/002_auth_users.py
  - alembic/versions/003_pedidos_processados.py
  - alembic/versions/004_parametros.py
  - alembic/versions/005_ingestao_databricks.py
  - alembic/versions/006_ordens_reserva_modificacoes.py
  - alembic/versions/007_estoque_canal.py
  - alembic/versions/008_ordens_reserva_aprovado_em.py
  - alembic/versions/009_pedidos_processados_erp.py
  - alembic/versions/010_proc_erp_valores.py
  - alembic/versions/011_proc_erp_data.py
  - alembic/versions/012_comunicacoes.py
  - alembic/versions/013_faturamento_colecao.py
  - alembic/versions/014_or_por_produto.py
  - alembic/versions/015_schema_linx_referencia_posicao.py
  - alembic/versions/016_estoque_dt_estoque.py
  - alembic/versions/017_estoque_virtual.py
  - alembic/versions/031_auth_provider_password_hash_nullable.py
  - app/modules/pedidos/domain/motor_adequacao.py
  - app/shared/config/settings.py
  - app/modules/parametros/domain/registro.py
  - app/modules/parametros/domain/leitura_resiliente.py
  - app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
  - app/modules/realtime/infrastructure/connection_manager.py
  - app/modules/realtime/infrastructure/http/routes.py
  - app/workers/tasks/ingestao.py
  - app/workers/tasks/pedidos.py
  - app/tests/test_pedidos_motor.py
  - app/tests/test_pedidos_processing_repository.py
  - app/tests/test_pedidos_processing_service.py
  - app/tests/test_auth_flows.py
  - app/tests/test_ingestao_sync.py
  - app/tests/test_pedidos_product_writes.py
  - .github/workflows/pipeline.yml

must_haves:
  truths:
    - "uv run ruff check app alembic (sem --select na CLI) passa lendo so a config do pyproject.toml"
    - "uv run ruff format --check app alembic continua passando"
    - "uv run pyright app alembic continua em 0 errors"
    - "A suite no Docker continua em 889 passed / 18 skipped / 0 failed"
    - "Os 3 arquivos da outra sessao seguem modificados e nao commitados por este trabalho"
    - "alembic/env.py mantem os 8 marcadores noqa: F401 e alembic check nao acusa drift"
    - "Uma migration nova gerada pelo template nasce compativel com a regua nova"
    - "_like_pattern e carregar_modificacoes nao existem mais no codigo"
  artifacts:
    - path: "pyproject.toml"
      provides: "Fonte unica de verdade da config do ruff"
      contains: "[tool.ruff.lint]"
    - path: "alembic/script.py.mako"
      provides: "Template de migration ja em PEP 604 / collections.abc"
    - path: ".github/workflows/pipeline.yml"
      provides: "Gate de CI lendo a config do pyproject"
  key_links:
    - from: "pyproject.toml"
      to: ".github/workflows/pipeline.yml"
      via: "ruff check sem --select"
      pattern: "ruff check app alembic"
    - from: "pyproject.toml"
      to: "app/modules/auth/infrastructure/http/routes.py"
      via: "extend-immutable-calls silencia B008 sem editar o arquivo alheio"
      pattern: "extend-immutable-calls"
---

<objective>
Remover o dead code real do backend e formalizar a config do ruff no `pyproject.toml`,
subindo a regua de lint (B, UP, I, SIM, C4 alem de E4/E7/E9/F) sem quebrar o pipeline.

Purpose: hoje o `--select` mora so na flag do CI, entao `ruff check` local e CI divergem.
Formalizar no pyproject faz os dois lerem a mesma fonte, e a regua nova pega classes
inteiras de problema (excecoes sem `from`, imports deprecados, imports fora de ordem)
que o gate atual nao ve.

Output: 2 simbolos mortos removidos, `[tool.ruff]`/`[tool.ruff.lint]` no pyproject,
`script.py.mako` corrigido na raiz, 110 achados zerados, pipeline apontando para a
config nova.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@.claude/CLAUDE.md
@pyproject.toml
@.github/workflows/pipeline.yml
</context>

<constraints>

## Arquivos intocaveis (outra sessao)

Estes 3 arquivos tem mudancas NAO COMMITADAS de outra sessao no mesmo working tree:

- `app/modules/auth/infrastructure/http/routes.py`
- `app/modules/auth/infrastructure/repositorio_otp.py`
- `app/tests/test_docs_seguranca.py`

**Nao editar, nao commitar, nao reverter.** Proibido: `git add -A`, `git add .`,
`git commit -a`, `git stash`, `git reset --hard`, `git checkout -- <path>`.
Todo `git add` deve listar caminhos explicitos.

**Ja verificado no planejamento:** os 11 achados de `routes.py` sao todos B008
(`Depends` em default de argumento) e sao **inteiramente silenciados pela config**
`extend-immutable-calls` da Task 2 — nenhuma edicao nesses arquivos e necessaria.
Os outros 2 arquivos tem 0 achados. Se em algum momento o ruff quiser corrigir um
desses 3 arquivos, **pare e reporte** — significa que a config saiu errada.

## alembic/env.py

Nunca remover os imports `# noqa: F401` — sem eles o autogenerate propoe `DROP TABLE`.
Hoje sao **8 marcadores `noqa: F401`** em **11 linhas** de import de `app.`. O autofix de
I001 apenas reagrupa um import em bloco parentizado e **preserva** o `# noqa` (ja validado
no planejamento). Ainda assim, conferir contagem e `alembic check` na Task 2.

## estoque_virtual nao e dead code

`pedidos/domain/estoque_virtual.py`, `pedidos/models.py::EstoqueVirtual` e
`pedidos/infrastructure/repositorio_estoque_virtual.py` sao placebo documentado com
data de validade no CLAUDE.md. Nao tocar.

## Como rodar as ferramentas

- Lint/format/tipos rodam no **host**, via `uv run` (a imagem Docker nao tem o grupo dev):
  `uv run ruff ...`, `uv run pyright app alembic`
- Nao use `.venv/Scripts/pyright.exe` direto — sem o `uv run` ele nao resolve as
  dependencias e reporta ~354 falsos `reportMissingImports`.
- Testes rodam no **container ja de pe**, sem worktree:
  `docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q`

## Baseline medido no planejamento (2026-08-26)

| Gate | Estado antes |
|---|---|
| `ruff check --select E4,E7,E9,F app alembic` | limpo (0 achados) |
| `ruff format --check app alembic` | 288 files already formatted |
| `uv run pyright app alembic` | 0 errors, 0 warnings |
| Suite no Docker | **889 passed, 18 skipped, 0 failed** (~143s) |
| Regua nova (E4,E7,E9,F,B,UP,I,SIM,C4 + py313 + bugbear config) | **110 achados** — 90 auto-fixaveis, 20 manuais |

Como `F` ja esta limpo, **nao existe import morto nem variavel morta**. O dead code real
e so de simbolo nao referenciado, e o planejamento ja o identificou exaustivamente
(varredura AST de todos os defs/classes de modulo em `app/` + `alembic/`, descontando
decorados, testes e migrations): sao exatamente **2 simbolos**, tratados na Task 1.

</constraints>

<tasks>

<task type="auto">
  <name>Task 1: Remover os 2 simbolos mortos e o Protocol orfao que eles deixam</name>
  <files>app/modules/pedidos/infrastructure/repositorio_consultas.py, app/modules/pedidos/infrastructure/repositorio_ordens.py</files>
  <action>
Remover o dead code confirmado por varredura AST + `grep` no repositorio inteiro
(zero referencias fora da propria definicao, em qualquer extensao de arquivo).

**1. `app/modules/pedidos/infrastructure/repositorio_consultas.py`**

Remover a funcao `_like_pattern` (definida em ~linha 42; helper privado que escapa
`\`, `%` e `_`, e envelopa o valor em `%...%`). Nenhum chamador existe. Nao confundir com
a funcao imediatamente acima dela, que monta o predicado `LIKE '%SEM%CRED%'` e **e usada**
— remover apenas `_like_pattern`, preservando a separacao de duas linhas em branco entre
o que sobra e a `class SqlPedidosReadRepository`.

**2. `app/modules/pedidos/infrastructure/repositorio_ordens.py`**

Remover, nesta ordem:

- `async def carregar_modificacoes(db) -> dict[Par, dict]` (~linha 92 ate o fim do
  `return {...}`), que le `PedidoModificacao`. Sem chamadores.
- `class _PedidoModificacaoResult(Protocol)` (~linhas 25-29). Fica orfao assim que
  `carregar_modificacoes` sai — o `cast(...)` interno dessa funcao era sua unica
  referencia.
- Atualizar o docstring de modulo (linhas 1-2). Hoje enumera
  `PedidoProcessado, PedidoModificacao e OrdemReserva`; depois da remocao o arquivo nao
  referencia mais `PedidoModificacao` em lugar nenhum, entao tirar essa tabela da
  enumeracao e manter `PedidoProcessado` e `OrdemReserva`.

**Nao mexer nos imports deste arquivo.** Ja conferido: `Protocol` continua usado por
`_OrdemReservaResult`, `Any` por `_OrdemReservaResult.itens`, `cast`/`Sequence` por
`carregar_ordens_reserva`, e o alias `Par` (linha 17) por `salvar_processados`. Se mesmo
assim o ruff acusar F401 depois, e sinal de que algo a mais foi removido por engano —
revise antes de apagar qualquer import.
  </action>
  <verify>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && ! grep -rn "_like_pattern\|carregar_modificacoes\|_PedidoModificacaoResult" app alembic --include="*.py" && uv run ruff check --select E4,E7,E9,F app alembic && uv run ruff format --check app alembic && uv run pyright app alembic 2>&1 | tail -2</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q 2>&1 | tail -3</automated>
  </verify>
  <done>
Os 3 simbolos nao existem mais; ruff F limpo; `ruff format --check` passa; pyright em
0 errors; suite em 889 passed / 18 skipped / 0 failed.

Commit com caminhos explicitos (jamais `-A`, `.` ou `-a`):

    git add app/modules/pedidos/infrastructure/repositorio_consultas.py app/modules/pedidos/infrastructure/repositorio_ordens.py
    git commit -m "refactor(pedidos): remover dead code em repositorio_consultas e repositorio_ordens"

Rodar `git status --short` depois e confirmar que os 3 arquivos intocaveis seguem
listados como ` M` (modificados, nao staged, nao commitados).
  </done>
</task>

<task type="auto">
  <name>Task 2: Config do ruff no pyproject, template de migration corrigido e autofix</name>
  <files>pyproject.toml, alembic/script.py.mako, alembic/env.py, alembic/versions/*.py, app/modules/pedidos/domain/motor_adequacao.py, app/shared/config/settings.py, app/tests/test_pedidos_motor.py, app/tests/test_pedidos_processing_repository.py, app/tests/test_pedidos_processing_service.py</files>
  <action>
**1. Adicionar a config do ruff ao `pyproject.toml`** (depois de `[tool.pytest.ini_options]`,
antes de `[tool.pyright]`):

- `[tool.ruff]` com `target-version = "py313"` **explicito**. Nao definir `line-length`:
  o repositorio inteiro ja esta formatado no default 88, e mexer nisso reformataria 288
  arquivos de graca.
- `[tool.ruff.lint]` com `select = ["E4", "E7", "E9", "F", "B", "UP", "I", "SIM", "C4"]`
  — preserva exatamente o conjunto que o CI ja usava e acrescenta os 5 novos.
- `[tool.ruff.lint.flake8-bugbear]` com `extend-immutable-calls` listando os callables de
  injecao do FastAPI: `fastapi.Depends`, `fastapi.Query`, `fastapi.Path`, `fastapi.Body`,
  `fastapi.Header`, `fastapi.Form`, `fastapi.File`, `fastapi.Cookie`, `fastapi.Security`.

  Comentar acima **por que**: B008 proibe chamada em default de argumento, mas em FastAPI
  `= Depends(...)` e a forma canonica de declarar dependencia, nao um default mutavel.
  Sao 47 falsos positivos, 11 deles no `routes.py` de auth que esta task nao pode tocar —
  a config e justamente o que torna essa task viavel sem encostar no arquivo alheio.

**2. Corrigir `alembic/script.py.mako`** — esta e a raiz de 76 dos 110 achados.

O template hoje gera `from typing import Sequence, Union` (UP035 + I001) e tres anotacoes
`Union[...]` (UP007) para `down_revision`, `branch_labels` e `depends_on`. Ou seja: **toda
migration nova nasceria quebrando a regua nova**, e este projeto cria migrations com
frequencia (33 ate agora, mais a 11-02 pendente da Victoria). Sem corrigir o template, a
regua vira atrito recorrente em vez de gate.

Trocar para `from collections.abc import Sequence` e anotacoes PEP 604 (`str | None`,
`str | Sequence[str] | None`), mantendo a ordem de import que o isort do ruff espera
(`collections.abc` antes de `from alembic import op` / `import sqlalchemy as sa`).

**3. Aplicar o autofix** (90 correcoes em 24 arquivos):

    uv run ruff check --fix app alembic
    uv run ruff format app alembic

Distribuicao esperada: UP007 x54, UP035 x18, C420 x9, I001 x7, SIM300 x2. A maior parte
(76) cai em `alembic/versions/` e e o mesmo boilerplate que a etapa 2 corrige na raiz —
migrations ja aplicadas sao apenas codigo Python, reescrever anotacao nelas nao altera o
schema nem a cadeia de revisoes. Usar somente `--fix` (seguro); **nao** usar
`--unsafe-fixes`.

**Guard-rails desta task:**

- Rodar `git status --short` logo apos o autofix e confirmar que os 3 arquivos intocaveis
  **nao** foram alterados por ele. Se tiverem sido, **pare e reporte** — reverter com
  `git checkout --` esta proibido porque destruiria o trabalho da outra sessao.
- Conferir que `alembic/env.py` manteve os 8 `# noqa: F401`. O autofix reagrupa o import de
  `AuthOtpChallenge, AuthUser` em bloco parentizado e move o `# noqa` para a linha do
  parentese de abertura — isso e correto e esperado.
  </action>
  <verify>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && ! grep -q "Union\[" alembic/script.py.mako && ! grep -q "from typing import Sequence" alembic/script.py.mako && grep -q "str | None" alembic/script.py.mako && echo TEMPLATE_OK</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && test "$(grep -c 'noqa: F401' alembic/env.py)" = "8" && test "$(git status --short -- app/modules/auth/infrastructure/http/routes.py app/modules/auth/infrastructure/repositorio_otp.py app/tests/test_docs_seguranca.py | grep -c '^ M')" = "3" && echo GUARDRAILS_OK</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && uv run ruff format --check app alembic && uv run pyright app alembic 2>&1 | tail -2 && uv run alembic check</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q 2>&1 | tail -3</automated>
  </verify>
  <done>
`pyproject.toml` tem `[tool.ruff]`, `[tool.ruff.lint]` e `[tool.ruff.lint.flake8-bugbear]`;
`script.py.mako` sem `Union[`; os 8 `noqa: F401` intactos; `alembic check` sem drift;
pyright 0 errors; suite em 889 passed / 18 skipped / 0 failed. `uv run ruff check app alembic`
agora deve reportar **exatamente os 20 achados manuais** da Task 3 (B904 x7, B017 x3,
B905 x2, SIM102 x2, SIM105 x2, SIM117 x2, UP047 x2) — nenhum a mais.

Commit com caminhos explicitos, incluindo os arquivos realmente tocados pelo autofix
(use `git status --short` para list -los; nao usar `-A`, `.` nem `-a`):

    git add pyproject.toml alembic/script.py.mako alembic/env.py alembic/versions/ app/modules/pedidos/domain/motor_adequacao.py app/shared/config/settings.py app/tests/test_pedidos_motor.py app/tests/test_pedidos_processing_repository.py app/tests/test_pedidos_processing_service.py
    git commit -m "chore(lint): formalizar config do ruff no pyproject e aplicar autofix da regua nova"
  </done>
</task>

<task type="auto">
  <name>Task 3: Resolver os 20 achados manuais e subir o gate do CI</name>
  <files>app/workers/tasks/ingestao.py, app/workers/tasks/pedidos.py, app/modules/parametros/domain/registro.py, app/modules/parametros/domain/leitura_resiliente.py, app/modules/pedidos/infrastructure/repositorio_ordens_linx.py, app/modules/realtime/infrastructure/connection_manager.py, app/modules/realtime/infrastructure/http/routes.py, app/tests/test_auth_flows.py, app/tests/test_pedidos_motor.py, app/tests/test_ingestao_sync.py, app/tests/test_pedidos_product_writes.py, .github/workflows/pipeline.yml</files>
  <action>
Corrigir os 20 achados que o autofix nao cobre. Localizacoes exatas ja levantadas no
planejamento — reconfirme com `uv run ruff check app alembic --output-format concise`
antes de editar, porque as linhas mudaram com o autofix da Task 2.

**B904 x7 — `raise ... from ...` dentro de `except`**
`app/workers/tasks/ingestao.py` (1) e `app/workers/tasks/pedidos.py` (6).
Preservar a causa original com `from err` quando o `except` liga a excecao a um nome e o
encadeamento ajuda o diagnostico. Onde a nova excecao **substitui** deliberadamente a
original (caso do `raise AssertionError("unreachable")` em `pedidos.py`, que so existe
para satisfazer o type checker depois de um `retry`/`fail` terminal), usar `from None`.
Nao alterar a semantica de retry das tasks Celery — so o encadeamento da excecao.

**SIM102 x2 — `if` aninhado colapsavel**
`app/modules/parametros/domain/registro.py`. Fundir em um unico `if` com `and`, **desde
que** o resultado continue legivel; se a condicao fundida ficar longa demais, quebrar em
variavel intermediaria nomeada em vez de deixar uma linha ilegivel.

**SIM105 x2 — `try/except/pass`**
`app/modules/realtime/infrastructure/connection_manager.py` e
`app/modules/realtime/infrastructure/http/routes.py`. Trocar por
`contextlib.suppress(RuntimeError, TimeoutError)`, adicionando `import contextlib`.
Manter exatamente o mesmo conjunto de excecoes suprimidas — nao alargar.

**SIM117 x2 — `with` aninhados**
`app/tests/test_ingestao_sync.py` e `app/tests/test_pedidos_product_writes.py`.
Fundir em um unico `with` com contextos separados por virgula.

**B905 x2 — `zip()` sem `strict=`**
`app/tests/test_pedidos_motor.py`. Usar `strict=True` (os pares comparados nesses testes
devem ter o mesmo comprimento; se algum quebrar, e bug de teste legitimo a investigar,
nao motivo para usar `strict=False`).

**B017 x3 — `pytest.raises(Exception)` cego**
`app/tests/test_auth_flows.py`. Preferir estreitar para a excecao concreta que o fluxo
levanta. Se ao inspecionar ficar claro que o teste quer mesmo provar "qualquer falha"
(ex.: fronteira que agrega erros heterogeneos), manter e silenciar **pontualmente** com
`# noqa: B017` **acompanhado de comentario justificando** — nunca com ignore global.

**UP047 x2 — generics pre-PEP 695**
`app/modules/parametros/domain/leitura_resiliente.py::get_param_value` e
`app/modules/pedidos/infrastructure/repositorio_ordens_linx.py::_chunks`.
Converter para a sintaxe nativa: `async def get_param_value[T](...)` e
`def _chunks[T](...)`. Em seguida remover o `T = TypeVar("T")` de cada arquivo e ajustar o
import de `typing`: em `leitura_resiliente.py` sobra `from typing import Any` (Any segue
usado em `Callable[[Any], T]`); em `repositorio_ordens_linx.py` o `from typing import TypeVar`
sai inteiro (era o unico simbolo importado de `typing`).
Se o pyright reclamar da sintaxe PEP 695 nesses dois pontos, **nao force**: reverta os dois
e adicione `UP047` a um `ignore` em `[tool.ruff.lint]` com comentario explicando o motivo.

**Atualizar `.github/workflows/pipeline.yml`** (job `quality`):

Trocar o passo "Validar lint (ruff check)" de
`uv run ruff check --select E4,E7,E9,F app alembic` para
`uv run ruff check app alembic` — sem `--select`, porque a selecao passa a vir do
`pyproject.toml` (fonte unica). **Manter o escopo `app alembic`** e nao mexer nos passos
de `ruff format --check` nem de `pyright`, que ja estao corretos.
  </action>
  <verify>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && uv run ruff check app alembic && uv run ruff format --check app alembic && uv run pyright app alembic 2>&1 | tail -2</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && grep -q "uv run ruff check app alembic" .github/workflows/pipeline.yml && ! grep -q "ruff check --select" .github/workflows/pipeline.yml && echo CI_OK</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && test "$(git status --short -- app/modules/auth/infrastructure/http/routes.py app/modules/auth/infrastructure/repositorio_otp.py app/tests/test_docs_seguranca.py | grep -c '^ M')" = "3" && echo UNTOUCHED_OK</automated>
    <automated>cd "C:/Users/bianca.teixeira/Desktop/PROJETO SYSTEM-AUTOMATION/backend" && docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q 2>&1 | tail -3</automated>
  </verify>
  <done>
`uv run ruff check app alembic` responde "All checks passed!" sem nenhum `--select` na CLI;
`ruff format --check` passa; pyright em 0 errors; pipeline.yml sem `--select` e com escopo
`app alembic`; os 3 arquivos intocaveis seguem ` M`; suite em 889 passed / 18 skipped /
0 failed.

Commit com caminhos explicitos (nunca `-A`, `.` ou `-a`) — dois commits, separando codigo
de infraestrutura de CI:

    git add app/workers/tasks/ingestao.py app/workers/tasks/pedidos.py app/modules/parametros/domain/registro.py app/modules/parametros/domain/leitura_resiliente.py app/modules/pedidos/infrastructure/repositorio_ordens_linx.py app/modules/realtime/infrastructure/connection_manager.py app/modules/realtime/infrastructure/http/routes.py app/tests/test_auth_flows.py app/tests/test_pedidos_motor.py app/tests/test_ingestao_sync.py app/tests/test_pedidos_product_writes.py
    git commit -m "refactor(lint): resolver achados de bugbear, simplify e pyupgrade da regua nova"

    git add .github/workflows/pipeline.yml
    git commit -m "ci(lint): ler selecao do ruff do pyproject em vez do --select da CLI"

Se `UP047` acabar em `ignore`, `pyproject.toml` entra tambem no primeiro `git add`.
  </done>
</task>

</tasks>

<verification>

Gate final, todos obrigatorios:

1. `uv run ruff check app alembic` → "All checks passed!" (sem `--select` na CLI)
2. `uv run ruff format --check app alembic` → 288 files already formatted
3. `uv run pyright app alembic` → 0 errors, 0 warnings
4. `uv run alembic check` → sem drift entre models e schema
5. `docker compose -f .docker/docker-compose.yml exec -T api python -m pytest app/tests -q`
   → 889 passed, 18 skipped, 0 failed
6. `grep -c "noqa: F401" alembic/env.py` → 8
7. `git status --short` → os 3 arquivos da outra sessao ainda como ` M`, nao commitados
8. `git log --oneline -4` → commits atomicos deste trabalho, sem mencao a Claude/coautoria

</verification>

<success_criteria>

- Dead code removido: `_like_pattern`, `carregar_modificacoes` e o Protocol orfao
  `_PedidoModificacaoResult` nao existem mais; nenhum outro simbolo removido.
- `pyproject.toml` e a fonte unica da config do ruff; CI e local concordam.
- Regua ampliada de E4/E7/E9/F para + B, UP, I, SIM, C4, com os 110 achados zerados.
- `alembic/script.py.mako` corrigido, para que migrations novas nascam compativeis.
- B008 do FastAPI silenciado por config justificada, nao por `noqa` espalhado.
- Zero alteracao nos 3 arquivos da outra sessao.
- Suite e pyright inalterados em relacao ao baseline.

</success_criteria>

<output>
Criar `.planning/quick/260826-fer-consolidar-cleanup-de-dead-code-no-backe/260826-fer-SUMMARY.md` ao terminar.
</output>
