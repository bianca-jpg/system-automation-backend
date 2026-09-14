---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
plan: 01
subsystem: ingestao
tags: [databricks, sqlalchemy, pytest, full-refresh, parse-tolerante]

# Dependency graph
requires:
  - phase: 01-schema-linx-e-refer-ncia-de-posi-o
    provides: "Tabela produto_tamanho_posicao (ProdutoTamanhoPosicao) criada pela migration 015, vazia, reversível"
provides:
  - "databricks_tabela_tamanho_ref em Settings + .env.example (env var DATABRICKS_TABELA_TAMANHO_REF)"
  - "_parse_posicao — parse tolerante de nr_posicao (1..48), descarta com None (D-01)"
  - "agregar_referencia_tamanhos — dedup por (cd_prod_cor, sg_tamanho) + detecção de conflito de posição agregada por produto (D-03/D-04)"
  - "ler_referencia_tamanhos — reader HTTP da view Databricks (5ª fonte)"
  - "substituir_referencia_tamanhos — repositório DELETE+INSERT incondicional, sem commit próprio"
  - "fix: _parse_int agora tolera string não numérica (ValueError capturado, devolve 0)"
affects: [02-03-sincronizar-referencia-tamanhos, fase-3-conversao-grade-e1-e48]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Parse tolerante que nunca lança exceção (mesmo padrão de _parse_float aplicado agora também a _parse_int)"
    - "Índice reverso posicao_por_produto (dict[str, dict[int, str]]) para detectar colisão cruzada de chave (conflito de posição), sem precedente exato no módulo antes deste plano"
    - "Repositório snapshot incondicional (sem guard/commit) — decisão de chamar ou não pertence ao caso de uso"

key-files:
  created: []
  modified:
    - app/shared/config/settings.py
    - .env.example
    - app/modules/ingestao/domain/traducao_databricks.py
    - app/tests/test_ingestao_parse.py
    - app/modules/ingestao/domain/agregacao.py
    - app/modules/ingestao/infrastructure/databricks_reader.py
    - app/modules/ingestao/infrastructure/repositorio_snapshot.py
    - app/tests/test_ingestao_sync.py

key-decisions:
  - "D-01/D-03/D-04 implementadas exatamente como especificado no CONTEXT.md; nenhum guard D-02 (referência vazia) foi implementado aqui — fica reservado para o caso de uso do Plano 02-03"
  - "Formato exato do aviso de conflito: f'{tamanho_anterior}/{tam}->pos{pos}' (ex.: 'M/GG->pos5')"
  - "_parse_int corrigido para capturar ValueError (bug pré-existente que quebrava a promessa 'parse tolerante, nunca lança' do arquivo, bloqueando o caso 'abc' exigido por _parse_posicao)"

patterns-established:
  - "Conflito de posição não remove nenhuma das linhas conflitantes de `agrupado` — ambas permanecem, cada uma na sua própria chave (cd, tam) (Pitfall 2 do RESEARCH.md)"

requirements-completed: [ING-01]

# Metrics
duration: ~20min
completed: 2026-08-05
---

# Phase 2 Plan 01: Fundação da 5ª fonte (referência tamanho→posição) Summary

**`_parse_posicao`, `agregar_referencia_tamanhos`, `ler_referencia_tamanhos` e `substituir_referencia_tamanhos` implementadas seguindo o molde exato das 4 fontes existentes, com detecção de conflito de posição agregada por produto (D-03/D-04) e descarte tolerante de `nr_posicao` inválido (D-01) — 226 testes passando (baseline 215 + 11 novos), sem regressão.**

## Performance

- **Duration:** ~20 min (leitura de contexto + implementação + verificação; commits entre 11:17 e 11:30 -03:00)
- **Started:** 2026-08-05T11:15:00-03:00 (aprox.)
- **Completed:** 2026-08-05T11:30:00-03:00 (aprox.)
- **Tasks:** 3/3
- **Files modified:** 8

