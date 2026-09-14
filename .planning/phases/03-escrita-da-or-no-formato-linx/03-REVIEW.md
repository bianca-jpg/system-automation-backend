---
phase: 03-escrita-da-or-no-formato-linx
reviewed: 2026-08-06T00:00:00Z
depth: standard
files_reviewed: 7
files_reviewed_list:
  - app/modules/pedidos/domain/ordem_reserva_linx.py
  - app/modules/pedidos/infrastructure/repositorio_ordens_linx.py
  - app/modules/ingestao/api_leitura.py
  - app/modules/pedidos/application/casos_uso.py
  - app/modules/pedidos/service.py
  - app/modules/pedidos/infrastructure/repositorio_ingestao_readmodel.py
  - app/tests/test_ordem_reserva_linx.py
findings:
  critical: 1
  critical_resolved: 1
  warning: 9
  info: 4
  total: 14
status: critical_resolved
resolution_note: "CR-01 corrigido em 2026-08-06 (totais contam só a grade convertida); 9 warnings + 4 info seguem abertos para triagem/discussão"
---

# Fase 3: Relatório de Code Review

**Revisado:** 2026-08-06
**Profundidade:** standard
**Arquivos revisados:** 7
**Status:** issues_found

## Sumário

Revisei a gravação da linha Linx introduzida pela Fase 3: o domínio puro
(`montar_linha_linx`), o repositório de upsert (`salvar_linhas_linx`), a leitura
em chunks da referência de posições (`obter_referencia_posicoes_por_produtos`),
os dois gatilhos em `casos_uso.py` e os testes puros. As decisões travadas
(D-01, D-02, D-02a, D-03, colunas NULL de propósito, upsert por SELECT+decide,
commit único no caso de uso) foram tratadas como dadas e **não** são findings.
A feature `estoque_virtual` em `casos_uso.py` foi ignorada como material de
outra sessão.

O desenho geral está coerente com o resto do módulo e a transação única
(OR + processados + virtual + Linx antes de um `commit`) está corretamente
montada. O problema central é **aritmético e vaza para o sistema externo**:
`qtde_embalada`/`valor_embalado` são somados sobre a grade INTEIRA, enquanto
`e1..e48` só recebem os tamanhos que a referência conseguiu posicionar. Quando
a grade é parcialmente convertida — caso explicitamente admitido por D-01 — a
linha entregue ao Linx afirma mais peças e mais dinheiro do que a soma da
própria grade posicional, e no pior caso (nenhum tamanho do pedido presente na
referência, mas a referência não vazia) entrega `qtde_embalada > 0` com as 48
posições em zero. Isso não é o mesmo assunto de D-01 (se grava ou não): é a
coerência interna da linha que É gravada.

Além disso: o upsert é read-then-write não atômico (duas gerações concorrentes
derrubam a transação inteira), `salvar_linhas_linx` escreve por `setattr` sem
validar chaves contra as colunas do model (chave desconhecida = erro no INSERT
e silêncio no UPDATE), o warning de D-01 não identifica o pedido e o par fica
marcado como processado para sempre (sem caminho de reprocessamento), e o bloco
de gravação está duplicado literalmente nos dois casos de uso.

## Narrative Findings (AI reviewer)

### Critical

> **✅ CR-01 RESOLVIDO em 2026-08-06** (commit de correção após o review). Decisão da usuária: opção "totais contam só a grade convertida". `montar_linha_linx` passou a derivar `qtde_embalada = sum(posicoes.values())` (invariante `sum(e1..e48) == qtde_embalada` por construção, robusto a ignorados e a conflito de posição) e `valor_embalado` da soma do `vl_liquido` só dos tamanhos não ignorados. Regressão travada por 2 testes novos em `test_ordem_reserva_linx.py` (grade parcial + pior caso "nenhum tamanho converte"). WR-05 (linha 100% zerada) NÃO foi resolvido junto — decisão separada, deixada para discussão com o tech lead; a linha zerada agora é ao menos internamente consistente.

