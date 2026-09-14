---
phase: 01-schema-linx-e-refer-ncia-de-posi-o
reviewed: 2026-08-04T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - app/modules/ingestao/models.py
  - app/modules/pedidos/models.py
  - alembic/env.py
  - alembic/versions/015_schema_linx_referencia_posicao.py
  - app/tests/test_schema_guard.py
findings:
  critical: 0
  warning: 2
  info: 4
  total: 6
status: issues_found
---

# Fase 1: Relatório de Code Review

**Revisado:** 2026-08-04
**Profundidade:** standard
**Arquivos revisados:** 5
**Status:** issues_found

## Sumário

Revisão adversarial da Fase 1 (schema Linx e referência de posição): dois models novos (`ProdutoTamanhoPosicao`, `OrdemReservaLinx`), migration manual 015 e registro de imports em `alembic/env.py` e `test_schema_guard.py`.

**Verificações de correção que PASSARAM** (checadas linha a linha, não presumidas):

- **Migration 015 ↔ models**: as 82 colunas de `ordens_reserva_linx` e as 5 de `produto_tamanho_posicao` batem coluna a coluna entre model e migration — mesmos nomes, tipos, precisões (`Numeric(14,2)`, `Numeric(6,2)`), nullability e nomes de constraint (`uq_ordens_reserva_linx_chave`, `uq_produto_tamanho_posicao_chave`). O `server_default="0"` do model e o `sa.text("0")` da migration produzem o mesmo default efetivo em Postgres para coluna `Integer`.
- **Cadeia de revisões Alembic**: `down_revision = "014"` confere com o `revision: str = "014"` real de `014_or_por_produto.py`. `downgrade()` dropa as duas tabelas na ordem inversa e o risco destrutivo pós-Fase 3 está documentado no docstring.
- **Cobertura de imports**: TODOS os 14 models `Base` do repositório (varredura em `app/modules/*/models.py`) estão importados tanto em `alembic/env.py` quanto em `test_schema_guard.py` — nenhum model ficou invisível ao guard ou ao Alembic.
- **Layout vs pesquisa**: colunas, tipos e ordem conferem com a tabela "Layout completo do CSV Linx" do `01-RESEARCH.md`.
- **Decisões travadas respeitadas** (não avaliadas como findings): migration manual, dois estilos de model coexistindo, colunas Linx nullable, `e1..e48` com `server_default "0"`, sem FK em `ordens_reserva_linx`.

Nenhum defeito Critical foi encontrado. Os achados abaixo são 2 Warnings (robustez do guard e integridade de dados que afeta as Fases 2/3) e 4 Infos.

## Warnings

### WR-01: Query de PK do schema guard não é qualificada por schema

**File:** `app/tests/test_schema_guard.py:96-105`
**Issue:** A query que lê a PK real do banco filtra `pg_class` apenas por `c.relname = :t`, sem restringir ao schema `public`. Se existir uma relação com o mesmo nome em QUALQUER outro schema (extensões, schemas de ferramenta, tabelas temporárias promovidas), o resultado une os attnames das duas relações e `pk_banco` fica errado — o teste pode falhar com falso positivo ou, pior, passar comparando contra a tabela errada. Note a inconsistência interna do próprio arquivo: a query de colunas (linha 62) filtra `table_schema = 'public'`, mas a de PK não. O problema é pré-existente, porém esta fase adiciona 2 tabelas parametrizadas que passam a depender dessa query.
**Fix:**
```sql
SELECT a.attname FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = i.indrelid
 AND a.attnum = ANY(i.indkey)
WHERE c.relname = :t AND n.nspname = 'public' AND i.indisprimary
```

### WR-02: `nr_posicao` sem restrição de faixa (1..48) — grade só tem 48 posições

