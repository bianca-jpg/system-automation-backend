---
quick_id: 260831-sen
status: complete
---

# Quick Task 260831-sen — SUMMARY

Fechado `OBS-01` do audit de segurança: nenhuma integração de Sentry/APM
existia antes desta quick task.

## O que mudou

- `pyproject.toml` / `uv.lock`: `sentry-sdk[fastapi]>=2.0.0` (dependência de
  runtime, não dev — precisa rodar em PROD).
- `app/shared/config/settings.py`: `sentry_dsn` (default `""`) e
  `sentry_traces_sample_rate` (default `0.0`, `0.0..1.0`).
- `app/shared/observability/sentry.py` (novo): `setup_sentry(settings)`
  chama `sentry_sdk.init(dsn=settings.sentry_dsn or None, environment=ENV,
  traces_sample_rate=..., send_default_pii=False)`.
- `app/main.py`: `setup_sentry(settings)` chamado logo após `get_settings()`,
  antes de `FastAPI(...)` — necessário para a integração ASGI do Sentry
  instrumentar o app corretamente.
- `.env.example`: `SENTRY_DSN=` / `SENTRY_TRACES_SAMPLE_RATE=0.0` documentados.

`dsn=None` é um no-op documentado do próprio SDK (fica desabilitado sem
levantar erro) — cobre o critério de aceite "ausência de DSN não derruba a
app" em qualquer ambiente, não só non-prod. Por isso não há fail-fast de PROD
sem DSN aqui (diferente de `CORS_ORIGINS`/`METRICS_API_KEY`) — o audit não
pediu isso para OBS-01.

## Testes

`app/tests/test_observability_sentry.py` (4 testes novos): DSN vazio →
`sentry_sdk.init` chamado com `dsn=None`; DSN real → repassado como está;
`environment` reflete `ENV`; `app.main` importa sem erro com DSN vazio.

Suíte completa: **902 passed, 18 skipped, 0 failed** (898 + 4 novos, 0
regressão). Rodada via `docker compose -f .docker/docker-compose.yml exec api
uv run pytest` após rebuild da imagem (`docker compose build api`) para
incluir a dependência nova.

## Commit

`abb46ba` — `feat(observability): integra Sentry via SENTRY_DSN (OBS-01)`

## Pendência operacional (fora de escopo de código)

`SENTRY_DSN` precisa ser provisionado no ambiente de PROD (secret
manager/task definition do ECS) para o Sentry efetivamente coletar erros —
isso é trabalho de infra/ops, não deste repositório. Sem essa variável
configurada, o comportamento observável não muda (Sentry continua
desabilitado, como já era antes desta quick task).