#### CR-01: `qtde_embalada`/`valor_embalado` contam tamanhos que não entraram em nenhuma posição `e1..e48`

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:40-46`

**Issue:** `converter_grade_para_posicoes` devolve, no 2º elemento, os tamanhos
que **não** foram convertidos, e `montar_linha_linx` descarta esse retorno
(`_ignorados`). Em seguida `qtde_embalada = sum(grade.values())` e
`valor_embalado = round(sum(vl_liquido), 2)` somam a grade **inteira**. Resultado:
`sum(e1..e48) != qtde_embalada` sempre que houver tamanho sem posição, e a linha
entregue ao ERP afirma peças/valor que não existem na grade posicional.

Dois caminhos reais chegam nesse estado:

1. **Grade parcialmente convertida (D-01 admite explicitamente).** Exatamente o
   cenário do teste `test_montar_linha_linx_grade_parcialmente_convertida_...`:
   M=5 (pos 5) + XG=2 (sem posição) produz `e5=5` com `qtde_embalada=7` e
   `valor_embalado=700.0`. O caso extremo é referência não vazia mas sem
   NENHUM dos tamanhos do pedido (ex.: referência `{"PP": 1}`, pedido com M e G):
   passa o guard de D-01, as 48 posições ficam 0 e a linha vai para o Linx com
   `qtde_embalada=8` / `valor_embalado=800.00` e grade vazia.
2. **Conflito de posição.** `agregar_referencia_tamanhos`
   (`ingestao/domain/agregacao.py:197-202`, coberto por
   `test_agregar_referencia_tamanhos_detecta_conflito_de_posicao_mantem_ambas_linhas`)
   **mantém as duas linhas** quando dois tamanhos do mesmo produto apontam para
   a mesma posição. `grade_linx.py:34` faz `posicoes[f"e{pos}"] = qtd`
   (atribuição, não acumulação), então a quantidade do tamanho perdedor
   desaparece das posições mas continua dentro de `qtde_embalada`.

Nada na Fase 3 detecta nem sinaliza a divergência: `_ignorados` é jogado fora e
a informação de conflito não é sequer devolvida por `converter_grade_para_posicoes`.
O único rastro é um `logger.warning` na camada de domínio da Fase 2.

**Fix:** derivar os totais da grade **efetivamente convertida** e, quando houver
divergência, tornar isso explícito (não silencioso). Ex.:

```python
posicoes, ignorados = converter_grade_para_posicoes(
    grade, referencia_produto, cd_prod_cor
)

# Totais coerentes com a grade que o Linx vai ler: só o que virou posição.
qtde_embalada = sum(v for k, v in posicoes.items())
valor_embalado = round(
    sum(i["vl_liquido"] for i in itens if i["sg_tamanho"] not in ignorados), 2
)
if ignorados or qtde_embalada != sum(grade.values()):
    logger.warning(
        "pedido %s / produto %s: %d peça(s) fora da grade posicional "
        "(tamanhos sem posição: %s) — qtde_embalada=%d de %d pedidas",
        nr_pedido, cd_prod_cor, sum(grade.values()) - qtde_embalada,
        ignorados, qtde_embalada, sum(grade.values()),
    )
```

Se a decisão de negócio for a oposta (manter `qtde_embalada` como "o que o
cliente pediu" e aceitar grade menor), então isso precisa ser uma decisão
registrada + um aviso explícito por linha divergente + um teste que fixe o
comportamento — hoje não é nenhum dos três. O que não pode ficar é a linha sair
para o ERP com aritmética interna incoerente e sem rastro no caminho da Fase 3.
Nota: isto é ortogonal a D-03 (divergência de centavos em `preco1 × qtde`);
aqui a divergência é em **peças inteiras**.

### Warnings

#### WR-01: `salvar_linhas_linx` escreve sem validar as chaves contra as colunas do model — falha assimétrica (erro no INSERT, silêncio no UPDATE)

**Arquivo:** `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py:34-38`

**Issue:** `OrdemReservaLinx(**linha)` levanta `TypeError` para qualquer chave que
não seja coluna; `setattr(obj, campo, valor)` no ramo de UPDATE **aceita
qualquer nome** e cria um atributo Python inócuo, sem erro e sem persistir —
perda silenciosa de dado. O mesmo dict, portanto, explode em um caminho e
falha em silêncio no outro.

Isso não é hipotético: a chave posicional é construída como `f"e{pos}"` a
partir do `nr_posicao` lido do banco (`grade_linx.py:34`), e `grade_linx` **não
valida a faixa 1..48** — a validação existe só na ingestão
(`agregacao.py:_parse_posicao`). Uma linha em `produto_tamanho_posicao` com
`nr_posicao = 0` ou `49` (coluna é `Integer` sem CHECK, e a tabela pode ser
alimentada por backfill/DBA/migração) produz `e0`/`e49` e cai exatamente nessa
assimetria. Somando D-02a (a função é pública para o v1.2 chamar de fora), um
typo de chave em um chamador futuro também passa batido no UPDATE.

**Fix:** filtrar/validar contra as colunas reais antes de escrever:

```python
COLUNAS = None  # cache lazy

