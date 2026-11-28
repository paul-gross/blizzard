"""Resources acquired by a host process scope."""

from collections.abc import Iterator

from sqlalchemy import Engine

from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.factories import store_status_reader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import migration_runner
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.scoped import RunnerStoreConnections


def open_engine(config: RunnerConfig, tracing: IPlatformTracing) -> Iterator[Engine]:
    engine = create_engine_from_url(config.db_url)
    try:
        tracing.instrument_engine(engine)
    except BaseException as failure:
        try:
            engine.dispose()
        except BaseException as cleanup:
            failure.add_note(f"disposing the engine after instrumentation also failed: {cleanup!r}")
        raise
    try:
        yield engine
    finally:
        engine.dispose()


def connections(engine: Engine) -> RunnerStoreConnections:
    return RunnerStoreConnections(engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))


def readiness(engine: Engine, config: RunnerConfig) -> ReadinessService:
    return ReadinessService(
        reader=store_status_reader(engine), expected_revision=migration_runner(config).script_head()
    )


def register(registry: Registry) -> None:
    registry.resource(open_engine, lifetime=Lifetime.PROCESS)
    registry.factory(connections, lifetime=Lifetime.PROCESS)
    registry.factory(readiness, lifetime=Lifetime.PROCESS)