**File:** `app/modules/ingestao/models.py:146` e `alembic/versions/015_schema_linx_referencia_posicao.py:31`
**Issue:** `nr_posicao` é o índice na grade posicional fixa `e1..e48` de `ordens_reserva_linx` (o próprio `01-RESEARCH.md` documenta "inteiro (posição 1..48)"). A coluna aceita qualquer inteiro — 0, negativo, 49, 500. Como a Fase 2 faz full refresh direto da view do Databricks sem contrato de dados, um valor fora da faixa entra silencioso e a Fase 3, ao resolver `e{nr_posicao}`, vai quebrar (KeyError/AttributeError) ou corromper a projeção — exatamente a classe de drift silencioso que motivou o `test_schema_guard.py`. Corrigir agora custa zero; depois custa uma migration extra.
**Fix:** Na migration 015 e no model (ou, alternativamente, validação explícita no código de ingestão da Fase 2 — mas o CHECK protege contra qualquer caminho de escrita):
```python
sa.CheckConstraint(
    "nr_posicao BETWEEN 1 AND 48",
    name="ck_produto_tamanho_posicao_faixa",
)
```

## Info

### IN-01: Comentário menciona "autogenerate" em projeto com migrations manuais travadas

**File:** `app/tests/test_schema_guard.py:21-22`
**Issue:** O comentário diz "mesma lista que alembic/env.py usa para o autogenerate", mas a decisão travada do projeto é migration manual SEM autogenerate (precedente da migration 012 perdida, documentado no `01-RESEARCH.md`). O comentário induz um futuro mantenedor a rodar `alembic revision --autogenerate`, exatamente o anti-pattern que o projeto proíbe.
**Fix:** Trocar por "mesma lista que alembic/env.py usa para popular o `target_metadata`".

### IN-02: Sem unicidade em `(cd_prod_cor, nr_posicao)` — dois tamanhos podem apontar para a mesma posição

**File:** `app/modules/ingestao/models.py:135-139`
**Issue:** O unique cobre `(cd_prod_cor, sg_tamanho)` (um tamanho → uma posição), mas nada impede o inverso: dois tamanhos do mesmo produto mapeados para a MESMA `nr_posicao`. Na Fase 3 isso faria duas quantidades disputarem a mesma coluna `e{n}` (última escrita vence ou soma indevida), corrompendo a grade enviada ao Linx sem nenhum erro. Trade-off: um unique adicional faria o full refresh falhar inteiro num dado sujo da view — se isso for inaceitável, validar/logar na ingestão da Fase 2.
**Fix:** `UniqueConstraint("cd_prod_cor", "nr_posicao", name="uq_produto_tamanho_posicao_pos")` ou validação com log na Fase 2.

### IN-03: Schema guard não compara tipos nem defaults — cobertura parcial para as 82 colunas novas

**File:** `app/tests/test_schema_guard.py:52-81`
**Issue:** O guard compara existência de coluna, nullability e PK — não compara tipo (`Integer` no model vs `varchar` no banco passaria) nem `server_default` (o "0" de `e1..e48`, decisão travada desta fase, não é verificado). Também é unidirecional: coluna existente no banco e ausente no model não é detectada. A fase apoia sua verificação de sucesso nesse guard; vale conhecer o limite. Escopo original do teste era a classe de bug de PK (documentado no docstring), então é limitação de design, não bug.
**Fix:** Se desejado, adicionar comparação de `data_type` do `information_schema.columns` contra `tabela.columns[nome].type` (mapeamento aproximado por família de tipo) em um teste separado. Não bloqueia esta fase.

### IN-04: `set_main_option` com URL crua — quebra se a senha contiver `%` (pré-existente)

**File:** `alembic/env.py:37`
**Issue:** `config.set_main_option("sqlalchemy.url", settings.database_url)` passa a URL pelo ConfigParser do Alembic, que faz interpolação de `%`. Uma senha com caractere `%` (comum quando URL-encoded, ex.: `%40` para `@`) levanta `InterpolationSyntaxError` ao rodar qualquer migration. Pré-existente (a mudança desta fase no arquivo foi só de imports), registrado porque o arquivo está no escopo.
**Fix:** `config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))`

---

_Revisado: 2026-08-04_
_Revisor: Claude (gsd-code-reviewer)_
_Profundidade: standard_
