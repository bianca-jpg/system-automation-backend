# Phase 5 — Wave map

Gerado por `/gsd-plan-phase 5` em 2026-08-12. Referência rápida para `/gsd-execute-phase 5`.

| Wave | Planos | Paralelo? | Requisitos | Bloqueio |
|------|--------|-----------|------------|----------|
| 1 | `05-01` (seed default + runbook), `05-02` (otp_hash + migration 029) | Sim — conjuntos de arquivos disjuntos | SEC-02, SEC-04, SEC-05 (fundação) | `05-02` Task 2 é **[BLOCKING]**: a migration precisa estar aplicada e commitada antes de qualquer gravação de hash |
| 2 | `05-03` (hash em repouso + entrega do OTP por e-mail) | Sozinho | SEC-03, SEC-05 | Depende de `05-02` (coluna `varchar(64)`) |
| 3 | `05-04` (revogação de sign-out) | Sozinho | SEC-06 | Depende de `05-03` — ambos editam `app/tests/test_auth_flows.py` |
| 4 | `05-05` (rate limit) | Sozinho | SEC-07 | Depende de `05-04` — ambos editam `app/modules/auth/infrastructure/http/routes.py` |

## Por que 2, 3 e 4 não paralelizam

Não é sequenciamento defensivo: é propriedade de arquivo compartilhado.

- `app/tests/test_auth_flows.py` é editado por `05-03` (helper `ler_otp`) e `05-04` (teste de sign-out autenticado).
- `app/modules/auth/infrastructure/http/routes.py` é editado por `05-04` (dependência `get_current_user`) e `05-05` (gate de rate limit).
- `app/modules/auth/infrastructure/repositorio_otp.py` grava digest de 64 chars — impossível antes da migration do wave 1.

## SEC-01

Fora de escopo: já implementado em `app/shared/config/settings.py:588-592`. Nenhum plano o toca.

## Cobertura das lacunas Wave 0 de `05-VALIDATION.md`

| Lacuna | Onde fecha |
|--------|-----------|
| Helper `ler_otp()` lendo a coluna crua | `05-03` Task 3 |
| Teste do default de `seed_auth_on_startup` | `05-01` Task 1 |
| Teste de entrega de OTP por e-mail em PROD | `05-03` Tasks 1 e 2 |
| Teste de sign-out/refresh revogado | `05-04` Tasks 1 e 3 |
| Teste de rate limit (429 / 503 / fail-open) | `05-05` Tasks 1 e 3 |
| Teste da migration que alarga a coluna | `05-02` Task 3 |
