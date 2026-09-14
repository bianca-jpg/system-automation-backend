# Phase 2: Ingestão da referência + conversão de grade - Context

**Gathered:** 2026-08-05
**Status:** Ready for planning

<domain>
## Phase Boundary

A referência tamanho→posição (`programa_estagio.refined.system_automation_prod_tamanho_ref`) passa a chegar ao Postgres a cada sync de 2h como 5ª fonte (full refresh, commit único com as outras 4), e nasce uma função pura — testada sem banco, com dados reais ingeridos nesta fase — que converte a grade interna `{sg_tamanho: qtd}` de um produto nas colunas posicionais `{"e1": qtd, ..., "e48": qtd}`. Requirements: ING-01, LINX-03. A escrita das linhas Linx na geração de OR é da Fase 3 (fora de escopo aqui).

</domain>

<decisions>
## Implementation Decisions

### Validação de posição na ingestão
- **D-01:** Linha da referência com `nr_posicao` inválido (não-numérico, < 1 ou > 48) é **descartada na camada de tradução** com aviso no log — mesmo padrão de tolerância do `_parse_int` das outras fontes. **SEM CheckConstraint no banco** e sem migration nova: como o sync é um commit único, uma constraint dura faria UMA linha suja derrubar as 5 fontes juntas. (Decisão da usuária, ciente da sugestão WR-02 do code review da Fase 1 — o descarte na tradução cobre o risco na prática.)

### Proteção contra referência vazia
- **D-02:** Se a view do Databricks retornar **0 linhas com sucesso**, a sincronização **NÃO substitui** a referência — mantém o snapshot anterior de `produto_tamanho_posicao` e loga warning. Desvio deliberado e documentado do padrão full-refresh das 4 fontes irmãs: referência é dado de cadastro, vazio legítimo é quase impossível e um vazio por erro na origem zeraria as grades E1..E48 da Fase 3. (Falha de conexão/HTTP já é protegida pelo rollback da transação — não requer nada novo.)

### Conflito de posição (dado sujo)
- **D-03:** Dois tamanhos do mesmo produto apontando para a mesma posição: **último vence + aviso** no log com produto e posição em conflito. O sync nunca trava por dado sujo; o aviso permite corrigir a origem.

### Formato dos avisos no log
- **D-04:** Avisos de tamanhos sem posição na referência são **agregados por produto** — 1 linha por produto com a lista de tamanhos ignorados (ex.: `produto ML.18.0315|001: tamanhos sem posição na referência: [M, GG]`). Vale tanto para a ingestão quanto para a função de conversão. Nunca 1 linha por item (inundaria o log no sync de 2h).

### Claude's Discretion
- Nome exato das funções/arquivos novos (seguir os análogos do módulo: `ler_referencia_tamanhos`, `substituir_referencia_tamanhos`, etc.)
- Assinatura exata da função de conversão (recebe grade interna + posições; devolve dict e1..e48 + lista de tamanhos ignorados, ou equivalente)
- Estrutura da fixture de dados reais para o teste puro (amostra estática capturada do banco após a primeira ingestão real)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Decisões do milestone
- `.planning/PROJECT.md` — Key Decisions travadas (layout Linx, full refresh, sem dependências novas)
- `.planning/ROADMAP.md` — 5 success criteria da Fase 2

### O que a Fase 1 construiu (base desta fase)
- `.planning/phases/01-schema-linx-e-refer-ncia-de-posi-o/01-01-SUMMARY.md` — models criados (ProdutoTamanhoPosicao, OrdemReservaLinx)
- `.planning/phases/01-schema-linx-e-refer-ncia-de-posi-o/01-REVIEW.md` — WR-01/WR-02 e infos do code review (contexto das decisões D-01)

### Código análogo (padrões a seguir)
- `app/modules/ingestao/infrastructure/databricks_reader.py` — molde do reader da 5ª fonte
- `app/modules/ingestao/domain/traducao_databricks.py` — molde do parse tolerante (D-01)
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` — molde do substituir_* (D-02 desvia aqui: guard de vazio)
- `app/modules/ingestao/application/casos_uso.py` — `sincronizar_tudo` (commit único, 5ª chamada entra aqui)
- `app/tests/test_ingestao_sync.py` — molde do teste de integração citado no criterion 1

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `executar_consulta` (databricks_client.py): cliente HTTP pronto — o reader novo só monta o SELECT
- `_parse_int` (traducao_databricks.py): parse tolerante reutilizável para `nr_posicao`
- Padrão DELETE+INSERT sem commit (repositorio_snapshot.py): base do `substituir_referencia_tamanhos`, com o guard de vazio (D-02) por cima
- `SincronizacaoResponse` (schemas.py): ganha o contador da 5ª fonte (atenção: já existe bug conhecido de campo faltante `faturamento_colecoes` — não repetir o erro)

### Established Patterns
- Env vars de tabela origem em `settings.py` + `.env.example` (`DATABRICKS_TABELA_TAMANHO_REF`)
- Fonte nova = reader → tradução (domínio puro) → repositório snapshot → caso de uso → contador na resposta
- Código e nomes em português; testes em `app/tests/` com mocks de reader

### Integration Points
- `sincronizar_tudo` (casos_uso.py da ingestão): 5ª chamada antes do commit único
- Função de conversão vive no módulo `pedidos` (domain/), consumidora da referência — será usada pela Fase 3 na geração de OR
- Ambiente: `uv run` local falha no Windows (pyicu) — verificações via `docker compose -f .docker/docker-compose.yml exec -T api uv run ...`

</code_context>

<specifics>
## Specific Ideas

- O exemplo de conversão posição→grade fornecido pela usuária (query Databricks com `posexplode` + join em `nr_posicao`) é a referência semântica: esta fase implementa o caminho INVERSO (tamanho→posição).
- Log de conflito deve citar produto e posição (D-03); log de tamanho ignorado deve citar produto e lista de tamanhos (D-04).

</specifics>

<deferred>
## Deferred Ideas

- CheckConstraint `nr_posicao BETWEEN 1 AND 48` no banco (WR-02 do review) — descartada por ora (D-01); reavaliar se algum dia houver escrita em `produto_tamanho_posicao` fora do sync.
- WR-01 do review (filtro de schema no query de PK do `test_schema_guard.py`) — dívida pré-existente de teste, não pertence a esta fase; candidata a `/gsd-quick` avulso.

</deferred>

---

*Phase: 2-Ingestão da referência + conversão de grade*
*Context gathered: 2026-08-05*
