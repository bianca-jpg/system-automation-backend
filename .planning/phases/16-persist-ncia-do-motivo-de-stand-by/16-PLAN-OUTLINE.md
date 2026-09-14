# Phase 16 — Plan Outline

**Gerado:** 2026-08-20
**Fonte:** `16-RESEARCH.md` § "Architecture Patterns" (diagrama de fluxo), § "Riscos de execução", § "Testes — impacto na suíte existente"

## Princípio da divisão

Executor sequencial (worktrees desligados) — waves aqui ordenam **dependência e risco por commit**, não paralelismo real. A migration fica isolada (arquivo próprio, sem overlap com código). O motor (domínio puro) é testável sem banco e não depende de nada além de si mesmo.

## Planos

| Plan ID | Objetivo | Wave | Depends On | Requirements |
|---------|----------|------|------------|--------------|
| 16-01 | Motor: `domain/standby_motivo.py` (3 constantes canônicas) + `preteridos_motivo` no laço de `processar_pedidos`, distinguindo `furo_grade` de `sem_estoque` nos 2 modos | 1 | — | STANDBY-01 (parte motor) |
| 16-02 | Migration `032_pedido_standby_motivo` (CHECK com os 3 motivos, não 2) + model ORM `PedidoStandbyMotivo` em `pedidos/infrastructure/models.py` | 2 | — | STANDBY-01 (schema) |
| 16-03 | `PlanDraft` ganha `deferred_pairs`/`blocked_credit_pairs` (fan-out de crédito por produto elegível); `build_processing_plan` os popula a partir de `preteridos_motivo`/`bloqueados_credito`; `plan_hash` os inclui; guarda contra overlap em `__post_init__` | 3 | 16-01 | STANDBY-01 (wiring de domínio) |
| 16-04 | Port `record_standby_reasons` + implementação (upsert com incremento de `execucoes_consecutivas`) + chamada em `_plan_once` **na mesma transação** de `store_plan` + `DELETE` em `apply_pairs` quando o par vira OR | 4 | 16-02, 16-03 | STANDBY-01 (persistência), STANDBY-06 |
| 16-05 | Limpeza de linhas órfãs no full refresh da ingestão (`reconstruir_pedido_produto_read`) | 5 | 16-02 | critério 4 do ROADMAP |

## Cobertura de requirements

| REQ | Plano |
|---|---|
| STANDBY-01 | 16-01 (motor) + 16-02 (schema) + 16-03 (wiring de domínio) + 16-04 (persistência) |
| STANDBY-06 | 16-04 |

## Fronteiras entre planos

- **16-01** toca só `app/modules/pedidos/domain/` e testes de motor. Não toca `processing/`.
- **16-02** toca só `alembic/versions/` (novo arquivo) e `app/modules/pedidos/infrastructure/models.py`. Migration isolada — sem overlap com nenhum outro plano.
- **16-03** toca só `app/modules/pedidos/processing/domain.py` e seu teste. Depende de `preteridos_motivo` existir (16-01).
- **16-04** toca `ports.py`, `repository.py`, `service.py` de `processing/` — depende da tabela existir (16-02) e dos campos do `PlanDraft` existirem (16-03).
- **16-05** toca só `app/modules/ingestao/infrastructure/repositorio_snapshot.py` e seu teste — depende só da tabela existir (16-02), não do resto do wiring.

## Risco central da fase (não perder de vista em nenhum plano)

O `ARCHITECTURE.md` § 3.2 (pesquisa do milestone) tem um DDL **desatualizado** com só 2 valores de motivo (`'sem_credito'`, `'sem_estoque'`). **O plano 16-02 deve citar `16-RESEARCH.md` como fonte do DDL, nunca `ARCHITECTURE.md`.** Copiar o DDL antigo reproduziria exatamente o bug que esta fase existe para resolver — furo de grade ficaria indistinguível de falta de estoque genérica, e a Phase 17 nunca teria dado para cumprir STANDBY-04.

## Notas válidas para todos os planos

- **Linha de base a confirmar no início da execução** (não assumir): ao fim da Phase 15 era 826 passed, 16 skipped, 1 failed. Rodar a suíte completa antes de tocar qualquer arquivo.
- A única falha aceitável em qualquer wave é a conhecida: `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` (teardown asyncpg, pré-existente). **Nenhuma exceção de wave vermelha nesta fase.**
- Testes **sempre** por Docker: `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q`
- **NÃO usar `gsd-tools query state.advance-plan`** — corrompe a frontmatter YAML do STATE.md. Usar Edit.
- **NÃO iniciar comandos em background** para esperar a suíte.
- Working tree compartilhado com trabalho de SSO não commitado: `git add` sempre com caminhos explícitos, conferir com `git show --stat`.
- Commits sem coautoria de Claude.
- `record_standby_reasons` deve ser chamada **antes** de `unit_of_work.commit()`, na mesma transação de `store_plan` — nunca depois. Perda de atomicidade aqui é perda silenciosa e permanente do motivo.
- `preteridos_motivo` deve conter sempre os valores curtos canônicos (`domain/standby_motivo.py`), nunca a string longa de `motivo_stand_by` (que continua existindo em paralelo, para log/depuração).

## OUTLINE COMPLETE

5 planos.
