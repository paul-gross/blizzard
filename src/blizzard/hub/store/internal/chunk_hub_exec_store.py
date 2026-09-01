"""SQLAlchemy adapter for the chunk hub-exec seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row or an atomically-guarded slot
claim; nothing here derives status. Timestamps arrive already stamped
(``bzh:injected-clock``).

D6: ``record_hub_step_transition`` stays one transaction on one connection, unchanged by
the seam carve — the shared row helpers below are plain function calls inside that same
``with self._store.write(...)`` block, never a second connection."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import HUB_EXEC_SLOT_PREFIX, Id
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import enqueue_close_intents, graph_id_of, latest_epoch, next_route_seq

# The generic ``merged/<repo>`` landing marker (issue #67) — mirrors domain/work.py's own
# copy (``LandedRepos``'s), which reads it back; each side owns its own constant.
_MARKER_PREFIX = "merged/"


class ChunkHubExecStore:
    """The generic hub command node's own execution-slot and step-transition facts (#65)."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def count_live_hub_exec_slots(self) -> int:
        with self._store.read("count_live_hub_exec_slots") as conn:
            return int(
                conn.execute(
                    select(func.count()).select_from(s.hub_exec_slot).where(s.hub_exec_slot.c.released_at.is_(None))
                ).scalar()
                or 0
            )

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution slot, **atomically** (crash-derivable
        fact, ``bzh:facts-not-status`` — never an in-process lock, so the invariant
        checker can assert at most one live slot and a ``kill -9`` mid-run leaves a
        stale, reclaimable row rather than a wedged fleet)."""
        with self._store.write("acquire_hub_exec_slot") as conn:
            # Force sqlite's whole-database write lock BEFORE the read-then-insert below,
            # closing the race a bare SELECT leaves open (see ``next_route_seq``).
            conn.execute(update(s.hub_exec_slot).values(node_id=s.hub_exec_slot.c.node_id))
            live_rows = conn.execute(select(s.hub_exec_slot).where(s.hub_exec_slot.c.released_at.is_(None))).all()
            for row in live_rows:
                if row.holder_chunk_id == chunk_id:
                    return row.slot_id  # reentrant — this chunk already holds it
                if at - row.acquired_at < stale_after:
                    return None  # a different chunk genuinely holds it — defer
                # Stale — a prior holder's run never released it (a kill -9); reclaim.
                conn.execute(
                    update(s.hub_exec_slot).where(s.hub_exec_slot.c.slot_id == row.slot_id).values(released_at=at)
                )
            slot_id = Id.mint(HUB_EXEC_SLOT_PREFIX, self._clock).value
            conn.execute(
                s.hub_exec_slot.insert().values(
                    slot_id=slot_id, holder_chunk_id=chunk_id, node_id=node_id, acquired_at=at, released_at=None
                )
            )
            return slot_id

    def release_hub_exec_slot(self, chunk_id: str, *, at: datetime) -> None:
        with self._store.write("release_hub_exec_slot") as conn:
            conn.execute(
                update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )

    def record_hub_step_transition(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        release_route: bool,
    ) -> bool:
        """Record a generic hub command node's exit transition **atomically and idempotently**
        (#65) — ``ChunkDeliveryStore.finalize_delivery``'s counterpart, generalized to any
        authored target. Two guards, both returning False: the transition's existence at
        ``(chunk_id, from_node_id, epoch)`` absorbs a redelivery replay, and the chunk's
        CURRENT epoch absorbs a restart that re-aimed it while the ``run:`` list ran
        (``bzh:epoch-fencing``)."""
        with self._store.write("record_hub_step_transition") as conn:
            already = conn.execute(
                select(s.transitions.c.transition_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            if latest_epoch(conn, chunk_id) >= epoch:
                return False
            conn.execute(
                s.lease_facts.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(
                s.transitions.insert().values(
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
                    s.artifacts.insert().values(
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
                    )
                )
            if release_route:
                conn.execute(
                    s.route_released.insert().values(
                        chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                    )
                )
            if any(row.name.startswith(_MARKER_PREFIX) for row in artifacts):
                enqueue_close_intents(conn, chunk_id, at=at)
            return True

    def record_hub_node_poll(self, chunk_id: str, *, node_id: str, epoch: int, at: datetime) -> None:
        """Append one pending-poll-attempt fact (#66) — never a transition, no
        idempotency guard (an at-least-once poll attempt is harmless recorded twice)."""
        with self._store.write("record_hub_node_poll") as conn:
            conn.execute(s.hub_node_poll.insert().values(chunk_id=chunk_id, node_id=node_id, epoch=epoch, polled_at=at))


def _conforms_hub_exec(x: ChunkHubExecStore) -> IWriteChunkHubExecRepository:
    return x
