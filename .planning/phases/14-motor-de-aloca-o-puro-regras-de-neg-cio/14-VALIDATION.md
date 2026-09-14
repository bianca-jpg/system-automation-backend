---
phase: 14
slug: motor-de-aloca-o-puro-regras-de-neg-cio
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-14
---

# Phase 14 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (já instalado) |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_motor.py -q` |
| **Full suite command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |
| **Estimated runtime** | ~5 s (quick, domínio puro sem banco) · ~3–5 min (full) |

> ⚠️ `uv run` local falha no Windows (pyicu). **Sempre** rodar por Docker.

---

## Sampling Rate

- **After every task commit:** `{quick run command}` — o domínio é puro, o feedback é de segundos
- **After every plan wave:** `{full suite command}` — **obrigatório**, não opcional nesta fase (ver risco abaixo)
- **Before `/gsd-verify-work`:** suíte completa verde
- **Max feedback latency:** 5 s (quick) / 300 s (full)

### Por que a suíte completa é obrigatória a cada wave nesta fase

Dois arquivos **fora** de `test_pedidos_motor.py` fazem `monkeypatch` sobre a assinatura atual de `adequar_grade_produto`:

- `app/tests/test_pedidos_motor_performance_024.py`
- `app/tests/test_pedidos_processing_cancellation_024.py`

Eles quebram **silenciosamente** quando a assinatura muda e **não aparecem** rodando só o arquivo de negócio. Rodar apenas o quick command entre waves cria falso verde.

### Exceção única e documentada: wave 4 (plano 14-06)

*Registrada em 2026-08-14 após achado do `gsd-plan-checker`, para os dois artefatos travados da fase não se contradizerem.*

A wave 4 é a **única** exceção à regra acima, e é **por desenho, não por acidente**. O plano 14-06 muda a assinatura de `adequar_grade_produto` (passa a receber `ledger: OrcamentoPedido` em vez de `tolerancia: float`), o que quebra de forma **esperada e antecipada**:

- **2 arquivos de assinatura fixa** — `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` (fazem `monkeypatch` da assinatura)
- **3 testes de regra antiga** em `test_pedidos_motor.py` — provam a tolerância `ceil` por produto isolado, regra que **deixou de existir** com ALOC-07/08

O conserto dos 5 é escopo do plano **14-07** (wave 5), não do 14-06. Portanto:

- O comando de verify da wave 4 reporta `0 failed` **somente após** aplicar os `--deselect`/`-k` nominais listados no próprio `14-06-PLAN.md`.
- Essa é a **única** wave autorizada a terminar com falhas conhecidas. Qualquer outra wave terminando vermelha é falha real, não exceção.
- Ao fim da **wave 5** a suíte completa volta a ser verde **sem nenhuma exclusão** — esse é o critério de saída da fase, e não admite `--deselect`.

Se durante a execução da wave 4 aparecer qualquer falha **fora** dessas 5 nomeadas, isso é regressão real e a wave não passa.

---

## Per-Task Verification Map

> Preenchido pelo planner ao criar os PLAN.md. Toda task de regra de negócio precisa de comando automatizado.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| _(planner preenche)_ | — | — | — | — | — | — | — | — | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Invariantes do domínio (asserções que devem valer sempre)

Estas são o coração da validação desta fase. Cada uma é verificável sem banco.

