---
phase: 2
slug: ingest-o-da-refer-ncia-convers-o-de-grade
status: ready
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-05
updated: 2026-08-05
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio |
| **Config file** | pyproject.toml |
| **Quick run command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ingestao_sync.py app/tests/test_ingestao_parse.py app/tests/test_grade_linx.py -q` |
| **Full suite command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |
| **Estimated runtime** | suíte completa ~5m22s no container (baseline real: 215 passed, 1 warning; cresce com os novos casos desta fase) |

**Nota deste repo:** `uv run` local falha no Windows (build do pyicu) — todos os comandos rodam via `docker compose exec -T api`. Testes de schema dependem de `alembic upgrade head` aplicado no banco do container.

---

## Sampling Rate

- **After every task commit:** teste do arquivo tocado (`test_ingestao_parse.py`, `test_ingestao_sync.py` ou `test_grade_linx.py`) via container
- **After every plan wave:** suíte completa no container
- **Before `/gsd-verify-work`:** suíte completa verde
- **Max feedback latency:** ~6 min (suíte completa no container)

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01 T1 (env var settings/.env.example) | 02-01 | 1 | ING-01 | T-02-01 | env var controlada pela equipe, não input HTTP | config smoke | `docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "from app.shared.config.settings import Settings; assert 'databricks_tabela_tamanho_ref' in Settings.model_fields"` | existing (estendido) | ⬜ pending |
| 02-01 T2 (`_parse_posicao`, D-01) | 02-01 | 1 | ING-01 | T-02-02 | parse tolerante, nunca lança exceção | unit (puro) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ingestao_parse.py -q -k parse_posicao` | existing (estendido) | ⬜ pending |
| 02-01 T3 (`agregar_referencia_tamanhos`/reader/repo, D-03/D-04) | 02-01 | 1 | ING-01 | T-02-02, T-02-03 | conflito de posição detectado e agregado por produto | unit (puro) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ingestao_sync.py -q -k agregar_referencia_tamanhos` | existing (estendido) | ⬜ pending |
| 02-02 T1 (`converter_grade_para_posicoes` + re-export) | 02-02 | 1 | LINX-03 | T-02-04, T-02-05 | tamanho sem posição não lança exceção | smoke (python -c) | `docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "from app.modules.pedidos.service import converter_grade_para_posicoes"` | new file | ⬜ pending |
| 02-02 T2 (testes sintéticos, critério 5/D-03) | 02-02 | 1 | LINX-03 | T-02-04, T-02-05 | conversão pura, sem I/O | unit (puro) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_grade_linx.py -q` | new file | ⬜ pending |
| 02-03 T1 (`sincronizar_referencia_tamanhos` D-02 + `sincronizar_tudo` + schema) | 02-03 | 2 | ING-01 | T-02-06 | guard de referência vazia nunca substitui | smoke (python -c) | `docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "from app.modules.ingestao.schemas import SincronizacaoResponse"` | existing (estendido) | ⬜ pending |
| 02-03 T2 (testes de integração ING-01 critérios 1-3, D-01/D-02) | 02-03 | 2 | ING-01 | T-02-06 | full refresh sem duplicação; view vazia preserva snapshot | integration (sessão real, sem commit) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_ingestao_sync.py -q` | existing (estendido) | ⬜ pending |
| 02-04 (checkpoint — sync real, evidência + captura de fixture) | 02-04 | 3 | ING-01, LINX-03 | T-02-08 | prova critérios 1/3 fora de mock; sem inventar dados | manual (curl + psql) | ver `<how-to-verify>` do plano (sequência de 8 passos) | n/a (evidência) | ⬜ pending |
| 02-05 T1 (fixture real + teste critério 4) | 02-05 | 4 | LINX-03 | T-02-09 | conversão testada sem banco, com dados reais já ingeridos | unit (puro, dados reais) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_grade_linx.py -q` | existing (estendido) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

Nenhum Wave 0 separado é necessário — cada plano cria seus próprios testes automatizados dentro da mesma task que cria o código (ou na task imediatamente seguinte, dentro do mesmo plano):

- [x] `app/tests/test_ingestao_parse.py` — estendido no Plano 02-01 T2 (parametrize de `_parse_posicao`)
- [x] `app/tests/test_ingestao_sync.py` — estendido no Plano 02-01 T3 (agregação pura) e no Plano 02-03 T2 (integração da 5ª fonte)
- [x] `app/tests/test_grade_linx.py` — criado no Plano 02-02 T2 (sintéticos) e estendido no Plano 02-05 T1 (dados reais, critério 4 — bloqueado até o checkpoint do Plano 02-04 capturar evidência real)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Sync real contra o Databricks — critérios 1 e 3 fora de mock | ING-01 | Nenhum agente automatizado deste ambiente tem credenciais Databricks reais | Plano 02-04 (checkpoint): login admin, `POST /api/v1/ingestao/sincronizar` 2x, `SELECT COUNT(*)` antes/depois via psql |
| Captura da amostra real para a fixture do teste puro (critério 4) | LINX-03 | O ROADMAP exige dados REAIS já ingeridos, não inventados; `produto_tamanho_posicao` está vazia até o 1º sync real | Plano 02-04 (checkpoint): `psql -c "SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM produto_tamanho_posicao WHERE cd_prod_cor = (...) ORDER BY sg_tamanho;"` — evidência consumida pelo Plano 02-05 |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify (única task sem `<automated>` regular é o checkpoint 02-04, que é verificação manual por natureza — não uma lacuna de amostragem)
- [x] Wave 0 covers all MISSING references (nenhum Wave 0 separado necessário — testes nascem dentro dos próprios planos)
- [x] No watch-mode flags
- [x] Feedback latency < 6 min
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** aprovado pelo planner em 2026-08-05 (5 planos, 9 tasks, cobertura completa de ING-01/LINX-03 e dos 5 critérios de sucesso do ROADMAP)
