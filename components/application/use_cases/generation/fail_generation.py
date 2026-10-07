import logging

from components.domain.entities.balance_transaction import BalanceTransaction
from components.domain.entities.generation import Generation
from components.domain.protocols.billing.balance_cache import IBalanceCache
from components.domain.protocols.shared.unit_of_work import IUnitOfWork
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus

logger = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 500


class FailGeneration:
    """Переводит генерацию в failed и возвращает пользователю её стоимость."""

    def __init__(self, uow: IUnitOfWork, balance_cache: IBalanceCache) -> None:
        self._uow = uow
        self._balance_cache = balance_cache

    async def execute(self, generation: Generation, error: str) -> bool:
        """Возвращает True, если генерация переведена в failed именно этим вызовом."""
        async with self._uow as uow:
            # Возврат привязан к смене статуса: он выполняется в той же
            # транзакции и только если статус сменили мы. Повторная доставка
            # сообщения, второй воркер или сверка увидят уже failed и не вернут
            # кредиты второй раз.
            failed = await uow.generations.change_status(
                generation.id,
                expected=(GenerationStatus.PENDING, GenerationStatus.PROCESSING),
                new=GenerationStatus.FAILED,
                error=error[:_MAX_ERROR_LENGTH],
            )
            if not failed:
                logger.info(
                    "Skip failing generation, it is already finished generation_id=%s",
                    generation.id,
                )
                return False

            await uow.balances.increment(generation.user_id, generation.cost)
            await uow.transactions.add(
                BalanceTransaction(
                    user_id=generation.user_id,
                    amount=generation.cost,
                    kind=TransactionKind.GENERATION_REFUND,
                    reference_id=generation.id,
                )
            )
            await uow.commit()

        await self._balance_cache.invalidate(generation.user_id)
        logger.warning(
            "Generation failed, credits refunded "
            "generation_id=%s user_id=%s refund=%s error=%s",
            generation.id,
            generation.user_id,
            generation.cost,
            error,
        )
        return True
