---
phase: quick/260826-iln
plan: 01
subsystem: api
tags: [fastapi, openapi, pedidos, dead-code, endpoints-audit]

requires: []
provides:
  - Router de pedidos com 11 rotas (era 14) — 3 endpoints legados 410 Gone removidos
  - Guarda de regressão no teste de OpenAPI que falha se qualquer um dos 3 paths legados voltar
  - Inventário auditável das 40 rotas ativas cruzadas com o consumo do frontend (Grupos A-E)
affects: [pedidos, api, docs]

tech-stack:
  added: []
  patterns: []

key-files:
  created:
    - .planning/research/ENDPOINTS-AUDIT-2026-08-26.md
  modified:
    - app/modules/pedidos/infrastructure/http/routes.py
    - app/tests/test_pedidos_routes.py
    - app/tests/test_pedidos_processing_routes.py
    - docs/api.md
    - docs/adequacao.md
    - docs/arquitetura.md

key-decisions:
  - "Endpoints só-410 removidos sem período de aviso adicional (já eram Desativado/deprecated no OpenAPI, nenhum consumidor pode depender de 410)"
  - "Grupos B e C (sem consumidor achado no frontend) documentados como pendência de negócio, não removidos"
  - "Grupo D (parâmetros POST/PUT/DELETE) marcado como proibido de remover até a Fase 11 (v1.2) fechar"

patterns-established:
  - "Guarda de regressão de OpenAPI: assert de ausência de path, não de presença de resposta 410 — impede a rota voltar sob qualquer status code"

requirements-completed: [QUICK-260826-ILN]

duration: ~60min
completed: 2026-08-26
---

# Quick Task 260826-iln: Remover endpoints legados de pedidos em 410 Gone Summary

**Removidos os 3 endpoints de pedidos que só respondiam `410 Gone` (`POST /adequar`, `POST /sem_adequar`, `PUT /{nr_pedido}/alterar-grade`); registrado inventário auditável das outras 40 rotas do OpenAPI cruzadas com o consumo do frontend, em 5 grupos (A-E).**

## Performance

- **Duration:** ~60min
- **Started:** 2026-08-26T16:40:00Z (aprox., baseline pré-edição)
- **Completed:** 2026-08-26T17:29:05Z
- **Tasks:** 2/2
- **Files modified:** 7 (6 na Task 1 + 1 criado na Task 2)

## Accomplishments
- Router de pedidos reduzido de 14 para 11 rotas ativas; OpenAPI caiu de 43 para 40 paths, sem tocar nas 4 rotas ativas de nome parecido (`/produtos/aprovar`, `/{nr_pedido}/aprovar`, `/produtos/grades`, `/processamentos`)
- Guarda de regressão reescrita no teste de OpenAPI: em vez de assumir `responses == {"410"}`, agora assere que os 3 paths legados **não existem** — pega qualquer forma de retorno da rota, não só 410
- `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` criado com os 5 grupos (A resolvido, B e C pendentes, D proibido até a Fase 11, E drift de documentação), todo path citado verificado contra o OpenAPI real pós-Task 1

## Task Commits

Each task was committed atomically:

1. **Task 1: Remover os 3 endpoints legados 410 Gone (código, testes e docs)** - `02ee402` (refactor)
2. **Task 2: Registrar o inventário de rotas como documento de auditoria (Grupos A-E)** - `bce5252` (docs)

_Nenhuma tarefa TDD; ambas são commits únicos conforme o plano._

## Files Created/Modified
- `app/modules/pedidos/infrastructure/http/routes.py` - Remove os 3 handlers `adequar_pedidos`, `sem_adequar_pedidos`, `alterar_grade_legacy` (decorator+corpo completos); nenhum import ficou órfão (confirmado por contagem grep por símbolo)
- `app/tests/test_pedidos_routes.py` - Remove os 3 testes que só cobriam a lápide 410; reescreve a docstring do módulo dizendo que as rotas foram removidas em 2026-08-26
- `app/tests/test_pedidos_processing_routes.py` - Renomeia `test_openapi_declara_header_bounded_legacy_410_e_nao_oferece_retry` para `test_openapi_declara_header_bounded_e_nao_expoe_rotas_legadas`; troca as 2 assertions de `responses == {"410"}` por 3 assertions de ausência de path (`not in paths`)
- `docs/api.md` - Remove as 3 linhas de tabela dos endpoints "Desativado"
- `docs/adequacao.md` - Reescreve o parágrafo dos "antigos POST /adequar..." para dizer "foram removidos em 2026-08-26", preservando a justificativa (nenhum atalho síncrono seguro) e a menção a `POST /{nr}/aprovar`
- `docs/arquitetura.md` - Troca "retorna 410" por "foi removido"/"foram removidos" nas 2 menções encontradas (linha ~128 e linha ~162 — a segunda não estava nos "Fatos já apurados" do plano, mas era necessária para satisfazer o must_have "docs não descrevem mais os 3 paths como 410 Gone"; ver Deviations)
- `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md` - Novo. Grupos A (resolvido), B (ordens-reserva/aprovar-por-pedido/comunicações por id — decisão pendente), C (health/metrics/status/ingestão — plausível infra, não confirmado), D (parâmetros POST/PUT/DELETE — proibido até Fase 11), E (5 achados de drift de documentação em `.planning/codebase/`)

