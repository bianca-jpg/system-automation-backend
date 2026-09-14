---
phase: quick/260831-jhj
plan: 01
type: execute
wave: 1
depends_on: [quick/260831-ieq]
files_modified:
  - app/modules/pedidos/domain/motor_adequacao.py
  - app/tests/test_pedidos_motor.py
  - docs/adequacao.md
autonomous: true
requirements: [QUICK-260831-jhj]

must_haves:
  truths:
    - "Com `tolerancia_adequacao=0.05` (default), a mensagem de stand-by por orçamento de corte continua byte-idêntica à de hoje: `Orçamento do pedido: corte necessário excede o restante do orçamento de 5%`"
    - "Com `tolerancia_adequacao=0.10`, a MESMA rota de stand-by produz `...do orçamento de 10%` — nunca `1E+1%`, nunca `10.0%`, nunca `5%`"
    - "Nenhum literal `de 5%` sobra em `motor_adequacao.py`: o percentual da mensagem é derivado de `ledger.tolerancia` no ponto de uso"
    - "A assinatura de `adequar_grade_produto` não muda — a tolerância vem do `ledger` que ela já recebe, sem parâmetro novo"
    - "Os demais motivos de stand-by (crédito, canal não identificado, `SEM_ESTOQUE`, `FURO_GRADE`, `BLACKLIST`) ficam exatamente como estão"
    - "`docs/adequacao.md` descreve o orçamento em função de `tolerancia_adequacao`, com 5% identificado explicitamente como default e não como valor fixo"
    - "A suíte completa fecha sem regressão contra o baseline 887 passed / 18 skipped / 0 failed"
  artifacts:
    - path: "app/modules/pedidos/domain/motor_adequacao.py"
      provides: "Mensagem de stand-by por orçamento de corte formatada com a tolerância real do ledger"
      contains: "_motivo_orcamento_corte"
      min_lines: 990
    - path: "app/tests/test_pedidos_motor.py"
      provides: "Prova de que a mensagem muda com a tolerância (0.05 → '5%', 0.10 → '10%'), com literais explícitos como oráculo independente"
      min_lines: 830
    - path: "docs/adequacao.md"
      provides: "Descrição do orçamento parametrizada por `tolerancia_adequacao` (default 5%)"
      contains: "tolerancia_adequacao"
      min_lines: 178
  key_links:
    - from: "app/modules/pedidos/domain/motor_adequacao.py::adequar_grade_produto"
      to: "app/modules/pedidos/domain/orcamento_pedido.py::OrcamentoPedido.tolerancia"
      via: "chamada `_motivo_orcamento_corte(ledger.tolerancia)` no ramo `not concedido`"
      pattern: "_motivo_orcamento_corte\\(ledger\\.tolerancia\\)"
---

<objective>
Eliminar a dívida deixada de propósito pela quick task 260831-ieq: os dois lugares que ainda dizem
"5%" como texto fixo, e por isso mentem quando `tolerancia_adequacao != 0.05`.

Purpose: a 260831-ieq conectou o parâmetro de negócio ao motor (o ledger `OrcamentoPedido` ganhou
o campo obrigatório `tolerancia` e `_processar_pedidos_canal` passou a repassar o valor real).
O comportamento ficou correto, mas a **explicação** dele não: a mensagem de stand-by e a
documentação continuam afirmando "5%" mesmo quando o teto configurado é outro. Um analista com
tolerância 10% vê um pedido em stand-by com a justificativa "excede o restante do orçamento de 5%"
— texto falso sobre uma decisão que o motor tomou corretamente.

Output: `_MOTIVO_ORCAMENTO_CORTE` deixa de ser constante de módulo e vira uma função que formata o
percentual a partir de `ledger.tolerancia`; `docs/adequacao.md:101` passa a descrever o orçamento
em função de `tolerancia_adequacao`, com 5% como default declarado.

**Restrição dura (do usuário): sem over-engineering.** É só tornar essa mensagem e essa linha de
doc precisas. NÃO redesenhar o sistema de motivos de stand-by, NÃO criar catálogo/enum/i18n de
mensagens, NÃO tocar nos outros motivos (`SEM_ESTOQUE`, `FURO_GRADE`, `BLACKLIST`, crédito, canal),
que continuam corretos como estão. Com `tolerancia=0.05` a saída tem que ser byte-idêntica à de
hoje — as 3 asserções existentes no teste são o gate disso.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@app/modules/pedidos/domain/motor_adequacao.py
@app/modules/pedidos/domain/orcamento_pedido.py
@app/tests/test_pedidos_motor.py
@docs/adequacao.md
</context>

