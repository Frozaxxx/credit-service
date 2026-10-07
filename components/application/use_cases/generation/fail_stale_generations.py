import logging

from components.application.use_cases.generation.fail_generation import (
    FailGeneration,
)
from components.domain.protocols.shared.unit_of_work import IUnitOfWork
from components.domain.types.enums.generation.generation_status import GenerationStatus

logger = logging.getLogger(__name__)

_ERRORS = {
    GenerationStatus.PENDING: "Generation was not picked up for processing in time",
    GenerationStatus.PROCESSING: "Generation was not finished in time",
}


class FailStaleGenerations:
    """Сверка: зависшие генерации переводятся в failed с возвратом кредитов.

    Зависают они, когда задача не дошла до воркера (pending) или воркер упал
    посреди обработки (processing).
    """

    def __init__(
        self,
        uow: IUnitOfWork,
        fail_generation: FailGeneration,
        *,
        stale_after_seconds: int,
        batch_size: int,
    ) -> None:
        self._uow = uow
        self._fail_generation = fail_generation
        self._stale_after_seconds = stale_after_seconds
        self._batch_size = batch_size

    async def execute(self) -> int:
        """Возвращает число генераций, переведённых в failed этим запуском."""
        async with self._uow as uow:
            stale = await uow.generations.list_stale(
                older_than_seconds=self._stale_after_seconds,
                limit=self._batch_size,
            )

        failed = 0
        for generation in stale:
            logger.warning(
                "Stale generation found generation_id=%s user_id=%s status=%s "
                "updated_at=%s",
                generation.id,
                generation.user_id,
                generation.status,
                generation.updated_at,
            )
            # Если генерация успела завершиться после выборки, FailGeneration
            # ничего не сделает: статус он меняет условно.
            if await self._fail_generation.execute(
                generation,
                _ERRORS[generation.status],
            ):
                failed += 1

        if stale:
            logger.info(
                "Stale generations reconciled found=%s failed=%s",
                len(stale),
                failed,
            )
        return failed
