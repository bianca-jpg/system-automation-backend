# Plan 14-05 — Summary

**Plan:** 14-05 — Política de quantidade por modo (tudo-ou-nada)
**Requirements:** ALOC-01, ALOC-02, ALOC-03
**Status:** Complete
**Executed:** 2026-08-17

> **Nota de execução:** o agente executor caiu com erro de API (`Server error mid-response`) **depois** de commitar as 4 tasks, no momento exato em que ia escrever este SUMMARY. Todo o código foi entregue e verificado; este documento foi escrito pelo orquestrador no fechamento, a partir dos commits.

## O que foi construído

| Commit | Entrega |
|---|---|
| `350dbbe` | `ModoAdequacao(StrEnum)` — enum próprio do domínio, com **teste de paridade** contra `ProcessingMode` |
| `946dae2` | `aplicar_tudo_ou_nada` — política do modo sem adequação, isolada (ALOC-02) |
| `e3f8681` | Despacho por modo em `processar_pedidos` / `_processar_pedidos_canal` (ALOC-01, 02, 03) |
| `f5ae688` | Cenário da mantenedora reexecutado em `SEM_ADEQUAR` |

**Arquivos de produção:** `app/modules/pedidos/domain/politica_quantidade.py` (novo), `app/modules/pedidos/domain/motor_adequacao.py`, `app/modules/pedidos/service.py`.
**Arquivos de teste:** `app/tests/test_pedidos_motor_politica_quantidade.py` (novo), `app/tests/test_pedidos_motor.py`, `app/tests/test_cenario_disputa_mantenedora.py`.

## Decisão de desenho registrada

`ModoAdequacao` é um enum **próprio do domínio**, espelhando os valores `adequar`/`sem_adequar` de `ProcessingMode` em vez de importá-lo de `processing/`. Motivo: direção de dependência do DDD — o domínio não pode depender de um subcontexto de aplicação. O risco da duplicação (os dois divergirem em silêncio) é neutralizado por um **teste de paridade** entre os enums: no dia em que alguém mudar um lado, o teste quebra.

O despacho é `if/else` explícito, não `Protocol`/classe de estratégia — as duas políticas têm assinaturas divergentes e o idioma de `pedidos/domain/` é função simples. Coerente com a restrição do repositório contra over-engineering.

## Verificação

Suíte completa via Docker: **811 passed, 16 skipped, 2 failed**.

- `test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql` — falha **conhecida e pré-existente** (`RuntimeError: Event loop is closed`, teardown de conexão), presente desde a linha de base da fase.
- `test_parametros.py::test_listar_parametros_contem_criado` — **falha nova, mas NÃO é regressão deste plano.** Investigada e provada de outra causa (ver abaixo).

### A falha de `test_parametros` — diagnóstico

Nenhum commit do 14-05 tocou o módulo `parametros` (verificado com `git log --name-only --grep="14-05"`). A causa real é **acúmulo de dados no banco de dev**, que é persistente entre execuções:

- A listagem ordena por `chave` ascendente, página de 25 (`repositorio_parametro.py::list_parametros_page`).
- O teste cria uma chave `listar-<uuid8>` e assume que ela aparece na **página 1**.
- A tabela tinha **222 linhas: 220 artefatos de execuções anteriores da suíte** (chaves como `atualizar-07b64f80`) e apenas **2 parâmetros reais de negócio** (`criterio_selecao`, `tolerancia_adequacao`).
- Com esse volume, a chave criada cai muito além da primeira página e a asserção falha.

O teste é **frágil por desenho** (assume que o registro criado cai na primeira página de uma listagem ordenada alfabeticamente) e apenas cruzou o limiar agora. Não tem relação com o motor de adequação.

## Deviations

Nenhuma no escopo do plano. O plano foi executado como escrito.

## Notas para os planos seguintes

- O **14-06** substitui a política de adequação pelo ledger de orçamento ±5%. A estrutura de despacho por modo criada aqui é o ponto de entrada.
- O **14-06** vai quebrar a assinatura de `adequar_grade_produto` e, com ela, os dois arquivos de `monkeypatch` — quebra esperada e declarada, com conserto no 14-07.
- A falha de `test_parametros` precisa de decisão fora do escopo desta fase: limpar os 220 artefatos do banco de dev, ou tornar o teste robusto (buscar pela chave em vez de assumir a página 1). Enquanto não for resolvida, a linha de base da fase passa a ter **2 falhas conhecidas**.
