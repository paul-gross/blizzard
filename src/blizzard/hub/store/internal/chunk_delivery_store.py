"""SQLAlchemy adapter for the chunk delivery seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``).

D6: ``finalize_delivery`` stays one transaction on one connection, unchanged by the seam
carve — the shared row helpers below are plain function calls inside that same
``with self._store.write(...)`` block, never a second connection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, select, update

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import PendingCloseIntent, WorkItemCloseOutcome, WorkItemMaterializationOutcome, WorkRef
from blizzard.hub.domain.work_closure import close_intent_is_due
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    enqueue_close_intents,
    graph_id_of,
    insert_materialization_row,
    next_route_seq,
    proposal_row,
)


class ChunkDeliveryStore:
    """The chunk's landing, closure, and work-item-materialization facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def landed_repos(self, chunk_id: str) -> set[str]:
        with self._store.read("landed_repos") as conn:
            return {
                r.repo
                for r in conn.execute(
                    select(s.delivery_repo_landed.c.repo).where(s.delivery_repo_landed.c.chunk_id == chunk_id)
                ).all()
            }

    def count_landed_since(self, repo: str, since: datetime) -> int:
        with self._store.read("count_landed_since") as conn:
            return conn.execute(
                select(func.count())
                .select_from(s.delivery_repo_landed)
                .where(s.delivery_repo_landed.c.repo == repo, s.delivery_repo_landed.c.landed_at > since)
            ).scalar_one()

    def pending_close_intents(self) -> list[PendingCloseIntent]:
        """Every pending, non-ephemeral intent's own backoff history in one flat, outer-
        joined, already-aggregated read (blizzard#524 D7) — never one query per intent.
        ``close_intent_is_due`` applies the domain's own due rule to each row."""
        ephemeral = select(s.chunk_grouped.c.chunk_id).union(select(s.chunk_deleted.c.chunk_id))
        attempts = (
            select(
                s.close_intent_attempts.c.intent_id,
                func.count(s.close_intent_attempts.c.id).label("attempt_count"),
                func.max(s.close_intent_attempts.c.attempted_at).label("last_attempt_at"),
            )
            .group_by(s.close_intent_attempts.c.intent_id)
            .subquery()
        )
        with self._store.read("pending_close_intents") as conn:
            rows = conn.execute(
                select(
                    s.close_intents.c.id,
                    s.close_intents.c.chunk_id,
                    s.close_intents.c.source,
                    s.close_intents.c.ref,
                    attempts.c.attempt_count,
                    attempts.c.last_attempt_at,
                )
                .select_from(s.close_intents.outerjoin(attempts, attempts.c.intent_id == s.close_intents.c.id))
                .where(s.close_intents.c.retired_at.is_(None))
                .where(s.close_intents.c.chunk_id.not_in(ephemeral))
                .order_by(s.close_intents.c.id)  # D2's explicit total order (`bzh:sql-portable`)
            ).all()
        now = self._clock.now()
        return [
            PendingCloseIntent(chunk_id=row.chunk_id, ref=WorkRef(source=row.source, ref=row.ref), intent_id=row.id)
            for row in rows
            if close_intent_is_due(now, attempt_count=row.attempt_count, last_attempt_at=row.last_attempt_at)
        ]

    def record_close_attempt_skipped(self, intent_id: int, *, at: datetime) -> None:
        with self._store.write("record_close_attempt_skipped") as conn:
            conn.execute(
                insert(s.close_intent_attempts).values(intent_id=intent_id, attempted_at=at, outcome="skipped")
            )

    def unmaterialized_proposals(self) -> list[WorkItemProposalRow]:
        """Every not-yet-judged proposal of a delivered, non-ephemeral chunk (blizzard#524
        D6) — all four exclusions (delivered, ephemeral, judged, struck) pushed into SQL as
        subqueries the engine plans once, rather than re-fetching and re-filtering every
        proposal ever written, payload included, on every pass. A read transaction: this
        writes nothing."""
        delivered = select(s.transitions.c.chunk_id).where(s.transitions.c.to_node_id == RESERVED_TERMINAL)
        ephemeral = select(s.chunk_grouped.c.chunk_id).union(select(s.chunk_deleted.c.chunk_id))
        judged = select(s.work_item_materializations.c.proposal_id)
        struck = select(s.work_item_strikes.c.proposal_id)
        with self._store.read("unmaterialized_proposals") as conn:
            rows = conn.execute(
                select(s.work_item_proposals)
                .where(s.work_item_proposals.c.chunk_id.in_(delivered))
                .where(s.work_item_proposals.c.chunk_id.not_in(ephemeral))
                .where(s.work_item_proposals.c.proposal_id.not_in(judged))
                .where(s.work_item_proposals.c.proposal_id.not_in(struck))
            ).all()
        return [proposal_row(row) for row in rows]

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None:
        with self._store.write("record_delivery_repo_landed") as conn:
            conn.execute(
                s.delivery_repo_landed.insert().values(
                    chunk_id=chunk_id, repo=repo, commit_hash=commit_hash, landed_at=at
                )
            )
            enqueue_close_intents(conn, chunk_id, at=at)

    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None:
        with self._store.write("record_delivery_landed") as conn:
            conn.execute(s.delivery_landed.insert().values(chunk_id=chunk_id, landed_at=at))
            enqueue_close_intents(conn, chunk_id, at=at)

    def finalize_delivery(
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
    ) -> bool:
        """Land the terminal delivery **atomically and idempotently** (crash recovery).

        The hub lease, ``delivery.landed``, the terminal transition and the route release
        are one transaction; guarded by ``delivery.landed``, True only when it wrote."""
        with self._store.write("finalize_delivery") as conn:
            already = conn.execute(
                select(s.delivery_landed.c.id).where(s.delivery_landed.c.chunk_id == chunk_id)
            ).first()
            if already is not None:
                return False
            conn.execute(
                s.lease_facts.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(s.delivery_landed.insert().values(chunk_id=chunk_id, landed_at=at))
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
            conn.execute(
                s.route_released.insert().values(chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id))
            )
            enqueue_close_intents(conn, chunk_id, at=at)
            return True

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: datetime
    ) -> bool:
        """Idempotent per ``(chunk_id, source, ref, outcome)`` — mirrors
        ``ChunkArtifactsStore.record_hub_artifact``'s own already-existed-row contract. A
        ``closed``/``gone`` outcome also retires the matching pending ``close_intents``
        row, same transaction, whether or not this call wrote a fresh outcome row — a
        replay finishes an interrupted retirement. A ``failed`` outcome instead appends a
        ``close_intent_attempts`` row for its matching intent, same transaction, on
        *every* call — never gated by ``wrote``, since the backoff ledger is its own
        append-only fact, not idempotency-guarded like the outcome row above (blizzard#524
        D7). See ``blizzard-context/architecture/crash-correctness/hub.md``."""
        with self._store.write("record_work_item_closure") as conn:
            already = conn.execute(
                select(s.work_item_closures.c.id).where(
                    (s.work_item_closures.c.chunk_id == chunk_id)
                    & (s.work_item_closures.c.source == pointer.source)
                    & (s.work_item_closures.c.ref == pointer.ref)
                    & (s.work_item_closures.c.outcome == outcome.value)
                )
            ).first()
            wrote = already is None
            if wrote:
                conn.execute(
                    s.work_item_closures.insert().values(
                        chunk_id=chunk_id,
                        source=pointer.source,
                        ref=pointer.ref,
                        outcome=outcome.value,
                        reason=reason,
                        recorded_at=at,
                    )
                )
            if outcome in (WorkItemCloseOutcome.CLOSED, WorkItemCloseOutcome.GONE):
                conn.execute(
                    update(s.close_intents)
                    .where(
                        (s.close_intents.c.chunk_id == chunk_id)
                        & (s.close_intents.c.source == pointer.source)
                        & (s.close_intents.c.ref == pointer.ref)
                        & (s.close_intents.c.retired_at.is_(None))
                    )
                    .values(retired_at=at)
                )
            elif outcome is WorkItemCloseOutcome.FAILED:
                intent_id = conn.execute(
                    select(s.close_intents.c.id).where(
                        (s.close_intents.c.chunk_id == chunk_id)
                        & (s.close_intents.c.source == pointer.source)
                        & (s.close_intents.c.ref == pointer.ref)
                        & (s.close_intents.c.retired_at.is_(None))
                    )
                ).scalar_one_or_none()
                if intent_id is not None:  # never enqueued (or already retired) — no backoff to tick
                    conn.execute(
                        insert(s.close_intent_attempts).values(intent_id=intent_id, attempted_at=at, outcome="failed")
                    )
            return wrote

    def record_work_item_materialization(
        self,
        proposal_id: str,
        *,
        outcome: WorkItemMaterializationOutcome,
        pointer: WorkRef | None,
        reason: str | None,
        at: datetime,
    ) -> bool:
        with self._store.write("record_work_item_materialization") as conn:
            return insert_materialization_row(
                conn, proposal_id=proposal_id, outcome=outcome, pointer=pointer, reason=reason, at=at
            )


def _conforms_delivery(x: ChunkDeliveryStore) -> IWriteChunkDeliveryRepository:
    return x
