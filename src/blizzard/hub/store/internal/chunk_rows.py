"""Shared SQL infrastructure for the chunk-seam adapters.

Every ``chunk_<seam>_store.py`` adapter in this package is a thin, independently
constructed class over the same ``chunks``-rooted schema; the column codecs, the
question-query builder, the four composite-write free functions ``work_item_store.py``
also reaches for, and the private row/id helpers more than one adapter needs, live here
so no seam re-derives another seam's own reading or writing. Everything below is
package-private — imported by the adapters in this directory, never by a domain caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection, func, insert, select

from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import (
    Chunk,
    IntendedMigration,
    MigrationMode,
    QuestionRow,
    RouteCreatedFact,
    RouteHistory,
    RouteReleasedFact,
    WorkItemCloseOutcome,
    WorkItemMaterializationOutcome,
    WorkRef,
)
from blizzard.hub.store import schema as s


@dataclass(frozen=True)
class MigrationColumn:
    """``chunks.intended_migration``'s JSON shape — ``None`` writes and reads ``NULL`` (issue #124)."""

    def encode(self, intended: IntendedMigration | None) -> str | None:
        if intended is None:
            return None
        return json.dumps({"mode": intended.mode.value, "graph_id": intended.graph_id, "node_name": intended.node_name})

    def decode(self, value: str | None) -> IntendedMigration | None:
        if value is None:
            return None
        data = json.loads(value)
        return IntendedMigration(
            mode=MigrationMode(data["mode"]), graph_id=data["graph_id"], node_name=data["node_name"]
        )


@dataclass(frozen=True)
class ModelColumn:
    """``chunks.default_model``'s column shape — a JSON ``list[str]`` (issue #144).

    An empty preference list writes ``NULL`` rather than ``"[]"``, so "express no
    preference" reads identically however the chunk reached it."""

    def encode(self, preferences: list[str]) -> str | None:
        return json.dumps(list(preferences)) if preferences else None

    def decode(self, value: str | None) -> list[str]:
        return [str(m) for m in json.loads(value)] if value else []


@dataclass(frozen=True)
class QuestionQuery:
    """A question row with its derived answer and delivery state, in one query.

    Both joins are **outer**. Deliveries pre-aggregate to the **earliest** instant per
    question, since ``answer_deliveries`` is append-only with no per-question uniqueness."""

    @property
    def select(self):  # type: ignore[no-untyped-def]
        earliest_delivery = (
            select(
                s.answer_deliveries.c.question_id.label("question_id"),
                func.min(s.answer_deliveries.c.delivered_at).label("delivered_at"),
            )
            .group_by(s.answer_deliveries.c.question_id)
            .subquery()
        )
        return (
            select(
                s.questions,
                s.question_answers.c.answer,
                s.question_answers.c.answered_by,
                s.question_answers.c.answered_at,
                earliest_delivery.c.delivered_at,
            )
            .select_from(s.questions)
            .outerjoin(s.question_answers, s.question_answers.c.question_id == s.questions.c.question_id)
            .outerjoin(earliest_delivery, earliest_delivery.c.question_id == s.questions.c.question_id)
        )

    def of(self, q) -> QuestionRow:  # type: ignore[no-untyped-def]
        """One :attr:`select` row as its domain shape — every derived state read off the
        joined columns, so the three question reads cannot disagree."""
        return QuestionRow(
            question_id=q.question_id,
            chunk_id=q.chunk_id,
            node_id=q.node_id,
            session_id=q.session_id,
            runner_id=q.runner_id,
            epoch=q.epoch,
            question=q.question,
            options=json.loads(q.options) if q.options else [],
            asked_at=q.asked_at,
            # The outer joins leave these NULL when the row is absent; `answered_by` is the
            # answer row's own non-nullable column, so its presence *is* the answer row's.
            answered=q.answered_by is not None,
            answer=q.answer,
            answered_by=q.answered_by,
            answered_at=q.answered_at,
            delivered=q.delivered_at is not None,
            delivered_at=q.delivered_at,
        )


INTENDED_MIGRATION = MigrationColumn()
DEFAULT_MODEL = ModelColumn()
QUESTIONS = QuestionQuery()

