from enum import StrEnum
from typing import Final


class automationRole(StrEnum):
    BASICO = "basico"
    OPERACIONAL = "operacional"
    GESTOR = "gestor"
    ADMINISTRADOR = "administrador"
    ADMIN_TECNICO = "admin_tecnico"


ALL_automation_ROLES: Final[frozenset[str]] = frozenset(r.value for r in automationRole)

# Nível de privilégio: papéis com nível maior contêm as capacidades dos menores.
ROLE_LEVEL: Final[dict[str, int]] = {
    automationRole.BASICO: 10,
    automationRole.OPERACIONAL: 20,
    automationRole.GESTOR: 30,
    automationRole.ADMINISTRADOR: 40,
    automationRole.ADMIN_TECNICO: 50,
}

# Rótulo legível de cada papel, para exibição no perfil do usuário. Vive aqui,
# ao lado de ROLE_LEVEL, para não haver um segundo lugar onde papel virou texto:
# um papel novo que entre no enum sem rótulo aparece na UI como o próprio valor
# técnico, o que é visível e corrigível, em vez de sumir da tela.
ROLE_TITLE: Final[dict[str, str]] = {
    automationRole.BASICO: "Básico",
    automationRole.OPERACIONAL: "Operacional",
    automationRole.GESTOR: "Gestor",
    automationRole.ADMINISTRADOR: "Administrador",
    automationRole.ADMIN_TECNICO: "Admin Técnico",
}

# Compatibilidade com seeds/tokens antigos (hype_* e operador → papéis System Automation atuais).
LEGACY_ROLE_MAP: Final[dict[str, str]] = {
    "operador": automationRole.OPERACIONAL,
    "hype_user": automationRole.OPERACIONAL,
    "hype_manager": automationRole.GESTOR,
    "hype_admin": automationRole.ADMINISTRADOR,
    "admin": automationRole.ADMINISTRADOR,
}


def normalize_roles(roles: list[str] | None) -> list[str]:
    if not roles:
        return [automationRole.BASICO]
    normalized: list[str] = []
    for role in roles:
        mapped = LEGACY_ROLE_MAP.get(role, role)
        if mapped in ALL_automation_ROLES and mapped not in normalized:
            normalized.append(mapped)
    return normalized or [automationRole.BASICO]


def max_role_level(roles: list[str] | None) -> int:
    """Maior nível de privilégio entre os papéis (após normalização)."""
    return max((ROLE_LEVEL.get(r, 0) for r in normalize_roles(roles)), default=0)


def has_min_level(roles: list[str] | None, minimum: str) -> bool:
    """True se algum papel atinge o nível mínimo exigido."""
    return max_role_level(roles) >= ROLE_LEVEL.get(minimum, 999)


def role_title(roles: list[str] | None) -> str:
    """Rótulo legível do papel de MAIOR nível — o que a pessoa é, na prática.

    Espelha `max_role_level`: quem acumula papéis é descrito pelo mais alto,
    porque é ele que define o que a pessoa alcança. Papel fora de ROLE_TITLE
    cai no próprio valor normalizado em vez de string vazia, para um papel novo
    sem rótulo aparecer na tela em vez de desaparecer dela.
    """
    normalized = normalize_roles(roles)
    maior = max(normalized, key=lambda r: ROLE_LEVEL.get(r, 0))
    return ROLE_TITLE.get(maior, maior)