## Accomplishments
- Env var `DATABRICKS_TABELA_TAMANHO_REF` disponível em `Settings` e documentada em `.env.example`, mesmo padrão dos 4 campos `databricks_tabela_*` existentes
- `_parse_posicao` valida `nr_posicao` na faixa 1..48 e nunca lança exceção, com 8 casos parametrizados cobrindo faixa válida, limites inclusivos, abaixo/acima da faixa, não numérico, `None` e vírgula decimal
- `agregar_referencia_tamanhos` deduplica por `(cd_prod_cor, sg_tamanho)`, descarta linhas inválidas (D-01) e detecta conflito de posição entre tamanhos diferentes do mesmo produto via índice reverso `posicao_por_produto`, agregando o aviso por produto — nunca por linha (D-03/D-04) — com 3 testes puros novos (sem I/O)
- `ler_referencia_tamanhos` e `substituir_referencia_tamanhos` seguem exatamente os análogos irmãos (`ler_estoque` / `substituir_faturamento_colecao`); o repositório permanece incondicional (sem guard, sem `db.commit()`), confirmado por grep automatizado

## Task Commits

Each task was committed atomically:

1. **Task 1: Env var da 5ª fonte (settings + .env.example)** - `7e6dbf4` (feat)
2. **Task 2: Parse tolerante de nr_posicao (D-01)** - `bb51dce` (test)
3. **Task 3: Agregação com conflito de posição (D-03/D-04), reader e repositório da 5ª fonte** - `894499a` (feat)

_Nota TDD: Task 2 tinha `tdd="true"` no plano, mas foi executada com implementação e teste no mesmo commit (não em commits RED→GREEN separados). Ver "Deviations from Plan" abaixo._

