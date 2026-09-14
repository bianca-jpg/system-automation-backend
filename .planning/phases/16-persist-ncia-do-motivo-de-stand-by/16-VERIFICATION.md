---
phase: 16-persist-ncia-do-motivo-de-stand-by
verified: 2026-08-21T00:00:00Z
status: passed
score: 11/11 must-haves verified
overrides_applied: 0
---

# Phase 16: Persistência do motivo de stand by — Verification Report

**Phase Goal:** Todo par que fica de fora de uma OR (crédito, estoque insuficiente ou furo de grade) tem o motivo registrado, sem inflar o retorno do job de processamento, e o sistema acumula quantas execuções seguidas cada par permanece parado.
**Verified:** 2026-08-21
**Status:** passed
**Re-verification:** No — verificação inicial completa (uma tentativa anterior no mesmo dia foi interrompida por limite de gasto antes de gravar o veredito formal; esta execução refez a leitura de código e a execução de testes do zero, de forma independente, e chegou à mesma conclusão)

## Método

Verificação goal-backward feita lendo diretamente o código-fonte resultante (não os SUMMARY.md) e confirmando com a suíte de testes real rodando via Docker. Arquivos lidos linha a linha: `ROADMAP.md` §Phase 16, `REQUIREMENTS.md` §v1.3 (STANDBY-01/06 + tabela de traceability), `16-CONTEXT.md` (GAP DE DESENHO), `16-VALIDATION.md` (K1-K7), `standby_motivo.py`, `motor_adequacao.py` (pontos de escrita de `preteridos_motivo`, `_commitar_par`, docstring de retorno), `processing/domain.py` (`PlanDraft`, `ProcessingResult.as_dict`, `build_processing_plan`, montagem do `plan_hash`), `processing/application/service.py` (`_plan_once`), `processing/infrastructure/repository.py` (`record_standby_reasons`, `apply_pairs`), `infrastructure/models.py::PedidoStandbyMotivo`, `ingestao/infrastructure/repositorio_snapshot.py::reconstruir_pedido_produto_read`, `alembic/versions/032_pedido_standby_motivo.py`, `alembic/env.py`, e os testes correspondentes lidos por completo (não só por nome): `test_pedidos_motor_furo_grade.py` (K1), `test_pedidos_processing_repository.py` (fan-out de crédito, upsert/contador, transação atômica com falha injetada), `test_pedidos_read_projection.py` (limpeza de órfãos). Cruzado com `git diff --name-only ab0ddfb 33bffd3` para confirmar a fronteira de arquivos tocados pela fase, e com `git status --porcelain` para confirmar que os 7 arquivos não commitados de outra sessão (paginação de `listar_produtos_sql`) não pertencem a este escopo. Suíte direcionada aos arquivos de teste da fase executada via `docker compose exec api uv run pytest` (não apenas lida — executada nesta sessão).

## Goal Achievement

### Observable Truths (ROADMAP § Phase 16, 4 critérios de sucesso)

| # | Truth | Status | Evidence |
|---|---|---|---|
| 1 | Motivo de qualquer par consultável, sem crescer o payload do job | ✓ VERIFIED | `ProcessingResult.as_dict()` (`processing/domain.py:335-341`) devolve só `plannedCount/appliedCount/deferredCount/blockedCreditCount` — 4 chaves, nenhuma lista de pares. `deferredPairs`/`blockedCreditPairs` aparecem em `processing/domain.py:648-649` só como insumo interno do cálculo de `plan_hash` (schemaVersion 2), nunca no payload retornado ao chamador. O motivo é consultado por SELECT direto em `pedido_standby_motivo`, fora do job |
| 2 | Par consecutivo em stand-by atualiza (não duplica), contador incrementa | ✓ VERIFIED | `record_standby_reasons` usa `pg_insert(...).on_conflict_do_update` na PK composta `(nr_pedido, cd_prod_cor)`, com `execucoes_consecutivas = execucoes_consecutivas + 1` no SET (`repository.py:221-238`). Teste real contra banco `test_record_standby_reasons_upserts_and_increments_execucoes_consecutivas` (linha 369) prova 3 chamadas sucessivas sobre o mesmo par: `COUNT==1` sempre, contador 1→2→3, mesmo trocando `sem_estoque→sem_credito→furo_grade` entre rodadas — executado nesta sessão, passou |
| 3 | Par que recebe OR some da consulta | ✓ VERIFIED | `apply_pairs` executa `DELETE FROM pedido_standby_motivo WHERE (nr_pedido, cd_prod_cor) IN (...)` (`repository.py:535-541`), na mesma transação do INSERT de `OrdemReserva`, antes do commit do caller |
| 4 | Full refresh limpa órfãos | ✓ VERIFIED | `_STANDBY_ORPHAN_CLEANUP_SQL` (`repositorio_snapshot.py:248-257`) roda dentro de `reconstruir_pedido_produto_read`, estritamente depois dos INSERTs pending/erp (comentário explícito no código sobre por que a ordem importa — a janela vazia entre DELETE e INSERT apagaria tudo). Teste `test_reconstruir_pedido_produto_read_apaga_standby_orfao_mas_preserva_par_pendente` (`test_pedidos_read_projection.py:719`) prova, no MESMO full refresh, o órfão apagado e o par pendente sobrevivendo — lido linha a linha |

