import pytest

from components.domain.entities.generation import Generation
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus


def put_generation(storage, status: GenerationStatus) -> Generation:
    generation = Generation(
        id="gen-1",
        user_id="user-1",
        prompt="a cat in space",
        status=status,
        cost=10,
    )
    storage.generations[generation.id] = generation
    storage.balances["user-1"] = 0
    return generation


@pytest.mark.parametrize(
    "status", [GenerationStatus.PENDING, GenerationStatus.PROCESSING]
)
async def test_unfinished_generation_is_failed_and_refunded(
    storage, fail_generation, balance_cache, status
) -> None:
    generation = put_generation(storage, status)
    balance_cache.values["user-1"] = 0

    failed = await fail_generation.execute(generation, "provider is down")

    assert failed is True
    assert storage.generations["gen-1"].status == GenerationStatus.FAILED
    assert storage.generations["gen-1"].error == "provider is down"
    assert storage.balances["user-1"] == 10
    (refund,) = storage.transactions
    assert refund.kind == TransactionKind.GENERATION_REFUND
    assert refund.amount == 10
    assert refund.reference_id == "gen-1"
    assert refund.user_id == "user-1"
    assert "user-1" not in balance_cache.values


async def test_second_call_does_not_refund_again(storage, fail_generation) -> None:
    generation = put_generation(storage, GenerationStatus.PROCESSING)

    first = await fail_generation.execute(generation, "provider is down")
    second = await fail_generation.execute(generation, "another reason")

    assert (first, second) == (True, False)
    assert storage.balances["user-1"] == 10
    assert len(storage.transactions) == 1
    assert storage.generations["gen-1"].error == "provider is down"


async def test_completed_generation_is_not_failed_or_refunded(
    storage, fail_generation, uow
) -> None:
    generation = put_generation(storage, GenerationStatus.COMPLETED)

    failed = await fail_generation.execute(generation, "too late")

    assert failed is False
    assert storage.generations["gen-1"].status == GenerationStatus.COMPLETED
    assert storage.balances["user-1"] == 0
    assert storage.transactions == []
    assert uow.commits == 0


async def test_long_error_is_truncated_to_fit_the_column(
    storage, fail_generation
) -> None:
    generation = put_generation(storage, GenerationStatus.PROCESSING)

    await fail_generation.execute(generation, "x" * 2000)

    assert len(storage.generations["gen-1"].error) <= 512
