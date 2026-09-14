import ast
import asyncio
import inspect
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.realtime import (
    enqueue_realtime_event,
    observe_active_entity_keys_scoped,
    observe_entity_keys,
)
from app.modules.realtime.application.ports import RealtimeRepositoryPort
from app.modules.realtime.domain import (
    InvalidRealtimeEvent,
    RealtimeEventEnvelope,
    normalize_payload,
    validate_event_type,
    validate_topic,
)
from app.modules.realtime.infrastructure import repository
from app.modules.realtime.infrastructure.models import RealtimeObservedEntity
from app.modules.realtime.infrastructure.persistence import (
    SqlRealtimeRepository,
)
from app.modules.realtime.service import realtime_status
from app.shared.config.settings import Settings
from app.shared.database.session import async_session_factory


def test_realtime_application_nao_importa_sqlalchemy_nem_infrastructure():
    application_root = (
        Path(__file__).parents[1] / "modules" / "realtime" / "application"
    )
    forbidden = ("sqlalchemy", "app.modules.realtime.infrastructure")
    violations: list[str] = []

    for source_path in application_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                modules: list[str] = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                    if node.level and "infrastructure" in node.module.split("."):
                        violations.append(
                            f"{source_path.name}:{node.lineno}:relative:{node.module}"
                        )
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    if module.startswith(forbidden):
                        violations.append(f"{source_path.name}:{node.lineno}:{module}")

    assert violations == []


def test_realtime_repository_port_e_bound_a_transacao_sem_parametro_db():
    methods = [
        member
        for _name, member in inspect.getmembers(
            RealtimeRepositoryPort,
            predicate=inspect.isfunction,
        )
        if not member.__name__.startswith("_")
    ]

    assert len(methods) == 9
    assert all("db" not in inspect.signature(method).parameters for method in methods)


@pytest.mark.asyncio
async def test_adapter_converte_cursor_orm_em_valor_da_porta():
    db = AsyncMock()
    adapter = SqlRealtimeRepository(db)
    acknowledge = AsyncMock(return_value=SimpleNamespace(last_read_sequence=17))

    with patch(
        "app.modules.realtime.infrastructure.persistence.repository.acknowledge_through",
        new=acknowledge,
    ):
        result = await adapter.acknowledge_through(
            user_id=42,
            topic="orders",
            through_sequence=17,
        )

    assert result == 17
    acknowledge.assert_awaited_once_with(
        db,
        user_id=42,
        topic="orders",
        through_sequence=17,
    )


@pytest.mark.parametrize(
    ("validator", "value", "expected"),
    [
        (validate_topic, " orders ", "orders"),
        (validate_topic, "order-updates.v1", "order-updates.v1"),
        (validate_event_type, " order.created ", "order.created"),
        (validate_event_type, "stock_update-v1", "stock_update-v1"),
    ],
)
def test_identificadores_realtime_validos_sao_normalizados(validator, value, expected):
    assert validator(value) == expected


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (validate_topic, ""),
        (validate_topic, "Orders"),
        (validate_topic, "orders/created"),
        (validate_topic, "o" * 65),
        (validate_event_type, ""),
        (validate_event_type, "Order.Created"),
        (validate_event_type, "order created"),
        (validate_event_type, "e" * 129),
    ],
)
def test_identificadores_realtime_invalidos_sao_recusados(validator, value):
    with pytest.raises(InvalidRealtimeEvent):
        validator(value)


def test_normalize_payload_faz_copia_json_sem_mutar_a_entrada():
    original = {"orderId": 123, "items": [{"sku": "CAMISA-AZUL", "quantity": 2}]}

    normalized = normalize_payload(original, max_bytes=1_024)
    normalized["items"][0]["quantity"] = 99

    assert normalized is not original
    assert original["items"][0]["quantity"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"invalid": {1, 2, 3}},
        {"invalid": float("nan")},
    ],
)
def test_normalize_payload_recusa_valores_que_nao_sao_json(payload):
    with pytest.raises(InvalidRealtimeEvent, match="objeto JSON válido"):
        normalize_payload(payload, max_bytes=1_024)


