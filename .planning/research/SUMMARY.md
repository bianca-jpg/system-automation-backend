# Project Research Summary

**Project:** automation OR — Otimização de Ordens de Reserva (milestone v1.1: Persistência da OR de negócio)
**Domain:** Persistência de cabeçalho de documento de negócio (Ordem de Reserva) em monólito modular FastAPI/SQLAlchemy 2.0 async, com numeração sequencial própria (Postgres Identity), backfill retroativo via Alembic, e preparação mínima para integração futura com ERP Linx
**Researched:** 2026-08-04
**Confidence:** HIGH

## Executive Summary

Este milestone não introduz um domínio novo nem uma dependência nova — ele adiciona uma camada de persistência (tabela de cabeçalho) sobre uma feature já em produção (motor de adequação + `ordens_reserva` por linha produto×cliente). A resposta correta, confirmada pelas quatro pesquisas, é usar corretamente o que já está instalado: `sa.Identity(start=255316)` do SQLAlchemy 2.0 para a PK do cabeçalho (em vez de `SERIAL`/`Sequence` solta), e uma migration Alembic escrita à mão (não autogerada) que segue a sequência clássica para adicionar coluna NOT NULL a tabela populada: `add_column(nullable=True)` seguido de backfill em SQL puro (`INSERT...SELECT` / `UPDATE...FROM`), depois `alter_column(nullable=False)` e `create_foreign_key`. Nenhuma biblioteca de mensageria, SDK do Linx ou modelagem de status de envio deve ser adicionada agora: o protocolo do barramento é desconhecido, e o padrão já validado no próprio repositório (cliente Databricks via httpx puro, sem SDK) reforça essa decisão.

A abordagem recomendada, do ponto de vista arquitetural, é: o cabeçalho (OrdemReservaCabecalho) é criado dentro do repositório (salvar_ordens_reserva), usando flush() para obter o id gerado pelo Postgres antes de vincular or_id às linhas, nunca commit() isolado, preservando a convenção "repositório não comita, caso de uso decide". A leitura (carregar_ordens_reserva) deve migrar de tupla posicional para NamedTuple, já que o retorno cresce de 6 para 8 campos e há 3 pontos de consumo. O campo codigo_linx (nullable, String(32)) já nasce como o "aggregate root" certo para uma futura integração via outbox, sem precisar de infraestrutura de mensageria hoje.

O maior risco identificado, e o único ainda sem decisão fechada no PROJECT.md, não é técnico, é de produto: a regra "1 cabeçalho por produto por rodada de geração" pode fragmentar visualmente o mesmo produto em múltiplos cards no dashboard quando processado em rodadas separadas, quebrando a garantia atual testada ("dois pedidos do mesmo produto = UMA ordem"). Isso deve ser validado explicitamente com a usuária de negócio antes da fase de escrita, não descoberto em produção. Riscos técnicos secundários (lock em SET NOT NULL, autogenerate espúrio com Identity, mutação de JSONB, contrato HTTP sem alias automático) são bem mapeados e têm mitigação direta e barata.

## Key Findings

### Recommended Stack

Nenhuma tecnologia nova é necessária. SQLAlchemy 2.0.49 (suporta Identity nativamente), Alembic 1.18.4 (livre dos bugs antigos de autogenerate+Identity de 2021), asyncpg 0.31.0 (não interfere em DDL). A decisão correta é usar Identity(start=255316) em vez de SERIAL/autoincrement=True (evitaria um ALTER SEQUENCE manual e frágil) e em vez de Sequence nomeada solta (cerimônia extra sem ganho).

Core technologies:
- sa.Identity(start=255316) (SQLAlchemy 2.0.49, já instalado): PK do cabeçalho com valor inicial customizado
- op.execute mais op.add_column/op.alter_column (Alembic 1.18.4, já instalado): backfill em SQL puro dentro da migration 015
- httpx (já instalado, sem SDK novo): precedente do cliente Databricks custom valida que a futura integração Linx também deve evitar SDK de terceiro até o contrato do barramento ser conhecido

