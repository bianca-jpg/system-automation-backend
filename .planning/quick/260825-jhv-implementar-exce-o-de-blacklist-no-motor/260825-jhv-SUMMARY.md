---
quick_id: 260825-jhv
subsystem: pedidos (motor de adequação) + ingestao
tags: [alembic, postgres, sqlalchemy, motor-adequacao, databricks, pytest]

# Dependency graph
requires:
  - phase: 14 (motor de alocação puro)
    provides: adequar_grade_produto, aplicar_tudo_ou_nada, marcar_stand_by, ledger de orçamento
  - phase: 15 (roteamento global sem adequação)
    provides: _processar_pedidos_canal, load_pending_items (adapters.py) como caminho real do motor
  - phase: 16 (persistência do motivo de stand-by)
    provides: standby_motivo.py (vocabulário canônico), preteridos_motivo, migration 032
provides:
  - Migration 033 (indica_blacklist em pedidos/pedido_produto_read + ck_psm_motivo com 4 motivos)
  - BLACKLIST no vocabulário canônico de standby_motivo.py
  - Propagação indica_blacklist Databricks → agregação → snapshot → load_pending_items → motor
  - Despacho forçado tudo-ou-nada para pedidos blacklist, independente de modo=ADEQUAR
  - Cobertura de teste dos 5 cenários de negócio + regressão + migration destrutiva
affects: [Phase 17 (UI de stand-by, precisa tratar o motivo "blacklist" visualmente)]

tech-stack:
  added: []
  patterns:
    - "motivo_sem_estoque keyword-only em _commitar_par para desviar o motivo canônico sem duplicar a função de commit"
    - "blacklist_nrs computado uma vez por canal, igual ao padrão já existente de sem_credito_nrs"

key-files:
  created:
    - alembic/versions/033_pedido_indica_blacklist.py
    - app/tests/test_pedidos_motor_blacklist.py
    - app/tests/test_migration_033_pedido_indica_blacklist.py
  modified:
    - app/modules/pedidos/domain/standby_motivo.py
    - app/modules/ingestao/infrastructure/models.py
    - app/modules/ingestao/infrastructure/databricks_reader.py
    - app/modules/ingestao/domain/agregacao.py
    - app/modules/ingestao/infrastructure/repositorio_snapshot.py
    - app/modules/pedidos/processing/infrastructure/adapters.py
    - app/modules/pedidos/processing/infrastructure/repository.py
    - app/modules/pedidos/domain/motor_adequacao.py
    - app/tests/factories_pedidos_motor.py
    - app/tests/test_pedidos_motor.py
    - app/tests/test_pedidos_processing_repository.py
    - app/tests/test_ingestao_sync.py
    - app/tests/test_factories_pedidos_motor.py
    - app/tests/test_migration_020_pedido_produto_read.py
    - .planning/STATE.md (handoff textual, sem commit desta sessão)

key-decisions:
  - "load_pending_items (adapters.py) é o caminho real que alimenta o motor — pedido_produto_read é só uma projeção de leitura paralela, também atualizada por completude mas não consumida pelo motor"
  - "motivo_sem_estoque keyword-only em _commitar_par (default SEM_ESTOQUE) evita duplicar a lógica de commit só para trocar o motivo gravado no ramo blacklist"
  - "migration 020 (backfill histórico de pedido_produto_read) NÃO foi editada para incluir indica_blacklist, porque a coluna só existe a partir da migration 033 (posterior); reescrever o backfill de 020 quebraria um fresh 'alembic upgrade head' — confirmado manualmente contra banco vazio. O teste de drift (test_runtime_and_migration_backfill_sql_do_not_drift) foi atualizado para declarar essa divergência esperada explicitamente"

requirements-completed: []

# Metrics
duration: ~2h40min
completed: 2026-08-25
---

# Quick Task 260825-jhv: Exceção de blacklist no motor de adequação Summary

**Migration 033 + motor força tudo-ou-nada (motivo canônico `blacklist`) para clientes `indica_blacklist=SIM`, mesmo em `modo=ADEQUAR`, com propagação completa Databricks → ingestão → motor.**

## Performance

- **Duration:** ~2h40min
- **Started:** 2026-08-25T15:2x (aprox., primeira leitura de contexto)
- **Completed:** 2026-08-25T18:07:26Z
- **Tasks:** 5/5 completas
- **Files modified:** 15 arquivos de produção/teste + STATE.md (não commitado nesta sessão)

