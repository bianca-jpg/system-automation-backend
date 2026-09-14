"""Regressões da fronteira DDD do bounded context Pedidos."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.modules.pedidos.application.ports import PedidosWritePort

_APPLICATION_ROOT = (
    Path(__file__).resolve().parents[1] / "modules" / "pedidos" / "application"
)


def test_application_nao_importa_sqlalchemy_infra_ou_realtime_concreto() -> None:
    violations: list[str] = []
    for path in sorted(_APPLICATION_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    if (
                        module == "sqlalchemy"
                        or module.startswith("sqlalchemy.")
                        or module.startswith("app.modules.pedidos.infrastructure")
                        or module == "app.modules.realtime"
                        or module.startswith("app.modules.realtime.")
                    ):
                        violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []


def test_write_port_e_bound_e_nao_vaza_async_session() -> None:
    for name, member in inspect.getmembers(
        PedidosWritePort, predicate=inspect.isfunction
    ):
        parameters = inspect.signature(member).parameters
        assert "db" not in parameters, f"{name} ainda exige sessão concreta"
