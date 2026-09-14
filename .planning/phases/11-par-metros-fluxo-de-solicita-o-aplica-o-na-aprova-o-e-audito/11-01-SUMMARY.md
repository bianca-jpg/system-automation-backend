---
phase: 11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito
plan: 01
subsystem: parametros
tags: [domain, registro, validacao, parametros, motor-adequacao]

# Dependency graph
requires: []
provides:
  - "app/modules/parametros/domain/registro.py::ParametroConhecido / PARAMETROS_CONHECIDOS / parametro_conhecido / e_consumido_pelo_motor / validar_valor_de_parametro"
  - "app/modules/parametros/domain/exceptions.py::ValorDeParametroInvalidoError"
  - "Guarda de drift que lê o fonte de load_adequation_config e falha se motor e registro divergirem"
affects: [11-03, 11-04, 11-05]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Domínio puro no pacote parametros: apenas dataclasses/typing/re, sem AsyncSession/get_settings/fastapi (mesmo molde de coercao.py)"
    - "Guarda de drift lendo o fonte de outro módulo via Path(__file__).resolve().parents[2] + regex (mesmo padrão de test_pipeline_safety.py)"
    - "Validação de escrita separada da leitura resiliente por desenho, provada por teste de ausência (grep no fonte de leitura_resiliente.py)"

key-files:
  created:
    - app/modules/parametros/domain/registro.py
    - app/tests/test_parametros_registro.py
    - .planning/phases/11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito/deferred-items.md
  modified:
    - app/modules/parametros/domain/exceptions.py

key-decisions:
  - "validar_valor_de_parametro retorna silenciosamente (sem erro) para chaves fora do registro — o registro descreve o que o motor honra, não uma política geral de nomes, conforme <behavior> do plano"
  - "Faixa de tolerancia_adequacao é inclusiva em 0.0 e 1.0, e a mensagem de erro cita explicitamente 'fração, não percentual (0.05 = 5%)' para atacar diretamente o bug de digitar 10 querendo 10%"
  - "_coerce é reusado (não reimplementado) na validação, garantindo que a faixa é checada sobre o mesmo valor que a leitura resiliente produziria"
  - "Guarda de drift falha se o conjunto extraído do fonte do adapter estiver vazio, para não silenciosamente parar de medir nada"

requirements-completed: ["PARAM-04 (parcial — registro e validação; os pontos de aplicação/choke point ficam em 11-03/11-04/11-05)"]

# Metrics
duration: ~35min
completed: 2026-08-17
---

# Fase 11 Plano 01: Registro de parâmetros conhecidos pelo motor — Summary

**Registro puro (`domain/registro.py`) declarando as duas chaves que o motor de adequação honra hoje — `tolerancia_adequacao` (fração 0..1) e `criterio_selecao` ({valor, quantidade}) — com validação de escrita e uma guarda de drift que lê o fonte de `load_adequation_config` e falha se motor e registro divergirem.**

## Performance

- **Duration:** ~35 min
- **Tasks:** 2/2 completed
- **Files created:** 2 (+ 1 arquivo de acompanhamento de itens fora de escopo)
- **Files modified:** 1

## Accomplishments

- `app/modules/parametros/domain/registro.py` criado como módulo 100% puro (`dataclasses`, `typing`, e o import de `ParametroTipo` já usado por `coercao.py`) — `grep -cE "AsyncSession|get_settings|fastapi"` confirma 0 ocorrências
- `PARAMETROS_CONHECIDOS` declara exatamente `tolerancia_adequacao` (FLOAT, faixa 0.0–1.0 inclusiva) e `criterio_selecao` (STRING, `{"valor", "quantidade"}`), cada um com `aplicado_em` citando o ponto exato do motor que consome a chave
- `parametro_conhecido` / `e_consumido_pelo_motor` como lookups puros, sem levantar erro para chave desconhecida
- `validar_valor_de_parametro` (função de escrita) rejeita `tolerancia_adequacao=10` (o bug real relatado no `11-CONTEXT.md`: gravar 10 querendo 10% grava 1000%), aceita `0.10`, aceita os limites inclusivos `0` e `1`, rejeita tipo divergente e valor não conversível, rejeita `criterio_selecao` fora do conjunto fechado, e **ignora** chaves fora do registro
- `ValorDeParametroInvalidoError` adicionada a `domain/exceptions.py` no mesmo estilo das exceções existentes, carregando `chave` e `motivo` para os planos seguintes montarem `detail` de HTTP
- Guarda de drift (`test_registro_cobre_todas_as_chaves_lidas_pelo_motor`) lê o fonte real de `app/modules/pedidos/processing/infrastructure/adapters.py::load_adequation_config` via regex e assere igualdade de conjuntos contra `PARAMETROS_CONHECIDOS` — uma terceira chave lida pelo motor sem entrada no registro quebra a suíte
- Guarda de ausência (`test_validacao_nao_e_alcancavel_pela_leitura_resiliente`) lê o fonte de `leitura_resiliente.py` e assere que nem `validar_valor_de_parametro` nem `registro` aparecem lá — a propriedade central (`get_param_value` nunca propaga erro) permanece intocada e provada, não apenas assumida

