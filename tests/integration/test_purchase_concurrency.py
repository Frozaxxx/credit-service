import asyncio

from components.application.use_cases.billing.apply_purchase_webhook import (
    ApplyPurchaseWebhook,
)
from components.domain.entities.purchase_event import PurchaseEvent
from components.domain.types.enums.billing.purchase_result import PurchaseResult

PARALLEL = 10


def make_event(event_id: str, credits: int = 50) -> PurchaseEvent:
    return PurchaseEvent(
        event_id=event_id,
        user_id="user-1",
        product_id="credits_50",
        credits=credits,
    )


async def test_parallel_retries_of_one_webhook_credit_once(
    db, new_uow, balance_cache
) -> None:
    results = await asyncio.gather(
        *(
            ApplyPurchaseWebhook(new_uow(), balance_cache).execute(make_event("evt-1"))
            for _ in range(PARALLEL)
        )
    )

    assert results.count(PurchaseResult.PROCESSED) == 1
    assert results.count(PurchaseResult.DUPLICATE) == PARALLEL - 1
    assert await db.balance("user-1") == 50
    assert await db.scalar("SELECT COUNT(*) FROM purchase_events") == 1
    assert await db.transactions() == [("purchase", 50, "evt-1")]


async def test_sequential_retry_is_a_duplicate(db, new_uow, balance_cache) -> None:
    first = await ApplyPurchaseWebhook(new_uow(), balance_cache).execute(
        make_event("evt-1")
    )
    second = await ApplyPurchaseWebhook(new_uow(), balance_cache).execute(
        make_event("evt-1")
    )

    assert (first, second) == (PurchaseResult.PROCESSED, PurchaseResult.DUPLICATE)
    assert await db.balance("user-1") == 50


async def test_parallel_different_purchases_are_all_credited(
    db, new_uow, balance_cache
) -> None:
    await db.set_balance("user-1", 0)

    results = await asyncio.gather(
        *(
            ApplyPurchaseWebhook(new_uow(), balance_cache).execute(
                make_event(f"evt-{number}")
            )
            for number in range(PARALLEL)
        )
    )

    assert results == [PurchaseResult.PROCESSED] * PARALLEL
    assert await db.balance("user-1") == 50 * PARALLEL
    assert len(await db.transactions()) == PARALLEL
