---
phase: 16-persist-ncia-do-motivo-de-stand-by
plan: 03
subsystem: api
tags: [python, dataclass, domain, processing, standby]

requires:
  - phase: 16-01
    provides: "preteridos_motivo (6ª chave aditiva de processar_pedidos) e o vocabulário canônico SEM_CREDITO/SEM_ESTOQUE/FURO_GRADE em standby_motivo.py"
provides:
  - "PlanDraft.deferred_pairs: tuple[tuple[int, str, str, str], ...] — (nr_pedido, cd_prod_cor, canal, motivo), motivo por par"
  - "PlanDraft.blocked_credit_pairs: tuple[tuple[int, str, str], ...] — (nr_pedido, cd_prod_cor, canal), fan-out por produto elegível"
  - "Guarda de overlap em PlanDraft.__post_init__ (InvalidProcessingRequest('standby_pair_in_both_groups'))"
  - "build_processing_plan populando os dois campos a partir de preteridos/preteridos_motivo/bloqueados_credito"
  - "plan_hash (schemaVersion 2) sensível aos dois campos novos, determinístico"
affects: ["16-04"]

tech-stack:
  added: []
  patterns:
    - "Fan-out de crédito em Python puro dentro de build_processing_plan (não no writer), iterando sorted(eligible) para determinismo"
    - "Ordenação explícita (nunca iteração de set) em qualquer coleção que alimenta o dict hasheado de plan_hash"

key-files:
  created: []
  modified:
    - app/modules/pedidos/processing/domain.py
    - app/tests/test_pedidos_processing_domain.py

key-decisions:
  - "Fan-out de crédito roda em Python puro dentro de build_processing_plan, não no writer — result['bloqueados_credito'] é grão pedido, a tabela é grão par, e só build_processing_plan tem o conjunto eligible desta rodada sem reconsultar o banco"
  - "blocked_credit_count NÃO muda de semântica — continua len(set(bloqueados_credito)), contagem por pedido; o fan-out por par vive só em blocked_credit_pairs (K2/K4 coexistindo de propósito)"
  - "channel_by_pair capturado dentro do laço já existente que monta flat (sorted(eligible)), sem varredura extra — custo zero adicional mesmo no pior caso de PLAN_PAIR_LIMIT"
  - "Resolução de motivo tolerante (fallback SEM_ESTOQUE se a chave faltar em preteridos_motivo), resolução de canal estrita (raise standby_pair_not_eligible se o par não estiver em channel_by_pair) — assimetria deliberada: falta de motivo é impossível hoje (16-01 garante o invariante) e degrada bem; falta de canal seria perda silenciosa de um par inteiro"
  - "schemaVersion do plan_hash bumpado de 1 para 2 (só nesta chamada de _sha256; a de new_processing_job permanece 1, contrato diferente)"

patterns-established:
  - "Guarda de overlap entre dois grupos de pares por chave (nr_pedido, cd_prod_cor), descartando canal/motivo por desempacotamento posicional — reutilizável se surgirem novos grupos de stand-by"

requirements-completed: []

duration: ~35min
completed: 2026-08-21
---

# Phase 16 Plan 03: PlanDraft ganha deferred_pairs/blocked_credit_pairs Summary

**`PlanDraft` passa a carregar o motivo de stand-by por par (furo de grade vs sem estoque) e o fan-out de crédito por produto elegível, com `plan_hash` (schemaVersion 2) sensível aos dois campos novos.**

## Performance

- **Duration:** ~35 min
- **Started:** 2026-08-21T13:35:00Z (aprox.)
- **Completed:** 2026-08-21T13:53:00Z
- **Tasks:** 2 tasks (TDD)
- **Files modified:** 2

## Accomplishments