## Task Commits

Each task was committed atomically:

1. **Task 1: Declarar o registro de chaves conhecidas em domain/registro.py** - `95499bb` (feat)
2. **Task 2: Validação de valor contra o registro (função pura) e a exceção de domínio** - `42290d8` (test)

**Plan metadata:** not committed to git per orchestrator instruction (`commit_docs` disabled for this run — `.planning/` left untracked/uncommitted; SUMMARY.md and `deferred-items.md` are on disk only)

## Files Created/Modified

- `app/modules/parametros/domain/registro.py` — `ParametroConhecido` (dataclass frozen/slots), `PARAMETROS_CONHECIDOS`, `parametro_conhecido`, `e_consumido_pelo_motor`, `validar_valor_de_parametro`; nenhuma dependência de banco/HTTP
- `app/modules/parametros/domain/exceptions.py` — `ValorDeParametroInvalidoError(ParametrosDomainError)` adicionada ao final, no estilo das classes existentes
- `app/tests/test_parametros_registro.py` — 21 testes coletados (lookups, tipo/faixa, `aplicado_em`, drift, `validar_valor_de_parametro` parametrizado em aceite/rejeição, exceção carrega `chave`/`motivo`, ausência na leitura resiliente)
- `.planning/phases/11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito/deferred-items.md` — registra dois achados de ambiente fora de escopo (ver "Issues Encountered")

## Decisions Made

- Reusar `_coerce` de `coercao.py` em vez de duplicar a lógica de conversão de tipo, garantindo que a validação de faixa opera sobre o mesmo valor que a leitura produziria
- Mensagem de erro de faixa cita explicitamente "fração, não percentual — 0.05 = 5%", diretamente acionável para o usuário que digitar `10` querendo `10%`
- Chave fora do registro é livre por desenho (retorno silencioso em `validar_valor_de_parametro`) — decisão travada no `11-CONTEXT.md` e no `<behavior>` do plano, não uma inferência minha
- Comentário curto acima de `validar_valor_de_parametro` documentando a restrição dura (T-11-03 do threat model): esta validação é do caminho de ESCRITA; `leitura_resiliente.py` não pode chamá-la

## Deviations from Plan

None in code — plan executed exatamente como escrito, ambas as tasks concluídas sem Rule 1/2/3/4.

Dois achados de ambiente, fora do escopo deste plano (domínio puro, sem banco), documentados em `deferred-items.md` e não corrigidos:

1. **[Fora de escopo] `uv run pytest` falha com `error: uv trampoline failed to canonicalize script path`** neste workstation (uv 0.11.15, Windows, caminho contendo `Área` não-ASCII). Workaround usado em toda a verificação: `./.venv/Scripts/python.exe -m pytest ...` — mesmo venv, mesma config de `pyproject.toml`, resultado equivalente.
2. **[Fora de escopo] Banco de teste local com `auth_users.user_name` ausente** apesar de `alembic current`/`alembic heads` reportarem `030 (head)`. Falha pré-existente (confirmada: `test_parametros.py` não importa nada de `registro.py`, e o conjunto de falhas é idêntico antes e depois das mudanças deste plano) em `app/modules/auth/`, não tocado por este plano. **Confirmado de forma independente pela suíte completa**: `test_schema_guard.py::test_colunas_do_model_existem_no_banco[auth_users]` — um teste já existente cujo único trabalho é detectar exatamente esse tipo de drift — falha por conta própria, e é a causa raiz de todas as 26 falhas + 43 erros da suíte completa (todos em arquivos que seedam `AuthUser`/token: `test_auth_flows.py`, `test_comunicacoes*.py`, `test_parametros.py`, `test_pedidos_routes.py`, `test_pedidos_read_projection.py`). Nenhum desses arquivos importa `registro.py` ou `ValorDeParametroInvalidoError`.

