# Phase 14 — Plan Outline

**Gerado:** 2026-08-14
**Fonte:** `14-RESEARCH.md` § 3 "Divisão em planos executáveis" (o pesquisador já produziu a divisão em ondas com a justificativa)

## Princípio da divisão

`app/modules/pedidos/domain/motor_adequacao.py` é o **gargalo de paralelização**. Quase todas as regras novas escrevem no mesmo laço de `_processar_pedidos_canal` e em `adequar_grade_produto`, e a sequência de decisão por par é **regra de negócio explícita**, não detalhe de implementação:

```
crédito → furo de grade → política de quantidade → guarda de zero → commit (decrementa estoque, debita orçamento)
```

Planos que editem esse laço **nunca paralelizam entre si** — cada um passaria nos próprios testes e o merge produziria uma ordem de decisão diferente da ditada, sem nenhum teste unitário acusando (Pitfall 1 de `PITFALLS.md`). Por isso: Onda 0 é paralela (arquivos distintos), e das Ondas 1 a 4 a cadeia é **estritamente sequencial**.

## Planos

| Plan ID | Objetivo | Wave | Depends On | Requirements |
|---------|----------|------|------------|--------------|
| 14-01 | Promover o rateio de maior resto (Hamilton) de `edicao_grade.py` a utilitário público de domínio, com teste próprio | 0 | — | FIX-02 |
| 14-02 | Factories/fixtures de teste do motor (itens de pedido, estoque por canal, cenário A/B da mantenedora) e helpers de asserção das invariantes I1–I9 | 0 | — | — (infraestrutura de prova) |
| 14-03 | Desempate determinístico na chave de prioridade (`nr_pedido` crescente como último campo) | 1 | 14-02 | ALOC-06 |
| 14-04 | Verificação de furo de grade como função pura, ligada dentro do laço, antes da política de quantidade | 2 | 14-03 | ALOC-04 |
| 14-05 | Política de quantidade por modo: tudo-ou-nada (sem adequação) vs adequação, com crédito e prioridade preservados | 3 | 14-04 | ALOC-01, ALOC-02, ALOC-03 |
| 14-06 | Ledger de orçamento ±5% por pedido completo, duas passadas (mínimo viável antes dos extras) e ligação do rateio Hamilton no recálculo financeiro | 4 | 14-05, 14-01 | ALOC-07, ALOC-08, ALOC-09, ALOC-10, FIX-02 |
| 14-07 | Migração da suíte: reescrever os testes cuja **regra** mudou, ajustar os 2 arquivos de assinatura fixa, e fechar com a suíte completa verde | 5 | 14-06 | — (prova da fase) |

## Cobertura de requirements

| REQ | Plano |
|---|---|
| ALOC-01 | 14-05 |
| ALOC-02 | 14-05 |
| ALOC-03 | 14-05 |
| ALOC-04 | 14-04 |
| ALOC-06 | 14-03 |
| ALOC-07 | 14-06 |
| ALOC-08 | 14-06 |
| ALOC-09 | 14-06 |
| ALOC-10 | 14-06 |
| FIX-02 | 14-01 (utilitário) + 14-06 (ligação no motor) |

**10/10 cobertos.**

## Fronteiras entre planos (para não colidirem)

- **14-01** toca `domain/edicao_grade.py` e cria `domain/rateio.py`. **Não** toca `motor_adequacao.py`.
- **14-02** toca **só** arquivos novos em `app/tests/`. Nenhum arquivo de produção.
- **14-03 a 14-06** tocam `motor_adequacao.py` **em sequência**, um de cada vez. Cada um deixa a suíte verde antes do próximo começar.
- **14-07** toca **só** arquivos de teste existentes.

## Notas de execução válidas para todos os planos

- Ambiente: `uv run` local falha no Windows (pyicu). Testes **sempre** por Docker.
- **Suíte completa ao fim de cada wave** — `test_pedidos_motor_performance_024.py` e `test_pedidos_processing_cancellation_024.py` fazem `monkeypatch` na assinatura de `adequar_grade_produto` e quebram em silêncio; rodar só o arquivo do motor dá falso verde.
- Contrato inegociável: as chaves de retorno de `processar_pedidos` (`resultados`, `selecionados`, `preteridos`, `bloqueados_credito`, `pares_processados`) não mudam de forma.
- A guarda de zero (ALOC-05) é da **Phase 13** — assumir que existe no orquestrador, não reimplementar.
- Working tree compartilhado com trabalho de SSO de outra frente: `git add` sempre com caminhos explícitos.
- Commits sem coautoria de Claude.

## OUTLINE COMPLETE

7 planos.
