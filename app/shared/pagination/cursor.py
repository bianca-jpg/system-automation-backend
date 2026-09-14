"""Cursor opaco e assinado para paginação keyset."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable, Sequence


class CursorInvalidoError(ValueError):
    pass


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _scope_hash(scope: str) -> str:
    return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]


def encode_cursor(*, kind: str, scope: str, key: Sequence[object], secret: str) -> str:
    payload = json.dumps(
        {"v": 1, "kind": kind, "scope": _scope_hash(scope), "key": list(key)},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()[:16]
    return f"{_b64encode(payload)}.{_b64encode(signature)}"


def decode_cursor(
    value: str | None,
    *,
    kind: str,
    scope: str,
    secret: str,
    key_size: int,
    key_validator: Callable[[tuple], bool] | None = None,
) -> tuple | None:
    if value is None:
        return None
    try:
        encoded_payload, encoded_signature = value.split(".", 1)
        payload = _b64decode(encoded_payload)
        signature = _b64decode(encoded_signature)
        expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()[
            :16
        ]
        if not hmac.compare_digest(signature, expected):
            raise CursorInvalidoError("assinatura inválida")
        decoded = json.loads(payload)
        key = decoded["key"]
        if (
            decoded.get("v") != 1
            or decoded.get("kind") != kind
            or decoded.get("scope") != _scope_hash(scope)
            or not isinstance(key, list)
            or len(key) != key_size
        ):
            raise CursorInvalidoError("cursor não pertence a esta consulta")
        typed_key = tuple(key)
        if key_validator is not None and not key_validator(typed_key):
            raise CursorInvalidoError("chave do cursor inválida")
        return typed_key
    except CursorInvalidoError:
        raise
    except (
        binascii.Error,
        UnicodeDecodeError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise CursorInvalidoError("cursor malformado") from exc