## Accomplishments
- Migration 033 aplicada e revertida com sucesso em ciclo destrutivo real contra Postgres (upgrade → downgrade → re-upgrade), incluindo um replay COMPLETO do zero (`alembic upgrade head` num banco vazio) para confirmar que a cadeia de 33 migrations não quebra.
- `indica_blacklist` propaga intacto do Databricks até o motor, nos dois caminhos de leitura (`load_pending_items` em `adapters.py`, caminho real do motor; e `pedido_produto_read`, projeção de leitura).
- Motor despacha QUALQUER pedido blacklist para o ramo tudo-ou-nada por produto, mesmo em `modo=ADEQUAR`, gravando o motivo canônico `blacklist` (nunca `furo_grade`/`sem_estoque`) quando falta estoque — provado nos 5 cenários de negócio (a-e) + regressão explícita para clientes não-blacklist.
- Suíte completa: **884 passed, 18 skipped, 0 failed** (rodada nativa Windows, fora de Docker — ver nota abaixo sobre a baseline documentada em STATE.md).

## Task Commits

Cada task foi commitada atomicamente:

1. **Task 1: Schema — vocabulário BLACKLIST + migration 033** - `a2284cf` (feat)
2. **Task 2: Ingestão — propagar indica_blacklist do Databricks até o snapshot** - `6396f5b` (feat)
3. **Task 3: Motor — despacho forçado tudo-ou-nada + wire do dado real (adapters.py)** - `56c00ec` (feat)
4. **Task 4: Testes — cenários de negócio, regressão, propagação e migration** - `b7f9932` (test)
5. **Task 5 (parte 1): fixes descobertos só ao rodar a suíte completa** - `c2e64a1` (test)

**Task 5 (verificação final):** sem commit de código — só verificação da suíte + nota de handoff textual em STATE.md (não commitada por esta sessão, conforme constraint do orquestrador).

_Nenhuma task usou TDD formal (tdd="true" só na Task 4, mas o "RED" foi o teste pré-existente `test_standby_motivo_valores_canonicos_e_distintos`, que já falhava sozinho assim que `BLACKLIST` entrou no frozenset na Task 1 — confirmado e documentado como esperado antes de corrigi-lo na Task 4)._

## Files Created/Modified

- `alembic/versions/033_pedido_indica_blacklist.py` - migration nova: `indica_blacklist` em `pedidos`/`pedido_produto_read` + `ck_psm_motivo` ampliada para 4 motivos
- `app/modules/pedidos/domain/standby_motivo.py` - `BLACKLIST = "blacklist"` no vocabulário canônico
- `app/modules/ingestao/infrastructure/models.py` - coluna `indica_blacklist` em `Pedido` e `PedidoProdutoRead`
- `app/modules/ingestao/infrastructure/databricks_reader.py` - SELECT de `ler_pedidos_em_aberto` inclui `indica_blacklist`
- `app/modules/ingestao/domain/agregacao.py` - `agregar_itens_pedidos` devolve `indica_blacklist` via `_parse_bool` existente
- `app/modules/ingestao/infrastructure/repositorio_snapshot.py` - CTE `products` do full refresh grava `indica_blacklist` via `bool_or`
- `app/modules/pedidos/processing/infrastructure/adapters.py` - `load_pending_items` (caminho real do motor) propaga `indica_blacklist`
- `app/modules/pedidos/processing/infrastructure/repository.py` - `_assert_plan_still_current` (re-verificação de staleness) também precisou incluir `indica_blacklist` para não quebrar o fingerprint de origem (achado durante Task 3, fora do file list original do plano)
- `app/modules/pedidos/domain/motor_adequacao.py` - `blacklist_nrs`, despacho forçado tudo-ou-nada, `_commitar_par` com `motivo_sem_estoque` keyword-only
- `app/tests/factories_pedidos_motor.py` - `item_pedido` ganha `indica_blacklist: bool = False`
- `app/tests/test_pedidos_motor.py` - `test_standby_motivo_valores_canonicos_e_distintos` atualizado para 4 motivos
- `app/tests/test_pedidos_motor_blacklist.py` (novo) - cenários (a)-(e) + regressão
- `app/tests/test_migration_033_pedido_indica_blacklist.py` (novo) - ciclo destrutivo upgrade/downgrade/re-upgrade
- `app/tests/test_pedidos_processing_repository.py` - teste de propagação + fixture `_source_item` atualizada
- `app/tests/test_ingestao_sync.py` - literal de SQL esperado atualizado (`test_sincronizar_pedidos_sql_exato`)
- `app/tests/test_factories_pedidos_motor.py` - `test_item_pedido_defaults` atualizado
- `app/tests/test_migration_020_pedido_produto_read.py` - `test_runtime_and_migration_backfill_sql_do_not_drift` passa a declarar a divergência esperada (ver Deviations)
- `.planning/STATE.md` - nota de handoff para a Fase 17 + linha na tabela de Quick Tasks Completed (não commitada nesta sessão — orquestrador cuida do commit de docs)

