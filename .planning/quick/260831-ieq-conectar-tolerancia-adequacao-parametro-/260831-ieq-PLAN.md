---
phase: quick/260831-ieq
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - app/modules/pedidos/domain/orcamento_pedido.py
  - app/modules/pedidos/domain/motor_adequacao.py
  - app/tests/test_pedidos_orcamento_pedido.py
  - app/tests/test_pedidos_motor.py
  - app/tests/invariantes_motor_adequacao.py
  - app/tests/test_invariantes_motor_adequacao.py
autonomous: true
requirements: [QUICK-260831-ieq]

must_haves:
  truths:
    - "`OrcamentoPedido` exige `tolerancia` na construção — omitir levanta `TypeError` do próprio Python, igual aos outros campos obrigatórios"
    - "Com `tolerancia=0.05`, `limite_adicao` e `limite_corte` são numericamente IDÊNTICOS a `(total_original * 5) // 100` para todo `total_original` de 0 a 1000 — o default não muda de comportamento"
    - "Com `tolerancia=0.10` e `total_original=100`, o limite é 10 — o parâmetro deixou de ser morto"
    - "O limite continua sendo floor inteiro, nunca ceil nem float: `tolerancia=0.05` com `total_original=19` dá 0 (ALOC-08)"
    - "`tolerancia` fora de [0.0, 1.0] levanta `ValueError` no `__post_init__`, igual à validação dos demais campos"
    - "O `OrcamentoPedido` construído em `_processar_pedidos_canal` recebe a `tolerancia` que a própria função recebeu — nenhum literal `5`/`100` de tolerância sobra em `orcamento_pedido.py`"
    - "`processar_pedidos` sobre um pedido de 100 peças com falta de 8 devolve stand-by com `tolerancia=0.05` e `Gerar OR` (qt_liquida 92) com `tolerancia=0.10` — prova ponta a ponta de que o parâmetro chega ao motor"
    - "A invariante I5 (`assert_orcamento_nao_excedido`) recebe a tolerância real da execução como argumento obrigatório sem default — nenhum call site pode esquecer e cair num `5` fixo em silêncio"
    - "A suíte relevante (orçamento, motor, invariantes, cenário da mantenedora, performance, cancelamento) fecha verde"
  artifacts:
    - path: "app/modules/pedidos/domain/orcamento_pedido.py"
      provides: "Campo `tolerancia` no ledger + limites derivados dele em aritmética exata (floor inteiro), com a invariante ALOC-08 documentada no arquivo"
      contains: "tolerancia"
      min_lines: 130
    - path: "app/modules/pedidos/domain/motor_adequacao.py"
      provides: "Repasse de `tolerancia` para `OrcamentoPedido` em `_processar_pedidos_canal`"
      contains: "tolerancia=tolerancia"
    - path: "app/tests/test_pedidos_orcamento_pedido.py"
      provides: "Prova de paridade numérica com o comportamento antigo em 0.05 + efeito real de outras tolerâncias + validação de faixa"
      min_lines: 280
    - path: "app/tests/invariantes_motor_adequacao.py"
      provides: "Oráculo I5 parametrizado pela tolerância real da execução (independente da implementação de produção)"
      contains: "tolerancia"
  key_links:
    - from: "app/modules/pedidos/domain/motor_adequacao.py::_processar_pedidos_canal"
      to: "app/modules/pedidos/domain/orcamento_pedido.py::OrcamentoPedido"
      via: "kwarg `tolerancia=tolerancia` na construção do ledger"
      pattern: "tolerancia=tolerancia"
    - from: "app/modules/pedidos/processing/infrastructure/adapters.py::load_adequation_config"
      to: "motor_adequacao.processar_pedidos(..., tolerancia=...)"
      via: "parâmetro de negócio `tolerancia_adequacao` já lido do banco — este plano fecha o último trecho do caminho, do orquestrador até o ledger"
      pattern: "tolerancia_adequacao"
---

<objective>
Conectar o parâmetro de negócio `tolerancia_adequacao` ao teto de ±5% do orçamento do pedido,
hoje hardcoded em `OrcamentoPedido.limite_adicao` / `.limite_corte` como `(self.total_original * 5) // 100`.

