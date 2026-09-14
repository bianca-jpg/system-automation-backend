# Phase 2: Ingestão da referência + conversão de grade - Research

**Researched:** 2026-08-05
**Domain:** Ingestão full-refresh Databricks→Postgres (5ª fonte) + função de domínio pura de conversão de grade tamanho→posição
**Confidence:** HIGH

## Summary

Esta fase é puramente uma extensão de um pipeline já maduro e replicado 4 vezes no mesmo módulo (`app/modules/ingestao/`). Não há framework novo para aprender: o trabalho é "seguir o molde" com precisão cirúrgica em 5 arquivos (`databricks_reader.py`, `traducao_databricks.py`, `agregacao.py`, `repositorio_snapshot.py`, `casos_uso.py`) e adicionar 1 função pura nova em `app/modules/pedidos/domain/`. Toda a pesquisa abaixo foi feita lendo o código real do repositório (não framework externo) e validando ao vivo no ambiente Docker já em execução (`docker compose -f .docker/docker-compose.yml exec api ...`).

Dois achados concretos e verificados mudam o desenho do plano:

1. **Bug real confirmado por execução direta** (não suposição): `SincronizacaoResponse` (`app/modules/ingestao/schemas.py`) não declara o campo `faturamento_colecoes`, e como o `model_config` não define `extra`, o Pydantic v2 usa o default `extra='ignore'` — o valor retornado por `sincronizar_tudo` é **silenciosamente descartado** na resposta HTTP. Confirmado rodando o model real dentro do container (ver seção Pitfalls). A 5ª fonte precisa de um campo novo no mesmo schema — se o plano não tratar isso explicitamente, o mesmo bug se repete para o campo novo.
2. **D-02 (guard de referência vazia) não tem precedente no código existente** — as 4 fontes atuais sempre fazem DELETE+INSERT incondicional. A decisão da usuária cria a primeira exceção a essa regra. A pesquisa aponta exatamente em qual camada esse `if` deve viver (caso de uso, não repositório) e por quê.

**Recomendação principal:** Copiar o padrão exato de `sincronizar_faturamento_colecao` (a 4ª fonte, adicionada mais recentemente — é o molde mais fresco) para a 5ª fonte, com 3 desvios pontuais e documentados: (a) descarte de `nr_posicao` inválido na tradução (D-01), (b) guard de "0 linhas válidas → não substituir" no caso de uso, antes de chamar o repositório (D-02), (c) agregação de avisos por produto em vez de um único contador global (D-03/D-04). A função pura de conversão vive em `app/modules/pedidos/domain/` (novo arquivo), é re-exportada em `app/modules/pedidos/service.py` (padrão idêntico ao `motor_adequacao.py`), e — seguindo o texto literal do success criterion 5 ("a conversão emite um aviso no log") e o precedente já existente de funções "puras" que logam (`agrupar_por_produto` em `motor_adequacao.py`) — a própria função loga o aviso agregado por produto, além de devolver a lista de tamanhos ignorados para permitir asserts diretos no teste sem depender de `caplog`.

## Architectural Responsibility Map

Adaptado às camadas DDD do próprio monólito (não há tiers de browser/CDN nesta fase — é 100% backend):

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Leitura HTTP da view Databricks (5ª fonte) | Infrastructure (`ingestao/infrastructure/databricks_reader.py`) | — | Único ponto que sabe montar o SELECT; reusa `executar_consulta` já existente |
| Parse/validação de `nr_posicao` (D-01) | Domain puro (`ingestao/domain/traducao_databricks.py`) | — | Anti-corruption layer — mesmo lugar de `_parse_int`/`_parse_float` |
| Dedup + detecção de conflito de posição (D-03/D-04) | Domain puro (`ingestao/domain/agregacao.py`) | — | Mesma responsabilidade de `agregar_estoque`/`agregar_itens_pedidos`; conflito é um problema de agregação, não de I/O |
| Guard de referência vazia (D-02) | Application (`ingestao/application/casos_uso.py`) | — | Decide **se** chama o repositório — decisão de orquestração, não de persistência bruta (ver Pitfalls) |
| DELETE+INSERT sem commit | Infrastructure (`ingestao/infrastructure/repositorio_snapshot.py`) | — | Repositório fica "burro" e simétrico aos 3 irmãos — nunca decide, apenas executa |
| Commit único das 5 fontes | Application (`ingestao/application/casos_uso.py::sincronizar_tudo`) | — | Ponto único de transação — já existente, só estende a chamada |
| Contador na resposta HTTP | API (`ingestao/schemas.py` + `routes.py`) | — | Superfície de contrato — cuidado com o bug conhecido (Pitfalls) |
| Conversão grade→posições (função pura) | Domain puro (`pedidos/domain/` — novo arquivo) | — | Consumida pela Fase 3; vive no módulo "consumidor" (pedidos), não em ingestao, seguindo a decisão já registrada em `01-CONTEXT`/`PROJECT.md` |

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ING-01 | Sync de 2h ingere a referência tamanho→posição do Databricks como 5ª fonte, full refresh, em `produto_tamanho_posicao` | Pipeline completo mapeado (reader→tradução→agregação→repositório→caso de uso→`sincronizar_tudo`→schema), com D-01/D-02/D-03/D-04 localizados camada a camada |
| LINX-03 | Grade interna convertida para `e1..e48` via referência; tamanho sem posição gera aviso no log | Função pura desenhada em `pedidos/domain/`, assinatura recomendada, estratégia de teste com dados reais capturados após a 1ª ingestão real |

## Standard Stack

### Core

Nenhuma dependência nova. O `PROJECT.md` trava explicitamente "sem dependências novas para este milestone" — confirmado no `pyproject.toml` (não inspecionado linha a linha porque a decisão já está travada e o padrão das 4 fontes existentes não usa nada além do que já está instalado).

| Componente | Já usado por | Reuso nesta fase |
|---|---|---|
| `httpx` (via `executar_consulta`) | `app/shared/infrastructure/databricks_client.py` | Reader novo só monta o SELECT; toda paginação/EXTERNAL_LINKS fallback é reusada como está — **não reimplementar** [VERIFIED: leitura direta do arquivo] |
| SQLAlchemy 2.0 async + asyncpg | Todo o `ingestao`/`pedidos` | Mesmo padrão `Mapped`/`mapped_column`, sessão via `AsyncSession` |
| Pydantic v2 | `schemas.py` de todos os módulos | Extensão de `SincronizacaoResponse` |
| pytest + pytest-asyncio (`asyncio_mode = "auto"`) | `app/tests/` | Testes novos seguem `test_ingestao_sync.py` como molde exato |

### Package Legitimacy Audit

