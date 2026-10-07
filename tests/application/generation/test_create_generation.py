import pytest

from components.application.use_cases.generation.create_generation import (
    CreateGeneration,
)
from components.application.use_cases.generation.fail_generation import (
    FailGeneration,
)
from components.domain.errors.billing.errors import InsufficientCreditsError
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus
from tests.fakes.in_memory_services import FailingTaskPublisher
from tests.fakes.in_memory_unit_of_work import InMemoryUnitOfWork


class CommitAwarePublisher:
    """Запоминает, сколько коммитов было сделано к моменту публикации."""

    def __init__(self, uow: InMemoryUnitOfWork) -> None:
        self._uow = uow
        self.commits_at_publish: list[int] = []

    async def publish(self, generation_id: str) -> None:
        self.commits_at_publish.append(self._uow.commits)


def make_use_case(uow, task_publisher, balance_cache) -> CreateGeneration:
    return CreateGeneration(
        uow=uow,
        task_publisher=task_publisher,
        balance_cache=balance_cache,
        fail_generation=FailGeneration(uow, balance_cache),
        generation_cost=10,
    )


async def test_debits_credits_and_publishes_task(
    uow, storage, task_publisher, balance_cache
) -> None:
    storage.balances["user-1"] = 25
    balance_cache.values["user-1"] = 25

    generation = await make_use_case(uow, task_publisher, balance_cache).execute(
        "user-1", "a cat in space"
    )

    assert generation.status == GenerationStatus.PENDING
    assert storage.balances["user-1"] == 15
    assert storage.generations[generation.id].cost == 10
    assert storage.transactions[-1].kind == TransactionKind.GENERATION_DEBIT
    assert storage.transactions[-1].amount == -10
    assert task_publisher.published == [generation.id]
    assert "user-1" not in balance_cache.values


async def test_insufficient_credits(
    uow, storage, task_publisher, balance_cache
) -> None:
    storage.balances["user-1"] = 5

    with pytest.raises(InsufficientCreditsError):
        await make_use_case(uow, task_publisher, balance_cache).execute(
            "user-1", "a cat in space"
        )

    assert storage.balances["user-1"] == 5
    assert storage.generations == {}
    assert task_publisher.published == []


async def test_insufficient_credits_error_reports_available_balance(
    uow, storage, task_publisher, balance_cache
) -> None:
    storage.balances["user-1"] = 5

    with pytest.raises(InsufficientCreditsError) as exc_info:
        await make_use_case(uow, task_publisher, balance_cache).execute(
            "user-1", "a cat in space"
        )

    assert exc_info.value.required == 10
    assert exc_info.value.available == 5


async def test_user_without_balance_cannot_generate(
    uow, storage, task_publisher, balance_cache
) -> None:
    with pytest.raises(InsufficientCreditsError) as exc_info:
        await make_use_case(uow, task_publisher, balance_cache).execute(
            "nobody", "a cat in space"
        )

    assert exc_info.value.available == 0
    assert storage.balances == {}
    assert storage.transactions == []


async def test_balance_for_one_generation_allows_exactly_one(
    uow, storage, task_publisher, balance_cache
) -> None:
    storage.balances["user-1"] = 10
    use_case = make_use_case(uow, task_publisher, balance_cache)

    first = await use_case.execute("user-1", "a cat in space")
    with pytest.raises(InsufficientCreditsError):
        await use_case.execute("user-1", "a cat in space")

    assert storage.balances["user-1"] == 0
    assert list(storage.generations) == [first.id]
    assert [t.amount for t in storage.transactions] == [-10]
    assert task_publisher.published == [first.id]


async def test_task_is_published_only_after_commit(uow, storage, balance_cache) -> None:
    storage.balances["user-1"] = 10
    publisher = CommitAwarePublisher(uow)

    await make_use_case(uow, publisher, balance_cache).execute("user-1", "a cat")

    assert publisher.commits_at_publish == [1]


async def test_publish_failure_fails_generation_and_refunds_credits(
    uow, storage, balance_cache
) -> None:
    storage.balances["user-1"] = 10
    balance_cache.values["user-1"] = 10

    with pytest.raises(ConnectionError):
        await make_use_case(uow, FailingTaskPublisher(), balance_cache).execute(
            "user-1", "a cat"
        )

    (generation,) = storage.generations.values()
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == "Could not enqueue the generation task"
    assert storage.balances["user-1"] == 10
    assert [(t.kind, t.amount) for t in storage.transactions] == [
        (TransactionKind.GENERATION_DEBIT, -10),
        (TransactionKind.GENERATION_REFUND, 10),
    ]
    assert "user-1" not in balance_cache.values
