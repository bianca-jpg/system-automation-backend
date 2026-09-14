# Phase 14: Motor de alocação puro (regras de negócio) - Pattern Map

**Mapped:** 2026-08-14
**Files analyzed:** 7 (2 modified diretamente, 1 modificado como dependência, ~4 novos módulos de domínio + suíte de testes)
**Analogs found:** 7 / 7

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `app/modules/pedidos/domain/motor_adequacao.py` (reescrita profunda) | domain orchestrator + rule engine | transform (in-memory, sem I/O) | ele mesmo (474 linhas atuais) | exact — é o próprio arquivo, mas a forma interna muda |
| `app/modules/pedidos/domain/orcamento_pedido.py` (novo, nome sugerido) | value object / ledger mutável | transform, estado local por iteração | `app/shared/jobs/domain.py::NewJob`/`DurableJob` (dataclass com `__post_init__` de validação) | role-match — não é `frozen` como os de jobs, mas usa o mesmo esqueleto de dataclass+`__post_init__` |
| `app/modules/pedidos/domain/furo_de_grade.py` (novo, ou dentro de `motor_adequacao.py`) | predicado puro de regra de negócio | transform (bool + motivo) | `app/modules/pedidos/domain/estoque_virtual.py::calcular_estoque_virtual` | role-match — função pura, doc extenso da regra, iteração sobre itens/estoque |
| `app/modules/pedidos/domain/politica_quantidade.py` (novo, ou inline) | strategy/protocol por variante | transform, despacho por modo | `app/shared/jobs/application/ports.py::DurableJobRepository` (Protocol) + `app/modules/pedidos/processing/domain.py::ProcessingMode` (StrEnum) | role-match — Protocol já é idioma do repo; StrEnum já é idioma para "modo" |
| `app/modules/pedidos/domain/rateio.py` (novo, promovendo `_allocate`) | utilitário compartilhado (extraído) | transform, aritmética Decimal | `app/modules/pedidos/domain/edicao_grade.py::_allocate` (linhas 17-40) | exact — é literalmente o código a mover, só remover o `_` e generalizar import |
| `app/tests/test_pedidos_motor.py` (parcialmente reescrito) | test (domínio puro) | request-response síncrono (chamada direta de função) | ele mesmo (20 testes atuais) | exact |
| `app/tests/test_pedidos_motor_orcamento.py` / `test_pedidos_motor_furo_grade.py` (novos, nomes sugeridos) | test (domínio puro, cenários novos) | idem | `app/tests/test_pedidos_motor.py` (factory `_item`, sem classes, funções soltas `test_*`) | exact |

## Pattern Assignments

### `app/modules/pedidos/domain/motor_adequacao.py` (reescrita)

**Analog:** ele mesmo — o arquivo já é o "core" do domínio; a fase reescreve por dentro sem mudar a forma pública.

**Imports pattern** (linhas 1-19, mantido):
```python
"""Motor de adequação: casamento pedido x estoque, isolado por canal.

Orquestração sem I/O — `processar_pedidos` -> `_processar_pedidos_canal` ->
`adequar_grade_produto`. Todas as funções aqui são puras (sem banco/HTTP).
"""

import logging
import math
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Mapping
from threading import Event

from app.modules.pedidos.domain.value_objects import (
    CANAIS,
    get_tamanho_idx,
    montar_chave_estoque,
    normalizar_canal,
)
```
Replicar: nenhum import de infraestrutura, só `domain.value_objects` e stdlib. Ao criar o ledger/política/furo em módulos irmãos, importar deles aqui do mesmo jeito (`from app.modules.pedidos.domain.orcamento_pedido import OrcamentoPedido`).

**Predicado puro com motivo — molde a replicar para `verificar_furo_grade`** (`is_sem_credito`, linhas 46-57):
```python
def is_sem_credito(status: str | None) -> bool:
    """True se o status de crédito indicar ausência de crédito.

    Robusto a acento/caixa: 'Sem Crédito', 'SEM CREDITO', 'sem credito' etc.
    Ausente ou 'Com Crédito' -> False (crédito OK).
    """
    if not status:
        return False
    norm = (
        unicodedata.normalize("NFKD", status).encode("ascii", "ignore").decode().lower()
    )
    return any(marker in norm for marker in ("sem cred", "bloque", "reprov"))
```
Adaptar: `verificar_furo_grade` precisa devolver `(bool, motivo)` em vez de só `bool` — usar o padrão de retorno de `_processar_pedidos_canal`/`marcar_stand_by`, que já carregam `motivo` como string (ver abaixo), não inventar um novo formato (ex. exception, Enum de motivo). Um `NamedTuple` ou `tuple[bool, str]` simples é suficiente; não há precedente de `Result`/`Either` no repo — não introduzir esse padrão.

