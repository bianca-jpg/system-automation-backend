# Phase 2: Ingestão da referência + conversão de grade - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-05
**Phase:** 2-Ingestão da referência + conversão de grade
**Areas discussed:** Posição inválida na origem, Referência vazia/falha, Conflito de posição, Formato dos avisos no log

---

## Posição inválida na origem

| Option | Description | Selected |
|--------|-------------|----------|
| Descartar na tradução + aviso | Linha ruim ignorada com warning; padrão _parse_int das outras fontes; sem migration | ✓ |
| Descarte + CheckConstraint | Também migration 016 com CHECK (nr_posicao BETWEEN 1 AND 48), sugestão WR-02 do review | |
| Só CheckConstraint | Validação só no banco; linha suja derrubaria o sync das 5 fontes (commit único) | |

**User's choice:** Descartar na tradução + aviso
**Notes:** Usuária ciente da sugestão WR-02 do code review; pesou o risco de uma linha suja derrubar o sync inteiro.

---

## Referência vazia/falha

| Option | Description | Selected |
|--------|-------------|----------|
| Proteger: manter snapshot anterior | 0 linhas com sucesso → não substitui, mantém referência anterior + warning | ✓ |
| Padrão das irmãs: substituir mesmo vazio | DELETE+INSERT sempre, uniforme com as outras 4 fontes | |

**User's choice:** Proteger: manter snapshot anterior
**Notes:** Referência é dado de cadastro; vazio por erro na origem zeraria as grades E1..E48 da Fase 3. Falha de conexão já protegida por rollback da transação.

---

## Conflito de posição

| Option | Description | Selected |
|--------|-------------|----------|
| Último vence + aviso | Linha mais recente sobrescreve; warning com produto e posição | ✓ |
| Descartar o produto inteiro + aviso | Produto com conflito sai da referência do sync | |
| Você decide | Claude's Discretion no CONTEXT.md | |

**User's choice:** Último vence + aviso

---

## Formato dos avisos no log

| Option | Description | Selected |
|--------|-------------|----------|
| Agregado por produto | 1 linha por produto com lista de tamanhos sem posição | ✓ |
| 1 linha por item | Cada tamanho ignorado gera warning próprio | |

**User's choice:** Agregado por produto
**Notes:** Vale para ingestão e conversão; evita inundar o log no sync de 2h.

---

## Claude's Discretion

- Nomes exatos de funções/arquivos novos (seguir análogos do módulo ingestao)
- Assinatura exata da função de conversão
- Estrutura da fixture de dados reais para o teste puro

## Deferred Ideas

- CheckConstraint em nr_posicao (WR-02) — reavaliar se houver escrita fora do sync
- WR-01 (filtro de schema no test_schema_guard) — dívida pré-existente, candidata a /gsd-quick avulso
