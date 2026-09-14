---
status: awaiting_human_verify
trigger: "Bug: sincronização de `pedidos` do Databricks falha com DatabricksLimitError (\"resultado truncado pelo provedor\") em app/shared/infrastructure/databricks_client.py. DATABRICKS_MAX_BYTES nunca estava setado no .env (usava default do código de 512MiB, acima do teto real de 25MiB da API do Databricks) — já corrigido no .env (DATABRICKS_MAX_BYTES=26214400) e containers reiniciados; `estoque` já sincroniza OK depois disso. Mas `pedidos` (451k linhas) ainda falha: em vez de retornar um estado de erro (que ativaria o fallback INLINE→EXTERNAL_LINKS já existente em `_execute_with_client`, linhas ~547-553), a Databricks responde com status SUCCEEDED e `manifest.truncated=true`. A função `_validate_manifest_budget` (linha ~383-396) trata isso como falha dura (`DatabricksLimitError`) sem nunca tentar EXTERNAL_LINKS. Precisa corrigir esse caminho para que truncated=true no INLINE dispare o retry em EXTERNAL_LINKS."
created: 2026-08-18T12:58:49Z
updated: 2026-08-18T13:35:00Z
---

## Current Focus

<!-- OVERWRITE on each update - always reflects NOW -->

hypothesis: `_validate_manifest_budget` (app/shared/infrastructure/databricks_client.py:383-396) raises `DatabricksLimitError` as soon as `manifest.truncated is True`, without ever checking `current_disposition == "INLINE"` first — so the existing INLINE→EXTERNAL_LINKS retry loop in `_execute_with_client` (lines ~528-554) never triggers for this failure shape, only for the (rarer) case where Databricks returns a terminal error state matched by `_is_inline_limit`.
test: Read `_execute_with_client` call order to confirm `_validate_manifest_budget` runs before the disposition-switch branch has any chance to see this payload (i.e. confirm the truncated-on-success path bypasses lines 547-553 entirely because `terminal_state` is SUCCEEDED, not in `_ESTADOS_ERRO`).
expecting: if confirmed, fix is to make `_validate_manifest_budget` (or its caller) treat `manifest.truncated=True` while `current_disposition == "INLINE"` as a signal to retry with EXTERNAL_LINKS instead of raising — mirroring the existing `_is_inline_limit` retry branch — and only raise `DatabricksLimitError` if still truncated after EXTERNAL_LINKS (or if disposition was already EXTERNAL_LINKS).
next_action: Human action completed by orchestrator: `.env` DATABRICKS_MAX_BYTES raised to 200000000, containers recreated (docker compose up -d --force-recreate api celery_worker celery_beat celery_orders_worker), and `ler_pedidos_em_aberto()` + `ler_estoque()` re-run for real end-to-end against Databricks — both now succeed (pedidos: 448825 rows, retry log "Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS." confirmed; estoque: 25415 rows). Awaiting explicit user confirmation before moving this session to resolved/ and before committing the code changes.
reasoning_checkpoint:
  hypothesis: "Layer 1 (RESOLVED): `_validate_manifest_budget` raised DatabricksLimitError immediately on manifest.truncated=True with zero awareness of current_disposition, so the INLINE->EXTERNAL_LINKS retry never triggered for the SUCCEEDED-but-truncated shape. FIXED and CONFIRMED (see Evidence @13:10 — retry log line now appears). Layer 2 (current): `_submit` sends `byte_limit=self._config.max_bytes` (26_214_400, chosen specifically to satisfy Databricks' INLINE-only hard ceiling) unconditionally for BOTH INLINE and EXTERNAL_LINKS requests. Since EXTERNAL_LINKS supports up to 100 GiB per Databricks docs, sending the tiny INLINE-sized byte_limit on the EXTERNAL_LINKS retry causes Databricks to truncate that attempt too — pedidos' real EXTERNAL_LINKS payload is ~64 MiB, comfortably under 100 GiB but over 25 MiB."
  confirming_evidence:
    - "Evidence @13:10: after layer-1 fix, retry to EXTERNAL_LINKS demonstrably fires (WARNING log observed) but still raises DatabricksLimitError('truncado') on the second attempt — proves the remaining failure is not a control-flow gap anymore, it's a budget/config value problem."
    - "Web research (Evidence @13:15): Databricks docs confirm byte_limit's 25 MiB-class ceiling is INLINE-specific; EXTERNAL_LINKS is limited to 100 GiB and defaults to 100 GiB when byte_limit is omitted."
    - "Evidence @13:20: raw httpx probe with byte_limit=400_000_000 and disposition=EXTERNAL_LINKS against the real pedidos query returns truncated=False, total_row_count=448825, total_byte_count=67123163 (~64 MiB) — and a full DatabricksClient run with max_bytes=200_000_000 + disposition=EXTERNAL_LINKS returns all 448825 rows successfully end-to-end."
  falsification_test: "If, after clamping INLINE's byte_limit to 26_214_400 and sending the full config.max_bytes (200_000_000) for EXTERNAL_LINKS, ler_pedidos_em_aberto() still raises DatabricksLimitError('truncado') on the EXTERNAL_LINKS attempt, this hypothesis is wrong (would mean pedidos' real payload has grown past 200 MiB or another constraint like max_chunks/max_rows is binding)."
  fix_rationale: "Root cause is a single config value (max_bytes) serving two conflicting purposes: the Databricks-enforced INLINE wire ceiling (hard-capped externally at 26_214_400) and the app's real total-data budget (needs to cover pedidos' actual ~64 MiB EXTERNAL_LINKS payload). Splitting the byte_limit sent per-disposition (clamped for INLINE, unclamped for EXTERNAL_LINKS) resolves the conflict without weakening any existing safety invariant: the local `_ResponseBudget.max_bytes` (raised to 200_000_000) remains the real ceiling on downloaded bytes for both dispositions, and the original 'reject rather than accept partial snapshot' guarantee (see code comment at _submit) is preserved for INLINE."
  blind_spots: "Chose 200_000_000 (~3x headroom over the observed 67 MiB) somewhat heuristically rather than from a documented growth projection — if pedidos volume triples, this ceiling would need revisiting. Have not yet confirmed max_rows (1_500_000) and max_chunks (2_048 wired via settings, presumably higher via get_settings()) comfortably cover 448825 rows / 7 chunks — evidence @13:20 shows 7 chunks and 448825 rows, both far under configured ceilings, so this is low risk but not exhaustively tested against future growth."