# The generic ``merged/<repo>`` landing marker (issue #67) — mirrors domain/work.py's own
# copy (``LandedRepos``'s), which reads it back; each side owns its own constant.
MARKER_PREFIX = "merged/"


def insert_chunk_rows(conn: Connection, chunk: Chunk) -> None:
    """Insert one chunk's ``chunks`` row and its ``chunk_work_refs`` rows on a
    caller-supplied ``conn`` — the caller owns the transaction boundary, so a composite
    write can fold this into its own transaction."""
    conn.execute(
        insert(s.chunks).values(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            minted_at=chunk.minted_at,
            # `chunks.model` is deliberately omitted (issue #144) — the insert
            # leans on its `server_default`.
            default_model=DEFAULT_MODEL.encode(chunk.default_model),
            default_effort=chunk.default_effort,
        )
    )
    for pointer in chunk.work_refs:
        conn.execute(insert(s.chunk_work_refs).values(chunk_id=chunk.chunk_id, source=pointer.source, ref=pointer.ref))


def insert_promote_rows(conn: Connection, chunk_id: str, *, position: float, at: datetime) -> int | None:
    """Insert one chunk's ``chunk_promoted`` and ``queue_positions`` rows on a
    caller-supplied ``conn`` — mirrors :func:`insert_chunk_rows`'s shared-connection
    shape, so ``ChunkQueueStore.record_promote_with_tail_position`` and a routine run's
    own mint-and-promote composite write both fold the promote-then-tail-stamp pair into
    their own transaction (blizzard#392). No idempotency check: a caller minting a fresh
    chunk has nothing to check against, and ``record_promote_with_tail_position`` keeps
    its own ahead of this call. Returns the freshly-inserted ``chunk_promoted.id``."""
    result = conn.execute(insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))
    conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else None


def record_deleted_row(conn: Connection, chunk_id: str, *, by: str, at: datetime) -> int:
    """Insert one ``chunk_deleted`` row on a caller-supplied ``conn`` (issue #364) —
    mirrors :func:`insert_chunk_rows`'s shared-connection shape, so the withdrawal
    half of a composite delete write can fold this into its own transaction. Returns
    the freshly-inserted ``chunk_deleted.id``."""
    result = conn.execute(insert(s.chunk_deleted).values(chunk_id=chunk_id, deleted_at=at, deleted_by=by))
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else 0


def record_grouped_row_conn(conn: Connection, chunk_id: str, *, grouped_into: str, at: datetime) -> int:
    """Insert one ``chunk_grouped`` row on a caller-supplied ``conn`` (issue #460) —
    mirrors :func:`record_deleted_row`'s shared-connection shape, extracted from
    ``ChunkLifecycleStore.record_grouped`` so the fold's own composite write
    (``ChunkDependenciesStore.record_fold``) can fold it into its own transaction.
    Returns the freshly-inserted ``chunk_grouped.id``."""
    result = conn.execute(insert(s.chunk_grouped).values(chunk_id=chunk_id, grouped_into=grouped_into, grouped_at=at))
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else 0


def insert_materialization_row(
    conn: Connection,
    *,
    proposal_id: str,
    outcome: WorkItemMaterializationOutcome,
    pointer: WorkRef | None,
    reason: str | None,
    at: datetime,
) -> bool:
    """Insert one ``work_item_materializations`` row on a caller-supplied ``conn`` (D5) —
    mirrors :func:`insert_chunk_rows`/:func:`record_deleted_row`'s shared-connection
    shape, so the mint/append composites can fold this into their own transaction.
    Idempotent per ``proposal_id``: returns False and writes nothing when a judgment
    already exists."""
    already = conn.execute(
        select(s.work_item_materializations.c.id).where(s.work_item_materializations.c.proposal_id == proposal_id)
    ).first()
    if already is not None:
        return False
    conn.execute(
        insert(s.work_item_materializations).values(
            proposal_id=proposal_id,
            outcome=outcome.value,
            source=pointer.source if pointer is not None else None,
            ref=pointer.ref if pointer is not None else None,
            reason=reason,
            recorded_at=at,
        )
    )
    return True


