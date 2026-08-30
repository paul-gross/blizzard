"""SQLAlchemy adapter for the environment-binding repository seam (package-private,
blizzard#410)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.logging import get_logger
from blizzard.runner.environments.repository import EnvBindingRecord, IWriteEnvironmentRepository
from blizzard.runner.store.internal.base import HELD_BINDING, RunnerStoreConnections
from blizzard.runner.store.schema import binding_releases, env_bindings

_log = get_logger("blizzard.runner.store")


class EnvironmentStore:
    """Read-write environment-binding adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def held_environment_ids(self) -> list[str]:
        stmt = select(env_bindings.c.environment_id).where(HELD_BINDING.clause).distinct()
        return [str(r.environment_id) for r in self._store.all(stmt)]

    def bindings_for_chunk(self, chunk_id: str) -> list[EnvBindingRecord]:
        stmt = (
            select(env_bindings)
            .where(env_bindings.c.chunk_id == chunk_id)
            .where(HELD_BINDING.clause)
            .order_by(env_bindings.c.bound_at)
        )
        return [self._row_to_binding(r) for r in self._store.all(stmt)]

    def live_tenure_chunk_ids(self) -> list[str]:
        stmt = select(env_bindings.c.chunk_id).where(HELD_BINDING.clause).distinct()
        return [str(r.chunk_id) for r in self._store.all(stmt)]

    def held_bindings(self) -> list[EnvBindingRecord]:
        stmt = select(env_bindings).where(HELD_BINDING.clause).order_by(env_bindings.c.bound_at)
        return [self._row_to_binding(r) for r in self._store.all(stmt)]

    def record_binding(self, *, chunk_id: str, environment_id: str, workdir: str, bound_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                env_bindings.insert().values(
                    chunk_id=chunk_id, environment_id=environment_id, workdir=workdir, bound_at=bound_at
                )
            )
        _log.info("env bound", chunk_id=chunk_id, environment_id=environment_id, workdir=workdir)

    def record_release(self, *, chunk_id: str, environment_id: str, released_at: datetime) -> None:
        with self._store.begin() as conn:
            conn.execute(
                binding_releases.insert().values(
                    chunk_id=chunk_id, environment_id=environment_id, released_at=released_at
                )
            )
        _log.info("env released", chunk_id=chunk_id, environment_id=environment_id)

    @staticmethod
    def _row_to_binding(r) -> EnvBindingRecord:  # type: ignore[no-untyped-def]
        return EnvBindingRecord(
            chunk_id=str(r.chunk_id),
            environment_id=str(r.environment_id),
            workdir=str(r.workdir),
            bound_at=r.bound_at,
        )


def _conforms_environment_store(x: EnvironmentStore) -> IWriteEnvironmentRepository:
    return x