## Decisions Made
- Baseline da suíte capturada **antes** de qualquer edição de código (889 passed, 18 skipped, 0 failed), conforme Passo 0 do plano — ver Issues Encountered sobre a ordem em que isso foi feito nesta execução.
- Nenhum import ficou órfão em `routes.py`: contagem por grep confirmou que `Path`, `Query`, `Depends`, `HTTPException`, `status` e `require_actor` continuam usados por outros handlers do mesmo arquivo.
- `docs/arquitetura.md` teve uma segunda menção aos endpoints 410 (linha ~162, "Os endpoints síncronos `/adequar` e `/sem_adequar` retornam `410`") que não constava do inventário de "Fatos já apurados" do plano. Corrigida por Rule 1 (bug/inconsistência de documentação) para satisfazer o must_have explícito "docs/api.md, docs/adequacao.md e docs/arquitetura.md nao descrevem mais os 3 paths como 410 Gone".

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Segunda menção aos endpoints 410 em docs/arquitetura.md não coberta pelo plano**
- **Found during:** Task 1, Passo 5 (docs)
- **Issue:** O plano listava só a menção de `docs/arquitetura.md` nas linhas ~127-128, mas havia uma segunda frase na linha ~162 ("Os endpoints síncronos `/adequar` e `/sem_adequar` retornam `410`") que também precisava mudar para satisfazer o must_have do plano de que os 3 docs não descrevem mais os paths como 410 Gone.
- **Fix:** Trocado "retornam `410`" por "foram removidos" na linha 162, mantendo o resto da frase sobre não haver segundo write path global.
- **Files modified:** docs/arquitetura.md
- **Verification:** Grep manual confirmou que não sobra nenhuma menção a "410" associada aos 3 paths legados nos 3 arquivos de doc.
- **Committed in:** 02ee402 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - correção de doc não listada explicitamente no plano, mas exigida pelo must_have)
**Impact on plan:** Sem impacto de escopo — mesma direção do Passo 5 do plano, só um ponto a mais no mesmo arquivo já em `files_modified`.

## Issues Encountered
- **Ordem do Passo 0 invertida por engano:** apliquei a remoção dos 3 handlers em `routes.py` antes de capturar a baseline da suíte (o plano exige baseline primeiro). Corrigido restaurando o arquivo ao estado original (via Edit reverso, confirmado com `git diff --stat` vazio), capturando a baseline real (889 passed, 18 skipped, 0 failed) e só então reaplicando a remoção. A suíte pós-edição fechou em 886 passed, 18 skipped, 0 failed — exatamente 3 `passed` a menos, conforme o gate do plano.
- **`ruff check app` (comando exato do verify) não está limpo — mas por causa de um problema de ambiente pré-existente, não desta task.** O container `api` não monta `pyproject.toml` como volume (só `app/`, `alembic/`, e alguns diretórios read-only); a imagem foi construída antes do commit da quick task `260826-fer` (2026-08-26, mais cedo hoje) que adicionou a seção `[tool.ruff]` ao `pyproject.toml` (select E4/E7/E9/F/B/UP/I/SIM/C4 + `extend-immutable-calls` para `Depends`/`Query` do FastAPI). Rodar `ruff check app` dentro do container usa a config **default** do ruff (sem o `select` nem a exceção de `B008` para FastAPI), gerando 330 erros espalhados por praticamente todo o `app/` — nenhum deles introduzido por esta task. Confirmado escopo real: `ruff check --select E4,E7,E9,F,B,UP,I,SIM,C4 --ignore B008` (a config pretendida do pyproject) nos 3 arquivos editados (`routes.py`, `test_pedidos_routes.py`, `test_pedidos_processing_routes.py`) retorna "All checks passed!". Fora do escopo desta quick task corrigir a imagem Docker; registrado aqui para quem for revisar o verify literal do plano não se assustar com "Found 330 errors" — é drift de imagem, não regressão desta mudança.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Router de pedidos limpo: 3 endpoints mortos a menos, sem afetar nenhuma rota ativa.
- Documento de auditoria pronto para orientar decisões futuras sobre os Grupos B e C, e para lembrar que o Grupo D é bloqueado até a Fase 11 (v1.2) fechar.
- Pendência fora do escopo desta quick task, mas relevante para quem mexer em CI/qualidade: a imagem Docker de `api`/`celery_*` precisa ser reconstruída para refletir o `[tool.ruff]` do `pyproject.toml` adicionado por `260826-fer` — hoje `docker compose exec api ruff check app` roda com a config default do ruff, não com a do projeto.
- Os 3 arquivos não commitados de outra sessão (`app/modules/auth/infrastructure/http/routes.py`, `app/modules/auth/infrastructure/repositorio_otp.py`, `app/tests/test_docs_seguranca.py`) seguem intocados e modificados no working tree, fora dos 2 commits desta task.

---
*Phase: quick/260826-iln*
*Completed: 2026-08-26*

## Self-Check: PASSED

Todos os arquivos citados (`routes.py`, `test_pedidos_routes.py`, `test_pedidos_processing_routes.py`,
`docs/api.md`, `docs/adequacao.md`, `docs/arquitetura.md`, `.planning/research/ENDPOINTS-AUDIT-2026-08-26.md`,
este SUMMARY.md) existem em disco. Ambos os commits (`02ee402`, `bce5252`) confirmados em `git log`.