def test_normalize_payload_aplica_limite_em_bytes_utf8():
    payload = {"message": "á"}

    normalized = normalize_payload(payload, max_bytes=16)
    assert normalized == payload

    with pytest.raises(InvalidRealtimeEvent, match="limite de 15 bytes"):
        normalize_payload(payload, max_bytes=15)


def test_envelope_serializa_contrato_publico_em_utc():
    event_id = UUID("3e84e878-9ae8-4fb2-85d3-bef13d93346d")
    occurred_at = datetime(
        2026,
        8,
        8,
        1,
        2,
        3,
        456000,
        tzinfo=timezone(timedelta(hours=-3)),
    )

    envelope = RealtimeEventEnvelope(
        event_id=event_id,
        sequence=42,
        event_type="order.created",
        topic="orders",
        occurred_at=occurred_at,
        payload={"orderId": 123},
    )

    assert envelope.as_dict() == {
        "version": 1,
        "eventId": str(event_id),
        "sequence": 42,
        "type": "order.created",
        "topic": "orders",
        "occurredAt": "2026-08-08T04:02:03.456000Z",
        "payload": {"orderId": 123},
    }


@pytest.mark.parametrize(
    "override",
    [
        {"sequence": 0},
        {"version": 2},
        {"topic": "Orders"},
        {"event_type": "order created"},
        # Instante naive é intencional: o domínio deve recusá-lo.
        {"occurred_at": datetime(2026, 8, 8, 4, 2, 3)},  # noqa: DTZ001
    ],
)
def test_envelope_recusa_invariantes_invalidos(override):
    values = {
        "event_id": UUID("3e84e878-9ae8-4fb2-85d3-bef13d93346d"),
        "sequence": 1,
        "event_type": "order.created",
        "topic": "orders",
        "occurred_at": datetime(2026, 8, 8, 4, 2, 3, tzinfo=UTC),
        "payload": {"orderId": 123},
        "version": 1,
    }
    values.update(override)

    with pytest.raises(InvalidRealtimeEvent):
        RealtimeEventEnvelope(**values)


@pytest.mark.asyncio
async def test_enqueue_normaliza_evento_e_delega_sem_controlar_transacao():
    db = AsyncMock()
    event_id = UUID("56af9b5d-3603-4bda-82a5-03cbd9a64db3")
    occurred_at = datetime(2026, 8, 8, 1, 2, 3, tzinfo=timezone(timedelta(hours=-3)))
    persisted = object()
    persist_event = AsyncMock(return_value=persisted)
    persistence = SimpleNamespace(persist_event=persist_event)

    with patch(
        "app.modules.realtime.service.build_realtime_repository",
        return_value=persistence,
    ) as build_repository:
        result = await enqueue_realtime_event(
            db,
            topic=" orders ",
            event_type=" order.created ",
            payload={"orderId": 123},
            occurred_at=occurred_at,
            event_id=event_id,
        )

    assert result is persisted
    build_repository.assert_called_once_with(db)
    persist_event.assert_awaited_once_with(
        event_id=event_id,
        topic="orders",
        event_type="order.created",
        occurred_at=datetime(2026, 8, 8, 4, 2, 3, tzinfo=UTC),
        payload={"orderId": 123},
    )
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_enqueue_recusa_payload_acima_do_limite_antes_do_repositorio():
    persist_event = AsyncMock()
    persistence = SimpleNamespace(persist_event=persist_event)
    settings = SimpleNamespace(realtime_payload_max_bytes=15)

    with (
        patch(
            "app.modules.realtime.application.facade.get_settings",
            return_value=settings,
        ),
        patch(
            "app.modules.realtime.service.build_realtime_repository",
            return_value=persistence,
        ),
        pytest.raises(InvalidRealtimeEvent, match="limite de 15 bytes"),
    ):
        await enqueue_realtime_event(
            AsyncMock(),
            topic="orders",
            event_type="order.created",
            payload={"message": "á"},
        )

    persist_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_observe_entity_keys_normaliza_deduplica_e_ordena():
    db = AsyncMock()
    observe_keys = AsyncMock(return_value=["10", "20"])
    persistence = SimpleNamespace(observe_keys=observe_keys)

    with patch(
        "app.modules.realtime.service.build_realtime_repository",
        return_value=persistence,
    ) as build_repository:
        result = await observe_entity_keys(
            db,
            " orders ",
            [20, " 10 ", "20", 10],
            baseline_if_empty=False,
        )

    assert result == ["10", "20"]
    build_repository.assert_called_once_with(db)
    observe_keys.assert_awaited_once_with(
        topic="orders",
        keys=["10", "20"],
        baseline_if_empty=False,
    )
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_observe_entity_keys_comporta_snapshot_erp_real_acima_de_cem_mil():
    db = AsyncMock()
    total = 132_533
    observe_keys = AsyncMock(return_value=[])
    persistence = SimpleNamespace(observe_keys=observe_keys)

    with patch(
        "app.modules.realtime.service.build_realtime_repository",
        return_value=persistence,
    ):
        result = await observe_entity_keys(
            db,
            "history",
            (f"{index}:PROD" for index in range(total)),
        )

    assert result == []
    assert observe_keys.await_args is not None
    forwarded = observe_keys.await_args.kwargs["keys"]
    assert len(forwarded) == total
    assert forwarded[0] == "0:PROD"
    assert "132532:PROD" in forwarded


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_key", ["   ", "x" * 257])
async def test_observe_entity_keys_recusa_chave_invalida_antes_do_repositorio(
    invalid_key,
):
    observe_keys = AsyncMock()
    persistence = SimpleNamespace(observe_keys=observe_keys)

    with (
        patch(
            "app.modules.realtime.service.build_realtime_repository",
            return_value=persistence,
        ),
        pytest.raises(InvalidRealtimeEvent),
    ):
        await observe_entity_keys(AsyncMock(), "orders", [invalid_key])

    observe_keys.assert_not_awaited()


