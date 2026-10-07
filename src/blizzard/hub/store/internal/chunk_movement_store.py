"""SQLAlchemy adapter for the chunk movement seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened and status is
derived. Timestamps arrive already stamped (``bzh:injected-clock``).

``record_transition`` and ``record_migration`` are each one
transaction on one connection — the shared row helpers below are plain function calls
inside that same ``with self._store.write(...)`` block, never a second connection — and
each locks the chunk row, then fences (``bzh:epoch-fencing``), before its first insert."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, EpochOwner, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    DEFAULT_MODEL,
    conn_of,
    enqueue_close_intents,
    fence,
    graph_id_of,
    insert_proposals,
    is_landing_marker,
    latest_epoch,
    lock_chunk_row,
    next_artifact_seq,
    next_route_seq,
    record_epoch_owner,
)


class ChunkMovementStore:
    """The chunk's graph movement — transitions, migrations, restarts, requeues."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        with self._store.read("accepted_transition_target") as conn:
            row = conn.execute(
                select(s.transitions.c.to_node_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            return row.to_node_id if row is not None else None

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a migration is already recorded for ``(chunk_id, from_node_id, epoch)``
        — the idempotency probe a re-applied cross-graph completion short-circuits on (#90).

        A migration writes no ``transitions`` row, so the transition-replay probe cannot
        see it; this is its counterpart, on :meth:`record_migration`'s natural key."""
        with self._store.read("accepted_migration") as conn:
            return self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch)

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        runner_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        decision_id: str | None = None,
    ) -> FenceRefusal | None:
        with self._store.write("record_transition") as conn:
            lock_chunk_row(conn, chunk_id)
            return self._record_transition_conn(
                conn,
                transition_id=transition_id,
                chunk_id=chunk_id,
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                choice_name=choice_name,
                epoch=epoch,
                admission=admission,
                claimant=claimant,
                runner_id=runner_id,
                at=at,
                artifacts=artifacts,
                proposals=proposals,
                decision_id=decision_id,
            )

    def record_transition_locked(
        self,
        handle: ILockedChunkRead,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        runner_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        decision_id: str | None = None,
    ) -> FenceRefusal | None:
        """:meth:`record_transition` on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``) — the caller's guard reads and this write share one lock."""
        return self._record_transition_conn(
            conn_of(handle),
            transition_id=transition_id,
            chunk_id=chunk_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            choice_name=choice_name,
            epoch=epoch,
            admission=admission,
            claimant=claimant,
            runner_id=runner_id,
            at=at,
            artifacts=artifacts,
            proposals=proposals,
            decision_id=decision_id,
        )

    def _record_transition_conn(
        self,
        conn: Connection,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        runner_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        decision_id: str | None = None,
    ) -> FenceRefusal | None:
        refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
        if refusal is not None:
            return refusal
        conn.execute(
            s.transitions.insert().values(
                transition_id=transition_id,
                chunk_id=chunk_id,
                graph_id=graph_id_of(conn, chunk_id),
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                choice_name=choice_name,
                decision_id=decision_id,
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
                    seq=next_artifact_seq(conn, row.chunk_id),
                )
            )
        insert_proposals(conn, proposals, at=at)
        if any(is_landing_marker(row.name, row.data) for row in artifacts):
            enqueue_close_intents(conn, chunk_id, at=at)
        return None

    def record_migration(
        self,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | FenceRefusal | None:
        """Record a cross-graph migration **atomically and idempotently** (#90).

        One transaction: the fact, the ``chunks.graph_id`` re-pin, the route release
        (unless ``release_route``, #111), this step's artifacts and proposals, and the
        intent clear (``clear_intent``, #124). Keyed ``(chunk_id, from_node_id, epoch)``."""
        with self._store.write("record_migration") as conn:
            lock_chunk_row(conn, chunk_id)
            return self._record_migration_conn(
                conn,
                chunk_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id,
                to_graph_id=to_graph_id,
                landed_node_id=landed_node_id,
                choice_name=choice_name,
                decision_id=decision_id,
                model=model,
                epoch=epoch,
                admission=admission,
                claimant=claimant,
                at=at,
                artifacts=artifacts,
                proposals=proposals,
                source=source,
                release_route=release_route,
                clear_intent=clear_intent,
                migration_id=migration_id,
            )

    def record_migration_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | FenceRefusal | None:
        """:meth:`record_migration` on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``) — the caller's guard reads and this write share one lock."""
        return self._record_migration_conn(
            conn_of(handle),
            chunk_id,
            from_node_id=from_node_id,
            from_graph_id=from_graph_id,
            to_graph_id=to_graph_id,
            landed_node_id=landed_node_id,
            choice_name=choice_name,
            decision_id=decision_id,
            model=model,
            epoch=epoch,
            admission=admission,
            claimant=claimant,
            at=at,
            artifacts=artifacts,
            proposals=proposals,
            source=source,
            release_route=release_route,
            clear_intent=clear_intent,
            migration_id=migration_id,
        )

    def _record_migration_conn(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | FenceRefusal | None:
        if self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch):
            return None
        refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
        if refusal is not None:
            return refusal
        resolved_migration_id = (
            migration_id if migration_id is not None else Id.mint(IdPrefix.MIGRATION, self._clock).value
        )
        conn.execute(
            s.chunk_migrations.insert().values(
                migration_id=resolved_migration_id,
                chunk_id=chunk_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id,
                to_graph_id=to_graph_id,
                landed_node_id=landed_node_id,
                choice_name=choice_name,
                decision_id=decision_id,
                model_after=model,
                epoch=epoch,
                recorded_at=at,
                source=source.value,
            )
        )
        values: dict[str, str | None] = {"graph_id": to_graph_id}
        if model is not None:
            # Written INLINE: a second transactional write would split the
            # durable fact from the pin it implies (`hub:migration-pin-consistent`).
            values["default_model"] = DEFAULT_MODEL.encode([model])
        if clear_intent:
            values["intended_migration"] = None
        conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(**values))
        if release_route:
            conn.execute(
                s.route_released.insert().values(chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id))
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
                    seq=next_artifact_seq(conn, row.chunk_id),
                )
            )
        insert_proposals(conn, proposals, at=at)
        if any(is_landing_marker(row.name, row.data) for row in artifacts):
            enqueue_close_intents(conn, chunk_id, at=at)
        return resolved_migration_id

    def record_restart_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        from_node_id: str | None,
        to_node_id: str,
        by: str,
        at: datetime,
        decision_id: str | None = None,
        answered_question_ids: Sequence[str] = (),
        answer: str = "",
        to_graph_id: str | None = None,
    ) -> int:
        """Record the forced move and everything it consumes (``bzh:store-exclusive-write``) —
        the restart's own write, on ``handle``'s already-locked connection."""
        return self._record_restart_conn(
            conn_of(handle),
            chunk_id,
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            by=by,
            at=at,
            decision_id=decision_id,
            answered_question_ids=answered_question_ids,
            answer=answer,
            to_graph_id=to_graph_id,
        )

    def _record_restart_conn(  # type: ignore[no-untyped-def]
        self,
        conn,
        chunk_id: str,
        *,
        from_node_id: str | None,
        to_node_id: str,
        by: str,
        at: datetime,
        decision_id: str | None,
        answered_question_ids: Sequence[str],
        answer: str,
        to_graph_id: str | None,
    ) -> int:
        epoch = latest_epoch(conn, chunk_id) + 1
        for question_id in answered_question_ids:
            already = conn.execute(
                select(s.question_answers.c.question_id).where(s.question_answers.c.question_id == question_id)
            ).first()
            if already is None:
                conn.execute(
                    s.question_answers.insert().values(
                        question_id=question_id, answer=answer, answered_by=by, answered_at=at
                    )
                )
        from_graph_id = graph_id_of(conn, chunk_id)
        if to_graph_id is not None:
            self._repin_by_restart(
                conn,
                chunk_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id,
                to_graph_id=to_graph_id,
                landed_node_id=to_node_id,
                epoch=epoch,
                at=at,
            )
        result = conn.execute(
            s.chunk_restarts.insert().values(
                chunk_id=chunk_id,
                graph_id=to_graph_id if to_graph_id is not None else from_graph_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id if to_graph_id is not None else None,
                to_node_id=to_node_id,
                epoch=epoch,
                decision_id=decision_id,
                restarted_by=by,
                recorded_at=at,
            )
        )
        record_epoch_owner(conn, chunk_id, epoch, EpochOwner.hub(), at=at)
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_requeue_locked(self, handle: ILockedChunkRead, chunk_id: str, *, at: datetime) -> int:
        conn = conn_of(handle)
        result = conn.execute(s.requeues.insert().values(chunk_id=chunk_id, requeued_at=at))
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def _repin_by_restart(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str,
        epoch: int,
        at: datetime,
    ) -> None:
        """A cross-graph restart's migration half (#371), inside the restart's own transaction: the
        fact that owns the re-pin, the pin, and the standing intent this eager move supersedes.
        Stamped at the restart's own ``(recorded_at, epoch)``, so ``latest_movement``'s kind rank
        settles which of the two the chunk stands on. No route release — the holding runner keeps
        it and re-enters — and no ``decision_id``, which is the restart fact's alone."""
        conn.execute(
            s.chunk_migrations.insert().values(
                migration_id=Id.mint(IdPrefix.MIGRATION, self._clock).value,
                chunk_id=chunk_id,
                from_node_id=from_node_id,
                from_graph_id=from_graph_id,
                to_graph_id=to_graph_id,
                landed_node_id=landed_node_id,
                choice_name=None,
                decision_id=None,
                model_after=None,
                epoch=epoch,
                recorded_at=at,
                source=MigrationSource.RESTART.value,
            )
        )
        conn.execute(
            update(s.chunks)
            .where(s.chunks.c.chunk_id == chunk_id)
            .values(graph_id=to_graph_id, intended_migration=None)
        )

    @staticmethod
    def _migration_exists(conn: Connection, chunk_id: str, *, from_node_id: str | None, epoch: int) -> bool:
        return (
            conn.execute(
                select(s.chunk_migrations.c.migration_id).where(
                    (s.chunk_migrations.c.chunk_id == chunk_id)
                    & (s.chunk_migrations.c.from_node_id == from_node_id)
                    & (s.chunk_migrations.c.epoch == epoch)
                )
            ).first()
            is not None
        )


def _conforms_movement(x: ChunkMovementStore) -> IWriteChunkMovementRepository:
    return x
