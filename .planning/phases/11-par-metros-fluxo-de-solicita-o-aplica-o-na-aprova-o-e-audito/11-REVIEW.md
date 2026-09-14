---
phase: 11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito
reviewed: 2026-08-18T00:00:00Z
depth: standard
files_reviewed: 5
files_reviewed_list:
  - app/shared/infrastructure/databricks_client.py
  - app/tests/test_databricks_client.py
  - app/modules/parametros/domain/registro.py
  - app/modules/parametros/domain/exceptions.py
  - app/tests/test_parametros_registro.py
findings:
  critical: 2
  warning: 8
  info: 8
  total: 18
status: issues_found
---

# Phase 11: Code Review Report

**Reviewed:** 2026-08-18
**Depth:** standard
**Files Reviewed:** 5
**Status:** issues_found

## Summary

Two independent bodies of work were reviewed adversarially.

**(1) Databricks fallback fix (uncommitted).** The two-layer fix is directionally right and both
layers are genuinely load-bearing — reverting either one breaks a test. The retry loop is *not*
unbounded (both fallback branches are gated on `current_disposition == "INLINE"`, which is only ever
assigned once, so at most two iterations), and `byte_limit` is computed exclusively from local config
with no provider input, so no bound was weakened by the change. However the fix is **incomplete in a
way that reproduces the failure class it was written to end**: the bytes downloaded by the abandoned
INLINE attempt stay charged to the shared `_ResponseBudget`, so the EXTERNAL_LINKS retry can die with
a non-retryable `DatabricksLimitError("bytes")` — reproduced empirically below. It works in
production today only because someone hand-set `DATABRICKS_MAX_BYTES=200000000` in an ungitted
`.env`; nothing in code or settings validates that invariant. Separately, the fix promotes the
EXTERNAL_LINKS collector — which by the debug record had likely **never** run in production for
`pedidos` — to the primary path for a 449k-row full refresh, and that collector has no
reconciliation between rows collected and `manifest.total_row_count`, so a partial page silently
becomes a partial full-refresh snapshot.

**(2) Parâmetros registry (committed, 95499bb / 42290d8).** The implementation follows 11-01-PLAN.md
very closely, and that is the problem: three of the defects below are specified *by the plan*, so
they route to a spec amendment rather than a code fix. The most consequential is that the range
check is not type-guarded, so it raises `TypeError` (→ 500) instead of the domain error the module
exists to produce — latent today, but it fires the moment a third registry entry is added, which is
the module's entire reason to exist. The drift guard is also weaker than the plan's `must_haves`
claim: it only recognises one exact call shape.

All 65 tests in the two in-scope test files pass (`./.venv/Scripts/python.exe -m pytest
app/tests/test_databricks_client.py app/tests/test_parametros_registro.py -q`), so "tests green" is
not evidence for any of the below.

## Critical Issues

### CR-01: EXTERNAL_LINKS retry inherits the discarded INLINE attempt's byte budget and dies with a misleading, non-retryable error

**File:** `app/shared/infrastructure/databricks_client.py:566-572` (retry branch), `:292-295`
(`consume_bytes`), `:997` (charge site), `:487-491` (single budget for the whole `execute`)

**Issue:** CONFIRMED (independently reproduced). `_ResponseBudget` is created once per `execute()` and
threaded through both attempts. The INLINE attempt downloads its full truncated payload — up to
`min(max_bytes, 26_214_400)` bytes, i.e. up to 25 MiB — and `_request_once` charges every one of
those bytes via `budget.consume_bytes` at line 997. When the new branch at line 566 abandons that
attempt, **nothing is credited back**. The EXTERNAL_LINKS retry therefore starts with a budget
already spent and can fail on its very first response.

Reproduction (scratch test, `max_bytes=1_048_576`, INLINE truncated payload of ~1_048_300 bytes):

```
WARNING  databricks_client.py:567 Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS.
SUBMITS: 2  ERRO: A resposta excedeu o limite configurado de bytes.
```

Two things make this critical rather than cosmetic:

1. The resulting `DatabricksLimitError` has `retryable=False` and says `bytes`, not `truncado` — so
   the Celery task gives up permanently and the operator is pointed at the wrong knob. That is
   exactly the diagnostic dead-end that kept `pedidos` stale from 2026-08-05 to 2026-08-18.
2. The new fallback is **dead code for any `DATABRICKS_MAX_BYTES` at or below roughly 28 MB**, and
   `app/shared/config/settings.py:187-191` still allows `ge=1_048_576`. The value that made
   production work yesterday (`26214400`, chosen to match the INLINE ceiling) is precisely a value
   for which the fallback cannot succeed. Nothing in `DatabricksClientConfig.__post_init__` encodes
   the new invariant `max_bytes > _INLINE_BYTE_LIMIT_CEILING + <real payload>`; it survives only in
   an ungitted `.env`.

**Fix:** snapshot and restore the budget across the disposition switch, and fail fast at config time
when the budget cannot accommodate a fallback.

```python
# databricks_client.py — in _ResponseBudget
def rollback_bytes(self, amount: int) -> None:
    """Devolve ao orcamento os bytes de uma tentativa abandonada."""
    self.bytes = max(0, self.bytes - amount)

# databricks_client.py — in _execute_with_client, around the two fallback branches
bytes_before_attempt = budget.bytes
...
if current_disposition == "INLINE" and _manifest_is_truncated(payload):
    logger.warning(
        "Resultado INLINE truncado pelo provedor (%d bytes descartados); "
        "repetindo em EXTERNAL_LINKS.",
        budget.bytes - bytes_before_attempt,
    )
    budget.rollback_bytes(budget.bytes - bytes_before_attempt)
    current_disposition = "EXTERNAL_LINKS"
    continue
```

```python
# DatabricksClientConfig.__post_init__ — make the new invariant explicit
if self.max_bytes <= _INLINE_BYTE_LIMIT_CEILING:
    raise ValueError(
        "max_bytes deve exceder o teto de INLINE para permitir o fallback "
        "para EXTERNAL_LINKS"
    )
```

Also raise `databricks_max_bytes`'s `ge` in `settings.py` to `_INLINE_BYTE_LIMIT_CEILING * 2` so a
bad `.env` is rejected at startup rather than two hours later inside a Celery task.

### CR-02: the EXTERNAL_LINKS collector never reconciles collected rows against `manifest.total_row_count`, so a partial page becomes a partial full-refresh snapshot

**File:** `app/shared/infrastructure/databricks_client.py:754-757` (`raw_links is None → []`),
`:809` (unconditional return), `:401-406` (`total_row_count` read and discarded), `:566-572` (the
change that makes this the primary path)

**Issue:** `_collect_external_links` explicitly tolerates a page with no `external_links` key
(`if raw_links is None: raw_links = []`) and then returns whatever rows it happened to gather, with
no check that the total matches the manifest. Concretely: initial result carries `external_links=[l1]`
plus `next_chunk_internal_link=/chunks/1`; the page fetched from `/chunks/1` comes back as `{}` or
`{"result": {}}`; the client silently collects only `l1`'s rows and returns **success**. For
`pedidos` that is a ~449k-row full refresh replaced by a fraction of itself.

`app/modules/ingestao/application/casos_uso.py:78-94` only guards the *fully empty* case
(`substituir=bool(agrupado)`), so an empty read is safe but a **partial** read is committed:
`DELETE` + `INSERT` of a truncated snapshot. Downstream that is wrong ORs sent to Linx — the exact
auditability property `CLAUDE.md` names as the project's core value.

This code is pre-existing, but the diff is what makes it matter: per
`.planning/debug/pedidos-inline-truncated.md`, `pedidos` had never successfully used EXTERNAL_LINKS
before, and now every 2-hour cycle routes through it. The manifest field needed to close the hole is
already parsed at line 401 and thrown away.

**Fix:** carry `total_row_count` out of the manifest and assert it after collection.