def _colunas_validas() -> set[str]:
    from app.modules.pedidos.models import OrdemReservaLinx
    return {c.key for c in OrdemReservaLinx.__table__.columns} - {"id", "created_at"}

...
desconhecidas = set(linha) - _colunas_validas()
if desconhecidas:
    raise ValueError(
        f"chaves fora do layout de ordens_reserva_linx: {sorted(desconhecidas)}"
    )
```

(e, complementarmente, validar `1 <= pos <= 48` em `converter_grade_para_posicoes`.)

#### WR-02: upsert read-then-write não atômico — duas gerações concorrentes derrubam a transação inteira

**Arquivo:** `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py:28-38`

**Issue:** entre o `SELECT` e o `INSERT` não há nada que impeça outra transação
de inserir o mesmo `(nr_pedido, cd_prod_cor)`. Dois operadores clicando
"Adequar"/"Faturar sem adequação" ao mesmo tempo (ou um retry de request)
resultam em `IntegrityError` no `uq_ordens_reserva_linx_chave` — e, porque tudo
está na mesma transação por design (critério 3), o erro descarta **também** as
ORs, os `pedidos_processados` e o recálculo do estoque virtual daquela rodada.
O padrão SELECT+decide está correto quanto à PK (a justificativa da docstring
procede), mas o Postgres oferece a versão atômica para chave natural.

**Fix:** `ON CONFLICT DO UPDATE` sobre a constraint natural — atômico e, de
quebra, resolve o N+1 (IN-02):

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

async def salvar_linhas_linx(db: AsyncSession, linhas: list[dict]) -> None:
    from app.modules.pedidos.models import OrdemReservaLinx
    if not linhas:
        return
    stmt = pg_insert(OrdemReservaLinx).values(linhas)
    atualizaveis = {
        c: stmt.excluded[c] for c in linhas[0] if c not in ("nr_pedido", "cd_prod_cor")
    }
    await db.execute(
        stmt.on_conflict_do_update(
            constraint="uq_ordens_reserva_linx_chave", set_=atualizaveis
        )
    )
```

#### WR-03: o warning de D-01 não identifica o pedido, e o par fica marcado como processado sem caminho de reprocessamento

**Arquivo:** `app/modules/pedidos/application/casos_uso.py:471-474` e `532-535`

**Issue:** o warning nomeia o produto (`cd`) mas **não o pedido** (`nr`), e é
emitido uma vez por par com texto idêntico. Com N clientes do mesmo produto sem
referência, o log recebe N linhas indistinguíveis e a operação não consegue
dizer quais ORs ficaram sem linha Linx. Pior: `salvar_processados` já gravou o
par como processado, então quando a referência for corrigida no Databricks o
par **nunca** será reavaliado por `executar_adequacao`/`executar_sem_adequacao`
— a linha Linx daquela OR não nasce nunca mais, e o único vestígio é uma
mensagem de log sem o número do pedido. O `montar_linha_linx`/`salvar_linhas_linx`
reexportados (D-02a) permitem consertar manualmente, mas ninguém sabe **o que**
consertar.

**Fix:** identificar o par e agregar num aviso único e acionável:

```python
sem_referencia: list[tuple[int, str]] = []
...
if linha is None:
    sem_referencia.append((nr, cd))
    continue
...
if sem_referencia:
    logger.warning(
        "%d par(es) sem nenhuma posição em produto_tamanho_posicao — linha Linx "
        "NÃO gravada (OR interna segue normal): %s",
        len(sem_referencia), sorted(sem_referencia),
    )
```

#### WR-04: `nome_clifor` fabrica `"Cliente {nr_pedido}"` numa tabela de saída para sistema externo

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:53`

**Issue:** o fallback `f"Cliente {nr_pedido}"` é legítimo na EXIBIÇÃO (é o que
`_montar_pedidos` faz para não mostrar card sem nome), mas aqui ele inventa um
nome de cliente que será importado pelo Linx. Contraria o critério "nada é
inventado" que o próprio módulo declara (docstring linhas 28-30) e a coluna
`nome_clifor` é `nullable=True` — não há necessidade de preencher. Um "Cliente
5" entrando no ERP como nome de clifor é dado sujo em sistema de terceiro, e
mais difícil de detectar do que um NULL.

**Fix:**

```python
nome_clifor = meta.get("client")  # NULL quando a origem não tem o nome
```

#### WR-05: linha Linx com `qtde_embalada == 0` é gravada — e é alcançável pelo fluxo real

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:44-46`

