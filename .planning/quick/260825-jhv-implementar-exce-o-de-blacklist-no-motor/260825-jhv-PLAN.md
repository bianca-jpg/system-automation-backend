---
quick_id: 260825-jhv
slug: implementar-exceção-de-blacklist-no-motor
date: 2026-08-25
mode: quick-full
repos:
  - backend
autonomous: true
files_modified:
  - alembic/versions/033_pedido_indica_blacklist.py
  - app/modules/ingestao/infrastructure/models.py
  - app/modules/pedidos/domain/standby_motivo.py
  - app/modules/ingestao/infrastructure/databricks_reader.py
  - app/modules/ingestao/domain/agregacao.py
  - app/modules/ingestao/infrastructure/repositorio_snapshot.py
  - app/modules/pedidos/processing/infrastructure/adapters.py
  - app/modules/pedidos/domain/motor_adequacao.py
  - app/tests/factories_pedidos_motor.py
  - app/tests/test_pedidos_motor.py
  - app/tests/test_pedidos_motor_blacklist.py
  - app/tests/test_pedidos_processing_repository.py
  - app/tests/test_migration_033_pedido_indica_blacklist.py
  - .planning/STATE.md
must_haves:
  truths:
    - "Cliente com indica_blacklist='SIM' nunca recebe grade alterada pelo motor, mesmo que o usuário processe com modo=ADEQUAR — o pedido é tratado tudo-ou-nada por produto, exatamente como no modo sem adequação."
    - "Falta de estoque em qualquer tamanho pedido por um cliente blacklist coloca o produto inteiro em stand-by com motivo canônico 'blacklist' (nunca 'furo_grade' nem 'sem_estoque'), sem reservar peças de tamanhos que tinham estoque disponível."
    - "A exceção blacklist é por nr_pedido e vale para TODOS os produtos daquele pedido, mas cada produto continua sendo tudo-ou-nada INDEPENDENTEMENTE (um produto do pedido pode gerar OR enquanto outro do mesmo pedido fica em stand-by)."
    - "Cliente sem indica_blacklist (ausente ou 'NÃO' na origem) continua se comportando exatamente como antes desta mudança — comportamento coberto por teste de regressão explícito, não só pelo default estrutural da coluna."
    - "A coluna indica_blacklist chega intacta do Databricks até o motor de adequação, passando por ingestão (databricks_reader → agregacao → repositorio_snapshot) e pelo caminho real que alimenta o motor (adapters.py::load_pending_items → build_processing_plan → agrupar_por_produto)."
    - "O motivo canônico 'blacklist' existe no vocabulário de domínio (standby_motivo.py) e na CHECK constraint do banco (pedido_standby_motivo.ck_psm_motivo), sem reaproveitar furo_grade/sem_estoque."
    - "A invariante len(preteridos) == len(preteridos_motivo) continua valendo nos 4 pontos de escrita do motor, incluindo o novo caminho blacklist."
    - "A suíte de testes completa passa sem novas falhas além da 1 falha conhecida da baseline (849 passed / 17 skipped / 1 failed)."
  artifacts:
    - path: "alembic/versions/033_pedido_indica_blacklist.py"
      provides: "Migration nova (down_revision=032) que adiciona indica_blacklist em pedidos e pedido_produto_read, e amplia ck_psm_motivo para aceitar 'blacklist'"
      contains: "down_revision: str | None = \"032\""
    - path: "app/modules/pedidos/domain/standby_motivo.py"
      provides: "Vocabulário canônico de motivo de stand-by com BLACKLIST adicionado"
      contains: "BLACKLIST = \"blacklist\""
    - path: "app/modules/pedidos/domain/motor_adequacao.py"
      provides: "blacklist_nrs + despacho forçado para o caminho tudo-ou-nada + relabeling de motivo"
      contains: "blacklist_nrs"
    - path: "app/modules/pedidos/processing/infrastructure/adapters.py"
      provides: "load_pending_items propaga indica_blacklist até o motor"
      contains: "indica_blacklist"
    - path: "app/tests/test_pedidos_motor_blacklist.py"
      provides: "Cenários (a)-(e) + regressão da exceção blacklist"
      min_lines: 60
    - path: "app/tests/test_migration_033_pedido_indica_blacklist.py"
      provides: "Ciclo destrutivo upgrade/downgrade da migration 033"
      min_lines: 30
  key_links:
    - from: "app/modules/pedidos/domain/standby_motivo.py"
      to: "app/modules/pedidos/domain/motor_adequacao.py"
      via: "import BLACKLIST no topo do motor, mesmo padrão de FURO_GRADE/SEM_ESTOQUE"
      pattern: "from app\\.modules\\.pedidos\\.domain\\.standby_motivo import.*BLACKLIST"
    - from: "alembic/versions/033_pedido_indica_blacklist.py"
      to: "pedido_standby_motivo.ck_psm_motivo"
      via: "DROP CONSTRAINT + ADD CONSTRAINT com os 4 motivos"
      pattern: "ck_psm_motivo"
    - from: "motor_adequacao.py::blacklist_nrs"
      to: "motor_adequacao.py::_processar_pedidos_canal despacho por modo"
      via: "condição 'modo == ModoAdequacao.SEM_ADEQUAR or nr in blacklist_nrs'"
      pattern: "nr in blacklist_nrs"
    - from: "adapters.py::load_pending_items"
      to: "motor_adequacao.py::_processar_pedidos_canal (todos_pedidos_flat)"
      via: "dict retornado com chave indica_blacklist, sem projeção em build_processing_plan/agrupar_por_produto"
      pattern: "\"indica_blacklist\": bool\\(row\\[.indica_blacklist.\\]\\)"
