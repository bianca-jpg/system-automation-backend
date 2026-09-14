# Feature Research

**Domínio:** Persistência de cabeçalho de documento de negócio (Ordem de Reserva) em sistema de automation de moda, com numeração sequencial própria e preparação para integração com ERP (Linx)
**Pesquisado em:** 2026-08-04
**Confiança:** MÉDIA-ALTA (padrões de ERP nacional e de integração corroborados por múltiplas fontes; nenhuma fonte oficial da Linx sobre o formato exato do barramento — acesso à documentação de integração não disponível)

## Contexto da pesquisa

Este milestone (v1.1) não introduz um domínio novo — adiciona uma camada de persistência (cabeçalho) sobre uma feature já em produção (motor de adequação + `ordens_reserva` por linha). Por isso este documento foca em três perguntas concretas, não num levantamento de mercado:

1. Como ERPs de automation/moda numeram documentos de negócio (cabeçalho + itens, unicidade, buracos na sequência)?
2. O que sistemas que se preparam para integrar com um ERP (Linx) costumam guardar *antes* de a integração existir?
3. Como a UI deve se comportar ao expor o número do documento quando o mesmo produto aparece em várias rodadas de geração?

## Feature Landscape

### Table Stakes (o sistema já teria "quebrado" sem isso)

| Feature | Por que é esperado | Complexidade | Notas |
|---------|---------------------|------------|-------|
| Cabeçalho separado das linhas (padrão "documento + itens") | Todo ERP de automation/varejo pesquisado (Senior, Sankhya) modela pedido/OR como cabeçalho único (NUNOTA/número do pedido) + N itens vinculados por FK, cada item com sua própria sequência dentro do documento. É o modelo mental que a operação comercial e o Linx esperam ao consumir "uma OR com N clientes". | BAIXA | Já decidido no projeto: `ordens_reserva_cabecalho` (1 por produto×rodada) + `ordens_reserva.or_id` FK NOT NULL. Alinhado ao padrão do domínio. |
| Número único e imutável por documento | Usuário e ERP externo precisam de um identificador estável para citar/rastrear a OR (ex.: em e-mail para o time comercial, em comunicação futura com o Linx). Reaproveitar um número ou deixá-lo mudar depois de gerado quebra rastreabilidade. | BAIXA | `Identity`/sequence do Postgres garante unicidade; imutabilidade vem de nunca fazer UPDATE no número após criação (só leitura). |
| Continuidade da numeração visível ao usuário | O frontend já exibia `255315` fixo — a expectativa do usuário é de que o próximo número seja maior, sequencial e não "reinicie". Trocar a lógica sem preservar essa continuidade seria uma regressão perceptível (usuária compara números entre telas/e-mails). | BAIXA | Resolvido pela decisão de `start=255316`, dando continuidade ao mock anterior sem colisão com nº de pedido. |
| Backfill de dados legados sem NULL residual | Toda linha histórica de `ordens_reserva` precisa apontar para um cabeçalho, senão qualquer relatório/leitura que faça `JOIN` ou agregue por `or_id` perde OR antigas silenciosamente ou precisa de `LEFT JOIN` + tratamento de NULL espalhado pelo código. | MÉDIA | Já decidido: `or_id NOT NULL` com backfill retroativo agrupando por `(cd_prod_cor, ...)` das linhas existentes antes de aplicar a constraint — ordem clássica de migration com dados: 1) criar tabela nova, 2) popular, 3) preencher FK, 4) só então aplicar NOT NULL. |
| Contrato HTTP aditivo (não quebra o frontend em produção) | Padrão de integração ao evoluir uma API já consumida: novos campos (`nrOr`, `codigoLinx`, `geradaEm`) são seguros; renomear ou remover campos existentes (`255315` hardcoded sendo removido no frontend, não na API) quebra clientes já implantados. | BAIXA | Já é constraint explícita do projeto; mencionado aqui como validação de que a estratégia (aditiva) é a prática padrão de evolução de API, não uma escolha arriscada. |
| Agrupamento determinístico de "1 OR = 1 produto por rodada" | Se duas rodadas de geração para o mesmo produto no mesmo dia gerarem números diferentes (o que é o requisito), a UI e qualquer relatório precisam expor explicitamente *qual rodada* cada número representa — do contrário o usuário vê "dois números para o mesmo produto" sem entender por quê. | BAIXA | Resolvido por expor `geradaEm` (timestamp da rodada) junto do `nrOr` — ver seção de UI abaixo. |