**Não aplicável** — esta fase não instala nenhum pacote novo. `PROJECT.md` (Constraints) trava "sem novas dependências para este milestone"; toda a implementação reusa bibliotecas já presentes (`httpx`, `sqlalchemy`, `pydantic`, `pytest`). Nenhum `pip install`/`uv add` é esperado nos planos desta fase.

## Architecture Patterns

### System Architecture Diagram

```
Databricks (view programa_estagio.refined.system_automation_prod_tamanho_ref)
        │  SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM <tabela>
        ▼
[infra] databricks_reader.ler_referencia_tamanhos()
        │  linhas brutas (list[dict], valores como string)
        ▼
[domain] traducao_databricks._parse_posicao(v)          <- NOVO helper (D-01)
        │  valida 1..48; inválido -> None (descartado)
        ▼
[domain] agregacao.agregar_referencia_tamanhos(linhas)   <- NOVA função
        │  dedup por (cd_prod_cor, sg_tamanho)
        │  detecta conflito de posição por produto (D-03: último vence)
        │  acumula conflitos_por_produto {cd_prod_cor: [avisos]} (D-04)
        ▼
   agrupado (dict), descartadas (int), conflitos_por_produto (dict)
        │
        ▼
[application] casos_uso.sincronizar_referencia_tamanhos(db)   <- NOVA função
        │
        ├─ if not agrupado:                                    <- GUARD D-02
        │      logger.warning(...); return contagem_atual       (repositório NUNCA chamado)
        │
        ├─ else: await substituir_referencia_tamanhos(db, agrupado.values())
        │        loga 1 linha por produto em conflito (D-04)
        │        loga resumo (total/descartadas) — padrão já existente
        ▼
[infrastructure] repositorio_snapshot.substituir_referencia_tamanhos()  <- NOVA função
        │  DELETE FROM produto_tamanho_posicao; INSERT ... (sem commit)
        ▼
   Postgres: produto_tamanho_posicao (full refresh)
        ▲
        │  chamada como 5ª etapa, ANTES do commit único
[application] casos_uso.sincronizar_tudo(db)
        │  pedidos, estoque, processados_erp, faturamento, referencia_tamanho_posicao
        └─ await db.commit()   <- atomicidade das 5 fontes


--- Função pura de conversão (consumida pela Fase 3) ---

pedidos/domain/<novo arquivo>.converter_grade_para_posicoes(
    grade: {sg_tamanho: qtd}, referencia: {sg_tamanho: nr_posicao}, cd_prod_cor: str
) -> {"e1": qtd, ..., "e48": qtd}
        │
        ├─ tamanho em `grade` sem chave em `referencia` -> ignorado
        │      logger.warning agregado: "produto X: tamanhos sem posição: [...]" (D-04)
        ├─ dois tamanhos de `grade` mapeando pra mesma posição -> último processado vence (D-03)
        │      logger.warning: "produto X: conflito de posição Y entre tamanhos A e B"
        └─ devolve (posicoes: dict, tamanhos_ignorados: list[str])  <- testável sem log
```

### Recommended Project Structure

Nenhuma pasta nova — apenas arquivos estendidos e 1 arquivo novo:

```
app/modules/ingestao/
├── infrastructure/
│   ├── databricks_reader.py        # + ler_referencia_tamanhos()
│   └── repositorio_snapshot.py     # + substituir_referencia_tamanhos()
├── domain/
│   ├── traducao_databricks.py      # + _parse_posicao()
│   └── agregacao.py                # + agregar_referencia_tamanhos()
├── application/
│   └── casos_uso.py                # + sincronizar_referencia_tamanhos(); sincronizar_tudo() estendido
├── models.py                       # (já existe desde a Fase 1 — ProdutoTamanhoPosicao)
└── schemas.py                      # SincronizacaoResponse + campo novo (+ fix opcional do bug existente)

app/modules/pedidos/
├── domain/
│   └── grade_linx.py                # NOVO — converter_grade_para_posicoes() (nome sugerido, ver Discretion)
└── service.py                       # + re-export da função nova (padrão motor_adequacao.py)

app/tests/
├── test_ingestao_sync.py            # + casos da 5ª fonte (D-01/D-02/D-03/D-04)
└── test_grade_linx.py               # NOVO — testes puros com fixture de dados reais capturados
```

### Pattern 1: Fonte nova = reader → tradução → agregação → repositório → caso de uso → contador

**O que é:** O molde replicado 4 vezes; a 4ª fonte (`sincronizar_faturamento_colecao`) é o exemplo mais recente e mais simples de seguir porque não tem filtro de mês/ano complexo.

**Quando usar:** Sempre que uma nova fonte Databricks entra no full refresh.

**Exemplo real (4ª fonte, hoje em produção — `app/modules/ingestao/application/casos_uso.py`):**
```python
# Fonte: app/modules/ingestao/application/casos_uso.py (código real do repo)
async def sincronizar_faturamento_colecao(db: AsyncSession) -> int:
    linhas = await ler_faturamento_colecao()
    processadas = processar_faturamento_colecao(linhas)
    await substituir_faturamento_colecao(db, processadas)
    logger.info(
        "Faturamento por coleção sincronizado: %d pontos (%d linhas brutas).",
        len(processadas), len(linhas),
    )
    return len(processadas)
```

**Adaptação recomendada para a 5ª fonte (com os 3 desvios D-01/D-02/D-03-D-04):**
```python
# app/modules/ingestao/application/casos_uso.py — NOVA função
async def sincronizar_referencia_tamanhos(db: AsyncSession) -> int:
    linhas = await ler_referencia_tamanhos()

    agrupado, descartadas, conflitos_por_produto = agregar_referencia_tamanhos(linhas)

    if not agrupado:
        # D-02: desvio deliberado do full-refresh incondicional. O repositório
        # NUNCA é chamado aqui — o snapshot anterior de produto_tamanho_posicao
        # permanece intacto. Falha de conexão/HTTP já é coberta pelo raise de
        # ler_referencia_tamanhos + rollback da transação; este guard cobre só
        # o caso "a view respondeu, mas devolveu 0 linhas válidas".
        logger.warning(
            "Referência tamanho->posição: 0 linhas válidas (%d linhas brutas) - "
            "mantendo snapshot anterior de produto_tamanho_posicao.",
            len(linhas),
        )
        atual = await db.scalar(select(func.count()).select_from(ProdutoTamanhoPosicao))
        return atual or 0

    await substituir_referencia_tamanhos(db, agrupado.values())

    # D-03/D-04: 1 linha de warning por produto em conflito (nunca 1 por item)
    for cd_prod_cor, avisos in conflitos_por_produto.items():
        logger.warning(
            "produto %s: conflito de posição na referência: %s", cd_prod_cor, avisos
        )

    logger.info(
        "Referência tamanho->posição sincronizada: %d itens (%d linhas brutas, %d descartadas).",
        len(agrupado), len(linhas), descartadas,
    )
    return len(agrupado)
```

