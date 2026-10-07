from components.application.use_cases.billing.apply_purchase_webhook import (
    ApplyPurchaseWebhook,
)
from components.application.use_cases.billing.get_user_balance import GetUserBalance
from components.domain.entities.purchase_event import PurchaseEvent
from components.domain.types.enums.billing.purchase_result import PurchaseResult
from components.domain.types.enums.billing.transaction_kind import TransactionKind


def make_event(event_id: str = "evt-1", credits: int = 50) -> PurchaseEvent:
    return PurchaseEvent(
        event_id=event_id,
        user_id="user-1",
        product_id="credits_50",
        credits=credits,
    )


async def test_new_event_credits_balance(uow, storage, balance_cache) -> None:
    result = await ApplyPurchaseWebhook(uow, balance_cache).execute(make_event())

    assert result == PurchaseResult.PROCESSED
    assert storage.balances["user-1"] == 50
    assert [t.kind for t in storage.transactions] == [TransactionKind.PURCHASE]
    assert uow.commits == 1


async def test_repeated_event_is_ignored(uow, storage, balance_cache) -> None:
    use_case = ApplyPurchaseWebhook(uow, balance_cache)

    await use_case.execute(make_event())
    result = await use_case.execute(make_event())

    assert result == PurchaseResult.DUPLICATE
    assert storage.balances["user-1"] == 50
    assert len(storage.transactions) == 1
    assert len(storage.purchase_events) == 1
    assert uow.commits == 1


async def test_same_event_for_many_retries_is_credited_once(
    uow, storage, balance_cache
) -> None:
    use_case = ApplyPurchaseWebhook(uow, balance_cache)

    results = [await use_case.execute(make_event()) for _ in range(5)]

    assert results.count(PurchaseResult.PROCESSED) == 1
    assert results.count(PurchaseResult.DUPLICATE) == 4
    assert storage.balances["user-1"] == 50


async def test_different_events_are_credited_separately(
    uow, storage, balance_cache
) -> None:
    use_case = ApplyPurchaseWebhook(uow, balance_cache)

    await use_case.execute(make_event("evt-1", credits=50))
    await use_case.execute(make_event("evt-2", credits=30))

    assert storage.balances["user-1"] == 80
    assert [t.reference_id for t in storage.transactions] == ["evt-1", "evt-2"]


async def test_balance_is_fresh_right_after_purchase(
    uow, storage, balance_cache
) -> None:
    storage.balances["user-1"] = 20
    get_balance = GetUserBalance(uow, balance_cache)
    assert await get_balance.execute("user-1") == 20  # прогрели кэш

    await ApplyPurchaseWebhook(uow, balance_cache).execute(make_event(credits=50))

    assert await get_balance.execute("user-1") == 70


async def test_duplicate_event_also_drops_cached_balance(
    uow, storage, balance_cache
) -> None:
    use_case = ApplyPurchaseWebhook(uow, balance_cache)
    await use_case.execute(make_event())
    balance_cache.values["user-1"] = 0  # устаревшее значение пережило первый запрос

    await use_case.execute(make_event())

    assert "user-1" not in balance_cache.values