### Expected Features

Must have (table stakes):
- Cabeçalho separado das linhas (padrão "documento + itens", igual a ERPs nacionais como Senior/Sankhya)
- Número único e imutável por documento, com continuidade visual do mock 255315 para 255316
- Backfill de dados legados sem NULL residual em or_id
- Contrato HTTP aditivo (não quebra o frontend em produção)
- Agrupamento determinístico "1 OR = 1 produto por rodada", expondo geradaEm

Should have (differentiators):
- codigo_linx reservado (nullable, String(32)) desde já
- Rastreabilidade histórica por rodada (múltiplos nrOr para o mesmo produto ao longo do tempo)

Defer (v2+):
- Status de envio ao Linx (pendente/enviado/erro)
- Numeração "gapless" — OR é documento interno pré-fiscal
- Auditoria de Stand By
- Frontend ligado a PUT /alterar-grade

### Architecture Approach

O cabeçalho é criado dentro de salvar_ordens_reserva (não no caso de uso), usando db.flush() para obter o id gerado pela Identity antes de vincular or_id às linhas, o commit() continua único, no caso de uso. A leitura (carregar_ordens_reserva) deve migrar de tupla posicional (6 para 8 campos, 3 pontos de consumo) para NamedTuple. A futura integração Linx deve nascer como módulo isolado (app/modules/integracao_linx/, seguindo o padrão de ingestao), com sua própria função de leitura dedicada.

Major components:
1. OrdemReservaCabecalho (novo model) — Identity(start=255316), cd_prod_cor, tipo, codigo_linx, criada_em
2. Migration 015 (manual, padrão da 014) — cria cabeçalho + or_id NOT NULL com backfill em 3 passos
3. salvar_ordens_reserva modificado — agrupa por produto, cria cabeçalho via flush, atribui or_id, retorna ids novos (seam para futuro outbox)
4. carregar_ordens_reserva + LinhaOrdemReserva (NamedTuple)
5. schemas.py/routes.py — OrdemReservaOut ganha nrOr/codigoLinx/geradaEm; /pedidos ganha nrOr por item

### Critical Pitfalls

1. Fragmentação da OR entre rodadas — regra "1 cabeçalho por produto por rodada" pode quebrar a garantia atual "mesmo produto = 1 card"; decisão ainda "Pending". Validar com a usuária antes de codar.
2. Identity() mais autogenerate frágil — escrever a migration 015 manualmente; confirmar diff vazio depois.
3. ALTER COLUMN ... SET NOT NULL sem NOT VALID+VALIDATE CONSTRAINT — trava a tabela com lock ACCESS EXCLUSIVE; aceitável no volume atual, registrar janela de manutenção.
4. Backfill importando ORM "vivo" na migration — nunca importar app.modules.*.models em alembic/versions/.
5. Tupla 6 para 8 em carregar_ordens_reserva — migrar para NamedTuple elimina risco de bug silencioso.

## Implications for Roadmap

### Phase 1: Validação da regra de negócio + Schema (models + migration)
Rationale: Decisão de produto mais cara de errar (Pitfall 1) precisa ser confirmada antes de qualquer código; schema precisa existir antes de escrita/leitura serem testáveis.
Delivers: Decisão de negócio registrada; OrdemReservaCabecalho model; migration 015 aplicada; env.py/test_schema_guard.py atualizados.
Addresses: Table stakes "cabeçalho separado", "backfill sem NULL residual"
Avoids: Pitfalls 1, 2, 3, 4, 10

### Phase 2: Escrita — cabeçalho por produto por rodada em salvar_ordens_reserva
Rationale: Depende do schema (Phase 1); gera dados reais para a Phase 3.
Delivers: _criar_cabecalho() + salvar_ordens_reserva modificado; testes cobrindo "1 cabeçalho por produto por rodada".
Uses: sa.Identity, db.flush()
Avoids: Pitfall 6

