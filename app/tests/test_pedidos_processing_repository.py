from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Table, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.ingestao.infrastructure.models import (
    Estoque,
    Pedido,
    PedidoProcessadoErp,
)
from app.modules.pedidos.infrastructure.models import (
    EstoqueVirtual,
    OrdemReserva,
    OrdemReservaLinx,
    PedidoProcessado,
    PedidoStandbyMotivo,
)
from app.modules.pedidos.processing.application import service as processing_service
from app.modules.pedidos.processing.application.ports import ProcessingPlannerSource
from app.modules.pedidos.processing.application.service import (
    claim_processing_job,
    submit_processing_job,
)
from app.modules.pedidos.processing.domain import (
    APPLY_CHUNK_SIZE,
    PROCESSING_LEASE,
    InvalidProcessingRequest,
    PendingSnapshotStats,
    PlanDraft,
    PlannedPair,
    PlanningState,
    ProcessingChannel,
    ProcessingLockUnavailable,
    ProcessingMode,
    ProcessingPlanConflict,
    ProcessingRequest,
    ProcessingSpec,
    build_processing_plan,
    fingerprint_pair_source,
)
from app.modules.pedidos.processing.infrastructure import (
    repository_read as repository_module,
)
from app.modules.pedidos.processing.infrastructure.adapters import (
    SqlAlchemyProcessingPlannerSource,
)
from app.modules.pedidos.processing.infrastructure.models import (
    PedidoProcessamentoPlanModel,
)
from app.modules.pedidos.processing.infrastructure.repository_read import (
    SqlAlchemyProcessingRepository,
)
from app.modules.pedidos.processing.infrastructure.repository_writer import (
    SqlAlchemyProcessingWriter,
)
from app.modules.pedidos.processing.infrastructure.session_lock import (
    PostgresOrderMutationSessionLock,
    SqlAlchemyProcessingLeaseKeeper,
)
from app.modules.pedidos.processing.infrastructure.unit_of_work import (
    SqlAlchemyProcessingUnitOfWork,
)
from app.shared.config.settings import get_settings
from app.shared.database.advisory_locks import ORDER_STATE_MUTATION_LOCK
from app.shared.jobs.application.ports import DurableJobRepository
from app.shared.jobs.domain import JobStatus, NewJob, fingerprint_payload
from app.shared.jobs.infrastructure.models import DurableJobModel
from app.shared.jobs.infrastructure.repository import (
    SqlAlchemyDurableJobRepository,
    SqlAlchemyDurableJobUnitOfWork,
)


