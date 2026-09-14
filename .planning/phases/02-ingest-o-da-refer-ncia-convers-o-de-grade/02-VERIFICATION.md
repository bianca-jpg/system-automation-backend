---
phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
verified: 2026-08-05T00:00:00Z
status: passed
score: 5/5 must-haves verified (ROADMAP success criteria) + 4/4 must-haves adicionais dos planos
overrides_applied: 0
---

# Phase 2: Ingestão da referência + conversão de grade — Verification Report

**Phase Goal:** A referência tamanho→posição chega ao Postgres a cada sync de 2h (5ª fonte, full refresh), e existe uma função pura — testada com dados reais já ingeridos — que converte a grade interna de tamanhos em posições e1..e48.
**Verified:** 2026-08-05
**Status:** passed
**Re-verification:** No — verificação inicial

## Goal Achievement

### Observable Truths (5 Success Criteria do ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | A 5ª fonte grava em `produto_tamanho_posicao` os registros da view Databricks (`cd_prod_cor`, `sg_tamanho`, `nr_posicao`) | ✓ VERIFIED | `ler_referencia_tamanhos` (databricks_reader.py:77-85), `agregar_referencia_tamanhos` (agregacao.py:173-204), `substituir_referencia_tamanhos` (repositorio_snapshot.py:85-90) existem, testados por `test_sincronizar_referencia_tamanhos_persiste_e_descarta_invalidas`. Confirmado fora de mock: `SELECT COUNT(*) FROM produto_tamanho_posicao` no dev_db real retorna **569726** (verificado via psql nesta sessão de verificação, idêntico ao valor documentado em 02-04-SUMMARY.md) |
| 2 | `sincronizar_tudo` chama a 5ª fonte dentro do mesmo commit único; falha na 5ª etapa não deixa as outras parcialmente sincronizadas | ✓ VERIFIED | `casos_uso.py:241` — `referencia_tamanho_posicao = await sincronizar_referencia_tamanhos(db)` é a última etapa antes de `await db.commit()` (linha 242). Teste `test_sincronizar_tudo_nao_persiste_nada_se_referencia_falhar` (linha 481) cobre exatamente este caso |
| 3 | Duas chamadas sucessivas substituem completamente `produto_tamanho_posicao` sem duplicar | ✓ VERIFIED | Teste `test_sincronizar_referencia_tamanhos_duas_chamadas_sucessivas_substitui_sem_duplicar` (test_ingestao_sync.py:665). Confirmado fora de mock em 02-04-SUMMARY.md: contagem estável em 569726 após a 2ª sincronização real |
| 4 | Função pura de conversão recebe grade + `cd_prod_cor`, devolve `e1..e48`, testada sem banco usando dados reais já ingeridos nesta fase | ✓ VERIFIED | `converter_grade_para_posicoes` (grade_linx.py:8-45); teste `test_converter_grade_para_posicoes_com_dados_reais_da_referencia` (test_grade_linx.py:60-72) usa `_REFERENCIA_REAL = {"17": 1, "19": 2, "21": 3, "23": 4, "25": 5, "27": 6}` e `_CD_PROD_COR_REAL = "AC.02.0002|163"` — confirmado idêntico à consulta real feita nesta verificação (`SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM produto_tamanho_posicao WHERE cd_prod_cor = 'AC.02.0002\|163'`) |
| 5 | `sg_tamanho` sem `nr_posicao` correspondente: aviso no log, resto da grade convertido, sem exceção | ✓ VERIFIED | `converter_grade_para_posicoes` (grade_linx.py:27-30): `if pos is None: ignorados.append(sg_tamanho); continue` — nunca lança. Teste `test_converter_grade_para_posicoes_tamanho_sem_posicao_nao_lanca_excecao` (test_grade_linx.py:33-42) |

**Score:** 5/5 truths verified (ROADMAP)

### Must-Haves Adicionais (frontmatter dos planos, não reduzem o contrato do ROADMAP)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 6 | `_parse_posicao` descarta `nr_posicao` fora de 1..48 sem lançar exceção (D-01) | ✓ VERIFIED | `traducao_databricks.py:21-26`; `test_parse_posicao` parametrizado (test_ingestao_parse.py:88, 8 casos) |
| 7 | Conflito de posição agregado por produto, ambas as linhas conflitantes permanecem em `agrupado` (D-03/D-04) | ✓ VERIFIED | `agregacao.py:197-200`; testes `test_agregar_referencia_tamanhos_detecta_conflito_de_posicao_mantem_ambas_linhas` e `test_agregar_referencia_tamanhos_agrega_conflitos_por_produto_nao_por_linha` (test_ingestao_sync.py:548, 562) |
| 8 | `substituir_referencia_tamanhos` incondicional, sem guard/commit próprio | ✓ VERIFIED | `repositorio_snapshot.py:85-90` — só `DELETE` + `add_all`, nenhum `if`/`db.commit()` |
| 9 | `converter_grade_para_posicoes` importável via `app.modules.pedidos.service` | ✓ VERIFIED | `service.py:11` (import) + `service.py:73` (`__all__`) |