## Decisions Made

- **`pedido_produto_read` vs caminho real do motor:** confirmado por leitura de código que `adapters.py::load_pending_items` (não `pedido_produto_read`) é o que alimenta `processar_pedidos`. Propagar `indica_blacklist` para `pedido_produto_read` também (Task 2, item 3 do plano) é correto por completude da projeção de leitura, mas não é o que faz a exceção funcionar — isso é 100% `adapters.py` + `motor_adequacao.py` (Task 3).
- **`motivo_sem_estoque` keyword-only em `_commitar_par`:** em vez de duplicar a função de commit para o ramo blacklist, um parâmetro opcional com default `SEM_ESTOQUE` preserva o comportamento de todo call-site existente e permite ao ramo tudo-ou-nada/blacklist passar `BLACKLIST` explicitamente só quando `nr in blacklist_nrs`.
- **Migration 020 não foi editada para incluir `indica_blacklist`** apesar do padrão estabelecido no projeto de manter o backfill histórico de `pedido_produto_read` sincronizado byte-a-byte com o SQL de runtime (`repositorio_snapshot.py`) — ver Deviations, item 6, para o porquê.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Nome de arquivo de teste no plano não existe**
- **Found during:** Task 2 (verificação)
- **Issue:** O plano indicava `app/tests/test_ingestao_agregacao.py` como comando de verificação; esse arquivo não existe no repositório.
- **Fix:** Identificado que os testes de `agregar_itens_pedidos` vivem em `app/tests/test_ingestao_sync.py`; verificação rodada contra o arquivo real.
- **Files modified:** nenhum (só ajuste do comando de verificação)
- **Committed in:** N/A (não é mudança de código)

**2. [Rule 1 - Bug] Literal de SQL esperado desatualizado**
- **Found during:** Task 2
- **Issue:** `test_sincronizar_pedidos_sql_exato` compara a string EXATA do SELECT de `ler_pedidos_em_aberto`; adicionar `indica_blacklist` à query quebrou a igualdade.
- **Fix:** Literal esperado atualizado para incluir `, indica_blacklist`.
- **Files modified:** `app/tests/test_ingestao_sync.py`
- **Committed in:** `6396f5b`

**3. [Rule 1 - Bug] Re-verificação de staleness (`_assert_plan_still_current`) não incluía `indica_blacklist`**
- **Found during:** Task 3
- **Issue:** `repository.py::_assert_plan_still_current` tem uma query SQL própria (independente de `adapters.py`) que reconstrói o "estado atual" de um par pendente para comparar o fingerprint hash contra o gravado no plano persistido. Como essa query não incluía `indica_blacklist`, e o fingerprint agora É calculado sobre dicts que incluem essa chave (via `load_pending_items`), toda chamada de `apply_pairs` passou a falhar com `processing_plan_stale` — regressão real em 4 testes de `test_pedidos_processing_repository.py`.
- **Fix:** Adicionado `p.indica_blacklist` ao SELECT e ao dict reconstruído em `_assert_plan_still_current`.
- **Files modified:** `app/modules/pedidos/processing/infrastructure/repository.py` (fora do `<files>` da Task 3 no plano original, mas parte do mesmo caminho real de wiring)
- **Committed in:** `56c00ec`

**4. [Rule 1 - Bug] Fixture de teste `_source_item` sem `indica_blacklist`**
- **Found during:** Task 3
- **Issue:** `_source_item()` em `test_pedidos_processing_repository.py` constrói manualmente o dict "fonte" usado para calcular `sourceHash` em `PlannedPair` fabricados à mão; sem `indica_blacklist`, o hash não bate com o dict reconstruído pela query real (que agora inclui a chave, default `False`).
- **Fix:** Adicionado `"indica_blacklist": False` ao dict da fixture.
- **Files modified:** `app/tests/test_pedidos_processing_repository.py`
- **Committed in:** `56c00ec`

