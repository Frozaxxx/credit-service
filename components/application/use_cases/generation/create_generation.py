import logging
from uuid import uuid4

from components.domain.entities.balance_transaction import BalanceTransaction
from components.domain.entities.generation import Generation
from components.domain.errors.billing.errors import InsufficientCreditsError
from components.domain.protocols.billing.balance_cache import IBalanceCache
from components.domain.protocols.generation.generation_task_publisher import (
    IGenerationTaskPublisher,
)
from components.domain.protocols.shared.unit_of_work import IUnitOfWork
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus

logger = logging.getLogger(__name__)


class CreateGeneration:
    def __init__(
        self,
        uow: IUnitOfWork,
        task_publisher: IGenerationTaskPublisher,
        balance_cache: IBalanceCache,
        generation_cost: int,
    ) -> None:
        self._uow = uow
        self._task_publisher = task_publisher
        self._balance_cache = balance_cache
        self._generation_cost = generation_cost

    async def execute(self, user_id: str, prompt: str) -> Generation:
        generation = Generation(
            id=uuid4().hex,
            user_id=user_id,
            prompt=prompt,
            status=GenerationStatus.PENDING,
            cost=self._generation_cost,
        )

        async with self._uow as uow:
            # Нельзя читать баланс и потом записывать новое значение: два
            # параллельных запроса прочитают одно и то же и спишут один раз.
            # Проверку и списание делает один атомарный запрос в БД.
            if not await uow.balances.debit(user_id, generation.cost):
                balance = await uow.balances.get(user_id)
                raise InsufficientCreditsError(
                    user_id=user_id,
                    required=generation.cost,
                    available=0 if balance is None else balance.balance,
                )

            await uow.generations.add(generation)
            await uow.transactions.add(
                BalanceTransaction(
                    user_id=user_id,
                    amount=-generation.cost,
                    kind=TransactionKind.GENERATION_DEBIT,
                    reference_id=generation.id,
                )
            )
            await uow.commit()

        # Задачу публикуем только после коммита. Иначе воркер может получить
        # сообщение раньше, чем генерация станет видна в БД, не найти её
        # и подтвердить сообщение — генерация навсегда останется в pending.
        try:
            await self._task_publisher.publish(generation.id)
        except Exception:
            logger.exception(
                "Generation task publish failed, generation left in pending "
                "generation_id=%s user_id=%s",
                generation.id,
                user_id,
            )
            raise
        finally:
            await self._balance_cache.invalidate(user_id)

        logger.info(
            "Generation created generation_id=%s user_id=%s cost=%s",
            generation.id,
            user_id,
            generation.cost,
        )
        return generation
