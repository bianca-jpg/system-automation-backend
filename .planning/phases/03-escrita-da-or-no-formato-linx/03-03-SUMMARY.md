---
phase: 03-escrita-da-or-no-formato-linx
plan: 03
subsystem: pedidos
tags: [sqlalchemy, pytest, linx, transacao-unica, upsert]

# Dependency graph
requires:
  - phase: 03-escrita-da-or-no-formato-linx
    provides: "montar_linha_linx (Plano 03-01) e salvar_linhas_linx + carregar_referencia_posicoes (Plano 03-02)"
provides:
  - "Gravação da linha Linx dentro de executar_adequacao e executar_sem_adequacao, na mesma transação (antes do db.commit() existente)"
  - "Warning nomeando o produto quando a linha Linx não é gravada por ausência total de posições (D-01)"
  - "montar_linha_linx + salvar_linhas_linx reexportados em pedidos/service.py — a função pública que o milestone v1.2 vai chamar (D-02a)"
  - "Testes dos critérios 1-3 do ROADMAP: campos gravados nos 2 fluxos + rollback conjunto"
affects: [milestone-v1.2-edicao-manual-de-grade, futura-integracao-linx]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gravação Linx posicionada DEPOIS do recálculo do estoque_virtual e ANTES do db.commit() existente — o gatilho do placebo não foi reordenado"
    - "Referência de posição carregada só para os cd_prod_cor da rodada (set derivado dos pares gravados), nunca a tabela inteira (569.726 linhas)"
    - "Critérios 1 e 2 cobertos ESTENDENDO os testes de fluxo já existentes (asserts de linha Linx sobre o mock de salvar_linhas_linx), em vez de duplicar o setup dos fluxos"

key-files:
  created: []
  modified:
    - app/modules/pedidos/application/casos_uso.py
    - app/modules/pedidos/service.py
    - app/tests/test_pedidos_routes.py
---

# Plano 03-03 — Wiring da gravação Linx nos fluxos de geração

**Commits:**
- `90f2723` feat(03-03): grava linha Linx nos 2 fluxos de geracao (LINX-02)
- `22b83ec` test(03-03): cobre gravacao Linx nos 2 fluxos e rollback conjunto (LINX-02)

## O que foi entregue

Em `executar_adequacao` e `executar_sem_adequacao`, logo depois das escritas já existentes
(incluindo o recálculo do `estoque_virtual`) e **antes do `await db.commit()`**:

1. Deriva o conjunto de `cd_prod_cor` da rodada e carrega a referência de posição só para eles
   (`carregar_referencia_posicoes`).
2. Para cada par cliente×produto×cor, chama `montar_linha_linx(...)` com `tipo="com"` (adequação)
   ou `tipo="sem"` (sem adequação).
3. Quando `montar_linha_linx` devolve `None` — produto sem nenhuma posição na referência, D-01 —
   loga warning nomeando o `cd_prod_cor` e segue: a OR interna é gravada normalmente, apenas a
   linha Linx não nasce.
4. Grava tudo de uma vez com `salvar_linhas_linx` (upsert por SELECT+decide, do Plano 03-02).

`pedidos/service.py` passou a reexportar `montar_linha_linx` e `salvar_linhas_linx` (`__all__`),
satisfazendo D-02a — o v1.2 chama essas duas funções para manter a linha Linx em sincronia sem
precisar de uma terceira função nem refatorar esta fase.

## Cobertura dos critérios do ROADMAP

| Critério | Como foi coberto |
|---|---|
| 1 — `/adequar` grava os campos | Asserts acrescentados ao teste de fluxo existente: `nome_clifor`, `pedido`, `produto`, `cor_produto`, `preco1`, `valor_embalado`, `qtde_embalada`, `e1` |
| 2 — `/sem_adequar` grava os mesmos campos | Idem, no teste do fluxo sem adequação |
| 3 — mesma transação | `test_rollback_conjunto_quando_gravacao_linx_falha` (sessão real): falha na gravação Linx desfaz também as escritas anteriores |
| 4 — upsert sem duplicar | Coberto no Plano 03-02 (`salvar_linhas_linx`) |
| 5 — colunas sem fonte ficam NULL | Coberto no Plano 03-01 (a função pura nunca inclui essas chaves no dict) |

## Verificação

- `pytest app/tests/test_pedidos_routes.py -q` → **20 passed**
- Suíte completa → **280 passed, 1 warning** (o warning é a deprecação pré-existente do FastAPI em
  `test_auth_flows.py`, sem relação com esta fase)
- D-02a: `python -c "from app.modules.pedidos.service import montar_linha_linx, salvar_linhas_linx"` → OK
- D-02 revista (critério de aceite negativo): `salvar_linhas_linx` aparece **0** vezes dentro de
  `executar_alteracao_grade` — o fluxo de edição de grade permaneceu intocado
- `git show --stat` de cada commit: apenas os arquivos deste plano entraram (nenhum arquivo de
  outras sessões foi varrido)

## Desvios e notas de processo

1. **Nenhum desvio de implementação** em relação ao plano.
2. **Interrupção de sessão:** o executor foi interrompido (usuária encerrou o expediente) logo após
   validar os testes e antes de commitá-los e de rodar a regressão. Na retomada: os testes foram
   verificados isoladamente (20 passed) e commitados em `22b83ec`, e a suíte completa rodou em
   seguida (280 passed). Por isso a mensagem de `22b83ec` registra a regressão como pendente
   naquele momento — ela foi executada na retomada, com o resultado acima.
3. **MCP `backstage_get_coding_standards` indisponível** neste ambiente — seguido o fallback
   documentado (padrões dos análogos apontados em `03-PATTERNS.md`).
4. **Convivência com sessão paralela:** o `estoque_virtual` (placebo documentado no `CLAUDE.md`)
   dispara recálculo dentro dos mesmos dois fluxos. A gravação Linx foi inserida depois dele, sem
   reordenar nem alterar o gatilho.

## Self-Check: PASSED

- Wiring presente nos dois fluxos, antes do commit único ✅
- D-01 (produto sem posições → não grava + warning nomeando o produto) ✅
- D-02a (função pública reexportada) ✅
- D-02 revista (`executar_alteracao_grade` intocado) ✅
- Critérios 1, 2 e 3 com teste automatizado ✅
- Suíte completa verde, sem regressão ✅
