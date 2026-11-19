"""SQLAlchemy adapter for the hosted-runner repository seam (package-private).

Host-scoped: ``hosted_runners`` and ``hosted_runner_facts`` say which runners are the host's, so every statement here
names its runner explicitly and none names a runner-scoped table. The statements that write a runner's hosting are
module functions too, so a cross-kind write (an admission, an ingest's copy) runs them on its own transaction."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, Row, func, inspect, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.hosting.model import (
    SEED,
    HostedRunnerFact,
    HostedRunnerOp,
    HostedRunnerState,
    HostingFact,
    state_of,
)
from blizzard.runner.hosting.ports import IAdmissionHandle, IWriteAdmittedHosting, IWriteHostedRunnerRepository
from blizzard.runner.store.internal.admission_handle import admission_of
from blizzard.runner.store.schema import hosted_runner_facts, hosted_runners
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class HostHostedRunnerStore:
    """Read-write adapter over the host's hosted runners and their facts."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def hosted_runner_ids(self) -> frozenset[str]:
        with self._store.connect() as conn:
            return frozenset(str(runner_id) for runner_id in conn.execute(select(hosted_runners.c.runner_id)).scalars())

    def active(self) -> tuple[str, ...]:
        return tuple(runner_id for runner_id, state in self._states() if state is HostedRunnerState.ACTIVE)

    def retired(self) -> frozenset[str]:
        return frozenset(runner_id for runner_id, state in self._states() if state is HostedRunnerState.RETIRED)

    def facts(self, runner_id: str) -> tuple[HostedRunnerFact, ...]:
        with self._store.connect() as conn:
            rows = conn.execute(
                select(hosted_runner_facts)
                .where(hosted_runner_facts.c.runner_id == runner_id)
                .order_by(hosted_runner_facts.c.id)
            )
            return tuple(_fact(row) for row in rows)

    def host(self, runner_ids: Sequence[str], hosting: HostingFact) -> tuple[str, ...]:
        with self._store.begin() as conn:
            hosted = tuple(runner_id for runner_id in runner_ids if host_locked(conn, runner_id, hosting))
        if hosted:
            _log.info("runners hosted", runner_ids=list(hosted), door=hosting.door, actor=hosting.actor)
        return hosted

    def host_admitted_locked(self, handle: IAdmissionHandle, hosting: HostingFact, *, alongside: Sequence[str]) -> bool:
        admission = admission_of(handle)
        if alongside and not hosts_any_locked(admission.conn):
            seeded = HostingFact(actor=SEED, door=SEED, at=hosting.at)
            for runner_id in alongside:
                host_locked(admission.conn, runner_id, seeded)
        return host_locked(admission.conn, admission.runner_id, hosting)

    def append_fact(self, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
        with self._store.begin() as conn:
            append_fact_locked(conn, runner_id, op, hosting)
        _log.info("hosted runner fact recorded", runner_id=runner_id, op=str(op), door=hosting.door)

    def drop(self, runner_id: str) -> bool:
        with self._store.begin() as conn:
            if hosted_runners.name not in inspect(conn).get_table_names():
                return False
            conn.execute(hosted_runner_facts.delete().where(hosted_runner_facts.c.runner_id == runner_id))
            dropped = bool(
                conn.execute(hosted_runners.delete().where(hosted_runners.c.runner_id == runner_id)).rowcount
            )
        if dropped:
            _log.info("hosted runner dropped", runner_id=runner_id)
        return dropped

    def _states(self) -> list[tuple[str, HostedRunnerState]]:
        """Each hosted runner and its state, in the order it was first hosted."""
        with self._store.connect() as conn:
            order = [
                str(runner_id)
                for runner_id in conn.execute(
                    select(hosted_runners.c.runner_id).order_by(hosted_runners.c.added_at, hosted_runners.c.runner_id)
                ).scalars()
            ]
            facts: dict[str, list[HostedRunnerFact]] = {}
            for row in conn.execute(select(hosted_runner_facts).order_by(hosted_runner_facts.c.id)):
                facts.setdefault(str(row.runner_id), []).append(_fact(row))
        return [(runner_id, state) for runner_id in order if (state := state_of(facts.get(runner_id, ()))) is not None]


def hosts_any_locked(conn: Connection) -> bool:
    """Whether the store holds any hosted row, read on ``conn``'s open transaction."""
    return bool(conn.execute(select(func.count()).select_from(hosted_runners)).scalar_one())


def host_locked(conn: Connection, runner_id: str, hosting: HostingFact) -> bool:
    """Give ``runner_id`` a hosted row and an ``added`` fact on ``conn``'s open transaction, unless the store already
    holds it — added or retired, so a retired runner is never brought back. Whether it was hosted."""
    held = conn.execute(select(hosted_runners.c.runner_id).where(hosted_runners.c.runner_id == runner_id)).first()
    if held is not None:
        return False
    conn.execute(hosted_runners.insert().values(runner_id=runner_id, added_at=hosting.at, added_by=hosting.actor))
    append_fact_locked(conn, runner_id, HostedRunnerOp.ADDED, hosting)
    return True


def append_fact_locked(conn: Connection, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
    conn.execute(
        hosted_runner_facts.insert().values(
            runner_id=runner_id, recorded_at=hosting.at, op=str(op), actor=hosting.actor, door=hosting.door
        )
    )


def _fact(row: Row[Any]) -> HostedRunnerFact:
    return HostedRunnerFact(
        runner_id=str(row.runner_id),
        recorded_at=row.recorded_at,
        op=HostedRunnerOp(row.op),
        actor=str(row.actor),
        door=row.door,
    )


_host_hosted_runner_store_conforms: type[IWriteHostedRunnerRepository] = HostHostedRunnerStore
_admitted_hosting_conforms: type[IWriteAdmittedHosting] = HostHostedRunnerStore
