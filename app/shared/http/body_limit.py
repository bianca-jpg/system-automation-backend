"""Middleware ASGI puro que rejeita requisicoes HTTP com corpo declarado acima
de um teto configurado, antes de qualquer rota executar.

O que garante: uma requisicao com header `Content-Length` maior que
`max_body_bytes` recebe 413 sem que a rota seja chamada.

O que NAO garante: um cliente que envia `Transfer-Encoding: chunked` (sem
`Content-Length`) passa pelo filtro sem ser medido — esta e uma guarda barata
de edge por header, nao um cap absoluto de streaming. O cap absoluto de
streaming pertence ao proxy/ALB na frente da API.

Implementado como middleware ASGI puro (nao `BaseHTTPMiddleware`, que
buferiza o corpo inteiro em memoria e anularia o proposito de rejeitar corpo
grande sem le-lo).
"""

from __future__ import annotations

import logging

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Passthrough obrigatorio: existe rota WebSocket em
        # app/modules/realtime/infrastructure/http/routes.py (`/ws`), e um
        # middleware que assume HTTP quebraria o realtime inteiro.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length_raw = headers.get("content-length")

        if content_length_raw is not None:
            try:
                content_length = int(content_length_raw)
            except ValueError:
                # Content-Length malformado e rejeitado antes, no parser h11
                # do uvicorn; nao e responsabilidade deste middleware.
                content_length = None

            if content_length is not None and content_length > self.max_body_bytes:
                logger.warning(
                    "Corpo de requisicao acima do teto: %s %s (%s bytes declarados, teto %s)",
                    scope.get("method"),
                    scope.get("path"),
                    content_length,
                    self.max_body_bytes,
                )
                resposta = JSONResponse(
                    status_code=413,
                    content={
                        "detail": "Request entity too large",
                        "type": "RequestEntityTooLarge",
                    },
                )
                await resposta(scope, receive, send)
                return

        await self.app(scope, receive, send)