---

<objective>
Implementar a exceção de blacklist no motor de adequação: cliente com `indica_blacklist="SIM"`
(nova coluna da view Databricks `system_automation_pedidos_em_aberto`) nunca pode ter a grade alterada
pelo motor, mesmo que o usuário escolha `modo=ADEQUAR` — o pedido dele é sempre tratado
tudo-ou-nada por produto (igual ao modo sem adequação hoje), com motivo canônico novo
`"blacklist"` quando faltar estoque de algum tamanho pedido.

Este é trabalho de exceção transversal aos motores já entregues nas Phases 14 (motor puro),
15 (roteamento global) e 16 (persistência do motivo de stand-by) do milestone v1.3 — não abre
nem reabre nenhuma dessas fases formalmente, é uma quick task cirúrgica sobre o código que elas
já deixaram pronto.

Purpose: cumprir uma regra de negócio dura da mantenedora — clientes blacklist não podem receber
peças de tamanhos que não pediram, sob nenhuma circunstância, mesmo que o operador clique
"processar com adequação para todos os pedidos".

Output:
- Migration `033` (colunas `indica_blacklist` em `pedidos`/`pedido_produto_read` + CHECK
  constraint `ck_psm_motivo` ampliada para 4 motivos).
- `indica_blacklist` propagada ponta-a-ponta: Databricks → ingestão → `load_pending_items` →
  motor de adequação.
- Motor força qualquer pedido blacklist para o caminho tudo-ou-nada por produto, independente do
  modo escolhido, gravando motivo `"blacklist"` (nunca `furo_grade`/`sem_estoque`) quando preterido.
- Testes cobrindo os 5 cenários de negócio + regressão + migration.
- Nota de handoff no STATE.md para a Fase 17 (UI de stand-by, ainda não construída) tratar o novo
  motivo visualmente quando chegar a vez dela — UI está fora de escopo aqui.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

# Vocabulário canônico de motivo de stand-by — ganha BLACKLIST
@app/modules/pedidos/domain/standby_motivo.py

# Migration anterior (032) — estilo/convenções a seguir para a 033
@alembic/versions/032_pedido_standby_motivo.py

# Núcleo do motor — onde entra a exceção blacklist
@app/modules/pedidos/domain/motor_adequacao.py