tdd_checkpoint: null

## Symptoms

<!-- Written during gathering, then immutable -->

expected: `sincronizar_databricks` deveria trazer todas as ~451 mil linhas de `pedidos` do Databricks a cada ciclo (full refresh), caindo automaticamente para disposition EXTERNAL_LINKS quando o resultado não cabe no limite do modo INLINE (25 MiB) — esse fallback já existe no código para o caso de erro reportado pela Databricks.
actual: A chamada falha com `DatabricksLimitError("dados; resultado truncado pelo provedor")`, levantada em `_validate_manifest_budget`, e a sincronização de pedidos nunca completa — a tabela `pedidos` fica parada em snapshot antigo (synced_at estava travado em 2026-08-05, 13 dias atrás, quando isso foi descoberto).
errors: |
  DatabricksLimitError: A resposta excedeu o limite configurado de dados; resultado truncado pelo provedor.
  (reproduzido chamando app.modules.ingestao.infrastructure.databricks_reader.ler_pedidos_em_aberto() diretamente dentro do container system_automation_celery_worker)
  Anteriormente (antes da correção do DATABRICKS_MAX_BYTES) o erro era outro: HTTP 400 databricks_http_rejected / INVALID_PARAMETER_VALUE "The byte_limit field must be in the range [1, 26214400]." — já resolvido, não confundir os dois.
reproduction: |
  1. No container system_automation_celery_worker (docker exec), rodar:
     python3 -c "import asyncio; from app.modules.ingestao.infrastructure.databricks_reader import ler_pedidos_em_aberto; asyncio.run(ler_pedidos_em_aberto())"
  2. Falha imediatamente com DatabricksLimitError, sem nenhum log de "Resultado INLINE excedeu o limite; repetindo em EXTERNAL_LINKS." (confirmado via logging.DEBUG) — ou seja, o fallback para EXTERNAL_LINKS nunca chega a ser tentado.
  `ler_estoque()` (25415 linhas, tabela menor) funciona normalmente com o mesmo DATABRICKS_MAX_BYTES=26214400, então o teto em si está correto — o problema é específico ao volume de `pedidos`.
started: Não é possível determinar precisamente quando isso começou a acontecer para `pedidos` porque estava mascarado por um bug anterior (DATABRICKS_MAX_BYTES nunca configurado, causando um HTTP 400 diferente desde pelo menos 2026-08-12). É possível que esse caminho de fallback nunca tenha funcionado corretamente para volumes deste tamanho.

