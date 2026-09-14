---
phase: 16-persist-ncia-do-motivo-de-stand-by
plan: 04
subsystem: database
tags: [sqlalchemy, postgres, upsert, pg_insert, on_conflict_do_update, asyncio, pytest]

# Dependency graph
requires:
  - phase: 16-02
    provides: "PedidoStandbyMotivo model + migration (PK composta nr_pedido/cd_prod_cor, CHECK de 3 motivos, execucoes_consecutivas server_default 1)"
  - phase: 16-03
    provides: "PlanDraft.deferred_pairs/blocked_credit_pairs (grão par, motivo explícito nos deferidos, implícito sem_credito nos bloqueados)"
provides:
  - "ProcessingRepository.record_standby_reasons: upsert único chunkado que grava/atualiza o motivo de stand-by por par, incrementando execucoes_consecutivas por expressão de coluna"
  - "apply_pairs apagando a linha de stand-by do par que virou OR, na mesma transação da OR"
  - "_plan_once persistindo plano + motivos na MESMA transação, com teste de rollback total sob falha injetada"
affects: ["17 (leitura de pedido_standby_motivo pela UI)"]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Upsert único via pg_insert(...).on_conflict_do_update com incremento de coluna no SET (sem SELECT prévio, sem read-modify-write)"
    - "Chunking por APPLY_CHUNK_SIZE reusando o helper _chunks já usado por store_plan, para não estourar o teto de 65535 bind parameters do Postgres"
    - "Escrita auxiliar dentro da MESMA transação do escritor principal (record_standby_reasons entre store_plan e commit), provado por teste de falha injetada + rollback"

key-files:
  created: []
  modified:
    - app/modules/pedidos/processing/application/ports.py
    - app/modules/pedidos/processing/application/service.py
    - app/modules/pedidos/processing/infrastructure/repository.py
    - app/tests/test_pedidos_processing_repository.py
    - app/tests/test_pedidos_processing_service.py

key-decisions:
  - "execucoes_consecutivas é agnóstico ao motivo: conta execuções seguidas em QUALQUER stand-by, não execuções seguidas com o MESMO motivo — decisão [ASSUMED] herdada de A2 do 16-RESEARCH.md, sem ambiguidade encontrada na execução (o Teste 1 da Task 1 prova 1→2→3 trocando de motivo em cada rodada)"
  - "Uma tentativa de planejamento que faz rollback NÃO infla execucoes_consecutivas — consequência direta de record_standby_reasons e store_plan viverem na mesma transação (provado pelo Teste 3 da Task 3)"
  - "A limpeza de órfãos no full refresh da ingestão (reconstruir_pedido_produto_read, critério 4 do ROADMAP) NÃO entrou neste plano — fica pendente na fase, por ter uma armadilha de ordenação própria (Pitfall 3 do 16-RESEARCH.md)"

patterns-established:
  - "Toda escrita auxiliar que precisa da mesma atomicidade do escritor principal (store_plan) entra ANTES do commit do caller, nunca com commit próprio — o método termina em flush()"

requirements-completed: [STANDBY-01, STANDBY-06]

# Metrics
duration: ~40min
completed: 2026-08-21
---

# Phase 16 Plan 04: Persistência real de record_standby_reasons + limpeza em apply_pairs Summary

**Upsert único chunkado (pg_insert + on_conflict_do_update) que grava o motivo de stand-by por par com incremento atômico de execucoes_consecutivas, chamado na MESMA transação de store_plan dentro de _plan_once, e removido por apply_pairs quando o par vira OR.**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-08-21T14:05:00Z (baseline de suíte antes de qualquer edição)
- **Completed:** 2026-08-21T14:39:30Z
- **Tasks:** 3/3 completed
- **Files modified:** 5 (+ 1 arquivo de docs de fase: `deferred-items.md`)

