from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.bootstrap import lifespan
from app.modules import all_routers
from app.shared.config.settings import ENV, get_settings
from app.shared.errors.handlers import register_exception_handlers
from app.shared.http.body_limit import MaxBodySizeMiddleware

settings = get_settings()


def _docs_urls(env: str) -> dict[str, Any]:
    """Fecha /docs, /redoc e /openapi.json em PROD (OBS-03).

    Fora de PROD mantem os defaults do FastAPI (nao passar as chaves usa o
    default da propria classe).
    """
    if env == "PROD":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title="System Automation API",
    description="API do portal System Automation project",
    version="0.1.0",
    lifespan=lifespan,
    **_docs_urls(ENV),
)

# Precisa ser adicionado ANTES do CORSMiddleware: Starlette.add_middleware faz
# insert(0, ...), entao o ultimo adicionado fica o mais externo. Com o limite
# de corpo adicionado primeiro, o CORS fica por fora e injeta os cabecalhos de
# CORS tambem na resposta 413 — senao o navegador do frontend veria um erro
# opaco de CORS em vez de um 413 legivel.
app.add_middleware(
    MaxBodySizeMiddleware, max_body_bytes=settings.request_max_body_bytes
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

register_exception_handlers(app)

for router in all_routers:
    app.include_router(router)


@app.get("/")
def home() -> dict[str, str]:
    return {"status": "Online", "msg": "System Automation API"}