# Caminho real que alimenta o motor (não é pedido_produto_read)
@app/modules/pedidos/processing/infrastructure/adapters.py

# Ingestão full refresh
@app/modules/ingestao/infrastructure/databricks_reader.py
@app/modules/ingestao/domain/agregacao.py
@app/modules/ingestao/infrastructure/repositorio_snapshot.py
@app/modules/ingestao/infrastructure/models.py

# Factories e testes de referência (estilo a espelhar)
@app/tests/factories_pedidos_motor.py
@app/tests/test_pedidos_motor_furo_grade.py
@app/tests/test_migration_032_pedido_standby_motivo.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Schema — vocabulário BLACKLIST + migration 033</name>
  <files>
    app/modules/pedidos/domain/standby_motivo.py,
    app/modules/ingestao/infrastructure/models.py,
    alembic/versions/033_pedido_indica_blacklist.py
  </files>
  <action>
    GUARDA PRÉVIA (rodar antes de tocar em qualquer arquivo): `./.venv/Scripts/python.exe -m
    alembic heads` deve mostrar `032 (head)` como único head. Se mostrar qualquer outra coisa
    (ex. um segundo head da migration da Victoria), PARE e reporte — não crie a 033 sobre uma
    base errada.

    1. `standby_motivo.py`: adicionar `BLACKLIST = "blacklist"` e incluir no `frozenset` de
       `MOTIVOS_VALIDOS` e na lista `__all__` (hoje só tem `SEM_CREDITO`/`SEM_ESTOQUE`/
       `FURO_GRADE`).
    2. `models.py`: em `Pedido` (linhas ~30-57) e em `PedidoProdutoRead` (linhas ~107-165),
       adicionar `indica_blacklist: Mapped[bool] = mapped_column(Boolean, nullable=False,
       default=False)` (em `PedidoProdutoRead`, seguir o padrão de `credito_bloqueado` que usa
       `server_default="false"` além de `default=False`, já que a tabela é escrita por SQL puro
       em `repositorio_snapshot.py`, não pelo ORM).
    3. Migration nova `alembic/versions/033_pedido_indica_blacklist.py`: `revision = "033"`,
       `down_revision = "032"`. Seguir o estilo de `032_pedido_standby_motivo.py` (docstring
       explicando o motivo, `from collections.abc import Sequence`, `import sqlalchemy as sa`,
       `from alembic import op`). `upgrade()`:
       - `op.add_column("pedidos", sa.Column("indica_blacklist", sa.Boolean(), nullable=False,
         server_default=sa.text("false")))`
       - `op.add_column("pedido_produto_read", sa.Column("indica_blacklist", sa.Boolean(),
         nullable=False, server_default=sa.text("false")))`
       - Trocar o CHECK `ck_psm_motivo` da tabela `pedido_standby_motivo`: `op.drop_constraint
         ("ck_psm_motivo", "pedido_standby_motivo", type_="check")` seguido de
         `op.create_check_constraint("ck_psm_motivo", "pedido_standby_motivo", "motivo IN
         ('sem_credito', 'sem_estoque', 'furo_grade', 'blacklist')")`.
       `downgrade()`: simétrico — recria o CHECK com só os 3 valores originais, depois
       `op.drop_column` das duas colunas novas (ordem inversa do upgrade).
  </action>
  <verify>
    <automated>./.venv/Scripts/python.exe -m alembic heads</automated>
  </verify>
  <done>
    `alembic heads` mostra `033 (head)`; `standby_motivo.py` exporta `BLACKLIST` em
    `MOTIVOS_VALIDOS` (4 valores) e em `__all__`; `models.py` tem `indica_blacklist` em `Pedido`
    e `PedidoProdutoRead`.
  </done>
</task>

