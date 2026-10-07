import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pymysql
from sqlalchemy import Row, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TABLES = ("balance_transactions", "generations", "purchase_events", "user_balances")


def parse_test_dsn(dsn: str) -> URL:
    url = make_url(dsn)
    # Тесты пересоздают и очищают базу, поэтому рабочую трогать нельзя.
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(
            "TEST_MYSQL_DSN must point to a database whose name ends with '_test'"
        )
    return url


def recreate_database(url: URL) -> None:
    connection = pymysql.connect(
        host=url.host or "localhost",
        port=url.port or 3306,
        user=url.username,
        password=url.password or "",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{url.database}`")
            cursor.execute(f"CREATE DATABASE `{url.database}`")
    finally:
        connection.close()


def run_alembic(dsn: str, *args: str) -> None:
    """Запускает alembic отдельным процессом: так же, как он запускается при выкатке."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env={**os.environ, "MYSQL_DSN": dsn},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )


class Database:
    """Прямой доступ к MySQL для подготовки данных и проверок в тестах."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        async with self._session_factory() as session:
            await session.execute(text(sql), params or {})
            await session.commit()

    async def rows(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> Sequence[Row[Any]]:
        async with self._session_factory() as session:
            result = await session.execute(text(sql), params or {})
            return result.all()

    async def scalar(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        async with self._session_factory() as session:
            return await session.scalar(text(sql), params or {})

    async def set_balance(self, user_id: str, balance: int) -> None:
        await self.execute(
            "INSERT INTO user_balances (user_id, balance) VALUES (:user_id, :balance)",
            {"user_id": user_id, "balance": balance},
        )

    async def balance(self, user_id: str) -> int | None:
        return await self.scalar(
            "SELECT balance FROM user_balances WHERE user_id = :user_id",
            {"user_id": user_id},
        )

    async def add_generation(
        self,
        generation_id: str,
        status: str,
        *,
        user_id: str = "user-1",
        cost: int = 10,
        age_seconds: int = 0,
    ) -> None:
        await self.execute(
            "INSERT INTO generations "
            "(id, user_id, prompt, status, cost, created_at, updated_at) "
            "VALUES (:id, :user_id, 'a cat in space', :status, :cost, "
            "TIMESTAMPADD(SECOND, -:age, NOW()), TIMESTAMPADD(SECOND, -:age, NOW()))",
            {
                "id": generation_id,
                "user_id": user_id,
                "status": status,
                "cost": cost,
                "age": age_seconds,
            },
        )

    async def generation(self, generation_id: str) -> Row[Any]:
        (row,) = await self.rows(
            "SELECT status, result_url, error FROM generations WHERE id = :id",
            {"id": generation_id},
        )
        return row

    async def transactions(self, user_id: str = "user-1") -> list[tuple[str, int, str]]:
        rows = await self.rows(
            "SELECT kind, amount, reference_id FROM balance_transactions "
            "WHERE user_id = :user_id ORDER BY id",
            {"user_id": user_id},
        )
        return [(row.kind, row.amount, row.reference_id) for row in rows]