@pytest.fixture
async def processing_database():
    database_url = get_settings().database_url
    assert database_url.rsplit("/", 1)[-1].endswith("_test")
    engine = create_async_engine(database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def cleanup() -> None:
        async with factory() as db:
            for model in (
                PedidoStandbyMotivo,
                OrdemReservaLinx,
                EstoqueVirtual,
                PedidoProcessado,
                OrdemReserva,
                PedidoProcessadoErp,
                Pedido,
                Estoque,
                DurableJobModel,
            ):
                await db.execute(delete(model))
            await db.commit()

    await cleanup()
    try:
        yield engine, factory
    finally:
        await cleanup()
        await engine.dispose()


@pytest.mark.parametrize("app_skew", [timedelta(days=-1), timedelta(days=1)])
async def test_submit_processing_anchors_deadline_and_dispatches_under_clock_skew(
    processing_database,
    app_skew: timedelta,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        before_submit = await db.scalar(select(func.clock_timestamp()))
        assert isinstance(before_submit, datetime)
        requested_at = before_submit + app_skew
        dispatcher = AsyncMock()
        submission = await submit_processing_job(
            jobs=SqlAlchemyDurableJobRepository(db),
            processing=SqlAlchemyProcessingRepository(db),
            unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
            dispatcher=dispatcher,
            owner_id=42,
            idempotency_key=f"clock-skew-{int(app_skew.total_seconds())}",
            request=ProcessingRequest(
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=ProcessingChannel.TODOS,
            ),
            requested_at=requested_at,
        )
        after_submit = await db.scalar(select(func.clock_timestamp()))
        assert isinstance(after_submit, datetime)
        dispatchable = await SqlAlchemyDurableJobRepository(db).list_dispatchable(
            now=requested_at,
            limit=10,
        )

    job = submission.reservation.job
    assert submission.broker_enqueued is True
    dispatcher.enqueue.assert_awaited_once_with(
        job_id=job.id,
        kind="orders.processing.v1",
    )
    assert before_submit <= job.requested_at <= after_submit
    assert job.available_at == job.updated_at == job.requested_at
    assert job.deadline_at is not None
    assert before_submit + timedelta(minutes=15) <= job.deadline_at
    assert job.deadline_at <= after_submit + timedelta(minutes=15)
    assert [candidate.id for candidate in dispatchable] == [job.id]


def _source_item(*, qty: int = 2) -> dict:
    return {
        "nr_pedido": 101,
        "cd_prod_cor": "PROD|01",
        "sg_tamanho": "36",
        "ds_grupo": "GRUPO",
        "qt_liquida": qty,
        "vl_liquido": float(qty * 20),
        "status_credito": "Com Crédito",
        "client": "Cliente protegido",
        "canal": "Franquia",
        "ds_produto": "Produto",
        "data": "2026-08-08",
        "indica_blacklist": False,
    }


def _planned_pair(*, tipo: str = "sem", qty: int = 2) -> PlannedPair:
    source = _source_item(qty=qty)
    item = dict(source)
    if tipo == "com":
        item["status_item"] = "Gerar OR"
        item["qt_solicitada"] = qty
        item["diff_valor"] = 0.0
    return PlannedPair(
        ordinal=1,
        nr_pedido=101,
        cd_prod_cor="PROD|01",
        payload={
            "type": tipo,
            "channel": "Franquia",
            "items": [item],
            "sourceHash": fingerprint_pair_source([source]),
            "linx": None,
            "stockTargets": [["PROD|01", "36", "Franquia"]],
        },
    )


async def _create_job_and_plan(
    db: AsyncSession,
    pair: PlannedPair,
) -> tuple[SqlAlchemyProcessingRepository, ProcessingSpec]:
    now = datetime.now(UTC)
    reservation = await SqlAlchemyDurableJobRepository(db).reserve(
        NewJob(
            kind="orders.processing.v1",
            owner_id=42,
            scope_key="sem_adequar:Franquia",
            idempotency_digest=None,
            fingerprint=fingerprint_payload({"test": "processing"}),
            requested_at=now,
            deadline_at=now + timedelta(hours=1),
        )
    )
    repository = SqlAlchemyProcessingRepository(db)
    await repository.create_spec(
        job_id=reservation.job.id,
        mode=ProcessingMode.SEM_ADEQUAR,
        channel=ProcessingChannel.FRANQUIA,
    )
    spec = await repository.store_plan(
        job_id=reservation.job.id,
        draft=PlanDraft(
            rows=(pair,),
            candidate_count=1,
            deferred_count=0,
            blocked_credit_count=0,
            plan_hash="a" * 64,
        ),
        planned_at=now,
    )
    return repository, spec


async def _create_job_and_store_draft(
    db: AsyncSession,
    draft: PlanDraft,
    *,
    mode: ProcessingMode,
    channel: ProcessingChannel = ProcessingChannel.FRANQUIA,
    scope_key: str = "adequar:Franquia",
) -> tuple[SqlAlchemyProcessingRepository, ProcessingSpec]:
    """Irmão de `_create_job_and_plan`: persiste um `PlanDraft` já construído
    por `build_processing_plan` (não um único `PlannedPair` fabricado à mão).
    Não altera `_create_job_and_plan`, usado por outros 6 testes."""

    now = datetime.now(UTC)
    reservation = await SqlAlchemyDurableJobRepository(db).reserve(
        NewJob(
            kind="orders.processing.v1",
            owner_id=42,
            scope_key=scope_key,
            idempotency_digest=None,
            fingerprint=fingerprint_payload(
                {"test": "processing-draft", "k": scope_key}
            ),
            requested_at=now,
            deadline_at=now + timedelta(hours=1),
        )
    )
    repository = SqlAlchemyProcessingRepository(db)
    await repository.create_spec(
        job_id=reservation.job.id,
        mode=mode,
        channel=channel,
    )
    spec = await repository.store_plan(
        job_id=reservation.job.id,
        draft=draft,
        planned_at=now,
    )
    return repository, spec


def _table(model: Any) -> Table:
    """`Model.__table__` é tipado como `FromClause` pelo pyright; os testes
    usam `.insert()`, que só existe em `Table`. Os modelos passados aqui são
    sempre declarative models reais (nunca outra FromClause), então isto é
    só uma correção de tipo, sem efeito em runtime."""
    return cast(Table, model.__table__)


async def _seed_source(db: AsyncSession, *, stock_qty: int = 20) -> None:
    await db.execute(
        _table(Pedido)
        .insert()
        .values(
            nr_pedido=101,
            cd_prod_cor="PROD|01",
            sg_tamanho="36",
            ds_grupo="GRUPO",
            qt_entregar=2,
            vl_liquido=40,
            client="Cliente protegido",
            canal="Franquia",
            status_credito="Com Crédito",
            ds_produto="Produto",
            data="2026-08-08",
        )
    )
    await db.execute(
        _table(Estoque)
        .insert()
        .values(
            cd_prod_cor="PROD|01",
            sg_tamanho="36",
            canal="Franquia",
            qt_disponivel=stock_qty,
            dt_estoque=datetime(2026, 8, 8, tzinfo=UTC).date(),
        )
    )


async def test_plan_checkpoint_is_sequential_and_resumable(processing_database) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        repository, spec = await _create_job_and_plan(db, _planned_pair())
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        checkpoint = await repository.checkpoint_chunk(
            job_id=spec.job_id,
            rows=chunk,
            applied_at=datetime.now(UTC),
        )
        await db.commit()

    assert checkpoint.applied_count == 1
    assert checkpoint.next_ordinal == 2
    async with factory() as db:
        repository = SqlAlchemyProcessingRepository(db)
        assert await repository.load_next_chunk(job_id=spec.job_id, limit=250) == ()


async def test_store_plan_materializes_at_most_one_insert_chunk(
    processing_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, factory = processing_database
    chunk_sizes: list[int] = []
    original_chunks = repository_module._chunks

    def observed_chunks(values, size):
        for chunk in original_chunks(values, size):
            chunk_sizes.append(len(chunk))
            yield chunk

    monkeypatch.setattr(repository_module, "_chunks", observed_chunks)
    now = datetime.now(UTC)
    rows = tuple(
        PlannedPair(
            ordinal=ordinal,
            nr_pedido=ordinal,
            cd_prod_cor=f"PROD-{ordinal}",
            payload={"ordinal": ordinal},
        )
        for ordinal in range(1, 502)
    )

    async with factory() as db:
        reservation = await SqlAlchemyDurableJobRepository(db).reserve(
            NewJob(
                kind="orders.processing.v1",
                owner_id=42,
                scope_key="sem_adequar:Todos",
                fingerprint=fingerprint_payload({"test": "chunked-plan-store"}),
                requested_at=now,
                deadline_at=now + timedelta(minutes=15),
            )
        )
        repository = SqlAlchemyProcessingRepository(db)
        await repository.create_spec(
            job_id=reservation.job.id,
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.TODOS,
        )
        await repository.store_plan(
            job_id=reservation.job.id,
            draft=PlanDraft(
                rows=rows,
                candidate_count=len(rows),
                deferred_count=0,
                blocked_credit_count=0,
                plan_hash="d" * 64,
            ),
            planned_at=now,
        )
        await db.commit()

    assert chunk_sizes == [250, 250, 1]


# --- record_standby_reasons (16-04) ---------------------------------------


async def test_record_standby_reasons_upserts_and_increments_execucoes_consecutivas(
    processing_database,
) -> None:
    """Três chamadas sucessivas sobre o MESMO par provam STANDBY-06/critério 2
    do ROADMAP: o upsert nunca insere uma segunda linha (COUNT continua 1) e
    o contador é agnóstico ao motivo (sem_estoque -> sem_credito -> furo_grade,
    1 -> 2 -> 3, sem resetar ao trocar de motivo — A2 do 16-RESEARCH.md).
    Nenhum `DurableJob` é reservado: `job_id` é só informativo, sem FK
    (decisão do 16-02), então `uuid4()` basta.
    """

    _engine, factory = processing_database
    job_a, job_b, job_c = uuid4(), uuid4(), uuid4()
    t1 = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
    t2 = datetime(2026, 8, 8, 12, 0, 1, tzinfo=UTC)
    t3 = datetime(2026, 8, 8, 12, 0, 2, tzinfo=UTC)

    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=job_a,
            deferred_pairs=((101, "PROD|01", "Franquia", "sem_estoque"),),
            blocked_credit_pairs=(),
            recorded_at=t1,
        )
        await db.commit()

    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 1
        row = await db.scalar(select(PedidoStandbyMotivo))
        assert row.execucoes_consecutivas == 1
        assert row.motivo == "sem_estoque"
        assert row.job_id == job_a
        assert row.atualizado_em == t1

    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=job_b,
            deferred_pairs=(),
            blocked_credit_pairs=((101, "PROD|01", "Franquia"),),
            recorded_at=t2,
        )
        await db.commit()

    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 1
        row = await db.scalar(select(PedidoStandbyMotivo))
        assert row.execucoes_consecutivas == 2
        assert row.motivo == "sem_credito"
        assert row.job_id == job_b
        assert row.atualizado_em == t2

    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=job_c,
            deferred_pairs=((101, "PROD|01", "Franquia", "furo_grade"),),
            blocked_credit_pairs=(),
            recorded_at=t3,
        )
        await db.commit()

    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 1
        row = await db.scalar(select(PedidoStandbyMotivo))
        assert row.execucoes_consecutivas == 3
        assert row.motivo == "furo_grade"
        assert row.job_id == job_c
        assert row.atualizado_em == t3


