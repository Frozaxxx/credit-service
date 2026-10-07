from components.application.use_cases.generation.process_generation import (
    ProcessGeneration,
)
from components.domain.entities.generation import Generation
from components.domain.errors.provider.errors import (
    ProviderPermanentError,
    ProviderTemporaryError,
)
from components.domain.types.enums.billing.transaction_kind import TransactionKind
from components.domain.types.enums.generation.generation_status import GenerationStatus
from tests.fakes.in_memory_services import (
    HangingGenerationProvider,
    RecordingSleep,
    ScriptedGenerationProvider,
)

RESULT_URL = "https://cdn.example.com/r.png"


def put_generation(
    storage, status: GenerationStatus = GenerationStatus.PENDING
) -> None:
    storage.generations["gen-1"] = Generation(
        id="gen-1",
        user_id="user-1",
        prompt="a cat in space",
        status=status,
        cost=10,
    )
    # Кредиты за генерацию уже списаны при её создании.
    storage.balances["user-1"] = 0


def make_use_case(
    uow,
    provider,
    fail_generation,
    *,
    sleep: RecordingSleep | None = None,
    max_attempts: int = 3,
    attempt_timeout_seconds: float = 5,
) -> ProcessGeneration:
    return ProcessGeneration(
        uow,
        provider,
        fail_generation,
        max_attempts=max_attempts,
        attempt_timeout_seconds=attempt_timeout_seconds,
        retry_base_delay_seconds=1,
        sleep=sleep or RecordingSleep(),
    )


def refunds(storage) -> list[tuple[int, str]]:
    return [
        (t.amount, t.reference_id)
        for t in storage.transactions
        if t.kind == TransactionKind.GENERATION_REFUND
    ]


async def test_completes_pending_generation(
    uow, storage, provider, fail_generation
) -> None:
    put_generation(storage)

    await make_use_case(uow, provider, fail_generation).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.COMPLETED
    assert generation.result_url == provider.result_url
    assert generation.error is None
    assert refunds(storage) == []


async def test_skips_already_processed_generation(
    uow, storage, provider, fail_generation
) -> None:
    put_generation(storage, GenerationStatus.COMPLETED)

    await make_use_case(uow, provider, fail_generation).execute("gen-1")

    assert provider.prompts == []


async def test_missing_generation_is_ignored(uow, provider, fail_generation) -> None:
    await make_use_case(uow, provider, fail_generation).execute("missing")

    assert provider.prompts == []


async def test_temporary_errors_are_retried_with_growing_pauses(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider(
        [ProviderTemporaryError("503"), ProviderTemporaryError("503"), RESULT_URL]
    )
    sleep = RecordingSleep()

    await make_use_case(uow, provider, fail_generation, sleep=sleep).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.COMPLETED
    assert generation.result_url == RESULT_URL
    assert provider.calls == 3
    assert sleep.delays == [1, 2]
    assert refunds(storage) == []


async def test_generation_fails_and_refunds_when_attempts_are_exhausted(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider([ProviderTemporaryError("503")] * 3)
    sleep = RecordingSleep()

    await make_use_case(uow, provider, fail_generation, sleep=sleep).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.result_url is None
    assert "gave up after 3 attempts" in generation.error
    assert "503" in generation.error
    assert provider.calls == 3
    assert sleep.delays == [1, 2]  # после последней попытки не ждём
    assert refunds(storage) == [(10, "gen-1")]
    assert storage.balances["user-1"] == 10


async def test_permanent_error_fails_immediately_without_retries(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider(
        [ProviderPermanentError("Request rejected by content policy")]
    )
    sleep = RecordingSleep()

    await make_use_case(uow, provider, fail_generation, sleep=sleep).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == (
        "Provider rejected the request: Request rejected by content policy"
    )
    assert provider.calls == 1
    assert sleep.delays == []
    assert refunds(storage) == [(10, "gen-1")]


async def test_timeout_is_retried_like_a_temporary_error(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = HangingGenerationProvider(hang_calls=1)

    await make_use_case(
        uow, provider, fail_generation, attempt_timeout_seconds=0.01
    ).execute("gen-1")

    assert storage.generations["gen-1"].status == GenerationStatus.COMPLETED
    assert provider.calls == 2
    assert refunds(storage) == []


async def test_timeout_applies_to_every_attempt_and_ends_in_failed(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = HangingGenerationProvider(hang_calls=10)

    await make_use_case(
        uow, provider, fail_generation, attempt_timeout_seconds=0.01
    ).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert "did not respond in 0.01s" in generation.error
    assert provider.calls == 3
    assert refunds(storage) == [(10, "gen-1")]


async def test_single_attempt_setting_disables_retries(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider([ProviderTemporaryError("503")])
    sleep = RecordingSleep()

    await make_use_case(
        uow, provider, fail_generation, sleep=sleep, max_attempts=1
    ).execute("gen-1")

    assert storage.generations["gen-1"].status == GenerationStatus.FAILED
    assert provider.calls == 1
    assert sleep.delays == []


async def test_unexpected_provider_error_fails_and_refunds(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider([RuntimeError("boom")])

    await make_use_case(uow, provider, fail_generation).execute("gen-1")

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.error == "Internal error while processing the generation"
    assert refunds(storage) == [(10, "gen-1")]


async def test_redelivered_message_does_not_refund_twice(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)
    provider = ScriptedGenerationProvider([ProviderPermanentError("rejected")])
    use_case = make_use_case(uow, provider, fail_generation)

    await use_case.execute("gen-1")
    await use_case.execute("gen-1")  # RabbitMQ доставил то же сообщение ещё раз

    assert provider.calls == 1
    assert refunds(storage) == [(10, "gen-1")]
    assert storage.balances["user-1"] == 10


async def test_redelivered_message_does_not_call_provider_for_processing_generation(
    uow, storage, provider, fail_generation
) -> None:
    put_generation(storage, GenerationStatus.PROCESSING)

    await make_use_case(uow, provider, fail_generation).execute("gen-1")

    assert provider.prompts == []
    assert storage.generations["gen-1"].status == GenerationStatus.PROCESSING


async def test_result_is_discarded_if_generation_was_failed_meanwhile(
    uow, storage, fail_generation
) -> None:
    put_generation(storage)

    class FailedMeanwhileProvider:
        """Пока провайдер отвечал, генерацию перевела в failed фоновая сверка."""

        async def generate(self, prompt: str) -> str:
            await fail_generation.execute(storage.generations["gen-1"], "stale")
            return RESULT_URL

    await make_use_case(uow, FailedMeanwhileProvider(), fail_generation).execute(
        "gen-1"
    )

    generation = storage.generations["gen-1"]
    assert generation.status == GenerationStatus.FAILED
    assert generation.result_url is None
    assert refunds(storage) == [(10, "gen-1")]