**5. [Rule 1 - Bug] `test_standby_motivo_valores_canonicos_e_distintos` (RED esperado, corrigido na Task 4)**
- **Found during:** Task 1 (falha esperada, mencionada no próprio plano)
- **Issue:** Teste pré-existente afirmava `MOTIVOS_VALIDOS == {FURO_GRADE, SEM_CREDITO, SEM_ESTOQUE}` (3 valores); passou a falhar sozinho assim que `BLACKLIST` entrou no frozenset.
- **Fix:** Atualizado para 4 valores, incluindo `BLACKLIST`.
- **Files modified:** `app/tests/test_pedidos_motor.py`
- **Committed in:** `b7f9932`

**6. [Rule 1 - Bug] `test_downgrade` da migration 033 falhava se a linha `blacklist` não fosse removida antes**
- **Found during:** Task 4 (validação manual da migration destrutiva contra Postgres real)
- **Issue:** `downgrade()` da migration 033 recria `ck_psm_motivo` restrita a 3 valores; se uma linha com `motivo='blacklist'` ainda existir na tabela, Postgres recusa a `ADD CONSTRAINT` (violação de dados existentes) — comportamento CORRETO de integridade, mas o teste não limpava a linha antes do downgrade.
- **Fix:** Teste passa a `DELETE FROM pedido_standby_motivo WHERE motivo = 'blacklist'` antes de chamar `downgrade`, documentando explicitamente por que isso é esperado.
- **Files modified:** `app/tests/test_migration_033_pedido_indica_blacklist.py`
- **Committed in:** `b7f9932`
- **Verificação extra:** teste rodado de fato (não só `pytest.skip`) contra um banco Postgres local real (`automation_migration_test`), incluindo o ciclo upgrade→downgrade→re-upgrade completo.

**7. [Rule 1 - Bug] `test_item_pedido_defaults` só quebrava na suíte completa**
- **Found during:** Task 5 (rodada da suíte completa)
- **Issue:** `item_pedido("M", 10, 500.0)` agora inclui `indica_blacklist: False` no dict; o teste de auto-verificação da factory (`test_factories_pedidos_motor.py`, arquivo DIFERENTE do `factories_pedidos_motor.py` alterado na Task 4) comparava contra um dict literal sem essa chave.
- **Fix:** Dict esperado atualizado para incluir `"indica_blacklist": False`.
- **Files modified:** `app/tests/test_factories_pedidos_motor.py`
- **Committed in:** `c2e64a1`

**8. [Rule 1 - Bug, com investigação arquitetural] Drift esperado entre migration 020 e `repositorio_snapshot.py`**
- **Found during:** Task 5 (rodada da suíte completa)
- **Issue:** `test_runtime_and_migration_backfill_sql_do_not_drift` exige igualdade byte-a-byte entre o backfill histórico da migration 020 (`_PENDING_BACKFILL_SQL`) e o SQL de runtime atual (`repositorio_snapshot._PENDING_READ_INSERT_SQL`) — um padrão do projeto que se sustentava porque toda coluna adicionada ao longo do tempo (`credito_bloqueado`, calculada inline; `indica_reserva`/`indica_embalado`, vindas de `pedidos_processados_erp` desde a migration 009) já existia como dado disponível quando a migration 020 rodou originalmente. `indica_blacklist` quebra essa premissa: só existe a partir da migration 033, POSTERIOR a 020.
- **Investigação:** tentei inicialmente editar a migration 020 para incluir `bool_or(p.indica_blacklist)`, espelhando o padrão estabelecido. **Confirmei manualmente contra um banco Postgres vazio** (`alembic upgrade head` do zero) que isso quebra um fresh install: a migration 020 executaria seu backfill referenciando `pedidos.indica_blacklist` ANTES da migration 033 criar essa coluna, falhando com `UndefinedColumnError`. Revertido.
- **Fix:** O teste de drift agora declara EXPLICITAMENTE a única divergência esperada (a fragmentação de `indica_blacklist`), documentando por que o backfill histórico de 020 legitimamente não inclui essa coluna (ela é preenchida corretamente na primeira sincronização/full-refresh que rodar após a migration 033, via `server_default=false` + o refresh real). Qualquer OUTRA divergência ainda faz o teste falhar.
- **Files modified:** `app/tests/test_migration_020_pedido_produto_read.py`
- **Committed in:** `c2e64a1`
- **Impacto:** nenhuma mudança de comportamento de produção; só corrige a premissa do teste para acomodar uma coluna genuinamente nova.

