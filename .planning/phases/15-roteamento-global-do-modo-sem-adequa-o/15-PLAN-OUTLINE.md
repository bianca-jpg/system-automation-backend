# Phase 15 — Plan Outline

**Gerado:** 2026-08-17
**Fonte:** `15-RESEARCH.md` § "Divisão em planos executáveis (waves e dependências)"

## Princípio da divisão

O executor deste projeto é **sequencial** (`workflow.use_worktrees: false`, porque os testes rodam num container que monta a árvore principal). Então "waves paralelas" não trazem ganho de tempo — o valor de dividir aqui é **ordem de dependência e isolamento de risco por commit**.

Os arquivos centrais (`service.py`, `domain.py`, `ports.py`, `adapters.py`, `repository.py`) são tocados por mais de uma preocupação. **Não criar planos que editem o mesmo arquivo em ordens diferentes** — sequenciar.

**Decisão estrutural herdada da pesquisa:** rotear numa entrega e remover o streaming em outra criaria uma janela real com **dois caminhos concorrentes fazendo a mesma coisa** — exatamente o anti-padrão que este projeto já pagou caro (uma trava de UI ficou órfã por um redesenho e foi apagada meses depois como "feature morta"). Por isso o plano 15-03 faz implementar → trocar → apagar como uma sequência de tasks dentro de **um** plano.

## Planos

| Plan ID | Objetivo | Wave | Depends On | Requirements |
|---------|----------|------|------------|--------------|
| 15-01 | Leitura dos dados de orçamento por pedido — método novo no port + implementação SQL + teste de integração isolado. **Puramente aditivo**, nada ainda o chama | 1 | — | (base do ALOC-09) |
| 15-02 | FIX-01 — `_assert_stock_capacity` passa a revalidar capacidade também para `tipo == "sem"`, com teste novo | 2 | — | FIX-01 |
| 15-03 | **A peça grande:** roteamento de `SEM_ADEQUAR` pelo caminho global + ligação do orçamento real (fecha ALOC-09) + remoção do streaming, como tasks sequenciais no mesmo plano | 3 | 15-01 | ARCH-01 |
| 15-04 | Medição de pico de memória do modo sem adequação (critério 4 do ROADMAP) — instrumentação nova | 4 | 15-03 | (critério 4) |
| 15-05 | Atualizar `docs/adequacao.md` — as seções de streaming e a descrição de `adequar_grade_produto` ficam desatualizadas | 5 | 15-03 | (dívida de doc) |

## Cobertura de requirements

| REQ | Plano |
|---|---|
| ARCH-01 | 15-03 |
| FIX-01 | 15-02 |
| ALOC-09 (fechamento; REQ-ID rastreado na Phase 14) | 15-01 (leitura) + 15-03 (ligação) |

## Fronteiras entre planos

- **15-01** só **adiciona** ao `ports.py` e implementa em `adapters.py`. Não toca `_plan_once` nem `build_processing_plan`. Risco de regressão ≈ zero, porque nada ainda chama o método novo.
- **15-02** toca só `repository.py` (`_assert_stock_capacity`) e um teste. Sem overlap com 15-01 nem com 15-03.
- **15-03** toca `service.py`, `domain.py`, `ports.py` (remoções), `adapters.py` (remoções), `repository.py` (remoções) e 4 arquivos de teste. É o único plano que remove símbolos.
- **15-04** cria arquivo de teste novo. Sem overlap.
- **15-05** só documentação.

## Notas válidas para todos os planos

- **Linha de base: 825 passed, 16 skipped, 1 failed.** A única falha é `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (teardown asyncpg, pré-existente). **Nenhuma wave desta fase pode terminar vermelha** — a exceção da Phase 14 acabou com o plano 14-06.
- Testes **sempre** por Docker: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- **NÃO usar `gsd-tools query state.advance-plan`** — corrompe a frontmatter YAML do STATE.md. Usar Edit.
- **NÃO iniciar comandos em background** para esperar a suíte.
- Working tree compartilhado com trabalho de SSO não commitado: `git add` sempre com caminhos explícitos, conferir com `git show --stat`.
- Commits sem coautoria de Claude.

## OUTLINE COMPLETE

5 planos.
