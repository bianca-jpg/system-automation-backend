# Segurança

A autenticação do backend é exclusivamente **Microsoft Entra ID** (SSO,
single-tenant): não existe senha local, cadastro público, OTP nem recuperação
de senha. Uma conta nova nasce no primeiro login SSO (`POST
/api/auth/sso/microsoft`) com papel `basico` e é promovida por
`PUT /api/auth/users/{id}/roles` usando um token de admin já existente. O
próprio Entra ID já garante "só gente do projeto" (app single-tenant) — o
backend não reforça isso de novo.

Este documento cobre a detecção e remoção das contas legadas do tempo do
login por senha (incluindo as antigas contas seed), a rotação da chave de
métricas e os controles de segurança vigentes.

## Detecção

Contas legadas são as que nasceram antes da remoção do login local —
identificadas por `auth_provider = 'local'` (o SSO sempre grava
`auth_provider = 'microsoft'`). Rode a query abaixo em qualquer ambiente
compartilhado (dev, staging, produção) para listá-las:

```sql
SELECT id, email, roles, auth_provider, confirmed_at, created_at
FROM auth_users
WHERE auth_provider = 'local'
ORDER BY id;
```

Se a query não retornar linhas, o ambiente já está limpo — nenhuma ação
adicional é necessária.

## Remoção (único caminho — não existe mais rotação de senha)

Contas `auth_provider = 'local'` não conseguem mais autenticar sozinhas (não
há caminho de senha), mas **devem ser removidas mesmo assim**: o login por
SSO casa a conta pelo e-mail, então se alguém no tenant Microsoft controlar
um endereço igual ao de uma conta legada com papel elevado (`administrador`,
`admin_tecnico`), essa pessoa herda o papel dessa linha no primeiro login.
Deixar as contas legadas paradas é um risco de escalonamento de privilégio,
não uma inofensividade por "não ter senha".

1. Garantir antes um administrador real via SSO: pedir para a pessoa
   logar uma vez em `POST /api/auth/sso/microsoft` (isso a provisiona com
   papel `basico`) e promovê-la com `PUT /api/auth/users/{id}/roles` usando
   um token de admin ainda válido. Nunca remova contas legadas com papel
   elevado sem ter primeiro um administrador real com acesso garantido.
2. Rodar a query de detecção acima para confirmar a lista exata.
3. `DELETE FROM auth_users WHERE auth_provider = 'local';` (ou por `id`
   específico, se só parte das contas legadas deve sair).

**Comportamento de FK (nenhuma bloqueia o `DELETE` hoje):**

- `comunicacoes.requested_by_user_id`: `ON DELETE SET NULL`.
- `realtime_read_cursors.user_id`: `ON DELETE CASCADE` (remove também os
  cursores da conta).
- `parametro_change_requests.requested_by`/`reviewed_by`: `ON DELETE SET
  NULL` desde a migration 034 (quick task 260825-f21) — o histórico de
  solicitações de parâmetro sobrevive intacto, só o vínculo com o usuário
  excluído se perde. Antes da 034 esta FK era `NOT NULL` sem `ON DELETE` e
  bloqueava o `DELETE`; isso não se aplica mais.

## Rotação da chave de métricas (`METRICS_API_KEY`)

`GET /metrics` exige o header `X-Metrics-Key` casando com `METRICS_API_KEY`
(ver `docs/api.md`). Rotação:

1. Gerar um valor novo: `openssl rand -hex 32`.
2. Atualizar `METRICS_API_KEY` no `.env`/secret manager do ambiente **e** no
   scraper Prometheus na mesma janela — os dois lados precisam bater. Como o
   `.env` só é lido na subida da stack, a troca exige reiniciar a API.
3. Enquanto os dois lados não estiverem sincronizados, o scrape responde 403
   (chave antiga não bate mais com a nova) — é esperado durante a janela de
   troca, não um incidente.
4. Confirmar com um `curl -H "X-Metrics-Key: <valor novo>" .../metrics` antes
   de considerar a rotação concluída.

Não existe rate limit em `/metrics` (dívida conhecida, aceita conscientemente
— ver threat register da tarefa 260827-emo). `app/shared/infrastructure/rate_limit.py`
já existe e pode ser aplicado numa tarefa própria se o brute force da chave
virar risco ativo.

## Prevenção

Controles vigentes:

- Único caminho de entrada é `POST /api/auth/sso/microsoft`: o ID token é
  assinado pela Microsoft e validado (assinatura, emissor, audiência,
  validade) contra o JWKS do tenant — não existe segredo adivinhável
  (senha ou código) em nenhum endpoint restante.
- `JWT_SECRET` forte obrigatório em PROD (SEC-01).
- Revogação de sessão no sign-out (SEC-06): `POST /api/auth/sign-out` grava
  um corte por usuário no Redis que invalida qualquer refresh token anterior.
- Detecção de reuso de refresh token por `jti`: reapresentar um refresh token
  já rotacionado revoga a sessão inteira (mesmo corte do sign-out).
- Teto absoluto de sessão (`AUTH_ABSOLUTE_SESSION_SECONDS`): passado o prazo
  desde o login original, `POST /api/auth/token/refresh` exige um login novo,
  independente de quantas renovações a janela deslizante permitiria.
- A autorização de quem entra (pertence ou não ao tenant do projeto) é
  responsabilidade do próprio Microsoft Entra ID.

**Controles que deixaram de se aplicar** com a remoção do login por
e-mail/senha (quick task 260827-fqf) — o objeto que protegiam não existe
mais:

- SEC-02 (seed de usuários on/off por env) — não há mais seed.
- SEC-03 (fail-fast de PROD para seed) — idem.
- SEC-04 (hash de senha) — não há mais senha local.
- SEC-05 (hash do código OTP) — não há mais OTP.
- SEC-07 (rate limit usuário+IP em sign-in/OTP) — não há mais segredo
  adivinhável nesses endpoints (eles não existem mais).
- SEC-08 (lockout de conta por falhas consecutivas) — mesma razão do SEC-07;
  removido junto (`app/shared/infrastructure/account_lockout.py`).

## Checklist por ambiente

- [ ] Rodar a query de detecção acima
- [ ] Se houver conta `auth_provider = 'local'` com papel elevado, garantir
      um administrador real via SSO antes de remover
- [ ] Remover as contas legadas (`DELETE ... WHERE auth_provider = 'local'`)
- [ ] Reexecutar a query de detecção para confirmar o resultado esperado