**Por que o guard fica no caso de uso, não no repositório:** os 3 repositórios de snapshot existentes (`substituir_pedidos`, `substituir_estoque`, `substituir_pedidos_processados_erp`, `substituir_faturamento_colecao`) são **incondicionais** — fazem `DELETE` seguido de `INSERT` sempre que chamados, sem nenhuma lógica de negócio. A convenção estabelecida (documentada no próprio docstring do arquivo: "quem decide quando commitar é `sincronizar_tudo`") é que o repositório só executa I/O; toda decisão de "devo persistir isto?" mora na camada de orquestração. D-02 é uma decisão de negócio ("um resultado vazio da view é suspeito, não confie nele"), então o `if not agrupado: return` deve interceptar a chamada **antes** de `substituir_referencia_tamanhos` ser invocado — mantendo o repositório simétrico aos 3 irmãos e testável isoladamente. [ASSUMED: inferência de convenção a partir do padrão observado nos 4 repositórios existentes — não há uma regra escrita explícita, mas o desvio de qualquer um dos 4 pares reader/repo existentes contraria o próprio texto do docstring de `repositorio_snapshot.py`.]

### Pattern 2: Parse tolerante em `traducao_databricks.py` (D-01)

**O que é:** Toda conversão de tipo vinda do Databricks (que chega como string ou `None`) passa por uma função `_parse_*` que nunca lança exceção — na pior hipótese devolve um valor "seguro" (0, `None`, string vazia). O descarte de linhas inválidas acontece DEPOIS, na camada de agregação, comparando o valor parseado com uma regra de negócio.

**Exemplo real (`_parse_int`, já existente):**
```python
# Fonte: app/modules/ingestao/domain/traducao_databricks.py (código real)
def _parse_int(v) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v or "").strip()
    return int(float(s.replace(",", "."))) if s else 0
```

**Novo helper recomendado para `nr_posicao` (D-01 — descarta fora de 1..48):**
```python
# app/modules/ingestao/domain/traducao_databricks.py — NOVO
def _parse_posicao(v) -> int | None:
    """nr_posicao válido: inteiro entre 1 e 48. Fora da faixa ou não numérico
    -> None (linha descartada na agregação). Mesmo padrão de tolerância de
    _parse_int, mas aqui o "valor seguro" é None, não 0 — 0 seria uma posição
    real inválida e poderia mascarar o descarte."""
    n = _parse_int(v)
    return n if 1 <= n <= 48 else None
```

Note que `_parse_int(v)` já devolve `0` para entradas não numéricas/vazias — `_parse_posicao` reaproveita essa tolerância e só adiciona a validação de faixa por cima, sem duplicar a lógica de parsing.

### Pattern 3: Agregação com detecção de conflito (D-03) e log por produto (D-04) — padrão NOVO

**O que é:** As 4 funções de agregação existentes (`agregar_itens_pedidos`, `agregar_estoque`, `agregar_itens_processados_erp`, `processar_faturamento_colecao`) só contam descartes (`descartadas`/`ignoradas`) e devolvem esse número para um ÚNICO log de resumo no caso de uso (`logger.info("... %d descartadas ...")`). **Nenhuma delas loga por item nem por produto** — este é o primeiro caso do módulo que precisa disso, então não existe um exemplo interno para copiar 1:1; é preciso compor o padrão a partir de duas peças existentes (contagem global + `logger.warning` já usado em outros módulos, ex. `motor_adequacao.py`).

**Exemplo (síntese recomendada, seguindo a assinatura `tuple[dict, int]` das 4 funções irmãs, estendida com um 3º elemento):**
```python
# app/modules/ingestao/domain/agregacao.py — NOVA função
def agregar_referencia_tamanhos(
    linhas: list[dict],
) -> tuple[dict[tuple, dict], int, dict[str, list[str]]]:
    """Agrega a referência tamanho->posição por (cd_prod_cor, sg_tamanho).
    Descarta linhas com nr_posicao inválido (D-01). Detecta dois tamanhos do
    mesmo produto apontando pra mesma posição: último processado vence, e o
    conflito é acumulado por produto para 1 aviso agregado (D-03/D-04) —
    nunca 1 log por linha."""
    agrupado: dict[tuple, dict] = {}
    posicao_por_produto: dict[str, dict[int, str]] = defaultdict(dict)
    conflitos_por_produto: dict[str, list[str]] = defaultdict(list)
    descartadas = 0

    for linha in linhas:
        cd = str(linha.get("cd_prod_cor") or "").strip()
        tam = str(linha.get("sg_tamanho") or "").strip().upper()
        pos = _parse_posicao(linha.get("nr_posicao"))

        if not cd or not tam or pos is None:
            descartadas += 1
            continue

        posicoes_produto = posicao_por_produto[cd]
        if pos in posicoes_produto and posicoes_produto[pos] != tam:
            conflitos_por_produto[cd].append(f"{posicoes_produto[pos]}/{tam}->pos{pos}")
        posicoes_produto[pos] = tam  # último processado vence (D-03)

        agrupado[(cd, tam)] = {"cd_prod_cor": cd, "sg_tamanho": tam, "nr_posicao": pos}

    return agrupado, descartadas, dict(conflitos_por_produto)
```

**Alerta de implementação:** `agrupado[(cd, tam)] = {...}` sobrescreve por chave `(cd_prod_cor, sg_tamanho)` — isso já é o "último vence" natural do full refresh para o MESMO tamanho repetido (dado sujo diferente de D-03). O conflito de D-03 é sobre **duas chaves diferentes** (`(cd, "M")` e `(cd, "GG")`) apontando para a MESMA `nr_posicao` — daí a necessidade da estrutura auxiliar `posicao_por_produto` (índice reverso por produto) para detectar a colisão, que o dict `agrupado` sozinho não enxerga.

### Pattern 4: Função pura de conversão grade→posições (LINX-03)

**O que é:** Recebe a grade interna de UM produto (`{sg_tamanho: qtd}`) mais a referência JÁ FILTRADA para aquele `cd_prod_cor` (`{sg_tamanho: nr_posicao}` — a Fase 3 monta esse recorte a partir de `produto_tamanho_posicao`), e devolve o dict posicional `{"e1": qtd, ...}` preenchendo com `0` as posições sem quantidade.

**Onde vive:** `app/modules/pedidos/domain/` — módulo consumidor (confirmado por `PROJECT.md`/`01-CONTEXT.md`: "Função de conversão vive no módulo pedidos (domain/), consumidora da referência"). Nome de arquivo sugerido (discricionário): `grade_linx.py` — segue o padrão de nomes descritivos de domínio já usados (`motor_adequacao.py`, `relatorios.py`, `value_objects.py`).

