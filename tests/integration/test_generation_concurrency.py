import asyncio

import pytest

from components.application.use_cases.generation.create_generation import (
    CreateGeneration,
)
from components.domain.entities.generation import Generation
from components.domain.errors.billing.errors import InsufficientCreditsError

PARALLEL = 10
COST = 10


def make_use_case(new_uow, new_fail_generation, task_publisher, balance_cache):
    return CreateGeneration(
        uow=new_uow(),
        task_publisher=task_publisher,
        balance_cache=balance_cache,
        fail_generation=new_fail_generation(),
        generation_cost=COST,
    )


async def create_in_parallel(
    new_uow, new_fail_generation, task_publisher, balance_cache
) -> tuple[list[Generation], list[BaseException]]:
    results = await asyncio.gather(
        *(
            make_use_case(
                new_uow, new_fail_generation, task_publisher, balance_cache
            ).execute("user-1", "a cat in space")
            for _ in range(PARALLEL)
        ),
        return_exceptions=True,
    )
    created = [result for result in results if isinstance(result, Generation)]
    errors = [result for result in results if isinstance(result, BaseException)]
    return created, errors


@pytest.mark.parametrize("affordable", [1, 3])
async def test_parallel_requests_cannot_spend_more_than_the_balance(
    db, new_uow, new_fail_generation, task_publisher, balance_cache, affordable
) -> None:
    await db.set_balance("user-1", COST * affordable)

    created, errors = await create_in_parallel(
        new_uow, new_fail_generation, task_publisher, balance_cache
    )

    assert len(created) == affordable
    assert len(errors) == PARALLEL - affordable
    assert all(isinstance(error, InsufficientCreditsError) for error in errors)
    assert await db.balance("user-1") == 0
    assert await db.scalar("SELECT COUNT(*) FROM generations") == affordable
    assert sorted(await db.transactions()) == sorted(
        ("generation_debit", -COST, generation.id) for generation in created
    )
    assert sorted(task_publisher.published) == sorted(g.id for g in created)


async def test_user_without_balance_row_gets_insufficient_credits(
    db, new_uow, new_fail_generation, task_publisher, balance_cache
) -> None:
    with pytest.raises(InsufficientCreditsError) as exc_info:
        await make_use_case(
            new_uow, new_fail_generation, task_publisher, balance_cache
        ).execute("nobody", "a cat in space")

    assert exc_info.value.available == 0
    assert await db.scalar("SELECT COUNT(*) FROM generations") == 0
    assert await db.scalar("SELECT COUNT(*) FROM balance_transactions") == 0
