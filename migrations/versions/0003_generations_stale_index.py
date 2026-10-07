"""index for stale generations lookup

Revision ID: 0003_generations_stale_index
Revises: 0002_unique_purchase_event
Create Date: 2026-10-07 15:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_generations_stale_index"
down_revision: str | None = "0002_unique_purchase_event"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Сверка ищет генерации по статусу и времени последнего изменения.
    # Составной индекс покрывает и прежние выборки только по статусу.
    op.create_index(
        "ix_generations_status_updated_at",
        "generations",
        ["status", "updated_at"],
    )
    op.drop_index("ix_generations_status", table_name="generations")


def downgrade() -> None:
    op.create_index("ix_generations_status", "generations", ["status"])
    op.drop_index("ix_generations_status_updated_at", table_name="generations")
