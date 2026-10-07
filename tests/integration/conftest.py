"""Интеграционные тесты на настоящей MySQL.

Запуск:
    docker compose up -d mysql
    TEST_MYSQL_DSN=mysql+aiomysql://root:root@localhost:3306/credits_test uv run pytest

Без TEST_MYSQL_DSN тесты из этой папки пропускаются. База из DSN пересоздаётся
при каждом запуске, поэтому её имя обязано заканчиваться на `_test`.
"""

import os
from collections.abc import AsyncIterator, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from components.application.use_cases.generation.fail_generation import (
    FailGeneration,
)
from components.infrastructure.stores.mysql.unit_of_work import MySQLUnitOfWork
from tests.fakes.in_memory_services import InMemoryBalanceCache
from tests.integration.database import (
    TABLES,
    Database,
    parse_test_dsn,
    recreate_database,
    run_alembic,
)

# Больше, чем число параллельных операций в тестах: каждой нужно своё соединение.
POOL_SIZE = 30


@pytest.fixture(scope="session")
def mysql_dsn() -> str:
    dsn = os.environ.get("TEST_MYSQL_DSN")
    if not dsn:
        pytest.skip("TEST_MYSQL_DSN is not set, integration tests are skipped")
    parse_test_dsn(dsn)
    return dsn


@pytest.fixture(scope="session")
def migrated_database(mysql_dsn: str) -> str:
    """Чистая база со схемой, созданной настоящими миграциями."""
    recreate_database(parse_test_dsn(mysql_dsn))
    run_alembic(mysql_dsn, "upgrade", "head")
    return mysql_dsn


@pytest.fixture
async def session_factory(
    migrated_database: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database, pool_size=POOL_SIZE)
    async with engine.begin() as connection:
        for table in TABLES:
            await connection.execute(text(f"TRUNCATE TABLE {table}"))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def db(session_factory: async_sessionmaker[AsyncSession]) -> Database:
    return Database(session_factory)


@pytest.fixture
def new_uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], MySQLUnitOfWork]:
    """Каждой параллельной операции — свой UoW, как у отдельного запроса в API."""
    return lambda: MySQLUnitOfWork(session_factory)


@pytest.fixture
def new_fail_generation(
    new_uow: Callable[[], MySQLUnitOfWork],
    balance_cache: InMemoryBalanceCache,
) -> Callable[[], FailGeneration]:
    return lambda: FailGeneration(new_uow(), balance_cache)