<task type="auto">
  <name>Task 2: Ingestão — propagar indica_blacklist do Databricks até o snapshot</name>
  <files>
    app/modules/ingestao/infrastructure/databricks_reader.py,
    app/modules/ingestao/domain/agregacao.py,
    app/modules/ingestao/infrastructure/repositorio_snapshot.py
  </files>
  <action>
    1. `databricks_reader.py::ler_pedidos_em_aberto()`: acrescentar `indica_blacklist` à lista de
       colunas do SELECT (hoje `nr_pedido, cd_prod_cor, sg_tamanho, ds_grupo, qt_entregar,
       vl_liquido, nm_cliente, ds_tp_canal, nm_prod, status_credito, dt_emissao`). NÃO incluir
       `bloqueio_faturamento` (fora de escopo).
    2. `agregacao.py::agregar_itens_pedidos()`: no dict do item agregado (bloco que já tem
       `"client"`, `"canal"`, `"status_credito"`, `"ds_produto"`, `"data"`), acrescentar
       `"indica_blacklist": _parse_bool(linha.get("indica_blacklist"))` — `_parse_bool` já existe
       no módulo e já trata `"sim"` (case-insensitive) como `True`; qualquer outro valor
       (`"não"`, ausente, `None`) vira `False`, o que é aceitável.
    3. `repositorio_snapshot.py::_PENDING_READ_INSERT_SQL`: na CTE `products`, acrescentar
       `bool_or(p.indica_blacklist) AS indica_blacklist` (mais simples que `credito_bloqueado`,
       já é booleano, sem pattern matching). Propagar a coluna na lista de colunas do `INSERT`
       (`..., credito_bloqueado, indica_blacklist, indica_reserva, indica_embalado, synced_at`)
       e no `SELECT` final correspondente (`p.credito_bloqueado, p.indica_blacklist, false,
       false, statement_timestamp()`). NÃO tocar em `_ERP_READ_INSERT_SQL` — linhas do ERP não
       inserem valor explícito para `indica_blacklist`, então usam o `server_default=false` da
       coluna automaticamente (mesmo padrão implícito que outras colunas booleanas novas teriam).
       `substituir_pedidos()` NÃO precisa de alteração: ela já faz `Pedido(**item)` com
       desempacotamento genérico, e o dict de `agregar_itens_pedidos` agora inclui
       `indica_blacklist`, que casa direto com a nova coluna do model.
  </action>
  <verify>
    <automated>./.venv/Scripts/python.exe -m pytest app/tests/test_ingestao_agregacao.py -x -q</automated>
  </verify>
  <done>
    `ler_pedidos_em_aberto` seleciona `indica_blacklist`; `agregar_itens_pedidos` devolve a chave
    `indica_blacklist` (bool) em cada item agregado; a CTE `products` do full refresh de
    `pedido_produto_read` grava `indica_blacklist` via `bool_or`.
  </done>
</task>