```python
def _expected_rows(payload: Mapping[str, Any]) -> int | None:
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        return None
    total = manifest.get("total_row_count")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    return total

# at the end of _execute_with_client, for both dispositions
esperado = _expected_rows(payload)
if esperado is not None and len(rows) != esperado:
    raise DatabricksProtocolError(
        "Coleta incompleta: o manifesto declarou mais linhas do que foram lidas."
    )
```

Also stop swallowing a missing `external_links` key: distinguish "page legitimately has no links"
(only valid when the page also has no `next_chunk_internal_link` and rows already match the manifest)
from "malformed page", and raise `DatabricksProtocolError` for the latter instead of returning short.

## Warnings

### WR-01: the abandoned INLINE statement is leaked server-side — `state.statement_id = None` destroys the only handle to it

**File:** `app/shared/infrastructure/databricks_client.py:539-548`

**Issue:** CONFIRMED empirically. Instrumenting every request during a truncated-INLINE fallback
shows exactly three calls and no cleanup:

```
REQ: ('POST', '/api/2.0/sql/statements')   # INLINE, SUCCEEDED + truncated
REQ: ('POST', '/api/2.0/sql/statements')   # EXTERNAL_LINKS
REQ: ('GET',  '/chunk')
```

No `POST /statements/{id}/cancel`, no `DELETE /statements/{id}`. This differs from the pre-existing
`_is_inline_limit` branch in a way that matters: there the abandoned statement is already in a
terminal error state, so there is nothing to release. Here the statement is **SUCCEEDED** and
Databricks is holding a materialised result set for it until its own retention expires. Line 540
(`state.statement_id = None`) then overwrites the handle before the second submit, so:

- the abandoned statement is never closed, on every sync cycle, forever;
- if the *second* submit or poll then hits the deadline, `_cancel_shielded` is called with the
  second statement's id only (or `None`, if the second submit failed before returning an id) — the
  first statement is unreachable and leaks even in the error path.

`_ExecutionState` holding a single `statement_id` is what structurally prevents cleanup.

**Fix:** close the abandoned statement best-effort before switching, reusing the existing shielded
compensation, then reset:

```python
if current_disposition == "INLINE" and _manifest_is_truncated(payload):
    logger.warning("Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS.")
    await self._close_best_effort(client, state.statement_id)  # DELETE /statements/{id}
    current_disposition = "EXTERNAL_LINKS"
    continue
```

`_close_best_effort` should mirror `_cancel_best_effort` (own timeout, swallow-and-log, never mask
the original flow) but issue `DELETE /api/2.0/sql/statements/{id}`, which is the documented way to
release a succeeded result. Apply it to both fallback branches.

### WR-02: `test_manifesto_ainda_truncado_apos_external_links_levanta_erro` is vacuous — it passes with the fix reverted

**File:** `app/tests/test_databricks_client.py:458-473`

**Issue:** CONFIRMED. Neutralising the fix (`monkeypatch.setattr(mod, "_manifest_is_truncated",
lambda payload: False)`) and re-running this test's body: **1 submit, test still passes.** With the
fix reverted, the very first INLINE response raises `DatabricksLimitError("dados; resultado truncado
pelo provedor")`, which satisfies `match="truncado"` — so the test can never distinguish "retried and
still truncated" from "never retried at all". It is the only test covering the still-truncated
terminal case, and it asserts nothing about the behaviour it is named after.

(For contrast, `test_manifesto_truncado_inline_repete_em_external_links` at :431 and
`test_byte_limit_inline_e_clampado_ao_teto_da_api_mas_external_links_nao` at :580 were both verified
to be genuine pins — each fails if its half of the fix is reverted.)

**Fix:** count submits, exactly as the sibling test does.

```python
async def test_manifesto_ainda_truncado_apos_external_links_levanta_erro() -> None:
    submits = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submits
        if request.method == "POST":
            submits += 1
            ...

    with pytest.raises(DatabricksLimitError, match="truncado"):
        await make_client(handler).execute("SELECT 1")
    assert submits == 2  # provou que o fallback rodou antes de desistir
```

