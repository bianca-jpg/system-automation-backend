import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.errors.handlers import register_exception_handlers


def _app_com_rota_que_explode() -> FastAPI:
    app_local = FastAPI()
    register_exception_handlers(app_local)

    @app_local.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    return app_local


def test_excecao_nao_tratada_loga_traceback_e_mantem_contrato_de_resposta(caplog):
    app_local = _app_com_rota_que_explode()

    with (
        caplog.at_level(logging.ERROR),
        TestClient(app_local, raise_server_exceptions=False) as c,
    ):
        resp = c.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error", "type": "RuntimeError"}
    assert "boom" not in resp.text

    registros_com_traceback = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and record.exc_info is not None
    ]
    assert registros_com_traceback, "esperava um registro ERROR com exc_info preenchido"
    assert any(
        "boom" in record.exc_text
        for record in registros_com_traceback
        if record.exc_text
    )