async def test_record_standby_reasons_fans_out_credit_block_per_product(
    processing_database,
) -> None:
    """UMA chamada com os dois grupos juntos: prova K2 (fan-out por PRODUTO
    elegível do pedido bloqueado por crédito — crédito é do cliente, não do
    produto — nunca 1 linha por pedido) e que os dois grupos convivem no
    MESMO INSERT sem colidir.
    """

    _engine, factory = processing_database
    job_id = uuid4()
    recorded_at = datetime(2026, 8, 8, 12, tzinfo=UTC)

    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=job_id,
            deferred_pairs=((303, "D", "Multimarca", "furo_grade"),),
            blocked_credit_pairs=(
                (202, "C1", "Franquia"),
                (202, "C2", "Franquia"),
            ),
            recorded_at=recorded_at,
        )
        await db.commit()

    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 3
        credit_rows = (
            await db.scalars(
                select(PedidoStandbyMotivo)
                .where(PedidoStandbyMotivo.nr_pedido == 202)
                .order_by(PedidoStandbyMotivo.cd_prod_cor)
            )
        ).all()
        assert [row.cd_prod_cor for row in credit_rows] == ["C1", "C2"]
        assert all(row.motivo == "sem_credito" for row in credit_rows)
        assert all(row.execucoes_consecutivas == 1 for row in credit_rows)
        deferred_row = await db.scalar(
            select(PedidoStandbyMotivo).where(PedidoStandbyMotivo.nr_pedido == 303)
        )
        assert deferred_row is not None
        assert deferred_row.motivo == "furo_grade"


