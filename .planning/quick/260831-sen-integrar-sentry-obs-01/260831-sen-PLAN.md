---
quick_id: 260831-sen
status: planned
---

# Quick Task 260831-sen: Integrar Sentry (OBS-01)

## Contexto

`OBS-01` do audit de segurança (`AUDIT-2026-08-07.md`): "Integração Sentry (ou
APM aprovado) via DSN em env; ausência de DSN não derruba a app em non-prod".
Confirmado ainda aberto em 2026-08-31 — nenhuma referência a Sentry no código.

Não é pedido fail-fast em PROD sem DSN (diferente de `CORS_ORIGINS`/
`METRICS_API_KEY`) — só que a ausência de DSN não quebre a app fora de PROD.
`sentry_sdk.init(dsn="")` já é um no-op documentado do SDK (fica desabilitado
sem levantar erro), então isso vale para qualquer ambiente, não só non-prod.

## Tasks

### Task 1 — Dependência + settings

- `pyproject.toml`: adicionar `"sentry-sdk[fastapi]>=2.0.0"` em
  `dependencies` (não em `dev` — precisa rodar em PROD).
- `app/shared/config/settings.py`: novos campos
  - `sentry_dsn: str = Field(default="", validation_alias="SENTRY_DSN")`
  - `sentry_traces_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0, validation_alias="SENTRY_TRACES_SAMPLE_RATE")`
- `.env.example`: documentar as duas variáveis (vazio = Sentry desabilitado).
- Rodar `uv lock` para atualizar `uv.lock`.

### Task 2 — Inicialização

- Novo módulo `app/shared/observability/__init__.py` +
  `app/shared/observability/sentry.py` com
  `def setup_sentry(settings: Settings) -> None`, chamando
  `sentry_sdk.init(dsn=settings.sentry_dsn or None, environment=ENV,
  traces_sample_rate=settings.sentry_traces_sample_rate,
  send_default_pii=False)`.
- `app/main.py`: chamar `setup_sentry(settings)` logo depois de
  `settings = get_settings()`, antes de criar o `FastAPI(...)` (integração
  ASGI do Sentry precisa vir antes da criação do app).

### Task 3 — Testes + docs

- `app/tests/test_observability_sentry.py`: mock de `sentry_sdk.init` via
  `unittest.mock.patch`, cobrindo:
  - DSN vazio → `sentry_sdk.init` chamado com `dsn=None` (não levanta).
  - DSN presente → chamado com o DSN real e `environment` correto.
  - Import de `app.main` com `SENTRY_DSN` vazio não levanta (non-prod
    continua de pé sem DSN — critério de aceite do OBS-01).
- Marcar `OBS-01` como `[x]` em `.planning/REQUIREMENTS.md` com o commit.
- Verify: suíte completa sem regressão.