<constraints>
- **Worktree é proibido neste repositório** — quebra os testes em Docker. Trabalhar direto na
  branch, e rodar os testes dentro do container `api` já em execução.
- **Commits sem coautoria/menção a Claude** (regra permanente deste projeto). Conventional Commits
  com escopo: `fix(pedidos):` para produção, `test(pedidos):` para o teste novo,
  `docs(adequacao):` para a doc.
- Branch de trabalho e push: `develop`, nunca `main`.
</constraints>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: mensagem de stand-by por orçamento de corte formatada com a tolerância real</name>
  <files>app/modules/pedidos/domain/motor_adequacao.py, app/tests/test_pedidos_motor.py</files>
  <behavior>
    - Tolerância 0.05 (default): mensagem termina em `do orçamento de 5%` — string byte-idêntica
      à de hoje, que as 3 asserções já existentes (linhas ~157, ~260, ~535) continuam provando
      SEM edição nenhuma.
    - Tolerância 0.10: mensagem termina em `do orçamento de 10%` (teste novo).
    - Nenhuma casa decimal espúria (`5.000000000000001%`), nenhum zero à direita (`5.00%`,
      `10.0%`) e nenhuma notação científica (`1E+1%`) em qualquer tolerância válida.
  </behavior>
  <action>
Remover a constante de módulo `_MOTIVO_ORCAMENTO_CORTE` (linhas 35-37) e, no mesmo ponto do
arquivo, definir em seu lugar a função privada `_motivo_orcamento_corte(tolerancia: float) -> str`.
Deixar `_MOTIVO_SEM_CANAL` e `_CENT` intocados.

A função devolve a MESMA frase de hoje, com o percentual interpolado no lugar do `5` fixo:
`Orçamento do pedido: corte necessário excede o restante do orçamento de {percentual}%`. Nada
mais na frase muda — nem pontuação, nem acentuação, nem espaçamento.

O percentual é derivado assim, e a ordem das três operações importa:

1. `Decimal(str(tolerancia)) * 100` — o `Decimal(str(...))` é exatamente o padrão que
   `OrcamentoPedido.limite_adicao` / `.limite_corte` já usam neste mesmo domínio (não inventar
   padrão novo). É o que impede o artefato de float `0.1 * 100 == 10.000000000000002`.
2. `.normalize()` no resultado — remove os zeros à direita que a multiplicação introduz
   (`Decimal("0.05") * 100` dá `Decimal("5.00")`, que viraria "5.00%").
3. Formatar com o format spec de ponto fixo `f` (ou seja, uma f-string com `:f` aplicado ao
   `Decimal` já normalizado). **Este passo não é opcional:** `.normalize()` devolve notação
   científica para valores redondos — `Decimal("0.1") * 100` normalizado é `Decimal("1E+1")`,
   cujo `str()` vazaria "1E+1%" na mensagem. O spec `f` força "10". Mesma coisa em
   `tolerancia=1.0` (`1E+2` → "100").

Resultado esperado da formatação: 0.05 → "5", 0.10 → "10", 0.075 → "7.5", 0.0 → "0", 1.0 → "100".

Trocar o ponto de uso em `adequar_grade_produto` (linha ~188, dentro do ramo `if not concedido`)
de `motivo=_MOTIVO_ORCAMENTO_CORTE` para `motivo=_motivo_orcamento_corte(ledger.tolerancia)`.
A assinatura de `adequar_grade_produto` NÃO muda: `ledger: OrcamentoPedido` já é parâmetro dela e
já carrega `.tolerancia` desde a quick task 260831-ieq. Não adicionar parâmetro novo, não propagar
tolerância por mais nenhum call site, não mexer nas outras 4 chamadas a `marcar_stand_by`
(linhas ~330, ~552, ~761, ~802, ~880) — elas usam motivos que não falam de percentual.

Confirmado no levantamento: `_MOTIVO_ORCAMENTO_CORTE` não é importado por nenhum outro módulo
(`service.py` reexporta `marcar_stand_by`, não o motivo), então não sobra shim nem `__all__` a
atualizar.

