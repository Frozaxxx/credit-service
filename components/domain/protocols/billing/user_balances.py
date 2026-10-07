from typing import Protocol

from components.domain.entities.user_balance import UserBalance


class IUserBalanceRepository(Protocol):
    async def get(self, user_id: str) -> UserBalance | None: ...

    async def debit(self, user_id: str, amount: int) -> bool:
        """Атомарно списывает amount, если на балансе достаточно средств.

        Возвращает False и ничего не меняет, если средств не хватает
        или у пользователя ещё нет баланса.
        """
        ...

    async def increment(self, user_id: str, delta: int) -> None:
        """Прибавляет delta к балансу, создавая запись пользователя при необходимости."""
        ...