Purpose: corrigir uma regressão da Fase 14. O `tolerancia` já é lido do banco por
`load_adequation_config` e viaja por `processar_pedidos` → `_processar_pedidos_canal`, mas nunca
é repassado ao ledger — é parâmetro morto na prática. Antes do refator para orçamento por pedido,
`adequar_grade_produto` usava a tolerância de verdade; quando o orçamento virou por-pedido, o
valor virou literal `5`. Mudar `tolerancia_adequacao` no painel hoje não muda absolutamente nada
no motor.

Output: `tolerancia` vira campo obrigatório de `OrcamentoPedido`, os limites passam a derivar dele
com aritmética exata (floor inteiro, ALOC-08), o motor repassa o valor que já recebe, e a
invariante I5 passa a medir contra a tolerância realmente usada na execução.

**Restrição dura:** com `tolerancia=0.05` (default de `settings.py`) o resultado numérico tem que
ser idêntico ao de hoje, para todo `total_original`. Isto é correção de wiring, não mudança de
regra de negócio. Sem abstração nova além de passar o valor adiante.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@app/modules/pedidos/domain/orcamento_pedido.py
@app/modules/pedidos/domain/motor_adequacao.py
@app/tests/test_pedidos_orcamento_pedido.py
@app/tests/invariantes_motor_adequacao.py

**Estado apurado no planejamento (não precisa redescobrir):**

- `OrcamentoPedido` é `@dataclass(slots=True)` com 4 campos de entrada obrigatórios sem default
  (`nr_pedido`, `total_original`, `consumido_previo_adicao`, `consumido_previo_corte`) e 2 campos
  `field(init=False, repr=False)` (`_restante_adicao`, `_restante_corte`). O literal está em duas
  properties, `limite_adicao` (linha ~67) e `limite_corte` (linha ~76), ambas `(self.total_original * 5) // 100`.
- Único ponto de produção que constrói o ledger: `motor_adequacao.py` linha ~850, dentro de
  `_processar_pedidos_canal` (a função começa na linha 600 e é a última do módulo — `tolerancia`,
  parâmetro da própria função na linha ~605, está em escopo no ponto da construção).
- `processar_pedidos` (linha 465) já repassa `tolerancia` para `_processar_pedidos_canal` (linha ~579).
- Construtores de `OrcamentoPedido` nos testes: 11 em `app/tests/test_pedidos_orcamento_pedido.py`
  + 3 dicionários de kwargs no teste de valores negativos, e **1 em `app/tests/test_pedidos_motor.py`
  linha ~98** (este não está no enunciado da tarefa, mas quebra por assinatura do mesmo jeito).
- `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` só
  ANOTAM o tipo `OrcamentoPedido` em funções fake de monkeypatch — não constroem, não quebram.
  O `tolerancia=0.25` em `test_pedidos_motor_performance_024.py` (linhas 73/78) só compara duas
  execuções entre si (`otimizado == referencia`), então é insensível ao valor do teto.
- `assert_orcamento_nao_excedido` (I5) tem 6 call sites: 3 em `test_pedidos_motor.py`
  (linhas ~164, ~200, ~256, todos com kwargs) e 3 em `test_invariantes_motor_adequacao.py`
  (linhas ~244, ~249, ~256, todos posicionais).
- `Decimal` já está importado em `app/tests/invariantes_motor_adequacao.py` (linha 8).

**Fora de escopo (deliberado, não corrigir aqui):**

- A constante de mensagem em `motor_adequacao.py` linha 36 diz literalmente "excede o restante do
  orçamento de 5%". Com tolerância diferente de 0.05 o texto fica impreciso. Mexer nela mudaria o
  que dois testes existentes verificam (`test_pedidos_motor.py` linhas ~156 e ~253) — fica
  registrado no SUMMARY como dívida, não é tarefa deste plano.