async def test_record_standby_reasons_chunks_beyond_apply_chunk_size(
    processing_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem chunking, um plano grande (até `PLAN_PAIR_LIMIT` pares) estouraria
    o teto de 65535 bind parameters do protocolo Postgres num único INSERT e
    abortaria a transação de planejamento inteira, levando o PLANO junto
    (T-16-04-03). `APPLY_CHUNK_SIZE + 1` pares distintos numa única chamada
    provam que o chunking preserva todos eles.
    """

    _engine, factory = processing_database
    chunk_sizes: list[int] = []
    original_chunks = repository_module._chunks

    def observed_chunks(values, size):
        for chunk in original_chunks(values, size):
            chunk_sizes.append(len(chunk))
            yield chunk

    monkeypatch.setattr(repository_module, "_chunks", observed_chunks)
    total = APPLY_CHUNK_SIZE + 1
    deferred_pairs = tuple(
        (ordinal, "X", "Franquia", "sem_estoque") for ordinal in range(1, total + 1)
    )

    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=uuid4(),
            deferred_pairs=deferred_pairs,
            blocked_credit_pairs=(),
            recorded_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        )
        await db.commit()

    assert chunk_sizes == [APPLY_CHUNK_SIZE, 1]
    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == total


async def test_record_standby_reasons_is_a_no_op_without_pairs(
    processing_database,
) -> None:
    """Guarda o caminho comum de um plano onde tudo foi selecionado (nenhum
    par em stand-by): um `pg_insert().values([])` seria erro de compilação de
    SQL, então o método precisa retornar antes de montar o INSERT.
    """

    _engine, factory = processing_database
    async with factory() as db:
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=uuid4(),
            deferred_pairs=(),
            blocked_credit_pairs=(),
            recorded_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
        )
        await db.commit()

    async with factory() as db:
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 0


async def test_record_standby_reasons_rejects_duplicate_pair_in_the_same_call(
    processing_database,
) -> None:
    """O mesmo par `(1, "A")` em `deferred_pairs` E `blocked_credit_pairs` na
    mesma chamada levanta `ProcessingPlanConflict` antes de montar o INSERT.
    A guarda `standby_pair_in_both_groups` de `PlanDraft.__post_init__`
    (16-03) já torna isso inalcançável pelo caminho de
    `build_processing_plan`, mas o port aceita `Sequence` arbitrária — sem
    esta guarda o Postgres levantaria um erro opaco ("ON CONFLICT DO UPDATE
    command cannot affect row a second time") longe da causa real.
    """

    _engine, factory = processing_database
    async with factory() as db:
        with pytest.raises(ProcessingPlanConflict, match="standby_duplicate_pair"):
            await SqlAlchemyProcessingRepository(db).record_standby_reasons(
                job_id=uuid4(),
                deferred_pairs=((1, "A", "Franquia", "sem_estoque"),),
                blocked_credit_pairs=((1, "A", "Franquia"),),
                recorded_at=datetime(2026, 8, 8, 12, tzinfo=UTC),
            )


async def test_writer_applies_pair_and_checkpoint_in_same_transaction(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        pair = _planned_pair()
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()

        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await repository.checkpoint_chunk(
            job_id=spec.job_id,
            rows=chunk,
            applied_at=datetime.now(UTC),
        )
        await db.commit()

    async with factory() as db:
        order = await db.scalar(select(OrdemReserva))
        assert order is not None and order.itens[0]["sg_tamanho"] == "36"
        assert await db.scalar(select(PedidoProcessado.nr_pedido)) == 101
        assert await db.scalar(select(EstoqueVirtual.qt_disponivel)) == 18


async def test_apply_pairs_removes_standby_row_of_the_pair_that_became_or(
    processing_database,
) -> None:
    """`apply_pairs` apaga, na MESMA transação da OR, a linha de
    `pedido_standby_motivo` do par que acabou de virar OR (critério 3 do
    ROADMAP). A linha de CONTROLE de um par diferente, que continua
    legitimamente em stand-by, sobrevive intacta com motivo e contador
    inalterados — o DELETE é escopado pelo par COMPOSTO, nunca um wipe.
    """

    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        await SqlAlchemyProcessingRepository(db).record_standby_reasons(
            job_id=uuid4(),
            deferred_pairs=(
                (101, "PROD|01", "Franquia", "sem_estoque"),
                (999, "OUTRO|99", "Franquia", "sem_credito"),
            ),
            blocked_credit_pairs=(),
            recorded_at=datetime.now(UTC),
        )
        pair = _planned_pair()
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()

        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await repository.checkpoint_chunk(
            job_id=spec.job_id,
            rows=chunk,
            applied_at=datetime.now(UTC),
        )
        await db.commit()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) == 101
        applied_row = await db.scalar(
            select(PedidoStandbyMotivo).where(
                PedidoStandbyMotivo.nr_pedido == 101,
                PedidoStandbyMotivo.cd_prod_cor == "PROD|01",
            )
        )
        assert applied_row is None
        control_row = await db.scalar(
            select(PedidoStandbyMotivo).where(
                PedidoStandbyMotivo.nr_pedido == 999,
                PedidoStandbyMotivo.cd_prod_cor == "OUTRO|99",
            )
        )
        assert control_row is not None
        assert control_row.motivo == "sem_credito"
        assert control_row.execucoes_consecutivas == 1


async def test_apply_pairs_is_idempotent_when_the_pair_has_no_standby_row(
    processing_database,
) -> None:
    """A maioria dos pares que viram OR nunca esteve em stand-by: o DELETE de
    0 linhas é inofensivo, e `apply_pairs` segue o caminho normal sem
    levantar. Guarda de não-regressão do caso comum.
    """

    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        pair = _planned_pair()
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()

        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await repository.checkpoint_chunk(
            job_id=spec.job_id,
            rows=chunk,
            applied_at=datetime.now(UTC),
        )
        await db.commit()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) == 101
        assert (
            await db.scalar(select(func.count()).select_from(PedidoStandbyMotivo))
        ) == 0


async def test_writer_rolls_back_all_outputs_before_checkpoint(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        pair = _planned_pair()
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) is None
        assert await db.scalar(select(PedidoProcessado.nr_pedido)) is None
        repository = SqlAlchemyProcessingRepository(db)
        assert len(await repository.load_next_chunk(job_id=spec.job_id, limit=250)) == 1


async def test_retry_after_source_mutation_fails_stale_before_any_write(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        repository, spec = await _create_job_and_plan(db, _planned_pair())
        await db.commit()
        await db.execute(update(Pedido).values(qt_entregar=3, vl_liquido=60))
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        with pytest.raises(ProcessingPlanConflict, match="processing_plan_stale"):
            await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) is None
        assert await db.scalar(select(PedidoProcessado.nr_pedido)) is None


async def test_adequation_revalidates_stock_before_writes(processing_database) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db, stock_qty=1)
        pair = _planned_pair(tipo="com", qty=2)
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        with pytest.raises(ProcessingPlanConflict, match="processing_plan_stale"):
            await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) is None


async def test_sem_adequar_revalidates_stock_before_writes(processing_database) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db, stock_qty=1)
        pair = _planned_pair(tipo="sem", qty=2)
        repository, spec = await _create_job_and_plan(db, pair)
        await db.commit()
        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        with pytest.raises(ProcessingPlanConflict, match="processing_plan_stale"):
            await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(OrdemReserva.nr_pedido)) is None


async def test_session_lock_excludes_second_connection_and_releases_on_close(
    processing_database,
) -> None:
    engine, _factory = processing_database
    async with PostgresOrderMutationSessionLock(engine) as first:
        await first.ensure_held()
        with pytest.raises(
            ProcessingLockUnavailable,
            match="order_state_mutation_lock_busy",
        ):
            async with PostgresOrderMutationSessionLock(engine):
                pass

    async with PostgresOrderMutationSessionLock(engine) as after_release:
        await after_release.ensure_held()

    connection = await engine.connect()
    assert await connection.scalar(
        text("SELECT pg_try_advisory_lock(:key)"),
        {"key": ORDER_STATE_MUTATION_LOCK},
    )
    await connection.close()  # connection loss releases every session advisory lock
    async with PostgresOrderMutationSessionLock(engine) as after_crash:
        await after_crash.ensure_held()


async def test_lock_contention_does_not_consume_job_attempts(
    processing_database,
) -> None:
    engine, factory = processing_database
    now = datetime.now(UTC)
    async with factory() as db:
        reservation = await SqlAlchemyDurableJobRepository(db).reserve(
            NewJob(
                kind="orders.processing.v1",
                owner_id=42,
                scope_key="sem_adequar:Multimarca",
                fingerprint=fingerprint_payload({"test": "lock-contention"}),
                requested_at=now,
                deadline_at=now + timedelta(minutes=15),
            )
        )
        await db.commit()
        job_id = reservation.job.id

    async with PostgresOrderMutationSessionLock(engine):
        for _attempt in range(4):
            with pytest.raises(
                ProcessingLockUnavailable,
                match="order_state_mutation_lock_busy",
            ):
                async with PostgresOrderMutationSessionLock(engine):
                    pass

        async with factory() as db:
            untouched = await SqlAlchemyDurableJobRepository(db).get(job_id)
            assert untouched is not None
            assert untouched.status is JobStatus.QUEUED
            assert untouched.attempts == 0

    async with PostgresOrderMutationSessionLock(engine) as acquired:
        db = acquired.session
        claimed = await claim_processing_job(
            job_id=job_id,
            worker_id="worker-after-lock",
            jobs=SqlAlchemyDurableJobRepository(db),
            lock_guard=acquired,
            unit_of_work=SqlAlchemyDurableJobUnitOfWork(db),
            now=lambda: now + timedelta(seconds=1),
        )
        assert claimed is not None
        assert claimed.status is JobStatus.RUNNING
        assert claimed.attempts == 1


async def test_lease_keeper_accepts_foreground_progress_race(
    processing_database,
) -> None:
    _engine, factory = processing_database
    now = datetime.now(UTC)
    async with factory() as db:
        reservation = await SqlAlchemyDurableJobRepository(db).reserve(
            NewJob(
                kind="orders.processing.v1",
                owner_id=42,
                scope_key="adequar:Multimarca",
                fingerprint=fingerprint_payload({"test": "lease-race"}),
                requested_at=now,
                deadline_at=now + timedelta(minutes=15),
            )
        )
        repository = SqlAlchemyDurableJobRepository(db)
        claimed = await repository.claim(
            job_id=reservation.job.id,
            kinds=("orders.processing.v1",),
            worker_id="worker-race",
            now=now,
            lease_until=now + PROCESSING_LEASE,
        )
        assert claimed is not None
        await db.commit()

        foreground = await repository.heartbeat(
            job_id=claimed.id,
            worker_id="worker-race",
            now=now + timedelta(seconds=1),
            lease_until=now + timedelta(seconds=61),
            progress_current=250,
            progress_total=250,
        )
        assert foreground is not None
        await db.commit()

    keeper = SqlAlchemyProcessingLeaseKeeper(factory, interval_seconds=3600)
    keeper._job_id = claimed.id
    keeper._worker_id = "worker-race"
    keeper._progress_current = 0
    keeper._progress_total = 250

    assert await keeper._heartbeat(now + timedelta(seconds=2)) is True
    assert keeper._progress_current == 250
    assert keeper._progress_total == 250


async def test_lease_keeper_rechecks_expired_guard_with_database_clock(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        database_now = await db.scalar(select(func.clock_timestamp()))
        assert isinstance(database_now, datetime)
        requested_at = database_now - timedelta(minutes=5)
        repository = SqlAlchemyDurableJobRepository(db)
        reservation = await repository.reserve(
            NewJob(
                kind="orders.processing.v1",
                owner_id=42,
                scope_key="adequar:Franquia",
                fingerprint=fingerprint_payload({"test": "lease-expired-clock"}),
                requested_at=requested_at,
                deadline_at=database_now + timedelta(minutes=15),
            )
        )
        claimed = await repository.claim(
            job_id=reservation.job.id,
            kinds=("orders.processing.v1",),
            worker_id="worker-expired-clock",
            now=database_now,
            lease_until=database_now + PROCESSING_LEASE,
        )
        assert claimed is not None
        await db.execute(
            update(DurableJobModel)
            .where(DurableJobModel.id == claimed.id)
            .values(
                requested_at=requested_at,
                available_at=database_now,
                started_at=requested_at + timedelta(seconds=1),
                heartbeat_at=requested_at + timedelta(seconds=2),
                lease_expires_at=requested_at + timedelta(seconds=30),
            )
        )
        await db.commit()

    keeper = SqlAlchemyProcessingLeaseKeeper(factory, interval_seconds=3600)
    keeper._job_id = claimed.id
    keeper._worker_id = "worker-expired-clock"
    keeper._progress_current = 0
    keeper._progress_total = 1

    stale_app_now = requested_at + timedelta(seconds=3)
    assert await keeper._heartbeat(stale_app_now) is False

    async with factory() as db:
        current = await SqlAlchemyDurableJobRepository(db).get(claimed.id)
    assert current is not None
    assert current.status is JobStatus.RUNNING
    assert current.progress_current == 0


@pytest.mark.parametrize("channel", list(ProcessingChannel))
async def test_planner_sql_rejects_mixed_pair_before_hydration(
    processing_database,
    channel: ProcessingChannel,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido).insert(),
            [
                {
                    "nr_pedido": 501,
                    "cd_prod_cor": "MIXED",
                    "sg_tamanho": "36",
                    "ds_grupo": "G",
                    "qt_entregar": 1,
                    "vl_liquido": 10,
                    "canal": "Franquia",
                },
                {
                    "nr_pedido": 501,
                    "cd_prod_cor": "MIXED",
                    "sg_tamanho": "37",
                    "ds_grupo": "G",
                    "qt_entregar": 1,
                    "vl_liquido": 10,
                    "canal": "Multimarca",
                },
            ],
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        with pytest.raises(
            InvalidProcessingRequest,
            match="pending_pair_channel_is_not_canonical",
        ):
            await source.inspect_pending(channel)


async def test_planner_preflight_and_hydration_share_the_same_scope(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await _seed_source(db)
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        stats = await source.inspect_pending(ProcessingChannel.FRANQUIA)
        rows = await source.load_pending_items(ProcessingChannel.FRANQUIA)

    assert stats.pair_count == 1
    assert stats.item_count == 1
    assert stats.estimated_bytes > 0
    assert [(row["nr_pedido"], row["cd_prod_cor"]) for row in rows] == [
        (101, "PROD|01")
    ]
    assert stats.max_pair_item_count == 1


async def test_load_pending_items_propaga_indica_blacklist(
    processing_database,
) -> None:
    """260825-jhv: load_pending_items devolve a chave `indica_blacklist` no
    dict de cada linha — mesmo padrão de propagação já provado para
    `status_credito` no teste acima."""
    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=555,
                cd_prod_cor="BL|01",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=1,
                vl_liquido=10,
                client="Cliente blacklist",
                canal="Franquia",
                status_credito="Com Crédito",
                ds_produto="Produto",
                data="2026-08-25",
                indica_blacklist=True,
            )
        )
        await db.execute(
            _table(Estoque)
            .insert()
            .values(
                cd_prod_cor="BL|01",
                sg_tamanho="36",
                canal="Franquia",
                qt_disponivel=5,
                dt_estoque=datetime(2026, 8, 25, tzinfo=UTC).date(),
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        rows = await source.load_pending_items(ProcessingChannel.FRANQUIA)

    assert len(rows) == 1
    assert rows[0]["indica_blacklist"] is True


async def test_lock_guard_fails_closed_after_connection_invalidation(
    processing_database,
) -> None:
    engine, _factory = processing_database
    locked = PostgresOrderMutationSessionLock(engine)
    await locked.__aenter__()
    assert locked._connection is not None
    await locked._connection.invalidate()
    with pytest.raises(
        ProcessingLockUnavailable,
        match="order_state_mutation_lock_not_held",
    ):
        await locked.ensure_held()
    await locked.__aexit__(None, None, None)

    async with PostgresOrderMutationSessionLock(engine) as reacquired:
        await reacquired.ensure_held()


async def test_load_pedido_budget_sums_total_original_from_pedidos_when_no_or_exists(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido).insert(),
            [
                {
                    "nr_pedido": 901,
                    "cd_prod_cor": "PROD|A",
                    "sg_tamanho": "36",
                    "ds_grupo": "GRUPO",
                    "qt_entregar": 4,
                    "vl_liquido": 40,
                },
                {
                    "nr_pedido": 901,
                    "cd_prod_cor": "PROD|B",
                    "sg_tamanho": "38",
                    "ds_grupo": "GRUPO",
                    "qt_entregar": 5,
                    "vl_liquido": 50,
                },
            ],
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({901})

    assert total_original == {901: 9}
    assert consumido_adicao == {901: 0}
    assert consumido_corte == {901: 0}


async def test_load_pedido_budget_separates_adicao_and_corte_without_compensating(
    processing_database,
) -> None:
    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=902,
                cd_prod_cor="PROD|C",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=6,
                vl_liquido=60,
            )
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=902,
                cd_prod_cor="PROD|C",
                tipo="com",
                itens=[
                    {
                        "sg_tamanho": "36",
                        "qt_solicitada": 2,
                        "qt_liquida": 4,
                    },
                    {
                        "sg_tamanho": "38",
                        "qt_solicitada": 5,
                        "qt_liquida": 3,
                    },
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({902})

    assert total_original == {902: 6}
    assert consumido_adicao == {902: 2}
    assert consumido_corte == {902: 2}


async def test_load_pedido_budget_includes_or_quantity_for_pair_missing_from_pedidos(
    processing_database,
) -> None:
    """Caso obrigatório J5 — denominador que encolhe, verificado em produção em
    2026-08-17: pedido 1591091, par (1591091, "ML.18.0315|001") já convertido em
    OR e ausente de `pedidos`, com `qt_solicitada=3`; os outros 6 produtos do
    pedido seguem abertos em `pedidos` somando `qt_entregar=6`. Total original
    verdadeiro é 6 + 3 = 9 — sem a correção, a leitura devolveria só 6.
    """

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido).insert(),
            [
                {
                    "nr_pedido": 1591091,
                    "cd_prod_cor": f"PROD|{indice}",
                    "sg_tamanho": "36",
                    "ds_grupo": "GRUPO",
                    "qt_entregar": 1,
                    "vl_liquido": 10,
                }
                for indice in range(6)
            ],
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=1591091,
                cd_prod_cor="ML.18.0315|001",
                tipo="com",
                itens=[
                    {
                        "sg_tamanho": "38",
                        "qt_solicitada": 3,
                        "qt_liquida": 3,
                    }
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            _consumido_adicao,
            _consumido_corte,
        ) = await source.load_pedido_budget({1591091})

    assert total_original == {1591091: 9}


# --- Ramo novo 'sem adequação' medido pelo TOTAL do par (PD-01, Task 3) ---


async def test_load_pedido_budget_sem_adequacao_consome_adicao_pelo_total_do_par(
    processing_database,
) -> None:
    """OR tipo='sem' cujo total líquido do par soma acima do total
    solicitado aparece como consumo de adição no orçamento do pedido."""

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=910,
                cd_prod_cor="SEM|01",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=10,
                vl_liquido=100,
            )
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=910,
                cd_prod_cor="SEM|01",
                tipo="sem",
                itens=[
                    {"sg_tamanho": "36", "qt_solicitada": 5, "qt_liquida": 6},
                    {"sg_tamanho": "38", "qt_solicitada": 5, "qt_liquida": 6},
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({910})

    assert total_original == {910: 10}
    assert consumido_adicao == {910: 2}
    assert consumido_corte == {910: 0}


async def test_load_pedido_budget_sem_adequacao_consome_corte_pelo_total_do_par(
    processing_database,
) -> None:
    """Simétrico ao caso de adição: soma líquida do par abaixo da soma
    solicitada aparece como consumo de corte."""

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=911,
                cd_prod_cor="SEM|02",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=10,
                vl_liquido=100,
            )
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=911,
                cd_prod_cor="SEM|02",
                tipo="sem",
                itens=[
                    {"sg_tamanho": "36", "qt_solicitada": 6, "qt_liquida": 5},
                    {"sg_tamanho": "38", "qt_solicitada": 6, "qt_liquida": 5},
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({911})

    assert total_original == {911: 10}
    assert consumido_adicao == {911: 0}
    assert consumido_corte == {911: 2}


async def test_load_pedido_budget_sem_adequacao_redistribuicao_pura_nao_consome_nada(
    processing_database,
) -> None:
    """Guardião do PD-01: um par 'sem' com itens que divergem tamanho a
    tamanho mas se cancelam no total (redistribuição pura) contribui ZERO
    para os dois orçamentos. Se alguém "simplificar" a query no futuro para
    medir 'sem' por item (igual a 'com'), este teste falha — a soma por item
    daria adicao=2/corte=2 em vez de 0/0."""

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=912,
                cd_prod_cor="SEM|03",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=10,
                vl_liquido=100,
            )
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=912,
                cd_prod_cor="SEM|03",
                tipo="sem",
                itens=[
                    {"sg_tamanho": "36", "qt_solicitada": 3, "qt_liquida": 5},
                    {"sg_tamanho": "38", "qt_solicitada": 5, "qt_liquida": 3},
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({912})

    assert total_original == {912: 10}
    assert consumido_adicao == {912: 0}
    assert consumido_corte == {912: 0}


async def test_load_pedido_budget_com_adequacao_medicao_por_item_permanece_intacta(
    processing_database,
) -> None:
    """Guardião do inverso do teste anterior (D-01): o MESMO formato de
    dados (itens que se cancelam no total) aplicado a um par 'com' continua
    consumindo orçamento, porque 'com' é medido POR ITEM, não pelo total —
    prova que os dois ramos coexistem com semânticas diferentes e que o
    ramo do motor não foi contaminado pela mudança desta fase."""

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido)
            .insert()
            .values(
                nr_pedido=913,
                cd_prod_cor="COM|01",
                sg_tamanho="36",
                ds_grupo="GRUPO",
                qt_entregar=10,
                vl_liquido=100,
            )
        )
        await db.execute(
            _table(OrdemReserva)
            .insert()
            .values(
                nr_pedido=913,
                cd_prod_cor="COM|01",
                tipo="com",
                itens=[
                    {"sg_tamanho": "36", "qt_solicitada": 3, "qt_liquida": 5},
                    {"sg_tamanho": "38", "qt_solicitada": 5, "qt_liquida": 3},
                ],
            )
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({913})

    assert total_original == {913: 10}
    assert consumido_adicao == {913: 2}
    assert consumido_corte == {913: 2}


async def test_load_pedido_budget_pedido_misto_com_e_sem_soma_sem_duplicar_linha(
    processing_database,
) -> None:
    """Pedido com um par 'com' e um par 'sem': o consumido do pedido é a
    soma das duas contribuições, e o pedido aparece uma única vez no
    resultado (a união dos dois ramos não duplica linha no LEFT JOIN)."""

    _engine, factory = processing_database
    async with factory() as db:
        await db.execute(
            _table(Pedido).insert(),
            [
                {
                    "nr_pedido": 914,
                    "cd_prod_cor": "MIX|COM",
                    "sg_tamanho": "36",
                    "ds_grupo": "GRUPO",
                    "qt_entregar": 10,
                    "vl_liquido": 100,
                },
                {
                    "nr_pedido": 914,
                    "cd_prod_cor": "MIX|SEM",
                    "sg_tamanho": "36",
                    "ds_grupo": "GRUPO",
                    "qt_entregar": 10,
                    "vl_liquido": 100,
                },
            ],
        )
        await db.execute(
            _table(OrdemReserva).insert(),
            [
                {
                    "nr_pedido": 914,
                    "cd_prod_cor": "MIX|COM",
                    "tipo": "com",
                    "itens": [
                        {"sg_tamanho": "36", "qt_solicitada": 5, "qt_liquida": 7}
                    ],
                },
                {
                    "nr_pedido": 914,
                    "cd_prod_cor": "MIX|SEM",
                    "tipo": "sem",
                    "itens": [
                        {"sg_tamanho": "36", "qt_solicitada": 5, "qt_liquida": 4},
                        {"sg_tamanho": "38", "qt_solicitada": 5, "qt_liquida": 4},
                    ],
                },
            ],
        )
        await db.commit()
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({914})

    # aparece uma única vez — se o LEFT JOIN duplicasse, a chave já não
    # bateria com um dict de tamanho 1 (sobrescrita silenciosa não é
    # equivalente a soma correta, mas o valor abaixo prova a soma certa).
    assert total_original == {914: 20}
    assert consumido_adicao == {914: 2}
    assert consumido_corte == {914: 2}


async def _seed_pedido_700(db: AsyncSession) -> None:
    """Pedido único com DOIS produtos (40 peças no total, orçamento de corte
    `floor(40*0.05)=2` — acima do piso do Pitfall 4), para que exista uma
    segunda execução com trabalho real a fazer."""

    await db.execute(
        _table(Pedido).insert(),
        [
            {
                "nr_pedido": 700,
                "cd_prod_cor": produto,
                "sg_tamanho": tamanho,
                "ds_grupo": "GRUPO",
                "qt_entregar": 10,
                "vl_liquido": 100.0,
                "client": "Cliente 700",
                "canal": "Franquia",
                "status_credito": "Com Crédito",
                "ds_produto": produto,
                "data": "2026-08-08",
            }
            for produto in ("ORC|01", "ORC|02")
            for tamanho in ("36", "37")
        ],
    )


async def test_orcamento_persiste_entre_execucoes_sucessivas(
    processing_database,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Critério 5 do ROADMAP / ALOC-09 fechado pelo caminho de produção real:
    duas execuções sucessivas sobre o mesmo pedido (700) não concedem 5%
    novos. A rodada 2 prova a propriedade DISCRIMINANTE comparando, sobre os
    MESMOS itens pendentes, a chamada com orçamento real (recusa o corte)
    contra a chamada sem orçamento — o placeholder de execução única (aceita
    o mesmo corte, porque o denominador encolhido concede um orçamento novo)."""

    _engine, factory = processing_database

    # --- Rodada 1: ORC|01 tem estoque suficiente (falta exatamente o teto de
    # corte do pedido, 2 peças); ORC|02 fica em stand-by por falta total. ---
    async with factory() as db:
        await _seed_pedido_700(db)
        await db.execute(
            _table(Estoque).insert(),
            [
                {
                    "cd_prod_cor": "ORC|01",
                    "sg_tamanho": "36",
                    "canal": "Franquia",
                    "qt_disponivel": 8,
                    "dt_estoque": datetime(2026, 8, 8, tzinfo=UTC).date(),
                },
                {
                    "cd_prod_cor": "ORC|01",
                    "sg_tamanho": "37",
                    "canal": "Franquia",
                    "qt_disponivel": 10,
                    "dt_estoque": datetime(2026, 8, 8, tzinfo=UTC).date(),
                },
            ],
        )
        await db.commit()

        source = SqlAlchemyProcessingPlannerSource(db)
        pending = await source.load_pending_items(ProcessingChannel.FRANQUIA)
        assert {(item["nr_pedido"], item["cd_prod_cor"]) for item in pending} == {
            (700, "ORC|01"),
            (700, "ORC|02"),
        }
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({700})
        assert total_original == {700: 40}
        assert consumido_adicao == {700: 0}
        assert consumido_corte == {700: 0}

        reference = {"ORC|01": {"36": 1, "37": 2}, "ORC|02": {"36": 1, "37": 2}}
        stock_round1 = {
            "Franquia": {
                "ORC|01_36": 8,
                "ORC|01_37": 10,
                "ORC|02_36": 0,
                "ORC|02_37": 0,
            }
        }
        draft1 = build_processing_plan(
            request=ProcessingRequest(
                mode=ProcessingMode.ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
            ),
            pending_items=pending,
            reference=reference,
            stock=stock_round1,
            total_original_por_pedido=total_original,
            consumido_previo_adicao_por_pedido=consumido_adicao,
            consumido_previo_corte_por_pedido=consumido_corte,
        )

        assert draft1.candidate_count == 2
        assert draft1.deferred_count == 1
        assert [(row.nr_pedido, row.cd_prod_cor) for row in draft1.rows] == [
            (700, "ORC|01")
        ]
        row1_qty = {
            item["sg_tamanho"]: item["qt_liquida"]
            for item in draft1.rows[0].payload["items"]
        }
        assert row1_qty == {"36": 8, "37": 10}

        repository, spec = await _create_job_and_store_draft(
            db, draft1, mode=ProcessingMode.ADEQUAR
        )
        await db.commit()

        chunk = await repository.load_next_chunk(job_id=spec.job_id, limit=250)
        await SqlAlchemyProcessingWriter(db).apply_pairs(chunk)
        await repository.checkpoint_chunk(
            job_id=spec.job_id,
            rows=chunk,
            applied_at=datetime.now(UTC),
        )
        await db.commit()

    # --- Estado intermediário: o que o writer persistiu em
    # `ordens_reserva.itens` (qt_solicitada/qt_liquida) é exatamente o que o
    # leitor de orçamento agrega (elo que o plano 15-01 não conseguiu provar
    # sozinho). ---
    async with factory() as db:
        source = SqlAlchemyProcessingPlannerSource(db)
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({700})
    assert total_original == {700: 40}
    assert consumido_adicao == {700: 0}
    assert consumido_corte == {700: 2}

    # --- Rodada 2: repõe estoque de ORC|02 (falta 1 peça); duas chamadas
    # comparativas sobre a MESMA foto pendente (só ORC|02 continua elegível,
    # ORC|01 já tem OR e saiu do escopo pendente). ---
    async with factory() as db:
        await db.execute(
            _table(Estoque).insert(),
            [
                {
                    "cd_prod_cor": "ORC|02",
                    "sg_tamanho": "36",
                    "canal": "Franquia",
                    "qt_disponivel": 9,
                    "dt_estoque": datetime(2026, 8, 8, tzinfo=UTC).date(),
                },
                {
                    "cd_prod_cor": "ORC|02",
                    "sg_tamanho": "37",
                    "canal": "Franquia",
                    "qt_disponivel": 10,
                    "dt_estoque": datetime(2026, 8, 8, tzinfo=UTC).date(),
                },
            ],
        )
        await db.commit()

        source = SqlAlchemyProcessingPlannerSource(db)
        pending2 = await source.load_pending_items(ProcessingChannel.FRANQUIA)
        assert {(item["nr_pedido"], item["cd_prod_cor"]) for item in pending2} == {
            (700, "ORC|02")
        }
        (
            total_original,
            consumido_adicao,
            consumido_corte,
        ) = await source.load_pedido_budget({700})
        assert (total_original, consumido_adicao, consumido_corte) == (
            {700: 40},
            {700: 0},
            {700: 2},
        )

        reference2 = {"ORC|02": {"36": 1, "37": 2}}
        stock_round2 = {"Franquia": {"ORC|02_36": 9, "ORC|02_37": 10}}
        request = ProcessingRequest(
            mode=ProcessingMode.ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        )
        logger_name = "app.modules.pedidos.domain.motor_adequacao"

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            draft_com_orcamento_real = build_processing_plan(
                request=request,
                pending_items=pending2,
                reference=reference2,
                stock=stock_round2,
                total_original_por_pedido=total_original,
                consumido_previo_adicao_por_pedido=consumido_adicao,
                consumido_previo_corte_por_pedido=consumido_corte,
            )
        assert not any("ALOC-09" in record.message for record in caplog.records)

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            draft_sem_orcamento_placeholder = build_processing_plan(
                request=request,
                pending_items=pending2,
                reference=reference2,
                stock=stock_round2,
            )
        assert any("ALOC-09" in record.message for record in caplog.records)

    # Orçamento restante = limite_corte(2) - consumido_corte(2) = 0: o corte
    # de 1 peça em ORC|02 é RECUSADO — a mesma foto, com orçamento real.
    assert draft_com_orcamento_real.rows == ()
    assert draft_com_orcamento_real.planned_count == 0
    assert draft_com_orcamento_real.deferred_count == 1

    # O mesmo corte de 1 peça, sem os mappings (placeholder de execução
    # única), usa como total só a soma do snapshot desta rodada (20 peças):
    # floor(20*0.05)=1, que cobre o corte de 1 — ACEITO.
    assert draft_sem_orcamento_placeholder.planned_count == 1
    assert [
        (row.nr_pedido, row.cd_prod_cor) for row in draft_sem_orcamento_placeholder.rows
    ] == [(700, "ORC|02")]

    async with factory() as db:
        assert (
            await db.scalar(
                select(OrdemReserva.nr_pedido).where(
                    OrdemReserva.cd_prod_cor == "ORC|02"
                )
            )
        ) is None


# --- _plan_once atômico: record_standby_reasons na mesma transação (16-04) --


class _StandbyLock:
    async def ensure_held(self) -> None:
        return None


class _StandbyLease:
    async def start(self, **_kwargs) -> None:
        return None

    async def update_progress(self, **_kwargs) -> None:
        return None

    async def ensure_valid(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _StandbyJobs:
    def __init__(self, job) -> None:
        self._job = job

    async def heartbeat(self, **_kwargs):
        return self._job


class _StandbyPlanner:
    """2 pedidos, canal Franquia, SEM_ADEQUAR: pedido 1/produto "A" tem
    estoque (entra no plano); pedido 2/produto "B" não tem estoque no seu
    único tamanho e é preterido por falta de estoque — tamanho único nunca
    produz furo_grade.
    """

    async def inspect_pending(self, _channel):
        return PendingSnapshotStats(
            pair_count=2,
            item_count=2,
            estimated_bytes=100,
            max_pair_item_count=1,
        )

    async def load_pending_items(self, _channel):
        return [
            {
                "nr_pedido": 1,
                "cd_prod_cor": "A",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 2,
                "vl_liquido": 20.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "A",
                "data": "2026-08-08",
            },
            {
                "nr_pedido": 2,
                "cd_prod_cor": "B",
                "sg_tamanho": "36",
                "ds_grupo": "G",
                "qt_liquida": 2,
                "vl_liquido": 20.0,
                "status_credito": "Com Crédito",
                "client": "protected",
                "canal": "Franquia",
                "ds_produto": "B",
                "data": "2026-08-08",
            },
        ]

    async def load_stock(self, _codes):
        return {"Franquia": {"A_36": 2, "B_36": 0}}

    async def load_adequation_config(self):
        return "valor", 0.05

    async def load_size_reference(self, _codes):
        return {"A": {"36": 1}, "B": {"36": 1}}

    async def load_pedido_budget(self, nr_pedidos):
        scope = set(nr_pedidos)
        return (
            dict.fromkeys(scope, 0),
            dict.fromkeys(scope, 0),
            dict.fromkeys(scope, 0),
        )


async def _standby_plan_fixture(db: AsyncSession):
    """Reserva um `DurableJob` real + header real (`create_spec`), com
    commit — o header precisa sobreviver ao rollback do teste de falha
    injetada. Setup mínimo compartilhado pelos dois testes de `_plan_once`
    atômico.
    """

    now = datetime.now(UTC)
    reservation = await SqlAlchemyDurableJobRepository(db).reserve(
        NewJob(
            kind="orders.processing.v1",
            owner_id=42,
            scope_key="sem_adequar:Franquia",
            idempotency_digest=None,
            fingerprint=fingerprint_payload({"test": "standby-atomic"}),
            requested_at=now,
            deadline_at=now + timedelta(hours=1),
        )
    )
    job = reservation.job
    await SqlAlchemyProcessingRepository(db).create_spec(
        job_id=job.id,
        mode=ProcessingMode.SEM_ADEQUAR,
        channel=ProcessingChannel.FRANQUIA,
    )
    await db.commit()
    return job


async def test_plan_once_persists_standby_reasons_in_the_same_transaction_as_the_plan(
    processing_database,
) -> None:
    """Critério 1 do ROADMAP provado ponta a ponta pelo caminho de produção:
    `_plan_once` grava o plano E o motivo do par preterido na MESMA
    transação, com `record_standby_reasons` reusando `planned_at`. Isto,
    junto com o teste de rollback abaixo, é a prova de atomicidade.
    """

    _engine, factory = processing_database
    async with factory() as db:
        job = await _standby_plan_fixture(db)

    planned_at = datetime.now(UTC)
    async with factory() as db:
        spec = await processing_service._plan_once(
            job=job,
            worker_id="worker-standby",
            jobs=cast(DurableJobRepository, _StandbyJobs(job)),
            processing=SqlAlchemyProcessingRepository(db),
            planner=cast(ProcessingPlannerSource, _StandbyPlanner()),
            lease_keeper=_StandbyLease(),
            lock_guard=_StandbyLock(),
            unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
            now=lambda: planned_at,
        )
        assert spec.planning_state is PlanningState.PLANNED
        assert spec.planned_count == 1

    async with factory() as db:
        repository = SqlAlchemyProcessingRepository(db)
        header = await repository.get_spec(job.id)
        assert header is not None
        assert header.planning_state is PlanningState.PLANNED
        assert header.planned_count == 1

        plan_chunk = await repository.load_next_chunk(job_id=job.id, limit=250)
        assert [(row.nr_pedido, row.cd_prod_cor) for row in plan_chunk] == [(1, "A")]

        standby_row = await db.scalar(
            select(PedidoStandbyMotivo).where(
                PedidoStandbyMotivo.nr_pedido == 2,
                PedidoStandbyMotivo.cd_prod_cor == "B",
            )
        )
        assert standby_row is not None
        assert standby_row.canal == "Franquia"
        assert standby_row.motivo == "sem_estoque"
        assert standby_row.execucoes_consecutivas == 1
        assert standby_row.job_id == job.id
        assert standby_row.atualizado_em == planned_at


async def test_plan_once_rolls_back_the_plan_when_the_standby_write_fails(
    processing_database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """O teste mais importante deste plano: uma falha injetada exatamente na
    fronteira entre `store_plan` e `record_standby_reasons` não deixa NADA
    commitado. Se a chamada estivesse depois do commit, o header ficaria
    `planned` e, pelo retorno antecipado de `_plan_once` quando
    `planning_state` já é `PLANNED`, nenhuma nova tentativa refaria a
    escrita perdida — perda silenciosa e PERMANENTE (Pitfall 2 / risco 2 do
    16-RESEARCH.md).
    """

    _engine, factory = processing_database
    async with factory() as db:
        job = await _standby_plan_fixture(db)

    async def boom(self, **_kwargs):
        raise RuntimeError("standby_write_boom")

    monkeypatch.setattr(SqlAlchemyProcessingRepository, "record_standby_reasons", boom)

    async with factory() as db:
        with pytest.raises(RuntimeError, match="standby_write_boom"):
            await processing_service._plan_once(
                job=job,
                worker_id="worker-standby",
                jobs=cast(DurableJobRepository, _StandbyJobs(job)),
                processing=SqlAlchemyProcessingRepository(db),
                planner=cast(ProcessingPlannerSource, _StandbyPlanner()),
                lease_keeper=_StandbyLease(),
                lock_guard=_StandbyLock(),
                unit_of_work=SqlAlchemyProcessingUnitOfWork(db),
                now=lambda: datetime.now(UTC),
            )
        await db.rollback()

    async with factory() as db:
        header = await SqlAlchemyProcessingRepository(db).get_spec(job.id)
        assert header is not None
        assert header.planning_state is PlanningState.PENDING
        assert header.plan_hash is None

        plan_rows = await db.scalar(
            select(func.count()).select_from(PedidoProcessamentoPlanModel)
        )
        assert plan_rows == 0

        standby_rows = await db.scalar(
            select(func.count()).select_from(PedidoStandbyMotivo)
        )
        assert standby_rows == 0