**Issue:** a divisão por zero está corretamente evitada (`preco1 = None`), mas a
linha **é emitida**: `qtde_embalada=0`, `preco1=NULL`, `e1..e48` todos 0. Isso é
uma linha vazia na bandeja de saída do ERP.

E não é só teoria: em `adequar_grade_produto` (`motor_adequacao.py:71-93`), uma
grade de total 1 peça com estoque 0 e tolerância 0,05 dá
`limite_falta = ceil(0.05) = 1` e `falta_total = 1`; como o teste é
`falta_total > limite_falta`, o status vira **"Gerar OR"** e
`nova_qtd = min(1, 0) = 0`. O par entra em `selecionados` → `ors_a_gravar` →
`montar_linha_linx` → linha 100% zerada persistida. Também vale o inverso, com
dado sujo de origem: `qt_liquida = 0` com `vl_liquido > 0` grava
`valor_embalado > 0` com `qtde_embalada = 0`.

**Fix:** não exportar linha sem peça (e deixar o motivo no log, como em D-01):

```python
if qtde_embalada <= 0:
    return None   # nada a reservar: não há linha Linx a entregar ao ERP
```

...ou filtrar no chamador junto do tratamento de `linha is None`. Em qualquer
caso, o teste `test_montar_linha_linx_qtde_embalada_zero_nao_lanca_excecao`
precisa ser atualizado para fixar a decisão escolhida.

#### WR-06: bloco de gravação Linx duplicado literalmente nos dois casos de uso

**Arquivo:** `app/modules/pedidos/application/casos_uso.py:465-477` e `526-538`

**Issue:** 13 linhas idênticas (montagem do set de `cd_prod_cor`, leitura da
referência, loop, warning, `salvar_linhas_linx`) repetidas nos dois fluxos, com
a única diferença sendo `tipo="com"`/`tipo="sem"`. Qualquer correção deste
review (CR-01, WR-03, WR-05) precisa ser aplicada duas vezes, e a próxima
alteração vai divergir — sem falar no v1.2, que precisará de um terceiro
chamador (D-02).

**Fix:** extrair o helper que o v1.2 vai reaproveitar:

```python
async def _gravar_linhas_linx(
    db: AsyncSession, ors: dict[tuple[int, str], list[dict]], tipo: str
) -> None:
    """Projeta as ORs do lote no layout Linx. Mesma transação do chamador."""
    referencia = await carregar_referencia_posicoes(db, {cd for _nr, cd in ors})
    linhas, sem_referencia = [], []
    for (nr, cd), itens in ors.items():
        linha = montar_linha_linx(nr, cd, itens, referencia.get(cd, {}), tipo=tipo)
        (linhas if linha is not None else sem_referencia).append(linha or (nr, cd))
    ...
    await salvar_linhas_linx(db, linhas)
```

#### WR-07: `itens[0]` sem guarda — `IndexError` numa função pública, e nome do cliente dependente da ordem dos itens

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:52-53`

**Issue:** duas coisas no mesmo `meta = itens[0]`:

1. `itens=[]` com `referencia_produto` não vazio passa o guard da linha 32,
   monta `grade={}`, chega em `itens[0]` e levanta `IndexError`. Os dois
   chamadores atuais nunca passam lista vazia, mas D-02a torna a função uma API
   pública destinada a chamadores futuros (v1.2, edição manual) — e não há teste
   cobrindo o caso.
2. O nome do cliente vem só do PRIMEIRO item. Se o item 0 tiver `client=None` e
   os demais tiverem o nome preenchido, grava-se o fallback fabricado (ver
   WR-04) mesmo havendo o nome disponível na grade.

**Fix:**

```python
if not itens:
    return None
...
nome_clifor = next((i.get("client") for i in itens if i.get("client")), None)
```

#### WR-08: `tipo` não é validado numa função pública que alimenta coluna `String(8) NOT NULL`

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:11` e `58`

**Issue:** `tipo` entra no dict sem validação e vai direto para
`OrdemReservaLinx.tipo` (`String(8)`, `nullable=False`, contrato `'sem' | 'com'`).
Um chamador do v1.2 (D-02a) passando `"comAdequacao"` ou `"COM"` grava lixo
(ou estoura em `DataError` a >8 chars) numa coluna que o ERP e a leitura interna
interpretam. Como a função é a fronteira pública do domínio, é aqui que o
contrato deve ser garantido.

