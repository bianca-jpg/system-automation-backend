---
quick_id: 260827-ewg
subsystem: auth (rotação de refresh token)
tags: [redis, jwt, jose, auth, sec, pytest]

# Dependency graph
requires:
  - phase: 5 (auth-otp-seed-jwt-fail-fast-security)
    provides: corte de sign-out por usuário via Redis (auth:signed_out_since:{user_id}, SEC-06), jti dead field em create_refresh_token
provides:
  - Detecção de reuso de refresh token por jti (auth:refresh_jti:{user_id}) dentro de refresh_token, fail-open
  - Ramo de revogação por reuso que reaproveita o mesmo corte auth:signed_out_since:{user_id} que sign_out já grava
  - Cobertura de teste ponta a ponta dos 7 cenários (Redis real do banco 15 + TestClient)
affects: []

tech-stack:
  added: []
  patterns:
    - "Helper async com uma responsabilidade só (_jti_vigente / _registrar_jti_vigente / _revogar_sessao_por_reuso), cada um capturando (RedisError, OSError, TimeoutError) isoladamente — mesmo padrão já usado na leitura do corte de sign-out"
    - "TTL de chave nova como alias de constante existente (_REFRESH_JTI_TTL_SECONDS = _SIGNED_OUT_TTL_SECONDS) em vez de repetir o literal 7 * 86_400"
    - "jti obtido decodificando o próprio token recém-emitido (decode_refresh_token) em vez de mudar a assinatura de create_refresh_token/_auth_success, para não afetar sign-in/SSO/confirm"

key-files:
  created:
    - app/tests/test_auth_reuso_refresh_token.py
  modified:
    - app/modules/auth/application/casos_uso.py

key-decisions:
  - "Chave de jti é POR USUÁRIO (não por sessão/jti anterior), espelhando a decisão travada 2 do sign-out. Consequência aceita e documentada: duas sessões simultâneas do mesmo usuário que renovem alternadamente são lidas como reuso e ambas caem — comportamento pedido pela mantenedora no plano, não bug"
  - "Ausência de claim jti nunca é tratada como reuso (aceita mesmo havendo jti guardado), para não invalidar tokens emitidos antes deste deploy — forjar um token sem jti exigiria o JWT_SECRET, que já é o segredo raiz de toda a autenticação"
  - "Fail-open deliberado: RedisError/OSError/TimeoutError na leitura ou escrita do jti não derruba o refresh (200), mesma postura já aceita para o corte de sign-out. Enquanto o Redis estiver fora, a detecção de reuso simplesmente não opera"
  - "Ramo de reuso usa logger.exception (não RevogacaoIndisponivelError) ao falhar em gravar o corte, porque /token/refresh só mapeia SessaoInvalidaError e UsuarioNaoEncontradoError — qualquer outra exceção viraria 500 pelo handler global"

requirements-completed: []

# Metrics
duration: ~25min
completed: 2026-08-27
---

# Quick Task 260827-ewg: Implementar detecção de reuso de refresh token Summary

**Rotação de refresh token fecha com "rotation with reuse detection": reapresentar um token já rotacionado derruba a sessão inteira via o mesmo corte que o sign_out grava no Redis.**

## Performance

- **Duration:** ~25min
- **Tasks:** 3/3 completas (RED, GREEN, gate de regressão)
- **Files modified:** 2 arquivos (1 produção, 1 teste)

## Baseline e resultado final da suíte

- **Baseline (Task 1, medido antes de qualquer edição):** 917 passed, 18 skipped, 2 failed.
  Falhas pré-existentes, não relacionadas a esta task:
  - `app/tests/test_auth_flows.py::test_delete_user_preserva_change_request_zerando_requested_by`
  - `app/tests/test_schema_guard.py::test_colunas_do_model_existem_no_banco[parametro_change_requests]`
- **Final (Task 3):** 924 passed, 18 skipped, 2 failed — o mesmo conjunto de 2 falhas do baseline,
  sem nenhuma falha nova. `passed` cresceu em exatamente 7 (os testes novos deste arquivo).

## Accomplishments

- `app/modules/auth/application/casos_uso.py`: três helpers async novos
  (`_jti_vigente`, `_registrar_jti_vigente`, `_revogar_sessao_por_reuso`) e a checagem de reuso
  dentro de `refresh_token`, inserida **depois** do corte de sign-out por `iat` e do teto absoluto
  por `auth_time` (nenhum dos dois foi tocado, reordenado ou afrouxado).