- `PlanDraft` ganhou `deferred_pairs` (4-tupla com motivo por par) e `blocked_credit_pairs` (3-tupla), ambos com default `()`, sem quebrar nenhum call site existente de `test_pedidos_processing_repository.py`.
- Guarda de overlap em `__post_init__`: `InvalidProcessingRequest("standby_pair_in_both_groups")` quando o mesmo `(nr_pedido, cd_prod_cor)` aparece nos dois grupos.
- `build_processing_plan` popula `deferred_pairs` com o motivo específico por par (lido de `result["preteridos_motivo"]`), provado no mesmo cenário que `furo_grade` e `sem_estoque` são valores distintos.
- `blocked_credit_pairs` implementa o fan-out por produto elegível — um pedido bloqueado por crédito com 2 produtos produz 2 linhas, enquanto `blocked_credit_count` continua contando pedidos (K2/K4 provados no mesmo teste).
- `plan_hash` inclui `deferredPairs`/`blockedCreditPairs` (schemaVersion 2), provado sensível aos campos novos e determinístico entre execuções da mesma entrada.

## Task Commits

Each task was committed atomically:

1. **Task 1: PlanDraft ganha deferred_pairs/blocked_credit_pairs + guarda de overlap** - `5c303f2` (feat)
2. **Task 2: build_processing_plan popula os dois campos (fan-out de crédito) e plan_hash os cobre** - `4a701af` (feat)

**Plan metadata:** (este commit)

## Files Created/Modified

- `app/modules/pedidos/processing/domain.py` — `PlanDraft.deferred_pairs`/`blocked_credit_pairs` (defaults `()`), guarda de overlap, import de `SEM_ESTOQUE`, `channel_by_pair` capturado no laço de `sorted(eligible)`, população de `deferred_pairs`/`blocked_credit_pairs` em `build_processing_plan`, `plan_hash` com as duas chaves novas e `schemaVersion: 2`.
- `app/tests/test_pedidos_processing_domain.py` — import de `PlanDraft`; 5 testes novos: `test_plan_draft_rejects_pair_in_deferred_and_blocked_credit`, `test_plan_draft_accepts_disjoint_standby_groups_and_freezes_them`, `test_plan_draft_standby_groups_default_to_empty_for_existing_call_sites`, `test_plan_captures_deferred_motivo_per_pair_and_credit_fan_out_per_product`, `test_plan_hash_is_sensitive_to_standby_pairs_and_deterministic`.

## Decisions Made

- Fan-out de crédito em Python puro dentro de `build_processing_plan`, não no writer (ver `key-decisions` no frontmatter) — decisão de desenho já fechada no `<objective>` do plano, apenas confirmada na execução.
- `blocked_credit_count` mantém a semântica de contagem por pedido; nenhuma tentativa de "corrigir" para `len(blocked_credit_pairs)`.
- Assimetria de resolução (motivo tolerante com fallback, canal estrito com `raise`) implementada exatamente como especificado — documentada inline no código com o raciocínio completo.

## Deviations from Plan

None - plan executado exatamente como escrito. Todas as ações, nomes de teste e cenários seguiram literalmente o `<behavior>`/`<action>` de cada task.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `16-04` (port `record_standby_reasons` + upsert + wiring transacional) agora tem as duas dependências satisfeitas (`16-02` schema, `16-03` este plano) e pode consumir `draft.deferred_pairs`/`draft.blocked_credit_pairs` diretamente, com a garantia estrutural de que os dois grupos são disjuntos por par (guarda de overlap) e o canal já vem normalizado ("Franquia"/"Multimarca", nunca "TODOS").
- Nenhuma persistência real entrou neste plano — `record_standby_reasons`, `service.py`, `ports.py`, `repository.py` e `motor_adequacao.py` permanecem intocados, confirmado por grep e `git diff --name-only`.
- Nota para depuração futura: `plan_hash` mudou de forma (schemaVersion 1 → 2) nesta chamada de `_sha256`; um job planejado antes deste deploy e replanejado depois produzirá hash diferente e cairia em `ProcessingPlanConflict` (`repository.py:121`) — sem impacto prático em ambiente de desenvolvimento (planos são efêmeros), mas é a informação que explica o sintoma se ele aparecer em produção após o deploy deste plano.

---
*Phase: 16-persist-ncia-do-motivo-de-stand-by*
*Completed: 2026-08-21*

## Self-Check: PASSED

- FOUND: app/modules/pedidos/processing/domain.py
- FOUND: app/tests/test_pedidos_processing_domain.py
- FOUND: .planning/phases/16-persist-ncia-do-motivo-de-stand-by/16-03-SUMMARY.md
- FOUND commit: 5c303f2 (Task 1)
- FOUND commit: 4a701af (Task 2)
