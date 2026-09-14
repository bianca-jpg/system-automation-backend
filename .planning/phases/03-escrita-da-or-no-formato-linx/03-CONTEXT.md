# Phase 3: Escrita da OR no formato Linx - Context

**Gathered:** 2026-08-05
**Status:** Ready for planning

<domain>
## Phase Boundary

Cada geração de ordens de reserva — pelos dois caminhos, `POST /pedidos/adequar` e
`POST /pedidos/sem_adequar` — passa a gravar/atualizar, **na mesma transação**, uma linha em
`ordens_reserva_linx` por cliente×produto×cor, usando `converter_grade_para_posicoes` (Fase 2)
e o schema da Fase 1. Requirement: LINX-02. Última fase do milestone v1.1.

Fora do escopo: envio efetivo ao Linx (barramento inexistente), status de envio, frontend.

</domain>

<decisions>
## Implementation Decisions

### Produto ausente da referência de posição
- **D-01:** Se a conversão não produzir **nenhuma** posição para o produto (produto sem nenhum
  tamanho em `produto_tamanho_posicao`), a linha Linx **NÃO é gravada** e um warning nomeia o
  produto. A OR interna (`ordens_reserva`) é gerada normalmente — a tela não muda de
  comportamento. Motivo: uma linha com `e1..e48` zerados faria o futuro load criar no ERP um
  documento sem peças; a ausência da linha + warning é mais segura e diz exatamente qual produto
  falta na referência.
- Observação: grade **parcialmente** convertida (alguns tamanhos sem posição) continua sendo
  gravada — esse caso já é tratado pela Fase 2 (avisos agregados por produto, D-04 da Fase 2).
  A regra acima vale só para o caso "zero posições".

### Grade editada — ADIADO para o milestone v1.2 (decisão revista em 2026-08-05)
- **D-02 (REVISTA):** A escrita da linha Linx nesta fase acontece **somente nos dois fluxos de
  geração** (`executar_adequacao` e `executar_sem_adequacao`). O fluxo de alteração de grade
  **não é tocado** nesta fase.
- **D-02a:** Em compensação, a fase deve expor **uma única função pública de (re)gravar a linha
  Linx de um par** (cliente×produto×cor), chamável de fora dos dois fluxos de geração. Quem
  implementar a edição manual no v1.2 só chama essa função — a linha Linx acompanha sem
  refatoração.

  **Por que foi revista:** a versão original supunha que editar a grade alterasse a OR. Não
  altera. Verificado no código: `carregar_modificacoes` (grades editadas) é lida **apenas** pela
  montagem da listagem (`_montar_pedidos`); nem `executar_adequacao` nem `executar_sem_adequacao`
  a consultam. Ou seja, a edição hoje muda só o que a tela mostra; a OR é gerada da grade original
  do Databricks, e uma edição feita depois da geração fica inerte (gravada em
  `pedido_modificacoes` e usada por ninguém). Fazer a linha Linx espelhar a edição criaria
  divergência entre o que o ERP recebe e a OR registrada — pior que não fazer.

  Corrigir isso é uma feature de negócio própria (muda o contrato do motor de adequação e exige
  travas que hoje não existem) e virou o **milestone v1.2** — ver Deferred Ideas e as Future
  Requirements GRADE-01..GRADE-04 em REQUIREMENTS.md.

### Coerência aritmética dos valores
- **D-03:** Preservar o **valor total**: `valor_embalado` = soma exata dos `vl_liquido` dos itens
  daquele cliente×produto×cor; `preco1` = `valor_embalado ÷ qtde_embalada`, arredondado a 2 casas.
  Aceita-se divergência de centavos em `preco1 × qtde_embalada` (a amostra real do Linx respeita
  essa igualdade — 311,24 × 3 = 933,72 —, mas o total financeiro é a nossa fonte de verdade).
  Registrar essa escolha no código, para revisão quando o contrato real do barramento existir.

### Sem backfill
- **D-04:** Somente gerações **novas** produzem linha Linx. As ORs já existentes em
  `ordens_reserva` (4 no banco de dev, de testes) não são convertidas: são dado de teste que nunca
  irá ao ERP. Mantém a decisão da Fase 1 (a tabela Linx nasce vazia).

### Claude's Discretion
- Nomes de arquivos/funções novos (seguir os análogos do módulo `pedidos`)
- Onde exatamente montar a linha (função de domínio pura vs no caso de uso) e como carregar a
  referência de posição por produto (uma query só para os produtos da rodada, provavelmente)
- Como preencher `nome_clifor` quando o item não traz `client` (há precedente: `f"Cliente {nr}"`)
- Estratégia de upsert (o `uq_ordens_reserva_linx_chave` da Fase 1 já existe para isso)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Decisões do milestone
- `.planning/PROJECT.md` — Key Decisions (layout Linx, nº da OR = pedido, campos sem fonte ficam vazios)
- `.planning/ROADMAP.md` — os 5 critérios de sucesso da Fase 3