**Cadeia de decisão por par com early-exit documentado** (`_processar_pedidos_canal`, linhas 402-453) — molde para a nova sequência `crédito → furo de grade → política de quantidade → guarda de zero → commit`:
```python
for cd_prod_cor, pedidos_do_produto in estrutura.items():
    _ensure_not_cancelled(cancel_token)
    estoque_local = dict(estoque_por_produto[cd_prod_cor])
    for nr in sorted(
        pedidos_do_produto.keys(), key=lambda n: ordem_idx.get(n, len(ordem_idx))
    ):
        _ensure_not_cancelled(cancel_token)
        par = (nr, cd_prod_cor)
        if nr in sem_credito_nrs:
            if not resultados_apenas_selecionados:
                resultados[par].extend(
                    marcar_stand_by(
                        pedidos_do_produto[nr],
                        motivo="Aguardando liberação de crédito",
                        cancel_token=cancel_token,
                    )
                )
            continue
        # ... resto da cadeia
```
Replicar exatamente o formato: cada guarda de saída antecipada (`continue`) chama `marcar_stand_by(..., motivo="...")` e NUNCA toca `estoque_local`/ledger antes do `continue`. O furo de grade entra como uma nova guarda igual a essa, entre crédito e a política de quantidade — mesmo padrão de `if <predicado>: marcar_stand_by(..., motivo=<motivo específico>); continue`. Note o comentário `estoque_local = dict(estoque_por_produto[cd_prod_cor])` — é uma CÓPIA por produto, não referência; a pesquisa (Risco 2) exige preservar esse padrão de cópia ao inverter os loops.

**Decremento de estoque só após confirmação (padrão de "commit" a replicar)** (linhas 437-443):
```python
for item in itens_processados:
    _ensure_not_cancelled(cancel_token)
    if item.get("status_item") == "Gerar OR":
        chave = montar_chave_estoque(cd_prod_cor, item["sg_tamanho"])
        estoque_local[chave] = max(
            0, estoque_local.get(chave, 0) - item["qt_liquida"]
        )
```
Replicar: decremento condicionado a `status_item == "Gerar OR"`, usando `montar_chave_estoque` (já existente, não recriar). O consumo do ledger de orçamento deve seguir o mesmo timing — só debitar depois que a política de quantidade decidiu, nunca antes/especulativamente.

**Marcação de stand-by com motivo parametrizado** (`marcar_stand_by`, linhas 180-197):
```python
def marcar_stand_by(
    itens: list,
    motivo: str = "Preterido: estoque insuficiente para todos os pedidos do produto",
    *,
    cancel_token: CancellationToken | None = None,
) -> list:
    resultado = []
    for item in itens:
        _ensure_not_cancelled(cancel_token)
        copia = dict(item)
        copia["status_item"] = "Pedido em Stand By"
        copia["diff_valor"] = 0.0
        copia["motivo_stand_by"] = motivo
        copia.pop("is_ext", None)
        copia.pop("comentario", None)
        copia.pop("aceita_adequacao", None)
        resultado.append(copia)
    return resultado
```
Replicar sem mudar a assinatura — só adicionar novos `motivo=` nas chamadas para furo de grade (`"Furo de grade: tamanho {X} sem estoque reservável"`) e tudo-ou-nada (`"Sem adequação: grade completa indisponível"`).

**`adequar_grade_produto` — o que muda de assinatura** (linhas 119-177): hoje calcula `limite_falta`/`orcamento_aumento` internamente com `math.ceil`/`math.floor` sobre `total_grade` local. Passa a **receber** o restante do ledger como parâmetro (não recalcular localmente) e devolver quanto consumiu. Preservar a mecânica de `is_ext`/preservação de `qt_solicitada` (linhas 151, 171) como está — só desacoplar de onde vem o orçamento.

