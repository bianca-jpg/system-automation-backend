from collections.abc import AsyncGenerator
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.config.settings import get_settings
from app.shared.database.options import asyncpg_connect_args

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args=asyncpg_connect_args(
        ssl_mode=settings.postgres_ssl_mode,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
        command_timeout_seconds=settings.postgres_command_timeout_seconds,
    ),
)
async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_database() -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


class DatabaseSchemaNotReadyError(RuntimeError):
    """Raised when the connected database is not at the repository Alembic head."""


@lru_cache(maxsize=1)
def _expected_alembic_heads() -> frozenset[str]:
    repository_root = Path(__file__).resolve().parents[3]
    alembic_config = Config(str(repository_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(repository_root / "alembic"))
    return frozenset(ScriptDirectory.from_config(alembic_config).get_heads())


async def check_database_schema() -> bool:
    """Reject readiness when migrations were not applied to the target database."""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        current_heads = frozenset(str(row[0]) for row in result)

    if current_heads != _expected_alembic_heads():
        raise DatabaseSchemaNotReadyError("database schema is not at Alembic head")
    return True


async def dispose_engine() -> None:
    await engine.dispose()