### O que as fases anteriores entregaram (base desta fase)
- `.planning/phases/01-schema-linx-e-refer-ncia-de-posi-o/01-01-SUMMARY.md` — model `OrdemReservaLinx` (82 colunas, UQ `uq_ordens_reserva_linx_chave`)
- `.planning/phases/02-ingest-o-da-refer-ncia-convers-o-de-grade/02-02-SUMMARY.md` — `converter_grade_para_posicoes(grade, referencia, cd_prod_cor) -> (posicoes, ignorados)`
- `.planning/phases/02-ingest-o-da-refer-ncia-convers-o-de-grade/02-04-SUMMARY.md` — evidência real; **posições não começam sempre em 1** (produto `.1|001` usa 2..5)
- `.planning/phases/02-ingest-o-da-refer-ncia-convers-o-de-grade/02-CONTEXT.md` — decisões D-01..D-04 da Fase 2 (avisos agregados por produto, último vence)

### Código a modificar (somente os dois fluxos de geração — ver D-02 revista)
- `app/modules/pedidos/application/casos_uso.py` — `executar_adequacao`, `executar_sem_adequacao` (NÃO tocar `executar_alteracao_grade`)
- `app/modules/pedidos/infrastructure/repositorio_ordens.py` — `salvar_ordens_reserva` (padrão "repositório não commita")
- `app/modules/pedidos/domain/grade_linx.py` — conversão da Fase 2 (não alterar a assinatura sem necessidade)
- `app/modules/pedidos/models.py` — `OrdemReservaLinx`
- `app/tests/test_pedidos_routes.py` — molde dos testes de integração dos dois fluxos

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `converter_grade_para_posicoes` (Fase 2): já resolve posições, tamanhos ignorados e conflitos
- `uq_ordens_reserva_linx_chave (nr_pedido, cd_prod_cor)` (Fase 1): chave do upsert, criada de propósito nesta fase
- `salvar_ordens_reserva` + `salvar_processados`: mesma transação, commit único no caso de uso
- Precedente de split de `cd_prod_cor` no `"|"` (ex.: `"ML.18.0315|001"`) espalhado no módulo

### Established Patterns
- Repositórios não commitam; o caso de uso decide (mesmo padrão da ingestão)
- Funções puras em `domain/` podem logar (precedente de `motor_adequacao.py`) mas não fazem I/O
- Código e nomes em português; testes em `app/tests/`

### Integration Points
- `executar_adequacao` / `executar_sem_adequacao`: gravação Linx entra ANTES do `db.commit()` existente
- **Sessão paralela ativa nos mesmos arquivos:** o `estoque_virtual` (placebo documentado no `CLAUDE.md`) dispara um recálculo dentro de `executar_adequacao` e `executar_sem_adequacao`. A gravação Linx precisa conviver com esse gatilho na mesma transação, sem reordená-lo. Confira o estado real dos arquivos antes de editar; prefira referenciar funções por nome, não por linha.
- Ambiente: `uv run` local falha no Windows (pyicu) — usar `docker compose -f .docker/docker-compose.yml exec -T api uv run ...`; `ruff` não é executável no container

</code_context>

<specifics>
## Specific Ideas

- Amostra real do layout Linx (`Tabelas_de_OR.csv`): `PRECO1 × QTDE_EMBALADA = VALOR_EMBALADO`
  exatamente nas linhas observadas — contexto da decisão D-03.
- Warning de produto sem referência (D-01) deve nomear o `cd_prod_cor`, no mesmo espírito dos
  avisos agregados por produto da Fase 2.

</specifics>

<deferred>
## Deferred Ideas

- Envio ao Linx via barramento, status de envio e reconciliação (INTG-01..03) — aguarda acesso da TI
- Preenchimento de FILIAL, ROMANEIO, CAIXA, REPRESENTANTE, ENTREGA, PACKS, ITEM (sem fonte hoje)
- Política de retenção/limpeza de `ordens_reserva_linx` (a tabela cresce a cada geração; sem
  coluna de status, nada remove linhas antigas) — reavaliar quando a integração for real
- Backfill das ORs anteriores, se algum dia precisarem chegar ao ERP (D-04 diz não agora)
- **Milestone v1.2 — "Edição manual de grade manda na OR"** (GRADE-01..04 em REQUIREMENTS.md).
  Achados que motivam e dimensionam esse milestone, todos verificados em 2026-08-05:
  1. `carregar_modificacoes` é lida só por `_montar_pedidos` — a geração ignora grades editadas.
  2. Editar depois da geração é aceito pelo endpoint e fica inerte (a OR não muda, a tela não mostra).
  3. **As travas não existem:** nenhuma referência a 5%/tolerância no modal do frontend; o saldo de
     estoque é apenas exibido (fica vermelho se negativo) e não impede salvar.
  4. Intenção da usuária para o v1.2: após a edição manual, a OR deve seguir exatamente a grade
     editada, "sem tirar nem pôr" — o que só é seguro se o backend recusar edições que furem
     ±5% ou o estoque (erro claro na tela) em vez de silenciosamente cortar.
  5. Outra sessão já está ligando o `PUT /alterar-grade` no frontend (worktree
     `.claude/worktrees/fervent-goodall-…` do repo do front) — coordenar antes de planejar o v1.2.

</deferred>

---

*Phase: 3-Escrita da OR no formato Linx*
*Context gathered: 2026-08-05*
