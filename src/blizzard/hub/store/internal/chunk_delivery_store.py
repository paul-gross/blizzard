"""SQLAlchemy adapter for the chunk delivery seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``).

``finalize_delivery`` is one transaction on one connection — the shared row helpers
below are plain function calls inside its ``with self._store.write(...)`` block,
never a second connection."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func

from blizzard.hub.domain.chunk.delivery_read import LandingFact
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, PendingCloseIntent, WorkItemCloseOutcome, WorkRef
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.work_items.closure import TERMINAL_CLOSE_OUTCOMES
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    enqueue_close_intents,
    ephemeral_ids_select,
    graph_id_of,
    lock_chunk_row,
    next_route_seq,
    record_hub_lease,
)
from blizzard.hub.store.scoped import ScopedConnections

#: The attempt outcome a rate-budget refusal writes: it holds the intent to its ``resume_at`` and spends no backoff.
DEFERRED_ATTEMPT = "deferred"


class ChunkDeliveryStore:
    """The chunk's landing and closure facts."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def landing_facts(self, chunk_id: str, *, after: int, limit: int) -> list[LandingFact]:
        table = s.delivery_repo_landed
        with self._store.read("landing_facts") as conn:
            rows = conn.execute(
                self._store.select(table, table.c.id, table.c.repo, table.c.commit_hash, table.c.landed_at)
                .where(table.c.chunk_id == chunk_id, table.c.id > after)
                .order_by(table.c.id)
                .limit(limit)
            ).all()
        return [LandingFact(row.id, row.repo, row.commit_hash, row.landed_at) for row in rows]

    def landed_repos(self, chunk_id: str) -> set[str]:
        with self._store.read("landed_repos") as conn:
            return {
                r.repo
                for r in conn.execute(
                    self._store.select(s.delivery_repo_landed, s.delivery_repo_landed.c.repo).where(
                        s.delivery_repo_landed.c.chunk_id == chunk_id
                    )
                ).all()
            }

    def count_landed_since(self, repo: str, since: datetime) -> int:
        with self._store.read("count_landed_since") as conn:
            return conn.execute(
                self._store.select(s.delivery_repo_landed, func.count()).where(
                    s.delivery_repo_landed.c.repo == repo, s.delivery_repo_landed.c.landed_at > since
                )
            ).scalar_one()

    def pending_close_intents(self) -> list[PendingCloseIntent]:
        """Every pending, non-ephemeral intent's own backoff history in one flat, outer-
        joined, already-aggregated read — never one query per intent. A ``deferred`` attempt is
        neither counted nor dated; the newest one's ``resume_at`` rides along instead."""
        attempt = s.close_intent_attempts
        spent = attempt.c.outcome != DEFERRED_ATTEMPT
        attempts = (
            self._store.select(
                attempt,
                attempt.c.intent_id,
                func.count(case((spent, attempt.c.id))).label("attempt_count"),
                func.max(case((spent, attempt.c.attempted_at))).label("last_attempt_at"),
            )
            .group_by(attempt.c.intent_id)
            .subquery()
        )
        resume_at = (
            self._store.select(attempt, attempt.c.resume_at)
            .where(attempt.c.intent_id == s.close_intents.c.id, attempt.c.outcome == DEFERRED_ATTEMPT)
            .order_by(
                attempt.c.attempted_at.desc(), attempt.c.id.desc()
            )  # An explicit total order (`bzh:sql-portable`)
            .limit(1)
            .scalar_subquery()
        )
        with self._store.read("pending_close_intents") as conn:
            rows = conn.execute(
                self._store.select(
                    self._store.scope_join(
                        s.close_intents, attempts, attempts.c.intent_id == s.close_intents.c.id, isouter=True
                    ),
                    s.close_intents.c.id,
                    s.close_intents.c.chunk_id,
                    s.close_intents.c.source,
                    s.close_intents.c.ref,
                    attempts.c.attempt_count,
                    attempts.c.last_attempt_at,
                    resume_at.label("resume_at"),
                )
                .where(s.close_intents.c.retired_at.is_(None))
                .where(s.close_intents.c.chunk_id.not_in(ephemeral_ids_select(self._store)))
                .order_by(s.close_intents.c.id)  # An explicit total order (`bzh:sql-portable`)
            ).all()
        return [
            PendingCloseIntent(
                chunk_id=row.chunk_id,
                ref=WorkRef(source=row.source, ref=row.ref),
                intent_id=row.id,
                attempt_count=row.attempt_count or 0,
                last_attempt_at=row.last_attempt_at,
                resume_at=row.resume_at,
            )
            for row in rows
        ]

    def record_close_attempt_deferred(self, intent_id: int, *, at: datetime, resume_at: datetime) -> None:
        with self._store.write("record_close_attempt_deferred") as conn:
            conn.execute(
                self._store.insert(s.close_intent_attempts).values(
                    intent_id=intent_id, attempted_at=at, outcome=DEFERRED_ATTEMPT, resume_at=resume_at
                )
            )

    def record_delivery_repo_landed(
        self,
        chunk_id: str,
        *,
        repo: str,
        commit_hash: str,
        repository_id: str | None,
        at: datetime,
        close_decision: CloseEnqueueDecision,
    ) -> None:
        with self._store.write("record_delivery_repo_landed") as conn:
            conn.execute(
                self._store.insert(s.delivery_repo_landed).values(
                    chunk_id=chunk_id, repo=repo, commit_hash=commit_hash, repository_id=repository_id, landed_at=at
                )
            )
            enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)

    def record_delivery_landed(self, chunk_id: str, *, at: datetime, close_decision: CloseEnqueueDecision) -> None:
        with self._store.write("record_delivery_landed") as conn:
            conn.execute(self._store.insert(s.delivery_landed).values(chunk_id=chunk_id, landed_at=at))
            enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)

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
        close_decision: CloseEnqueueDecision,
    ) -> bool:
        """`src/blizzard/hub/domain/chunk/ports/delivery.py`'s `IWriteChunkDeliveryRepository.finalize_delivery`.

        The hub lease, ``delivery.landed``, the terminal transition and the route release
        share one write; an existing ``delivery_landed`` row is the replay guard."""
        with self._store.write("finalize_delivery") as conn:
            lock_chunk_row(conn, chunk_id)
            already = conn.execute(
                self._store.select(s.delivery_landed, s.delivery_landed.c.id).where(
                    s.delivery_landed.c.chunk_id == chunk_id
                )
            ).first()
            if already is not None:
                return False
            record_hub_lease(conn, chunk_id, epoch=epoch, runner_id=runner_id, at=at)
            conn.execute(self._store.insert(s.delivery_landed).values(chunk_id=chunk_id, landed_at=at))
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
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
            enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)
            return True

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: datetime
    ) -> bool:
        """`src/blizzard/hub/domain/chunk/ports/delivery.py`'s
        `IWriteChunkDeliveryRepository.record_work_item_closure`."""
        with self._store.write("record_work_item_closure") as conn:
            already = conn.execute(
                self._store.select(s.work_item_closures, s.work_item_closures.c.id).where(
                    (s.work_item_closures.c.chunk_id == chunk_id)
                    & (s.work_item_closures.c.source == pointer.source)
                    & (s.work_item_closures.c.ref == pointer.ref)
                    & (s.work_item_closures.c.outcome == outcome.value)
                )
            ).first()
            wrote = already is None
            if wrote:
                conn.execute(
                    self._store.insert(s.work_item_closures).values(
                        chunk_id=chunk_id,
                        source=pointer.source,
                        ref=pointer.ref,
                        outcome=outcome.value,
                        reason=reason,
                        recorded_at=at,
                    )
                )
            if outcome in TERMINAL_CLOSE_OUTCOMES:
                conn.execute(
                    self._store.update(s.close_intents)
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
                    self._store.select(s.close_intents, s.close_intents.c.id).where(
                        (s.close_intents.c.chunk_id == chunk_id)
                        & (s.close_intents.c.source == pointer.source)
                        & (s.close_intents.c.ref == pointer.ref)
                        & (s.close_intents.c.retired_at.is_(None))
                    )
                ).scalar_one_or_none()
                if intent_id is not None:  # never enqueued (or already retired) — no backoff to tick
                    conn.execute(
                        self._store.insert(s.close_intent_attempts).values(
                            intent_id=intent_id, attempted_at=at, outcome="failed"
                        )
                    )
            return wrote


_delivery_conforms: type[IWriteChunkDeliveryRepository] = ChunkDeliveryStore
