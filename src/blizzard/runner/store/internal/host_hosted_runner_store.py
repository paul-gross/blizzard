"""SQLAlchemy adapter for the hosted-runner repository seam (package-private).

Host-scoped: ``hosted_runners`` and ``hosted_runner_facts`` say which runners are the host's, so every statement here
names its runner explicitly and none names a runner-scoped table. A cross-kind write (an admission, an ingest's
copy) hosts its runner through this store's own ``*_locked`` methods, on that write's transaction."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, and_, exists, func, inspect, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.hosting.model import (
    CLI,
    OPERATOR,
    SEED,
    HostedRoster,
    HostedRunnerFact,
    HostedRunnerOp,
    HostingFact,
)
from blizzard.runner.hosting.ports import IAdmissionHandle, IWriteAdmittedHosting, IWriteHostedRunnerRepository
from blizzard.runner.store.internal.admission_handle import admission_of
from blizzard.runner.store.internal.row_transfer import handle_of, removal_of
from blizzard.runner.store.schema import hosted_runner_facts, hosted_runners
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.transfer.rows import IRunnerRemovalHandle, IRunnerTransferHandle, IWriteCarriedHosting

_log = get_logger("blizzard.runner.store")


class HostHostedRunnerStore:
    """Read-write adapter over the host's hosted runners and their facts."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def hosted_runner_ids(self) -> frozenset[str]:
        with self._store.connect() as conn:
            return frozenset(str(runner_id) for runner_id in conn.execute(select(hosted_runners.c.runner_id)).scalars())

    def roster(self) -> HostedRoster:
        with self._store.connect() as conn:
            return _roster(conn)

    def active(self) -> tuple[str, ...]:
        return self.roster().active

    def retired(self) -> frozenset[str]:
        return self.roster().retired

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
            hosted = tuple(runner_id for runner_id in runner_ids if _host_locked(conn, runner_id, hosting))
        if hosted:
            _log.info("runners hosted", runner_ids=list(hosted), door=hosting.door, actor=hosting.actor)
        return hosted

    def host_admitted_locked(self, handle: IAdmissionHandle, hosting: HostingFact, *, alongside: Sequence[str]) -> bool:
        admission = admission_of(handle)
        return _host_beside(admission.conn, admission.runner_id, hosting, alongside)

    def host_carried_locked(self, handle: IRunnerTransferHandle, *, at: datetime, alongside: Sequence[str]) -> bool:
        copy = handle_of(handle)
        hosted = _host_beside(copy.destination, copy.runner_id, HostingFact(actor=OPERATOR, door=CLI, at=at), alongside)
        _log.info("carried runner hosted", runner_id=copy.runner_id, hosted=hosted)
        return hosted

    def record_retired(self, runner_id: str, hosting: HostingFact) -> bool:
        with self._store.begin() as conn:
            if _retired(conn, runner_id):
                return False
            _host_locked(conn, runner_id, hosting)
            _append_fact_locked(conn, runner_id, HostedRunnerOp.RETIRED, hosting)
        _log.info("hosted runner retired", runner_id=runner_id, door=hosting.door, actor=hosting.actor)
        return True

    def append_fact(self, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
        with self._store.begin() as conn:
            _append_fact_locked(conn, runner_id, op, hosting)
        _log.info("hosted runner fact recorded", runner_id=runner_id, op=str(op), door=hosting.door)

    def drop_removed_locked(self, handle: IRunnerRemovalHandle) -> bool:
        removal = removal_of(handle)
        conn = removal.conn
        dropped = False
        if hosted_runners.name in inspect(conn).get_table_names():
            conn.execute(hosted_runner_facts.delete().where(hosted_runner_facts.c.runner_id == removal.runner_id))
            dropped = bool(
                conn.execute(hosted_runners.delete().where(hosted_runners.c.runner_id == removal.runner_id)).rowcount
            )
        _log.info("removed hosted runner dropped", runner_id=removal.runner_id, dropped=dropped)
        return dropped


def _roster(conn: Connection) -> HostedRoster:
    """Every hosted runner holding a fact, in the order it was first hosted, split by whether any fact retires it —
    one read, so the two sets come from one snapshot."""
    holds_fact = exists().where(hosted_runner_facts.c.runner_id == hosted_runners.c.runner_id)
    rows = conn.execute(
        select(hosted_runners.c.runner_id, _retires(hosted_runners.c.runner_id).label("retired"))
        .where(holds_fact)
        .order_by(hosted_runners.c.added_at, hosted_runners.c.runner_id)
    ).all()
    return HostedRoster(
        active=tuple(str(row.runner_id) for row in rows if not row.retired),
        retired=frozenset(str(row.runner_id) for row in rows if row.retired),
    )


def _retires(runner_id: Any) -> Any:
    """Whether a ``retired`` fact names ``runner_id`` — retirement is final, so one is enough."""
    return exists().where(
        and_(hosted_runner_facts.c.runner_id == runner_id, hosted_runner_facts.c.op == str(HostedRunnerOp.RETIRED))
    )


def _retired(conn: Connection, runner_id: str) -> bool:
    return bool(conn.execute(select(_retires(runner_id))).scalar_one())


def _host_beside(conn: Connection, runner_id: str, hosting: HostingFact, alongside: Sequence[str]) -> bool:
    """Host ``runner_id`` on ``conn``'s open transaction; a store hosting no runner yet first hosts every runner
    ``alongside`` under the seed's door and actor. Whether ``runner_id`` was hosted."""
    if alongside and not _hosts_any_locked(conn):
        seeded = HostingFact(actor=SEED, door=SEED, at=hosting.at)
        for named in alongside:
            _host_locked(conn, named, seeded)
    return _host_locked(conn, runner_id, hosting)


def _hosts_any_locked(conn: Connection) -> bool:
    """Whether the store holds any hosted row, read on ``conn``'s open transaction."""
    return bool(conn.execute(select(func.count()).select_from(hosted_runners)).scalar_one())


def _host_locked(conn: Connection, runner_id: str, hosting: HostingFact) -> bool:
    """Give ``runner_id`` a hosted row and an ``added`` fact on ``conn``'s open transaction, unless the store already
    holds it — added or retired, so a retired runner is never brought back. Whether it was hosted."""
    held = conn.execute(select(hosted_runners.c.runner_id).where(hosted_runners.c.runner_id == runner_id)).first()
    if held is not None:
        return False
    conn.execute(hosted_runners.insert().values(runner_id=runner_id, added_at=hosting.at, added_by=hosting.actor))
    _append_fact_locked(conn, runner_id, HostedRunnerOp.ADDED, hosting)
    return True


def _append_fact_locked(conn: Connection, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
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
_carried_hosting_conforms: type[IWriteCarriedHosting] = HostHostedRunnerStore
