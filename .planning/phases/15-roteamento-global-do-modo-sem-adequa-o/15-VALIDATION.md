---
phase: 15
slug: roteamento-global-do-modo-sem-adequa-o
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-08-17
---

# Phase 15 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest + pytest-asyncio (já instalado) |
| **Config file** | `pyproject.toml` |
| **Quick run command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest app/tests/test_pedidos_processing_domain.py app/tests/test_pedidos_processing_service.py app/tests/test_pedidos_processing_repository.py -q` |
| **Full suite command** | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` |
| **Estimated runtime** | ~40 s (quick) · ~2 min 30 s (full) |

> ⚠️ `uv run` local falha no Windows (pyicu). **Sempre** rodar por Docker.

---

## LINHA DE BASE REAL — medida em 2026-08-17, ao fim da Phase 14

**825 passed, 16 skipped, 1 failed.**

A única falha é `app/tests/test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (`RuntimeError: Event loop is closed`, teardown de conexão asyncpg). Pré-existente, na camada de leitura/infra, sem relação com esta fase. **Não consertar.**

**Verde nesta fase = exatamente 1 falha, e é essa. Sem `--deselect`, sem `-k`.** Diferente da Phase 14, aqui **nenhuma wave está autorizada a terminar vermelha** — a exceção da wave 4 valia só para o plano 14-06 e acabou.

> Correção de um dado da pesquisa: `15-RESEARCH.md` cita a base como "820 passed / 6 failed", com a ressalva de que o plano 14-07 podia não ter rodado. Ele rodou e fechou a Phase 14. **A base correta é 825 passed / 1 failed.**

---

## Sampling Rate

- **Após cada commit de task:** comando quick (arquivos de `processing/`)
- **Ao fim de cada wave:** suíte completa
- **Gate da fase:** suíte completa verde (1 falha conhecida) antes da verificação

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
| 1 | `SEM_ADEQUAR` usa a mesma pipeline do `ADEQUAR`; nenhuma decisão depende de em qual página um par caiu | Reescrever `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` — hoje ele prova **o oposto** (paginação). O teste novo prova hidratação global |
| 2 | O construtor de plano por streaming não existe mais no repositório | Verificação estática: `grep -rn "StreamingPlanBuilder\|PLANNING_PAIR_PAGE_SIZE\|prepare_pending_pair_stream\|load_pending_pair_page\|append_plan_rows\|finalize_streamed_plan" app/modules app/tests` retorna **vazio** |
| 3 | Plano de `sem_adequar` que exceda o estoque na escrita é rejeitado, igual ao `adequar` | Teste novo espelhando `test_adequation_revalidates_stock_before_writes`, com `tipo="sem"` (FIX-01) |
| 4 | Pico de memória do canal inteiro em `sem_adequar` cabe no orçamento do worker | Instrumentação **nova** — ver seção abaixo |
| 5 | Orçamento ±5% usa dados reais; 2 execuções não concedem 5% novos; o aviso ALOC-09 some do log | Teste de integração de duas execuções sucessivas + `caplog` provando ausência do aviso |

---

## Invariantes desta fase

| # | Invariante | Critério |
|---|---|---|
| J1 | Nenhum par sem crédito entra em OR no modo `sem_adequar` | 1 |
| J2 | Nenhum par com grade incompleta (furo) entra em OR no modo `sem_adequar` | 1 |
| J3 | `deferred_count` e `blocked_credit_count` do job deixam de ser 0 no modo `sem_adequar` quando há pares recusados | 1 |
| J4 | Aplicar plano `tipo="sem"` acima do estoque disponível é **rejeitado** | 3 |
| J5 | O denominador do orçamento inclui pares já convertidos em OR que **sumiram** de `pedidos` | 5 |
| J6 | O `logger.warning` de ALOC-09 **não é emitido** quando os dados reais são fornecidos | 5 |
| J7 | Nenhum símbolo de streaming permanece no repositório | 2 |

---

## ⚠ Caso obrigatório: o denominador que encolhe (J5)

**Verificado no banco em 2026-08-17, não é hipótese.** De 4 pares com OR, **1 já sumiu** da tabela `pedidos`: o par `(1591091, ML.18.0315|001)` não existe mais lá, **mas o pedido 1591091 segue aberto com outros 6 produtos**. `SUM(qt_entregar)` devolve **6**; o par que sumiu tinha `qt_solicitada = 3`; o total original verdadeiro é **9**.

Causa: `pedidos` espelha a view `system_automation_pedidos_em_aberto` — **pedidos em aberto**. Par atendido sai do recorte na origem.

**Teste obrigatório:** montar um pedido com um par já convertido em OR e **ausente** de `pedidos`, e provar que o denominador do orçamento inclui a quantidade dele. Sem esse teste, o desenho passa nos demais e erra silenciosamente em produção — reduzindo a tolerância a que o cliente tem direito.

---

## Medição de memória (critério 4) — instrumentação nova

Não existe hoje **nenhuma** instrumentação de memória automatizada no repositório. Os números de `docs/adequacao.md` (434 MiB) são medições manuais antigas, não testes reprodutíveis.

**Desenho recomendado:** teste novo seguindo a convenção `_024`, usando `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` (stdlib, disponível no Linux do container) em torno de uma chamada a `build_processing_plan` com dataset sintético grande (40–50k pares / 100k+ itens, gerado programaticamente, **não lido do banco**), em modo `SEM_ADEQUAR`.

**Tratar como instrumentação a calibrar na primeira execução, não como valor já validado.** Nenhum pico real de `SEM_ADEQUAR` foi medido — o volume elegível desse modo pode ser estruturalmente maior que o do `ADEQUAR`, porque hoje ele aceita todo par sem filtro de crédito. Se o teto escolhido falhar na primeira rodada, é sinal para medir e ajustar conscientemente, não para afrouxar o número até passar.

---

## Wave 0 Requirements

- [ ] `test_sem_adequar_revalidates_stock_before_writes` — FIX-01
- [ ] Teste de duas execuções sucessivas provando orçamento persistente — ALOC-09
- [ ] Teste do denominador que encolhe (J5)
- [ ] `test_pedidos_processing_sem_adequar_memory_024.py` — critério 4
- [ ] Reescrita de `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration`
- [ ] Nenhuma dependência nova — `resource` é stdlib

---

## Manual-Only Verifications

| Behavior | Why Manual | Instructions |
|----------|------------|--------------|
| Queda no volume de ORs geradas | Só observável contra dado real de produção | Depois do deploy, comparar o número de ORs de `sem_adequar` antes/depois. **Uma queda é esperada e correta** (o modo passa a recusar por crédito, estoque e furo). Distinguir "caiu porque a regra agora está certa" de "caiu demais" exige olhar os contadores `deferredCount`/`blockedCreditCount` do job: eles devem explicar a diferença. Se a soma não fecha, há bug |

---

## Riscos de validação específicos desta fase

1. **O teste que prova o oposto.** `test_sem_adequar_planning_uses_keyset_pages_without_global_hydration` hoje **prova a paginação** — o comportamento que esta fase remove. Reescrever conscientemente como asserção da hidratação global; não "ajustar até passar".
2. **Fixture faltando, não regra mudada.** 4 testes em `test_pedidos_processing_domain.py` chamam `build_processing_plan(mode=SEM_ADEQUAR, ...)` **sem `stock`**. Depois do roteamento, isso produz plano vazio em silêncio, não erro. É classe de falha diferente de "regra antiga" e precisa de fixture de estoque, não de reescrita.
3. **Remoção de símbolo sem consumidor conhecido.** Recomendado remover os símbolos de streaming **primeiro** e ver o que quebra, em vez de tentar prever quais testes os usam.
4. **Janela de dois caminhos.** Rotear numa entrega e remover o streaming em outra criaria um intervalo com dois caminhos concorrentes fazendo a mesma coisa — o anti-padrão que este projeto já pagou caro. Implementar → trocar → apagar, no mesmo plano.

---

## Validation Sign-Off

- [ ] Todas as tasks têm verificação automatizada ou dependência de Wave 0
- [ ] Nenhuma wave termina vermelha
- [ ] Grep de símbolos de streaming retorna vazio
- [ ] Teste do denominador que encolhe (J5) existe e passa
- [ ] `nyquist_compliant: true` no frontmatter

**Approval:** pending