<task type="auto">
  <name>Task 3: Motor — despacho forçado tudo-ou-nada + wire do dado real (adapters.py)</name>
  <files>
    app/modules/pedidos/processing/infrastructure/adapters.py,
    app/modules/pedidos/domain/motor_adequacao.py
  </files>
  <action>
    1. `adapters.py::_PENDING_BASE_SQL`: acrescentar `p.indica_blacklist` à lista de colunas do
       SELECT da CTE `pending_base` (hoje `p.id, p.nr_pedido, p.cd_prod_cor, p.sg_tamanho,
       p.ds_grupo, p.qt_entregar AS qt_liquida, p.vl_liquido, p.status_credito, p.client,
       p.canal, p.ds_produto, p.data, CASE ... END AS canonical_channel`).
    2. `adapters.py::load_pending_items()`: acrescentar `indica_blacklist` ao SELECT final
       (`FROM eligible_items ORDER BY ...`) e `"indica_blacklist": bool(row["indica_blacklist"])`
       ao dict retornado no laço `async for row in result.mappings()`. Este dict passa por
       `agrupar_por_produto()` e por `build_processing_plan()` (`flat.append(item)`) SEM
       projeção de campos — a chave chega intacta em `todos_pedidos_flat` dentro do motor, então
       nenhuma outra mudança de wiring é necessária fora deste arquivo e do motor.
    3. `motor_adequacao.py`: importar `BLACKLIST` de `app.modules.pedidos.domain.standby_motivo`
       no topo (mesma linha de import de `FURO_GRADE, SEM_ESTOQUE`).
    4. Em `_processar_pedidos_canal`, logo depois do bloco que computa `sem_credito_nrs` (com seu
       log `[Credito] %d pedido(s)...`), adicionar o cálculo análogo:
       `blacklist_nrs = {nr for nr, itens in todos_pedidos_flat.items() if
       any(i.get("indica_blacklist") for i in itens)}`. Não precisa de log dedicado (o log do
       modo tudo-ou-nada forçado é opcional; se adicionar, seguir o estilo de `logger.info` já
       usado para `sem_credito_nrs`).
    5. Trocar a condição de entrada no despacho por modo dentro do laço `for nr in
       ordens_prioridade:` de `if modo == ModoAdequacao.SEM_ADEQUAR:` para
       `if modo == ModoAdequacao.SEM_ADEQUAR or nr in blacklist_nrs:` — isso força QUALQUER
       pedido blacklist para o ramo tudo-ou-nada, independente do `modo` do request.
    6. Dentro desse mesmo ramo, quando `tem_furo_de_grade` retornar `True` e o código hoje grava
       `preteridos_motivo[par] = FURO_GRADE`, trocar para
       `preteridos_motivo[par] = BLACKLIST if nr in blacklist_nrs else FURO_GRADE`. NÃO alterar a
       segunda ocorrência de `preteridos_motivo[par] = FURO_GRADE` (a que fica mais abaixo, dentro
       do ramo `ADEQUAR` normal) — pedidos blacklist já saem do laço nesse ponto via `continue` do
       ramo do item 5, então aquele segundo ponto nunca roda para `nr` blacklist.
    7. `_commitar_par`: adicionar parâmetro keyword-only `motivo_sem_estoque: str = SEM_ESTOQUE` à
       assinatura, e usá-lo (em vez do literal `SEM_ESTOQUE`) na única linha onde a função hoje
       grava `preteridos_motivo[par] = SEM_ESTOQUE` quando `gerou_or` é `False`. No call-site
       dentro do ramo tudo-ou-nada/blacklist (o que roda `aplicar_tudo_ou_nada` seguido de
       `_commitar_par`), passar `motivo_sem_estoque=BLACKLIST if nr in blacklist_nrs else
       SEM_ESTOQUE`. O OUTRO call-site de `_commitar_par` (dentro do ramo `ADEQUAR`, depois do
       recálculo financeiro) NÃO recebe esse argumento — continua usando o default `SEM_ESTOQUE`,
       comportamento idêntico ao de hoje.
    8. Confirme explicitamente (não assuma) que a invariante `len(preteridos) ==
       len(preteridos_motivo)` continua valendo nos 4 pontos de escrita do motor depois destas
       mudanças — isso será coberto por asserts nos testes da Task 4, mas releia o código para
       garantir que todo `append` em `preteridos` dentro do ramo tocado tem uma escrita
       correspondente em `preteridos_motivo` na mesma branch.
  </action>
  <verify>
    <automated>./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_motor_furo_grade.py -x -q</automated>
  </verify>
  <done>
    `load_pending_items` devolve `indica_blacklist`; `_processar_pedidos_canal` calcula
    `blacklist_nrs` e força esses pedidos para o ramo tudo-ou-nada mesmo em `modo=ADEQUAR`,
    gravando `preteridos_motivo[par] = BLACKLIST` (nunca `FURO_GRADE`/`SEM_ESTOQUE`) quando
    preterido; testes pré-existentes de furo de grade/motor continuam verdes (prova de que o
    comportamento não-blacklist não regrediu).
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 4: Testes — cenários de negócio, regressão, propagação e migration</name>
  <files>
    app/tests/factories_pedidos_motor.py,
    app/tests/test_pedidos_motor.py,
    app/tests/test_pedidos_motor_blacklist.py,
    app/tests/test_pedidos_processing_repository.py,
    app/tests/test_migration_033_pedido_indica_blacklist.py
  </files>
  <behavior>
    - `test_pedidos_motor.py::test_standby_motivo_valores_canonicos_e_distintos` (EXISTENTE):
      precisa ser atualizado — hoje afirma `MOTIVOS_VALIDOS == {FURO_GRADE, SEM_CREDITO,
      SEM_ESTOQUE}` (3 valores) e vai FALHAR sozinho assim que `BLACKLIST` entrar no frozenset na
      Task 1. Atualizar para incluir `BLACKLIST` e `len(...) == 4`.
    - Cenário (a): cliente blacklist, estoque completo em todos os tamanhos pedidos,
      `modo=ADEQUAR` → gera OR com grade EXATAMENTE igual ao pedido, nenhum tamanho extra,
      nenhum corte (par em `selecionados`, não em `preteridos`).
    - Cenário (b): cliente blacklist, falta de estoque num tamanho INTERIOR à grade pedida (ex.
      PP/M/G com M sem estoque — cenário que, sem blacklist, geraria `FURO_GRADE`), `modo=ADEQUAR`
      → produto inteiro em stand-by com `preteridos_motivo[par] == BLACKLIST` (nunca
      `FURO_GRADE`), e NENHUMA peça reservada em nenhum tamanho, nem os que tinham estoque.
    - Cenário (c): mesmo pedido blacklist do cenário (a) ou (b), processado uma vez com
      `modo=ADEQUAR` e outra com `modo=ADEQUAR` vs `modo=SEM_ADEQUAR` (comparação explícita) →
      resultado IDÊNTICO em `resultados`/`preteridos`/`preteridos_motivo` nos dois modos — prova
      de que a exceção vence o parâmetro do request.
    - Cenário (d): pedido com 2 produtos do MESMO `nr_pedido` de um cliente blacklist, um com
      estoque completo (gera OR) e outro com furo/falta (`preteridos_motivo == BLACKLIST`) →
      confirma que a blacklist vale para todo o pedido, mas tudo-ou-nada É POR PRODUTO (não
      "o pedido inteiro vira tudo-ou-nada-do-pedido-inteiro-junto").
    - Cenário (e): dois `nr_pedido` diferentes no mesmo lote — um blacklist, um normal —
      processados juntos em `modo=ADEQUAR` → o pedido normal mantém comportamento ADEQUAR normal
      (ledger ±5%, furo de grade rotulado `FURO_GRADE` se ocorrer), só o blacklist é desviado.
    - Regressão: cliente com `indica_blacklist=False` (ausente/`"NÃO"` na origem, já convertido
      pelo `_parse_bool` da Task 2) processado em `modo=ADEQUAR` com um cenário que teria furo de
      grade → continua gravando `FURO_GRADE` (não `BLACKLIST`) — prova que o comportamento não
      regrediu, não só que o default estrutural existe.
    - Em cada cenário que gera `preteridos`, assertar explicitamente
      `len(resultado["preteridos"]) == len(resultado["preteridos_motivo"])`.
    - `test_pedidos_processing_repository.py`: teste novo (ou extensão de
      `test_planner_preflight_and_hydration_share_the_same_scope`) provando que
      `load_pending_items` devolve a chave `indica_blacklist` no dict de cada linha — seguir o
      padrão de `_seed_source`/`rows[0][...]` já usado nesse arquivo para `status_credito`.
    - `test_migration_033_pedido_indica_blacklist.py`: espelhar
      `test_migration_032_pedido_standby_motivo.py` (mesmos helpers `_isolated_database_url`,
      `_alembic`, fixture `migration_database_url`). Provar: (1) upgrade para 033 adiciona
      `indica_blacklist` em `pedidos` e `pedido_produto_read`; (2) `ck_psm_motivo` aceita INSERT
      com `motivo='blacklist'` e continua rejeitando (`IntegrityError`) um motivo fora dos 4
      permitidos; (3) downgrade para 032 reverte as 2 colunas e o CHECK volta a rejeitar
      `'blacklist'`; (4) re-upgrade para 033 é limpo.
  </behavior>
  <action>
    1. `factories_pedidos_motor.py::item_pedido`: adicionar parâmetro nomeado
       `indica_blacklist: bool = False` (mesmo padrão de `status_credito`), incluído no dict
       `base`.
    2. Atualizar `test_pedidos_motor.py::test_standby_motivo_valores_canonicos_e_distintos`
       conforme o `<behavior>` acima.
    3. Criar `app/tests/test_pedidos_motor_blacklist.py` (arquivo novo, dedicado — mesmo padrão de
       `test_pedidos_motor_furo_grade.py`: import de `processar_pedidos` via
       `app.modules.pedidos.service`, `ModoAdequacao` de
       `app.modules.pedidos.domain.politica_quantidade`, `BLACKLIST`/`FURO_GRADE`/`SEM_ESTOQUE`
       de `app.modules.pedidos.domain.standby_motivo`, e `item_pedido`/`estoque_produto`/
       `estoque_por_canal` de `app.tests.factories_pedidos_motor`) com os cenários (a)-(e) +
       regressão descritos no `<behavior>`.
    4. Adicionar o teste de propagação em `test_pedidos_processing_repository.py`.
    5. Criar `test_migration_033_pedido_indica_blacklist.py` conforme o `<behavior>`.
  </action>
  <verify>
    <automated>./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_motor_blacklist.py app/tests/test_pedidos_processing_repository.py -x -q</automated>
  </verify>
  <done>
    Todos os cenários (a)-(e) + regressão passam; `test_standby_motivo_valores_canonicos_e_distintos`
    atualizado e verde; `load_pending_items` comprovadamente devolve `indica_blacklist`; migration
    033 tem teste destrutivo dedicado (roda via `MIGRATION_TEST_DATABASE_URL`, pode ser `skip`
    localmente se a variável não estiver setada — comportamento idêntico ao teste da 032).
  </done>
