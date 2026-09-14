# Deferred Items — Fase 14

Itens descobertos durante a execução de planos desta fase que estão **fora do
escopo** do arquivo/plano em questão. Não corrigidos aqui — só registrados.

## 14-05 — Poluição de dados de teste em `parametros` (dev_db persistente)

**Descoberto durante:** verificação de suíte completa do plano 14-05.

**Sintoma:** `app/tests/test_parametros.py::test_listar_parametros_contem_criado`
falha intermitentemente na suíte completa (`assert any(p["chave"] == chave for
p in body["rows"])` → `False`), mas **passa 100% quando o arquivo é executado
isoladamente** (`pytest app/tests/test_parametros.py -q` → 24 passed).

**Causa raiz (não é regressão deste plano):** `test_parametros.py` cria linhas
reais na tabela `parametros` do Postgres de dev (`dev_db`, container docker
local) via `_chave_unica()` (chaves com sufixo `uuid4().hex[:8]`), sem
truncar/roll-back entre execuções da suíte. Como a listagem usada pelo teste
pede `page=1`/`pageSize=25`, quantas mais chaves de teste acumularem na tabela
ao longo de execuções repetidas da suíte completa neste `dev_db` persistente,
maior a chance de a chave recém-criada não caber na primeira página — o teste
depende implicitamente de "poucas linhas pré-existentes", premissa que se
rompe com o uso contínuo do ambiente.

**Evidência de que não é causado pelo 14-05:**
- Nenhum arquivo tocado pelos 3 commits deste plano pertence a
  `app/modules/parametros/**` nem a `app/shared/database/**`.
- As três tasks deste plano tocam só `app/modules/pedidos/domain/**` e testes
  puros (sem I/O, sem banco) — `politica_quantidade.py`, `motor_adequacao.py`
  (função nova + parâmetro `modo`), e os arquivos de teste correspondentes.
- Rodar `test_parametros.py` isolado (sem o resto da suíte já ter poluído a
  tabela nesta sessão) passa 100%.

**Impacto no contrato de "verde" desta fase:** a suíte completa, ao final da
Task 3 do 14-05 (antes de qualquer execução extra de verificação), rodou
**811 passed, 16 skipped, 1 failed** — exatamente a falha conhecida e
pré-existente (`test_read_projection_empty_contracts_execute_real_sql`),
sem regressão. A segunda falha (`test_listar_parametros_contem_criado`)
só apareceu em reexecuções subsequentes da suíte completa feitas *depois*
daquele ponto, como efeito colateral de reexecutar o próprio
`test_parametros.py` várias vezes na mesma sessão contra o `dev_db`
persistente (cada execução insere mais linhas de teste).

**Ação recomendada (fora do escopo deste plano):** isolar `test_parametros.py`
com uma transação por teste (rollback automático) ou truncar as linhas
criadas por `_chave_unica()` num fixture de teardown, para a suíte parar de
depender do estado acumulado do `dev_db` entre execuções.

**Não corrigido aqui:** alterar o isolamento de `test_parametros.py` é
arquitetura de teste de um módulo fora do escopo do 14-05 (Rule 4 — mudança
estrutural, não um bug introduzido por este plano).
