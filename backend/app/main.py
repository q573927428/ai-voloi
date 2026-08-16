"""FastAPI 应用入口与生命周期管理。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.models import Base
from app.services.broadcast import KlineBroadcaster, SignalBroadcaster
from app.services.runtime import MonitorRuntime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """建表并管理采集运行时，确保关闭时释放连接。"""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    broadcaster = SignalBroadcaster()
    kline_broadcaster = KlineBroadcaster()
    runtime = MonitorRuntime(settings, SessionLocal, broadcaster, kline_broadcaster)
    app.state.broadcaster = broadcaster
    app.state.kline_broadcaster = kline_broadcaster
    app.state.runtime = runtime
    if settings.auto_start_monitor:
        await runtime.start()
    try:
        yield
    finally:
        if settings.auto_start_monitor:
            await runtime.stop()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_prefix)