## Eliminated

<!-- APPEND only - prevents re-investigating after /clear -->

- hypothesis: Credencial/token do Databricks expirado ou revogado (erro 401/403)
  evidence: O erro real era HTTP 400 (Bad Request) com error_code=INVALID_PARAMETER_VALUE, não 401/403. Token, host e warehouse_id funcionam normalmente para outras queries (estoque OK).
  timestamp: 2026-08-18T12:20:00Z

- hypothesis: A view `system_automation_pedidos_em_aberto` foi renomeada/alterada no Databricks, causando erro de SQL
  evidence: Reproduzindo a mesma query manualmente via httpx sem o campo `byte_limit`, a chamada retornou 200 OK / PENDING normalmente — a query em si é válida.
  timestamp: 2026-08-18T12:15:00Z

- hypothesis: DATABRICKS_MAX_BYTES estava configurado com um valor razoável e o problema é só de rede/timeout
  evidence: DATABRICKS_MAX_BYTES nunca aparecia no .env — get_settings() usava o default do Pydantic (536_870_912 = 512 MiB), que excede o teto real documentado pela própria API do Databricks (26_214_400 bytes = 25 MiB), confirmado pela mensagem de erro literal da API.
  timestamp: 2026-08-18T12:22:00Z

## Evidence

<!-- APPEND only - facts discovered during investigation -->

- timestamp: 2026-08-18T11:30:00Z
  checked: durable_jobs (tabela) filtrando kind='ingestion.full_sync.v1'
  found: job falha repetidamente desde pelo menos 2026-08-12 com error_code=databricks_http_rejected, rodando a cada ~2h via Celery beat (task sincronizar-databricks configurada e disparando normalmente)
  implication: o full refresh de pedidos/estoque está parado há dias; a última sincronização bem-sucedida (synced_at) ficou em 2026-08-05

- timestamp: 2026-08-18T12:20:00Z
  checked: log do celery worker no momento exato da falha (traceback completo)
  found: "HTTP Request: POST .../api/2.0/sql/statements 'HTTP/1.1 400 Bad Request'" seguido de "DatabricksHTTPError('O Databricks rejeitou a requisicao.')" — o databricks_client.py não loga o corpo da resposta de erro, só o status code
  implication: para achar a causa real do 400 foi preciso reproduzir a chamada manualmente com httpx cru dentro do container e capturar response.json()

- timestamp: 2026-08-18T12:25:00Z
  checked: resposta manual da API do Databricks reintroduzindo byte_limit=536870912
  found: 'error_code: INVALID_PARAMETER_VALUE, message: "The byte_limit field must be in the range [1, 26214400]."'
  implication: causa raiz do HTTP 400 original confirmada — DATABRICKS_MAX_BYTES (nunca setado no .env, usando default de 512MiB do código) excede o teto real da API (25 MiB)

- timestamp: 2026-08-18T12:45:00Z
  checked: settings.py — Field(default=536_870_912, ge=1_048_576, le=1_073_741_824, validation_alias="DATABRICKS_MAX_BYTES")
  found: o range de validação do Pydantic (até 1 GiB) é bem mais permissivo que o teto real da API do Databricks (25 MiB) — nada no código impede configurar um valor inválido
  implication: possível causa raiz secundária / melhoria: o Field deveria ter le=26_214_400 para falhar cedo (na leitura de settings) em vez de só na chamada HTTP

- timestamp: 2026-08-18T12:50:00Z
  checked: .env corrigido (DATABRICKS_MAX_BYTES=26214400 anexado), containers recriados via docker compose up -d --force-recreate, chamada real a ler_pedidos_em_aberto() e ler_estoque()
  found: estoque agora funciona (25415 linhas OK); pedidos ainda falha, mas com erro NOVO — DatabricksLimitError "resultado truncado pelo provedor" (antes era HTTP 400)
  implication: o bug do byte_limit estava mascarando este segundo bug, específico ao volume de pedidos (451k linhas >> 25 MiB)

- timestamp: 2026-08-18T12:55:00Z
  checked: log em nível DEBUG do logger app.shared.infrastructure.databricks_client durante a chamada de ler_pedidos_em_aberto()
  found: nenhuma linha "Resultado INLINE excedeu o limite; repetindo em EXTERNAL_LINKS." aparece — a exceção sai direto de _validate_manifest_budget sem passar pelo branch de retry (linhas 547-553 de _execute_with_client)
  implication: a Databricks está retornando terminal_state=SUCCEEDED com manifest.truncated=true (não um estado de erro), e esse caminho de sucesso-mas-truncado não é coberto pela lógica de fallback existente, que só reage a estados de erro reconhecidos por _is_inline_limit

