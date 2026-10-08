from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from job_search.infrastructure.persistence.sqlalchemy import models  # noqa: F401
from job_search.infrastructure.persistence.sqlalchemy.base import Base
from job_search.infrastructure.persistence.sqlalchemy.database import (
    create_database_engine,
)


@pytest_asyncio.fixture
async def database(
    tmp_path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    database_path = tmp_path / "test.db"
    engine = create_database_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    try:
        yield engine, session_factory
    finally:
        await engine.dispose()
