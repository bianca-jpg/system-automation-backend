---
phase: 03-escrita-da-or-no-formato-linx
plan: 01
subsystem: api
tags: [domain, python, pedidos, linx, testing]

# Dependency graph
requires:
  - phase: 02-ingest-o-da-refer-ncia-convers-o-de-grade
    provides: "converter_grade_para_posicoes(grade, referencia, cd_prod_cor) -> (posicoes, ignorados)"
provides:
  - "montar_linha_linx: função pura de domínio que monta o dict completo da linha Linx de UM par (nr_pedido, cd_prod_cor)"
affects: [03-escrita-da-or-no-formato-linx-plano-03, casos_uso.py, service.py]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Função pura de domínio decide 'gravar ou não' devolvendo None (nunca exceção); quem loga é o chamador"
    - "Split defensivo de cd_prod_cor via .partition(\"|\"), nunca .split(\"|\") desestruturado"

key-files:
  created:
    - app/modules/pedidos/domain/ordem_reserva_linx.py
    - app/tests/test_ordem_reserva_linx.py
  modified: []

key-decisions:
  - "D-01 é checada ANTES de chamar converter_grade_para_posicoes (referência vazia -> None), nunca inferida a partir do 2º retorno (ignorados)"
  - "D-03: valor_embalado = soma exata dos vl_liquido dos itens crus (não da grade agregada); preco1 = valor_embalado / qtde_embalada arredondado a 2 casas, aceitando divergência de centavos"
  - "montar_linha_linx não loga (import logging ausente de propósito) — o warning de D-01 é responsabilidade do chamador (Plano 03-03), que sabe o contexto certo para o aviso"

patterns-established:
  - "Domínio puro sem I/O, decide via None (nunca exceção) — mesmo espírito de grade_linx.py"

requirements-completed: [LINX-02]

# Metrics
duration: ~15min
completed: 2026-08-05
---

# Phase 3 Plan 01: montar_linha_linx Summary

**Função pura `montar_linha_linx` monta o dict completo da linha Linx (produto, cor, preco1, valor_embalado, e1..e48) de UM par (nr_pedido, cd_prod_cor), decidindo D-01 (referência vazia -> None) antes de qualquer conversão de grade.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-08-05T18:26:00Z (aprox.)
- **Completed:** 2026-08-05T18:41:00Z
- **Tasks:** 2
- **Files modified:** 2 (ambos novos)

## Accomplishments
- `montar_linha_linx` implementada em `app/modules/pedidos/domain/ordem_reserva_linx.py`, reaproveitando `converter_grade_para_posicoes` (Fase 2) sem alterar sua assinatura
- 8 testes puros (sem banco/HTTP) cobrindo D-01 (zero posições vs. parcial), D-03 (soma exata + arredondamento, com e sem divergência de centavos), split defensivo e critério 5 (colunas sem fonte)
- D-01 e D-04/Fase 2 confirmados como distintos: referência vazia (`{}`) -> `None`; referência parcial (ao menos 1 tamanho com posição) -> segue gerando a linha normalmente, delegando os tamanhos sem posição para o aviso agregado já existente em `converter_grade_para_posicoes`

## Task Commits

Each task was committed atomically:

1. **Task 1: Implementar montar_linha_linx (D-01, D-03, split defensivo)** - `c5ac878` (feat)
2. **Task 2: Testes puros de montar_linha_linx (D-01, D-03, split, critério 5)** - `0a2fb89` (test)

**Plan metadata:** (commit final deste plano, ver abaixo)

## Files Created/Modified
- `app/modules/pedidos/domain/ordem_reserva_linx.py` - `montar_linha_linx(nr_pedido, cd_prod_cor, itens, referencia_produto, tipo) -> dict | None`, função pura de domínio
- `app/tests/test_ordem_reserva_linx.py` - 8 testes puros (sem banco), molde de `test_grade_linx.py`

## Corpo final da função (referência para o Plano 03-03)

```python
def montar_linha_linx(
    nr_pedido: int,
    cd_prod_cor: str,
    itens: list[dict],
    referencia_produto: dict[str, int],
    tipo: str,
) -> dict | None:
    if not referencia_produto:
        return None

    grade: dict[str, int] = {}
    for item in itens:
        sg_tamanho = item["sg_tamanho"]
        grade[sg_tamanho] = grade.get(sg_tamanho, 0) + item["qt_liquida"]

    posicoes, _ignorados = converter_grade_para_posicoes(
        grade, referencia_produto, cd_prod_cor
    )

    qtde_embalada = sum(grade.values())
    valor_embalado = round(sum(item["vl_liquido"] for item in itens), 2)
    preco1 = round(valor_embalado / qtde_embalada, 2) if qtde_embalada else None

    produto, sep, cor_produto = cd_prod_cor.partition("|")
    if not sep:
        produto, cor_produto = cd_prod_cor, None

    meta = itens[0]
    nome_clifor = meta.get("client") or f"Cliente {nr_pedido}"

    return {
        "nr_pedido": nr_pedido,
        "cd_prod_cor": cd_prod_cor,
        "tipo": tipo,
        "nome_clifor": nome_clifor,
        "produto": produto,
        "cor_produto": cor_produto,
        "pedido": nr_pedido,
        "preco1": preco1,
        "valor_embalado": valor_embalado,
        "qtde_embalada": qtde_embalada,
        **posicoes,
    }
```

Assinatura para o Plano 03-03 chamar: `montar_linha_linx(nr, cd, itens, referencia.get(cd, {}), tipo="com"|"sem")`. Devolve `None` quando não há linha a gravar (D-01) — o chamador decide o `logger.warning` e o `continue`.

## Decisions Made
- D-01 checada com `if not referencia_produto: return None` como a PRIMEIRA linha da função, nunca a partir de `_ignorados` (Pitfall 1 do RESEARCH.md)
- D-03: soma feita sobre `itens` (crus), não sobre `grade` (agregada), para não perder itens repetidos do mesmo tamanho na soma financeira
- `montar_linha_linx` não importa `logging` — decisão deliberada do plano (Pattern 4 do RESEARCH.md): a função pura devolve `None` silenciosamente; o aviso nomeando o produto é responsabilidade do caso de uso no Plano 03-03, que tem o contexto completo (nr_pedido, tipo do fluxo) para o warning fazer sentido

## Deviations from Plan

None - plan executado exatamente como escrito.

## Issues Encountered
None.

## Known Stubs
None - `montar_linha_linx` não introduz stubs; colunas sem fonte conhecida são omitidas por design (critério 5), não são stubs.

## User Setup Required
None - nenhuma configuração externa necessária.

## Next Phase Readiness
- `montar_linha_linx` está pronta para ser importada diretamente pelo Plano 03-03 em `casos_uso.py` (mesmo padrão de import direto de `repositorio_ordens.py`, sem passar por `service.py`)
- `service.py` deve reexportar `montar_linha_linx` no Plano 03-03 (D-02a), junto com `salvar_linhas_linx`
- Nenhum bloqueio identificado; o plano é independente da leitura em lote da referência e do repositório de upsert (Plano 03-02), que roda em paralelo

## Self-Check: PASSED

- FOUND: `app/modules/pedidos/domain/ordem_reserva_linx.py`
- FOUND: `app/tests/test_ordem_reserva_linx.py`
- FOUND: `.planning/phases/03-escrita-da-or-no-formato-linx/03-01-SUMMARY.md`
- FOUND commit: `c5ac878`
- FOUND commit: `0a2fb89`

---
*Phase: 03-escrita-da-or-no-formato-linx*
*Completed: 2026-08-05*