**Aritmética inteira — não usar `math.ceil`/`float` para o ledger novo:** o código atual usa `math.ceil(total_grade * tolerancia)` (linha 135) e `math.floor(...)` (linha 136) com `float`. A regra nova exige `floor` nos dois lados E aritmética inteira (`(total * 5) // 100`), não `float`. Não copiar o padrão `float` daqui para o ledger — copiar o padrão de `Decimal`/inteiro de `edicao_grade.py` em vez disso.

---

### `app/modules/pedidos/domain/orcamento_pedido.py` (novo — ledger de orçamento)

**Analog primário:** `app/shared/jobs/domain.py` (dataclasses de domínio do repo)

**Padrão de dataclass com validação em `__post_init__`** (linhas 127-161):
```python
@dataclass(frozen=True, slots=True)
class NewJob:
    kind: str
    fingerprint: str
    scope_key: str | None
    owner_id: int | None = None
    idempotency_digest: str | None = None
    max_attempts: int = 3
    requested_at: datetime = field(default_factory=utc_now)
    deadline_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        if not _SAFE_KIND.fullmatch(self.kind):
            raise InvalidJobData("invalid_job_kind")
        ...
```
Padrão do repo para VOs imutáveis: `@dataclass(frozen=True, slots=True)`, validação isolada num método `_validate()` chamado de `__post_init__`, exceção de domínio específica (`InvalidJobData`, um `DurableJobError`) em vez de `ValueError` cru — mas note que aqui o ledger PRECISA de mutação incremental (`consumir_adicao`/`consumir_corte`), então **não pode ser `frozen=True`** — é a exceção documentada no `RESEARCH.md` ("Ledger como dataclass imutável por leitura, com métodos de consumo"). Usar `@dataclass(slots=True)` (sem `frozen`), e não expor os campos mutáveis diretamente — só via método/propriedade, como no esqueleto do research:
```python
@dataclass(slots=True)
class OrcamentoPedido:
    nr_pedido: int
    total_original: int
    _restante_adicao: int = field(init=False)
    _restante_corte: int = field(init=False)

    def __post_init__(self) -> None:
        limite = (self.total_original * 5) // 100  # floor inteiro, nunca ceil
        ...

    def consumir_adicao(self, desejado: int) -> int:
        usado = min(desejado, self._restante_adicao)
        self._restante_adicao -= usado
        return usado
```
Replicar do jobs/domain.py: `slots=True`, validação centralizada, nomes em português (o resto do domínio de pedidos é em português — `motor_adequacao.py`/`edicao_grade.py`/`estoque_virtual.py` — enquanto `jobs/domain.py` é em inglês porque é `app/shared/`; seguir o idioma do pacote em que o arquivo vive, ou seja, português em `pedidos/domain/`).

**Exceção de domínio, se necessária:** não existe hoje uma exceção específica de `motor_adequacao.py`/`edicao_grade.py` (eles levantam `ValueError` cru, ver `edicao_grade.py` linhas 63/70/74/85). Seguir esse precedente local (não o de `jobs/domain.py`) e usar `ValueError`/`raise ValueError("...")` simples se o ledger precisar validar entrada (ex. `total_original < 0`), mantendo consistência com o resto do pacote `pedidos/domain/`.

---

### `app/modules/pedidos/domain/furo_de_grade.py` (novo — ou função dentro de `motor_adequacao.py`)

**Analog:** `app/modules/pedidos/domain/estoque_virtual.py::calcular_estoque_virtual`

**Docstring extenso explicando a regra de negócio ANTES do código** (linhas 31-60) — molde a replicar para `verificar_furo_grade`:
```python
def calcular_estoque_virtual(
    foto: dict[ChaveEstoque, int],
    ordens: Iterable[tuple],
    dt_foto: date | None,
    pares_no_erp: set[tuple[int, str]],
) -> dict[ChaveEstoque, int]:
    """Disponível por (cd_prod_cor, sg_tamanho, canal) descontando as reservas que
    o app já gerou e que a foto de estoque ainda não reflete.

    ...(explica CADA condição da regra, com o "porquê", casos de borda,
    e o piso em zero explicitamente documentado)...

    Piso em zero: a projeção nunca fica negativa.
    """
```
Replicar: assinatura com kwargs nomeados e tipados, docstring que documenta cada condição da regra (aqui: "considera só tamanhos pedidos", "ordena por `get_tamanho_idx`", "tamanho 999 nunca é extremo nem interior", "depende do estoque no momento — roda dentro do loop") — os casos de borda do CONTEXT.md (1 tamanho, 2 adjacentes, PP+GG sem M, tamanho 999) devem virar frases explícitas na docstring, no mesmo estilo de "Piso em zero: a projeção nunca fica negativa."

