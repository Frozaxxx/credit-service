from collections.abc import Collection
from dataclasses import replace

from components.domain.entities.balance_transaction import BalanceTransaction
from components.domain.entities.generation import Generation
from components.domain.entities.purchase_event import PurchaseEvent
from components.domain.entities.user_balance import UserBalance
from components.domain.errors.billing.errors import DuplicatePurchaseEventError
from components.domain.types.enums.generation.generation_status import GenerationStatus
from tests.fakes.in_memory_storage import InMemoryStorage


class InMemoryUserBalanceRepository:
    def __init__(self, storage: InMemoryStorage) -> None:
        self._storage = storage

    async def get(self, user_id: str) -> UserBalance | None:
        if user_id not in self._storage.balances:
            return None
        return UserBalance(user_id=user_id, balance=self._storage.balances[user_id])

    async def debit(self, user_id: str, amount: int) -> bool:
        if self._storage.balances.get(user_id, 0) < amount:
            return False
        self._storage.balances[user_id] -= amount
        return True

    async def increment(self, user_id: str, delta: int) -> None:
        self._storage.balances[user_id] = self._storage.balances.get(user_id, 0) + delta


class InMemoryGenerationRepository:
    def __init__(self, storage: InMemoryStorage) -> None:
        self._storage = storage

    async def add(self, generation: Generation) -> None:
        self._storage.generations[generation.id] = replace(generation)

    async def get(self, generation_id: str) -> Generation | None:
        generation = self._storage.generations.get(generation_id)
        return None if generation is None else replace(generation)

    async def change_status(
        self,
        generation_id: str,
        *,
        expected: Collection[GenerationStatus],
        new: GenerationStatus,
        result_url: str | None = None,
        error: str | None = None,
    ) -> bool:
        generation = self._storage.generations.get(generation_id)
        if generation is None or generation.status not in expected:
            return False
        generation.status = new
        generation.result_url = result_url
        generation.error = error
        return True


class InMemoryPurchaseEventRepository:
    def __init__(self, storage: InMemoryStorage) -> None:
        self._storage = storage

    async def add(self, event: PurchaseEvent) -> None:
        # Повторяет уникальный индекс по event_id в MySQL.
        if any(e.event_id == event.event_id for e in self._storage.purchase_events):
            raise DuplicatePurchaseEventError(event.event_id)
        self._storage.purchase_events.append(event)


class InMemoryBalanceTransactionRepository:
    def __init__(self, storage: InMemoryStorage) -> None:
        self._storage = storage

    async def add(self, transaction: BalanceTransaction) -> None:
        self._storage.transactions.append(transaction)

    async def list_by_user(
        self,
        user_id: str,
        *,
        limit: int,
    ) -> list[BalanceTransaction]:
        items = [t for t in self._storage.transactions if t.user_id == user_id]
        return list(reversed(items))[:limit]
