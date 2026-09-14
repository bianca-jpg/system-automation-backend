# Itens fora de escopo descobertos durante a execução do 16-04

## `ruff check` pré-existente: ambiente do container não tinha `ruff` instalado

O container `api` não tinha o binário `ruff` disponível no `.venv` (`uv run ruff` falhava
com "Permission denied"/"No such file or directory"), apesar de `ruff==0.16.2` já estar
declarado em `pyproject.toml` (`dependency-groups.dev`) e resolvido em `uv.lock`. Rodei
`uv pip install ruff==0.16.2` (mesma versão já pinada no lockfile, nenhum pacote novo) só
para conseguir executar as verificações de lint exigidas pelo plano 16-04. Isso é um gap
de setup do container, não uma mudança de dependência — sinalizando aqui para quem cuidar
da imagem Docker: o `uv sync --frozen`/`--all-groups` não reinstalou `ruff` porque `uv`
já considerava os 63 pacotes "checados", mas o binário não estava no `.venv`.

## `ruff check` acusa ruído pré-existente em TODO o repositório, não só nos arquivos do 16-04

Com esse `ruff` já rodando, ficou claro que o projeto não passa "limpo" em `ruff check`
hoje: por exemplo, `app/modules/parametros` (não tocado por esta fase) já retorna 28
erros, incluindo `EXE002` (shebang ausente) em TODOS os arquivos do módulo `pedidos`,
`processing` etc. — inclusive arquivos que este plano nem tocou (`__init__.py`,
`adapters.py`, `models.py`). Não tentei consertar esse ruído porque:
(a) é pré-existente em todo o repositório, não causado pelas tasks do 16-04;
(b) o escopo estrito deste plano é `ports.py`/`service.py`/`repository.py` (as duas
classes relevantes) + os dois arquivos de teste.

Único achado de lint diretamente no arquivo tocado (`infrastructure/repository.py`) foi
`I001` (falta uma linha em branco entre o bloco de imports e `def _chunks`) — também
pré-existente: já estava assim antes de qualquer edição do 16-04 (só adicionei linhas
DENTRO do bloco de imports, sem alterar a linha em branco que seguia). Não corrigido,
pelo mesmo motivo de escopo.

Nenhum dos dois itens acima bloqueia os critérios de sucesso do 16-04: a suíte de testes
(`pytest`) está limpa (mesma 1 falha conhecida de teardown asyncpg, nenhuma outra), que é
o contrato de "verde" real desta fase.

## Flakiness pré-existente e não-determinística em `test_pedidos_read_projection.py`

Numa das execuções completas da suíte (durante a Task 3), 2 testes adicionais falharam
além da falha conhecida: `test_product_projection_separates_same_code_by_channel_and_keeps_sizes`
e `test_read_projection_nao_classifica_canal_desconhecido_como_franquia`. Rodando o
arquivo isolado (`pytest app/tests/test_pedidos_read_projection.py`), só a falha conhecida
aparece (1 failed, 13 passed). Reexecutando a suíte completa duas vezes seguidas depois,
voltou a exatamente 1 falha conhecida + `838 + 10 = 848` passed, nas duas vezes — ou seja,
a falha extra é intermitente/dependente de ordem de execução (a asserção que falhou foi
sobre `alerts.rows == []` recebendo ~20 linhas de alertas de registro de usuário, um
vazamento de estado de OUTRO módulo/teste, não relacionado a `pedido_standby_motivo` nem a
nenhuma tabela tocada por esta fase). Não investiguei a causa raiz porque é pré-existente e
fora do escopo estrito do 16-04 (nenhum arquivo de `auth`/alertas foi tocado por este
plano); documentando aqui para quem for investigar flakiness de suíte depois.