@pytest.mark.asyncio
async def test_observe_active_scoped_normaliza_e_delega_sem_controlar_transacao():
    db = AsyncMock()
    observe_scoped = AsyncMock(return_value=["10:error"])
    persistence = SimpleNamespace(observe_active_keys_scoped=observe_scoped)

    with patch(
        "app.modules.realtime.service.build_realtime_repository",
        return_value=persistence,
    ) as build_repository:
        result = await observe_active_entity_keys_scoped(
            db,
            " alerts ",
            [" 10:error ", "10:error", "20:warning"],
            scope_prefixes=[20, " 10 ", 20],
        )

    assert result == ["10:error"]
    build_repository.assert_called_once_with(db)
    observe_scoped.assert_awaited_once_with(
        topic="alerts",
        keys=["10:error", "20:warning"],
        scope_prefixes=["10", "20"],
    )
    db.commit.assert_not_awaited()
    db.rollback.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "scope_prefixes", "message"),
    [
        (["10:error"], [], "scope_prefixes não pode ser vazio"),
        (["11:error"], ["10"], "não pertence a scope_prefixes"),
        (["10"], ["10"], "deve seguir o formato"),
        ([], ["10:unsafe"], "não pode conter ':'"),
    ],
)
async def test_observe_active_scoped_recusa_escopo_invalido_antes_do_repositorio(
    keys,
    scope_prefixes,
    message,
):
    observe_scoped = AsyncMock()
    persistence = SimpleNamespace(observe_active_keys_scoped=observe_scoped)

    with (
        patch(
            "app.modules.realtime.service.build_realtime_repository",
            return_value=persistence,
        ),
        pytest.raises(InvalidRealtimeEvent, match=message),
    ):
        await observe_active_entity_keys_scoped(
            AsyncMock(),
            "alerts",
            keys,
            scope_prefixes=scope_prefixes,
        )

    observe_scoped.assert_not_awaited()


@pytest.mark.asyncio
async def test_observe_active_scoped_isola_pedidos_e_detecta_recorrencia_no_banco():
    topic = f"scoped-{uuid4().hex}"
    first_scope = f"{uuid4().int % 1_000_000_000}"
    second_scope = f"{uuid4().int % 1_000_000_000}"
    first_key = f"{first_scope}:error"
    second_key = f"{second_scope}:warning"

    async with async_session_factory() as db:
        try:
            entered = await observe_active_entity_keys_scoped(
                db,
                topic,
                [first_key, second_key],
                scope_prefixes=[first_scope, second_scope],
            )
            assert entered == sorted([first_key, second_key])

            resolved = await observe_active_entity_keys_scoped(
                db,
                topic,
                [],
                scope_prefixes=[first_scope],
            )
            assert resolved == []
            first_row = await db.get(RealtimeObservedEntity, (topic, first_key))
            second_row = await db.get(RealtimeObservedEntity, (topic, second_key))
            assert first_row is not None and first_row.active is False
            assert second_row is not None and second_row.active is True

            recurrent = await observe_active_entity_keys_scoped(
                db,
                topic,
                [first_key],
                scope_prefixes=[first_scope],
            )
            assert recurrent == [first_key]
            await db.refresh(first_row)
            assert first_row.active is True
            assert second_row.active is True
        finally:
            await db.rollback()