- `docs/adequacao.md` linha ~101 descreve o teto como `floor(total_original_do_pedido × 5%)`.
  Mesma razão: registrar no SUMMARY, não editar aqui.
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: `tolerancia` vira campo do ledger e passa a derivar os dois limites</name>
  <files>app/modules/pedidos/domain/orcamento_pedido.py, app/tests/test_pedidos_orcamento_pedido.py</files>
  <behavior>
    Testes a escrever/ajustar em `app/tests/test_pedidos_orcamento_pedido.py` ANTES da implementação:
    - Paridade com o comportamento antigo (o teste decisivo da restrição do usuário): para todo
      `total_original` em `range(0, 1001)`, um ledger com `tolerancia=0.05` tem
      `limite_adicao == limite_corte == (total_original * 5) // 100`.
    - Tolerância viva: `total_original=100` com `tolerancia=0.10` dá limite 10; com `tolerancia=0.0`
      dá 0 (e `restante_adicao == restante_corte == 0`); com `tolerancia=1.0` dá 100.
    - Floor, nunca ceil, agora com o campo: `total_original=19`, `tolerancia=0.05` → limite 0;
      `total_original=99`, `tolerancia=0.10` → 9; e `isinstance(limite, int)` continua valendo.
    - Faixa: `tolerancia=-0.01` e `tolerancia=1.01` levantam `ValueError` na construção.
    - Campo obrigatório: construir sem `tolerancia` levanta `TypeError`.
  </behavior>
  <action>
Em `app/modules/pedidos/domain/orcamento_pedido.py`:

1. Adicionar o campo `tolerancia: float` ao dataclass, **depois** de `consumido_previo_corte` e
   **antes** dos dois `field(init=False, repr=False)`. Sem default — mesma razão já documentada no
   docstring do módulo para os outros 4 campos: um default aqui reintroduziria em silêncio o bug
   que este plano corrige (um chamador futuro esquece de passar e o ledger volta a inventar 5%).
2. No `__post_init__`, acrescentar a validação de faixa logo depois das três checagens de valor
   negativo e ANTES do cálculo de `_restante_adicao`/`_restante_corte`: se
   `not (0.0 <= self.tolerancia <= 1.0)`, levantar `ValueError` com mensagem no mesmo formato das
   demais (nome do campo + `{self.tolerancia!r}`).
3. Substituir o literal nas duas properties. Importar `Decimal` de `decimal` no topo do módulo e
   usar, em `limite_adicao` e em `limite_corte`, a expressão `int(Decimal(str(self.tolerancia)) * self.total_original)`.

   Por que `Decimal(str(...))` e não `int(self.total_original * self.tolerancia)`: `0.05` em binário
   é ligeiramente MAIOR que 0,05, e o arredondamento do produto pode cair um ULP ABAIXO do inteiro
   exato — `floor` devolveria `N-1` e o default mudaria de comportamento em casos isolados, que é
   exatamente o que a restrição do usuário proíbe. `Decimal(str(0.05))` é `Decimal("0.05")` exato,
   e `int()` de um `Decimal` não-negativo trunca para baixo, ou seja, é o mesmo floor de hoje. Para
   `tolerancia=0.05` a expressão é matematicamente idêntica a `(total_original * 5) // 100` (mesma
   racional exata, mesmo floor) — o teste de paridade em `range(0, 1001)` é a prova.
4. Atualizar a documentação da invariante no próprio arquivo (ALOC-08), sem inventar regra nova:
   - No docstring do MÓDULO, trocar "cada um `floor(total * 5%)`" por "cada um
     `floor(total_original × tolerancia)`", mantendo o resto da explicação (aritmética inteira,
     nunca `float`/`ceil`, os dois orçamentos não se compensam) e registrando que `tolerancia` é o
     parâmetro de negócio `tolerancia_adequacao`, obrigatório e sem default pelo mesmo motivo dos
     `consumido_previo_*`.
   - Nos docstrings de `limite_adicao` e `limite_corte`, trocar "floor(total_original * 5%)" por
     "floor(total_original × tolerancia)" e registrar que o resultado é sempre `int` por floor
     (nunca ceil, nunca float) — ALOC-08.
   Cuidar para que o texto não deixe nenhuma frase afirmando que o teto é 5% fixo.

Em `app/tests/test_pedidos_orcamento_pedido.py`:

5. Acrescentar `tolerancia=0.05` em cada uma das 11 construções de `OrcamentoPedido` (linhas ~12,
   25, 36, 52, 70, 94, 114, 138, 161, 178, 194) — 0.05 é o valor que todos esses testes já assumem
   implicitamente hoje. **Não alterar nenhuma asserção existente.**
6. No `test_orcamento_pedido_construtor_recusa_valores_negativos`, acrescentar
   `"tolerancia": 0.05` aos 3 dicionários de kwargs (senão o construtor levanta `TypeError` e o
   teste, que espera `ValueError`, quebra pelo motivo errado).