**Uso de `get_tamanho_idx`/estoque local — reaproveitar sem recriar:**
```python
from app.modules.pedidos.domain.value_objects import get_tamanho_idx, montar_chave_estoque
```
`get_tamanho_idx` já devolve 999 para desconhecido (linha 109 de `value_objects.py`) — a regra "tamanho 999 nunca define extremo" deve filtrar por `idx != 999` antes de calcular `min`/`max`, análogo a como `adequar_grade_produto` já usa `idx_min, idx_max = min(indices), max(indices)` (linha 132) mas SEM filtrar hoje — este é um ponto de divergência a implementar, não copiar cegamente essa linha.

**Assinatura recomendada** (do RESEARCH.md, seção "Funções que não existem hoje"):
```python
def tem_furo_de_grade(itens_pedido_produto: list, estoque_local: Mapping[str, int], cd_prod_cor: str) -> tuple[bool, str | None]:
    ...
```

---

### `app/modules/pedidos/domain/politica_quantidade.py` (novo — estratégia por modo)

**Analog 1 — Protocol como abstração de porta:** `app/shared/jobs/application/ports.py::DurableJobRepository` (linhas 24-114)
```python
class DurableJobRepository(Protocol):
    async def reserve(self, proposed: NewJob) -> JobReservation:
        """Persist job/key atomically, including keys coalesced onto an active job."""
        ...

    async def find_idempotent(self, proposed: NewJob) -> JobReservation | None:
        """Read a compatible binding as REPLAYED, or raise on fingerprint conflict."""
        ...
```
Replicar a FORMA (Protocol com docstring de uma linha por método, tipos completos), mas note que este é `async def` (I/O) — a política de quantidade é síncrona/pura, então adaptar para `def` comum, sem `async`. Ver o esqueleto do RESEARCH.md:
```python
from typing import Protocol

class PoliticaQuantidade(Protocol):
    def decidir(
        self,
        itens: list[dict],
        estoque_local: dict[str, int],
        ledger: "OrcamentoPedido | None",
    ) -> list[dict]:
        """Devolve os itens com qt_liquida/status_item decididos. Nunca decrementa
        estoque nem toca o ledger diretamente — quem chama faz o commit."""
        ...
```

**Analog 2 — StrEnum para "modo"/variante:** `app/modules/pedidos/processing/domain.py` linhas 58-66
```python
class ProcessingMode(StrEnum):
    ADEQUAR = "adequar"
    SEM_ADEQUAR = "sem_adequar"

class ProcessingChannel(StrEnum):
    TODOS = "Todos"
    FRANQUIA = "Franquia"
    MULTIMARCA = "Multimarca"
```
`ProcessingMode` já existe e já nomeia os dois modos exatamente como o CONTEXT.md descreve (`adequar` vs `sem_adequar`) — **não recriar um enum equivalente em `domain/`**; ou reusar `ProcessingMode` (cuidado: vive em `processing/`, camada de aplicação, e o CONTEXT proíbe vazamento de camada — então talvez seja necessário um enum espelho em `domain/` ou aceitar a referência, é decisão do planejador) ou passar o modo como parâmetro `str`/`bool` simples para não criar acoplamento indevido entre `domain/` e `processing/`. Documentar essa tensão explicitamente no plano.

---

### `app/modules/pedidos/domain/rateio.py` (novo — promovendo `_allocate`)

**Analog:** `app/modules/pedidos/domain/edicao_grade.py` linhas 1-40 (arquivo inteiro é o candidato a extração parcial)