</task>

<task type="auto">
  <name>Task 5: Verificação final da suíte + handoff para a Fase 17</name>
  <files>.planning/STATE.md</files>
  <action>
    1. Confirmar `./.venv/Scripts/python.exe -m alembic heads` mostra `033 (head)` como único head
       (reconfirmação pós-Task 1, antes de migrar o banco de teste).
    2. Migrar o banco de teste: `DATABASE_URL=postgresql+asyncpg://<mesmas credenciais do .env,
       trocando o nome do banco para>system_automation_test ./.venv/Scripts/python.exe -m alembic
       upgrade head` — usar a URL real do `.env` deste projeto, só troca o nome do banco para
       `system_automation_test` (o `conftest.py` não roda alembic automaticamente; ver nota em
       `.planning/STATE.md` § Blockers/Concerns sobre esse ponto).
    3. Rodar a suíte completa: `./.venv/Scripts/python.exe -m pytest`. Comparar contra a baseline
       conhecida documentada em STATE.md: 849 passed / 17 skipped / 1 failed (a 1 falha já existe
       hoje e não é desta mudança). Não pode haver NENHUMA falha nova. Se houver falha nova,
       corrigir antes de prosseguir — não reportar sucesso com falha nova pendente.
    4. Editar `.planning/STATE.md`: adicionar um bloco de handoff (próximo da seção "Deferred
       Items" ou como nota dentro de "Accumulated Context" — usar o Edit tool, NUNCA
       `gsd-tools query state.advance-plan`, que corrompe este STATE.md conforme já documentado)
       registrando: o motivo de stand-by `"blacklist"` já existe no banco
       (`pedido_standby_motivo.ck_psm_motivo`) e no dict retornado pelo motor a partir desta quick
       task (260825-jhv); a Fase 17 (tags de motivo de stand-by na UI, ainda não implementada —
       ver ROADMAP.md) precisa tratar esse motivo visualmente quando for construída; isso é
       trabalho de FRONTEND, fora de escopo desta quick task — nenhum arquivo do repo
       frontend foi tocado.
    5. NÃO usar `git add -A`, `git add .`, `git commit -a`, `git stash`, `git reset --hard` nem
       `git checkout --` em nenhum passo (working tree compartilhado com outras frentes, conforme
       já documentado) — usar caminhos explícitos em todo `git add`.
  </action>
  <verify>
    <automated>./.venv/Scripts/python.exe -m pytest -q</automated>
  </verify>
  <done>
    Suíte completa roda sem falha nova (baseline preservada: 849 passed / 17 skipped / 1 failed
    conhecida); `.planning/STATE.md` tem a nota de handoff da Fase 17 sobre o motivo `blacklist`.
  </done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|--------------|