Nos testes (`app/tests/test_pedidos_motor.py`):

- **Não editar** as 3 asserções existentes de literal (linhas ~157, ~260, ~535). Elas rodam com
  `tolerancia=0.05` e o texto esperado continua "…de 5%" — mantê-las como literal explícito é o
  oráculo independente que prova ausência de regressão. Se algum comentário ao redor descrever a
  fonte como "constante", ajustar só o comentário.
- **Nunca importar `_motivo_orcamento_corte` no teste** para montar o esperado: comparar a saída
  contra a própria função seria tautologia e não provaria formatação nenhuma. Os esperados são
  sempre literais escritos à mão.
- Adicionar UM teste novo, logo depois de
  `test_processar_pedidos_tolerancia_recebida_muda_o_teto_observavel` (termina na linha ~298),
  chamado `test_processar_pedidos_motivo_de_stand_by_reflete_a_tolerancia_configurada`. Cenário:
  um único item via `_item("M", 100, 10000.0, nr_pedido=1, cd_prod_cor="A", canal="Franquia")`,
  estoque `{"Franquia": {"A_M": 85}}` — falta de 15, acima tanto do teto de 5 (0.05) quanto do de
  10 (0.10), então os DOIS lados caem em stand-by e a única diferença observável é o texto.
  Produto de tamanho único não dispara furo de grade (`tem_furo_de_grade` devolve `(False, None)`
  com menos de 2 tamanhos reconhecidos), então o stand-by é comprovadamente o do orçamento.
  Rodar `processar_pedidos(dados, estoque, ja_processados=set(), criterio="valor", tolerancia=X)`
  duas vezes, com X = 0.05 e X = 0.10, e afirmar em cada uma: `status_item == "Pedido em Stand By"`,
  `qt_liquida == 100` (preservada) e `motivo_stand_by` igual ao literal correspondente, terminando
  em "de 5%" e "de 10%". Docstring curta explicando que o teste existe para travar a formatação
  (o caso 0.10 é justamente o que pegaria o `1E+1%`).
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests/test_pedidos_motor.py -q</automated> <!-- pragma: allowlist secret -->
    <automated>docker compose -f .docker/docker-compose.yml exec api sh -c "grep -v '^[[:space:]]*#' app/modules/pedidos/domain/motor_adequacao.py | grep -c 'de 5%' || true" retorna 0</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec api sh -c "grep -c '_motivo_orcamento_corte(ledger.tolerancia)' app/modules/pedidos/domain/motor_adequacao.py || true" retorna 1</automated>
  </verify>
  <done>`app/tests/test_pedidos_motor.py` 100% verde, incluindo as 3 asserções de literal "…de 5%" sem edição e o teste novo provando "…de 10%"; nenhum literal `de 5%` restante em `motor_adequacao.py` fora de comentário; ponto de uso lendo `ledger.tolerancia`.</done>
</task>

<task type="auto">
  <name>Task 2: docs/adequacao.md deixa de afirmar 5% como valor fixo</name>
  <files>docs/adequacao.md</files>
  <action>
Reescrever a linha 101 (dentro do parágrafo "Alocação por pedido — ledger e duas passadas") que
hoje diz `cada um `floor(total_original_do_pedido × 5%)` em aritmética inteira, que`. O texto novo
deve descrever o teto em função do parâmetro de negócio e identificar 5% como default, algo como:
`cada um `floor(total_original_do_pedido × tolerancia_adequacao)` em aritmética inteira
(tolerância configurável pelo parâmetro de negócio `tolerancia_adequacao`, default 5%), que` —
preservando a continuidade da frase, que segue na linha 102 com "**não se compensam** entre si".

Não reescrever o restante da seção, não mexer na linha 42 (que já cita `tolerancia_adequacao`
corretamente como parâmetro vindo do banco) e não abrir seção nova. O levantamento confirmou que
a linha 101 é a única ocorrência de "5%" no arquivo — depois da edição, a única menção a 5% deve
ser a que o marca como default.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec api sh -c "grep -c 'tolerancia_adequacao' docs/adequacao.md || true" retorna 2 ou mais</automated>
    <automated>docker compose -f .docker/docker-compose.yml exec api sh -c "grep -c 'default 5%' docs/adequacao.md || true" retorna 1</automated>
  </verify>
  <done>A linha descreve `floor(total_original_do_pedido × tolerancia_adequacao)` com 5% rotulado como default; frase continua gramaticalmente com a linha seguinte; nenhuma outra parte do documento alterada.</done>
