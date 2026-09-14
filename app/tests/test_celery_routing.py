import re
from pathlib import Path

import pytest

from app.workers.celery_app import celery_app

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILES = (
    _BACKEND_ROOT / ".docker" / "docker-compose.yml",
    _BACKEND_ROOT / ".docker" / "docker-compose.prod.yml",
)


def _service_block(path: Path, service: str) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [A-Za-z0-9_]+:\n|"
        r"^networks:\n|^volumes:\n|\Z)",
        content,
    )
    assert match is not None, f"service {service!r} missing from {path.name}"
    return match.group(1)


def test_processamento_pedidos_tem_rota_exclusiva():
    assert celery_app.conf.task_default_queue == "celery"
    assert celery_app.conf.task_routes[
        "app.workers.tasks.pedidos.processar_pedidos"
    ] == {"queue": "orders_processing"}


@pytest.mark.parametrize("compose_path", _COMPOSE_FILES, ids=lambda path: path.name)
def test_worker_geral_nao_consumira_fila_pesada(compose_path: Path):
    worker = _service_block(compose_path, "celery_worker")

    assert "--queues=celery" in worker
    assert "orders_processing" not in worker


@pytest.mark.parametrize("compose_path", _COMPOSE_FILES, ids=lambda path: path.name)
def test_worker_orders_e_isolado_e_reciclavel(compose_path: Path):
    worker = _service_block(compose_path, "celery_orders_worker")

    assert "--queues=orders_processing" in worker
    assert "--concurrency=1" in worker
    assert "--prefetch-multiplier=1" in worker
    assert "--max-tasks-per-child=" in worker
    assert "--max-memory-per-child=" in worker
    assert "mem_reservation:" in worker
    assert "1280m" in worker
    assert "mem_limit:" in worker
    assert "1536m" in worker


def test_env_documenta_limites_do_worker_orders():
    env_example = (_BACKEND_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "ORDERS_PROCESSING_MAX_TASKS_PER_CHILD=20" in env_example
    assert "ORDERS_PROCESSING_MAX_MEMORY_PER_CHILD_KB=1048576" in env_example
    assert "ORDERS_PROCESSING_MEMORY_RESERVATION=1280m" in env_example
    assert "ORDERS_PROCESSING_MEMORY_LIMIT=1536m" in env_example
