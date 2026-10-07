import asyncio
import logging
from collections.abc import Awaitable, Callable

from components.application.use_cases.generation.fail_generation import (
    FailGeneration,
)
from components.domain.entities.generation import Generation
from components.domain.errors.provider.errors import (
    ProviderPermanentError,
    ProviderTemporaryError,
)
from components.domain.protocols.generation.generation_provider import (
    IGenerationProvider,
)
from components.domain.protocols.shared.unit_of_work import IUnitOfWork
from components.domain.types.enums.generation.generation_status import GenerationStatus

logger = logging.getLogger(__name__)


class ProcessGeneration:
    def __init__(
        self,
        uow: IUnitOfWork,
        provider: IGenerationProvider,
        fail_generation: FailGeneration,
        *,
        max_attempts: int,
        attempt_timeout_seconds: float,
        retry_base_delay_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._uow = uow
        self._provider = provider
        self._fail_generation = fail_generation
        self._max_attempts = max_attempts
        self._attempt_timeout_seconds = attempt_timeout_seconds
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._sleep = sleep

    async def execute(self, generation_id: str) -> None:
        generation = await self._claim(generation_id)
        if generation is None:
            return

        try:
            result_url = await self._generate_with_retries(generation)
        except ProviderPermanentError as exc:
            await self._fail_generation.execute(
                generation,
                f"Provider rejected the request: {exc}",
            )
        except ProviderTemporaryError as exc:
            await self._fail_generation.execute(
                generation,
                f"Provider is unavailable, gave up after {self._max_attempts} "
                f"attempts. Last error: {exc}",
            )
        except Exception:
            logger.exception(
                "Unexpected error while calling provider generation_id=%s",
                generation_id,
            )
            await self._fail_generation.execute(
                generation,
                "Internal error while processing the generation",
            )
        else:
            await self._complete(generation, result_url)

    async def _claim(self, generation_id: str) -> Generation | None:
        """Забирает генерацию в работу: pending -> processing."""
        async with self._uow as uow:
            generation = await uow.generations.get(generation_id)
            if generation is None:
                logger.warning("Generation not found generation_id=%s", generation_id)
                return None

            claimed = await uow.generations.change_status(
                generation_id,
                expected=(GenerationStatus.PENDING,),
                new=GenerationStatus.PROCESSING,
            )
            if not claimed:
                # Повторная доставка сообщения или генерацию уже забрал другой воркер.
                logger.info(
                    "Skip generation, it is not pending generation_id=%s status=%s",
                    generation_id,
                    generation.status,
                )
                return None
            await uow.commit()

        logger.info(
            "Generation processing started generation_id=%s user_id=%s",
            generation_id,
            generation.user_id,
        )
        return generation

    async def _generate_with_retries(self, generation: Generation) -> str:
        """Вызывает провайдера с повторами.

        Raises:
            ProviderPermanentError: провайдер отказал окончательно.
            ProviderTemporaryError: попытки закончились, внутри последняя ошибка.
        """
        attempt = 1
        while True:
            try:
                return await self._call_provider(generation.prompt)
            except ProviderTemporaryError as exc:
                if attempt >= self._max_attempts:
                    logger.warning(
                        "Provider attempts exhausted generation_id=%s attempts=%s "
                        "error=%s",
                        generation.id,
                        attempt,
                        exc,
                    )
                    raise
                delay = self._retry_base_delay_seconds * 2 ** (attempt - 1)
                logger.warning(
                    "Provider attempt failed, will retry generation_id=%s "
                    "attempt=%s/%s retry_in=%ss error=%s",
                    generation.id,
                    attempt,
                    self._max_attempts,
                    delay,
                    exc,
                )
                await self._sleep(delay)
                attempt += 1

    async def _call_provider(self, prompt: str) -> str:
        """Одна попытка с собственным таймаутом. Таймаут — временная ошибка."""
        try:
            async with asyncio.timeout(self._attempt_timeout_seconds):
                return await self._provider.generate(prompt)
        except TimeoutError as exc:
            raise ProviderTemporaryError(
                f"Provider did not respond in {self._attempt_timeout_seconds:g}s"
            ) from exc

    async def _complete(self, generation: Generation, result_url: str) -> None:
        async with self._uow as uow:
            completed = await uow.generations.change_status(
                generation.id,
                expected=(GenerationStatus.PROCESSING,),
                new=GenerationStatus.COMPLETED,
                result_url=result_url,
            )
            await uow.commit()

        if completed:
            logger.info("Generation completed generation_id=%s", generation.id)
        else:
            # Пока ждали провайдера, генерацию уже перевели в failed и вернули
            # кредиты. Результат не сохраняем, чтобы не отдать его бесплатно.
            logger.warning(
                "Provider result discarded, generation is no longer processing "
                "generation_id=%s",
                generation.id,
            )