**Score combinado:** 9/9 must-haves verificados, 0 falhas, 0 overrides.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app/shared/config/settings.py` | campo `databricks_tabela_tamanho_ref` | ✓ VERIFIED | Linha 86-87, `Field(default="", validation_alias="DATABRICKS_TABELA_TAMANHO_REF")` |
| `.env.example` | `DATABRICKS_TABELA_TAMANHO_REF=...` | ✓ VERIFIED | Linha 41 |
| `app/modules/ingestao/domain/traducao_databricks.py` | `_parse_posicao` | ✓ VERIFIED | Linha 21-26; nunca lança exceção; wired em `agregacao.py` |
| `app/modules/ingestao/domain/agregacao.py` | `agregar_referencia_tamanhos` | ✓ VERIFIED | Linha 173-204; dedup + conflito D-03/D-04 |
| `app/modules/ingestao/infrastructure/databricks_reader.py` | `ler_referencia_tamanhos` | ✓ VERIFIED | Linha 77-85; SQL exato `SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM {tabela}` |
| `app/modules/ingestao/infrastructure/repositorio_snapshot.py` | `substituir_referencia_tamanhos` | ✓ VERIFIED | Linha 85-90; DELETE+INSERT incondicional |
| `app/modules/ingestao/application/casos_uso.py` | `sincronizar_referencia_tamanhos` + `sincronizar_tudo` estendido | ✓ VERIFIED | Linha 181-249; guard D-02 implementado; 5ª chamada antes do commit |
| `app/modules/ingestao/schemas.py` | `SincronizacaoResponse.faturamento_colecoes` (fix) + `.referencia_tamanho_posicao` (novo) | ✓ VERIFIED | Linha 13, 15 |
| `app/modules/pedidos/domain/grade_linx.py` | `converter_grade_para_posicoes` | ✓ VERIFIED | Linha 8-45; nunca lança, sempre devolve 48 chaves |
| `app/modules/pedidos/service.py` | re-export | ✓ VERIFIED | Import + `__all__` |
| `app/tests/test_grade_linx.py` | 5 testes (4 sintéticos + 1 dados reais) | ✓ VERIFIED | 5 funções `test_*` confirmadas; fixture real `_REFERENCIA_REAL`/`_CD_PROD_COR_REAL` conferida byte-a-byte contra o banco real |
| `app/tests/test_ingestao_sync.py` | testes de agregação pura + integração da 5ª fonte + orquestração de 5 etapas | ✓ VERIFIED | `test_agregar_referencia_tamanhos_*` (3), `test_sincronizar_referencia_tamanhos_*` (5), `test_sincronizar_tudo_orquestra_as_cinco_etapas_e_soma_resultado`, `test_sincronizar_tudo_nao_persiste_nada_se_referencia_falhar` |
| `app/tests/test_ingestao_parse.py` | `test_parse_posicao` (parametrizado) | ✓ VERIFIED | Linha 88, 8 casos |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `agregacao.py` | `traducao_databricks.py::_parse_posicao` | import direto | ✓ WIRED | `agregacao.py:14` import, usado em `agregacao.py:191` |
| `repositorio_snapshot.py` | `models.py::ProdutoTamanhoPosicao` | `db.add_all(ProdutoTamanhoPosicao(**item) ...)` | ✓ WIRED | `repositorio_snapshot.py:17` import, `repositorio_snapshot.py:90` uso |
| `casos_uso.py::sincronizar_tudo` | `casos_uso.py::sincronizar_referencia_tamanhos` | 5ª chamada, imediatamente antes de `db.commit()` | ✓ WIRED | `casos_uso.py:241-242` |
| `routes.py::sincronizar` | `schemas.py::SincronizacaoResponse` | `SincronizacaoResponse(status="success", **resultado)` | ✓ WIRED | `routes.py:34`; `resultado` inclui `referencia_tamanho_posicao` (chave do dict retornado por `sincronizar_tudo`) |
| `pedidos/service.py` | `grade_linx.py::converter_grade_para_posicoes` | re-export em `__all__` | ✓ WIRED | `service.py:11` + `service.py:73` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `produto_tamanho_posicao` (tabela) | linhas persistidas | `ler_referencia_tamanhos` → Databricks real → `agregar_referencia_tamanhos` → `substituir_referencia_tamanhos` | Sim — confirmado via `psql` nesta verificação: `SELECT COUNT(*)` = 569726 (não mock, não vazio) | ✓ FLOWING |
| `test_converter_grade_para_posicoes_com_dados_reais_da_referencia` | `_REFERENCIA_REAL`, `_CD_PROD_COR_REAL` | Transcrição literal de `02-04-SUMMARY.md`, conferida nesta verificação contra `SELECT ... WHERE cd_prod_cor = 'AC.02.0002\|163'` no banco real | Sim — os 6 pares `(sg_tamanho, nr_posicao)` na fixture são idênticos aos 6 registrados no Postgres real | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Suíte de testes da Fase 2 (parse, agregação, integração, conversão) | `docker compose exec -T api uv run pytest app/tests/test_grade_linx.py app/tests/test_ingestao_sync.py app/tests/test_ingestao_parse.py -q` | `84 passed` | ✓ PASS |
| Suíte completa do projeto (regressão) | `docker compose exec -T api uv run pytest -q` | `248 passed, 1 warning` (warning pré-existente do FastAPI em `test_auth_flows`, não é regressão desta fase) | ✓ PASS |
| Contagem real de `produto_tamanho_posicao` | `psql -c "SELECT COUNT(*) FROM produto_tamanho_posicao;"` | `569726` | ✓ PASS |
| Amostra real usada na fixture do teste | `psql -c "SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM produto_tamanho_posicao WHERE cd_prod_cor='AC.02.0002\|163' ORDER BY sg_tamanho;"` | 6 linhas, idênticas à fixture `_REFERENCIA_REAL` do teste | ✓ PASS |
| Env var `databricks_tabela_tamanho_ref` exposta em `Settings` | `grep -n "databricks_tabela_tamanho_ref" app/shared/config/settings.py .env.example` | 1 ocorrência em cada arquivo | ✓ PASS |
| Commits das tasks existem em `git log` | `git log --oneline -1 <hash>` para os 8 hashes documentados | Todos os 8 commits encontrados (`7e6dbf4`, `bb51dce`, `894499a`, `4193d7e`, `1b11f2b`, `4b85279`, `b448bb4`, `1e23b4a`) | ✓ PASS |

Nota sobre a contagem da suíte completa: o ambiente documenta baseline esperado de 241 passed; o resultado real (248 passed) é maior porque o working tree contém trabalho de terceiros não commitado, fora do escopo da Fase 2 (refatoração de estoque com `dt_estoque`, documentada em `deferred-items.md` e nas notas do ambiente) — isso NÃO é uma regressão da Fase 2, é trabalho adicional e não relacionado presente no mesmo working tree. Nenhum teste falhou.

### Probe Execution

Não aplicável — esta fase não usa probes dedicados (`scripts/*/tests/probe-*.sh`); a verificação funcional é feita via suíte pytest e consulta direta ao Postgres, ambos executados acima.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| ING-01 | 02-01, 02-03, 02-04 | Sync de 2h ingere a referência tamanho→posição como 5ª fonte, full refresh, em `produto_tamanho_posicao` | ✓ SATISFIED | Truths 1-3 verificadas; código wired; dados reais confirmados no Postgres (569726 linhas); marcado `[x]` em REQUIREMENTS.md e ROADMAP.md |
| LINX-03 | 02-02, 02-05 | Grade interna convertida em `e1..e48` usando a referência; tamanho sem posição gera aviso no log | ✓ SATISFIED | Truths 4-5 verificadas; `converter_grade_para_posicoes` implementada, testada com dados sintéticos e reais; marcado `[x]` em REQUIREMENTS.md e ROADMAP.md |

Nenhum requisito órfão: `REQUIREMENTS.md` mapeia apenas ING-01 e LINX-03 para a Fase 2, e ambos aparecem no campo `requirements:` de ao menos um dos 5 planos (`02-01`/`02-03`/`02-04` para ING-01; `02-02`/`02-05` para LINX-03).

### Anti-Patterns Found

Nenhum. Varredura em todos os 9 arquivos modificados/criados pela Fase 2 (`traducao_databricks.py`, `agregacao.py`, `databricks_reader.py`, `repositorio_snapshot.py`, `casos_uso.py`, `schemas.py`, `service.py` de ingestão e pedidos, `grade_linx.py`, `test_grade_linx.py`) não encontrou `TODO`, `FIXME`, `XXX`, `TBD`, `HACK`, `PLACEHOLDER`, retornos vazios hardcoded (`return null`/`return {}`/`return []`) nem handlers stub. O único match textual (`casos_uso.py:104`, "sem erro visível na API") é um comentário de UX pré-existente, não um marcador de dívida.

### Human Verification Required

Nenhum item pendente. O único passo que exigia acesso humano — sync real contra credenciais Databricks (checkpoint `02-04`) — já foi executado e documentado com evidência real e verificável (confirmada nesta sessão de verificação diretamente contra o Postgres de desenvolvimento: contagem 569726 e amostra `AC.02.0002|163` batem exatamente com `02-04-SUMMARY.md`).

### Gaps Summary

Nenhum gap. Os 5 critérios de sucesso do ROADMAP e os 4 must-haves adicionais dos planos estão implementados, testados e confirmados contra dados reais do Postgres de desenvolvimento (não apenas contra as afirmações do SUMMARY.md). A suíte de testes está verde (248 passed, sem regressão) e os 8 commits documentados existem no histórico do git.

Observação não bloqueante (fora do escopo desta verificação): o working tree contém trabalho de terceiros não commitado e sem relação com a Fase 2 (refatoração de estoque com `dt_estoque`), já sinalizado em `deferred-items.md` e nas notas do ambiente — não avaliado, não commitado e não revertido por esta verificação, conforme instrução explícita.

---

*Verified: 2026-08-05*
*Verifier: Claude (gsd-verifier)*
