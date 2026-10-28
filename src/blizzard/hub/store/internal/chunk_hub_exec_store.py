"""SQLAlchemy adapter for the chunk hub-exec seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row or an atomically-guarded slot
claim; nothing here derives status. Timestamps arrive already stamped
(``bzh:injected-clock``).

``record_hub_step_transition`` is one transaction on one connection — the shared row
helpers below are plain function calls inside that same ``with self._store.write(...)``
block, never a second connection."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Connection, func

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    enqueue_close_intents,
    fence,
    graph_id_of,
    is_landing_marker,
    lock_chunk_row,
    next_artifact_seq,
    next_route_seq,
    record_hub_lease,
)
from blizzard.hub.store.scoped import ScopedConnections


class ChunkHubExecStore:
    """The generic hub command node's own execution-slot and step-transition facts (#65)."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def count_live_hub_exec_slots(self) -> int:
        with self._store.read("count_live_hub_exec_slots") as conn:
            return int(
                conn.execute(
                    self._store.select(s.hub_exec_slot, func.count()).where(s.hub_exec_slot.c.released_at.is_(None))
                ).scalar()
                or 0
            )

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """See ``src/blizzard/hub/domain/chunk/ports/hub_exec.py``'s
        ``IWriteChunkHubExecRepository.acquire_hub_exec_slot``.

        The scoped write opens on the tenant row's lock (``bzh:store-exclusive-write``), so two
        acquires in one tenant serialize before the read-then-insert below, while another
        tenant's acquire never waits on this one's slot."""
        with self._store.write("acquire_hub_exec_slot") as conn:
            live_rows = conn.execute(
                self._store.select(s.hub_exec_slot).where(s.hub_exec_slot.c.released_at.is_(None))
            ).all()
            for row in live_rows:
                if at - row.acquired_at < stale_after:
                    return None  # held, by this chunk's own run or another's — defer
                # Stale — a prior holder's run never released it (a kill -9); reclaim.
                conn.execute(
                    self._store.update(s.hub_exec_slot)
                    .where(s.hub_exec_slot.c.slot_id == row.slot_id)
                    .values(released_at=at)
                )
            slot_id = Id.mint(IdPrefix.HUB_EXEC_SLOT, self._clock).value
            conn.execute(
                self._store.insert(s.hub_exec_slot).values(
                    slot_id=slot_id, holder_chunk_id=chunk_id, node_id=node_id, acquired_at=at
                )
            )
            return slot_id

    def release_hub_exec_slot(self, slot_id: str, *, at: datetime) -> None:
        with self._store.write("release_hub_exec_slot") as conn:
            conn.execute(
                self._store.update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.slot_id == slot_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )

    def release_live_hub_exec_slots(self, *, at: datetime) -> int:
        """Release every live slot in this store's tenant — the boot step's release of slots a crash
        orphaned."""
        with self._store.write("release_live_hub_exec_slots") as conn:
            return conn.execute(
                self._store.update(s.hub_exec_slot)
                .where(s.hub_exec_slot.c.released_at.is_(None))
                .values(released_at=at)
            ).rowcount

    def record_hub_step_transition(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        release_route: bool,
    ) -> bool:
        """See ``src/blizzard/hub/domain/chunk/ports/hub_exec.py``'s
        ``IWriteChunkHubExecRepository.record_hub_step_transition``."""
        with self._store.write("record_hub_step_transition") as conn:
            return self._transition_conn(
                conn,
                chunk_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                choice_name=choice_name,
                epoch=epoch,
                admission=admission,
                runner_id=runner_id,
                transition_id=transition_id,
                at=at,
                artifacts=artifacts,
                release_route=release_route,
            )

    def record_hub_step_transition_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        release_route: bool,
    ) -> bool:
        """:meth:`record_hub_step_transition` on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), so a turn's end commits with it."""
        return self._transition_conn(
            conn_of(handle),
            chunk_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            choice_name=choice_name,
            epoch=epoch,
            admission=admission,
            runner_id=runner_id,
            transition_id=transition_id,
            at=at,
            artifacts=artifacts,
            release_route=release_route,
        )

    def _transition_conn(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        release_route: bool,
    ) -> bool:
        lock_chunk_row(conn, chunk_id)
        already = conn.execute(
            self._store.select(s.transitions, s.transitions.c.transition_id).where(
                (s.transitions.c.chunk_id == chunk_id)
                & (s.transitions.c.from_node_id == from_node_id)
                & (s.transitions.c.epoch == epoch)
            )
        ).first()
        if already is not None:
            return False
        if fence(conn, chunk_id, epoch=epoch, admission=admission) is not None:
            return False
        record_hub_lease(conn, chunk_id, epoch=epoch, runner_id=runner_id, at=at)
        conn.execute(
            self._store.insert(s.transitions).values(
                transition_id=transition_id,
                chunk_id=chunk_id,
                graph_id=graph_id_of(conn, chunk_id),
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                choice_name=choice_name,
                decision_id=None,
                epoch=epoch,
                runner_id=runner_id,
                recorded_at=at,
            )
        )
        for row in artifacts:
            conn.execute(
                self._store.insert(s.artifacts).values(
                    artifact_id=row.artifact_id,
                    chunk_id=row.chunk_id,
                    node_id=row.node_id,
                    node_name=row.node_name,
                    epoch=row.epoch,
                    name=row.name,
                    kind=row.kind.value,
                    data=row.data,
                    repo=row.repo,
                    forge=row.forge,
                    produced_at=at,
                    seq=next_artifact_seq(conn, row.chunk_id),
                )
            )
        if release_route:
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
        if any(is_landing_marker(row.name, row.data) for row in artifacts):
            enqueue_close_intents(conn, chunk_id, at=at)
        return True

    def record_hub_node_poll(
        self, chunk_id: str, *, node_id: str, epoch: int, at: datetime, next_poll_at: datetime | None = None
    ) -> None:
        """See ``src/blizzard/hub/domain/chunk/ports/hub_exec.py``'s
        ``IWriteChunkHubExecRepository.record_hub_node_poll``."""
        with self._store.write("record_hub_node_poll") as conn:
            self._poll_conn(conn, chunk_id, node_id=node_id, epoch=epoch, at=at, next_poll_at=next_poll_at)

    def record_hub_node_poll_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        at: datetime,
        next_poll_at: datetime | None = None,
    ) -> None:
        """:meth:`record_hub_node_poll` on ``handle``'s already-locked connection (``bzh:store-exclusive-write``)."""
        self._poll_conn(conn_of(handle), chunk_id, node_id=node_id, epoch=epoch, at=at, next_poll_at=next_poll_at)

    def _poll_conn(
        self, conn: Connection, chunk_id: str, *, node_id: str, epoch: int, at: datetime, next_poll_at: datetime | None
    ) -> None:
        conn.execute(
            self._store.insert(s.hub_node_poll).values(
                chunk_id=chunk_id, node_id=node_id, epoch=epoch, polled_at=at, next_poll_at=next_poll_at
            )
        )


_hub_exec_conforms: type[IWriteChunkHubExecRepository] = ChunkHubExecStore
