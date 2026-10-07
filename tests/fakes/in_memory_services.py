import asyncio


class InMemoryBalanceCache:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def get(self, user_id: str) -> int | None:
        return self.values.get(user_id)

    async def set(self, user_id: str, balance: int) -> None:
        self.values[user_id] = balance

    async def invalidate(self, user_id: str) -> None:
        self.values.pop(user_id, None)


class RecordingTaskPublisher:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def publish(self, generation_id: str) -> None:
        self.published.append(generation_id)


class FailingTaskPublisher:
    async def publish(self, generation_id: str) -> None:
        raise ConnectionError("broker is unavailable")


class StubGenerationProvider:
    def __init__(self, result_url: str = "https://cdn.example.com/r.png") -> None:
        self.result_url = result_url
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.result_url


class ScriptedGenerationProvider:
    """Отвечает по сценарию: на каждый вызов — следующая ошибка или URL."""

    def __init__(self, outcomes: list[Exception | str]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class HangingGenerationProvider:
    """Первые hang_calls вызовов зависают, остальные отвечают сразу."""

    def __init__(self, hang_calls: int, result_url: str = "https://cdn/r.png") -> None:
        self._hang_calls = hang_calls
        self.result_url = result_url
        self.calls = 0

    async def generate(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self._hang_calls:
            await asyncio.sleep(60)
        return self.result_url


class RecordingSleep:
    """Подмена asyncio.sleep: запоминает паузы и не ждёт."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