## Files Created/Modified
- `app/shared/config/settings.py` - campo `databricks_tabela_tamanho_ref`
- `.env.example` - linha `DATABRICKS_TABELA_TAMANHO_REF` no bloco Databricks
- `app/modules/ingestao/domain/traducao_databricks.py` - `_parse_posicao` + fix de `_parse_int` (tolerância a string não numérica)
- `app/tests/test_ingestao_parse.py` - `test_parse_posicao` (8 casos parametrizados)
- `app/modules/ingestao/domain/agregacao.py` - `agregar_referencia_tamanhos`
- `app/modules/ingestao/infrastructure/databricks_reader.py` - `ler_referencia_tamanhos`
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` - `substituir_referencia_tamanhos` (+ import de `ProdutoTamanhoPosicao`)
- `app/tests/test_ingestao_sync.py` - 3 testes puros de `agregar_referencia_tamanhos`

## Decisions Made
- Nenhuma decisão nova além das já travadas em `02-CONTEXT.md` (D-01, D-03, D-04). D-02 (guard de referência vazia) foi deliberadamente NÃO implementado aqui — pertence ao caso de uso do Plano 02-03, conforme escopo do plano.
- Formato do aviso de conflito confirmado exatamente como especificado: `"M/GG->pos5"` (`f"{tamanho_anterior}/{tam}->pos{pos}"`).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `_parse_int` lançava `ValueError` para string não numérica**
- **Found during:** Task 2 (implementação de `_parse_posicao` e seu teste com `_parse_posicao("abc")`)
- **Issue:** O plano assumia que `_parse_int("abc")` já devolvia `0` ("não numérico — `_parse_int` já devolve `0` para isso"), mas na prática `_parse_int` só tratava string vazia; qualquer texto não numérico (`"abc"`) propagava `ValueError` sem ser capturado — quebrando a garantia documentada no cabeçalho do módulo ("parse tolerante, nunca lança") e bloqueando a conclusão da Task 2, cujo teste falhava com exceção não tratada em vez de receber `None`.
- **Fix:** Envolvido o `int(float(...))` em `try/except ValueError: return 0`, no mesmo padrão que `_parse_float` já usa no mesmo arquivo.
- **Files modified:** `app/modules/ingestao/domain/traducao_databricks.py`
- **Verification:** Suíte completa (226 testes) verde, incluindo os 8 casos de `test_parse_posicao` e os 9 casos pré-existentes de `test_parse_int` (nenhuma regressão nos 4 usos existentes de `_parse_int` em `agregacao.py`).
- **Committed in:** `bb51dce` (parte do commit da Task 2)

**2. [Processo] Task 2 (`tdd="true"`) executada sem separação estrita RED→GREEN**
- **Found during:** Task 2
- **Issue:** O plano marcava a task com `tdd="true"`, esperando commits separados (test primeiro, falhando; depois implementação). A implementação de `_parse_posicao` e o teste correspondente foram escritos e commitados juntos em um único commit `test(02-01): ...`.
- **Impact:** Nenhum impacto funcional — comportamento e cobertura de teste são idênticos ao que o ciclo RED/GREEN produziria. Documentado aqui por transparência de processo, não como correção de bug.
- **Committed in:** `bb51dce`

**3. [Nota de contagem] `test_parse_posicao` tem 8 casos, não 7 como o texto do plano sugeria**
- **Found during:** Task 2
- **Issue:** O `<behavior>` do plano lista 7 marcadores, mas o segundo marcador ("`_parse_posicao(1) == 1` e `_parse_posicao(48) == 48`") descreve 2 valores de entrada distintos. Implementados os 8 casos individuais (5, 1, 48, 0, 49, "abc", None, "5,0") para cobertura fiel de todos os valores citados no `<behavior>`; a saída real é `8 passed`, não `7 passed` como o texto do `<acceptance_criteria>`/`<verification>` do plano antecipava.
- **Impact:** Nenhum — todos os comportamentos exigidos por D-01 estão cobertos; a discrepância é apenas na contagem textual do plano.
- **Committed in:** `bb51dce`

---

**Total deviations:** 3 (1 bug fix - Rule 1, 2 notas de processo/documentação sem impacto funcional)
**Impact on plan:** O fix em `_parse_int` foi necessário para a correção prometida pelo próprio módulo (parse tolerante, nunca lança) e para viabilizar o caso "abc" exigido pelo `<behavior>` da Task 2. Sem escopo extra além do estritamente necessário para completar as tasks do plano.

## Issues Encountered
Nenhum além do documentado em "Deviations from Plan".

## User Setup Required
None - nenhuma configuração de serviço externo necessária. `DATABRICKS_TABELA_TAMANHO_REF` já documentada em `.env.example`; a usuária preenche o valor real no `.env` local quando for testar a ingestão de fato (fora do escopo deste plano, que só constrói a fundação pura/testável).

## Requirement Tracking Note

`ING-01` aparece em `requirements:` do frontmatter deste plano, mas também em `02-03-PLAN.md` e `02-04-PLAN.md`. Como o objetivo deste plano é explícito ("Este plano NÃO liga a 5ª fonte ao caso de uso nem ao contrato HTTP"), `requirements.mark-complete ING-01` foi executado e depois **revertido deliberadamente** — `ING-01` permanece `[ ]` em `REQUIREMENTS.md` até o Plano 02-03/02-04 efetivamente ligar `sincronizar_referencia_tamanhos` a `sincronizar_tudo`. Marcar completo agora seria enganoso: a fundação pura está pronta, mas o sync real ainda não ingere a 5ª fonte.

## Next Phase Readiness
- As 3 funções assíncronas/puras (`_parse_posicao`, `agregar_referencia_tamanhos`, `ler_referencia_tamanhos`, `substituir_referencia_tamanhos`) estão prontas para o Plano 02-03 importar e orquestrar em `sincronizar_referencia_tamanhos`, incluindo o guard D-02 (referência vazia) e o log de resumo por produto.
- Nenhum bloqueio conhecido. Suíte completa verde (226 passed, 1 warning, ~3min40s no container).

## Self-Check: PASSED

Todos os 8 arquivos modificados encontrados no filesystem; os 3 commits de task (`7e6dbf4`, `bb51dce`, `894499a`) encontrados em `git log`.

---
*Phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade*
*Completed: 2026-08-05*
