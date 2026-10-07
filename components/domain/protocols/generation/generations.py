from collections.abc import Collection
from typing import Protocol

from components.domain.entities.generation import Generation
from components.domain.types.enums.generation.generation_status import GenerationStatus


class IGenerationRepository(Protocol):
    async def add(self, generation: Generation) -> None: ...

    async def get(self, generation_id: str) -> Generation | None: ...

    async def change_status(
        self,
        generation_id: str,
        *,
        expected: Collection[GenerationStatus],
        new: GenerationStatus,
        result_url: str | None = None,
        error: str | None = None,
    ) -> bool:
        """Атомарно переводит генерацию в статус new, если сейчас она в одном из expected.

        Возвращает False и ничего не меняет, если генерации нет или её статус
        уже другой: значит, переход успел сделать кто-то ещё.
        """
        ...
