---
phase: 15-roteamento-global-do-modo-sem-adequa-o
plan: 04
subsystem: testing
tags: [pytest, resource, memory-profiling, performance]

# Dependency graph
requires:
  - phase: 15-03
    provides: "SEM_ADEQUAR roteado pelo caminho global (build_processing_plan -> processar_pedidos), streaming removido, guarda bloqueante deste plano satisfeita"
provides:
  - "Primeira instrumentação de memória automatizada do repositório: app/tests/test_pedidos_processing_sem_adequar_memory_024.py, medindo resource.getrusage(RUSAGE_SELF).ru_maxrss de build_processing_plan em modo SEM_ADEQUAR sobre dataset sintético de 45.000 pares/112.500 itens"
  - "Número real de pico de memória medido e documentado: delta_total_mib=630.9 MiB, delta_chamada_mib=540.7 MiB, dentro do teto de calibração de 700.0 MiB (margem real ~9.9%)"
affects: [15-05-docs]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Medição de pico de RSS via resource.getrusage(RUSAGE_SELF).ru_maxrss antes/depois de cada fase (hidratação do dataset, chamada à função sob medição) para isolar o delta incremental de cada etapa, não só o pico acumulado do processo inteiro"
    - "Asserções de volume do dataset sintético (candidate_count/rows/deferred/blocked) SEMPRE antes da asserção de memória, para nunca mascarar um dataset incompleto como 'memória OK'"
    - "Calibração consciente de teto de memória: primeira execução real decide se o teto documentado como 'referência inicial' fica como está (Passo 2a) ou é recalibrado com margem real (Passo 2b) — nunca afrouxado silenciosamente"

key-files:
  created:
    - app/tests/test_pedidos_processing_sem_adequar_memory_024.py
  modified: []

key-decisions:
  - "Tasks 1 e 2 do plano foram combinadas em um único commit: a medição passou de primeira contra o teto de calibração inicial (700.0 MiB), então o docstring calibrado com o número real já existia antes do primeiro commit — não havia uma versão intermediária 'não calibrada' que fizesse sentido commitar separadamente"
  - "Teto _MEMORY_CEILING_MIB mantido em 700.0 (não recalibrado), conforme Passo 2a do plano — a asserção passou com margem real de ~9.9% sobre o pico observado (630.9 MiB)"
  - "Dataset sintético usa produto único por pedido (cd_prod_cor = f'SKU{i:06d}' distinto por par) para estressar _indexar_estoque_por_produto com a cardinalidade máxima de produtos distintos, o cenário de maior custo do índice"
  - "Estoque abundante e crédito liberado em 100% do dataset, para medir o cenário de PICO de payload materializado (todo par gera OR), não o cenário mais leniente de stand-by"

patterns-established: []

requirements-completed: []

# Metrics
duration: ~15min
completed: 2026-08-20
---

# Phase 15 Plan 04: Medição de pico de memória do modo sem adequação Summary

**Primeira instrumentação de memória automatizada do repositório: `resource.getrusage(RUSAGE_SELF).ru_maxrss` mede o pico real de RSS de `build_processing_plan` em modo `SEM_ADEQUAR` sobre um dataset sintético de 45.000 pares/112.500 itens — pico observado de 630.9 MiB, dentro do teto de calibração de 700.0 MiB (margem real ~9.9%).**

## Performance

- **Duration:** ~15 min
- **Tasks:** 2/2 (combinadas em 1 commit — ver Decisões)
- **Files modified:** 1 (novo)

## Accomplishments