### WR-03: no test covers the byte-budget carryover, and the new config invariant is untested

**File:** `app/tests/test_databricks_client.py:431-474`, `:580-612`

**Issue:** Both new tests use payloads of a handful of bytes against `max_bytes` of 200_000 /
200_000_000, so the budget is never anywhere near exhausted and CR-01 is invisible to the suite. The
existing `test_streaming_interrompe_resposta_que_excede_max_bytes` (:489) covers a single attempt
only. Nothing pins the relationship between `max_bytes` and `_INLINE_BYTE_LIMIT_CEILING`, which is
now the invariant the whole fallback depends on — so a future `DATABRICKS_MAX_BYTES` regression to
26214400 would ship green.

**Fix:** add the reproduction from CR-01 as a permanent regression test, plus a config-level test.

```python
@pytest.mark.asyncio
async def test_bytes_da_tentativa_inline_abandonada_nao_consomem_o_orcamento() -> None:
    padding = "x" * 1_048_300  # quase todo o orcamento
    # ... INLINE truncado com esse payload, depois EXTERNAL_LINKS ...
    rows = await make_client(handler, client_config=config(max_bytes=1_048_576)).execute("SELECT 1")
    assert rows == [{"value": "ok"}]  # hoje: DatabricksLimitError("bytes")


def test_max_bytes_precisa_exceder_o_teto_de_inline() -> None:
    with pytest.raises(ValueError):
        config(max_bytes=26_214_400)
```

### WR-04: the success log reports the requested disposition, not the one that served the result

**File:** `app/shared/infrastructure/databricks_client.py:521-525`

**Issue:** CONFIRMED. `_execute_with_client` owns `current_disposition` and never publishes it;
`execute` logs `normalized_disposition`, the value the *caller* asked for. In production every
`pedidos` sync now falls back, so the one line that gets emitted per sync says `disposition=INLINE`
for a result that was entirely served by EXTERNAL_LINKS. Given that this incident was diagnosed
largely by grepping for a WARNING line, degrading the INFO line's truthfulness on the same code path
is a real regression in operability.

**Fix:** return the serving disposition and log that.

```python
rows, served_disposition = await self._execute_with_client(...)
...
logger.info(
    "Databricks: %d linhas retornadas (disposition solicitada=%s, efetiva=%s).",
    len(rows), normalized_disposition, served_disposition,
)
```

### WR-05: the range check is not type-guarded — it raises `TypeError` (→ 500) instead of `ValorDeParametroInvalidoError`

**File:** `app/modules/parametros/domain/registro.py:111-117`

**Issue:** CONFIRMED empirically, in two distinct ways. The guard is `if conhecido.minimo is not None
or conhecido.maximo is not None`, but the body compares against *both* bounds and assumes `coagido`
is numeric:

```
1. entry with minimo only        -> TypeError: '<=' not supported between instances of 'float' and 'NoneType'
3. JSON entry with minimo/maximo -> TypeError: '<=' not supported between instances of 'float' and 'dict'
```

Both escape `validar_valor_de_parametro` uncaught, so a write path that plans 04/05 will wrap in a
4xx handler produces a 500 instead. Latent today (both current entries set both bounds and are
numeric) — but this module exists precisely so that entries get added, and the failure mode is
"adding a registry entry crashes the API".