**Quem loga — resolvido pelo texto literal do success criterion 5** ("a conversão emite um aviso no log") e pelo precedente já existente no próprio módulo `pedidos`: `agrupar_por_produto` em `motor_adequacao.py` é uma função classificada como parte de um conjunto "puro" (`# Todas as funções aqui são puras (sem banco/HTTP)`) e MESMO ASSIM loga diretamente via `logger = logging.getLogger(__name__)` de módulo. "Puro" neste código-base significa "sem I/O de banco/rede", não "sem logging" — logging não é tratado como efeito colateral impuro aqui. Recomendação: a função de conversão loga o aviso agregado (1 linha por chamada, já que cada chamada é 1 produto) **e também devolve** a lista de tamanhos ignorados, para permitir asserts diretos no teste sem depender de `caplog` (ver Validation Architecture).

```python
# app/modules/pedidos/domain/grade_linx.py — NOVO
import logging

logger = logging.getLogger(__name__)

_POSICOES = range(1, 49)  # e1..e48


def converter_grade_para_posicoes(
    grade: dict[str, int], referencia: dict[str, int], cd_prod_cor: str
) -> tuple[dict[str, int], list[str]]:
    """Converte a grade interna {sg_tamanho: qtd} de UM produto nas colunas
    posicionais e1..e48, usando `referencia` ({sg_tamanho: nr_posicao}) já
    filtrada para aquele cd_prod_cor.

    Tamanho sem posição na referência -> ignorado, SEM lançar exceção (LINX-03
    critério 5); aviso agregado por produto (D-04), nunca 1 log por tamanho.
    Dois tamanhos da GRADE mapeando pra mesma posição -> último processado
    (ordem de iteração de `grade`) vence (D-03), com aviso de conflito.

    Retorna (posicoes, tamanhos_ignorados) — o 2º elemento existe para permitir
    asserts diretos no teste sem depender de captura de log.
    """
    posicoes = {f"e{n}": 0 for n in _POSICOES}
    ignorados: list[str] = []
    conflitos: list[str] = []
    ocupante_da_posicao: dict[int, str] = {}

    for sg_tamanho, qtd in grade.items():
        pos = referencia.get(sg_tamanho)
        if pos is None:
            ignorados.append(sg_tamanho)
            continue
        if pos in ocupante_da_posicao and ocupante_da_posicao[pos] != sg_tamanho:
            conflitos.append(f"{ocupante_da_posicao[pos]}/{sg_tamanho}->pos{pos}")
        ocupante_da_posicao[pos] = sg_tamanho
        posicoes[f"e{pos}"] = qtd  # último processado vence (D-03)

    if ignorados:
        logger.warning(
            "produto %s: tamanhos sem posição na referência: %s", cd_prod_cor, ignorados
        )
    if conflitos:
        logger.warning("produto %s: conflito de posição na grade: %s", cd_prod_cor, conflitos)

    return posicoes, ignorados
```

**Re-export obrigatório** em `app/modules/pedidos/service.py` (padrão idêntico ao usado para `motor_adequacao`/`relatorios`/`value_objects`) — sem isso, `test_pedidos_motor.py`-style (`from app.modules.pedidos.service import ...`) não consegue importar a função nova, e a Fase 3 também depende desse ponto único de import.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Chamada HTTP/paginação/EXTERNAL_LINKS ao Databricks | Cliente HTTP novo para a 5ª fonte | `executar_consulta` (`app/shared/infrastructure/databricks_client.py`) — já trata INLINE, fallback EXTERNAL_LINKS (>25MiB), polling e erro | Reescrever duplicaria lógica de retry/paginação já testada pelas outras 4 fontes; o reader novo só monta o SELECT (ver `ler_faturamento_colecao` como o exemplo mais recente) |
| Parse de inteiro/string vindo do Databricks | Parser ad-hoc dentro do reader ou da agregação | `_parse_int` (`traducao_databricks.py`), estendido com `_parse_posicao` por cima | Mesma tolerância (bool→0, vírgula decimal, vazio→0) já coberta por testes parametrizados em `test_ingestao_parse.py` |
| Transação/commit da 5ª fonte | `db.commit()` dentro da nova função de sync | Deixar o commit **só** em `sincronizar_tudo` | É o próprio ponto do success criterion 2 — commit único garante atomicidade das 5 fontes |
| Contagem de linhas persistidas na resposta HTTP | Query adicional em `routes.py` | Retorno já numérico de `sincronizar_referencia_tamanhos` (`len(agrupado)`), passado direto ao schema | Mesmo padrão das outras 4 fontes — nenhuma delas faz `SELECT COUNT(*)` extra no `routes.py` |

**Key insight:** Não há nada "deceptivamente complexo" nesta fase que justifique uma biblioteca — o único risco real de reinvenção é reescrever o cliente Databricks ou os parsers tolerantes que já existem e já têm cobertura de teste.

## Common Pitfalls

### Pitfall 1: Campo novo no `SincronizacaoResponse` repetindo o bug existente

**What goes wrong:** `SincronizacaoResponse(BaseModel)` hoje declara `status`, `pedidos_inseridos`, `estoque_chaves`, `processados_erp` — mas **não** `faturamento_colecoes`, embora `sincronizar_tudo` já devolva essa chave desde a 4ª fonte. Como o `model_config` de `SincronizacaoResponse` não define `extra`, o Pydantic v2 usa o default `extra='ignore'`: qualquer kwarg não declarado passado no construtor é **silenciosamente descartado**, sem erro, sem warning.

**Confirmado por execução direta neste ambiente** (não é suposição):
```
$ docker compose -f .docker/docker-compose.yml exec -T api uv run python -c "
from app.modules.ingestao.schemas import SincronizacaoResponse
r = SincronizacaoResponse(status='success', pedidos_inseridos=1, estoque_chaves=2, processados_erp=3, faturamento_colecoes=999)
print(r.model_dump())
"
# -> {'status': 'success', 'pedidos_inseridos': 1, 'estoque_chaves': 2, 'processados_erp': 3}
# faturamento_colecoes desaparece.
```
[VERIFIED: execução direta no container `api`, 2026-08-05]

**Why it happens:** Ninguém adicionou o campo ao schema quando a 4ª fonte foi criada; o `routes.py` faz `SincronizacaoResponse(status="success", **resultado)` — passar um dict maior que o schema não é um erro em tempo de execução, é só descartado.

**How to avoid:** O plano precisa adicionar o campo novo (`referencia_tamanho_posicao: int = 0` ou nome equivalente) ao `SincronizacaoResponse` **e** decidir explicitamente se corrige o bug pré-existente (adicionar `faturamento_colecoes: int = 0` de carona, já que o arquivo está sendo tocado mesmo). Recomendação: corrigir de carona — é uma linha, o arquivo já vai ser editado nesta fase, e não corrigir perpetua exatamente o padrão de erro que o `01-CONTEXT.md` pediu para não repetir. Se a usuária preferir manter o escopo estritamente fechado, documentar a decisão explicitamente (ver Open Questions).

