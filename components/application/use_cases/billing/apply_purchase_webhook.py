import logging

from components.domain.entities.balance_transaction import BalanceTransaction
from components.domain.entities.purchase_event import PurchaseEvent
from components.domain.errors.billing.errors import DuplicatePurchaseEventError
from components.domain.protocols.billing.balance_cache import IBalanceCache
from components.domain.protocols.shared.unit_of_work import IUnitOfWork
from components.domain.types.enums.billing.purchase_result import PurchaseResult
from components.domain.types.enums.billing.transaction_kind import TransactionKind

logger = logging.getLogger(__name__)


class ApplyPurchaseWebhook:
    def __init__(self, uow: IUnitOfWork, balance_cache: IBalanceCache) -> None:
        self._uow = uow
        self._balance_cache = balance_cache

    async def execute(self, event: PurchaseEvent) -> PurchaseResult:
        result = await self._apply(event)
        # Кэш сбрасываем после коммита и на дубликате тоже: если первый запрос
        # закоммитил начисление, но упал до сброса кэша, повтор вебхука его починит.
        await self._balance_cache.invalidate(event.user_id)
        return result

    async def _apply(self, event: PurchaseEvent) -> PurchaseResult:
        async with self._uow as uow:
            # Проверки «есть ли уже такое событие» нет намеренно: между проверкой
            # и вставкой успевает пройти параллельный повтор вебхука. Дубликат
            # определяет уникальный индекс по event_id, поэтому вставка идёт первой.
            try:
                await uow.purchase_events.add(event)
            except DuplicatePurchaseEventError:
                logger.info(
                    "Duplicate purchase event event_id=%s user_id=%s",
                    event.event_id,
                    event.user_id,
                )
                return PurchaseResult.DUPLICATE

            await uow.balances.increment(event.user_id, event.credits)
            await uow.transactions.add(
                BalanceTransaction(
                    user_id=event.user_id,
                    amount=event.credits,
                    kind=TransactionKind.PURCHASE,
                    reference_id=event.event_id,
                )
            )
            await uow.commit()

        logger.info(
            "Purchase applied event_id=%s user_id=%s credits=%s",
            event.event_id,
            event.user_id,
            event.credits,
        )
        return PurchaseResult.PROCESSED
