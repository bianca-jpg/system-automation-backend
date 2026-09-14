"""JWT e autorização — dependências FastAPI para RBAC.

Reexporta app.modules.auth.infrastructure.http.dependencies: a lógica de RBAC pertence ao bounded
context Identidade & Acesso, não é infraestrutura genérica. Este módulo existe
só para não quebrar os imports já espalhados pelo resto do backend
(`from app.shared.security import require_viewer`, etc.).
"""

from app.modules.auth.infrastructure.http.dependencies import (
    CurrentUser,
    bearer_scheme,
    get_current_user,
    require_actor,
    require_admin,
    require_gestor,
    require_viewer,
)

__all__ = [
    "CurrentUser",
    "bearer_scheme",
    "get_current_user",
    "require_actor",
    "require_admin",
    "require_gestor",
    "require_viewer",
]
