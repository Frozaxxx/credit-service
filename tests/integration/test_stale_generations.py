import asyncio

from components.application.use_cases.generation.fail_stale_generations import (
    FailStaleGenerations,
)
from components.domain.types.enums.generation.generation_status import GenerationStatus

STALE_AFTER_SECONDS = 600


def make_use_case(new_uow, new_fail_generation, batch_size: int = 100):
    return FailStaleGenerations(
        new_uow(),
        new_fail_generation(),
        stale_after_seconds=STALE_AFTER_SECONDS,
        batch_size=batch_size,
    )


async def test_only_stale_unfinished_generations_are_failed_and_refunded(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("stale-pending", "pending", age_seconds=3600)
    await db.add_generation("stale-processing", "processing", age_seconds=3600)
    await db.add_generation("fresh-processing", "processing", age_seconds=30)
    await db.add_generation("old-completed", "completed", age_seconds=3600)

    failed = await make_use_case(new_uow, new_fail_generation).execute()

    assert failed == 2
    assert (await db.generation("stale-pending")).status == "failed"
    assert (await db.generation("stale-processing")).status == "failed"
    assert (await db.generation("fresh-processing")).status == "processing"
    assert (await db.generation("old-completed")).status == "completed"
    assert await db.balance("user-1") == 20
    assert sorted(await db.transactions()) == [
        ("generation_refund", 10, "stale-pending"),
        ("generation_refund", 10, "stale-processing"),
    ]


async def test_parallel_reconcilers_refund_each_generation_once(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    for number in range(5):
        await db.add_generation(f"gen-{number}", "processing", age_seconds=3600)

    counts = await asyncio.gather(
        *(make_use_case(new_uow, new_fail_generation).execute() for _ in range(4))
    )

    assert sum(counts) == 5
    assert await db.balance("user-1") == 50
    assert len(await db.transactions()) == 5


async def test_batch_size_limits_one_pass_and_oldest_go_first(
    db, new_uow, new_fail_generation
) -> None:
    await db.set_balance("user-1", 0)
    await db.add_generation("newer", "processing", age_seconds=1000)
    await db.add_generation("oldest", "processing", age_seconds=5000)

    failed = await make_use_case(new_uow, new_fail_generation, batch_size=1).execute()

    assert failed == 1
    assert (await db.generation("oldest")).status == "failed"
    assert (await db.generation("newer")).status == "processing"


async def test_status_change_refreshes_updated_at(db, new_uow) -> None:
    # Сверка опирается на updated_at: смена статуса обязана его обновлять,
    # иначе только что взятая в работу старая генерация считалась бы зависшей.
    await db.add_generation("gen-1", "pending", age_seconds=3600)

    async with new_uow() as uow:
        await uow.generations.change_status(
            "gen-1",
            expected=(GenerationStatus.PENDING,),
            new=GenerationStatus.PROCESSING,
        )
        stale_before_commit = await uow.generations.list_stale(
            older_than_seconds=STALE_AFTER_SECONDS,
            limit=10,
        )
        await uow.commit()

    assert stale_before_commit == []
    age = await db.scalar(
        "SELECT TIMESTAMPDIFF(SECOND, updated_at, NOW()) FROM generations "
        "WHERE id = 'gen-1'"
    )
    assert age < 60
