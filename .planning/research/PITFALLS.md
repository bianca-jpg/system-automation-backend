# Pitfalls Research — Persistência do cabeçalho de OR (v1.1)

**Domain:** Persistência de cabeçalho de OR com numeração sequencial (Postgres `Identity`) + FK `or_id` NOT NULL com backfill retroativo (Alembic) + mudança de agrupamento de leitura + preparação para integração futura com ERP Linx, num monólito FastAPI/SQLAlchemy 2.0 async já em produção.
**Researched:** 2026-08-04
**Confidence:** HIGH para os pitfalls específicos deste codebase (grounded em leitura direta do código); MEDIUM/HIGH para os pitfalls genéricos de Postgres/Alembic/SQLAlchemy async (verificados via issues oficiais do Alembic/SQLAlchemy e documentação/discussões do Postgres).

Todos os pitfalls abaixo foram derivados lendo o código real do milestone v1.1: `app/modules/pedidos/models.py`, `app/modules/pedidos/infrastructure/repositorio_ordens.py`, `app/modules/pedidos/application/casos_uso.py`, `app/modules/pedidos/routes.py`, `app/modules/pedidos/schemas.py`, `alembic/versions/014_or_por_produto.py`, `alembic/env.py`, `app/tests/test_schema_guard.py`, `app/tests/test_pedidos_routes.py` e `.planning/PROJECT.md` / `.planning/codebase/CONCERNS.md` / `.planning/codebase/TESTING.md`. Não são pitfalls genéricos de "como usar Alembic" — são os pontos exatos onde ESTE código quebra ao receber ESTA feature.

## Critical Pitfalls

### Pitfall 1: Fragmentação da OR de negócio entre rodadas — o `or_id` pode quebrar o grão "1 produto + N clientes"

**What goes wrong:**
`salvar_ordens_reserva` (`repositorio_ordens.py:114-128`) grava/atualiza por par `(nr_pedido, cd_prod_cor)` e é chamada a cada rodada de adequação — cada clique em "Adequar" ou "Faturar sem adequação" (`casos_uso.py:440-468` e função equivalente de `sem_adequacao`), processando SÓ os pares ainda não processados naquele momento. Se o produto `PROD_X` é vendido para o pedido 11 na rodada 1 hoje e para o pedido 22 na rodada 2 dias depois, cada rodada cria SEU PRÓPRIO cabeçalho para `PROD_X` (regra decidida: "1 cabeçalho por produto por rodada de geração"). `GET /ordens-reserva` agrupado por `or_id` (em vez de `cd_prod_cor`, como hoje em `listar_ordens_reserva`, `casos_uso.py:354-419`) passaria a mostrar DUAS ORs separadas para o mesmo produto — quebrando exatamente a garantia que o sistema entrega hoje ("dois pedidos do mesmo produto = UMA ordem", testado em `test_pedidos_routes.py:360-403`).

**Why it happens:**
A leitura atual agrega por `cd_prod_cor` GLOBALMENTE (todas as linhas daquele produto, de qualquer época). A mudança de chave de agrupamento (produto → `or_id`) e a regra de escrita (cabeçalho novo por rodada) são decisões tratadas como independentes no PROJECT.md, mas colidem se implementadas ao pé da letra: a fragmentação por rodada é, ao mesmo tempo, o comportamento desejado (Key Decision "rodadas distintas são eventos de negócio distintos") e um risco de regressão visual/funcional se ninguém validar isso explicitamente antes do deploy.

**How to avoid:**
Decidir e registrar ANTES de codar se "rodada" = uma invocação de `salvar_ordens_reserva` (proposto no PROJECT.md) ou se um cabeçalho "aberto" (nenhum cliente aprovado ainda) deve ser reaproveitado por rodadas subsequentes tocando o mesmo produto. Se a intenção é mesmo fragmentar por rodada, validar explicitamente com a usuária que múltiplos cards do mesmo produto ao longo do tempo é aceitável — é uma mudança de UX visível, não só de schema. A Key Decision no PROJECT.md ainda está marcada como "Pending": esta é a decisão mais cara de errar de todo o milestone.