- **Guarda bloqueante confirmada antes de escrever qualquer código**: releitura de `app/modules/pedidos/processing/domain.py::build_processing_plan` confirmou que o ramo `SEM_ADEQUAR` já chama `processar_pedidos(..., modo=modo)` incondicionalmente (roteamento global entregue por 15-03) — a pré-condição descrita no `<objective>` do plano estava satisfeita.
- **Arquivo novo `app/tests/test_pedidos_processing_sem_adequar_memory_024.py`**: dataset sintético gerado em memória (`_construir_canal_sintetico`) com 45.000 pares (pedido, produto) distintos, produto único por pedido, alternando 2/3 tamanhos por par (112.500 itens totais), estoque abundante (10 unidades por chave) e crédito liberado ("Com Crédito") — cenário de pico de payload materializado, onde todo par gera OR via `aplicar_tudo_ou_nada`.
- `resource.getrusage(RUSAGE_SELF).ru_maxrss` capturado em 3 pontos: antes da hidratação do dataset, depois da hidratação, e depois da chamada a `build_processing_plan` — permitindo isolar `delta_chamada_mib` (só a chamada) de `delta_total_mib` (hidratação + chamada, mais fiel ao texto do critério 4 "processar um canal inteiro").
- Asserções de volume (`candidate_count == 45_000`, `len(rows) == 45_000`, `deferred_count == 0`, `blocked_credit_count == 0`) executadas ANTES da asserção de memória, para nunca mascarar um dataset incompleto ou parcial como "memória OK".
- Guard `pytest.mark.skipif(sys.platform != "linux", ...)` no teste, documentando no docstring do módulo que `resource` existe em qualquer POSIX (Linux/macOS), mas `ru_maxrss` reporta unidades diferentes entre eles (KiB vs. bytes) — o motivo real de restringir a comparação a Linux, mais preciso que um simples "ImportError fora de Linux".
- **Pico real medido, isolado via Docker**: `delta_total_mib=630.9 MiB`, `delta_chamada_mib=540.7 MiB`, contra o teto de calibração inicial `_MEMORY_CEILING_MIB=700.0`. A asserção passou de primeira (margem real ~9.9%); o teto **não foi alterado** — só o docstring do módulo foi atualizado com o número real, a data e a decisão de manter o teto (Passo 2a do plano).

## Task Commits

1. **Tasks 1+2 combinadas: criar o teste de medição + calibrar conscientemente o teto** - `5e0f553` (test)

**Plan metadata:** (este commit, `docs(15-04): complete plan`)

## Files Created/Modified

- `app/tests/test_pedidos_processing_sem_adequar_memory_024.py` - novo arquivo: dataset sintético (`_construir_canal_sintetico`), guard de plataforma, medição de `ru_maxrss` em 3 pontos, asserções de volume antes da asserção de memória, teto `_MEMORY_CEILING_MIB=700.0` documentado com o número real observado

## Decisions Made

- **Tasks 1 e 2 combinadas em um único commit**: o plano descreve Task 1 (criar o teste, com um docstring citando o teto como "não calibrado ainda") e Task 2 (rodar, medir o número real, e só então atualizar o docstring/decidir sobre o teto) como passos sequenciais com commits separados. Como a medição da Task 1 já passou de primeira contra o teto de calibração inicial, o docstring calibrado com o número real foi escrito antes do primeiro commit — não fazia sentido commitar uma versão "não calibrada" artificial só para separar as duas tasks. O resultado funcional (arquivo final, comportamento, número real documentado) é idêntico ao que as duas tasks sequenciais teriam produzido.
- **Teto `_MEMORY_CEILING_MIB` mantido em 700.0** (Passo 2a do plano): a asserção passou com margem real de ~9.9% sobre o pico observado (630.9 MiB) — nenhuma necessidade de recalibrar.
- **Achado a registrar (não bloqueante)**: o pico real de `sem_adequar` (630.9 MiB) fica **acima** da extrapolação de ~600 MiB documentada em `processing/domain.py` (linhas 40-41) para o pior caso do modo `ADEQUAR` — consistente com a Assumption A2 de `15-RESEARCH.md` (o volume elegível de `sem_adequar` pode ser estruturalmente maior por não filtrar crédito antes do roteamento pelo caminho global). A margem de ~9.9% sobre o teto de calibração é mais apertada que outros números medidos neste projeto; folgadamente dentro do orçamento reservado ao worker dedicado (1280-1536 MiB, per comentário de `processing/domain.py`), mas vale reacompanhar se o volume elegível real de produção se aproximar do hard cap `PLAN_ITEM_LIMIT=200_000` (o dataset deste teste usa 112.500 itens, bem abaixo do teto). Não é um bloqueio de entrega — é o resultado esperado de medir, pela primeira vez, um caminho que nunca tinha instrumentação: nenhuma ação corretiva é exigida pelo critério 4 do ROADMAP, que só pede que o pico "permaneça dentro do orçamento reservado para o worker", o que se confirma com folga ampla.

