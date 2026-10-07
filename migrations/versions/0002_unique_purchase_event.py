"""unique purchase event id

Revision ID: 0002_unique_purchase_event
Revises: 0001_initial
Create Date: 2026-10-07 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_unique_purchase_event"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # В проде уже есть события, записанные дважды, с ними уникальный индекс
    # не создастся. Оставляем самую раннюю запись каждого event_id.
    # Балансы и balance_transactions не трогаем: лишние начисления остаются
    # видны как несколько строк kind='purchase' с одним reference_id,
    # решение о списании принимается отдельно.
    op.execute(
        """
        DELETE duplicate
        FROM purchase_events AS duplicate
        JOIN purchase_events AS original
          ON original.event_id = duplicate.event_id
         AND original.id < duplicate.id
        """
    )
    op.create_unique_constraint(
        "uq_purchase_events_event_id",
        "purchase_events",
        ["event_id"],
    )
    op.drop_index("ix_purchase_events_event_id", table_name="purchase_events")


def downgrade() -> None:
    # Удалённые дубликаты не восстанавливаются.
    op.create_index("ix_purchase_events_event_id", "purchase_events", ["event_id"])
    op.drop_constraint("uq_purchase_events_event_id", "purchase_events", type_="unique")