## Accomplishments
- `ProcessingRepository.record_standby_reasons` portado e implementado como upsert único (`pg_insert(...).on_conflict_do_update`) na PK composta `(nr_pedido, cd_prod_cor)`, chunkado por `APPLY_CHUNK_SIZE`, incrementando `execucoes_consecutivas` por expressão de coluna — nunca delete-then-insert, nunca read-modify-write.
- Fan-out de crédito por PRODUTO (não por pedido) provado em DB real: um pedido bloqueado com N produtos elegíveis gera N linhas `motivo='sem_credito'`.
- `apply_pairs` (`SqlAlchemyProcessingWriter`) apaga a linha de stand-by do par que virou OR, escopada pelo par composto, na mesma transação da OR — com teste de linha de controle sobrevivente.
- `_plan_once` chama `record_standby_reasons` entre `store_plan` e `unit_of_work.commit()`, reusando `planned_at`; um teste de falha injetada exatamente nessa fronteira prova rollback total (header ainda `pending`, zero linhas em `pedido_processamento_plan` e em `pedido_standby_motivo`).
- Suíte completa: `838 → 848 passed` (10 testes novos: 5 + 2 + 3), `17 skipped`, mesma 1 falha conhecida de teardown asyncpg — nenhuma regressão.

## Task Commits

Each task was committed atomically:

1. **Task 1: Port + upsert com incremento (chunkado)** - `ab2fb62` (feat)
2. **Task 2: apply_pairs apaga a linha de stand-by** - `c5045c8` (feat)
3. **Task 3: wiring em _plan_once (mesma transação)** - `75269f4` (feat)
4. **Follow-up de estilo (Task 2, achado durante a checagem de acceptance criteria):** `6aae767` (style) — reformatou o filtro `tuple_(...)` do DELETE para uma única linha (mesmo estilo já usado para `OrdemReserva` no mesmo método); sem mudança de comportamento, suíte re-executada e verde antes e depois.

_Nenhuma task usou TDD com commits separados test→feat→refactor: `tdd="true"` no frontmatter, mas cada task escreveu RED+GREEN dentro do MESMO commit (padrão já usado nas plans anteriores da fase 16), porque o `<action>` do plano pede confirmação de RED antes de implementar, não um commit de RED isolado._

## Files Created/Modified
- `app/modules/pedidos/processing/application/ports.py` - `ProcessingRepository.record_standby_reasons` (Protocol, keyword-only), posicionado depois de `store_plan`
- `app/modules/pedidos/processing/infrastructure/repository.py` - `SqlAlchemyProcessingRepository.record_standby_reasons` (upsert chunkado); `SqlAlchemyProcessingWriter.apply_pairs` ganha o `DELETE` escopado por par composto
- `app/modules/pedidos/processing/application/service.py` - `_plan_once` chama `record_standby_reasons` entre `store_plan` e o heartbeat/commit
- `app/tests/test_pedidos_processing_repository.py` - fixture `processing_database` com `PedidoStandbyMotivo` no cleanup; 10 testes novos (5 upsert/fan-out/chunking/no-op/duplicata, 2 de `apply_pairs`, 3 de `_plan_once` atômico incluindo o teste de rollback)
- `app/tests/test_pedidos_processing_service.py` - `record_standby_reasons` nos dois fakes `Processing`; 1 teste novo de ordem `store → standby → commit` com relógio incremental

## Decisions Made
- `execucoes_consecutivas` é agnóstico ao motivo (não reseta ao trocar de motivo entre rodadas) — decisão `[ASSUMED]` de A2 do `16-RESEARCH.md`, confirmada sem ambiguidade pelo Teste 1 da Task 1 (1→2→3 trocando `sem_estoque`→`sem_credito`→`furo_grade`).
- Uma tentativa de planejamento que sofre rollback não infla o contador — não é uma decisão nova, é consequência direta de `record_standby_reasons` viver na mesma transação de `store_plan` (provado pelo teste de falha injetada da Task 3).
- A limpeza de órfãos do full refresh de ingestão (`reconstruir_pedido_produto_read`) permanece fora de escopo, como o plano já previa — não é responsabilidade desta plan.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `ruff` ausente no `.venv` do container `api`**
- **Found during:** Task 1, ao rodar a verificação de lint exigida pelo plano
- **Issue:** `uv run ruff` falhava com "Permission denied"/"No such file or directory" — o binário não estava instalado no `.venv`, apesar de `ruff==0.16.2` já estar declarado em `pyproject.toml`/`uv.lock`. `uv sync --frozen --all-groups` não resolveu (relatou "63 packages checked" sem instalar o `ruff` faltante)
- **Fix:** `uv pip install ruff==0.16.2` — mesma versão já pinada no lockfile, nenhum pacote novo/não auditado
- **Files modified:** nenhum arquivo do repositório (mudança só no ambiente do container)
- **Verificação:** `uv run ruff --version`/`uv run ruff check` passaram a funcionar
- **Committed in:** N/A (mudança de ambiente, não de código)