| Databricks view → ingestão | `indica_blacklist` vem de fonte externa (view mantida por outro time); valor fora de "SIM"/"NÃO" deve degradar com segurança |
| Migration 033 → dados existentes | ALTER TABLE em tabelas de produção (`pedidos`, `pedido_produto_read`) e troca de CHECK constraint em `pedido_standby_motivo` |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-260825-01 | Tampering | `_parse_bool(linha.get("indica_blacklist"))` | mitigate | Valor inesperado da origem (não "SIM"/"NÃO"/booleano) já degrada para `False` pelo `_parse_bool` existente — falha para o lado conservador (cliente NÃO tratado como blacklist por engano é o risco aceito, nunca o inverso silencioso: um "SIM" mal formatado por acento/caixa ainda cai no branch `s in ("true","t","1","sim","s")`, que já cobre "sim" minúsculo/maiúsculo) |
| T-260825-02 | Denial of Service / Repudiation | Migration 033 `DROP CONSTRAINT` + `ADD CONSTRAINT` em `pedido_standby_motivo` | mitigate | `downgrade()` simétrico testado explicitamente em `test_migration_033_pedido_indica_blacklist.py`; migration roda dentro de uma única transação do Alembic (padrão do projeto), sem estado intermediário exposto |
| T-260825-03 | Tampering | Nenhuma dependência nova instalada (sem npm/pip/cargo install nesta task) | accept | Gate de legitimidade de pacote não se aplica — nenhum pacote novo entra no `pyproject.toml` |