### Phase 3: Leitura — APIs expondo nrOr/codigoLinx/geradaEm
Rationale: Depende de dados reais gerados na Phase 2.
Delivers: carregar_ordens_reserva como NamedTuple; _montar_pedidos/_format_items carimbando nrOr; OrdemReservaOut ganhando campos novos.
Addresses: "contrato HTTP aditivo", "agrupamento determinístico por rodada"
Avoids: Pitfalls 7, 8, 9

### Phase 4: Frontend — remoção do hardcode 255315
Rationale: Depende da Phase 3 em produção; cruza para outro repositório git.
Delivers: product-grade-detail-modal.tsx exibindo nrOr real; tipos TypeScript opcionais até confirmar backend.
Avoids: UX Pitfall (fragmentação visual sem aviso), Pitfall 9

### Phase Ordering Rationale

- Schema antes de aplicação: migration manual precisa espelhar o model.
- Escrita antes de leitura: sem dado real não há o que ler.
- Validação de negócio dentro da Phase 1 porque é bloqueante para todas as fases seguintes.
- Frontend por último, em PR separada, contrato aditivo puro.
- Integração real com Linx fica fora do roadmap deste milestone — condicionada a evento externo.

### Research Flags

Nenhuma fase técnica precisa de research-phase — os 4 documentos já cobrem schema, escrita, leitura e frontend com exemplos verificados contra o código real. O único ponto de atenção é a decisão de negócio da Phase 1, que exige validação com stakeholder, não pesquisa adicional.

Phases with standard patterns (skip research-phase): todas (1, 2, 3, 4) — padrões já demonstrados no próprio repositório (migration 014, convenção flush/commit, dict aditivo).

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Stack já instalado, versões confirmadas via uv.lock; Identity verificado na doc oficial SQLAlchemy 2.0 |
| Features | MEDIA-ALTA | Padrões de ERP nacional corroboram cabeçalho+itens; sem fonte oficial da Linx sobre formato do barramento |
| Architecture | HIGH | Fundada na leitura direta do código atual do repositório |
| Pitfalls | HIGH (específicos) / MEDIUM-HIGH (genéricos) | Específicos derivados do código; genéricos verificados via issues oficiais Alembic/SQLAlchemy e docs Postgres |

Overall confidence: HIGH

### Gaps to Address

- Decisão de negócio "1 cabeçalho por produto por rodada" ainda "Pending" — fechar com a usuária antes da fase de escrita.
- Formato real do codigo_linx/contrato do barramento Linx — sem acesso à documentação; aceitável pois o objetivo é "banco preparado".
- Estratégia de agrupamento do backfill para linhas legadas (por cd_prod_cor distinto, recomendado) — confirmar explicitamente na implementação da migration 015.

## Sources

### Primary (HIGH confidence)
- Documentação oficial SQLAlchemy 2.0 (dialeto PostgreSQL, Identity columns)
- PostgreSQL Documentation — Identity Columns / ALTER TABLE
- Código-fonte do repositório: models.py, repositorio_ordens.py, casos_uso.py, schemas.py, routes.py, alembic/versions/014_or_por_produto.py, alembic/env.py, test_schema_guard.py, test_pedidos_routes.py
- uv.lock, .planning/PROJECT.md, .planning/codebase/*

### Secondary (MEDIUM confidence)
- GitHub issues Alembic/SQLAlchemy sobre Identity + autogenerate (alembic#775, #821, discussions#1181, sqlalchemy#6129)
- Suporte Senior / Sankhya Developer — padrão cabeçalho+itens
- CYBERTEC PostgreSQL — sequences vs. invoice numbers
- Artigos sobre padrões de integração/idempotência

### Tertiary (LOW confidence)
- Linx Share (documentação institucional) — acesso restrito, sem detalhamento do contrato de barramento

---
Research completed: 2026-08-04
Ready for roadmap: yes
