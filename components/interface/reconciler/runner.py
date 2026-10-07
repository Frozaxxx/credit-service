import asyncio
import logging

from redis.asyncio import Redis

from components.application.use_cases.generation.fail_generation import (
    FailGeneration,
)
from components.application.use_cases.generation.fail_stale_generations import (
    FailStaleGenerations,
)
from components.core.config.settings import Settings, get_settings
from components.core.logging.setup import setup_logging
from components.infrastructure.stores.mysql.session_factory import (
    create_engine,
    create_session_factory,
)
from components.infrastructure.stores.mysql.unit_of_work import MySQLUnitOfWork
from components.infrastructure.stores.redis.balance_cache import RedisBalanceCache

logger = logging.getLogger(__name__)


async def run_reconciler(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    setup_logging(resolved.log_level)

    engine = create_engine(resolved)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(resolved.redis_url, decode_responses=True)
    balance_cache = RedisBalanceCache(
        redis,
        ttl_seconds=resolved.balance_cache_ttl_seconds,
    )

    logger.info(
        "Reconciler started stale_after=%ss interval=%ss batch_size=%s",
        resolved.stale_generation_seconds,
        resolved.reconcile_interval_seconds,
        resolved.reconcile_batch_size,
    )
    try:
        while True:
            use_case = FailStaleGenerations(
                uow=MySQLUnitOfWork(session_factory),
                fail_generation=FailGeneration(
                    uow=MySQLUnitOfWork(session_factory),
                    balance_cache=balance_cache,
                ),
                stale_after_seconds=resolved.stale_generation_seconds,
                batch_size=resolved.reconcile_batch_size,
            )
            try:
                await use_case.execute()
            except Exception:
                # Сбой одного прохода не должен останавливать сверку.
                logger.exception("Reconciliation pass failed")
            await asyncio.sleep(resolved.reconcile_interval_seconds)
    finally:
        await redis.aclose()
        await engine.dispose()
