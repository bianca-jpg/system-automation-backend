"""JWT e autorização — dependências FastAPI para RBAC.

Open Host Service do bounded context Identidade & Acesso: é o que todo outro
módulo consome (via app.shared.security, que reexporta este arquivo) para
autenticar/autorizar requisições. Fica dentro de `auth` — não em `shared` —
porque a lógica é do domínio deste contexto, não infraestrutura genérica.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.domain.roles import automationRole, has_min_level, normalize_roles
from app.modules.auth.infrastructure.models import AuthUser
from app.modules.auth.infrastructure.security import decode_access_token
from app.shared.database.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass
class CurrentUser:
    id: int
    email: str | None = None
    roles: list[str] = field(default_factory=list)

    def has_level(self, minimum: str) -> bool:
        return has_min_level(self.roles, minimum)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise _unauthorized()

    try:
        payload = decode_access_token(creds.credentials)
    except JWTError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    sub = payload.get("sub")
    if sub is None:
        raise _unauthorized("Malformed token")

    try:
        user_id = int(sub)
    except (TypeError, ValueError) as exc:
        raise _unauthorized("Malformed token") from exc

    user = await db.get(AuthUser, user_id)
    if user is None:
        raise _unauthorized("User not found")

    # Papéis frescos do banco (mudança de papel vale antes do token expirar);
    # cai para os papéis do token apenas se o banco vier vazio.
    roles = normalize_roles(list(user.roles or [])) or normalize_roles(
        payload.get("roles")
    )
    return CurrentUser(id=user.id, email=user.email, roles=roles)


def require_min_role(minimum: str):
    """Fábrica de dependência: exige nível de privilégio >= mínimo."""

    async def _guard(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has_level(minimum):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role level >= {minimum}",
            )
        return user

    return _guard


# Atalhos de guarda por nível.
require_viewer = require_min_role(automationRole.BASICO)
require_actor = require_min_role(automationRole.OPERACIONAL)
require_gestor = require_min_role(automationRole.GESTOR)
require_admin = require_min_role(automationRole.ADMINISTRADOR)
require_tecnico = require_min_role(automationRole.ADMIN_TECNICO)