7. Renomear `test_orcamento_pedido_construtor_exige_os_4_campos_obrigatorios` para
   `..._exige_os_5_campos_obrigatorios` e ajustar só o texto do `raise AssertionError` — a
   asserção (construir com 2 campos levanta `TypeError`) permanece a mesma.
8. A linha ~205 (`limite = (total_original * 5) // 100`, oráculo local do teste de acumulado entre
   execuções) fica como está: aquele teste roda em 0.05, onde a conta continua valendo.
9. Adicionar os testes novos do bloco `<behavior>`: paridade em `range(0, 1001)`, tolerância viva
   (0.0 / 0.10 / 1.0), floor com tolerância diferente de 0.05, e um
   `test_orcamento_pedido_construtor_recusa_tolerancia_fora_da_faixa` cobrindo `-0.01` e `1.01`
   (seguir o estilo `try/except ValueError/else: raise AssertionError` já usado no arquivo, não
   introduzir `pytest.raises` num arquivo que não importa `pytest`).
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests/test_pedidos_orcamento_pedido.py -q</automated> <!-- pragma: allowlist secret -->
    <automated>docker compose -f .docker/docker-compose.yml exec api sh -c "grep -v '^\s*#' app/modules/pedidos/domain/orcamento_pedido.py | grep -c 'total_original \* 5) // 100'" retorna 0</automated>
  </verify>
  <done>`OrcamentoPedido` tem `tolerancia` obrigatório e validado em [0.0, 1.0]; os dois limites derivam dele por floor inteiro exato; o teste de paridade prova igualdade com `(total * 5) // 100` em 0 a 1000 para `tolerancia=0.05`; nenhuma asserção pré-existente do arquivo foi alterada; `test_pedidos_orcamento_pedido.py` passa inteiro.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: motor repassa a tolerância e a invariante I5 passa a medir contra ela</name>
  <files>app/modules/pedidos/domain/motor_adequacao.py, app/tests/test_pedidos_motor.py, app/tests/invariantes_motor_adequacao.py, app/tests/test_invariantes_motor_adequacao.py</files>
  <behavior>
    Teste ponta a ponta novo em `app/tests/test_pedidos_motor.py` (RED antes do wiring — hoje ele
    falha, porque a tolerância é ignorada e o teto é sempre 5):
    - Mesmo cenário, duas tolerâncias. Pedido 1, produto "A", tamanho "M", `qt` 100, canal Franquia
      (reusar o helper `_item` do arquivo, no mesmo formato de
      `test_processar_pedidos_orcamento_acumulado_execucoes_nao_reseta`), estoque
      `{"Franquia": {"A_M": 92}}` — falta de 8, produto de tamanho único, então sem furo de grade.
    - Com `tolerancia=0.05`: teto 5, falta 8 não cabe (tudo-ou-nada) → o item sai como
      `"Pedido em Stand By"` com `qt_liquida == 100`.
    - Com `tolerancia=0.10`: teto 10, falta 8 cabe → o item sai como `"Gerar OR"` com
      `qt_liquida == 92`.
    - Fechar com `assert_orcamento_nao_excedido(total_original=100, adicionado_execucoes=[0], cortado_execucoes=[8], tolerancia=0.10)`.

    Teste novo em `app/tests/test_invariantes_motor_adequacao.py`:
    - O oráculo I5 segue a tolerância recebida: `assert_orcamento_nao_excedido(100, [8], [0], tolerancia=0.10)`
      passa (limite 10) e a MESMA chamada com `tolerancia=0.05` levanta `AssertionError` (limite 5).
  </behavior>
  <action>
1. `app/modules/pedidos/domain/motor_adequacao.py`, construção do ledger em
   `_processar_pedidos_canal` (linha ~850): acrescentar o kwarg `tolerancia=tolerancia` à chamada
   de `OrcamentoPedido(...)`, junto de `nr_pedido`, `total_original`, `consumido_previo_adicao` e
   `consumido_previo_corte`. É a única mudança de produção desta task — nada de novo helper, nada
   de novo parâmetro em nenhuma assinatura de `motor_adequacao.py`: `tolerancia` já é parâmetro de
   `_processar_pedidos_canal` e já chega de `processar_pedidos`.
2. `app/tests/test_pedidos_motor.py` linha ~98
   (`test_adequar_grade_produto_estoque_suficiente_sem_ajuste`): acrescentar `tolerancia=0.05` à
   construção do ledger. Sem tocar nas asserções.
