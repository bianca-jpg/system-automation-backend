# Phase 3: Escrita da OR no formato Linx - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-05
**Phase:** 3-Escrita da OR no formato Linx
**Areas discussed:** Produto sem referência de posição, Grade editada depois da OR, Coerência preco1 × qtde, ORs já existentes (backfill)

---

## Produto sem referência de posição

| Option | Description | Selected |
|--------|-------------|----------|
| Não gravar a linha + aviso | OR interna gerada normalmente; linha Linx não nasce; warning nomeia o produto | ✓ |
| Gravar com zeros | Linha existe com E1..E48 = 0; risco de documento vazio no ERP | |
| Bloquear a geração | Rodada inteira falha se um produto não tem referência | |

**User's choice:** Não gravar a linha + aviso
**Notes:** Contexto apresentado: a referência tem 569.726 linhas, mas nada garante cobertura de 100% dos produtos. Grade parcialmente convertida continua sendo gravada (caso já coberto pela Fase 2).

---

## Coerência preco1 × qtde vs valor_embalado

| Option | Description | Selected |
|--------|-------------|----------|
| Preservar o valor total | valor_embalado = soma exata; preco1 = total ÷ qtde arredondado | ✓ |
| Preservar a igualdade do ERP | preco1 arredondado e valor_embalado = preco1 × qtde recalculado | |
| Não sei — confirmar com o time | Registrar pendência | |

**User's choice:** Preservar o valor total
**Notes:** Evidência apresentada: na amostra real do Linx, 311,24 × 3 = 933,72 exatamente. A escolha aceita divergência de centavos na multiplicação, priorizando o total financeiro.

---

## Grade editada depois da OR

| Option | Description | Selected |
|--------|-------------|----------|
| Atualizar a linha (espelho) | Editar a grade regrava a linha Linx com as novas quantidades | ✓ |
| Manter como foto da geração | Linha congela o estado da geração | |
| Fora do escopo agora | Registrar pendência (o front não usa o endpoint hoje) | |

**User's choice:** Atualizar a linha (espelho)
**Notes:** Amplia o escopo da fase para o fluxo `executar_alteracao_grade`, além dos dois fluxos de geração.

---

## ORs já existentes (backfill)

| Option | Description | Selected |
|--------|-------------|----------|
| Só gerações novas | Mantém a decisão da Fase 1 (tabela nasce vazia) | ✓ |
| Backfill das existentes | Script/migration converte as ORs já gravadas | |

**User's choice:** Só gerações novas
**Notes:** As 4 ORs no banco de dev são de teste e nunca irão ao ERP.

---

## Claude's Discretion

- Nomes de arquivos/funções novos; onde montar a linha (domínio puro vs caso de uso)
- Como carregar a referência de posição dos produtos da rodada
- Fallback de `nome_clifor` quando o item não traz `client`
- Estratégia de upsert sobre `uq_ordens_reserva_linx_chave`

## Deferred Ideas

- Envio ao Linx, status de envio, reconciliação (INTG-01..03)
- Campos do layout sem fonte (FILIAL, ROMANEIO, CAIXA, REPRESENTANTE, ENTREGA, PACKS, ITEM)
- Política de retenção de `ordens_reserva_linx`
- Backfill futuro, se as ORs antigas precisarem chegar ao ERP
- Ligar o frontend ao `PUT /alterar-grade`