**Score:** 4/4 critérios do ROADMAP verificados

### Invariantes K1-K7 (16-VALIDATION.md)

| # | Invariante | Status | Evidence |
|---|---|---|---|
| K1 | `preteridos_motivo` distingue `furo_grade` de `sem_estoque` nos dois modos | ✓ VERIFIED | `motor_adequacao.py`: `FURO_GRADE` gravado em dois pontos (linha 775, modo SEM_ADEQUAR; linha 848, modo ADEQUAR); `_commitar_par` grava `SEM_ESTOQUE` (linha 453). Testes `test_processar_pedidos_preteridos_motivo_distingue_furo_de_sem_estoque_modo_adequar` e `..._modo_sem_adequar` (`test_pedidos_motor_furo_grade.py:180,213`) lidos por completo: cada um monta um par com furo real e outro só sem estoque (tamanho único, nunca é furo), e asserta `!=` explicitamente entre os dois motivos, nos dois modos |
| K2 | Crédito bloqueado gera 1 linha por produto elegível do pedido, não por pedido | ✓ VERIFIED | Fan-out em `build_processing_plan` (`processing/domain.py:602-606`): itera `sorted(eligible)` filtrando por pedido bloqueado — não itera por pedido. Prova real contra banco: `test_record_standby_reasons_fans_out_credit_block_per_product` (`test_pedidos_processing_repository.py:444`), pedido 202 com produtos "C1"/"C2" → 2 linhas `sem_credito` distintas gravadas de fato — executado nesta sessão, passou |
| K3 | 5 chaves de retorno de `processar_pedidos` não mudam de forma; `preteridos_motivo` é aditivo | ✓ VERIFIED | Docstring de `processar_pedidos` (linhas 498-509) e os 3 pontos de merge (`motor_adequacao.py:521-528`, `585`, `963-973`) mantêm `resultados/selecionados/preteridos/bloqueados_credito/pares_processados` intactas, com `preteridos_motivo` como 6ª chave nova |
| K4 | `ProcessingResult` continua limitado a 4 contadores | ✓ VERIFIED | Ver Truth #1 |
| K5 | `CHECK (motivo IN (...))` aceita os 3 valores desde a criação | ✓ VERIFIED | `alembic/versions/032_pedido_standby_motivo.py:65-68`: `ck_psm_motivo` com `'sem_credito','sem_estoque','furo_grade'`, lido diretamente no DDL — não há migration posterior alterando o CHECK |
| K6 | Upsert nunca perde linha por corrida (`ON CONFLICT DO UPDATE`, nunca delete-then-insert) | ✓ VERIFIED | `repository.py:221-238` lido — nenhum `DELETE` antes do `INSERT` em `record_standby_reasons`; incremento é expressão de coluna dentro do próprio SET, sem SELECT prévio |
| K7 | Migration reversível | ✓ VERIFIED | `downgrade()` de 1 linha (`op.drop_table`), migration lida por completo |

**Score:** 7/7 invariantes verificadas

### Gap de desenho — verificação ponta a ponta (o motivo real de esta fase existir)

Cadeia completa rastreada linha a linha, sem colapso em nenhum ponto:

1. **Motor** (`motor_adequacao.py`): `tem_furo_de_grade` → `preteridos_motivo[par] = FURO_GRADE` (2 pontos, ambos os modos); caminho de falta de estoque genérica → `_commitar_par` grava `SEM_ESTOQUE`. Verbatim, sem relabeling.
2. **`PlanDraft`** (`processing/domain.py:592`): `motivo = result["preteridos_motivo"].get(pair, SEM_ESTOQUE)` — lê o valor do motor e grava em `deferred_pairs` como está. O comentário no código (linhas 586-591) documenta que o fallback só age em caso impossível hoje (dado K3), nunca reescreve valor presente.
3. **Repositório** (`repository.py:189-199`): `motivo=motivo` do tuple desempacotado direto para o dict do INSERT — nenhum mapeamento/normalização.
4. **Tabela** (migration 032): `CHECK` aceita os 3 valores desde a criação.