**Warning signs:** Um teste de contrato (`assert "referencia_tamanho_posicao" in resp.json()`) pega isso imediatamente — vale adicionar esse assert explícito, já que o Pydantic não vai levantar erro nenhum.

### Pitfall 2: Confundir "conflito de posição" (D-03) com "tamanho duplicado" (já tratado)

**What goes wrong:** A chave única de `produto_tamanho_posicao` é `(cd_prod_cor, sg_tamanho)` — full refresh já resolve "o mesmo tamanho apareceu duas vezes" (o dict `agrupado` sobrescreve por essa chave, último processado vence, sem necessidade de lógica extra). D-03 é um problema DIFERENTE: **dois tamanhos distintos** (`M` e `GG`, por exemplo) apontando para a **mesma** `nr_posicao` — isso não vira erro de unicidade no banco (a `UniqueConstraint` é sobre `cd_prod_cor+sg_tamanho`, não sobre `cd_prod_cor+nr_posicao` — ver WR-02/IN-02 do `01-REVIEW.md`, que já documentou esse gap deliberadamente sem CheckConstraint).

**Why it happens:** É fácil implementar só a dedup óbvia (por `sg_tamanho`) e esquecer o índice reverso por posição, porque o dict de agregação sozinho não expõe a colisão.

**How to avoid:** Manter a estrutura auxiliar `posicao_por_produto: dict[cd_prod_cor, dict[nr_posicao, sg_tamanho]]` durante a agregação (ver Pattern 3) — é ela que detecta a colisão antes de sobrescrever.

**Warning signs:** Um teste com 2 linhas `(cd="X", tam="M", pos=5)` e `(cd="X", tam="GG", pos=5)` deve produzir 1 warning de conflito E ambas as linhas persistidas em `produto_tamanho_posicao` (a tabela aceita as duas — o conflito só importa na hora de USAR a referência para converter uma grade real).

### Pitfall 3: D-02 implementado dentro do repositório em vez do caso de uso

**What goes wrong:** Se o guard "0 linhas → não substituir" for colocado dentro de `substituir_referencia_tamanhos` (repositório), a função perde a simetria com os 3 repositórios irmãos (todos incondicionais) e passa a misturar regra de negócio com I/O — dificultando testá-la isoladamente e quebrando o princípio já documentado no docstring do arquivo ("quem decide quando commitar é `sincronizar_tudo`" — a mesma lógica de "quem decide" se estende a "quem decide SE persiste").

**How to avoid:** Guard vive em `sincronizar_referencia_tamanhos` (caso de uso), ANTES de chamar `substituir_referencia_tamanhos` — ver Pattern 1.

**Warning signs:** Se o teste do guard (D-02) precisar mockar `db.execute(delete(...))` para verificar que ele NÃO foi chamado, é sinal de que o guard está na camada errada — o teste correto verifica que `substituir_referencia_tamanhos` (a função inteira) não foi invocada.

### Pitfall 4: Log agregado por produto em pleno full refresh de milhares de linhas

**What goes wrong:** Os 4 padrões de agregação existentes só emitem 1 log de resumo (contador total). D-04 exige "1 linha por produto" para conflitos e tamanhos ignorados — se implementado descuidadamente (logar dentro do loop principal, por linha), um sync de 2h com um dado sujo recorrente pode gerar centenas de linhas de log, exatamente o que D-04 foi desenhado para evitar ("nunca 1 linha por item — inundaria o log no sync de 2h").

**How to avoid:** Acumular em um dict `{cd_prod_cor: [avisos]}` durante o loop de agregação e só iterar/logar esse dict DEPOIS que o loop principal terminar — nunca `logger.warning` dentro do loop de linhas brutas.

### Pitfall 5: `caplog` é um padrão de teste novo neste repositório

**What goes wrong:** Nenhum dos 11 arquivos de teste atuais usa `caplog` (confirmado via busca — nenhum resultado). Se o plano decidir testar os avisos SOMENTE via captura de log, é um padrão sem precedente que pode surpreender quem revisar o PR.

**How to avoid:** Como a função pura de conversão (Pattern 4) e a agregação de ingestão (Pattern 3) retornam explicitamente `ignorados`/`conflitos_por_produto`, prefira asserts sobre o VALOR DE RETORNO — mais alinhado ao estilo existente (`test_pedidos_motor.py`, `test_ingestao_parse.py`). Reservar `caplog` só se for indispensável confirmar a MENSAGEM exata do log (ex.: formato "produto X: tamanhos sem posição: [...]").

## Code Examples

### Extensão de `sincronizar_tudo` (5ª etapa, commit único)

```python
# app/modules/ingestao/application/casos_uso.py
async def sincronizar_tudo(db: AsyncSession) -> dict[str, int]:
    """Sincroniza pedidos, estoque, processados-ERP, faturamento e a
    referência tamanho->posição numa única transação (full refresh)."""
    pedidos = await sincronizar_pedidos(db)
    estoque = await sincronizar_estoque(db)
    processados_erp = await sincronizar_pedidos_processados_erp(db)
    faturamento = await sincronizar_faturamento_colecao(db)
    referencia_tamanho_posicao = await sincronizar_referencia_tamanhos(db)  # NOVO
    await db.commit()
    return {
        "pedidos_inseridos": pedidos,
        "estoque_chaves": estoque,
        "processados_erp": processados_erp,
        "faturamento_colecoes": faturamento,
        "referencia_tamanho_posicao": referencia_tamanho_posicao,  # NOVO
    }
```

### Extensão de `settings.py` + `.env.example` (nome já definido em `01-CONTEXT.md`)

```python
# app/shared/config/settings.py — dentro da classe Settings
databricks_tabela_tamanho_ref: str = Field(
    default="", validation_alias="DATABRICKS_TABELA_TAMANHO_REF"
)
```
```bash
# .env.example
# Referência tamanho->posição da grade Linx (view programa_estagio.refined.system_automation_prod_tamanho_ref)
DATABRICKS_TABELA_TAMANHO_REF=catalogo.schema.tamanho_ref
```

### Reader novo (molde: `ler_faturamento_colecao`, simplificado — sem filtro de data)

```python
# app/modules/ingestao/infrastructure/databricks_reader.py
async def ler_referencia_tamanhos() -> list[dict]:
    settings = get_settings()
    tabela = settings.databricks_tabela_tamanho_ref
    if not tabela:
        raise DatabricksError("DATABRICKS_TABELA_TAMANHO_REF não configurada no .env.")

    statement = f"SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM {tabela}"
    return await executar_consulta(statement)
```