def ephemeral_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
    """Every chunk id gone from every read (issue #364) — the union of grouped-away
    and deleted chunks; widened here so every consumer across every seam inherits the
    exclusion."""
    grouped = {r.chunk_id for r in conn.execute(select(s.chunk_grouped.c.chunk_id)).all()}
    deleted = {r.chunk_id for r in conn.execute(select(s.chunk_deleted.c.chunk_id)).all()}
    return grouped | deleted


def route_of_conn(conn: Connection, chunk_id: str) -> Route | None:
    """:meth:`~blizzard.hub.store.internal.chunk_route_store.ChunkRouteStore.route_of`'s
    query body, taking an already-open ``conn`` so a write transaction elsewhere (the
    lifecycle adapter's own ``record_stop``/``record_completion``) can resolve the same
    question inside its own commit (issue #118). Delegates the tie-break to
    :attr:`~blizzard.hub.domain.work.RouteHistory.newest`, so route liveness has exactly
    one answer at a same-instant tie (issue #41)."""
    # (created_at, seq) desc — must stay in lockstep with the key
    # `RouteHistory.newest` orders by; that property, not this query, owns it.
    created = conn.execute(
        select(s.route_created)
        .where(s.route_created.c.chunk_id == chunk_id)
        .order_by(s.route_created.c.created_at.desc(), s.route_created.c.seq.desc())
    ).first()
    if created is None:
        return None
    # (released_at, seq) desc — see the order_by above; same owner.
    released = conn.execute(
        select(s.route_released.c.released_at, s.route_released.c.seq)
        .where(s.route_released.c.chunk_id == chunk_id)
        .order_by(s.route_released.c.released_at.desc(), s.route_released.c.seq.desc())
    ).first()
    routes_released = [RouteReleasedFact(released_at=released.released_at, seq=released.seq)] if released else []
    routes_created = [RouteCreatedFact(created_at=created.created_at, seq=created.seq)]
    if RouteHistory(routes_created, routes_released).newest is None:
        return None
    env_ids = [
        e.environment_id
        for e in conn.execute(
            select(s.route_environments.c.environment_id).where(s.route_environments.c.route_id == created.route_id)
        ).all()
    ]
    return Route(
        chunk_id=chunk_id,
        runner_id=created.runner_id,
        workspace_id=created.workspace_id,
        environment_ids=env_ids,
        created_at=created.created_at,
        route_id=created.route_id,
    )


def next_route_seq(conn: Connection, chunk_id: str) -> int:
    """One past the current max ``seq`` across ``route_created``, ``route_released``
    and ``route_token_minted`` for this chunk, so the triple is totally ordered even
    when timestamps tie. Read-then-insert, so concurrent callers are serialized by a
    no-op ``UPDATE`` on the chunk's own row — one portable write-lock statement for
    both dialects (``bzh:sql-portable``; ``tests/test_route_seq_concurrency.py``)."""
    conn.execute(s.chunks.update().where(s.chunks.c.chunk_id == chunk_id).values(chunk_id=chunk_id))
    created_max = conn.execute(
        select(func.max(s.route_created.c.seq)).where(s.route_created.c.chunk_id == chunk_id)
    ).scalar()
    released_max = conn.execute(
        select(func.max(s.route_released.c.seq)).where(s.route_released.c.chunk_id == chunk_id)
    ).scalar()
    token_max = conn.execute(
        select(func.max(s.route_token_minted.c.seq)).where(s.route_token_minted.c.chunk_id == chunk_id)
    ).scalar()
    return max(created_max or 0, released_max or 0, token_max or 0) + 1


def graph_id_of(conn: Connection, chunk_id: str) -> str:
    """The chunk's then-current graph pin — the provenance a transition is stamped
    with (issue #90). Read inside the writing transaction so a transition always
    carries the graph it actually moved within, even as a later migration re-pins
    ``chunks.graph_id`` in a subsequent write."""
    return conn.execute(select(s.chunks.c.graph_id).where(s.chunks.c.chunk_id == chunk_id)).scalar_one()


