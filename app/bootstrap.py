import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.modules.realtime.infrastructure.runtime import get_realtime_runtime
from app.shared.config.settings import get_settings
from app.shared.database.session import dispose_engine
from app.shared.infrastructure.redis_client import close_redis
from app.shared.logging.setup import setup_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    get_settings()

    realtime = get_realtime_runtime()
    await realtime.start()

    try:
        yield
    finally:
        try:
            await realtime.stop()
        finally:
            try:
                await close_redis()
            finally:
                await dispose_engine()
