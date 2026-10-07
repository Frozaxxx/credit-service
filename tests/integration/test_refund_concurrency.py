import asyncio

from components.application.use_cases.generation.process_generation import (
    ProcessGeneration,
)
from components.domain.entities.generation import Generation
from components.domain.errors.provider.errors import (
    ProviderPermanentError,
    ProviderTemporaryError,
)
from components.domain.types.enums.generation.generation_status import GenerationStatus
from tests.fakes.in_memory_services import RecordingSleep

PARALLEL = 10
RESULT_URL = "https://cdn.example.com/r.png"


class CountingProvider:
    """Считает вызовы и отвечает одним и тем же исходом."""

    def __init__(self, outcome: Exception | str) -> None:
        self._outcome = outcome
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1
        await asyncio.sleep(0.05)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def make_generation(status: GenerationStatus) -> Generation:
    return Generation(
        id="gen-1",
        user_id="user-1",
        prompt="a cat in space",
        status=status,
        cost=10,
    )


def make_process(new_uow, new_fail_generation, provider) -> ProcessGeneration:
    return ProcessGeneration(
        new_uow(),
        provider,
        new_fail_generation(),
        max_attempts=3,
        attempt_timeout_seconds=5,
        retry_base_delay_seconds=1,
        sleep=RecordingSleep(),
    )


async def test_parallel_failures_of_one_generation_refund_once(
    db, new_fail_generation, balance_cache
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("gen-1", "processing")
    generation = make_generation(GenerationStatus.PROCESSING)

    results = await asyncio.gather(
        *(
            new_fail_generation().execute(generation, "provider is down")
            for _ in range(PARALLEL)
        )
    )

    assert results.count(True) == 1
    assert await db.balance("user-1") == 10
    assert await db.transactions() == [("generation_refund", 10, "gen-1")]
    row = await db.generation("gen-1")
    assert (row.status, row.error) == ("failed", "provider is down")


async def test_refund_creates_balance_row_if_it_is_missing(
    db, new_fail_generation
) -> None:
    await db.add_generation("gen-1", "pending")

    await new_fail_generation().execute(
        make_generation(GenerationStatus.PENDING), "not enqueued"
    )

    assert await db.balance("user-1") == 10


async def test_redelivered_messages_call_provider_once(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("gen-1", "pending")
    provider = CountingProvider(RESULT_URL)

    await asyncio.gather(
        *(
            make_process(new_uow, new_fail_generation, provider).execute("gen-1")
            for _ in range(PARALLEL)
        )
    )

    assert provider.calls == 1
    row = await db.generation("gen-1")
    assert (row.status, row.result_url, row.error) == ("completed", RESULT_URL, None)
    assert await db.transactions() == []


async def test_redelivered_messages_of_rejected_generation_refund_once(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("gen-1", "pending")
    provider = CountingProvider(ProviderPermanentError("rejected by content policy"))

    await asyncio.gather(
        *(
            make_process(new_uow, new_fail_generation, provider).execute("gen-1")
            for _ in range(PARALLEL)
        )
    )

    assert provider.calls == 1
    row = await db.generation("gen-1")
    assert row.status == "failed"
    assert row.error == "Provider rejected the request: rejected by content policy"
    assert await db.balance("user-1") == 10
    assert await db.transactions() == [("generation_refund", 10, "gen-1")]


async def test_exhausted_attempts_end_in_failed_with_refund(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("gen-1", "pending")
    provider = CountingProvider(ProviderTemporaryError("503"))

    await make_process(new_uow, new_fail_generation, provider).execute("gen-1")

    assert provider.calls == 3
    row = await db.generation("gen-1")
    assert row.status == "failed"
    assert "gave up after 3 attempts" in row.error
    assert await db.transactions() == [("generation_refund", 10, "gen-1")]


async def test_late_provider_result_does_not_overwrite_failed_generation(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("gen-1", "pending")

    class FailedMeanwhileProvider:
        """Пока провайдер отвечал, генерацию перевела в failed фоновая сверка."""

        async def generate(self, prompt: str) -> str:
            await new_fail_generation().execute(
                make_generation(GenerationStatus.PROCESSING), "stale"
            )
            return RESULT_URL

    await make_process(new_uow, new_fail_generation, FailedMeanwhileProvider()).execute(
        "gen-1"
    )

    row = await db.generation("gen-1")
    assert (row.status, row.result_url, row.error) == ("failed", None, "stale")
    assert await db.transactions() == [("generation_refund", 10, "gen-1")]
