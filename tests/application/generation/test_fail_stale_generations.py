from datetime import datetime, timedelta

from components.application.use_cases.generation.fail_stale_generations import (
    FailStaleGenerations,
)
from components.domain.entities.generation import Generation
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus

STALE_AFTER_SECONDS = 600


def put_generation(
    storage,
    generation_id: str,
    status: GenerationStatus,
    *,
    age_seconds: int,
) -> None:
    storage.generations[generation_id] = Generation(
        id=generation_id,
        user_id="user-1",
        prompt="a cat in space",
        status=status,
        cost=10,
        updated_at=datetime.now() - timedelta(seconds=age_seconds),
    )
    storage.balances.setdefault("user-1", 0)


def make_use_case(uow, fail_generation, batch_size: int = 100) -> FailStaleGenerations:
    return FailStaleGenerations(
        uow,
        fail_generation,
        stale_after_seconds=STALE_AFTER_SECONDS,
        batch_size=batch_size,
    )


async def test_stale_processing_generation_is_failed_and_refunded(
    uow, storage, fail_generation
) -> None:
    put_generation(storage, "gen-1", GenerationStatus.PROCESSING, age_seconds=3600)

    failed = await make_use_case(uow, fail_generation).execute()

    assert failed == 1
    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == "Generation was not finished in time"
    assert storage.balances["user-1"] == 10
    (refund,) = storage.transactions
    assert (refund.kind, refund.amount, refund.reference_id) == (
        TransactionKind.GENERATION_REFUND,
        10,
        "gen-1",
    )


async def test_stale_pending_generation_is_failed_and_refunded(
    uow, storage, fail_generation
) -> None:
    put_generation(storage, "gen-1", GenerationStatus.PENDING, age_seconds=3600)

    await make_use_case(uow, fail_generation).execute()

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == "Generation was not picked up for processing in time"
    assert storage.balances["user-1"] == 10


async def test_fresh_and_finished_generations_are_left_alone(
    uow, storage, fail_generation
) -> None:
    put_generation(storage, "fresh", GenerationStatus.PROCESSING, age_seconds=30)
    put_generation(storage, "done", GenerationStatus.COMPLETED, age_seconds=3600)
    put_generation(storage, "failed", GenerationStatus.FAILED, age_seconds=3600)

    failed = await make_use_case(uow, fail_generation).execute()

    assert failed == 0
    assert storage.generations["fresh"].status == GenerationStatus.PROCESSING
    assert storage.generations["done"].status == GenerationStatus.COMPLETED
    assert storage.transactions == []
    assert storage.balances["user-1"] == 0


async def test_repeated_pass_does_not_refund_twice(
    uow, storage, fail_generation
) -> None:
    put_generation(storage, "gen-1", GenerationStatus.PROCESSING, age_seconds=3600)
    use_case = make_use_case(uow, fail_generation)

    first = await use_case.execute()
    second = await use_case.execute()

    assert (first, second) == (1, 0)
    assert storage.balances["user-1"] == 10
    assert len(storage.transactions) == 1


async def test_pass_is_limited_by_batch_size_and_takes_oldest_first(
    uow, storage, fail_generation
) -> None:
    put_generation(storage, "newer", GenerationStatus.PROCESSING, age_seconds=1000)
    put_generation(storage, "oldest", GenerationStatus.PROCESSING, age_seconds=5000)

    failed = await make_use_case(uow, fail_generation, batch_size=1).execute()

    assert failed == 1
    assert storage.generations["oldest"].status == GenerationStatus.FAILED
    assert storage.generations["newer"].status == GenerationStatus.PROCESSING
