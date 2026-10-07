from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from components.domain.entities.purchase_event import PurchaseEvent
from components.domain.errors.billing.errors import DuplicatePurchaseEventError
from components.domain.protocols.billing.purchase_events import (
    IPurchaseEventRepository,
)
from components.infrastructure.stores.mysql.models.purchase_event import PurchaseEvents

_MYSQL_DUPLICATE_ENTRY = 1062


class PurchaseEventRepository(IPurchaseEventRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: PurchaseEvent) -> None:
        self._session.add(
            PurchaseEvents(
                event_id=event.event_id,
                user_id=event.user_id,
                product_id=event.product_id,
                credits=event.credits,
            )
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:
            if _is_duplicate_entry(exc):
                raise DuplicatePurchaseEventError(event.event_id) from exc
            raise


def _is_duplicate_entry(exc: IntegrityError) -> bool:
    args: tuple[object, ...] = getattr(exc.orig, "args", ())
    return bool(args) and args[0] == _MYSQL_DUPLICATE_ENTRY
