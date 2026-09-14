---
slug: 260908-prf
status: incomplete
date: 2026-09-08
repos: [backend, frontend]
branch: develop
commit: null
---

# Summary — 260908-prf — Perfil do usuário via SSO

**Status: implementado e verificado, NÃO commitado.** Aguardando a usuária conferir a tela
logada e aprovar as mensagens de commit (são dois repos, dois commits).

## Backend (backend)

| Arquivo | Mudança |
|---|---|
| `alembic/versions/036_auth_users_display_name.py` | **novo** — coluna nullable, sem backfill (não há fonte de nomes; derivar do e-mail produziria dado errado) |
| `app/modules/auth/infrastructure/models.py` | `AuthUser.display_name` |
| `app/modules/auth/domain/roles.py` | `ROLE_TITLE` + `role_title(roles)` devolvendo o rótulo do papel de MAIOR nível |
| `app/modules/auth/application/schemas.py` | `AuthUserResponse` ganha `display_name` e `role_title` |
| `app/modules/auth/application/casos_uso.py` | `sign_in_microsoft` captura o claim `name`; `_user_response` serializa os campos novos; caso de uso `obter_perfil_atual` |
| `app/modules/auth/infrastructure/http/routes.py` | `GET /api/auth/users/me`, só `get_current_user` (ver o próprio nome não é privilégio) |
| `app/modules/auth/service.py` | reexporta `obter_perfil_atual` |
| `app/tests/test_auth_perfil_usuario.py` | **novo** — 18 testes |

## Frontend (frontend)

| Arquivo | Mudança |
|---|---|
| `lib/api/types/auth.ts` | `roleTitle` em `NormalizedAuthSession` |
| `lib/api/normalize-auth-api.ts` | propaga `role_title`; comentário fixando por que `image` segue `undefined` |
| `next-auth.d.ts` | `roleTitle` em `User`, `Session` e `JWT` |
| `auth.ts` | propaga `roleTitle` no build, no `jwt`, no `session` e **no refresh** (papel pode mudar entre emissão e renovação) |
| `shared/lib/iniciais.ts` | **novo** — iniciais primeiro+último nome, ignorando preposições |
| `shared/ui/primitives/avatar.tsx` | reexporta `AvatarImage` (já existia no DS, faltava na fachada) |
| `widgets/app-shell/ui/app-sidebar.tsx` | avatar com iniciais; menu mostra nome + rótulo do papel |
| `features/preferences/ui/settings-modal.tsx` | duas colunas (Informações / Tema); aba nova em modo leitura |
| `shared/lib/iniciais.test.ts` | **novo** — 8 testes |
| `lib/api/normalize-auth-api.test.ts` | **novo** — 8 testes |
| `widgets/app-shell/ui/app-sidebar.identidade.test.tsx` | **novo** — 5 testes |

## Decisões de implementação

- **Nome reescrito a cada login, não editável.** O diretório é a fonte de verdade; campo
  editável prometeria alteração que o próximo login desfaria. Guard: claim ausente **não**
  apaga nome já guardado, e só grava quando mudou (evita sujar `updated_at` a cada acesso).
- **`role_title` serializado pelo backend**, não montado na UI: papel virando texto tem um
  só dono, junto de `ROLE_LEVEL`.
- **`readOnly` em vez de `disabled`** nos campos do perfil: `disabled` sai da ordem de
  tabulação e não é anunciado por leitor de tela — esconderia justamente o conteúdo da tela.
- **Iniciais não são derivadas de e-mail**: "victoria.mollica" viraria "V", que diz menos
  que o ícone genérico.
- **`AvatarImage` reexportado agora** embora nada renderize foto: deixa a fachada completa e
  o próximo passo (Graph) não precisa mexer aqui.

## Verificação executada

**Backend**
- `alembic upgrade head` nos dois bancos — o de teste estava em **034**, duas atrás
- `pytest` completo: **982 passed, 18 skipped, 0 falhas**
- `test_auth_perfil_usuario.py`: **18 passed**

**Frontend**
- `pnpm typecheck` — limpo
- `pnpm lint` — limpo
- `pnpm build` — compila
- Áreas tocadas juntas (19 arquivos): **185 passed, 0 falhas**

**Instabilidade pré-existente da suíte completa (não regressão):** `pnpm vitest run` inteiro
falhou 3 testes numa execução e 5 em outra, com **conjuntos diferentes** —
`orders-list.pagination`, `parametros/page`, `create-parameter-modal`. Nenhum deles em auth,
perfil, sidebar ou settings-modal. Todos passam isolados, antes e depois da mudança. Confere
com a instabilidade já conhecida do baseline.

## Não verificado

A tela **logada** não foi aberta: o SSO é Microsoft e a sessão é da usuária. Falta conferir
visualmente o nome + papel no menu, as iniciais no avatar e a aba Informações em duas colunas
(incluindo o empilhamento no mobile).

**Importante para o teste:** é preciso **deslogar e logar de novo**. O `display_name` só é
gravado no `sign_in_microsoft`, então a sessão atual não tem o nome — e a conta da usuária no
banco local está com `display_name` nulo até o próximo login.

## Fora de escopo (Camada 2 — depende do tenant)

Cargo, telefone, matrícula e **foto de perfil**. Todos exigem Microsoft Graph com escopo
`User.Read`, e dependem de os campos estarem populados no Entra do projeto. A foto ainda
precisa que o backend busque e reserva os bytes (`GET /me/photo/$value` exige header de
autorização, não dá para apontar `<img src>` direto). CPF, admissão, cidade, estado e modelo
de trabalho são dados de RH — fonte diferente, provavelmente a mesma que alimenta o
`collaborator` do Ara.