def _settings(**overrides: object) -> Settings:
    # `_env_file` é um kwarg especial de `BaseSettings.__init__` (não um campo
    # do modelo); o pyright sintetiza o __init__ a partir dos campos e não
    # reconhece esse parâmetro, mesmo sendo válido em runtime. Confinado a
    # esta única linha do helper.
    return Settings(_env_file=None, **overrides)  # pyright: ignore[reportCallIssue]


def test_database_url_codifica_usuario_e_senha_com_caracteres_reservados(
    monkeypatch,
):
    # O teste precisa ser hermético mesmo quando o runner injeta DEV_POSTGRES_*.
    # AliasChoices dá precedência ao alias específico do ambiente, então limpar
    # somente POSTGRES_* não basta.
    for prefix in ("POSTGRES", "DEV_POSTGRES", "PROD_POSTGRES", "TEST_POSTGRES"):
        for suffix in ("USER", "PASSWORD", "DB", "HOST", "PORT"):
            monkeypatch.delenv(f"{prefix}_{suffix}", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    settings = _settings(
        POSTGRES_USER="user@:/#",
        POSTGRES_PASSWORD="pass@:/#",
        POSTGRES_DB="system_automation",
        POSTGRES_HOST="localhost",
        POSTGRES_PORT="5432",
    )

    assert settings.database_url == (
        "postgresql+asyncpg://user%40%3A%2F%23:"
        "pass%40%3A%2F%23@localhost:5432/system_automation"
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        (
            "postgresql://encoded:user@db.internal:5432/automation",
            "postgresql+asyncpg://encoded:user@db.internal:5432/automation",
        ),
        (
            "postgresql+asyncpg://encoded:user@db.internal:5432/automation",
            "postgresql+asyncpg://encoded:user@db.internal:5432/automation",
        ),
    ],
)
def test_database_url_override_preserva_conteudo_e_normaliza_so_o_driver(
    override,
    expected,
):
    settings = _settings(DATABASE_URL=override)

    assert settings.database_url == expected


@pytest.mark.asyncio
async def test_persist_event_retry_idempotente_retorna_existente_sem_avancar_estado():
    event_id = UUID("662178aa-1708-455a-b5f4-3aef268caf77")
    occurred_at = datetime(2026, 8, 8, 4, 2, 3, tzinfo=UTC)
    existing = SimpleNamespace(
        event_id=event_id,
        sequence=12,
        event_type="order.created",
        topic="orders",
        occurred_at=occurred_at,
        payload={"orderId": 123},
        version=1,
    )
    db = AsyncMock()
    db.scalar.return_value = existing
    locked_topic_state = AsyncMock()

    with patch(
        "app.modules.realtime.infrastructure.repository._locked_topic_state",
        new=locked_topic_state,
    ):
        envelope = await repository.persist_event(
            db,
            event_id=event_id,
            topic="orders",
            event_type="order.created",
            occurred_at=occurred_at,
            payload={"orderId": 123},
        )

    assert envelope.as_dict()["sequence"] == 12
    assert envelope.as_dict()["eventId"] == str(event_id)
    locked_topic_state.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_event_retry_divergente_recusa_reuso_do_event_id():
    event_id = UUID("662178aa-1708-455a-b5f4-3aef268caf77")
    occurred_at = datetime(2026, 8, 8, 4, 2, 3, tzinfo=UTC)
    existing = SimpleNamespace(
        event_id=event_id,
        sequence=12,
        event_type="order.created",
        topic="orders",
        occurred_at=occurred_at,
        payload={"orderId": 123},
        version=1,
    )
    db = AsyncMock()
    db.scalar.return_value = existing
    locked_topic_state = AsyncMock()

    with (
        patch(
            "app.modules.realtime.infrastructure.repository._locked_topic_state",
            new=locked_topic_state,
        ),
        pytest.raises(InvalidRealtimeEvent, match="event_id já existe"),
    ):
        await repository.persist_event(
            db,
            event_id=event_id,
            topic="orders",
            event_type="order.created",
            occurred_at=occurred_at,
            payload={"orderId": 999},
        )

    locked_topic_state.assert_not_awaited()
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_outbox_respeita_head_of_line_durante_backoff():
    db = AsyncMock()
    db.scalars.return_value = SimpleNamespace(all=list)

    claimed = await repository.claim_outbox_batch(
        db,
        instance_id="instance-a",
        batch_size=100,
    )

    assert claimed == []
    statement = db.scalars.await_args.args[0]
    sql = " ".join(
        str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        .lower()
        .split()
    )
    assert "realtime_outbox.sequence = (select min(realtime_outbox.sequence)" in sql
    assert "realtime_outbox.next_attempt_at <=" in sql
    assert "limit 1" in sql


