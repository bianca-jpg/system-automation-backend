---
quick_id: 260831-kic
status: planned
---

# Quick Task 260831-kic: Fechar hardening de CORS/docs (OBS-03)

## Contexto

Achados do audit de segurança (`AUDIT-2026-08-07.md`) ainda abertos, confirmados
direto no código em 2026-08-31:

1. `GET /docs`, `/redoc`, `/openapi.json` sempre expostos — `app/main.py` não
   passa `docs_url`/`redoc_url`/`openapi_url` para o `FastAPI(...)`.
2. `CORSMiddleware` usa `allow_methods=["*"]` / `allow_headers=["*"]`.
3. `CORS_ORIGINS` (`OBS-03`) não tem fail-fast em PROD — hoje um boot em PROD
   com `CORS_ORIGINS=http://localhost:3000` sobe sem erro.

De fora de escopo (quick tasks separadas): Sentry (OBS-01) e
`--forwarded-allow-ips=*` no Dockerfile.

## Tasks

### Task 1 — `app/main.py`: gate de docs + CORS explícito

- Extrair `_docs_urls(env: str) -> dict[str, str | None]` que devolve
  `{"docs_url": None, "redoc_url": None, "openapi_url": None}` quando
  `env == "PROD"`, e os defaults do FastAPI (`"/docs"`, `"/redoc"`,
  `"/openapi.json"`) caso contrário.
- `FastAPI(**_docs_urls(ENV), ...)` usando o `ENV` já importado de
  `app.shared.config.settings`.
- Trocar `allow_methods=["*"]` → `["GET", "POST", "PUT", "DELETE", "OPTIONS"]`
  (métodos realmente usados pelas rotas: get/post/put/delete — ver
  `grep -Eon "@router\.(get|post|put|patch|delete)"`, sem `patch`).
- Trocar `allow_headers=["*"]` → `["Authorization", "Content-Type"]` (os dois
  únicos headers que `frontend/lib/api/http-client.ts` envia).
- Verify: `uv run pytest app/tests/test_main_hardening.py -q`.
- Done: nenhuma rota deixa de funcionar (suíte completa continua verde).

### Task 2 — `app/shared/config/settings.py`: fail-fast de CORS em PROD

- Dentro do bloco `if ENV == "PROD":` já existente em
  `validate_cross_field_constraints` (mesmo bloco do `METRICS_API_KEY`),
  adicionar: se `cors_origins_list` vazia OU toda origem resolver para host em
  `{"localhost", "127.0.0.1", "::1"}`, `raise ValueError("PROD exige
  CORS_ORIGINS com ao menos uma origem que não seja localhost")`.
- Usar `urlsplit(origin).hostname` para extrair o host de cada origem (mesmo
  padrão já usado para `redis_urls` no mesmo validator).
- Verify: `uv run pytest app/tests/test_main_hardening.py -q`.
- Done: PROD com `CORS_ORIGINS` só-localhost falha o boot com mensagem clara;
  PROD com origem real sobe normalmente.

### Task 3 — Testes + docs

- Criar `app/tests/test_main_hardening.py` cobrindo:
  - `_docs_urls("PROD")` fecha os 3 campos; `_docs_urls("DEV")` (e outros)
    mantém os defaults.
  - CORS: `allow_methods`/`allow_headers` do middleware montado não contêm
    `"*"` (inspecionar `app.user_middleware`).
  - Fail-fast: `_monkeypatch_prod_remoto`-style (mesmo helper de
    `test_metrics_guard.py`) com `CORS_ORIGINS=http://localhost:3000` →
    `ValidationError` citando a mensagem acima; com
    `CORS_ORIGINS=https://or-automation.example.com` → sobe sem erro.
- Marcar `OBS-03` como `[x]` em `.planning/REQUIREMENTS.md` com nota do commit.
- Verify: suíte completa (`uv run pytest`) sem regressão.