---

**Total deviations:** 8 auto-fixed (1 blocking/nome de arquivo, 7 bugs em testes pré-existentes ou fixtures — nenhum no motor de produção em si além do que já estava no escopo do plano).
**Impact on plan:** Todos os auto-fixes foram necessários para manter a suíte sem regressão. Nenhum scope creep de produção: os únicos arquivos de produção tocados fora do `<files>` original das tasks foram `repository.py` (mesmo caminho real de wiring da Task 3) e a migration 020 foi EXPLICITAMENTE revertida após se confirmar que a edição quebraria fresh installs.

## Issues Encountered

- **Incidente operacional (recuperado):** durante a investigação do item 8 acima, um script de limpeza executado para resetar um banco de teste DESCARTÁVEL (`automation_migration_test_fresh`) foi acidentalmente apontado para `system_automation_test` (o banco de teste real usado pela suíte), executando `DROP SCHEMA public CASCADE` nele. Detectado imediatamente pela explosão de falhas (162 failed / 112 errors) na rodada seguinte da suíte completa. Recuperado rodando `alembic upgrade head` do zero contra `system_automation_test` (que, como efeito colateral, serviu de segunda confirmação independente de que a cadeia completa de 33 migrations aplica limpo do zero). Suíte re-rodada e voltou a 884 passed / 18 skipped / 0 failed. Nenhum dado de produção foi afetado — só o banco de teste local, que não é fonte de verdade de nada.
- **Divergência de baseline documentada:** STATE.md documenta a baseline da suíte completa como **849 passed / 17 skipped / 1 failed**, medida historicamente via Docker. Esta sessão rodou **nativamente no Windows** (fora de Docker) e obteve **884 passed / 18 skipped / 0 failed** — mais testes passando (35 testes novos desta quick task) e ZERO falhas, incluindo a falha conhecida documentada (`test_pedidos_read_projection.py`), que **não reproduziu** neste ambiente nativo (confirmado isoladamente: 16/16 passed). Isso não é uma regressão — é uma melhora líquida (nenhuma falha nova, e a falha pré-existente simplesmente não se manifesta fora de Docker, provavelmente por diferença de timezone/locale entre os dois ambientes). `app/tests/test_pedidos_processing_sem_adequar_memory_024.py` não pode nem ser coletado neste ambiente (`import resource`, módulo exclusivo POSIX) — erro de coleta pré-existente e não relacionado a esta mudança, contornado com `--ignore` para conseguir rodar a suíte completa.

## User Setup Required

None - nenhuma configuração de serviço externo necessária. A migration 033 precisa ser aplicada manualmente em qualquer banco de teste/dev que ainda não tenha rodado `alembic upgrade head` (ver nota já existente em STATE.md sobre `conftest.py` não rodar alembic automaticamente).

## Next Phase Readiness

- Backend 100% pronto para a Fase 17 (UI de stand-by) tratar o motivo `blacklist` — ver handoff textual adicionado em `.planning/STATE.md`.
- Nenhum arquivo de `frontend` foi tocado nesta quick task.
- Migration 033 é a head atual (`033`); qualquer trabalho futuro que crie uma nova migration deve usar `down_revision = "033"`.

---
*Quick task: 260825-jhv*
*Completed: 2026-08-25*

## Self-Check: PASSED

- FOUND: `alembic/versions/033_pedido_indica_blacklist.py`
- FOUND: `app/tests/test_pedidos_motor_blacklist.py`
- FOUND: `app/tests/test_migration_033_pedido_indica_blacklist.py`
- FOUND: `.planning/quick/260825-jhv-implementar-exce-o-de-blacklist-no-motor/260825-jhv-SUMMARY.md`
- FOUND commit: `a2284cf`
- FOUND commit: `6396f5b`
- FOUND commit: `56c00ec`
- FOUND commit: `b7f9932`
- FOUND commit: `c2e64a1`