3. `app/tests/invariantes_motor_adequacao.py`, `assert_orcamento_nao_excedido`: acrescentar um
   parâmetro **keyword-only e obrigatório** `tolerancia: float` (assinatura
   `(total_original, adicionado_execucoes, cortado_execucoes, *, tolerancia)`), e trocar
   `limite = (total_original * 5) // 100` por `limite = int(Decimal(str(tolerancia)) * total_original)`
   (`Decimal` já está importado no arquivo). Sem default de propósito: um default `0.05` faria a
   invariante voltar a mentir em silêncio no dia em que algum cenário rodar com outra tolerância —
   que é o defeito que este plano está corrigindo. O oráculo continua INDEPENDENTE: não importar
   nada de `orcamento_pedido.py` nem de `motor_adequacao.py` para calcular o limite. Atualizar o
   docstring de I5 (hoje diz `floor(total_original * 0,05)`) para `floor(total_original × tolerancia)`,
   mantendo a referência a ALOC-07/08/09, e citar a tolerância na mensagem de `AssertionError` no
   lugar do "5% de {total_original}" fixo.
4. Atualizar os 6 call sites: os 3 de `app/tests/test_pedidos_motor.py` (linhas ~164, ~200, ~256)
   ganham `tolerancia=0.05` — é a tolerância que aquelas execuções de fato usam; e os 3 de
   `app/tests/test_invariantes_motor_adequacao.py` (linhas ~244, ~249, ~256), que hoje chamam
   posicionalmente, ganham `tolerancia=0.05` sem mudar os valores que já passam nem o que asseguram.
5. Adicionar os dois testes novos descritos no `<behavior>`.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests/test_pedidos_motor.py app/tests/test_invariantes_motor_adequacao.py -q</automated> <!-- pragma: allowlist secret -->
    <automated>docker compose -f .docker/docker-compose.yml exec api grep -c "tolerancia=tolerancia" app/modules/pedidos/domain/motor_adequacao.py retorna 1</automated>
  </verify>
  <done>O ledger construído pelo motor usa a tolerância recebida; o teste de duas tolerâncias prova a diferença observável (stand-by em 0.05, OR em 0.10) e falhava antes do wiring; I5 exige a tolerância explicitamente e nenhum call site depende mais de um `5` fixo; os dois arquivos de teste passam inteiros.</done>
</task>

<task type="auto">
  <name>Task 3: gate de suíte — nenhuma regressão no default 0.05</name>
  <files>(nenhum arquivo novo — gate de verificação)</files>
  <action>
Rodar, dentro do container `api` já em execução (worktree é proibido neste repo: quebra os testes
em Docker), primeiro a suíte relevante e depois a suíte completa:

1. Relevante: `app/tests/test_pedidos_orcamento_pedido.py`, `app/tests/test_pedidos_motor.py`,
   `app/tests/test_invariantes_motor_adequacao.py`, `app/tests/test_cenario_disputa_mantenedora.py`,
   `app/tests/test_pedidos_motor_performance_024.py`,
   `app/tests/test_pedidos_processing_cancellation_024.py`.
2. Suíte completa `app/tests -q`, comparando com o baseline registrado no STATE.md
   (881 passed / 18 skipped / 0 failed na última quick task). Qualquer falha nova precisa ser
   atribuída a este trabalho ou provada pré-existente com `git stash` antes de seguir.

Se aparecer falha por assinatura em algum arquivo não previsto (`TypeError: __init__() missing 1
required positional argument: 'tolerancia'`), acrescentar `tolerancia=0.05` naquela construção — o
levantamento do planejamento achou 12 construtores em 2 arquivos de teste, mas o gate é a suíte,
não a lista.

