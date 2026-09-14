---
quick_id: 260831-fai
status: complete
---

# Quick Task 260831-fai — SUMMARY

Fechado o achado de hardening `--forwarded-allow-ips=*` no
`.docker/Dockerfile`.

## O que mudou

- `.docker/Dockerfile`: `--forwarded-allow-ips=*` → `--forwarded-allow-ips="${UVICORN_FORWARDED_ALLOW_IPS:-30.1.1.0/24,30.1.11.0/24,30.1.21.0/24}"`.
  Os 3 CIDRs são as subnets privadas reais da VPC do ECS, confirmadas pela
  mantenedora em 2026-08-31. `uvicorn[standard]==0.47.0` já suporta notação
  CIDR nativamente (`ipaddress.ip_network`, confirmado por inspeção direta do
  código instalado em `uvicorn/middleware/proxy_headers.py::_TrustedHosts`).
  Fica configurável via `UVICORN_FORWARDED_ALLOW_IPS` (env do task definition
  do ECS) para não precisar rebuild de imagem se a VPC mudar.
- `.env.example`: variável documentada.
- `.docker/docker-compose.yml` **não foi tocado** — dev local não tem proxy
  real na frente, então `--forwarded-allow-ips=*` ali não é o mesmo risco (e
  trocar sem necessidade só adicionaria chance de quebrar dev local).

## Testes

`app/tests/test_pipeline_safety.py`: novo teste
`test_dockerfile_nao_confia_em_forwarded_ips_de_qualquer_peer` (lê o
Dockerfile como texto, mesmo padrão já usado nesse arquivo para `pipeline.yml`)
garante que o literal `forwarded-allow-ips=*` não volta e que os 3 CIDRs
esperados estão presentes.

Suíte completa: **903 passed, 18 skipped, 0 failed** (902 + 1 novo, 0
regressão).

## Commit

`fa20ac4` — `fix(security): restringe forwarded-allow-ips do uvicorn aos CIDRs reais da VPC`

## Nota

Isso fecha o último dos 4 achados de backend levantados pelo tech lead nesta
rodada (Sentry, CORS/docs, forwarded-allow-ips). O 5º item (hardcode de
parâmetros no front) é uma quick task separada, no repo
`frontend`.