### Repositório novo (molde exato de `substituir_faturamento_colecao` — sem guard, ver Pitfall 3)

```python
# app/modules/ingestao/infrastructure/repositorio_snapshot.py
async def substituir_referencia_tamanhos(db: AsyncSession, itens: Iterable[dict]) -> None:
    """Substitui a referência tamanho->posição (D-02: quem decide SE chama esta
    função é o caso de uso — aqui a substituição é sempre incondicional)."""
    await db.execute(delete(ProdutoTamanhoPosicao))
    db.add_all(ProdutoTamanhoPosicao(**item) for item in itens)
```

### Teste do guard D-02 (esqueleto — sessão real nunca commitada, mesmo padrão de `test_ingestao_sync.py`)

```python
# app/tests/test_ingestao_sync.py — NOVOS casos
async def test_sincronizar_referencia_view_vazia_mantem_snapshot_anterior():
    async with async_session_factory() as session:
        # Simula um snapshot pré-existente (flush, sem commit).
        session.add(ProdutoTamanhoPosicao(cd_prod_cor="AAA1|001", sg_tamanho="M", nr_posicao=5))
        await session.flush()

        with _mock_consulta([]):  # view devolve 0 linhas
            total = await service.sincronizar_referencia_tamanhos(session)

        # A linha pré-existente continua lá — substituir_referencia_tamanhos
        # nunca foi chamada.
        row = await session.scalar(
            select(ProdutoTamanhoPosicao).where(ProdutoTamanhoPosicao.cd_prod_cor == "AAA1|001")
        )
        assert row is not None
        assert total == 1  # contagem atual, não 0

        await session.rollback()
```

### Teste da função pura de conversão (fixture com dados reais — ver Validation Architecture)

```python
# app/tests/test_grade_linx.py — NOVO
from app.modules.pedidos.service import converter_grade_para_posicoes

# Amostra real capturada de produto_tamanho_posicao após a 1ª ingestão real
# (ver task de checkpoint em Open Questions) — NÃO inventar valores.
_REFERENCIA_REAL_PRODUTO_X = {"P": 3, "M": 5, "G": 7, "GG": 9}  # placeholder — substituir


def test_converter_grade_para_posicoes_com_dados_reais():
    grade = {"P": 2, "M": 10, "GG": 1}
    posicoes, ignorados = converter_grade_para_posicoes(
        grade, _REFERENCIA_REAL_PRODUTO_X, cd_prod_cor="PRODUTO_X|001"
    )
    assert posicoes["e3"] == 2
    assert posicoes["e5"] == 10
    assert posicoes["e9"] == 1
    assert posicoes["e1"] == 0  # posição sem quantidade
    assert ignorados == []


def test_converter_grade_para_posicoes_tamanho_sem_posicao_nao_lanca_excecao():
    grade = {"M": 10, "XG": 3}  # "XG" não existe na referência do produto
    posicoes, ignorados = converter_grade_para_posicoes(
        grade, _REFERENCIA_REAL_PRODUTO_X, cd_prod_cor="PRODUTO_X|001"
    )
    assert posicoes["e5"] == 10
    assert ignorados == ["XG"]
```

## State of the Art

Não aplicável no sentido "ecossistema externo evoluiu" — esta é uma extensão de um padrão interno brownfield, não uma escolha de tecnologia de mercado. O único "antes/depois" relevante é interno ao próprio código:

| Antes (padrão das 4 fontes) | Depois (5ª fonte, esta fase) | Motivo da mudança |
|---|---|---|
| Full refresh sempre incondicional (0 linhas = tabela fica vazia) | Full refresh condicional — 0 linhas válidas = mantém snapshot anterior (D-02) | Referência é dado de cadastro; um vazio por erro na origem zeraria as grades E1..E48 da Fase 3 |
| Log de resumo único (1 contador global) | Log de resumo + avisos agregados por produto (D-04) | Conflitos/descartes precisam ser rastreáveis por produto para correção na origem, sem inundar o log de 2h |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | O guard D-02 deve ficar na camada de caso de uso (`casos_uso.py`), não no repositório — inferido da convenção observada nos 4 pares reader/repo existentes, não de uma regra escrita explícita | Architecture Patterns (Pattern 1), Pitfall 3 | Se a usuária preferir o guard no repositório por algum motivo de encapsulamento, o plano precisaria ser ajustado — risco baixo, é uma reorganização de poucas linhas, não uma mudança de contrato |
| A2 | Nome do env var `DATABRICKS_TABELA_TAMANHO_REF` — copiado literalmente do `02-CONTEXT.md` (`code_context`), não inventado nesta pesquisa | Code Examples | Nenhum — já é uma decisão registrada pela usuária |
| A3 | Corrigir "de carona" o bug de `faturamento_colecoes` ausente em `SincronizacaoResponse` ao adicionar o campo da 5ª fonte — recomendação desta pesquisa, não decisão travada | Pitfall 1, Open Questions | Se a usuária preferir escopo estritamente fechado, o plano deve conter só o campo novo e registrar o bug pré-existente como dívida técnica separada (mesmo padrão do WR-01 do `01-REVIEW.md`) |
| A4 | Nome de arquivo `grade_linx.py` e função `converter_grade_para_posicoes` para a conversão pura — sugestão, explicitamente marcada como discricionária no `02-CONTEXT.md` | Architecture Patterns (Pattern 4) | Nenhum risco funcional — é só nomenclatura; o planner/usuária pode escolher outro nome sem impacto |
| A5 | Retornar a contagem ATUAL da tabela (`SELECT COUNT(*)`) quando o guard D-02 dispara, em vez de retornar `0` — decisão de design desta pesquisa, não explicitada no `02-CONTEXT.md` | Code Examples (Pattern 1) | Baixo — afeta só o valor do contador `referencia_tamanho_posicao` na resposta HTTP quando o guard dispara; não afeta o dado persistido nem os critérios de sucesso da fase |

**Se esta tabela estivesse vazia:** não está — as 5 entradas acima usam conhecimento de convenção observada (não 100% explícita no CONTEXT.md), por isso ficam registradas para confirmação do planner/usuária, mas nenhuma delas é um risco de dado incorreto ou retrabalho estrutural.

## Open Questions (RESOLVED)

> Todas as 3 perguntas foram resolvidas no planejamento — ver `02-03-PLAN.md` (decisões travadas, itens 3 e 4) e a estratégia checkpoint 02-04 → fixture 02-05. Q1: sim, corrigir de carona. Q2: contador = contagem atual quando o guard D-02 dispara. Q3: fixture capturada no checkpoint humano do Plano 02-04, consumida pelo Plano 02-05.