**Warning signs:**
O teste "dois pedidos do mesmo produto = UMA ordem" (`test_pedidos_routes.py:360`) passa a falhar, ou é ajustado sem qualquer teste novo cobrindo DUAS chamadas SEPARADAS de `salvar_ordens_reserva` para o mesmo `cd_prod_cor` — que é exatamente o requisito "Testes backend cobrindo o grão '1 OR por produto por rodada'" já sinalizado como Active no PROJECT.md.

**Phase to address:**
Fase de escrita (cabeçalho + `salvar_ordens_reserva`) — antes da fase de leitura (`GET /ordens-reserva` por `or_id`).

---

### Pitfall 2: `Identity()` e o autogenerate do Alembic

**What goes wrong:**
Colunas `Identity` do Postgres têm histórico de suporte frágil no autogenerate do Alembic/SQLAlchemy: `AttributeError` ao autogenerar em cima de uma tabela com coluna `Identity` existente ([alembic#775](https://github.com/sqlalchemy/alembic/issues/775)), diffs espúrios tentando remover/recriar a `Identity` mesmo sem mudança real ([alembic#821](https://github.com/sqlalchemy/alembic/issues/821), [discussion #1181](https://github.com/sqlalchemy/alembic/discussions/1181)), e falha de detecção quando o nome da tabela tem maiúsculas ([sqlalchemy#6129](https://github.com/sqlalchemy/sqlalchemy/issues/6129)).

**Why it happens:**
A comparação de `Identity` entre o metadata declarado no model e o schema real do banco não entende bem todos os parâmetros (`start`, `increment`, `always`) nas versões correntes de Alembic/SQLAlchemy.

**How to avoid:**
Escrever a migration da tabela `ordens_reserva_cabecalho` manualmente (`op.create_table(..., sa.Column("id", sa.Integer(), sa.Identity(start=255316), primary_key=True))`), usando autogenerate só como conferência visual, nunca aceitando o diff sem revisão manual linha a linha. Depois de aplicada, rodar `alembic revision --autogenerate` de novo e confirmar que NÃO aparece nenhum diff residual tocando a coluna `id` — se aparecer, é falso positivo conhecido; não "corrigir" editando a `Identity`.

**Warning signs:**
Um autogenerate posterior à 015 mostra `op.alter_column(..., existing_server_default=...)` na coluna `id` da tabela nova sem nenhuma mudança real de schema.

**Phase to address:**
Fase de schema/migration.

---

### Pitfall 3: Migration 015 repetindo o padrão de drift da migration 012

**What goes wrong:**
A migration `012` foi aplicada em dev e nunca commitada (fonte perdida, só restou o `.pyc`); a `014` precisou de lógica condicional (`_tem_cd_prod_cor`, `alembic/versions/014_or_por_produto.py:50-93`) para rodar em qualquer estado de banco. A `015` (cabeçalho + `or_id` NOT NULL + backfill) tem o MESMO risco: se testada localmente, revertida (`alembic downgrade`), editada e reaplicada várias vezes durante o desenvolvimento — comportamento já observado neste time —, o banco de dev pode ficar num estado intermediário (coluna `or_id` existe mas ainda nullable; cabeçalhos já criados cujos ids colidiriam com uma nova tentativa de Identity) que a versão final da migration não prevê.

**Why it happens:**
Iteração rápida em migrations complexas (DDL + backfill DML) por uma mantenedora iniciante, sem disciplina explícita de "testar upgrade+downgrade em sequência antes de considerar pronto".

**How to avoid:**
Escrever a 015 já com guarda condicional no estilo da 014 (checar se `or_id` já existe / já é NOT NULL antes de repetir `add_column` + backfill + `alter`), e testar SEMPRE `upgrade` → `downgrade` → `upgrade` antes de considerar a migration pronta — não só o upgrade isolado. Documentar no docstring da migration (como a 014 faz) o que o `downgrade` faz com os dados: aqui, provavelmente não é possível reverter sem perder a rastreabilidade de `or_id` — decidir e registrar essa perda explicitamente, em vez de deixar implícito.

**Warning signs:**
`alembic upgrade head` funciona limpo em CI (banco vazio) mas falha ou levanta `IntegrityError` em dev (banco com estado residual de uma tentativa anterior de aplicar a 015).

**Phase to address:**
Fase de schema/migration.

---

### Pitfall 4: `ALTER COLUMN or_id SET NOT NULL` sem o padrão `NOT VALID` + `VALIDATE CONSTRAINT`

**What goes wrong:**
Rodar direto `ALTER TABLE ordens_reserva ALTER COLUMN or_id SET NOT NULL` depois do backfill mantém um lock `ACCESS EXCLUSIVE` em `ordens_reserva` durante o scan de validação (varredura completa procurando `NULL`) — bloqueando toda leitura/escrita da tabela (`GET /pedidos`, `GET /ordens-reserva`, adequar, aprovar) durante a migration.

**Why it happens:**
É o comportamento padrão do Postgres para `SET NOT NULL` sem um `CHECK` constraint previamente validado.

**How to avoid:**
Para o volume atual do projeto (tabela pequena, poucas milhares de linhas), o lock dura milissegundos — aceitável, mas registrar a janela de manutenção esperada no PR. Se quiser lock mínimo desde já (barato de fazer, sem over-engineering real): `ALTER TABLE ordens_reserva ADD CONSTRAINT or_id_not_null CHECK (or_id IS NOT NULL) NOT VALID;` (lock leve, não escaneia a tabela) seguido de `VALIDATE CONSTRAINT` (lock `SHARE UPDATE EXCLUSIVE`, não bloqueia escritas concorrentes) e só então `SET NOT NULL` — o Postgres 12+ reconhece o `CHECK` já validado e pula o re-scan.

**Warning signs:**
A migration trava mais que alguns segundos em produção, ou requests concorrentes durante o deploy retornam timeout de conexão ao Postgres.

**Phase to address:**
Fase de schema/migration.

---

### Pitfall 5: Backfill dentro da migration usando o model ORM "vivo"

**What goes wrong:**
Para popular `ordens_reserva_cabecalho` + `or_id` a partir das linhas existentes de `ordens_reserva`, é tentador importar `OrdemReserva`/o novo `OrdemReservaCabecalho` de `app/modules/pedidos/models.py` dentro do arquivo de migration e usar o ORM para o backfill.

**Why it happens:**
Migrations Alembic devem ser um snapshot IMUTÁVEL do schema naquele ponto do tempo. Se `models.py` evoluir depois (novo campo, renomeação de coluna), rodar essa migration antiga do zero (onboarding, banco de CI limpo) executa código que referencia atributos que já não existem mais no model atual — quebrando `alembic upgrade head` para sempre em bases novas.

**How to avoid:**
Usar `sa.table()`/`sa.column()` locais dentro da própria migration (redeclarando apenas as colunas necessárias) ou SQL puro via `op.execute(sa.text(...))`. Nunca importar de `app.modules.*.models` dentro de `alembic/versions/`.

**Warning signs:**
`git diff`/`git blame` do arquivo de migration mostra `from app.modules.pedidos.models import ...`.

**Phase to address:**
Fase de schema/migration.

---

### Pitfall 6: `flush()` mal posicionado ao criar o cabeçalho antes de vincular `or_id`

**What goes wrong:**
Para preencher `OrdemReserva.or_id`, `salvar_ordens_reserva` precisa primeiro `db.add(cabecalho)` e então `await session.flush()` para o Postgres gerar o `id` via `Identity` (RETURNING) — sem isso, `cabecalho.id` é `None` e gravar `or_id=None` numa coluna NOT NULL levanta `IntegrityError` (falha alta, não silenciosa — mas confusa para quem não sabe o motivo).

**Why it happens:**
No SQLAlchemy async, o id só existe depois do INSERT de fato ir ao banco; `session.add()` só marca o objeto como pendente. `flush()` precisa ser `await`ado — esquecer o `await` deixa uma coroutine pendurada (warning "coroutine was never awaited") e o id nunca é populado. Além disso, `flush()` despeja TUDO que está pendente na sessão, não só o cabeçalho: se `salvar_ordens_reserva` roda no meio de um fluxo que já tem outros objetos pendentes na mesma sessão, um erro de integridade em OUTRO objeto pendente aparece nesse flush, não no commit final (`casos_uso.py:466-468` já commita logo depois de `salvar_ordens_reserva` + `salvar_processados` — ordem que precisa ser preservada).

**How to avoid:**
Agrupar `resultados` por `cd_prod_cor`, criar/obter o cabeçalho, `await session.flush()`, capturar `cabecalho.id` num dict Python simples (`{cd_prod_cor: header_id}`) imediatamente após o flush, e só então atribuir `or_id` às linhas de `OrdemReserva`. Não depender de reacessar o atributo `.id` do objeto ORM depois de um commit distante no fluxo — capture-o logo após o flush. (`expire_on_commit=False` está configurado em `app/shared/database/session.py:11`, então acessar atributos depois do commit não gera erro de `greenlet`, mas ainda é mais seguro e mais claro capturar o valor explicitamente.)

**Warning signs:**
`IntegrityError` "or_id violates not-null constraint" em teste que deveria passar; warning "coroutine was never awaited" no output do pytest.

**Phase to address:**
Fase de escrita (`salvar_ordens_reserva`).

---

### Pitfall 7: Mudança de forma da tupla (6→7) em `carregar_ordens_reserva`

**What goes wrong:**
`carregar_ordens_reserva` (`repositorio_ordens.py:70-86`) tem exatamente DOIS pontos de desestruturação posicional hoje — `casos_uso.py:192` (dentro de `_montar_pedidos`, usada por `listar_pedidos_abertos`/`obter_historico`/`obter_resumo`) e `casos_uso.py:366` (`listar_ordens_reserva`) — mais um mock de teste com tuplas hardcoded (`test_pedidos_routes.py:372-375`). Adicionar `or_id` à tupla e esquecer de atualizar QUALQUER um dos três pontos quebra a aplicação.

**Why it happens:**
É fácil, ao editar `listar_ordens_reserva`, esquecer que a MESMA função é reaproveitada por `_montar_pedidos` para montar o card de "OR" dentro de `GET /pedidos` — os dois métodos de leitura (por produto e por cliente) compartilham a mesma fonte de dados.

**How to avoid:**
Inserir o novo campo (`or_id`) SEMPRE no final da tupla (nunca no meio/início — evita trocar silenciosamente o significado de posições cujo tipo colide, ex.: `nr_pedido` e `or_id` são ambos `int`) e, no mesmo commit, `grep -rn "carregar_ordens_reserva"` para confirmar os 3 pontos de uso. Melhor ainda, dado que só há 3 chamadores: trocar a tupla posicional por um `NamedTuple` (ex.: `OrdemReservaRow`) — desestruturação por nome elimina esse tipo de bug de vez, com custo de refactor baixo.

**Warning signs:**
`ValueError: not enough values to unpack` (bom — pega na hora) OU, pior, nenhum erro mas `nr_pedido`/`cd_prod_cor` trocados de posição silenciosamente se o novo campo for inserido no meio da tupla.

**Phase to address:**
Fase de leitura.

---

### Pitfall 8: Mutar o `itens` (JSONB) lido do ORM ao carimbar `nrOr`

**What goes wrong:**
Para "carimbar" `nrOr` nos itens de `GET /pedidos`, o caminho mais direto (e arriscado) é `for it in row.itens: it["nrOr"] = or_id` diretamente sobre o objeto retornado pelo ORM. `OrdemReserva.itens` é `Column(JSONB, nullable=False)` puro, sem `MutableDict`/`MutableList` (`models.py:45`) — é o MESMO objeto Python guardado no identity map da sessão. `itens` é reutilizado em vários pontos do mesmo fluxo de leitura (`meta = itens[0]` em `listar_ordens_reserva:369`, `_format_items` em vários pontos de `casos_uso.py`): mutar essa lista em memória sem cópia defensiva arrisca contaminar a mesma referência usada por outro consumidor dentro da mesma requisição/sessão, ou (se algo mais tarde reatribuir `obj.itens = itens_mutados` por outro motivo) gravar de volta um JSONB poluído com `nrOr` dentro dos itens persistidos.

**Why it happens:**
Sem `Mutable` extension configurada, a mutação em si não necessariamente aciona o dirty-tracking do SQLAlchemy — o que cria uma falsa sensação de segurança ("não vai persistir sozinho") mas não protege contra reatribuições explícitas feitas por OUTRO trecho de código que reusa o mesmo objeto.

**How to avoid:**
Sempre copiar (`{**item, "nrOr": or_id}` ou `copy.deepcopy`) ao montar o payload de saída; nunca escrever direto no objeto retornado pela query. Tratar `itens` vindo do ORM como somente leitura em qualquer código de apresentação.

**Warning signs:**
Teste que chama duas rotas de leitura na mesma sessão/teste e vê `nrOr` "vazando" para dentro do `itens` armazenado quando não devia (ou o inverso: `itens` sem `nrOr` apesar do código parecer correto).

**Phase to address:**
Fase de leitura.

---

### Pitfall 9: Contrato aditivo sem rede de segurança de nome de campo (camelCase manual)

**What goes wrong:**
`schemas.py` não usa `alias_generator` — os campos camelCase (`totalQt`, `qtdClientes`, `qtSolicitada`, etc.) são escritos literalmente com `# noqa: N815` (`schemas.py:76-97`). Além disso, `GET /pedidos`, `GET /historico` e `GET /resumo` (`routes.py:24-62`) devolvem `JSONResponse(content=dict)` DIRETO, sem passar por Pydantic — ali não existe NENHUMA rede de segurança de nome de chave. Um typo como `nr_or` em vez de `nrOr` no dict manual não é pego por nenhum teste que só valida o dict Python interno; só quebra no frontend, em produção.

**Why it happens:**
A convenção do projeto é 100% manual (nomear campos em camelCase por convenção de código), sem tooling que garanta consistência entre os dois estilos de resposta: `response_model` Pydantic em `/ordens-reserva` vs. dicts crus em `/pedidos`/`/historico`/`/resumo`.

**How to avoid:**
Escrever teste HTTP explícito que valida a PRESENÇA e o NOME EXATO das novas chaves (`nrOr`, `codigoLinx`, `geradaEm`) no JSON de resposta de AMBOS os estilos de endpoint — replicando o padrão já usado em `test_pedidos_routes.py:119-128` ("shape esperado"), não só checar o retorno da função de serviço em Python. Coordenar o deploy: backend primeiro, frontend com o campo OPCIONAL (`nrOr?: number`) até confirmar em produção que o backend já responde com o campo real; só então endurecer o tipo no frontend e remover o fallback `255315`.

**Warning signs:**
PR de frontend assume campo obrigatório antes do backend estar em produção; testes de shape do backend não cobrem os 3 novos campos.

**Phase to address:**
Fase de leitura (contrato) + fase de frontend.

---

### Pitfall 10: Import duplicado de models esquecido (`env.py` / `test_schema_guard.py`)

**What goes wrong:**
`alembic/env.py` (linhas 22-26) e `app/tests/test_schema_guard.py` (linhas 32-36) importam EXPLICITAMENTE cada model do módulo `pedidos`, em DOIS lugares independentes. Esquecer de adicionar `OrdemReservaCabecalho` a QUALQUER um dos dois faz o autogenerate (`env.py`) não ver a tabela nova, OU faz o schema guard SKIPAR silenciosamente (não falhar) a verificação daquela tabela — o guard existe justamente para pegar drift entre model e banco (nasceu do incidente da migration 012, ver `test_schema_guard.py:1-13`), mas ele PULA (`pytest.skip`) tabelas ausentes em vez de falhar, então uma tabela nova sem migration aplicada passa batido, não é capturada como erro.

**Why it happens:**
Registro manual duplicado, sem um único ponto de verdade — fácil esquecer um dos dois ao adicionar um model novo.

**How to avoid:**
Ao criar o model novo, atualizar os dois arquivos NO MESMO commit; adicionar um item ao checklist de PR/definition-of-done da fase de schema. Lembrar que `test_schema_guard.py` SKIPA (não falha) tabelas totalmente ausentes — não confiar nele para detectar "esqueci de escrever a migration inteira", só para detectar divergência de colunas/PK em tabelas já existentes.

**Warning signs:**
`alembic revision --autogenerate` não detecta a tabela nova (diff vazio) apesar do model existir; suíte de testes "verde" sem nenhum teste do schema guard rodando para a tabela nova (checar por `SKIPPED` no output do pytest).

**Phase to address:**
Fase de schema/migration.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|------------------|
| Reaproveitar cabeçalho antigo do mesmo produto em vez de criar um novo por rodada | Evita a fragmentação do Pitfall 1 sem discussão | Contraria a decisão registrada no PROJECT.md; a leitura precisaria saber decidir quando um header está "ainda aberto" | Só se a decisão de negócio for revista explicitamente com a usuária — nesse caso, mude a decisão documentada, não só o código |
| Deixar `or_id` nullable "por enquanto" e não fazer o backfill retroativo já | Entrega mais rápido | Reintroduz `if or_id is None` espalhado pelo código — exatamente o que a decisão de NOT NULL queria evitar (ver Key Decisions do PROJECT.md) | Nunca como estado permanente; aceitável só como passo transitório DENTRO da mesma migration (nullable → backfill → `SET NOT NULL`) |
| Pular o teste de "duas rodadas tocando o mesmo produto" por falta de tempo | Fecha o milestone mais rápido | Esconde exatamente o Pitfall 1 até aparecer em produção | Nunca — é o teste mais barato de escrever (duas chamadas de `salvar_ordens_reserva`) e o mais caro de descobrir depois do deploy |
| Deixar `codigo_linx` sem índice/constraint de unicidade | Menos DDL agora, formato do Linx é desconhecido | Quando a integração real chegar, duplicidade de código não é pega pelo banco | Aceitável agora (decisão já tomada de escopo mínimo); documentar como dívida explícita para a fase de integração real |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| ERP Linx (barramento ainda inexistente) | Modelar `codigo_linx` já assumindo formato/tamanho específico (ex.: numérico, tamanho fixo, regex de validação) sem confirmação do time de TI | `String(32)` nullable sem validação de formato — decisão já tomada; não "ajudar" apertando o tipo antes de conhecer o formato real |
| ERP Linx (barramento ainda inexistente) | Adicionar colunas de status de envio (pendente/enviado/erro) ou lógica de retry especulativamente "para adiantar" a integração | Resistir — decisão explícita de escopo mínimo registrada no PROJECT.md; nova migration quando o barramento existir de fato |
| Frontend Next.js (deploy/versionamento separado do backend) | Tornar o campo novo obrigatório no tipo TypeScript antes de confirmar que o backend correspondente já está em produção | Campo opcional no tipo (`nrOr?: number`) até confirmar em produção; remover o fallback hardcoded `255315` só depois disso |
| Frontend Next.js (deploy/versionamento separado do backend) | Confiar que camelCase "só funciona" porque funcionou em `/ordens-reserva` (passa por Pydantic) e esquecer que `/pedidos` devolve dict cru sem Pydantic | Testar o JSON de resposta HTTP real (não o retorno da função de serviço) dos DOIS endpoints antes de considerar o contrato pronto |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| `ALTER TABLE ... SET NOT NULL` sem `NOT VALID` + `VALIDATE CONSTRAINT` | Migration trava por segundos/minutos; requests concorrentes dão timeout de conexão | Padrão `ADD CONSTRAINT ... CHECK (...) NOT VALID` → `VALIDATE CONSTRAINT` → `SET NOT NULL` | Tabela com muitas dezenas/centenas de milhares de linhas — hoje `ordens_reserva` é pequena, mas cresce sem limpeza (ver CONCERNS.md, "sem monitoramento/cleanup") |
| Backfill via loop Python linha a linha em vez de `UPDATE ... FROM` em lote | Migration lenta; lock exclusivo mantido por mais tempo do que o necessário | Um único `UPDATE ordens_reserva SET or_id = c.id FROM ordens_reserva_cabecalho c WHERE c.cd_prod_cor = ordens_reserva.cd_prod_cor` em SQL puro | Qualquer volume acima de algumas centenas de linhas |
| Headers órfãos acumulando sem limpeza (se `or_id` for reatribuído a cada rodada, headers antigos do mesmo produto deixam de ter linhas apontando pra eles) | Tabela de cabeçalho cresce mais rápido do que o esperado; sequência "consumida" mais rápido do que a contagem real de ORs vigentes | Aceitável para o volume atual; documentar como dívida explícita se o Pitfall 1 for resolvido criando header novo a cada rodada | Quando o volume de rodadas/dia crescer — reavaliar estratégia de reuso de header |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Expor `codigo_linx` (hoje sempre vazio) em `GET /ordens-reserva` para qualquer usuário autenticado sem necessidade de negócio | Baixo risco hoje (campo vazio), mas revisar quando o campo passar a ter valor real vindo do Linx | Manter o RBAC atual (`require_viewer` já é a política de leitura do projeto); reavaliar exposição/mascaramento de `codigo_linx` quando a integração for real |
| Aceitar `or_id` como parâmetro de filtro externo em algum endpoint futuro sem validar pertencimento/escopo | Permitiria consultar cabeçalho fora do contexto esperado — hoje não há isolamento por canal na tabela de cabeçalho | Manter `or_id` como detalhe interno derivado da consulta por produto/pedido; não aceitá-lo como filtro de entrada não validado em endpoints futuros |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|--------------|-------------------|
| Produto que hoje aparece como 1 card no dashboard passa a fragmentar em vários cards (um por rodada) sem aviso | Usuária de negócio vê "duplicação" aparente do mesmo produto e desconfia do sistema | Comunicar a mudança de comportamento ANTES do deploy; validar com a usuária se rodadas do mesmo produto devem aparecer agrupadas visualmente no frontend, mesmo sendo `or_id` distintos no banco |
| Número da OR (`Identity`) com "buracos" se algum cabeçalho for descartado/rollback de rodada | Usuária estranha numeração "faltando" e pode achar que é bug | Documentar para a usuária que gaps de numeração são normais (mesma explicação já usada hoje para `nr_pedido`) |

## "Looks Done But Isn't" Checklist

- [ ] **Migration 015 (backfill):** roda sem erro não significa "cobriu tudo" — verificar `SELECT count(*) FROM ordens_reserva WHERE or_id IS NULL` (deve ser 0) ANTES de aplicar `SET NOT NULL`
- [ ] **Leitura (`GET /ordens-reserva`):** os 3 pontos de unpacking de `carregar_ordens_reserva` (`casos_uso.py:192`, `casos_uso.py:366`, mock em `test_pedidos_routes.py:372-375`) estão todos atualizados — `grep -rn "carregar_ordens_reserva"` antes de considerar pronto
- [ ] **Escrita (grão "1 cabeçalho por produto por rodada"):** existe teste cobrindo DUAS chamadas SEPARADAS de `salvar_ordens_reserva` para o MESMO `cd_prod_cor` — não só uma chamada com dois pedidos no mesmo lote
- [ ] **Contrato HTTP:** `nrOr`/`codigoLinx`/`geradaEm` aparecem no JSON HTTP real (via `TestClient`, não só no dict interno do service) para os DOIS endpoints (`/pedidos` e `/ordens-reserva`)
- [ ] **Registro de model novo:** `alembic/env.py` e `app/tests/test_schema_guard.py` foram os DOIS atualizados com o import de `OrdemReservaCabecalho`
- [ ] **Frontend:** o hardcode `255315` (`product-grade-detail-modal.tsx:240`) só foi removido DEPOIS de confirmar em produção que o backend já expõe `nrOr` real — não em paralelo com o deploy do backend

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|----------------|------------------|
| Fragmentação indesejada de OR entre rodadas descoberta em produção (Pitfall 1) | MEDIUM | Migration de "merge": reatribuir `or_id` de headers órfãos do mesmo `cd_prod_cor` para o header mais recente ainda aberto, OU aceitar como comportamento correto e ajustar a apresentação no frontend |
| Migration 015 travou em produção durante `SET NOT NULL` (Pitfall 4) | LOW-MEDIUM | Cancelar a migration (Postgres desfaz DDL parcial automaticamente ao abortar a transação); reaplicar com o padrão `NOT VALID` + `VALIDATE` em janela de manutenção |
| `or_id` NULL detectado depois do `SET NOT NULL` ter passado | HIGH (sinal de causa mais profunda) | Não deveria ser possível — `SET NOT NULL` escaneia a tabela inteira no momento do `ALTER`. Se ocorrer, investigar escrita concorrente (API/Celery) rodando durante a janela do deploy fora da transação da migration |
| Tupla 6→7 quebrada em produção por esquecer um dos 3 pontos (Pitfall 7) | LOW | Erro é imediato e ruidoso (`ValueError`) — rollback do deploy e correção pontual; sem risco de corrupção de dados |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|----------------|
| 1. Fragmentação da OR entre rodadas | Fase de escrita (cabeçalho) | Teste com 2 chamadas separadas de `salvar_ordens_reserva` para o mesmo produto; decisão validada com a usuária antes de codar |
| 2. `Identity` + autogenerate frágil | Fase de schema/migration | Rodar `alembic revision --autogenerate` de novo após aplicar a 015 e confirmar diff vazio |
| 3. Migration 015 sem idempotência (repete drift da 012) | Fase de schema/migration | Testar `upgrade` → `downgrade` → `upgrade` em dev antes do merge |
| 4. Lock do `SET NOT NULL` | Fase de schema/migration | Medir tempo de execução da migration num banco com volume real de dev |
| 5. Backfill com ORM "vivo" importado na migration | Fase de schema/migration | Code review: migration não importa `app.modules.*.models` |
| 6. `flush()` antes de vincular `or_id` | Fase de escrita | Teste que insere cabeçalho + linha na mesma chamada e lê `or_id` de volta sem commit |
| 7. Tupla 6→7 em `carregar_ordens_reserva` | Fase de leitura | `grep` confirma os 3 pontos de uso atualizados; preferir migrar para `NamedTuple` |
| 8. Mutação do JSONB `itens` ao carimbar `nrOr` | Fase de leitura | Code review: nenhuma atribuição direta em `row.itens`; sempre copiar antes de enriquecer |
| 9. Contrato aditivo / camelCase manual sem alias | Fase de leitura (contrato) + fase de frontend | Teste HTTP de shape cobre os 3 novos campos nos 2 estilos de endpoint (Pydantic e dict cru) |
| 10. Import duplicado de models esquecido | Fase de schema/migration | Checklist de PR menciona explicitamente `env.py` e `test_schema_guard.py` |

## Sources

- Código-fonte lido diretamente (fonte primária, HIGH confidence):
  - `app/modules/pedidos/models.py`
  - `app/modules/pedidos/infrastructure/repositorio_ordens.py`
  - `app/modules/pedidos/application/casos_uso.py`
  - `app/modules/pedidos/routes.py`
  - `app/modules/pedidos/schemas.py`
  - `alembic/versions/014_or_por_produto.py`
  - `alembic/env.py`
  - `app/shared/database/session.py`
  - `app/tests/test_schema_guard.py`
  - `app/tests/test_pedidos_routes.py`
  - `.planning/PROJECT.md`, `.planning/codebase/CONCERNS.md`, `.planning/codebase/TESTING.md`
- [AttributeError: 'Identity' object has no attribute 'arg' — alembic#775](https://github.com/sqlalchemy/alembic/issues/775)
- [Alembic generate unnecessary alter commands to column for Identity — alembic discussion #1181](https://github.com/sqlalchemy/alembic/discussions/1181)
- [new hook: compare_identity_default — alembic#821](https://github.com/sqlalchemy/alembic/issues/821)
- [SQLAlchemy doesn't detect Identity columns when table name has uppercase letters — sqlalchemy#6129](https://github.com/sqlalchemy/sqlalchemy/issues/6129)
- [PostgreSQL Documentation — Identity Columns](https://www.postgresql.org/docs/current/ddl-identity-columns.html)
- [PostgreSQL Documentation — ALTER TABLE](https://www.postgresql.org/docs/current/sql-altertable.html)
- [Which ALTER TABLE Operations Lock Your PostgreSQL Table? — DEV Community](https://dev.to/mickelsamuel/which-alter-table-operations-lock-your-postgresql-table-1082)
- [When Postgres blocks: 7 tips for dealing with locks — Citus Data](https://www.citusdata.com/blog/2018/02/22/seven-tips-for-dealing-with-postgres-locks/)
- [Locks acquired by ALTER TABLE ADD COLUMN — pglocks.org](https://pglocks.org/?pgcommand=ALTER+TABLE+ADD+COLUMN)

---
*Pitfalls research for: persistência de cabeçalho de OR com numeração sequencial e preparação para integração com ERP Linx (milestone v1.1)*
*Researched: 2026-08-04*
