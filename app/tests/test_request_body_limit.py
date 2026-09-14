from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.config.settings import get_settings
from app.shared.http.body_limit import MaxBodySizeMiddleware


def _app_com_rota_post(chamadas: list[bool]) -> FastAPI:
    app_local = FastAPI()
    app_local.add_middleware(MaxBodySizeMiddleware, max_body_bytes=1_024)

    @app_local.post("/echo")
    async def echo() -> dict[str, bool]:
        chamadas.append(True)
        return {"ok": True}

    @app_local.get("/sem-corpo")
    def sem_corpo() -> dict[str, bool]:
        return {"ok": True}

    return app_local


def test_corpo_acima_do_teto_responde_413_e_nao_executa_a_rota():
    chamadas: list[bool] = []
    app_local = _app_com_rota_post(chamadas)

    with TestClient(app_local) as c:
        resp = c.post(
            "/echo",
            content=b"x" * 2_048,
            headers={"content-type": "application/octet-stream"},
        )

    assert resp.status_code == 413
    assert resp.json() == {
        "detail": "Request entity too large",
        "type": "RequestEntityTooLarge",
    }
    assert chamadas == []


def test_corpo_abaixo_do_teto_chega_na_rota():
    chamadas: list[bool] = []
    app_local = _app_com_rota_post(chamadas)

    with TestClient(app_local) as c:
        resp = c.post(
            "/echo",
            content=b"x" * 512,
            headers={"content-type": "application/octet-stream"},
        )

    assert resp.status_code == 200
    assert chamadas == [True]


def test_get_sem_corpo_passa():
    chamadas: list[bool] = []
    app_local = _app_com_rota_post(chamadas)

    with TestClient(app_local) as c:
        resp = c.get("/sem-corpo")

    assert resp.status_code == 200


async def test_passthrough_de_scope_nao_http():
    chamado_com: dict = {}

    async def espia(scope, receive, send) -> None:
        chamado_com["scope"] = scope

    middleware = MaxBodySizeMiddleware(espia, max_body_bytes=1_024)

    async def receive():
        return {"type": "websocket.receive"}

    enviados: list[dict] = []

    async def send(message):
        enviados.append(message)

    await middleware({"type": "websocket", "headers": []}, receive, send)

    assert chamado_com["scope"]["type"] == "websocket"
    assert enviados == []


def test_fiacao_real_413_sai_com_cabecalhos_de_cors(client):
    settings = get_settings()
    origin = settings.cors_origins_list[0]
    corpo_grande = b"x" * (settings.request_max_body_bytes + 1)

    resp = client.post(
        "/api/auth/sso/microsoft",
        content=corpo_grande,
        headers={"content-type": "application/json", "origin": origin},
    )

    assert resp.status_code == 413
    assert resp.headers.get("access-control-allow-origin") == origin