## Issues Encountered

- Ver `deferred-items.md` para os dois itens de ambiente acima (trampoline do `uv` e schema drift em `auth_users`) — nenhum bloqueou a execução das duas tasks, ambos contornados sem alterar código de produção.

## User Setup Required

None — nenhuma configuração externa necessária; nenhuma dependência nova (`dataclasses`, `typing`, `re` são stdlib).

## Next Phase Readiness

- `PARAMETROS_CONHECIDOS`, `e_consumido_pelo_motor` e `validar_valor_de_parametro` estão prontos para os três consumidores previstos: validação na escrita (planos 03/04/05), selo "ativo no motor" no `GET` (plano 03), e documentação viva
- `ValorDeParametroInvalidoError` está pronta para ser capturada nas rotas e convertida em `HTTPException` (planos 03/04/05)
- A guarda de drift e a guarda de ausência ficam permanentemente na suíte, protegendo os planos seguintes contra regressão silenciosa
- **Atenção para o plano 11-04/11-05 (ou qualquer plano que rode a suíte completa de auth):** o banco de teste local precisa ser corrigido (rebuild/remigração de `auth_users`) antes de confiar em "suíte completa verde" como critério de aceite — ver `deferred-items.md`. Confirmado independentemente pela suíte completa: `test_schema_guard.py::test_colunas_do_model_existem_no_banco[auth_users]` já falha por conta própria.

## Verification — real output

- `./.venv/Scripts/python.exe -m pytest app/tests/test_parametros_registro.py -q` → **21 passed** (task 1: 8, task 2: +13, todos coletados)
- `./.venv/Scripts/python.exe -m pytest app/tests/test_parametros.py -q` (pré-existente, comparação antes/depois) → idêntico nos dois momentos: **2 failed, 10 passed, 12 errors** — todos pela ausência de `auth_users.user_name` no banco local, nada relacionado a este plano
- `./.venv/Scripts/python.exe -m pytest app/tests/test_pedidos_motor.py -q` → incluído na rodada de `test_parametros.py`/`test_pedidos_motor.py`; `test_pedidos_motor.py` (sem dependência de `AuthUser`) permanece 100% verde
- `./.venv/Scripts/python.exe -m pytest -q` (suíte completa) → **26 failed, 685 passed, 16 skipped, 43 errors in 500.95s**; todas as 26+43 são a mesma causa raiz de schema drift em `auth_users` (confirmada por `test_schema_guard.py::test_colunas_do_model_existem_no_banco[auth_users]` falhando por conta própria), em arquivos que não têm relação de import com `registro.py`/`exceptions.py`
- `grep -cE "AsyncSession|get_settings|fastapi" app/modules/parametros/domain/registro.py` → **0**
- `grep -cE "validar_valor_de_parametro|registro" app/modules/parametros/domain/leitura_resiliente.py` → **0**
- `grep -c "class ValorDeParametroInvalidoError" app/modules/parametros/domain/exceptions.py` → **1**
- `grep -c "aplicado_em" app/modules/parametros/domain/registro.py` → **5** (≥3 exigido)

**Nota de ambiente:** `uv run pytest ...` falhou consistentemente com `error: uv trampoline failed to canonicalize script path` neste workstation (uv 0.11.15, caminho com `Área` não-ASCII). Toda a verificação acima foi feita com `./.venv/Scripts/python.exe -m pytest ...` — mesmo venv, mesma config (`pyproject.toml`), resultado equivalente. `uv --version` funciona normalmente; só `uv run` trampolina falha.

---
*Phase: 11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito*
*Completed: 2026-08-17*

## Self-Check: PASSED

- FOUND: app/modules/parametros/domain/registro.py
- FOUND: app/modules/parametros/domain/exceptions.py
- FOUND: app/tests/test_parametros_registro.py
- FOUND: .planning/phases/11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito/deferred-items.md
- FOUND: .planning/phases/11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito/11-01-SUMMARY.md
- FOUND commit: 95499bb (Task 1)
- FOUND commit: 42290d8 (Task 2)
