---
quick_id: 260831-kic
status: complete
---

# Quick Task 260831-kic — SUMMARY

Fechados 3 achados do audit de segurança (`AUDIT-2026-08-07.md`), confirmados
ainda abertos em 2026-08-31:

1. **`/docs`, `/redoc`, `/openapi.json` sempre expostos** — `app/main.py`
   ganhou `_docs_urls(env)`, que zera os três campos quando `ENV=="PROD"`;
   fora de PROD mantém os defaults do FastAPI, sem mudança de comportamento.
2. **CORS com `allow_methods=["*"]`/`allow_headers=["*"]`** — trocados por
   listas explícitas: `["GET", "POST", "PUT", "DELETE", "OPTIONS"]` (únicos
   métodos usados pelas rotas do backend) e `["Authorization", "Content-Type"]`
   (únicos headers que o front — `lib/api/http-client.ts` — envia).
3. **`OBS-03` — sem fail-fast de `CORS_ORIGINS` em PROD** — novo bloco em
   `Settings.validate_cross_field_constraints` (mesmo `if ENV == "PROD":` do
   guard de `METRICS_API_KEY`): falha o boot se `cors_origins_list` estiver
   vazia ou toda origem resolver para `localhost`/`127.0.0.1`/`::1`.

## Arquivos

- `app/main.py` — `_docs_urls()` + CORS explícito
- `app/shared/config/settings.py` — fail-fast de CORS em PROD
- `app/tests/test_main_hardening.py` (novo) — 10 testes cobrindo os 3 pontos
- `app/tests/test_database_options.py`, `app/tests/test_metrics_guard.py` —
  ajustados para incluir `CORS_ORIGINS` real nos cenários "PROD sobe sem erro"
  que já existiam (quebravam com o novo fail-fast)
- `.secrets.baseline` — 1 entrada nova (`Basic Auth Credentials`, mesmo
  fake-credential já usado em `test_metrics_guard.py`, hash idêntico)

## Testes

Suíte completa: **898 passed, 18 skipped, 0 failed** (baseline 896 + 2 novos
efetivos — 10 testes novos em `test_main_hardening.py` menos os 2 que só
ganharam um `setenv` adicional nos arquivos existentes). Rodada via
`docker compose -f .docker/docker-compose.yml exec api uv run pytest`.

## Commit

`59cddc2` — `fix(security): fecha /docs em PROD e CORS methods/headers/origins explicitos (OBS-03)`

## Fora de escopo (quick tasks separadas)

- Sentry (OBS-01)
- `--forwarded-allow-ips=*` no `.docker/Dockerfile`
- Hardcode de parâmetros no front (`CONTRACT-01`)

## Nota operacional

Durante a execução, outra sessão Claude avisou que ia rodar
`git-filter-repo` + `force-push` no mesmo repositório (purga de segredos do
histórico, a pedido da mantenedora). Coordenei via mensagem cross-session:
segurei o commit até fechar a suíte verde, confirmei quando commitei local
(`59cddc2`, sem push) e liberei a outra sessão para prosseguir. Se o
histórico for reescrito, este commit pode precisar de rebase depois — conferir
`git log` antes de continuar as próximas quick tasks desta mesma sessão.
