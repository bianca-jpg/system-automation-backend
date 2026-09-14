import asyncio
import time
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.modules.auth.infrastructure.models import (  # noqa: F401
    AuthUser,
)
from app.modules.comunicacoes.infrastructure.models import (  # noqa: F401
    Comunicacao,
    ComunicacaoEmailDelivery,
)
from app.modules.ingestao.infrastructure.models import (  # noqa: F401
    Estoque,
    FaturamentoColecao,
    Pedido,
    PedidoProcessadoErp,
    PedidoProdutoRead,
    ProdutoTamanhoPosicao,
)
from app.modules.parametros.infrastructure.models import (  # noqa: F401
    Parametro,
    ParametroChangeRequest,
)
from app.modules.pedidos.infrastructure.models import (  # noqa: F401
    EstoqueVirtual,
    OrdemReserva,
    OrdemReservaLinx,
    PedidoModificacao,
    PedidoProcessado,
    PedidoStandbyMotivo,
)
from app.modules.pedidos.processing.infrastructure.models import (  # noqa: F401
    PedidoProcessamentoModel,
    PedidoProcessamentoPlanModel,
)
from app.modules.realtime.infrastructure.models import (  # noqa: F401
    RealtimeObservedEntity,
    RealtimeOutbox,
    RealtimeReadCursor,
    RealtimeTopicState,
)
from app.shared.config.settings import get_settings
from app.shared.database.base import Base
from app.shared.database.options import alembic_config_url, asyncpg_connect_args
from app.shared.jobs.infrastructure.models import DurableJobModel  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", alembic_config_url(settings.database_url))

# Serializa upgrades concorrentes (por exemplo, duas tasks novas do ECS
# iniciando juntas). Precisa ser um lock de sessao: migrations que usam
# ``autocommit_block`` (CREATE INDEX CONCURRENTLY) encerram a transacao Alembic,
# mas nao podem liberar a exclusao mutua no meio do upgrade.
_MIGRATION_ADVISORY_LOCK = 7_541_240_193_001
_MIGRATION_LOCK_POLL_SECONDS = 0.25


def _acquire_migration_lock(connection: Connection) -> str:
    """Adquire o lock sem manter uma transacao waiter aberta.

    Um ``pg_advisory_lock`` bloqueante dentro do autobegin pode deadlockar com
    ``CREATE INDEX CONCURRENTLY``: o indexador espera a virtual transaction do
    waiter, enquanto o waiter espera o lock do indexador. Tentativas curtas em
    AUTOCOMMIT encerram cada statement antes de aguardar novamente.
    """
    original_isolation = connection.get_isolation_level()
    connection.execution_options(isolation_level="AUTOCOMMIT")
    deadline = time.monotonic() + settings.postgres_migration_timeout_seconds
    while True:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": _MIGRATION_ADVISORY_LOCK},
            ).scalar_one()
        )
        # Limpa o autobegin logico do SQLAlchemy. No driver a statement ja foi
        # executada em autocommit, portanto waiters nao conservam snapshot.
        connection.commit()
        if acquired:
            connection.execution_options(isolation_level=original_isolation)
            return original_isolation
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            connection.execution_options(isolation_level=original_isolation)
            raise TimeoutError("tempo esgotado aguardando o lock global de migrations")
        time.sleep(min(_MIGRATION_LOCK_POLL_SECONDS, remaining))


def _release_migration_lock(connection: Connection, isolation_level: str) -> None:
    """Libera o lock de sessao mesmo se a migration falhar."""
    # Qualquer transacao Alembic que tenha falhado precisa estar encerrada antes
    # de mudar o isolation level da conexao.
    if connection.in_transaction():
        connection.rollback()
    connection.execution_options(isolation_level="AUTOCOMMIT")
    released = bool(
        connection.execute(
            text("SELECT pg_advisory_unlock(:lock_id)"),
            {"lock_id": _MIGRATION_ADVISORY_LOCK},
        ).scalar_one()
    )
    connection.commit()
    connection.execution_options(isolation_level=isolation_level)
    if not released:
        raise RuntimeError("lock global de migrations nao estava adquirido")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    isolation_level = _acquire_migration_lock(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        _release_migration_lock(connection, isolation_level)


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=asyncpg_connect_args(
            ssl_mode=settings.postgres_ssl_mode,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
            command_timeout_seconds=settings.postgres_migration_timeout_seconds,
        ),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