</threat_model>

<verification>
1. `./.venv/Scripts/python.exe -m alembic heads` → `033 (head)`.
2. `DATABASE_URL=...system_automation_test ./.venv/Scripts/python.exe -m alembic upgrade head` roda
   limpo.
3. `./.venv/Scripts/python.exe -m pytest` → sem falhas novas além da baseline conhecida (849
   passed / 17 skipped / 1 failed).
4. `grep -n "BLACKLIST" app/modules/pedidos/domain/standby_motivo.py
   app/modules/pedidos/domain/motor_adequacao.py` mostra o símbolo definido e importado.
5. `.planning/STATE.md` contém a nota de handoff para a Fase 17.
</verification>

<success_criteria>
- Migration 033 existe, aplica limpo, e reverte limpo (downgrade simétrico testado).
- `indica_blacklist` propaga do Databricks até o motor sem projeção perdida em nenhuma camada.
- Pedido de cliente blacklist NUNCA gera OR com tamanho fora do pedido original, em nenhum modo.
- Falta de estoque em cliente blacklist grava motivo `"blacklist"`, nunca `"furo_grade"` ou
  `"sem_estoque"`.
- Cliente não-blacklist mantém comportamento idêntico ao pré-existente (regressão coberta).
- Suíte completa sem regressão (baseline 849/17/1 preservada).
- Nenhum arquivo do frontend tocado; handoff textual da Fase 17 registrado em STATE.md.
</success_criteria>

<output>
Criar `.planning/quick/260825-jhv-implementar-exce-o-de-blacklist-no-motor/260825-jhv-SUMMARY.md`
ao final da execução.
</output>