### Differentiators (o que essa preparação agrega além do mínimo)

| Feature | Valor agregado | Complexidade | Notas |
|---------|-------------------|------------|-------|
| Campo `codigo_linx` reservado (nullable, `String(32)`) desde já | Evita uma segunda migration disruptiva quando o barramento existir: a integração futura só fará `UPDATE ordens_reserva_cabecalho SET codigo_linx = ...` — não precisa criar coluna, backfill, nem tocar em linhas já processadas. Isso é o padrão real de "integração preparada": guardar um campo de correlação com o sistema externo antes de a integração existir, tipado de forma frouxa (string) porque o formato do identificador do ERP ainda é desconhecido. | BAIXA | Confirma decisão já tomada. Padrão de mercado (ver `external_id`/`erp_code`) é justamente esse: nullable, tipo permissivo, populado só quando a integração roda. |
| `nrOr` como identificador de negócio distinto do `id` interno da tabela | Separar "identidade técnica" (PK usada em joins) de "número de negócio" (o que a usuária e o Linx vão citar) dá liberdade para, no futuro, trocar a estratégia de geração do número (ex.: se o Linx exigir prefixo por ano/filial) sem tocar em FKs internas. | BAIXA-MÉDIA | O projeto já resolveu isso ao decidir que o nº da OR *é* o `id` da tabela cabeçalho (mais simples, sem sequência extra) — é uma simplificação aceitável **enquanto o Linx não impuser um formato próprio**. Vale registrar como ponto de atenção para quando a integração real chegar (ver Pitfalls/dependências). |
| Rastreabilidade histórica por rodada (mesmo produto, múltiplos `nrOr`) | Permite à operação comercial responder "essa OR específica, gerada nesse dia, tinha esses clientes e essa grade" mesmo depois de rodadas posteriores reprocessarem o mesmo produto — sem isso, o histórico de decisões de negócio (quem foi atendido em cada rodada) se perde. | BAIXA | Decorre naturalmente do grão escolhido (1 cabeçalho por produto×rodada); não exige trabalho extra além do que já está no escopo. |

### Anti-Features (parecem necessárias agora, mas não são)

| Feature | Por que parece necessária | Por que é problemática agora | Alternativa |
|---------|---------------|-----------------|-------------|
| Status de envio ao Linx (`pendente`/`enviado`/`erro`) | Todo padrão de integração "preparação para ERP" que aparece na pesquisa (staging, correlação, idempotência) eventualmente precisa de uma máquina de estados para acompanhar o envio. Parece natural adiantar o campo agora, "já que estamos tocando na tabela". | Sem o barramento real, esse enum não tem transições verdadeiras para validar — vira campo morto ou, peor, é testado com estados que não refletem o fluxo real quando a integração chegar, exigindo re-trabalho. Modelar estado antes de conhecer o contrato do barramento é o erro clássico de "adivinhar a integração". | Adicionar via nova migration quando o barramento existir e o contrato (webhook, polling, fila) for conhecido — decisão já tomada no projeto (`Out of Scope`), correta. |
| Numeração "gapless" (sem buracos) para a OR | Documentos fiscais no Brasil (NF-e) têm exigência legal de sequência sem buracos, e é comum confundir "documento numerado" com "precisa ser gapless". | OR é documento *interno* pré-fiscal (a NF-e é emitida depois, pelo Linx, fora deste sistema). `Identity`/sequence do Postgres não decrementa em rollback — pode haver buracos numa falha de transação, e **isso é aceitável** para este documento. Perseguir gapless aqui exigiria lock explícito de linha única, contenção séria em geração em lote, e resolve um problema que não existe (nenhuma exigência legal recai sobre a OR). | Sequence/Identity padrão, como já decidido. Se um dia o Linx exigir gapless para o número que ele recebe, isso é problema do *código_linx* gerado pelo Linx, não do `nrOr` interno. |
| Inferir o `nrOr` no frontend a partir de agrupamento de linhas (como hoje faz `GROUP BY cd_prod_cor` em runtime) | O frontend já faz esse agrupamento hoje para montar a visão "1 produto + N clientes" — parece natural continuar assim e só trocar o número mockado por algo calculado no cliente. | Sem um número persistido e citável, duas rodadas diferentes para o mesmo produto ficam indistinguíveis no frontend (mesmo agrupamento, nenhuma chave estável) — o problema que este milestone existe para resolver. Também duplica lógica de agregação em dois lugares (backend gera, frontend re-deriva), gerando divergência quando o backend mudar o critério de agrupamento. | Backend expõe `nrOr` já pronto por linha/grupo (`GET /ordens-reserva` agrupado por `or_id`, itens de `GET /pedidos` carimbados com `nrOr`) — já é o desenho decidido. |
| Persistir/auditar o comportamento de Stand By agora | Já que o cabeçalho está sendo criado, parece um bom momento para também formalizar o que acontece com pedidos que ficam de fora da adequação. | Aumenta o escopo do milestone sem necessidade: o comportamento atual (pedidos em stand-by reaparecem para nova tentativa) já atende a operação; auditoria de stand-by é uma decisão de produto separada, não uma consequência técnica de ter um `nrOr`. | Mantido fora de escopo (decisão já registrada no projeto); revisitar como milestone independente se a operação pedir histórico de stand-by. |
| Renomear/remover o campo mockado direto na API sem preservar contrato | Tentação de "limpar" o payload já que o valor `255315` nunca foi um dado real. | Quebra o frontend em produção que já lê aquele campo (ainda que com valor fixo) — mudança destrutiva num contrato já consumido. | Adicionar `nrOr`/`codigoLinx`/`geradaEm` como campos novos (aditivo); remover o hardcode é uma mudança **no frontend**, não uma remoção de campo na API. |

