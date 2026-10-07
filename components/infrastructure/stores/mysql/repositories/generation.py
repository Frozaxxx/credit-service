from collections.abc import Collection
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from components.domain.entities.generation import Generation
from components.domain.protocols.generation.generations import (
    IGenerationRepository,
)
from components.domain.types.enums.generation.generation_status import GenerationStatus
from components.infrastructure.stores.mysql.mappers.generation import (
    generation_to_domain,
    generation_to_model,
)
from components.infrastructure.stores.mysql.models.generation import Generations


class GenerationRepository(IGenerationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, generation: Generation) -> None:
        self._session.add(generation_to_model(generation))
        await self._session.flush()

    async def get(self, generation_id: str) -> Generation | None:
        model = await self._session.scalar(
            select(Generations).where(Generations.id == generation_id)
        )
        return None if model is None else generation_to_domain(model)

    async def list_stale(
        self,
        *,
        older_than_seconds: int,
        limit: int,
    ) -> list[Generation]:
        # Границу считаем по часам БД: ими же проставляется updated_at.
        threshold = func.timestampadd(text("SECOND"), -older_than_seconds, func.now())
        result = await self._session.scalars(
            select(Generations)
            .where(
                Generations.status.in_(
                    [
                        GenerationStatus.PENDING.value,
                        GenerationStatus.PROCESSING.value,
                    ]
                ),
                Generations.updated_at < threshold,
            )
            .order_by(Generations.updated_at)
            .limit(limit)
        )
        return [generation_to_domain(model) for model in result]

    async def change_status(
        self,
        generation_id: str,
        *,
        expected: Collection[GenerationStatus],
        new: GenerationStatus,
        result_url: str | None = None,
        error: str | None = None,
    ) -> bool:
        # Проверка текущего статуса и запись нового — один UPDATE. Из двух
        # конкурентов строку обновит только один, второй получит rowcount == 0.
        result = await self._session.execute(
            update(Generations)
            .where(
                Generations.id == generation_id,
                Generations.status.in_([status.value for status in expected]),
            )
            .values(
                status=new.value,
                result_url=result_url,
                error=error,
                # Явно, а не через onupdate модели: по updated_at сверка
                # определяет зависшие генерации.
                updated_at=func.now(),
            )
        )
        return cast(CursorResult[Any], result).rowcount == 1
