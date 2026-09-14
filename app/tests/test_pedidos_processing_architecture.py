from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.modules.pedidos.processing.application.ports import (
    ProcessingPlannerSource,
    ProcessingRealtime,
    ProcessingRepository,
    ProcessingWriter,
)

_APPLICATION_ROOT = (
    Path(__file__).resolve().parents[1]
    / "modules"
    / "pedidos"
    / "processing"
    / "application"
)


def test_processing_application_does_not_import_infrastructure_or_sqlalchemy() -> None:
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
                        module.startswith(("sqlalchemy", "app.modules.realtime"))
                        or ".infrastructure" in module
                    ):
                        violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []


def test_processing_ports_are_bound_and_do_not_leak_db_session() -> None:
    for port in (
        ProcessingPlannerSource,
        ProcessingRealtime,
        ProcessingRepository,
        ProcessingWriter,
    ):
        for name, member in inspect.getmembers(port, predicate=inspect.isfunction):
            parameters = inspect.signature(member).parameters
            assert "db" not in parameters, f"{port.__name__}.{name} leaks db"