## Feature Dependencies

```
Cabeçalho `ordens_reserva_cabecalho` (nº sequencial, cd_prod_cor, tipo, codigo_linx, criada_em)
    └──requires──> Migration 015 (head atual = 014, `or_id` só entra depois do backfill)
                       └──requires──> Grão canônico (nr_pedido, cd_prod_cor) da migration 014 (não muda)

`ordens_reserva.or_id` NOT NULL
    └──requires──> Backfill retroativo de todas as linhas existentes
                       └──requires──> Cabeçalho já populado (não dá para apontar FK para algo que não existe)

Escrita: 1 cabeçalho por produto por rodada em `salvar_ordens_reserva`
    └──requires──> Cabeçalho + or_id NOT NULL já migrados
    └──enhances──> Motor de adequação existente (não altera regra de negócio, só onde persiste)

Leitura: GET /ordens-reserva agrupado por or_id (nrOr/codigoLinx/geradaEm)
    └──requires──> Escrita do cabeçalho funcionando (não há o que ler antes disso)
GET /pedidos com itens carimbados com nrOr
    └──requires──> mesmo cabeçalho + FK

Frontend exibe nrOr real (remove 255315 hardcoded)
    └──requires──> API de leitura expondo nrOr (campo aditivo)

`codigo_linx` reservado (nullable)
    ──enhances (preparação futura)──> Integração real com Linx (fora de escopo deste milestone)

Status de envio ao Linx (pendente/enviado/erro)
    ──conflicts (escopo)──> Escopo mínimo deste milestone (decidido: fora de escopo até o barramento existir)

Numeração gapless
    ──conflicts (não aplicável)──> Natureza não-fiscal da OR (documento interno, sem exigência legal)
```

### Dependency Notes

- **Backfill requires cabeçalho populado:** a ordem de execução dentro da migration 015 importa — criar tabela, popular cabeçalhos agrupando linhas legadas por produto (usando algum critério determinístico de "rodada" para dados históricos, já que rodadas antigas não foram registradas explicitamente), só então aplicar `or_id NOT NULL`. Se a ordem for invertida, a migration falha ou perde dados.
- **Escrita requires migration completa:** `salvar_ordens_reserva` não pode gravar `or_id` antes de a coluna existir e estar NOT NULL-ready; isso é sequencial, não paralelizável com o backfill.
- **`codigo_linx` enhances integração futura, não este milestone:** o campo é escrito como NULL agora; nenhuma feature deste milestone depende dele estar preenchido. Ele existe para que a *próxima* integração não precise de nova migration disruptiva.
- **Status de envio conflicts com escopo mínimo:** adicionar esse campo agora forçaria decidir uma máquina de estados sem contrato real do barramento — motivo correto para ter ficado de fora.
- **Numeração gapless conflicts com natureza do documento:** não é uma decisão técnica de implementação, é uma constatação de que o requisito (gapless) simplesmente não se aplica a este tipo de documento — não há necessidade de resolver o "problema" que motivaria lock explícito de sequência.

## MVP Definition

### Launch With (v1.1 — este milestone)

