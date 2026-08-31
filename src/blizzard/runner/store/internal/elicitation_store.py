"""SQLAlchemy adapter for the in-flight-elicitation repository seam (package-private,
blizzard#443)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.domain.elicitation import ElicitationRecord, IWriteElicitationRepository
from blizzard.runner.store.internal.base import RunnerStoreConnections
from blizzard.runner.store.schema import in_flight_elicitations

_log = get_logger("blizzard.runner.store")


class ElicitationStore:
    """Read-write in-flight-elicitation adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def in_flight_elicitation(self, lease_id: str, epoch: int) -> ElicitationRecord | None:
        row = self._store.all(
            select(in_flight_elicitations).where(
                and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch)
            )
        )
        if not row:
            return None
        r = row[0]
        return ElicitationRecord(
            lease_id=str(r.lease_id),
            epoch=int(r.epoch),
            pid=int(r.pid) if r.pid is not None else None,
            process_start_time=str(r.process_start_time) if r.process_start_time is not None else None,
            output_path=str(r.output_path),
            first_launched_at=r.first_launched_at,
            relaunch_count=int(r.relaunch_count),
        )

    def record_elicitation_launch(self, lease_id: str, epoch: int, *, output_path: str, at: datetime) -> None:
        # Delete-then-insert: a fresh launch for this (lease, epoch) always starts a clean
        # record — the prior epoch's row, if any, was already cleared on its own collect.
        with self._store.begin() as conn:
            conn.execute(
                in_flight_elicitations.delete().where(
                    and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch)
                )
            )
            conn.execute(
                in_flight_elicitations.insert().values(
                    lease_id=lease_id,
                    epoch=epoch,
                    pid=None,
                    process_start_time=None,
                    output_path=output_path,
                    first_launched_at=at,
                    relaunch_count=0,
                )
            )
        _log.info("elicitation launch recorded", lease_id=lease_id, epoch=epoch, output_path=output_path)

    def record_elicitation_started(self, lease_id: str, epoch: int, *, pid: int, process_start_time: str) -> None:
        with self._store.begin() as conn:
            conn.execute(
                in_flight_elicitations.update()
                .where(and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch))
                .values(pid=pid, process_start_time=process_start_time)
            )
        _log.info("elicitation started", lease_id=lease_id, epoch=epoch, pid=pid)

    def record_elicitation_relaunch(self, lease_id: str, epoch: int, *, output_path: str) -> None:
        with self._store.begin() as conn:
            existing = conn.execute(
                select(in_flight_elicitations.c.relaunch_count).where(
                    and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch)
                )
            ).one()
            conn.execute(
                in_flight_elicitations.update()
                .where(and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch))
                .values(
                    pid=None,
                    process_start_time=None,
                    output_path=output_path,
                    relaunch_count=int(existing.relaunch_count) + 1,
                )
            )
        _log.info("elicitation relaunch recorded", lease_id=lease_id, epoch=epoch, output_path=output_path)

    def clear_elicitation(self, lease_id: str, epoch: int) -> None:
        with self._store.begin() as conn:
            conn.execute(
                in_flight_elicitations.delete().where(
                    and_(in_flight_elicitations.c.lease_id == lease_id, in_flight_elicitations.c.epoch == epoch)
                )
            )
        _log.info("elicitation cleared", lease_id=lease_id, epoch=epoch)


def _conforms_elicitation_store(x: ElicitationStore) -> IWriteElicitationRepository:
    return x