**Fix:**

```python
TIPOS_VALIDOS = ("com", "sem")
...
if tipo not in TIPOS_VALIDOS:
    raise ValueError(f"tipo inválido: {tipo!r} (esperado um de {TIPOS_VALIDOS})")
```

#### WR-09: os testes não cobrem justamente a aritmética divergente nem os caminhos de borda

**Arquivo:** `app/tests/test_ordem_reserva_linx.py:15-27`

**Issue:** `test_montar_linha_linx_grade_parcialmente_convertida_continua_gerando_linha`
é o único teste do cenário de CR-01 e assere **apenas** `linha is not None` e
`linha["e5"] == 5`. Os dois campos onde está o defeito — `qtde_embalada` (7) e
`valor_embalado` (700.0) contra uma grade posicional de 5 peças — não são
asserados, então o comportamento incorreto passa sem ninguém precisar decidir
sobre ele. Faltam também: (a) caso `itens=[]` (WR-07), (b) caso de conflito de
posição (dois tamanhos na mesma posição), (c) caso "referência não vazia mas
sem nenhum tamanho do pedido" (a pior instância de CR-01), (d) `tipo` inválido.

**Fix:** ao corrigir CR-01, fixar a decisão com asserts explícitos, por exemplo:

```python
def test_grade_parcialmente_convertida_totais_batem_com_a_grade_posicional():
    itens = [
        {"sg_tamanho": "M", "qt_liquida": 5, "vl_liquido": 500.0},
        {"sg_tamanho": "XG", "qt_liquida": 2, "vl_liquido": 200.0},
    ]
    linha = montar_linha_linx(1, "X|1", itens, {"M": 5}, tipo="com")

    posicional = sum(v for k, v in linha.items() if k.startswith("e"))
    assert linha["qtde_embalada"] == posicional  # hoje: 7 != 5
    assert linha["valor_embalado"] == 500.0
```

### Info

#### IN-01: a lista `colunas_sem_fonte` do teste duplica o layout do model

**Arquivo:** `app/tests/test_ordem_reserva_linx.py:97-104`

**Issue:** as 22 colunas estão corretas e completas hoje, mas hardcoded: uma
coluna nova sem fonte adicionada ao model não será coberta, e o teste passa a
dar falsa segurança sobre o critério 5.

**Fix:** derivar do model — `{c.key for c in OrdemReservaLinx.__table__.columns} -
set(linha) - {"id", "created_at"}` e assertar que o conjunto restante é
exatamente o esperado.

#### IN-02: um `SELECT` por linha no upsert

**Arquivo:** `app/modules/pedidos/infrastructure/repositorio_ordens_linx.py:28-33`

**Issue:** performance está fora do escopo v1, registro apenas por contexto: uma
rodada de adequação pode gerar centenas/milhares de pares e o loop faz um
round-trip por par, dentro da transação que também contém ORs e estoque virtual
(prolongando a janela de lock). Resolvido de graça pelo fix de WR-02
(`ON CONFLICT` com `values(linhas)` em um único statement).

#### IN-03: logging misturando f-string e lazy `%s` no mesmo arquivo

**Arquivo:** `app/modules/pedidos/application/casos_uso.py:471-474` vs `479-482`

**Issue:** o código novo da fase usa corretamente `%s` lazy, mas convive com
`logger.info(f"...")` nas linhas vizinhas do mesmo bloco. Inconsistência
herdada; vale padronizar em `%s` ao tocar o arquivo.

#### IN-04: `preco1` é arredondamento sobre arredondamento

**Arquivo:** `app/modules/pedidos/domain/ordem_reserva_linx.py:45-46`

**Issue:** `valor_embalado` já é `round(soma, 2)` e `preco1` divide **esse valor
arredondado**. A divergência de centavos em `preco1 × qtde` é aceita por D-03,
mas o double-rounding a amplia sem necessidade. Usar a soma crua como dividendo
mantém D-03 intacto e reduz o erro:

```python
soma = sum(item["vl_liquido"] for item in itens)
valor_embalado = round(soma, 2)
preco1 = round(soma / qtde_embalada, 2) if qtde_embalada else None
```

---

_Revisado: 2026-08-06_
_Revisor: Claude (gsd-code-reviewer)_
_Profundidade: standard_