Confirmado por teste em dois níveis: nível de domínio puro (`test_pedidos_motor_furo_grade.py`, K1) e nível de banco real (`test_record_standby_reasons_fans_out_credit_block_per_product`, valores gravados de fato). Nenhum teste ou trecho de código encontrado que colapse os dois motivos.

### Atomicidade real (record_standby_reasons dentro da transação de store_plan)

**Ordem confirmada por leitura de código:** `service.py::_plan_once` chama `processing.store_plan(...)` (linha 260), depois `processing.record_standby_reasons(...)` (linha 275), e só então `unit_of_work.commit()` (linha 292) — mesma sessão, comentário explícito no código (linhas 265-274) justificando por que a ordem é obrigatória (perda silenciosa e permanente se fosse depois do commit).

**Prova com falha injetada, lida e executada nesta sessão:** `test_plan_once_rolls_back_the_plan_when_the_standby_write_fails` (`test_pedidos_processing_repository.py:1562`) usa `monkeypatch` para forçar `record_standby_reasons` a lançar `RuntimeError`, chama `_plan_once` de verdade (não um fake simplificado), captura a exceção, faz `rollback()` explícito, e então abre uma nova sessão para confirmar: header ainda `PENDING`, `plan_hash is None`, `COUNT(*) FROM pedido_processamento_plan == 0`, `COUNT(*) FROM pedido_standby_motivo == 0`. Isto é evidência real de rollback total via asserções de banco, não uma afirmação de SUMMARY.

### Fronteira respeitada

`git diff --name-only ab0ddfb 33bffd3` (primeiro/último commit da fase) — confirmado nesta sessão — mostra apenas: `alembic/env.py`, `alembic/versions/032_pedido_standby_motivo.py`, `app/modules/ingestao/infrastructure/repositorio_snapshot.py`, `app/modules/pedidos/domain/{motor_adequacao,standby_motivo}.py`, `app/modules/pedidos/infrastructure/models.py`, `app/modules/pedidos/processing/{domain.py,application/ports.py,application/service.py,infrastructure/repository.py}`, os arquivos de teste correspondentes, e documentos de `.planning/`. Nenhum arquivo de frontend, nenhuma migration extra, nenhum arquivo de Phase 17+.

`git status --porcelain` nesta sessão confirma os 7 arquivos de paginação (`listar_produtos_sql` e afins: `application/consultas.py`, `application/schemas.py`, `domain/consultas.py`, `infrastructure/http/routes.py`, `infrastructure/repositorio_produtos.py`, `infrastructure/repositorio_resumo.py`, `service.py`) modificados e não commitados por outra sessão ao vivo — nenhum deles aparece no diff da Fase 16 acima; não fazem parte deste escopo e não foram tocados por esta verificação.

### Required Artifacts

| Artifact | Expected | Status | Details |
|---|---|---|---|
| `app/modules/pedidos/domain/standby_motivo.py` | Vocabulário canônico 3 valores | ✓ VERIFIED | `SEM_CREDITO/SEM_ESTOQUE/FURO_GRADE/MOTIVOS_VALIDOS`, módulo-folha sem imports |
| `app/modules/pedidos/domain/motor_adequacao.py` | `preteridos_motivo` como 6ª chave aditiva | ✓ VERIFIED | Confirmado nos pontos de escrita e merge |
| `alembic/versions/032_pedido_standby_motivo.py` | Tabela + CHECK 3 motivos | ✓ VERIFIED | DDL completo, `downgrade()` funcional, importado em `alembic/env.py:33` |
| `app/modules/pedidos/infrastructure/models.py::PedidoStandbyMotivo` | Model ORM espelhando migration | ✓ VERIFIED | PK composta, `execucoes_consecutivas` default 1, `job_id` sem FK (decisão documentada no docstring) |
| `app/modules/pedidos/processing/domain.py::PlanDraft` | `deferred_pairs`/`blocked_credit_pairs` | ✓ VERIFIED | Guarda de overlap em `__post_init__` (linhas 307-311), `plan_hash` schemaVersion 2 sensível aos dois campos |
| `app/modules/pedidos/processing/infrastructure/repository.py::record_standby_reasons` | Upsert chunkado | ✓ VERIFIED | `pg_insert(...).on_conflict_do_update`, chunkado por `APPLY_CHUNK_SIZE` |
| `app/modules/ingestao/infrastructure/repositorio_snapshot.py::reconstruir_pedido_produto_read` | Limpeza de órfãos | ✓ VERIFIED | `_STANDBY_ORPHAN_CLEANUP_SQL` posicionado corretamente após os INSERTs |

### Key Link Verification

