from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from jose import JWTError, jwt

from app.shared.config.settings import get_settings


def _encode_token(payload: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    *, user_id: int, email: str, roles: list[str]
) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    expire = datetime.now(UTC) + timedelta(seconds=expires_in)
    token = _encode_token(
        {
            "typ": "access",
            "sub": str(user_id),
            "email": email,
            "roles": roles,
            "exp": expire,
        }
    )
    return token, expires_in


def create_refresh_token(
    *, user_id: int, email: str, roles: list[str], auth_time: int | None = None
) -> str:
    now = datetime.now(UTC)
    expire = now + timedelta(days=7)
    # auth_time é o instante do login original (Microsoft SSO ou senha) e é
    # preservado a cada renovação — é ele, não o "iat" deste token específico,
    # que refresh_token usa para aplicar o teto absoluto de sessão.
    resolved_auth_time = auth_time if auth_time is not None else int(now.timestamp())
    return _encode_token(
        {
            "typ": "refresh",
            "sub": str(user_id),
            "email": email,
            "roles": roles,
            "jti": uuid4().hex,
            "iat": int(now.timestamp()),
            "auth_time": resolved_auth_time,
            "exp": expire,
        }
    )


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("typ") != "access":
        raise JWTError("Invalid access token type")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("typ") != "refresh":
        raise JWTError("Invalid refresh token type")
    return payload