Commits: Conventional Commits com escopo (`fix(pedidos): ...` para o wiring). **Nunca** incluir
`Co-Authored-By` nem qualquer menção a Claude nas mensagens de commit deste repositório.
  </action>
  <verify>
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests/test_pedidos_orcamento_pedido.py app/tests/test_pedidos_motor.py app/tests/test_invariantes_motor_adequacao.py app/tests/test_cenario_disputa_mantenedora.py app/tests/test_pedidos_motor_performance_024.py app/tests/test_pedidos_processing_cancellation_024.py -q</automated> <!-- pragma: allowlist secret -->
    <automated>docker compose -f .docker/docker-compose.yml exec -e TEST_DATABASE_URL=postgresql+asyncpg://automation:automation@dev_db:5432/system_automation_test -e TEST_REDIS_URL=redis://redis:6379/15 api uv run pytest app/tests -q</automated> <!-- pragma: allowlist secret -->
  </verify>
  <done>Suíte relevante 100% verde; suíte completa sem falha nova em relação ao baseline do STATE.md (falha remanescente, se houver, provada pré-existente).</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| painel de parâmetros (admin) → banco → motor de alocação | `tolerancia_adequacao` é escrito por usuário privilegiado e lido por `load_adequation_config` (`cast=float`), atravessando `processar_pedidos` até o ledger. Depois desta mudança o valor passa a mover peça de verdade — antes era inerte. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-ieq-01 | Tampering | `OrcamentoPedido.__post_init__` | mitigate | Validar `0.0 <= tolerancia <= 1.0` no construtor (Task 1). Defesa em profundidade sobre `validar_valor_de_parametro` (registro de `parametros`, faixa 0..1) e sobre o `cast=float` de `load_adequation_config`: um valor absurdo (ex. 5.0 = 500% de corte) passa a falhar alto no domínio em vez de esvaziar pedidos silenciosamente. |
| T-ieq-02 | Tampering | `limite_adicao` / `limite_corte` | mitigate | Aritmética exata via `Decimal(str(tolerancia))` + floor inteiro, com teste de paridade em `range(0, 1001)` (Task 1). Impede que erro de ponto flutuante altere o teto do default 0.05 — teto errado para mais é peça reservada a mais que não existe no estoque. |
| T-ieq-03 | Repudiation | mudança de `tolerancia_adequacao` | accept | O rastro de quem mudou o parâmetro é responsabilidade do fluxo de change requests do módulo `parametros` (Fase 11, em aberto), não deste plano. |
| T-ieq-SC | Tampering | supply chain | n/a | Nenhum pacote novo: zero comandos `uv add`/`pip install`/`npm install` neste plano. Só `decimal` da stdlib. Gate de legitimidade de pacote não se aplica. |
</threat_model>

<verification>
1. `tolerancia=0.05` produz o mesmo número de hoje: teste de paridade contra `(total_original * 5) // 100` em `range(0, 1001)` (Task 1) — é a prova formal da restrição "não mudar o comportamento default".
2. O parâmetro deixou de ser morto: mesmo cenário, `tolerancia=0.05` dá stand-by e `tolerancia=0.10` dá OR (Task 2).
3. Nenhuma abstração nova: as mudanças de produção são um campo de dataclass, uma validação, duas expressões de property e um kwarg na construção do ledger. Nenhuma assinatura de função de `motor_adequacao.py` muda.
4. Suíte completa sem regressão contra o baseline do STATE.md (Task 3).
</verification>

<success_criteria>
- [ ] `OrcamentoPedido` tem `tolerancia: float` obrigatório, validado em [0.0, 1.0], e os dois limites derivam dele por floor inteiro exato
- [ ] Nenhum literal de tolerância (`* 5) // 100`) resta em `orcamento_pedido.py`; docstring do módulo e das duas properties descrevem a invariante ALOC-08 em termos de `tolerancia`
- [ ] `_processar_pedidos_canal` repassa `tolerancia=tolerancia` ao ledger
- [ ] `assert_orcamento_nao_excedido` exige a tolerância da execução (keyword-only, sem default) e calcula o limite a partir dela, sem importar código de produção
- [ ] Teste de paridade em `range(0, 1001)` verde para `tolerancia=0.05`
- [ ] Teste ponta a ponta de duas tolerâncias (0.05 → stand-by, 0.10 → OR) verde
- [ ] Suíte relevante verde e suíte completa sem falha nova
- [ ] Commits em Conventional Commits, sem `Co-Authored-By` nem menção a Claude
</success_criteria>

<output>
Create `.planning/quick/260831-ieq-conectar-tolerancia-adequacao-parametro-/260831-ieq-SUMMARY.md` when done.

Registrar no SUMMARY as duas dívidas deixadas de fora de propósito: a mensagem de stand-by
hardcoded em "5%" (`motor_adequacao.py` linha ~36) e a descrição do teto em `docs/adequacao.md`
(linha ~101), ambas imprecisas quando `tolerancia_adequacao != 0.05`.
</output>