- [ ] Tabela `ordens_reserva_cabecalho` com sequência própria (`Identity(start=255316)`), `cd_prod_cor`, `tipo`, `codigo_linx` NULL, `criada_em` — é o mínimo para ter um número de negócio persistido e citável
- [ ] `ordens_reserva.or_id` NOT NULL com backfill retroativo — sem isso, dados legados ficam órfãos e qualquer leitura nova quebra ou precisa de tratamento especial para linhas antigas
- [ ] `salvar_ordens_reserva` grava 1 cabeçalho por produto por rodada — é o comportamento que justifica a tabela existir; sem isso a tabela fica vazia para dados novos
- [ ] `GET /ordens-reserva` e `GET /pedidos` expõem `nrOr`/`codigoLinx`/`geradaEm` de forma aditiva — é o que permite ao frontend parar de usar o valor fixo
- [ ] Frontend troca `255315` pelo `nrOr` real — é o ponto de valor visível para a usuária final deste milestone
- [ ] Testes cobrindo o grão "1 OR por produto por rodada" (inclusive múltiplas rodadas gerando múltiplos cabeçalhos para o mesmo produto) — sem isso, uma regressão futura no agrupamento passa silenciosa

### Add After Validation (v1.x)

- [ ] Campos de status de envio ao Linx (`pendente`/`enviado`/`erro`) — adicionar **quando o acesso ao barramento existir e o contrato de integração for conhecido**; tentar adivinhar agora é o anti-feature já descartado
- [ ] Preenchimento automático de `codigo_linx` via job/consumer do barramento — depende diretamente do item anterior
- [ ] Revisão do formato do `nrOr` se o Linx exigir um formato próprio (ex.: prefixo por filial/ano) — só decidir quando o contrato real do Linx for conhecido, para não desenhar em cima de suposição

### Future Consideration (v2+)

- [ ] Auditoria de pedidos em Stand By — decisão de produto independente, sem gatilho técnico deste milestone
- [ ] Ligar o frontend ao `PUT /alterar-grade` — gap conhecido e documentado, mas é outro fluxo (edição de grade), não decorre da persistência do cabeçalho
- [ ] Reconciliação/idempotência na integração real com Linx (evitar reenvio duplicado ao barramento) — só faz sentido desenhar quando o barramento e seu comportamento de retry forem conhecidos

## Feature Prioritization Matrix

| Feature | Valor para o usuário | Custo de implementação | Prioridade |
|---------|------------|---------------------|----------|
| Cabeçalho + numeração sequencial | ALTO (elimina número fictício visível à operação) | BAIXA | P1 |
| Backfill de `or_id` para linhas legadas | ALTO (evita quebrar leitura para OR antigas) | MÉDIA (migration com dados) | P1 |
| `codigo_linx` reservado (nullable) | BAIXO agora / ALTO no futuro | BAIXA | P1 (custo trivial, paga dividendo depois) |
| Exposição de `nrOr`/`geradaEm`/`codigoLinx` nas APIs | ALTO (frontend depende disso) | BAIXA (campos aditivos) | P1 |
| Frontend exibindo número real | ALTO (é o resultado visível do milestone) | BAIXA | P1 |
| Status de envio ao Linx | MÉDIO (só quando o barramento existir) | MÉDIA-ALTA (exige contrato real) | P3 (fora deste milestone) |
| Auditoria de Stand By | BAIXO-MÉDIO (não bloqueia operação atual) | MÉDIA | P3 |

**Priority key:**
- P1: Necessário para este milestone
- P2: Deveria vir a seguir quando possível (nenhum item deste levantamento se qualifica — o escopo do milestone já é o P1 completo)
- P3: Depende de evento externo (barramento Linx existir) ou é decisão de produto separada

## Análise de Padrões do Domínio (em vez de "concorrentes")

Como este não é um produto de mercado com concorrentes diretos, a comparação relevante é com padrões de ERPs de automation/varejo nacionais e com práticas gerais de integração — não com produtos competidores.