</task>

<task type="auto">
  <name>Task 3: gate de suíte completa contra o baseline 887/18/0</name>
  <files>(nenhum arquivo de código — task de verificação; só o SUMMARY do plano)</files>
  <action>
Rodar, dentro do container `api` já em execução (worktree é proibido neste repo — quebra os testes
em Docker), primeiro a suíte relevante do motor e depois a suíte completa.

Comparar com o baseline registrado no STATE.md pela quick task 260831-ieq: **887 passed / 18
skipped / 0 failed**. O esperado aqui é **888 passed / 18 skipped / 0 failed** (baseline + 1 teste
novo desta task). Qualquer `failed` é regressão: este plano não muda comportamento nenhum em
`tolerancia=0.05`, então não existe falha "esperada" a tolerar.

Registrar os números finais no SUMMARY (passed/skipped/failed antes e depois) e a nota de que a
dívida declarada pela 260831-ieq (mensagem "5%" hardcoded + `docs/adequacao.md:101`) está quitada,
para o STATE.md parar de carregá-la como débito conhecido.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_orcamento_pedido.py app/tests/test_invariantes_motor_adequacao.py -q</automated> <!-- pragma: allowlist secret -->
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests -q</automated> <!-- pragma: allowlist secret -->
  </verify>
  <done>Suíte relevante 100% verde; suíte completa em 888 passed / 18 skipped / 0 failed (ou, no mínimo, 0 failed e nenhuma falha nova em relação ao baseline), com os números anotados no SUMMARY.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| motor de adequação → `motivo_stand_by` exibido ao analista | Texto gerado no domínio puro e propagado até a UI; o único dado interpolado é `tolerancia`, um `float` já validado em `[0.0, 1.0]` por `OrcamentoPedido.__post_init__` |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-jhj-01 | Information disclosure | `_motivo_orcamento_corte` | accept | A mensagem expõe apenas o percentual de tolerância, que já é um parâmetro de negócio visível no painel de parâmetros — nenhum dado novo cruza a fronteira |
| T-jhj-02 | Tampering | valor de `tolerancia` interpolado na string | mitigate | Faixa já validada em `OrcamentoPedido.__post_init__` (`ValueError` fora de `[0.0, 1.0]`); a formatação com `Decimal` + spec `f` garante saída numérica determinística, sem notação científica |
| T-jhj-SC | Tampering | npm/pip/cargo installs | mitigate | Não aplicável — nenhuma dependência nova é instalada por este plano (só `decimal`, da stdlib, já importado no arquivo) |
</threat_model>

<verification>
- Com tolerância default (0.05), as 3 asserções pré-existentes de literal continuam passando SEM
  terem sido editadas — prova direta de ausência de regressão de texto.
- Com tolerância 0.10, o mesmo cenário devolve "…do orçamento de 10%".
- `grep` não encontra `de 5%` em código não-comentado de `motor_adequacao.py`.
- `docs/adequacao.md` cita `tolerancia_adequacao` na descrição do orçamento e marca 5% como default.
- Suíte completa sem falha nova em relação ao baseline 887/18/0.
</verification>

<success_criteria>
- [ ] `_MOTIVO_ORCAMENTO_CORTE` não existe mais; `_motivo_orcamento_corte(tolerancia)` formata a
      frase com o percentual real
- [ ] Ponto de uso em `adequar_grade_produto` passa `ledger.tolerancia`, sem parâmetro novo na
      assinatura
- [ ] Formatação sem zeros à direita, sem casas espúrias de float e sem notação científica
      (0.05→"5", 0.10→"10", 0.075→"7.5", 1.0→"100")
- [ ] 1 teste novo prova a mudança de texto entre 0.05 e 0.10, com literais escritos à mão
- [ ] As 3 asserções existentes seguem intactas e verdes
- [ ] Nenhum outro motivo de stand-by tocado
- [ ] `docs/adequacao.md:101` parametrizado por `tolerancia_adequacao` com 5% como default
- [ ] Suíte completa sem regressão contra 887 passed / 18 skipped / 0 failed
</success_criteria>

<output>
Create `.planning/quick/260831-jhj-eliminar-a-divida-deixada-de-proposito-n/260831-jhj-SUMMARY.md` when done
</output>
