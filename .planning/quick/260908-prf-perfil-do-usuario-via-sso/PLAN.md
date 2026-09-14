---
slug: 260908-prf
description: Exibe nome real, e-mail corporativo e nível de acesso do usuário logado, alimentados pelo claim name do SSO
date: 2026-09-08
repos: [backend, frontend]
branch: develop
---

# Quick Task 260908-prf — Perfil do usuário via SSO

## Problema

O menu do usuário mostra o **e-mail**, não o nome. A cadeia:

1. `sign_in_microsoft` valida o `id_token` do Entra e lê **só** `email`/`preferred_username`
   (`casos_uso.py:112`). O claim `name`, que a Microsoft já manda, é descartado.
2. `AuthUserResponse` devolve apenas `id`, `email`, `roles`, `groups` (`casos_uso.py:69`).
3. O frontend resolve o nome como `display_name → email → "Usuário"`
   (`lib/api/normalize-auth-api.ts`). Sem `display_name`, cai no e-mail.

O tipo `UserApiResponse` do frontend (`lib/api/types/auth.ts:30`) já declara `display_name`
e `role_title` — contrato herdado do manager do Ara. O frontend sabe receber; o backend
nunca implementou.

## Escopo (decidido pela usuária em 2026-09-08)

Exibir **nome**, **e-mail corporativo** e **nível de acesso** (o nome do papel, não a lista
de permissões).

Mais as **iniciais do nome no avatar**, no lugar do ícone genérico de pessoa que está lá
hoje — mesmo comportamento do Teams para quem não tem foto. Sai de graça junto com o nome.

**Fora de escopo:** cargo, telefone, matrícula, CPF, admissão, cidade, estado, modelo de
trabalho. Nenhum deles existe no `id_token` — cargo/telefone/matrícula exigiriam Microsoft
Graph (não integrado, e depende de os campos estarem populados no Entra); o resto é dado de
RH. CPF ficou de fora por decisão de custo/risco: criaria um novo armazenamento de dado
pessoal sensível sem valor para o fluxo de OR.

**Foto de perfil** também fica fora, pelo mesmo motivo: o `id_token` do Entra não tem claim
de imagem, então a foto exige Graph (`GET /me/photo/$value`, escopo `User.Read`), que devolve
bytes de JPEG e precisa de header de autorização — o backend teria de buscar e reservir. Vai
junto com o cargo numa Camada 2. O caminho de UI já existe: o design system exporta
`AvatarImage` (só não está reexportado na fachada `shared/ui/primitives/avatar.tsx`) e a
sessão já carrega `image`. As iniciais desta task viram o fallback natural de quem não tiver
foto cadastrada.

## Tarefas — Backend

1. **Migration 036** (head atual: 035): `auth_users.display_name`, `String(255)`, nullable.
   Registrar na docstring que **não** é a volta do `user_name` que a 035 removeu — aquele era
   campo de cadastro local; este é alimentado pelo SSO.
2. **`models.py`**: coluna `display_name` no `AuthUser`.
3. **`domain/roles.py`**: mapa `ROLE_TITLE: dict[str, str]` (`basico`→"Básico",
   `operacional`→"Operacional", `gestor`→"Gestor", `administrador`→"Administrador",
   `admin_tecnico`→"Admin Técnico") + `role_title(roles)` devolvendo o rótulo do papel de
   MAIOR nível. Fica ao lado de `ROLE_LEVEL`, mesma fonte de verdade — nenhum rótulo escrito
   solto na UI.
4. **`application/schemas.py`**: `AuthUserResponse` ganha `display_name: str | None` e
   `role_title: str | None`.
5. **`casos_uso.py`**:
   - `sign_in_microsoft` lê o claim `name` e grava/atualiza `display_name` **a cada login**
     (SSO é a fonte de verdade: troca de nome no AD se corrige sozinha no acesso seguinte).
   - `_user_response` passa a devolver `display_name` e `role_title`.
   - Novo caso de uso `obter_perfil_atual(session, user_id)`.
6. **`routes.py`**: `GET /api/auth/users/me`, protegido por `get_current_user`. Hoje não
   existe nenhum endpoint de perfil próprio — só a listagem de admin.
   **Declarar antes de `/users/{user_id}`** não é necessário (rotas distintas), mas `me`
   nunca pode ser lido como `user_id`: o path é `/users/me`, e `/users/{user_id}` recebe
   `int`, então o FastAPI já separa os dois.
7. **`service.py`**: reexportar o caso de uso novo.

## Tarefas — Frontend

8. **`auth.ts`**: `buildUserFromNormalizedAuth` e os callbacks `jwt`/`session` propagam
   `roleTitle`; `display_name` já é consumido por `normalizeAuthApiResponse` (vira `name`).
9. **`next-auth.d.ts`**: `roleTitle` na sessão e no JWT.
10. **`app-sidebar.tsx`**: menu do usuário mostra nome + rótulo do papel embaixo.
11. **Aba "Informações" no `SettingsModal`**: layout de duas colunas espelhando o Ara —
    navegação à esquerda (Informações / Tema), conteúdo à direita. Campos em **modo leitura**
    (`disabled`): nome e e-mail vêm do SSO e são reescritos a cada login, então oferecer
    edição prometeria o que não se sustenta.
    Primitivas: `Dialog`, `input`, `label`, `Typography`, `Pressable`, `Separator`, `Avatar` —
    todas já existentes. Nada novo no design system, nada editado em `design-system/`.

## Verificação

- Backend: `pytest` nos testes de auth; `alembic upgrade head` nos **dois** bancos
  (aplicação e `system_automation_test`)
- Frontend: `pnpm typecheck`, `pnpm lint`, `pnpm vitest run`, `pnpm build`
- Navegador: sem sessão não dá para conferir a tela logada (SSO Microsoft é da usuária)
