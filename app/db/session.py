"""异步 SQLAlchemy 引擎与 session 工厂。

缺省使用 SQLite（aiosqlite），零配置可用；配置 MYSQL_DSN 后自动切到 MySQL。
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.db.models import Base

engine = create_async_engine(settings.db_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """建表（仅 SQLite 演示用；生产用 Alembic 迁移）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _ensure_columns()


async def _ensure_columns() -> None:
    """轻量幂等迁移：给已存在的旧表补上新增列（正式迁移仍建议用 Alembic）。

    每条 ALTER 单独事务执行；若列已存在会报错，直接吞掉即可。
    """
    alters = [
        "ALTER TABLE chat_session ADD COLUMN owner VARCHAR(64)",
    ]
    for ddl in alters:
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(ddl)
        except Exception:  # noqa: BLE001 —— 列已存在
            pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入用的 session 生成器。"""
    async with AsyncSessionLocal() as session:
        yield session