1. **Corrigir "de carona" o bug de `faturamento_colecoes` ausente em `SincronizacaoResponse`?** — **RESOLVIDA: sim.**
   - What we know: confirmado por execução direta que o campo é descartado silenciosamente hoje; o arquivo `schemas.py` já será editado nesta fase para adicionar o campo da 5ª fonte.
   - What's unclear: se a usuária quer manter o escopo desta fase estritamente fechado ao que está no `02-CONTEXT.md` (que não menciona corrigir o bug, só "não repetir o erro" para o campo novo).
   - Recommendation: corrigir de carona (1 linha, risco zero, mesmo arquivo) — mas apresentar como decisão explícita ao planejar, não decidir silenciosamente.

2. **Qual o valor de retorno de `sincronizar_referencia_tamanhos` quando o guard D-02 dispara?**
   - What we know: a tabela mantém o snapshot anterior intacto.
   - What's unclear: se o contador na resposta HTTP deve refletir a contagem atual (`SELECT COUNT(*)`, recomendação desta pesquisa) ou `0` (sinalizando "sync desta fonte foi pulado").
   - Recommendation: contagem atual — mais consistente com o significado dos outros 4 contadores ("quantos itens existem agora"), mas é uma escolha de UX de log/resposta, não um critério de sucesso trancado.

3. **Como capturar a amostra real de dados para o teste puro da função de conversão (LINX-03 critério 4)?**
   - What we know: o teste precisa de dados REAIS já ingeridos, não inventados — e `produto_tamanho_posicao` está vazia agora (confirmado: `SELECT COUNT(*)` = 0 no dev_db).
   - What's unclear: a captura só é possível DEPOIS que a 5ª fonte estiver implementada E uma sincronização real contra o Databricks tiver rodado (requer `DATABRICKS_TOKEN`/credenciais válidas no `.env`, que este agente de pesquisa não tem acesso nem pode disparar).
   - Recommendation: o plano deve conter uma task explícita com `checkpoint:human-verify` após a implementação da 5ª fonte: (1) rodar `POST /api/v1/ingestao/sincronizar` manualmente (ou aguardar o beat), (2) capturar via `psql` 2-3 produtos reais de `produto_tamanho_posicao` (`SELECT cd_prod_cor, sg_tamanho, nr_posicao FROM produto_tamanho_posicao WHERE cd_prod_cor = '...'`), (3) colar esses valores como dict fixture hardcoded no teste (mesmo estilo de dicts inline já usado em `test_ingestao_sync.py`), (4) só então escrever os testes puros da função de conversão contra essa amostra real.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker Compose stack (`api`, `dev_db`, `redis`, `celery_*`) | Todas as verificações desta fase | ✓ | Stack já em execução (`system_automation_api` healthy, 21min uptime no momento da pesquisa) | — |
| Postgres (`dev_db`) | Persistência de `produto_tamanho_posicao` | ✓ | postgres:16-alpine | — |
| `uv run` local (host Windows) | Execução de testes/scripts fora do container | ✗ (falha no build de `pyicu`) | — | `docker compose -f .docker/docker-compose.yml exec -T api uv run ...` — já confirmado funcional nesta pesquisa |
| Conectividade real com Databricks (token válido) | Sync real da 5ª fonte para capturar a fixture de dados reais (Open Question 3) | Não verificável por este agente | — | Requer ação manual da usuária/mantenedora — task com `checkpoint:human-verify` no plano |
| MCP `backstage_get_coding_standards` | Padrões de código | ✗ (mesma limitação já documentada na Fase 1) | — | Fallback já usado: padrões dos arquivos análogos do próprio repositório |

**Missing dependencies with no fallback:**
- Conectividade real com Databricks para gerar a fixture de dados reais do teste puro (LINX-03 critério 4) — não pode ser simulada; precisa de um sync real após a implementação.

**Missing dependencies with fallback:**
- `uv run` local → contornado via `docker compose exec` (mesmo padrão já usado e documentado na Fase 1).
- MCP Backstage → contornado via padrões do repositório (mesmo padrão já usado e documentado na Fase 1).

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 8.3.0+ com pytest-asyncio 0.24.0+ (`asyncio_mode = "auto"`) |
| Config file | `pyproject.toml` (`[tool.pytest.ini_options]`, `testpaths = ["app/tests"]`) |
| Quick run command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q app/tests/test_ingestao_sync.py app/tests/test_ingestao_parse.py` (confirmado: 83 passed em ~7s) |
| Full suite command | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` (confirmado: 215 passed, 1 warning pré-existente e não relacionado, em ~5min22s) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ING-01 (critério 1) | 5ª fonte grava `produto_tamanho_posicao` a partir da view | integration (sessão real, sem commit) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q app/tests/test_ingestao_sync.py -k referencia` | ❌ Wave 0 (casos novos a criar em `test_ingestao_sync.py`) |
| ING-01 (critério 2) | `sincronizar_tudo` chama a 5ª fonte no commit único | unit (orquestração mockada) | mesmo arquivo, `-k sincronizar_tudo` | ❌ Wave 0 (estender teste existente de 4 para 5 mocks) |
| ING-01 (critério 3) | Full refresh substitui sem duplicar em 2 syncs sucessivas | integration | teste dedicado com 2 chamadas sequenciais + `SELECT COUNT(*)` | ❌ Wave 0 |
| ING-01 (D-02) | View vazia não substitui, mantém snapshot | integration | ver Code Examples (`test_sincronizar_referencia_view_vazia_mantem_snapshot_anterior`) | ❌ Wave 0 |
| LINX-03 (critério 4) | Função pura converte grade+posição sem banco, com dados reais | unit (sem I/O) | `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q app/tests/test_grade_linx.py` | ❌ Wave 0 — **bloqueado por Open Question 3** (precisa de dados reais capturados após 1ª ingestão) |
| LINX-03 (critério 5) | Tamanho sem posição gera aviso, não lança exceção | unit | mesmo arquivo, `-k sem_posicao` | ❌ Wave 0 |

### Sampling Rate

- **Por commit de task:** `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q app/tests/test_ingestao_sync.py app/tests/test_ingestao_parse.py app/tests/test_grade_linx.py`
- **Por merge de wave:** `docker compose -f .docker/docker-compose.yml exec -T api uv run pytest -q` (suíte completa — baseline atual confirmado: 215 passed)
- **Gate de fase:** suíte completa verde antes de `/gsd-verify-work`, **mais** verificação manual via API real (ver abaixo) — os critérios de sucesso 1-3 envolvem full refresh contra Postgres real, então o teste automatizado prova a LÓGICA, mas a verificação funcional completa pede uma chamada real:

```bash
# Verificação manual do critério 1-3 (após implementação, com stack rodando):
# 1. Disparar sync manual (endpoint já exige role admin — usar token válido)
curl -X POST http://localhost:8000/api/v1/ingestao/sincronizar -H "Authorization: Bearer <token>"

