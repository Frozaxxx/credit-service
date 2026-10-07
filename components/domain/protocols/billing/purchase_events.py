from typing import Protocol

from components.domain.entities.purchase_event import PurchaseEvent


class IPurchaseEventRepository(Protocol):
    async def add(self, event: PurchaseEvent) -> None:
        """Сохраняет событие покупки.

        Raises:
            DuplicatePurchaseEventError: событие с таким event_id уже сохранено.
        """
        ...
