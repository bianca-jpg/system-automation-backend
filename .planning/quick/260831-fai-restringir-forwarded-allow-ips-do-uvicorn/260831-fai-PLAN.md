---
quick_id: 260831-fai
status: planned
---

# Quick Task 260831-fai: Restringir `--forwarded-allow-ips` do uvicorn

## Contexto

`.docker/Dockerfile:52` usa `--forwarded-allow-ips=*`, confiando em
`X-Forwarded-*` de qualquer peer que alcance a porta 8000. Achado do audit de
hardening confirmado ainda aberto em 2026-08-31. A mantenedora confirmou os
CIDRs reais das subnets privadas da VPC do ECS: `30.1.1.0/24`, `30.1.11.0/24`,
`30.1.21.0/24`.

`uvicorn[standard]==0.47.0` (`_TrustedHosts` em
`uvicorn/middleware/proxy_headers.py`) já suporta notação CIDR nativa
(`ipaddress.ip_network`) numa lista separada por vírgula — confirmado por
inspeção direta do código instalado.

Fora de escopo: `.docker/docker-compose.yml` (dev local, sem proxy real na
frente — `--forwarded-allow-ips=*` ali não é o achado do audit e trocar
introduziria risco de quebrar o dev local sem ganho de segurança real).

## Tasks

### Task 1 — Dockerfile

- Trocar `--forwarded-allow-ips=*` por
  `--forwarded-allow-ips="${UVICORN_FORWARDED_ALLOW_IPS:-30.1.1.0/24,30.1.11.0/24,30.1.21.0/24}"`
  — env var configurável (se a VPC mudar, não precisa rebuild de imagem) com
  default já restrito aos CIDRs reais de hoje.
- Atualizar o comentário acima do `CMD` explicando a troca e onde ajustar se
  a VPC mudar.
- `.env.example`: documentar `UVICORN_FORWARDED_ALLOW_IPS` (opcional, só times
  de infra normalmente precisam mexer).

### Task 2 — Teste de conformidade estática

- `app/tests/test_pipeline_safety.py` (mesmo arquivo que já testa
  `pipeline.yml`, mesmo padrão de leitura de arquivo): novo teste
  `test_dockerfile_no_confia_em_forwarded_ips_de_qualquer_peer` que lê
  `.docker/Dockerfile` e garante que a linha do `CMD` não contém o literal
  `forwarded-allow-ips=*` e contém os 3 CIDRs esperados como default.
- Verify: `uv run pytest app/tests/test_pipeline_safety.py -q`.

### Task 3 — Docs

- Marcar o achado no `.planning/STATE.md` (bloco Blockers/Concerns) como
  resolvido.