# 2. Confirmar contagem e ausência de duplicação
docker compose -f .docker/docker-compose.yml exec -T dev_db psql -U automation -d system_automation \
  -c "SELECT COUNT(*) FROM produto_tamanho_posicao;"

# 3. Rodar o sync uma 2a vez e confirmar que a contagem não dobrou (critério 3)
curl -X POST http://localhost:8000/api/v1/ingestao/sincronizar -H "Authorization: Bearer <token>"
docker compose -f .docker/docker-compose.yml exec -T dev_db psql -U automation -d system_automation \
  -c "SELECT COUNT(*) FROM produto_tamanho_posicao;"
```

### Wave 0 Gaps

- [ ] Casos novos em `app/tests/test_ingestao_sync.py` — cobre ING-01 (todos os critérios) e D-01/D-02/D-03/D-04 no lado da ingestão
- [ ] `app/tests/test_grade_linx.py` (arquivo novo) — cobre LINX-03; **depende de Open Question 3 resolvida** (fixture de dados reais só pode ser escrita depois de uma sincronização real)
- [ ] Estender `app/tests/test_ingestao_parse.py` com casos para `_parse_posicao` (D-01) — padrão idêntico aos parametrize já existentes para `_parse_int`
- [ ] Framework: nenhum a instalar — pytest/pytest-asyncio já configurados

## Security Domain

`security_enforcement` não está desabilitado no `.planning/config.json` (chave ausente = habilitado por padrão), então esta seção é obrigatória, ainda que o escopo desta fase seja quase inteiramente batch/interno (sem superfície HTTP nova).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | Não (endpoint já existente, sem mudança) | `/api/v1/ingestao/sincronizar` já protegido por `Depends(require_admin)` — nenhuma rota nova nesta fase |
| V3 Session Management | Não | Sem sessão nova |
| V4 Access Control | Não (herda o `require_admin` já existente) | RBAC 5 níveis já aplicado no router `ingestao` |
| V5 Input Validation | Sim | Validação tolerante de `nr_posicao` na camada de tradução (D-01) — mesmo padrão de `_parse_int`/`_parse_float`, nunca confia no tipo bruto vindo do Databricks |
| V6 Cryptography | Não | Nenhum dado sensível novo (token Databricks já gerenciado via `.env` gitignored, sem mudança) |

### Known Threat Patterns for {stack}

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Injeção via nome de tabela interpolado no SQL (`f"... FROM {tabela}"`) | Tampering | **Risco pré-existente, não introduzido nesta fase** — o valor vem de `settings.databricks_tabela_tamanho_ref` (variável de ambiente controlada pela própria equipe, não input de usuário HTTP). Mesmo padrão de risco aceito nas 4 fontes existentes (`ler_pedidos_em_aberto`, `ler_estoque`, etc.) — nenhuma usa parametrização porque o "parâmetro" é o nome da tabela, não um valor de linha. [ASSUMED: nenhuma mitigação nova recomendada por esta pesquisa além de manter a variável fora de input HTTP, como já é o caso] |
| Dado sujo da origem corrompendo `e1..e48` na Fase 3 (conflito de posição) | Tampering | D-03 (último vence + log) + D-04 (log agregado) — mitigação de negócio, não de segurança de rede; documentada nesta pesquisa como padrão novo (Pattern 3) |
| Full refresh vazio zerando a referência (D-02) | Denial of Service (interno) | Guard "0 linhas válidas → não substituir" (Pattern 1) — mitigação já desenhada nesta pesquisa |

## Sources

### Primary (HIGH confidence — leitura direta do código-fonte real do repositório)
- `app/modules/ingestao/infrastructure/databricks_reader.py` — 4 funções de leitura existentes, molde da 5ª
- `app/modules/ingestao/domain/traducao_databricks.py` — parsers tolerantes existentes
- `app/modules/ingestao/domain/agregacao.py` — 4 funções de agregação existentes
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` — 4 funções de substituição existentes
- `app/modules/ingestao/application/casos_uso.py` — `sincronizar_tudo` e as 4 sub-funções
- `app/modules/ingestao/schemas.py`, `app/modules/ingestao/routes.py` — schema/rota atuais
- `app/modules/ingestao/models.py` — `ProdutoTamanhoPosicao` (criado na Fase 1)
- `app/modules/pedidos/models.py` — `OrdemReservaLinx` (criado na Fase 1, colunas `e1..e48`)
- `app/modules/pedidos/domain/motor_adequacao.py`, `value_objects.py` — precedente de "função pura que loga"
- `app/modules/pedidos/service.py`, `app/modules/pedidos/application/casos_uso.py` — padrão de re-export e orquestração
- `app/shared/infrastructure/databricks_client.py` — `executar_consulta` (cliente HTTP reusável)
- `app/shared/config/settings.py`, `.env.example` — padrão de env vars por fonte
- `app/tests/test_ingestao_sync.py`, `test_ingestao_parse.py`, `test_pedidos_motor.py`, `test_ingestao_api_leitura.py` — moldes de teste
- Execução direta no container Docker (`docker compose exec api uv run python -c ...` e `uv run pytest`) — confirmação do bug do `SincronizacaoResponse` e baseline real da suíte (215 passed)
- `psql` no container `dev_db` — confirmação de que `produto_tamanho_posicao`/`ordens_reserva_linx` estão vazias hoje e do schema real da tabela

### Secondary (MEDIUM confidence)
- `.planning/phases/01-schema-linx-e-refer-ncia-de-posi-o/01-REVIEW.md` — WR-01/WR-02/IN-02, base da análise de conflito de posição (D-03)
- `.planning/codebase/CONVENTIONS.md`, `TESTING.md` — convenções gerais do backend (mapa gerado em 2026-08-04, cruzado com leitura direta do código atual)

### Tertiary (LOW confidence)
- Nenhuma — toda a pesquisa desta fase foi possível diretamente no código-fonte real e no ambiente ao vivo, sem necessidade de WebSearch/Context7 (não há biblioteca externa nova envolvida).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — nenhuma dependência nova; todo o reuso foi confirmado lendo os arquivos reais
- Architecture: HIGH — padrão replicado 4 vezes no mesmo módulo, lido linha a linha; os 2 pontos genuinamente novos (guard D-02, log agregado D-03/D-04) foram raciocinados a partir de convenções observadas e sinalizados como `[ASSUMED]` no Assumptions Log
- Pitfalls: HIGH — o pitfall mais crítico (bug do `SincronizacaoResponse`) foi confirmado por execução direta, não suposição

**Research date:** 2026-08-05
**Valid until:** Sem prazo de validade curto — é código interno estável, não uma biblioteca externa com ciclo de release. Revalidar apenas se o schema da view Databricks (`system_automation_prod_tamanho_ref`) mudar ou se a Fase 3 revelar um requisito não previsto para a função de conversão.
