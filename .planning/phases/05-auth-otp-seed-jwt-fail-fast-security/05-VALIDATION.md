---
phase: 5
slug: auth-otp-seed-jwt-fail-fast-security
status: planned
nyquist_compliant: true
wave_0_complete: false
created: 2026-08-12
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3+ / pytest-asyncio 0.24+ (`asyncio_mode = "auto"`) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (`testpaths = ["app/tests"]`) |
| **Quick run command** | `uv run pytest app/tests/test_auth_flows.py -q` |
| **Full suite command** | `uv run pytest -q` |
| **Estimated runtime** | ~30-60s (full suite hits real isolated Postgres + Redis DB 15) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest app/tests/test_auth_flows.py -q`
- **After every plan wave:** Run `uv run pytest -q` (full suite — this phase touches shared `Settings` validation, which other modules' tests may implicitly depend on)
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 60s

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01 T1 | 05-01 | 1 | SEC-02 | T-05-05 | `seed_auth_on_startup` default `False`; PROD ainda falha se `True` | unit | `uv run pytest app/tests/test_settings_seed.py -q` | ➕ criado pela task | ⬜ pending |
| 05-01 T2 | 05-01 | 1 | SEC-04 | T-05-12 | Runbook cobre as 5 contas seed; guard quebra em drift | unit (doc guard) | `uv run pytest app/tests/test_docs_seguranca.py -q` | ➕ criado pela task | ⬜ pending |
| 05-02 T1 | 05-02 | 1 | SEC-05 | T-05-16 | Digest HMAC-SHA256 de 64 chars; comparação constant-time | unit | `uv run pytest app/tests/test_auth_otp_hash.py -q` | ➕ criado pela task | ⬜ pending |
| 05-02 T2 | 05-02 | 1 | SEC-05 | T-05-15 | Coluna `code` aceita 64 chars; head única `029` | CLI | `uv run alembic heads` | ✅ n/a | ⬜ pending |
| 05-02 T3 | 05-02 | 1 | SEC-05 | T-05-15 | Ciclo upgrade → insert 64 chars → downgrade → upgrade | integration (destrutivo) | `uv run pytest app/tests/test_migration_029_auth_otp_code_hash.py -q` | ➕ criado pela task | ⬜ pending |
| 05-03 T1 | 05-03 | 2 | SEC-03 | T-05-06 | Adapter SMTP monta e envia o e-mail do OTP; propaga falha | unit | `uv run pytest app/tests/test_auth_otp_email.py -q` | ➕ criado pela task | ⬜ pending |
| 05-03 T2 | 05-03 | 2 | SEC-03, SEC-05 | T-05-01, T-05-08, T-05-18 | Hash gravado; PROD envia e nunca loga o código; falha de SMTP não quebra o fluxo | integration | `uv run pytest app/tests/test_auth_otp_email.py -q` | ➕ criado pela task | ⬜ pending |
| 05-03 T3 | 05-03 | 2 | SEC-05 | T-05-01 | Fluxos de confirmação/recuperação verdes com OTP hasheado | integration | `uv run pytest app/tests/test_auth_flows.py -q` | ✅ existente (atualizado) | ⬜ pending |
| 05-04 T1 | 05-04 | 3 | SEC-06 | T-05-03 | Refresh token carrega `iat` em segundos | unit | `uv run pytest app/tests/test_auth_sessao_revogacao.py -q` | ➕ criado pela task | ⬜ pending |
| 05-04 T2 | 05-04 | 3 | SEC-06 | T-05-20, T-05-21, T-05-22 | Sign-out exige access token e grava o corte; refresh consulta; 503 se Redis cair no sign-out | integration | `uv run pytest app/tests/test_auth_flows.py -q` | ✅ existente (atualizado) | ⬜ pending |
| 05-04 T3 | 05-04 | 3 | SEC-06 | T-05-03 | Refresh anterior ao sign-out → 401; novo sign-in volta a funcionar | integration | `uv run pytest app/tests/test_auth_sessao_revogacao.py -q` | ➕ criado pela task | ⬜ pending |
| 05-05 T1 | 05-05 | 4 | SEC-07 | T-05-26 | Contadores de rate limit isolados por teste (sem flakiness) | infra de teste | `uv run pytest -q` | ✅ `conftest.py` (estendido) | ⬜ pending |
| 05-05 T2 | 05-05 | 4 | SEC-07 | T-05-02, T-05-04, T-05-09 | Gate usuário+IP nos 5 endpoints, antes da verificação de credencial | integration | `uv run pytest app/tests/test_auth_flows.py -q` | ✅ existente | ⬜ pending |
| 05-05 T3 | 05-05 | 4 | SEC-07 | T-05-02, T-05-04, T-05-07, T-05-24 | 429 com `Retry-After`; 503 fail-closed em OTP; 200 fail-open em sign-in | integration | `uv run pytest app/tests/test_auth_rate_limit.py -q` | ➕ criado pela task | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs preenchidos por `/gsd-plan-phase 5` em 2026-08-12. SEC-01 não aparece: já implementado fora desta fase (`settings.py:588-592`). Marcar ✅/❌ conforme cada plano executa.*

---

## Wave 0 Requirements

Todas as lacunas abaixo estão atribuídas a uma task concreta — nenhuma ficou implícita.

- [ ] Helper `ler_otp` lendo a coluna crua → **05-03 Task 3** (substituído por `otp_existe` + `ler_otp_do_log` via `caplog`)
- [ ] Teste do default de `seed_auth_on_startup` → **05-01 Task 1** (`app/tests/test_settings_seed.py`)
- [ ] Teste de entrega do OTP por e-mail em PROD (e ausência do código no log) → **05-03 Tasks 1 e 2** (`app/tests/test_auth_otp_email.py`)
- [ ] Teste de revogação de sessão (`iat` + refresh recusado após sign-out) → **05-04 Tasks 1 e 3** (`app/tests/test_auth_sessao_revogacao.py`)
- [ ] Teste de rate limit (429 com `Retry-After`, 503 fail-closed em OTP, fail-open em sign-in) → **05-05 Tasks 1 e 3** (`app/tests/conftest.py` + `app/tests/test_auth_rate_limit.py`)
- [ ] Migration + teste do alargamento de `auth_otp_challenges.code` → **05-02 Tasks 2 e 3** (`test_migration_029_auth_otp_code_hash.py`)

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Runbook content accuracy (seed user rotation/removal steps) | SEC-04 | Pure documentation — no code executes it | Review `docs/seguranca.md` against `app/modules/auth/bootstrap/seed.py::DEFAULT_AUTH_USERS` for accuracy; confirm linked from `docs/index.md` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** planos criados 2026-08-12; assinaturas marcadas conforme execução.