- timestamp: 2026-08-18T13:00:00Z
  checked: app/shared/infrastructure/databricks_client.py lines 383-575 in full (`_validate_manifest_budget`, `_execute_with_client`, `_is_inline_limit`)
  found: confirmed control-flow gap exactly as hypothesized — `_validate_manifest_budget(payload, budget)` (line 556) runs unconditionally right after the `terminal_state in _ESTADOS_ERRO` branch (lines 547-554), with no check of `current_disposition` before it. Also found `app/tests/test_databricks_client.py::test_rejeita_manifesto_truncado_e_total_de_linhas_oversize` (lines 430-445) which submits an INLINE (default disposition) query with `manifest.truncated=True` and asserts an immediate `DatabricksLimitError(match="truncado")` with zero retry — the existing test suite codifies the exact bug as expected behavior.
  implication: root cause (layer 1, control flow) confirmed via structured reasoning checkpoint. Fix: add `_manifest_is_truncated` predicate + INLINE-truncated retry branch mirroring `_is_inline_limit`, and rewrite the codified-bug test into three tests reflecting corrected behavior (retry succeeds / still-truncated-after-retry raises / oversize row count still raises).

- timestamp: 2026-08-18T13:10:00Z
  checked: applied layer-1 fix (databricks_client.py `_manifest_is_truncated` + retry branch), restarted system_automation_celery_worker (bind-mounted source at /workspace/app), re-ran `ler_pedidos_em_aberto()` against real Databricks with DEBUG logging
  found: the retry now DOES trigger — log line "Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS." appears (confirms layer-1 fix works as designed) — but the EXTERNAL_LINKS retry ALSO comes back with manifest.truncated=true, so `_validate_manifest_budget` still raises `DatabricksLimitError` on the second attempt.
  implication: layer-1 fix is correct and necessary but not sufficient. There is a SECOND, distinct root cause: `_submit` (line ~605) sends `"byte_limit": self._config.max_bytes` unconditionally for BOTH dispositions. DATABRICKS_MAX_BYTES=26214400 (25 MiB) was deliberately chosen to satisfy Databricks' INLINE-specific byte_limit ceiling (confirmed earlier via the literal "must be in the range [1, 26214400]" error) — but that same 25 MiB value is now also being sent as byte_limit on the EXTERNAL_LINKS retry, causing Databricks to truncate EXTERNAL_LINKS too, even though EXTERNAL_LINKS supports far larger results.

- timestamp: 2026-08-18T13:15:00Z
  checked: web research — Databricks Statement Execution API docs (byte_limit / EXTERNAL_LINKS disposition semantics)
  found: byte_limit applies to total result size for both dispositions, but EXTERNAL_LINKS is limited to 100 GiB and defaults to a 100 GiB byte_limit when the field is omitted — i.e. the 25 MiB ceiling is INLINE-specific, not a universal Databricks constraint.
  implication: confirms the fix direction — byte_limit sent to Databricks must be computed per-disposition (clamped to 26_214_400 for INLINE, unclamped up to config.max_bytes for EXTERNAL_LINKS), not a single shared value.

- timestamp: 2026-08-18T13:20:00Z
  checked: probed real Databricks directly (raw httpx, EXTERNAL_LINKS, byte_limit=400_000_000) to read the manifest without the client's local budget interfering
  found: state=SUCCEEDED, truncated=False, total_row_count=448825, total_byte_count=67123163 (~64 MiB), total_chunk_count=7. Also verified end-to-end: calling `DatabricksClient(config_with_max_bytes=200_000_000).execute(statement, disposition="EXTERNAL_LINKS")` for the real pedidos query returns 448825 rows successfully.
  implication: pedidos' real EXTERNAL_LINKS payload is ~64 MiB. A max_bytes of 200_000_000 (200 MiB, ~3x headroom) comfortably covers current volume and is well under the DatabricksClientConfig/Settings 1 GiB validation ceiling and Databricks' 100 GiB EXTERNAL_LINKS ceiling.

## Resolution

<!-- OVERWRITE as understanding evolves -->