| Aspecto | Padrão observado em ERPs nacionais (Senior, Sankhya) | Padrão geral de integração (correlação/idempotência) | Nossa abordagem |
|---------|--------------------------------------------------------|----------------------------------------------------------|--------------|
| Modelo cabeçalho + itens | Documento único (nº do pedido/NUNOTA) com itens vinculados por sequência dentro do documento | Mensagens de integração carregam um "envelope" com metadados (timestamp, sistema de origem, correlation ID) separado do payload de negócio | `ordens_reserva_cabecalho` (documento) + `ordens_reserva` (itens/linhas produto×cliente) — mesmo padrão, sem envelope de mensageria porque não há barramento ainda |
| Numeração | Sequência interna gerenciada pelo banco (com possibilidade de reuso de números livres em alguns ERPs) — não é gapless por padrão fora de exigência fiscal | Correlação por ID estável, não por posição sequencial | `Identity` do Postgres, sem reuso de buracos (mais simples), suficiente porque não há exigência fiscal sobre a OR |
| Campo de correlação com sistema externo | Tabelas de integração (ex.: WMB_CUPOM_OB) guardam o identificador do sistema externo já nas tabelas operacionais, populado quando a integração roda | `external_id`/`erp_code` nullable, tipo permissivo (string), preenchido só quando a integração roda; upsert por ID externo evita duplicidade | `codigo_linx` String(32) nullable — mesmo padrão, decisão já tomada corretamente |
| Estado de sincronização | ERPs com integração ativa mantêm status por documento (enviado/pendente/erro) | Idempotência via chave de dedup no consumidor da mensagem | Explicitamente fora de escopo agora — correto, pois não há contrato de barramento para modelar o estado real |

## Sources

- [6312 - Sequência de numeração dos pedidos - Suporte Senior](https://suporte.senior.com.br/hc/pt-br/articles/4408634923412-6312-Sequ%C3%AAncia-de-numera%C3%A7%C3%A3o-dos-pedidos) — MÉDIA confiança (documentação de suporte de ERP nacional, não Linx diretamente, mas mesmo domínio de automation/varejo)
- [Inclusão e Alteração de Itens no Pedido — Sankhya Developer](https://developer.sankhya.com.br/reference/post_incaltitempedido) — MÉDIA confiança (confirma padrão NUNOTA + SEQUENCIA de item, ou seja, cabeçalho + itens)
- [PostgreSQL: Sequences vs. Invoice numbers — CYBERTEC](https://www.cybertec-postgresql.com/en/postgresql-sequences-vs-invoice-numbers/) — ALTA confiança (fonte técnica especializada em Postgres, confirma que sequences não são gapless e quando isso importa)
- [howto-everything: postgres-gapless-counter-for-invoice-purposes.md](https://github.com/kimmobrunfeldt/howto-everything/blob/master/postgres-gapless-counter-for-invoice-purposes.md) — MÉDIA confiança (repositório comunitário, mas alinhado com a fonte CYBERTEC)
- [Linx Share — Integração Linx / ERP](https://share.linx.com.br/pages/viewpage.action?pageId=227409737) — BAIXA confiança (acesso restrito/institucional; não foi possível validar detalhes do contrato de barramento real da Linx; conteúdo indexado sugere apenas a existência de tabelas de integração como `WMB_CUPOM_OB`, sem detalhamento de payload)
- [The Architect's Guide to Data Integration Patterns — Medium](https://medium.com/@prayagvakharia/the-architects-guide-to-data-integration-patterns-migration-broadcast-bi-directional-a4c92b5f908d) — MÉDIA confiança (artigo de arquitetura, consistente com práticas amplamente documentadas de correlação/idempotência)
- [Idempotency in Distributed Systems — Alok](https://aloknecessary.github.io/blogs/idempotency-distributed-systems/) — MÉDIA confiança (reforça o padrão de idempotency key / upsert por ID externo, usado para validar a decisão de `codigo_linx` nullable)
- `.planning/PROJECT.md` do próprio projeto — ALTA confiança (fonte primária das decisões já tomadas; usada para verificar alinhamento, não para "descobrir" requisitos)

## Gaps / Limitações desta pesquisa

- **Não foi possível acessar o contrato real de integração da Linx** (formato exato do `codigo_linx`, se o barramento empurra ou se o sistema precisa consultar/polling, se há exigência de status). O acesso a essa documentação depende de credenciais/parceria que a equipe de TI ainda não obteve — confirmado como bloqueio conhecido no `PROJECT.md`. Isso é aceitável para este milestone porque o objetivo explícito é "banco preparado", não "integração funcionando".
- A decisão de o `nrOr` **ser** o `id` da tabela cabeçalho (em vez de um número de negócio desacoplado da PK técnica) é uma simplificação razoável para o estado atual, mas é o ponto mais provável de precisar revisão quando o contrato real do Linx aparecer (ex.: se o Linx exigir um formato de número com prefixo). Vale registrar isso como possível item de re-trabalho, não como erro da decisão atual.

---
*Feature research for: persistência de cabeçalho de OR com numeração sequencial e preparação para integração ERP Linx (automation de moda)*
*Researched: 2026-08-04*
