from app.modules.auth.infrastructure.http.routes import router as auth_router
from app.modules.comunicacoes.infrastructure.http.routes import (
    router as comunicacoes_router,
)
from app.modules.core.routes import router as core_router
from app.modules.health.routes import router as health_router
from app.modules.ingestao.infrastructure.http.routes import router as ingestao_router
from app.modules.parametros.infrastructure.http.routes import (
    router as parametros_router,
)
from app.modules.pedidos.infrastructure.http.routes import alertas_router
from app.modules.pedidos.infrastructure.http.routes import router as pedidos_router
from app.modules.realtime.infrastructure.http.routes import router as realtime_router
from app.shared.metrics.router import router as metrics_router

all_routers = [
    health_router,
    metrics_router,
    core_router,
    auth_router,
    pedidos_router,
    alertas_router,
    realtime_router,
    comunicacoes_router,
    parametros_router,
    ingestao_router,
]