- `app/tests/test_auth_reuso_refresh_token.py`: 7 testes cobrindo refresh legítimo grava jti,
  reapresentação de token rotacionado devolve 401, reuso revoga a sessão inteira (prova via leitura
  direta do Redis que reaproveita `auth:signed_out_since:{user_id}`), primeiro refresh sem jti
  guardado é aceito e passa a guardar, token sem claim `jti` nunca é reuso, fail-open com Redis
  indisponível, e isolamento entre usuários.
- `app/modules/auth/domain/tokens.py`, `app/modules/auth/infrastructure/http/routes.py` e todos os
  outros fluxos de auth (`sign_in`, `sign_in_microsoft`, `register`, `confirm_register`, recuperação
  de senha) permanecem intocados — confirmado por `git diff --stat` vazio contra os dois arquivos e
  por leitura antes/depois de `casos_uso.py`.

## Consequência aceita (documentada no código e aqui)

A chave de jti é **por usuário**, não por sessão. Duas sessões simultâneas do mesmo usuário (dois
navegadores, dois dispositivos) que renovem alternadamente serão lidas como reuso — as duas caem, o
usuário refaz o login. Isso é o desenho pedido pela mantenedora (espelha a decisão travada 2 do
sign-out, que já é por usuário), documentado em comentário WHY em `casos_uso.py` e no threat register
do plano (`T-EWG-03`, disposition `accept`).

## Fail-open do Redis

Enquanto o Redis estiver indisponível (`RedisError`/`OSError`/`TimeoutError` na leitura ou na escrita
do jti), a detecção de reuso simplesmente não opera — o refresh continua sendo aceito (200, sem 500).
Mesma postura já aceita para o corte de sign-out (`T-EWG-02`, disposition `accept`).

## Follow-ups / itens em aberto

1. **Documentação:** `docs/seguranca.md` ficou fora de escopo desta task porque duas quick tasks em
   voo (`260827-efy`, `260827-emo`) editam esse arquivo em paralelo. Registrar a detecção de reuso
   lá depois que ambas fecharem.
2. **Requirement formal:** não existe requirement `SEC-*` cobrindo rotação com detecção de reuso —
   `SEC-06` cobre só o sign-out. Vale avaliar abrir um requirement novo se isso virar escopo formal
   do v1.2 Platform Hardening.

## Task Commits

| Task | Commit | Descrição |
|------|--------|-----------|
| 1 (RED) | `03eea89` | `test(auth): cobertura RED da detecção de reuso de refresh token` — 298 linhas, 7 testes |
| 2 (GREEN) | `78443bd` | `feat(auth): detecta reuso de refresh token por jti e revoga a sessão` — 90 linhas adicionadas em `casos_uso.py` |
| 3 (gate) | — | Só verificação, sem commit de código; suíte completa 924 passed/18 skipped/2 failed |

## Verificação (verification do plano)

1. `pytest app/tests/test_auth_reuso_refresh_token.py -q` — 7 passed. ✅
2. `pytest app/tests/test_auth_sessao_revogacao.py app/tests/test_auth_flows.py app/tests/test_auth.py app/tests/test_auth_rate_limit.py -q` — verde (94 testes, 93 passed + 1 falha pré-existente não relacionada), sem uma linha editada nesses 4 arquivos. ✅
3. `ruff check app/` — os únicos achados nos dois arquivos tocados são `EXE002` (bit de execução sem shebang), pré-existente em 356 arquivos do repositório inteiro, não introduzido por esta task. Nenhum achado de conteúdo. ✅
4. `pytest -q` (suíte completa) — 924 passed (baseline 917 + 7), 18 skipped, mesmo conjunto de 2 failed do baseline. ✅
5. `git diff --stat HEAD~2 -- app/` (relativo aos dois commits desta task) lista exatamente `app/modules/auth/application/casos_uso.py` e `app/tests/test_auth_reuso_refresh_token.py` — confirmado via `git show --stat` em cada commit individualmente (outras sessões commitaram entre os dois commits desta task, então `HEAD~2` literal não isola mais os dois; a verificação foi feita por hash de commit). ✅
6. `git status -sb` — working tree seguiu compartilhado com outras sessões em voo (`app/tests/test_health.py` modificado e `.planning/quick/260827-f8o-.../` novo, de outra sessão) e intocado por esta task. ✅

## Self-Check: PASSED

- FOUND: `app/tests/test_auth_reuso_refresh_token.py`
- FOUND: `app/modules/auth/application/casos_uso.py`
- FOUND: commit `03eea89` (test RED)
- FOUND: commit `78443bd` (feat GREEN)
