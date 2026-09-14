"""Adapter SMTP com classificação conservadora do resultado de entrega."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage

import aiosmtplib
from aiosmtplib import errors

from app.modules.comunicacoes.application.ports import (
    FalhaTerminalEntrega,
    FalhaTransitoriaEntrega,
    ResultadoIncertoEntrega,
)
from app.modules.comunicacoes.domain.modelos import EntregaEmail
from app.shared.config.settings import Settings


def _response_error(code: int) -> FalhaTerminalEntrega | FalhaTransitoriaEntrega:
    family = code // 100
    error_code = f"smtp_response_{family}xx"
    if family == 4:
        return FalhaTransitoriaEntrega(error_code)
    return FalhaTerminalEntrega(error_code)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def enviar(self, entrega: EntregaEmail) -> None:
        settings = self._settings
        if not settings.smtp_configured:
            raise FalhaTerminalEntrega("smtp_not_configured")

        message = EmailMessage()
        message["From"] = settings.effective_smtp_from
        message["To"] = entrega.recipient
        message["Subject"] = entrega.subject
        message["Message-ID"] = entrega.message_id
        message.set_content(entrega.content)

        try:
            # ``timeout`` do aiosmtplib vale para operações individuais. O
            # deadline externo limita a conversa SMTP completa e mantém o
            # lease maior que todo o I/O, não apenas que cada comando.
            async with asyncio.timeout(settings.smtp_timeout_seconds):
                await aiosmtplib.send(
                    message,
                    hostname=settings.smtp_host,
                    port=settings.smtp_port,
                    username=settings.smtp_user,
                    password=settings.smtp_password,
                    start_tls=settings.smtp_starttls,
                    timeout=settings.smtp_timeout_seconds,
                )
        except errors.SMTPAuthenticationError as exc:
            raise FalhaTerminalEntrega("smtp_authentication") from exc
        except errors.SMTPRecipientsRefused as exc:
            codes = [int(recipient.code) for recipient in exc.recipients]
            normalized = max(codes, default=500)
            raise _response_error(normalized) from exc
        except errors.SMTPResponseException as exc:
            raise _response_error(int(exc.code)) from exc
        except errors.SMTPConnectTimeoutError as exc:
            raise FalhaTransitoriaEntrega("smtp_connect_timeout") from exc
        except errors.SMTPConnectError as exc:
            raise FalhaTransitoriaEntrega("smtp_connect_error") from exc
        except errors.SMTPNotSupported as exc:
            raise FalhaTerminalEntrega("smtp_not_supported") from exc
        except (
            errors.SMTPReadTimeoutError,
            errors.SMTPServerDisconnected,
        ) as exc:
            # O provider pode ter aceitado DATA e perdido apenas a resposta.
            # Repetir automaticamente criaria uma duplicata possível.
            raise ResultadoIncertoEntrega("smtp_outcome_unknown") from exc
        except TimeoutError as exc:
            # O deadline global pode vencer depois de DATA ter sido aceito.
            raise ResultadoIncertoEntrega("smtp_outcome_unknown") from exc
        except Exception as exc:
            raise ResultadoIncertoEntrega("smtp_outcome_unknown") from exc