**Código exato a promover** (linhas 17-40):
```python
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

def _allocate(total: Decimal, sizes: dict[str, int]) -> dict[str, Decimal]:
    total_cents_signed = int((total * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    sign = -1 if total_cents_signed < 0 else 1
    total_cents = abs(total_cents_signed)
    total_qty = sum(sizes.values())
    allocations: list[tuple[str, int, Decimal]] = []
    base_total = 0
    for size, qty in sorted(sizes.items()):
        exact = Decimal(total_cents) * Decimal(qty) / Decimal(total_qty)
        base = int(exact.quantize(Decimal(1), rounding=ROUND_DOWN))
        allocations.append((size, base, exact - base))
        base_total += base
    remainder = total_cents - base_total
    winners = {
        size
        for size, _base, _fraction in sorted(
            allocations,
            key=lambda item: (-item[2], item[0]),
        )[:remainder]
    }
    return {
        size: (Decimal(sign * (base + int(size in winners))) / 100).quantize(_CENT)
        for size, base, _fraction in allocations
    }
```
Ação: mover para `domain/rateio.py` (ou nome equivalente), renomear `_allocate` -> `ratear_hamilton` (público, sem `_`), manter a assinatura `(total: Decimal, sizes: dict[str, int]) -> dict[str, Decimal]` inalterada — é usada por `montar_grade_atualizada` hoje e vai ser usada por `adequar_grade_produto` amanhã. `edicao_grade.py` passa a importar de `rateio.py` em vez de definir localmente:
```python
from app.modules.pedidos.domain.rateio import ratear_hamilton as _allocate
```
(ou renomear todos os call-sites — decisão do planejador; o `__all__ = ["montar_grade_atualizada"]` no fim do arquivo, linha 125, mostra que o repo já é explícito sobre API pública de módulo — replicar esse padrão no novo `rateio.py`: `__all__ = ["ratear_hamilton"]`).

---

### Testes — `app/tests/test_pedidos_motor.py` e novos arquivos de teste

**Analog:** ele mesmo (20 testes atuais)

**Padrão de factory simples (função, não classe/fixture pytest)** (linhas 15-25):
```python
def _item(sg_tamanho, qt_liquida, vl_liquido, **extra):
    base = {
        "nr_pedido": 1,
        "cd_prod_cor": "PROD1",
        "sg_tamanho": sg_tamanho,
        "ds_grupo": "Camisas",
        "qt_liquida": qt_liquida,
        "vl_liquido": vl_liquido,
    }
    base.update(extra)
    return base
```
Replicar exatamente essa forma para a "factory de pedido completo" que o RESEARCH.md pede (Wave 0 gap): uma função `_pedido(nr_pedido, produtos: list[dict], **extra)` que agrega múltiplos `_item(...)` sob o mesmo `nr_pedido`, retornando `list[dict]` — não introduzir classes de fixture nem `@pytest.fixture` parametrizado; o repo usa funções `_algo(...)` com `**extra` para overrides pontuais, é o idioma local.

**Nomeação de teste — descritivo e longo, em português, prefixado por função testada:**
```python
def test_adequar_grade_produto_falta_alem_da_tolerancia_vira_stand_by():
def test_processar_pedidos_prioriza_maior_valor_e_isola_sem_credito():
def test_processar_pedidos_fatura_um_produto_e_deixa_o_outro_em_stand_by():
```
Padrão: `test_{funcao}_{cenario_de_negocio_em_portugues}`. Para os cenários novos (furo de grade, orçamento por pedido, tudo-ou-nada, determinismo), seguir o mesmo padrão: `test_processar_pedidos_furo_de_grade_bloqueia_tamanho_do_meio`, `test_processar_pedidos_orcamento_acumulado_entre_execucoes_nao_reseta`, etc. — nomes já sugeridos no `RESEARCH.md` seção "Phase Requirements → Test Map" (`-k furo_de_grade`, `-k orcamento_pedido`, `-k acumulado_execucoes`, `-k tudo_ou_nada`, `-k determinismo`, `-k rateio_sem_drift`, `-k minimo_viavel`, `-k prioridade_disputa`) — usar essas substrings nos nomes dos testes para que os comandos `pytest -k` do RESEARCH.md funcionem sem alteração.

**Sem classes de teste, sem `@pytest.mark.parametrize` neste arquivo hoje** — funções soltas, um `assert` por linha lógica, comentários de bloco (`# ---`) separando seções por função testada:
```python
# ---------------------------------------------------------------------------
# adequar_grade_produto
# ---------------------------------------------------------------------------
```
Replicar essa separação por seção de comentário ao adicionar os novos blocos de teste (furo de grade, ledger, política por modo).

