---
phase: 3
slug: escrita-da-or-no-formato-linx
status: planned
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-05
updated: 2026-08-05
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_routes.py -q` |
| **Full suite command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |
| **Estimated runtime** | suíte completa ~4 min no container |

**Notas deste repo:**
- `uv run` local falha no Windows (build do pyicu) — tudo via `docker compose exec -T api`.
- `ruff` NÃO é executável no container (permission denied) — a suíte é o gate.
- Os testes conectam num Postgres REAL, sem banco efêmero: testes de escrita nunca commitam
  (verificam dentro da própria transação e fazem rollback/flush), padrão já estabelecido em
  `test_ingestao_sync.py` e `test_pedidos_routes.py`.
- Baseline móvel: a contagem de testes inclui o trabalho de outras sessões. Comparar antes/depois
  na mesma sessão, nunca contra um número fixo memorizado.

---

## Sampling Rate

- **After every task commit:** teste do arquivo tocado via container
- **After every plan wave:** suíte completa no container
- **Before `/gsd-verify-work`:** suíte completa verde
- **Max feedback latency:** ~4 min (suíte completa)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 03-01-T1 | 03-01 | 1 | LINX-02 | T-03-01, T-03-02 | `montar_linha_linx` decide D-01 via None ANTES de converter; D-03 (soma exata + arredondamento); split defensivo | unit (puro, smoke) | `docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx; ..."` | ❌ (criado nesta task) | ⬜ pending |
| 03-01-T2 | 03-01 | 1 | LINX-02 | T-03-01, T-03-02 | 8 testes puros: D-01 (zero vs. parcial), D-03 (com/sem divergência de centavos), split defensivo, critério 5 | unit (puro) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ordem_reserva_linx.py -v` | ❌ (criado nesta task) | ⬜ pending |
| 03-02-T1 | 03-02 | 1 | LINX-02 | T-03-03 | `obter_referencia_posicoes_por_produtos` filtra por rodada, chunk 500, nunca a tabela inteira | integration (sessão real, read-only) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ingestao_api_leitura.py -v -k referencia_posicoes` | ❌ (criado nesta task) | ⬜ pending |
| 03-02-T2 | 03-02 | 1 | LINX-02 | T-03-04 | `salvar_linhas_linx` — upsert SELECT+decide, critério 4 (não duplica) | integration (sessão real, flush sem commit) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_routes.py -v -k salvar_linhas_linx` | ❌ (criado nesta task) | ⬜ pending |
| 03-03-T1 | 03-03 | 2 | LINX-02 | T-03-05 | Wiring nos 2 fluxos antes do commit; `executar_alteracao_grade` intocado (D-02 revista); re-export D-02a | smoke (import) | `docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "from app.modules.pedidos.service import montar_linha_linx, salvar_linhas_linx; print('OK')"` | ❌ (criado nesta task) | ⬜ pending |
| 03-03-T2 | 03-03 | 2 | LINX-02 | T-03-05, T-03-06 | Critérios 1/2 (campos corretos, mockado) e critério 3 (rollback conjunto, sessão real) | integration (mockado + sessão real) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_routes.py -v` | ❌ (extensão criada nesta task) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Gaps identificados em `03-RESEARCH.md` §"Wave 0 Gaps" e como cada um é fechado por este set de planos:

- [x] `app/modules/pedidos/domain/ordem_reserva_linx.py` — criado no Plano 03-01, Task 1
- [x] `app/tests/test_ordem_reserva_linx.py` — criado no Plano 03-01, Task 2
- [x] `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py` — criado no Plano 03-02, Task 2
- [x] `obter_referencia_posicoes_por_produtos` em `app/modules/ingestao/api_leitura.py` — criado no Plano 03-02, Task 1
- [x] Casos novos em `app/tests/test_pedidos_routes.py` (upsert, mocks dos 2 fluxos, rollback conjunto) — Planos 03-02 (Task 2) e 03-03 (Task 2)

Nenhum gap do RESEARCH.md ficou sem task correspondente. O 3º ponto de integração descrito no RESEARCH.md (`executar_alteracao_grade`) foi excluído do escopo por D-02 revista (03-CONTEXT.md) — não é um gap, é uma decisão de escopo documentada e verificada por acceptance_criteria negativo no Plano 03-03 (`grep -A40 "def executar_alteracao_grade" | grep -c "salvar_linhas_linx"` == `0`).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | — | Nenhuma verificação manual necessária nesta fase. |

Ao contrário da Fase 2, esta fase NÃO depende de sync real: a referência de posição já está
populada (569.726 linhas, ingerida na Fase 2) e a gravação Linx é inteiramente testável com
sessão real sem commit (flush + rollback implícito ao sair do `async with`) ou com mocks via
HTTP. Todos os 5 critérios de sucesso do ROADMAP são verificáveis por teste automatizado.

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (todas as 6 tasks têm `<automated>`)
- [x] Wave 0 covers all MISSING references (ver seção acima)
- [x] No watch-mode flags
- [x] Feedback latency < 4 min
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** planned — 3 planos (03-01, 03-02, 03-03) cobrindo os 5 critérios de sucesso do ROADMAP e o requirement LINX-02.
