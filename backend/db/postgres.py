"""
PostgreSQL 异步数据库连接
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_env == "development",
    pool_size=10,
    max_overflow=20,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """创建所有表（开发环境使用，生产环境用 Alembic）"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 同步引擎（供 Celery worker 使用）
sync_engine = create_engine(
    settings.database_url.replace("asyncpg", "psycopg2"),
    pool_size=5,
    max_overflow=10,
)

SyncSession = sessionmaker(sync_engine, expire_on_commit=False)