**Import direto do shim `service.py`, não do módulo `domain` interno** (linhas 4-12):
```python
from app.modules.pedidos.service import adequar_grade_produto as adequar
from app.modules.pedidos.service import (
    agrupar_por_produto,
    canal_bucket,
    get_tamanho_idx,
    is_sem_credito,
    marcar_stand_by,
    processar_pedidos,
)
```
Manter esse padrão de import — o shim `app/modules/pedidos/service.py` precisa continuar re-exportando os mesmos símbolos (mais os novos, se os testes forem importar `OrcamentoPedido`/`tem_furo_de_grade`/`ratear_hamilton` publicamente) — **confirmar em `service.py` se ele já re-exporta tudo ou se precisa de novas linhas de re-export**.

**Docstring de módulo no topo do arquivo de teste** (linhas 1-2):
```python
"""Unit tests do motor de adequação (Core) — Etapa 0 do plano de extração DDD.
Todas as funções aqui são puras (sem I/O), então os testes não tocam o banco."""
```
Replicar em qualquer novo arquivo de teste (`test_pedidos_motor_orcamento.py` etc.) — uma docstring de módulo de 1-2 linhas explicando o escopo do arquivo.

---

## Shared Patterns

### Nenhuma dependência de banco/HTTP nesta fase
**Fonte:** todo o pacote `app/modules/pedidos/domain/*.py` já é 100% síncrono e sem I/O — nenhuma função aqui usa `async def`, `Depends`, `AsyncSession`. Aplicar a todos os arquivos novos desta fase.

### Aritmética inteira/Decimal, nunca `float` para dinheiro ou orçamento
**Fonte:** `edicao_grade.py` usa `Decimal` com `ROUND_HALF_UP`/`ROUND_DOWN` explícitos; o CONTEXT exige `(total * 5) // 100` inteiro para o ledger. `motor_adequacao.py` atual usa `float`/`math.ceil`/`math.floor` (bug a corrigir, não padrão a copiar). Aplicar: `OrcamentoPedido`, `rateio.py`.

### Exceções de domínio simples via `ValueError`, sem hierarquia de exceção customizada em `pedidos/domain/`
**Fonte:** `edicao_grade.py::montar_grade_atualizada` levanta `ValueError("...")` cru 4 vezes (linhas 63, 70, 74, 85) — nenhuma classe `PedidosDomainError` existe no pacote hoje (diferente de `app/modules/auth/domain/exceptions.py`, que tem `CredenciaisInvalidasError`). Se o ledger precisar de exceção, seguir o padrão local (`ValueError` simples com mensagem descritiva), não importar o padrão de outro módulo.

### Docstring extensa e didática acima de funções de regra de negócio, explicando o "porquê" e casos de borda
**Fonte:** `estoque_virtual.py::calcular_estoque_virtual`, `value_objects.py::canal_bucket`, `edicao_grade.py::montar_grade_atualizada` — todas têm docstrings de 5-20 linhas descrevendo a regra, não só a assinatura. Aplicar a `verificar_furo_grade`, `OrcamentoPedido`, `PoliticaQuantidade`.

### `__all__` explícito em módulos de domínio pequenos e focados
**Fonte:** `edicao_grade.py` linha 125 (`__all__ = ["montar_grade_atualizada"]`). Aplicar a `rateio.py` (`__all__ = ["ratear_hamilton"]`) e a qualquer novo módulo com função única de propósito.

## No Analog Found

| File | Role | Data Flow | Reason |
|---|---|---|---|
| Nenhum — os 3 padrões pedidos (VO/dataclass, predicado com motivo, estratégia por modo) têm análogo direto no repositório, listados acima | — | — | — |

## Metadata

**Analog search scope:** `app/modules/pedidos/domain/`, `app/modules/pedidos/processing/`, `app/shared/jobs/`, `app/tests/test_pedidos_motor*.py`
**Files scanned:** `motor_adequacao.py`, `value_objects.py`, `edicao_grade.py`, `estoque_virtual.py`, `processing/domain.py` (trecho de enums), `shared/jobs/domain.py`, `shared/jobs/application/ports.py`, `test_pedidos_motor.py`
**Pattern extraction date:** 2026-08-14