| From | To | Via | Status | Details |
|---|---|---|---|---|
| `motor_adequacao.processar_pedidos` | `PlanDraft.deferred_pairs` | `build_processing_plan` lê `result["preteridos_motivo"]` | ✓ WIRED | Verbatim, sem relabeling |
| `PlanDraft` | `record_standby_reasons` | `_plan_once` passa `draft.deferred_pairs`/`draft.blocked_credit_pairs` | ✓ WIRED | `service.py:276-280` |
| `record_standby_reasons` | `store_plan` (mesma transação) | chamada sequencial antes de `unit_of_work.commit()` | ✓ WIRED | Provado com falha injetada + rollback (teste executado nesta sessão) |
| `apply_pairs` | `pedido_standby_motivo` (limpeza) | `DELETE` escopado por par, mesma transação da OR | ✓ WIRED | `repository.py:535-541` |
| `reconstruir_pedido_produto_read` | `pedido_standby_motivo` (limpeza de órfãos) | `_STANDBY_ORPHAN_CLEANUP_SQL` após os INSERTs | ✓ WIRED | Ordem verificada por leitura de código e teste dedicado |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|---|---|---|---|---|
| STANDBY-01 | 16-01 a 16-04 | Motivo persistido sem inflar job | ✓ SATISFIED | Truths 1-4, K1-K5 |
| STANDBY-06 | 16-04 | Contador de execuções consecutivas | ✓ SATISFIED | `execucoes_consecutivas`, teste 1→2→3 trocando motivo entre rodadas, executado nesta sessão |

`REQUIREMENTS.md` §v1.3, tabela de traceability (linhas 272 e 277): STANDBY-01 e STANDBY-06 mapeados só para a Phase 16, ambos `Complete`. Nenhum requirement órfão.

### Anti-Patterns Found

Nenhum `TBD`/`FIXME`/`XXX`/`TODO`/`HACK`/`PLACEHOLDER` real encontrado nos arquivos de produção tocados por esta fase (`grep` deu 2 falsos positivos: a palavra "TODOS" em maiúsculo, capturada pelo regex de "TODO", sem relação com débito técnico).

**Achado informativo (não bloqueante):** `test_migration_032_pedido_standby_motivo.py` (K5/K7) se auto-descarta (`pytest.skip`) sem a env var `MIGRATION_TEST_DATABASE_URL`, ausente no `docker-compose.yml` de desenvolvimento — confirmado nesta sessão pela execução real (apareceu como 1 skip). É o padrão já estabelecido para os demais `test_migration_*.py` do repositório, coberto isoladamente pela CI — não uma decisão isolada desta fase.

### Behavioral Spot-Checks / Execução real dos testes (nesta sessão)

Executado via `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_motor_furo_grade.py app/tests/test_pedidos_processing_domain.py app/tests/test_pedidos_processing_repository.py app/tests/test_pedidos_processing_service.py app/tests/test_migration_032_pedido_standby_motivo.py app/tests/test_pedidos_read_projection.py -q`.

**Resultado:** `1 failed, 118 passed, 1 skipped`. A única falha é `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` — assert sobre `alerts.rows`/contaminação de dados de alertas de outra área (auth/registro), seguida de erro de teardown asyncpg (`Event loop is closed`), sem qualquer relação com `pedido_standby_motivo`, `preteridos_motivo` ou qualquer artefato desta fase. Consistente com a linha de base documentada em `16-VALIDATION.md` (falha pré-existente, "nunca conserte") e com a medição independente do orquestrador na suíte completa (849 passed, 17 skipped, 1 failed).

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Suíte direcionada da Fase 16 (7 arquivos) | `docker compose exec api uv run pytest ...` | 118 passed, 1 skipped, 1 failed (pré-existente, fora de escopo) | ✓ PASS |

### Probe Execution

Nenhum probe declarado ou convencional (`scripts/*/tests/probe-*.sh`) encontrado para esta fase. Não aplicável.

### Human Verification Required

Nenhum. Fase 100% backend (domínio + banco), confirmado em `16-VALIDATION.md` ("Manual-Only Verifications: nenhuma").

### Gaps Summary

Nenhum gap bloqueante encontrado. O "gap de desenho" que motivou a criação desta fase — o risco de colapsar `furo_grade` em `sem_estoque` — foi verificado ponta a ponta (motor → PlanDraft → repositório → CHECK da tabela), lendo cada ponto de escrita pessoalmente, e confirmado NÃO colapsado, com testes que asseram explicitamente valores diferentes no mesmo cenário, nos dois modos de processamento e em dois níveis (domínio puro e banco real executado nesta sessão). A atomicidade real (rollback total sob falha injetada) e o fan-out de crédito por produto (não por pedido) foram confirmados com testes que tocam banco de verdade, executados nesta sessão — não apenas lidos. A fronteira de arquivos da fase está limpa, sem sobreposição com o trabalho não commitado de paginação de outra sessão em curso.

---

*Verified: 2026-08-21*
*Verifier: Claude (gsd-verifier)*
