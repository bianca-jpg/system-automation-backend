---
phase: 16
slug: persist-ncia-do-motivo-de-stand-by
status: draft
nyquist_compliant: true
wave_0_complete: true
created: 2026-08-20
---

# Phase 16 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (já instalado) |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py app/tests/test_pedidos_motor_furo_grade.py app/tests/test_pedidos_processing_domain.py app/tests/test_pedidos_processing_repository.py -q` |
| **Full suite command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |
| **Estimated runtime** | ~30 s (quick) · ~2 min 30 s (full) |

> ⚠️ `uv run` local falha no Windows (pyicu). **Sempre** rodar por Docker.

---

## LINHA DE BASE — confirmada ao fim da Phase 15 (2026-08-20)

**826 passed, 16 skipped, 1 failed.** A única falha é `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (teardown asyncpg, pré-existente). **Nunca conserte.**

**Verde nesta fase = 826 + N novos, com exatamente a mesma 1 falha conhecida, sem `--deselect`/`-k`.** Confirmar a linha de base real rodando a suíte completa antes de tocar qualquer arquivo — não assumir este número como verdade absoluta se o tempo passar entre o planejamento e a execução.

---

## Per-Task Verification Map

> Preenchido pelo planner ao criar os PLAN.md.

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------|-------------------|--------|
| _(planner preenche)_ | — | — | — | — | — | ⬜ pending |

---

## Critérios de sucesso do ROADMAP → como provar

| # | Critério | Prova |
|---|---|---|
| 1 | Motivo de qualquer par consultável, sem crescer o retorno do job | Teste em `test_pedidos_processing_repository.py`: chama `record_standby_reasons`, lê de volta via SELECT direto na tabela nova, e separadamente confere que `ProcessingResult`/o payload do job (4 contadores) não ganhou campo novo |
| 2 | Par em stand by consecutivo atualiza (não duplica), contador incrementa | Duas chamadas sucessivas de `record_standby_reasons` sobre o mesmo par: `COUNT(*) == 1` (upsert, não insert duplo) e `execucoes_consecutivas` vai de 1 para 2 |
| 3 | Par que recebe OR some da consulta | Teste de integração: par em `pedido_standby_motivo`, roda `apply_pairs` selecionando aquele par, confere `SELECT` vazio para aquele `(nr_pedido, cd_prod_cor)` depois |
| 4 | Full refresh limpa órfãos | Par em `pedido_standby_motivo` cujo `(nr_pedido, cd_prod_cor)` não existe mais em `pedido_produto_read` com `source='pending'`; roda `reconstruir_pedido_produto_read`; confere que a linha órfã foi removida |

---

## Invariantes desta fase

| # | Invariante | Requirement |
|---|---|---|
| K1 | `preteridos_motivo[par]` distingue `'furo_grade'` de `'sem_estoque'` nos **dois** modos (`ADEQUAR` e `SEM_ADEQUAR`) | STANDBY-01 |
| K2 | Crédito bloqueado gera uma linha `motivo='sem_credito'` para **todos** os produtos daquele pedido no conjunto elegível da rodada — não só os que apareceriam no plano | STANDBY-01 (prepara STANDBY-02 da Phase 17) |
| K3 | `preteridos`/`bloqueados_credito`/`resultados`/`selecionados`/`pares_processados` — as 5 chaves de retorno de `processar_pedidos` — não mudam de forma. `preteridos_motivo` é campo **aditivo**, novo | contrato protegido |
| K4 | O payload do job (`ProcessingResult`) continua limitado aos 4 contadores — nenhuma lista de itens/pares vaza para lá | STANDBY-01 |
| K5 | `CHECK (motivo IN (...))` da tabela nova aceita os **três** valores desde a criação: `'sem_credito'`, `'sem_estoque'`, `'furo_grade'` — nunca só dois | achado desta fase |
| K6 | Upsert nunca perde uma linha por corrida — `ON CONFLICT DO UPDATE`, nunca delete-then-insert | STANDBY-01/06 |
| K7 | Migration é reversível (`downgrade()` remove a tabela sem erro) | convenção do repo |

---

## ⚠ Caso obrigatório: o motivo do furo de grade não pode se perder

**Achado nesta sessão, confirmado pela pesquisa da fase:** o motor já sabe diferenciar furo de grade de falta de estoque genérica (`tem_furo_de_grade` devolve o motivo certo), mas isso é descartado antes de chegar em `processing/`, porque `build_processing_plan` chama `processar_pedidos(..., resultados_apenas_selecionados=True)`.

**Teste obrigatório:** com o `preteridos_motivo` novo, montar um cenário com um par barrado por furo de grade e outro barrado por falta de estoque genérica (sem furo), nos dois modos, e confirmar que a tabela final grava motivos **diferentes** para os dois — não o mesmo valor genérico. Sem esse teste, é fácil a implementação colapsar os dois em `'sem_estoque'` e a Phase 17 nunca vai ter dado para cumprir STANDBY-04.

---

## Achado colateral da pesquisa — código morto no ramo "sem canal"

A pesquisa encontrou um 4º ponto que escreve em `preteridos` (`motor_adequacao.py:524-546`, ramo "canal não identificado"), mas confirmou que é **código morto** no caminho real de `build_processing_plan` — `_eligible_pairs` já garante canal canônico por par antes do motor rodar. Não é bloqueante; se o planner decidir tratar esse ramo defensivamente (ex. mapear para um motivo genérico "canal_invalido" só por precaução), documentar a decisão — não é obrigatório.

---

## Wave 0 Requirements

- [ ] Fixture `processing_database` (já existe em `test_pedidos_processing_repository.py`) precisa incluir o model novo na lista de cleanup entre testes
- [ ] Nenhuma dependência nova, nenhum framework novo

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | — | — |

*Todo comportamento desta fase é backend puro (domínio + banco), com verificação automatizada.*

---

## Riscos de validação específicos desta fase

1. **Colapsar furo de grade em "sem estoque"** — ver seção acima. É o risco central desta fase.
2. **DDL com só 2 motivos no `CHECK`** — a pesquisa do milestone (`ARCHITECTURE.md`) tinha esse DDL desatualizado. Se a migration copiar aquele DDL sem revisar, o terceiro valor falta e uma segunda migration seria necessária para corrigir depois. Verificar o `CHECK constraint` explicitamente no teste de migration.
3. **STANDBY-06 é `[ASSUMED]`, não travado** — a pesquisa assumiu que o contador é agnóstico ao motivo (não reseta quando o motivo muda, só quando a linha é apagada). Isso é uma leitura razoável do texto do requirement, mas é uma suposição de negócio. Se o executor perceber ambiguidade real durante a implementação, registrar no SUMMARY em vez de decidir silenciosamente.
4. **Upsert com corrida** — dois processamentos simultâneos do mesmo par (não deveria acontecer dado o advisory lock de mutação já existente no projeto, mas vale um teste de sanidade se for barato).

---

## Validation Sign-Off

- [x] Todas as tasks têm verificação automatizada ou dependência de Wave 0 — confirmado pelo `gsd-plan-checker` (2026-08-20): os 5 planos têm `<automated>` verify em toda task
- [x] Nenhuma wave termina vermelha — nenhum plano usa `--deselect`/`-k`
- [x] `preteridos_motivo` distingue furo de grade de estoque insuficiente nos dois modos — cadeia 16-01→16-03→16-04 verificada ponta a ponta sem colapso, incluindo a exigência de zero ocorrências das strings de motivo fora da leitura da tupla em `repository.py`
- [x] `CHECK` da migration aceita os 3 valores — acceptance criteria explícita no 16-02, testada positivo/negativo
- [x] `nyquist_compliant: true` no frontmatter

**Approval:** approved 2026-08-20 (gsd-plan-checker: PASSED com 2 warnings não-bloqueantes — ver `16-RESEARCH.md` e nota de chunking no 16-04-PLAN.md)