**2. [Achado durante checagem de acceptance criteria, não um bug] Reformatação de estilo no DELETE de `apply_pairs`**
- **Found during:** revisão final dos critérios de aceite da Task 2 (`grep -n "tuple_(PedidoStandbyMotivo"` esperava 1 linha; o código original quebrava a chamada em múltiplas linhas)
- **Issue:** nenhum — comportamento idêntico, só formatação
- **Fix:** `tuple_(PedidoStandbyMotivo.nr_pedido, PedidoStandbyMotivo.cd_prod_cor).in_(sorted(pairs))` reformatado para caber no padrão de uma linha só, mesmo estilo já usado para `OrdemReserva` no mesmo arquivo
- **Files modified:** `app/modules/pedidos/processing/infrastructure/repository.py`
- **Verificação:** `pytest app/tests/test_pedidos_processing_repository.py -q` → 31 passed antes e depois
- **Committed in:** `6aae767` (commit de estilo separado, escopo `16-04`)

---

**Total deviations:** 1 auto-fix de ambiente (Rule 3, não é código) + 1 ajuste de estilo pós-commit sem mudança de comportamento.
**Impact on plan:** Nenhum. Nenhuma mudança de arquitetura, nenhuma mudança de comportamento fora do que o plano especificou.

## Issues Encountered

- **Ruído pré-existente de `ruff check` em todo o repositório:** com o `ruff` já funcionando, ficou claro que a base inteira do projeto não passa "limpo" hoje — por exemplo, `app/modules/parametros` (não tocado por esta fase) já retorna 28 erros, incluindo `EXE002` (shebang ausente) em TODOS os arquivos de vários módulos, inclusive arquivos que este plano nem tocou. Não tentei consertar esse ruído (fora do escopo estrito do 16-04); documentado em `deferred-items.md` na pasta da fase. O único achado de lint diretamente no arquivo tocado (`repository.py`) foi `I001` (falta 1 linha em branco antes de `def _chunks`), também pré-existente — não corrigido, mesmo motivo.
- **Flakiness intermitente em `test_pedidos_read_projection.py`:** numa das execuções completas da suíte (durante a Task 3), 2 testes adicionais falharam além da falha conhecida (`test_product_projection_separates_same_code_by_channel_and_keeps_sizes` e `test_read_projection_nao_classifica_canal_desconhecido_como_franquia`), por vazamento de ~20 linhas de alertas de registro de usuário de OUTRO módulo — não relacionado a `pedido_standby_motivo`. Rodando o arquivo isolado, só a falha conhecida aparece; reexecutando a suíte completa duas vezes depois, voltou a exatamente 1 falha conhecida + `848 passed` nas duas vezes. Tratado como flakiness pré-existente e não-determinística, documentada em `deferred-items.md`, não investigada (fora de escopo).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

`pedido_standby_motivo` passa a ser fonte real e viva: uma linha por par atualmente em stand-by, motivo canônico, `execucoes_consecutivas` de visibilidade de starvation, e a linha desaparecendo quando o par vira OR. A Phase 17 pode consultar essa tabela como fonte autoritativa de "ainda pendente" (LEFT JOIN/EXISTS) sem risco de tag errada.

**Pendências conhecidas da fase 16** (não bloqueiam esta plan, mas ficam para depois):
- Limpeza de órfãos no full refresh da ingestão (`reconstruir_pedido_produto_read`, critério 4 do ROADMAP).
- Ruído de `ruff check`/flakiness de suíte documentado em `deferred-items.md` — não é deste plano, mas vale investigar antes de a fase 17 herdar o mesmo ambiente.

---
*Phase: 16-persist-ncia-do-motivo-de-stand-by*
*Completed: 2026-08-21*