root_cause: |
  Two layered bugs in app/shared/infrastructure/databricks_client.py:
  (1) Control flow: `_validate_manifest_budget` raised `DatabricksLimitError` immediately whenever
      `manifest.truncated is True`, with no check of `current_disposition` first. Since Databricks
      returns terminal_state=SUCCEEDED (not an error state) for a truncated-but-"successful" INLINE
      result, the existing INLINE->EXTERNAL_LINKS retry branch (gated on `terminal_state in _ESTADOS_ERRO`)
      never triggered for this shape — only the rarer explicit-error-state case was covered.
  (2) Config conflation: `_submit` sent `byte_limit=self._config.max_bytes` unconditionally for both
      dispositions. DATABRICKS_MAX_BYTES=26214400 (25 MiB) was chosen specifically to satisfy Databricks'
      INLINE-only hard byte_limit ceiling, but that same tiny value was then also sent as byte_limit on
      the EXTERNAL_LINKS retry — even though EXTERNAL_LINKS supports up to 100 GiB per Databricks docs and
      pedidos' real EXTERNAL_LINKS payload is ~64 MiB (448,825 rows). So even after fixing (1), the retry
      attempt itself also came back truncated.
fix: |
  (1) Added `_manifest_is_truncated(payload)` predicate and a retry branch in `_execute_with_client`
      (current_disposition == "INLINE" and truncated -> switch to EXTERNAL_LINKS and retry), inserted
      between the `_ESTADOS_ERRO` branch and the `_validate_manifest_budget` call, mirroring the existing
      `_is_inline_limit` retry branch.
  (2) Made `_submit`'s `byte_limit` disposition-aware via new `_INLINE_BYTE_LIMIT_CEILING = 26_214_400`
      constant: INLINE requests clamp to `min(config.max_bytes, 26_214_400)`; EXTERNAL_LINKS requests send
      `config.max_bytes` uncapped.
  (3) PENDING (human action required): raise `DATABRICKS_MAX_BYTES` in `.env` from 26214400 to 200000000
      (verified sufficient: real payload is ~64 MiB, ~3x headroom, still within Settings/DatabricksClientConfig's
      1 GiB validation ceiling) so the local `_ResponseBudget.max_bytes` — shared across both dispositions —
      can actually hold pedidos' EXTERNAL_LINKS payload. Without this env change, fixes (1) and (2) alone are
      inert because config.max_bytes is still 26214400, identical to the INLINE ceiling.
verification: |
  Layer 1 confirmed live against real Databricks (system_automation_celery_worker, bind-mounted source, container
  restarted): retry log line "Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS." now
  appears where it never did before.
  Layer 2 confirmed via: (a) web research on Databricks byte_limit/EXTERNAL_LINKS semantics; (b) raw httpx
  probe (bypassing the client) with byte_limit=400_000_000 + disposition=EXTERNAL_LINKS against the real
  pedidos query — truncated=False, total_row_count=448825, total_byte_count=67123163; (c) full
  DatabricksClient run with max_bytes=200_000_000 + disposition=EXTERNAL_LINKS — returns all 448825 rows.
  Unit tests: 44/44 pass in app/tests/test_databricks_client.py (3 new/rewritten tests for the truncated-INLINE
  retry behavior + 1 new test for the disposition-aware byte_limit clamp); 107/107 pass across
  test_databricks_client.py + test_ingestao_sync.py + test_ingestao_worker.py; full suite 755 passed / 16
  skipped / 1 failed (test_pedidos_read_projection.py::test_read_projection_empty_contracts_execute_real_sql,
  confirmed pre-existing and unrelated — fails identically in isolation due to leftover rows in the
  system_automation_test database, not touched by this fix).
  DONE (2026-08-18T13:35:00Z, orchestrator): applied `.env` change (DATABRICKS_MAX_BYTES 26214400 -> 200000000),
  recreated api/celery_worker/celery_beat/celery_orders_worker containers, re-ran `ler_pedidos_em_aberto()` and
  `ler_estoque()` for real inside system_automation_celery_worker. Output:
    max_bytes agora: 200000000
    Resultado INLINE truncado pelo provedor; repetindo em EXTERNAL_LINKS.
    pedidos OK 448825 linhas
    estoque OK 25415 linhas
  Both readers now return full data end-to-end against the real Databricks warehouse. Awaiting user confirmation
  to close this session and to decide whether/how to commit the code + .env changes.
files_changed:
  - app/shared/infrastructure/databricks_client.py
  - app/tests/test_databricks_client.py
  - .env (DATABRICKS_MAX_BYTES: 26214400 -> 200000000) — applied and verified by orchestrator