def latest_epoch(conn: Connection, chunk_id: str) -> int:
    """The chunk's newest fencing epoch, read INSIDE the caller's transaction — the same
    fold ``ChunkFacts.latest_epoch`` derives, across lease facts and operator restarts.

    Read here rather than handed in, so a read-then-write epoch decision cannot be
    overtaken between the two (``bzh:epoch-fencing``)."""
    lease_max = conn.execute(
        select(func.max(s.lease_facts.c.epoch)).where(s.lease_facts.c.chunk_id == chunk_id)
    ).scalar()
    restart_max = conn.execute(
        select(func.max(s.chunk_restarts.c.epoch)).where(s.chunk_restarts.c.chunk_id == chunk_id)
    ).scalar()
    return max(lease_max or 0, restart_max or 0)


def row_exists(conn, table, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
    return conn.execute(select(table.c.chunk_id).where(table.c.chunk_id == chunk_id).limit(1)).first() is not None


def insert_proposals(conn: Connection, proposals: list[WorkItemProposalRow], *, at: datetime) -> None:
    for row in proposals:
        conn.execute(
            insert(s.work_item_proposals).values(
                proposal_id=row.proposal_id,
                chunk_id=row.chunk_id,
                node_id=row.node_id,
                node_name=row.node_name,
                epoch=row.epoch,
                ordinal=row.ordinal,
                kind=row.kind,
                data=row.data,
                proposed_at=at,
                runner_id=row.runner_id,
            )
        )


def enqueue_close_intents(conn: Connection, chunk_id: str, *, at: datetime) -> None:
    """Enqueue one pending close intent per this chunk's still-open work ref (D1,
    blizzard#383) — called, inside the caller's own transaction, from every write
    that lands or completes a chunk, across the movement/delivery/lifecycle/decisions/
    artifacts/hub_exec seams. A chunk in the ephemeral set enqueues nothing; a ref
    already carrying a terminal ``work_item_closures`` outcome is skipped; a replayed
    landing writes nothing new (unique on ``chunk_id, source, ref``)."""
    if chunk_id in ephemeral_ids(conn):
        return
    refs = conn.execute(
        select(s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref).where(s.chunk_work_refs.c.chunk_id == chunk_id)
    ).all()
    if not refs:
        return
    terminal = {
        (r.source, r.ref)
        for r in conn.execute(
            select(s.work_item_closures.c.source, s.work_item_closures.c.ref).where(
                (s.work_item_closures.c.chunk_id == chunk_id)
                & s.work_item_closures.c.outcome.in_(
                    [WorkItemCloseOutcome.CLOSED.value, WorkItemCloseOutcome.GONE.value]
                )
            )
        ).all()
    }
    already = {
        (r.source, r.ref)
        for r in conn.execute(
            select(s.close_intents.c.source, s.close_intents.c.ref).where(s.close_intents.c.chunk_id == chunk_id)
        ).all()
    }
    for row in refs:
        if (row.source, row.ref) in terminal or (row.source, row.ref) in already:
            continue
        conn.execute(
            insert(s.close_intents).values(
                chunk_id=chunk_id, source=row.source, ref=row.ref, enqueued_at=at, retired_at=None
            )
        )


def proposal_row(row) -> WorkItemProposalRow:  # type: ignore[no-untyped-def]
    return WorkItemProposalRow(
        proposal_id=row.proposal_id,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        node_name=row.node_name,
        epoch=row.epoch,
        ordinal=row.ordinal,
        kind=row.kind,
        data=row.data,
        runner_id=row.runner_id,
    )


def chunk_row(conn, row) -> Chunk:  # type: ignore[no-untyped-def]
    pointers = [
        WorkRef(source=p.source, ref=p.ref)
        for p in conn.execute(select(s.chunk_work_refs).where(s.chunk_work_refs.c.chunk_id == row.chunk_id)).all()
    ]
    return Chunk(
        chunk_id=row.chunk_id,
        graph_id=row.graph_id,
        work_refs=pointers,
        minted_at=row.minted_at,
        default_model=DEFAULT_MODEL.decode(row.default_model),
        default_effort=row.default_effort,
        intended_migration=INTENDED_MIGRATION.decode(row.intended_migration),
    )
