"""Миграции применяются к базе, в которой уже лежат последствия багов."""

import pymysql
import pytest
from sqlalchemy.engine import URL

from tests.integration.database import parse_test_dsn, recreate_database, run_alembic


def connect(url: URL) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=url.host or "localhost",
        port=url.port or 3306,
        user=url.username,
        password=url.password or "",
        database=url.database,
        autocommit=True,
    )


def insert_purchase_event(cursor, event_id: str) -> None:
    cursor.execute(
        "INSERT INTO purchase_events (event_id, user_id, product_id, credits) "
        "VALUES (%s, 'user-1', 'credits_50', 50)",
        (event_id,),
    )
    cursor.execute(
        "INSERT INTO balance_transactions (user_id, amount, kind, reference_id) "
        "VALUES ('user-1', 50, 'purchase', %s)",
        (event_id,),
    )


@pytest.fixture
def production_like_database(mysql_dsn: str):
    """База на ревизии 0001 с данными, которые оставили баги."""
    url = parse_test_dsn(mysql_dsn)
    recreate_database(url)
    run_alembic(mysql_dsn, "upgrade", "0001_initial")

    connection = connect(url)
    with connection.cursor() as cursor:
        # Баг 2: одна покупка записана и начислена дважды.
        insert_purchase_event(cursor, "evt-duplicated")
        insert_purchase_event(cursor, "evt-duplicated")
        insert_purchase_event(cursor, "evt-duplicated")
        insert_purchase_event(cursor, "evt-normal")
        cursor.execute(
            "INSERT INTO user_balances (user_id, balance) VALUES ('user-1', 200)"
        )
        # Баг 4 и сбои провайдера: генерации, зависшие в pending и processing.
        cursor.execute(
            "INSERT INTO generations (id, user_id, prompt, status, cost) VALUES "
            "('stuck-pending', 'user-1', 'a cat', 'pending', 10), "
            "('stuck-processing', 'user-1', 'a cat', 'processing', 10)"
        )
    try:
        yield connection
    finally:
        connection.close()
        # Следующие тесты ждут чистую базу на последней ревизии.
        recreate_database(url)
        run_alembic(mysql_dsn, "upgrade", "head")


def test_upgrade_deduplicates_purchase_events_and_keeps_money_untouched(
    mysql_dsn, production_like_database
) -> None:
    connection = production_like_database
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT MIN(id) FROM purchase_events WHERE event_id = 'evt-duplicated'"
        )
        (earliest_id,) = cursor.fetchone()

    run_alembic(mysql_dsn, "upgrade", "head")

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, event_id FROM purchase_events ORDER BY id")
        events = cursor.fetchall()
        cursor.execute("SELECT balance FROM user_balances WHERE user_id = 'user-1'")
        (balance,) = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM balance_transactions")
        (transactions,) = cursor.fetchone()
        cursor.execute("SELECT id, status FROM generations ORDER BY id")
        generations = cursor.fetchall()

    assert [event_id for _, event_id in events] == ["evt-duplicated", "evt-normal"]
    assert events[0][0] == earliest_id  # осталась самая ранняя запись
    # Миграция не решает за бизнес, списывать ли лишние начисления:
    # баланс и история операций остаются как были.
    assert balance == 200
    assert transactions == 4
    assert generations == (
        ("stuck-pending", "pending"),
        ("stuck-processing", "processing"),
    )


def test_after_upgrade_database_rejects_duplicate_event(
    mysql_dsn, production_like_database
) -> None:
    connection = production_like_database
    run_alembic(mysql_dsn, "upgrade", "head")

    with connection.cursor() as cursor:
        with pytest.raises(pymysql.err.IntegrityError) as exc_info:
            cursor.execute(
                "INSERT INTO purchase_events (event_id, user_id, product_id, credits) "
                "VALUES ('evt-normal', 'user-1', 'credits_50', 50)"
            )

    assert exc_info.value.args[0] == 1062


def test_downgrade_and_upgrade_again(mysql_dsn, production_like_database) -> None:
    run_alembic(mysql_dsn, "upgrade", "head")
    run_alembic(mysql_dsn, "downgrade", "0001_initial")
    run_alembic(mysql_dsn, "upgrade", "head")

    with production_like_database.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM purchase_events")
        (events,) = cursor.fetchone()
    assert events == 2
