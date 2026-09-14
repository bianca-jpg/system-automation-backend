# Deferred Items — Fase 2

Itens fora do escopo do Plano 02-05 encontrados no working tree durante a execução, não tocados (Scope Boundary do executor).

## 1. Trabalho não commitado, sem relação com o Plano 02-05

Encontrado no `git status --short` ao iniciar o Plano 02-05 (working tree já sujo antes desta execução):

- `app/modules/ingestao/application/casos_uso.py` (modificado)
- `app/modules/ingestao/domain/agregacao.py` (modificado) — refatoração de `agregar_estoque` para `EstoqueDiagnostico` (NamedTuple com `ignoradas`, `sem_disponivel`, `duplicadas`, `datas`), troca de vocabulário `qt_disponivel`/`ds_canal`/`cd_prod_cor` para `quantidade_disponivel`/`canal`/`codigo_produto`/`tamanho`, novo `_parse_data`
- `app/modules/ingestao/domain/traducao_databricks.py` (modificado)
- `app/modules/ingestao/infrastructure/databricks_reader.py` (modificado)
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` (modificado)
- `app/modules/ingestao/models.py` (modificado)
- `alembic/versions/016_estoque_dt_estoque.py` (novo, untracked)
- `.planning/config.json` (novo, untracked)

Este conjunto de mudanças (diagnóstico de agregação de estoque, `dt_estoque`, migration 016) é claramente trabalho de outra frente — não relacionado à conversão de grade (LINX-03) nem à referência tamanho→posição (ING-01) que são o escopo da Fase 2. Não foi criado, modificado nem commitado por este plano. Deixado intocado conforme Scope Boundary do executor.

**Ação recomendada:** revisar e commitar (ou descartar) esse trabalho separadamente, fora do fluxo GSD desta fase.
