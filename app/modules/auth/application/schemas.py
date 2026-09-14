from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenBlock(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int
    token_type: str = "bearer"


class AuthUserResponse(BaseModel):
    id: int
    email: EmailStr | None = None
    # Nome vindo do claim `name` do SSO. `None` para contas que ainda não
    # autenticaram desde a migration 036 — o frontend já trata a ausência
    # caindo no e-mail (`normalizeAuthApiResponse`).
    display_name: str | None = None
    # Rótulo legível do papel de maior nível (ROLE_TITLE). Serializado pelo
    # backend, e não montado na UI, para o texto do papel ter um só dono.
    role_title: str | None = None
    roles: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)


class AuthResponse(BaseModel):
    user: AuthUserResponse
    token: TokenBlock


class MicrosoftSsoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id_token: str = Field(min_length=1, max_length=8192)


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=4096)


class SignOutResponse(BaseModel):
    status: str = "signed_out"


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    roles: list[str] = Field(default_factory=list)
    confirmed_at: datetime | None = None


class AdminUsersPageResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    rows: list[AdminUserResponse]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, serialization_alias="pageSize")
    total_pages: int = Field(ge=1, serialization_alias="totalPages")


class SetUserRolesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    roles: list[str] = Field(min_length=1)