@pytest.mark.asyncio
async def test_replay_trava_watermark_compartilhado_contra_cleanup():
    db = AsyncMock()
    db.scalar.return_value = 10
    db.execute.return_value = SimpleNamespace(all=list)
    db.scalars.return_value = SimpleNamespace(all=list)

    await repository.replay_events(
        db,
        topics=["orders"],
        after_sequence=5,
        through_sequence=10,
        limit=100,
    )

    floor_statement = db.execute.await_args.args[0]
    sql = str(floor_statement.compile(dialect=postgresql.dialect())).upper()
    assert "FOR SHARE" in sql
    replay_statement = db.scalars.await_args.args[0]
    replay_sql = str(replay_statement.compile(dialect=postgresql.dialect())).lower()
    assert "realtime_outbox.sequence <=" in replay_sql


@pytest.mark.asyncio
async def test_realtime_status_preserva_watermark_global_ao_filtrar_topicos():
    db = AsyncMock()
    statuses = {
        "orders": {
            "latestSequence": 7,
            "lastReadSequence": 5,
            "unseen": 2,
            "latest": None,
        }
    }
    get_topic_statuses = AsyncMock(return_value=statuses)
    get_global_latest_sequence = AsyncMock(return_value=42)
    persistence = SimpleNamespace(
        get_topic_statuses=get_topic_statuses,
        get_global_latest_sequence=get_global_latest_sequence,
    )

    with patch(
        "app.modules.realtime.service.build_realtime_repository",
        return_value=persistence,
    ) as build_repository:
        result = await realtime_status(db, user_id=99, topics=["orders"])

    assert result == {"lastSequence": 42, "topics": statuses}
    build_repository.assert_called_once_with(db)
    get_topic_statuses.assert_awaited_once_with(
        user_id=99,
        topics=["orders"],
    )
    get_global_latest_sequence.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_scoped_lock_global_antes_do_topic_evita_deadlock_em_duas_sessoes():
    """Reproduz a antiga inversão topic->global contra global->topic."""

    topic = f"lock-{uuid4().hex}"
    second_started = asyncio.Event()

    async def persist_in_second_session() -> None:
        async with async_session_factory() as second_db:
            second_started.set()
            await enqueue_realtime_event(
                second_db,
                topic=topic,
                event_type="lock.second",
                payload={"source": "second"},
            )
            await second_db.commit()

    task: asyncio.Task[None] | None = None
    try:
        async with async_session_factory() as first_db:
            await observe_active_entity_keys_scoped(
                first_db,
                topic,
                ["1:error"],
                scope_prefixes=["1"],
            )
            task = asyncio.create_task(persist_in_second_session())
            await second_started.wait()
            # Dá à segunda sessão oportunidade de disputar o lock. Na ordem
            # antiga ela segurava global aguardando topic, fechando o ciclo.
            await asyncio.sleep(0.05)
            await enqueue_realtime_event(
                first_db,
                topic=topic,
                event_type="lock.first",
                payload={"source": "first"},
            )
            await first_db.commit()
        await asyncio.wait_for(task, timeout=5)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
