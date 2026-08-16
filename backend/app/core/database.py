"""SQLAlchemy 异步引擎、会话工厂与 API 依赖。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """为单次 API 请求提供自动释放的数据库会话。"""
    async with SessionLocal() as session:
        yield session
