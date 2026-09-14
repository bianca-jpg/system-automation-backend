from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.database.base import Base


class AuthUser(Base):
    __tablename__ = "auth_users"

    # Sem index=True no id: a primary key já cria o índice.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # Nome de exibição espelhado do diretório corporativo: `sign_in_microsoft`
    # grava o claim `name` do ID token a cada autenticação. Nullable porque
    # contas provisionadas antes da migration 036 só ganham nome no próximo
    # login — e porque o claim, embora sempre presente na prática, é opcional
    # no OIDC. Não é editável pelo painel: o SSO sobrescreveria a alteração.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Origem da conta: "local" (legado, sem caminho de autenticação possível
    # após a remoção do login por senha) ou "microsoft" (SSO Entra ID, único
    # login que existe hoje). Base da query de detecção do runbook
    # (docs/seguranca.md) — contas "local" nunca mais autenticam sozinhas,
    # mas continuam existindo (e herdáveis por e-mail via SSO) até serem
    # removidas ou rebaixadas.
    auth_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