| # | Invariante | Requirement |
|---|---|---|
| I1 | A soma reservada de um `(produto, tamanho)` **nunca** excede o estoque disponível daquele canal | ALOC-02, ALOC-03 |
| I2 | Pedido sem crédito **nunca** aparece em `selecionados` nem em `pares_processados`, e **não** decrementa estoque | ALOC-01 |
| I3 | No modo sem adequação, todo par selecionado tem `qt_liquida == qt_solicitada` em **todos** os seus tamanhos | ALOC-02 |
| I4 | Par com furo de grade (tamanho estritamente entre o menor e o maior pedido com 0 reservável) **nunca** aparece em `selecionados` | ALOC-04 |
| I5 | Somando execuções sucessivas do mesmo pedido, o total adicionado **nunca** excede `floor(total_original × 0,05)` — e o total cortado idem, **contados separadamente** | ALOC-07, ALOC-08, ALOC-09 |
| I6 | Rodar o mesmo cenário duas vezes produz plano idêntico, inclusive com empate exato de prioridade | ALOC-06 |
| I7 | Após recalcular valores, `sum(vl_liquido dos itens) == valor total rateado`, sem sobra nem falta de centavo | FIX-02 |
| I8 | Todo produto do pedido que tinha alocação viável recebe o mínimo antes de qualquer extra ser distribuído | ALOC-10 |
| I9 | As chaves de retorno de `processar_pedidos` continuam sendo exatamente `resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados` | contrato |

---

## Cenários nomeados (obrigatórios)

| Cenário | Descrição | Esperado |
|---|---|---|
| **Disputa da mantenedora** | Cliente A: 10 peças de um produto, pedido global de 1.000 pçs / R$ 50.000. Cliente B: 15 peças, pedido de 50 pçs / R$ 1.000. Estoque = 20 | A atendido; B em stand by. **Nos dois modos** |
| **Furo no meio** | Cliente pede PP, M, G; estoque de M = 0 | Stand by, mesmo com PP e G sobrando |
| **Não é furo** | Cliente pede PP e GG, sem pedir M; M zerado | Reserva normalmente — M não foi pedido |
| **Tamanho único** | Cliente pede só M | Nunca é furo |
| **Tamanho desconhecido** | `get_tamanho_idx` devolve 999 | Descartado da ordenação; não vira furo nem define extremo |
| **Orçamento atravessa produtos** | Pedido de 100 pçs em 3 produtos, cortes 2+2+2 | Terceiro par em stand by; total cortado ≤ 5 |
| **Não se compensam** | Pedido que já cortou 5 | Ainda pode adicionar até 5 |
| **Double-spend** | Mesmo pedido processado em 2 execuções | Orçamento da 2ª rodada é o que sobrou da 1ª, não 5% novos |
| **Furo sensível à ordem** | Pedido prioritário zera o M; o seguinte pede M | O seguinte cai por furo (prova que a checagem roda dentro do laço) |
| **Empate de prioridade** | Dois pedidos com valor idêntico | Ordem estável e reproduzível entre execuções |
| **Permutação de produtos** | Mesmo pedido, produtos em ordem de entrada diferente | Resultado idêntico |

---

## Wave 0 Requirements

- [ ] Factory/fixture de itens de pedido (`nr_pedido`, `cd_prod_cor`, `sg_tamanho`, `qt_liquida`, `vl_liquido`, `status_credito`, `canal`) — hoje os testes montam dicionários à mão, e a fase precisa de muitos cenários
- [ ] Factory de estoque por canal (`{canal: {f"{cd}_{tam}": qt}}`)
- [ ] Helper de asserção para as invariantes I1–I9, reutilizável pelos testes de propriedade da Phase 19

*A infraestrutura de teste em si já existe (pytest configurado, `app/tests/conftest.py`). Wave 0 aqui é só fixture, não instalação.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| — | — | — | — |

*Todo comportamento desta fase é domínio puro e tem verificação automatizada. Nada manual.*

---

## Riscos de validação específicos desta fase

1. **Falso verde por suíte parcial** — ver seção de sampling. Mitigação: suíte completa a cada wave.
2. **Teste que espelha a implementação** — a reescrita muda a estrutura interna; testes que assertam detalhes internos (e não regra) devem ser reescritos como asserção de comportamento, não adaptados mecanicamente.
3. **Mudança de regra disfarçada de fix** — o `ceil` (corte) vs `floor` (adição) assimétricos de hoje viram `floor` nos dois, sobre o pedido inteiro. Isso **não** é só o fix de arredondamento da Phase 13: é mudança de regra (ALOC-08) e invalida o teste antigo de "falta além da tolerância". O plano precisa reescrever esse teste conscientemente, não "consertar" até passar.

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s (quick) / 300s (full)
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