**PLAN ATTRIBUTION:** `11-01-PLAN.md:162` specifies the `ou` guard verbatim ("Se `conhecido.minimo`
**ou** `conhecido.maximo` estiver definido, checar `minimo <= coagido <= maximo`"). The code follows
the spec; the spec is wrong. Fixing this needs a plan amendment as well as a code change.

Related, same root cause: `_coerce(valor, "bool")` (`coercao.py:16-17`) can never raise, so for a
future BOOL key the `try/except` at :103-109 is unreachable and any string is silently accepted as
`False`.

**Fix:** compare bound-by-bound and reject non-numeric coerced values before comparing.

```python
if conhecido.minimo is not None or conhecido.maximo is not None:
    if isinstance(coagido, bool) or not isinstance(coagido, (int, float)):
        raise ValorDeParametroInvalidoError(
            chave, f"faixa numerica declarada, mas o valor coagido é '{type(coagido).__name__}'."
        )
    fora_do_minimo = conhecido.minimo is not None and coagido < conhecido.minimo
    fora_do_maximo = conhecido.maximo is not None and coagido > conhecido.maximo
    if fora_do_minimo or fora_do_maximo:
        raise ValorDeParametroInvalidoError(chave, _motivo_de_faixa(conhecido, coagido))
```

### WR-06: unbounded, unsanitised user input is echoed into `motivo`, which is destined for an HTTP `detail` and the logs

**File:** `app/modules/parametros/domain/registro.py:106-109`

**Issue:** `f"valor '{valor}' não é conversível para o tipo '{tipo}'."` interpolates the raw
candidate value. `ParametroUpdate.valor` (`application/schemas.py:44`) is a bare `str | None` with no
`max_length`, and the change-request path is worse: `ChangeRequestCreate.proposed_payload` is
`dict[str, Any]` with no shape validation at all, so whatever a `gestor` submits reaches this
f-string verbatim. Confirmed round-trip:

```
6. motivo repr: "valor 'abc\nWARNING: fake log line' não é conversível para o tipo 'float'."
```

Consequences once plans 03-05 surface `motivo` as `detail` and log it: embedded newlines forge log
lines (the exception message is what `ParametrosDomainError.__str__` yields, and
`leitura_resiliente.py:34` already shows this module's habit of logging), and a multi-megabyte
`valor` produces a multi-megabyte error response. Note the value is *rejected*, so the only reason it
reaches an operator's eyes is this message.

**Fix:** bound and flatten before interpolating, and add `max_length` at the schema boundary.

```python
def _resumo(valor: object, *, limite: int = 64) -> str:
    texto = str(valor).replace("\n", " ").replace("\r", " ")
    return texto if len(texto) <= limite else f"{texto[:limite]}…"
...
raise ValorDeParametroInvalidoError(
    chave, f"valor '{_resumo(valor)}' não é conversível para o tipo '{tipo}'."
) from exc
```

### WR-07: the drift guard only recognises one exact call shape, so the plan's central `must_have` truth is over-claimed

**File:** `app/tests/test_parametros_registro.py:88-104`

**Issue:** The extraction is
`re.findall(r'get_param_value\(\s*self\._db,\s*"([^"]+)"', corpo)`. It matches today's two calls
(verified against `adapters.py:305-318`), but a third key added in any of these shapes is **not
detected** and the suite stays green:

```python
await param_service.get_param_value(db, "nova_chave", default=...)             # receptor diferente
await param_service.get_param_value(self._db, chave="nova_chave", default=...) # kwarg
await param_service.get_param_value(self._db, _CHAVE_NOVA, default=...)        # constante
```

The `assert chaves_lidas_pelo_motor` non-empty check does not help — it only fires if *every* key
stops matching. So `11-01-PLAN.md:23` ("Se alguém acrescentar uma terceira chave em
`load_adequation_config` sem declará-la no registro, a suíte falha") and the T-11-04 mitigation at
:205 are both true only for one spelling. The body regex is equally brittle: it requires
`load_adequation_config` to be followed by another `async def ` at four-space indent, so making it
the last method of the class turns the guard off (it does assert loudly in that case, which is
good).

**Fix:** parse instead of grepping — `ast` makes every call shape visible and removes the
"next `async def`" dependency.

```python
import ast

arvore = ast.parse(fonte_adapter)
funcao = next(
    n for n in ast.walk(arvore)
    if isinstance(n, ast.AsyncFunctionDef) and n.name == "load_adequation_config"
)
chaves_lidas_pelo_motor = set()
nao_literais = []
for no in ast.walk(funcao):
    if not (isinstance(no, ast.Call) and getattr(no.func, "attr", None) == "get_param_value"):
        continue
    alvo = no.args[1] if len(no.args) > 1 else next(
        (kw.value for kw in no.keywords if kw.arg == "chave"), None
    )
    if isinstance(alvo, ast.Constant) and isinstance(alvo.value, str):
        chaves_lidas_pelo_motor.add(alvo.value)
    else:
        nao_literais.append(ast.dump(alvo) if alvo is not None else "<ausente>")
assert not nao_literais, f"chave não literal em load_adequation_config: {nao_literais}"
```

### WR-08: the drift guard asserts set *equality* against a single function, contradicting the registry's own documented scope

**File:** `app/tests/test_parametros_registro.py:96-104`; `app/modules/parametros/domain/registro.py:1-16`

**Issue:** CONFIRMED. `registro.py`'s docstring and `PARAMETROS_CONHECIDOS`' contract are framed as
"as chaves que o motor de adequação honra literalmente", but the test enforces
`chaves_lidas_pelo_motor == chaves_registradas` where the left side is computed from exactly one
function body. `apenas_no_registro` being an assertion failure means: the day the engine honours a
key read anywhere other than `load_adequation_config`, registering it (the correct action) breaks the
suite, and the only way to get green is to *not* document the key — i.e. the guard actively pushes
toward the drift it was written to prevent. Grep confirms both keys are today read only there
(`get_param_value` call sites: `adapters.py:306`, `adapters.py:312`, plus tests), so this is
forward-looking, not currently wrong.

**PLAN ATTRIBUTION:** `11-01-PLAN.md:109` specifies equality ("assertar que o conjunto extraído é
**igual** ao conjunto de chaves de `PARAMETROS_CONHECIDOS`").

**Fix:** keep the strong direction, relax the weak one, and make the scope explicit — assert
`chaves_lidas_pelo_motor <= chaves_registradas` (a key the engine reads must be registered) and
either scan every engine module for `get_param_value` call sites before asserting the reverse, or
narrow the registry docstring to say "lidas por `load_adequation_config`".

## Info

### IN-01: the two fallback branches in `_execute_with_client` are structurally duplicated

**File:** `app/shared/infrastructure/databricks_client.py:557-572`
**Issue:** CONFIRMED. Both branches are `if current_disposition == "INLINE" and <predicado>: log;
current_disposition = "EXTERNAL_LINKS"; continue`, differing only in predicate and message. Any
cleanup added for WR-01/CR-01 has to be written twice, which is precisely how one of them will drift.
**Fix:** collapse into one guarded switch, e.g. compute `motivo_do_fallback: str | None` from the two
predicates and have a single branch perform log + rollback + close + switch.

### IN-02: the "fração, não percentual" hint lives in the generic range branch

**File:** `app/modules/parametros/domain/registro.py:114-117`
**Issue:** CONFIRMED. Injecting a hypothetical INT key with `minimo=1.0, maximo=30.0` yields:
`"valor deve estar entre 1.0 e 30.0 (fração, não percentual — 0.05 = 5%); recebido 99."` — factually
wrong for that key. The bounds also print as floats (`1.0`, `30.0`) for an INT key.
**PLAN ATTRIBUTION:** `11-01-PLAN.md:162` asks for this wording in this branch.
**Fix:** move the per-key hint onto `ParametroConhecido` (e.g. `dica_de_faixa: str | None`) and
append it only when present; format bounds with `:g`.

### IN-03: `assert "registro" not in fonte_leitura` is a substring check on a common Portuguese word

**File:** `app/tests/test_parametros_registro.py:160`
**Issue:** CONFIRMED (passes today only because `leitura_resiliente.py` happens to contain no
`regist*`). Any future comment mentioning "registro"/"registrar"/"registrado" in that file fails a
test whose stated purpose is unrelated, and the failure message will not explain why.
**PLAN ATTRIBUTION:** `11-01-PLAN.md:170` asks for exactly this assertion.
**Fix:** assert on the import graph instead of the text —
`assert "domain.registro" not in fonte_leitura` and `assert "validar_valor_de_parametro" not in
fonte_leitura`, or walk the module's `ast` imports.

### IN-04: `PARAMETROS_CONHECIDOS` is a mutable `dict` behind a `Final` annotation

**File:** `app/modules/parametros/domain/registro.py:43`
**Issue:** CONFIRMED — `PARAMETROS_CONHECIDOS["so_minimo"] = ...` succeeds at runtime. `Final`
prevents rebinding, not mutation, so the "fonte única de verdade" and the drift guard can be
sidestepped by any in-process write.
**Fix:** `PARAMETROS_CONHECIDOS: Final[Mapping[str, ParametroConhecido]] =
MappingProxyType({...})`. The dataclass is already `frozen=True, slots=True`, which is right.

### IN-05: `max_polls` is per attempt, so the fallback doubles the documented poll ceiling

**File:** `app/shared/infrastructure/databricks_client.py:648-651`
**Issue:** `polls = 0` is local to `_poll_until_terminal`, and the retry calls it again, so a single
`execute()` can issue up to `2 × max_polls` polls. Not a liveness problem — the absolute `deadline`
and the `asyncio.timeout(total_timeout)` in `execute` still bound it — but `max_polls` no longer
means what its name and the config docstring imply.
**Fix:** either pass a shared poll counter through the loop, or document `max_polls` as per-attempt.

### IN-06: the registry module has no production callers yet, so PARAM-04's enforcement is not in effect

**File:** `app/modules/parametros/domain/registro.py:128-134`
**Issue:** `validar_valor_de_parametro`, `PARAMETROS_CONHECIDOS` and `e_consumido_pelo_motor` are
imported only by `app/tests/test_parametros_registro.py`. `tolerancia_adequacao = 10` is therefore
still accepted by every write path today. The plan is honest about this (T-11-01 says "ligada aos
caminhos de escrita nos planos 04 e 05"), but the `must_haves` truth "`tolerancia_adequacao = 10`
(querendo 10%) é rejeitado" reads as a shipped guarantee and is not one yet — worth stating plainly
so UAT does not sign it off.
Also noted: `_coerce` is a private name imported across modules
(`registro.py:21`). It matches existing practice (`leitura_resiliente.py:12`) and the plan asks for
it, but the underscore now misrepresents the symbol's reach — consider promoting it to `coerce` with
a thin `_coerce` alias.

### IN-07: the disposition comment in the ingestão reader is now wrong

**File:** `app/modules/ingestao/infrastructure/databricks_reader.py:31` (adjacent, not in the review
scope, but falsified by this change)
**Issue:** `# INLINE (rápido); se algum mês passar de 25 MiB o cliente cai p/ EXTERNAL_LINKS.` — there
is no month filter (the module docstring and `CLAUDE.md` both say so), and `pedidos` now exceeds the
INLINE ceiling on *every* cycle, so the reader is unconditionally paying for a discarded ~25 MiB
INLINE download before the real one. A reader of this comment will conclude the INLINE path is the
normal case; it is now the never-successful case.
**Fix:** correct the comment, and consider passing `disposition="EXTERNAL_LINKS"` for
`ler_pedidos_em_aberto` so the wasted attempt (and CR-01's whole trigger) disappears for the one
query known to exceed the ceiling.

### IN-08: `tipo` comparison is case-sensitive with a confusing message

**File:** `app/modules/parametros/domain/registro.py:97-101`
**Issue:** The `tipo != conhecido.tipo` StrEnum comparison is correct (verified: `"float" !=
ParametroTipo.FLOAT` is `False`, and `f"{ParametroTipo.FLOAT}"` renders as `float`). But
`tipo="FLOAT"` produces `"tipo declarado é 'float', recebido 'FLOAT'."`, which reads like a
contradiction. The HTTP schemas coerce `tipo` through `ParametroTipo`, so this is only reachable via
`ChangeRequestCreate.proposed_payload` (free-form `dict[str, Any]`) — which plan 04 will feed
straight into this function.
**Fix:** normalise defensively (`tipo_normalizado = str(tipo).strip().lower()`) and compare that,
keeping the original in the message.

---

_Reviewed: 2026-08-18_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