## Deviations from Plan

None relevante às regras 1-4 de deviation. A única divergência da execução literal do plano foi de **granularidade de commit** (Tasks 1+2 em um commit em vez de dois), documentada acima em "Decisions Made" — não é um bug, funcionalidade ausente, correção bloqueante nem mudança arquitetural; é uma consequência direta de a medição ter passado na primeira tentativa.

## Issues Encountered

**Execução paralela do Plano 15-05 durante esta sessão.** Ao iniciar a verificação de `git status --short` antes do commit, a working tree já continha `.planning/STATE.md` modificado e `.planning/phases/15-roteamento-global-do-modo-sem-adequa-o/15-05-SUMMARY.md` não commitado — resultado de outra sessão/agente executando o Plano 15-05 (`docs/adequacao.md`) em paralelo a esta, já com os dois commits de código (`8e73b90`, `1230212`) no histórico. Ambos os planos só dependem formalmente de `15-03` (já fechado), por isso puderam avançar em paralelo sem conflito real de arquivos (15-05 tocou só `docs/adequacao.md`; 15-04 tocou só o teste novo). Durante a preparação deste commit de fechamento, a outra sessão concluiu seu próprio commit final (`e355712`, atualizando `STATE.md`/`ROADMAP.md`/`15-05-SUMMARY.md`), então o trabalho de 15-05 já estava integralmente commitado antes deste commit de 15-04 tocar `STATE.md`/`ROADMAP.md` — nenhuma reconciliação manual de conflito foi necessária, e o trabalho de 15-05 foi preservado sem alteração.

## User Setup Required

None - nenhuma configuração de serviço externo necessária.

## Next Phase Readiness

- **Fase 15 fechada (5/5 planos)**: 15-01 (leitura de orçamento) → 15-02 (FIX-01) → 15-03 (roteamento global + ALOC-09 + remoção do streaming) → 15-05 (docs) → 15-04 (esta entrega, medição de memória). Os 5 critérios de sucesso do ROADMAP da Fase 15 estão satisfeitos, incluindo o critério 4 (pico de memória medido e dentro do orçamento do worker).
- **Requirements da fase (ARCH-01, FIX-01)** já estavam marcados completos desde 15-03/15-02; este plano não tinha requirements próprios (`requirements: []` no frontmatter do `15-04-PLAN.md`).
- **Próximo passo**: `/gsd-plan-phase 16` (persistência do motivo de stand by), que depende de Phase 14 e Phase 15, ambas agora completas. Phase 13 (guarda contra OR zerada) permanece TBD e paralelizável.
- **Achado a acompanhar (não bloqueante)**: margem de calibração de memória mais apertada (~9.9%) que outros pontos medidos do sistema — reavaliar se o volume elegível real de produção de `sem_adequar` crescer significativamente (ver "Decisions Made" acima).

## Self-Check: PASSED

- `app/tests/test_pedidos_processing_sem_adequar_memory_024.py` - FOUND
- Commit `5e0f553` - FOUND in `git log --oneline --all`
- `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_processing_sem_adequar_memory_024.py -q -s` - `1 passed`, delta real impresso
- `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` (suíte completa) - `826 passed, 16 skipped, 1 failed` (825 baseline + 1 novo; mesma falha conhecida)

---
*Phase: 15-roteamento-global-do-modo-sem-adequa-o*
*Completed: 2026-08-20*
