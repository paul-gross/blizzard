"""Package-private SQL helpers shared by the ``chunks``-rooted seam adapters.

Column codecs, question queries and composite-write helpers live here so adapters
do not re-derive another seam's reads or writes. Domain callers never import this
module.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from sqlalchemy import Connection, Select, func, insert, literal, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.roles import adapter_model
from blizzard.hub.domain.chunk.model import (
    Chunk,
    IntendedMigration,
    NodeQuestion,
    RouteCreatedFact,
    RouteHistory,
    RouteReleasedFact,
    WorkItemMaterializationOutcome,
    WorkRef,
    is_landed_revision,
)
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead, ILockedWorkRefRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, EpochOwner, FenceRefusal, MintAdmission
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.domain.work_items.closure import TERMINAL_CLOSE_OUTCOMES
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_terminal_predicates import chunk_is_terminal


@adapter_model
@dataclass(frozen=True)
class MigrationColumn:
    """``chunks.intended_migration``'s JSON shape — ``None`` writes and reads ``NULL``."""

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


@adapter_model
@dataclass(frozen=True)
class ModelColumn:
    """``chunks.default_model``'s column shape — a JSON ``list[str]``.

    An empty preference list writes ``NULL`` rather than ``"[]"``, so "express no
    preference" reads identically however the chunk reached it."""

    def encode(self, preferences: list[str]) -> str | None:
        return json.dumps(list(preferences)) if preferences else None

    def decode(self, value: str | None) -> list[str]:
        return [str(m) for m in json.loads(value)] if value else []


@adapter_model
@dataclass(frozen=True)
class QuestionQuery:
    """A question row with its derived answer and delivery state, in one query.

    Both joins are **outer**. Deliveries pre-aggregate to the **earliest** instant per
    question, since ``answer_deliveries`` is append-only with no per-question uniqueness."""

    @property
    def select(self):  # ast-grep-ignore: bzh:property-delegates  # type: ignore[no-untyped-def]
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

    def of(self, q) -> NodeQuestion:  # type: ignore[no-untyped-def]
        """One :attr:`select` row as its domain shape — every derived state read off the
        joined columns, so the three question reads cannot disagree."""
        return NodeQuestion(
            question_id=q.question_id,
            chunk_id=q.chunk_id,
            node_id=q.node_id,
            session_id=q.session_id,
            harness_id=q.harness_id,
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
DEFAULT_HARNESSES = ModelColumn()
QUESTIONS = QuestionQuery()

# The ``merged/<repo>`` marker also appears in domain/chunk/model.py's ``LandedRepos``.
MARKER_PREFIX = "merged/"


def is_landing_marker(name: str, data: str) -> bool:
    """Whether an artifact is a ``merged/<repo>`` marker naming a revision — the writes that owe close intents."""
    return name.startswith(MARKER_PREFIX) and is_landed_revision(data)


def insert_chunk_rows(conn: Connection, chunk: Chunk) -> None:
    """Insert one chunk's ``chunks`` row and its ``chunk_work_refs`` rows on a
    caller-supplied ``conn`` — the caller owns the transaction boundary, so a composite
    write can fold this into its own transaction."""
    conn.execute(
        insert(s.chunks).values(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            minted_at=chunk.minted_at,
            # `chunks.model` is deliberately omitted — the insert
            # leans on its `server_default`.
            default_model=DEFAULT_MODEL.encode(chunk.default_model),
            default_effort=chunk.default_effort,
            default_harnesses=DEFAULT_HARNESSES.encode(chunk.default_harnesses),
        )
    )
    for pointer in chunk.work_refs:
        conn.execute(insert(s.chunk_work_refs).values(chunk_id=chunk.chunk_id, source=pointer.source, ref=pointer.ref))


def insert_promote_rows(conn: Connection, chunk_id: str, *, position: float, at: datetime) -> int | None:
    """Insert one chunk's ``chunk_promoted`` and ``queue_positions`` rows on a
    caller-supplied ``conn`` — mirrors :func:`insert_chunk_rows`'s shared-connection
    shape. No idempotency check; the caller owns it. Returns the freshly-inserted
    ``chunk_promoted.id``."""
    result = conn.execute(insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))
    conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else None


def record_deleted_row(conn: Connection, chunk_id: str, *, by: str, at: datetime) -> int:
    """Insert one ``chunk_deleted`` row on a caller-supplied ``conn`` —
    mirrors :func:`insert_chunk_rows`'s shared-connection shape, so the withdrawal
    half of a composite delete write can fold this into its own transaction. Returns
    the freshly-inserted ``chunk_deleted.id``."""
    result = conn.execute(insert(s.chunk_deleted).values(chunk_id=chunk_id, deleted_at=at, deleted_by=by))
    key = result.inserted_primary_key
    return int(key[0]) if key is not None else 0


def record_grouped_row_conn(conn: Connection, chunk_id: str, *, grouped_into: str, at: datetime) -> int:
    """Insert one ``chunk_grouped`` row on a caller-supplied ``conn`` —
    mirrors :func:`record_deleted_row`'s shared-connection shape, so the fold's own
    composite write can fold every target's row into one transaction. Returns the
    freshly-inserted ``chunk_grouped.id``."""
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
    """Insert one ``work_item_materializations`` row on a caller-supplied ``conn`` —
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


_EPHEMERAL_TABLES = (s.chunk_grouped, s.chunk_deleted)
"""The tables whose chunk ids are gone from every read — the one place the ephemeral set is
declared; the subquery helper and the id-set helpers below all derive from it."""


def ephemeral_ids_select() -> Select:  # type: ignore[type-arg]
    """The ephemeral set as a ``chunk_id`` subquery for a ``NOT IN`` — the union of
    grouped-away and deleted chunks, for a read that filters in SQL rather than fetching
    :func:`ephemeral_ids`; widened here so every consumer inherits the exclusion."""
    first, *rest = (select(t.c.chunk_id) for t in _EPHEMERAL_TABLES)
    return first.union(*rest)  # type: ignore[return-value]


def ephemeral_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
    """Every chunk id gone from every read — the union of grouped-away
    and deleted chunks; widened here so every consumer across every seam inherits the
    exclusion."""
    return {r.chunk_id for table in _EPHEMERAL_TABLES for r in conn.execute(select(table.c.chunk_id)).all()}


def is_ephemeral_id(conn, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
    """Whether ``chunk_id`` alone is grouped-away or deleted — a single-id call site's
    narrow sibling of :func:`ephemeral_ids`, two targeted existence checks rather than
    that helper's unfiltered scan of both tables."""
    return any(row_exists(conn, table, chunk_id) for table in _EPHEMERAL_TABLES)


def ephemeral_ids_in(conn, batch: Sequence[str]) -> set[str]:  # type: ignore[no-untyped-def]
    """Which of ``batch``'s ids are grouped-away or deleted — :func:`graph_id_of_batch`'s
    own exclusion, factored out so a caller already holding ``batch``'s full ``chunks``
    rows doesn't pay for a second, narrower read of the same ids just to get it. A
    singleton batch excludes via :func:`is_ephemeral_id`'s two targeted checks; a larger
    one via two id-batch-bounded ``IN`` queries."""
    if len(batch) == 1:
        (chunk_id,) = batch
        return {chunk_id} if is_ephemeral_id(conn, chunk_id) else set()
    return {
        r.chunk_id
        for table in _EPHEMERAL_TABLES
        for r in conn.execute(select(table.c.chunk_id).where(table.c.chunk_id.in_(batch))).all()
    }


def graph_id_of_batch(conn, batch: Sequence[str] | None) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """A selection's chunk id -> graph id pins, ephemeral ids excluded. ``None`` (the
    whole live fleet) keeps :func:`ephemeral_ids`'s own single unfiltered scan; a bounded
    batch delegates to :func:`ephemeral_ids_in`."""
    if batch is None:
        ephemeral = ephemeral_ids(conn)
        rows = conn.execute(select(s.chunks.c.chunk_id, s.chunks.c.graph_id)).all()
    else:
        rows = conn.execute(
            select(s.chunks.c.chunk_id, s.chunks.c.graph_id).where(s.chunks.c.chunk_id.in_(batch))
        ).all()
        ephemeral = ephemeral_ids_in(conn, batch)
    return {r.chunk_id: r.graph_id for r in rows if r.chunk_id not in ephemeral}


def route_of_conn(conn: Connection, chunk_id: str) -> Route | None:
    """Resolve the live route on the caller's connection, even inside a write.

    :attr:`~blizzard.hub.domain.chunk.model.RouteHistory.newest` owns same-instant ties."""
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


def lock_chunk_row(conn: Connection, chunk_id: str) -> None:
    """Take the chunk row's write lock, as the transaction's FIRST statement — a no-op
    ``UPDATE`` on a row the caller already knows exists (``bzh:sql-portable``,
    ``bzh:store-exclusive-write``; ``tests/test_route_seq_concurrency.py``). Takes
    SQLite's single writer lock before any later read; on Postgres, queues a concurrent
    locker of the same row. Every guard read a decision rests on must follow this call."""
    conn.execute(s.chunks.update().where(s.chunks.c.chunk_id == chunk_id).values(chunk_id=chunk_id))


def lock_keys(conn: Connection, namespace: str, keys: Sequence[str]) -> None:
    """Take the write lock of each ``(namespace, key)`` row of ``keyed_locks``, in sorted key
    order, as the transaction's FIRST statements — for a decision whose race has no existing
    row to lock (``bzh:store-exclusive-write``). A no-op ``UPDATE`` on an absent row locks
    nothing, so each row is first inserted if unseen: in its own savepoint, so a concurrent
    insert of the same key loses with an ``IntegrityError`` that rolls back only that insert
    (the shape of ``review_findings_store._mint_scope_if_unseen``). The ``UPDATE`` then locks
    a row that exists: SQLite's single writer lock on the first statement; on Postgres, a
    concurrent locker of the same key queues behind this transaction's commit. Rows are
    never deleted."""
    lock = s.keyed_locks
    for key in sorted(set(keys)):
        if (
            conn.execute(select(lock.c.key).where((lock.c.namespace == namespace) & (lock.c.key == key))).first()
            is None
        ):
            try:
                with conn.begin_nested():
                    conn.execute(insert(lock).values(namespace=namespace, key=key))
            except IntegrityError:
                pass
        conn.execute(update(lock).where((lock.c.namespace == namespace) & (lock.c.key == key)).values(key=key))


class _LockedConnection(Protocol):
    """The write token every ``*_locked`` store method needs — the real capability
    :class:`ILockedChunkRead` deliberately does not expose to the domain layer, so a fake
    handle satisfying that Protocol structurally still cannot satisfy this one too."""

    conn: Connection


def conn_of(handle: ILockedChunkRead) -> Connection:
    """Recover the connection from a domain-held :class:`ILockedChunkRead`.

    Real handles are store-built ``LockedChunkTransaction`` instances; this cast
    keeps their concrete type out of the domain and avoids a cyclic import."""
    return cast(_LockedConnection, handle).conn


def work_ref_conn_of(handle: ILockedWorkRefRead) -> Connection:
    """:func:`conn_of`'s sibling for a domain-held :class:`ILockedWorkRefRead`."""
    return cast(_LockedConnection, handle).conn


def next_route_seq(conn: Connection, chunk_id: str) -> int:
    """One past the current max ``seq`` across ``route_created``, ``route_released``
    and ``route_token_minted`` for this chunk, so the triple is totally ordered even
    when timestamps tie. Read-then-insert, so concurrent callers are serialized by
    :func:`lock_chunk_row` (``bzh:sql-portable``; ``tests/test_route_seq_concurrency.py``)."""
    lock_chunk_row(conn, chunk_id)
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


def next_artifact_seq(conn: Connection, chunk_id: str) -> int:
    """One past the current max ``artifacts.seq`` for this chunk — the artifacts' durable
    write order, which a same-millisecond pair of ids cannot settle. Read-then-insert, so
    concurrent callers are serialized by :func:`lock_chunk_row` (``bzh:sql-portable``;
    ``tests/test_artifact_seq_concurrency.py``). Call it in the inserting transaction."""
    lock_chunk_row(conn, chunk_id)
    current = conn.execute(select(func.max(s.artifacts.c.seq)).where(s.artifacts.c.chunk_id == chunk_id)).scalar()
    return (current or 0) + 1


def graph_id_of(conn: Connection, chunk_id: str) -> str:
    """The chunk's then-current graph pin — the provenance a transition is stamped
    with. Read inside the writing transaction so a transition always
    carries the graph it actually moved within, even as a later migration re-pins
    ``chunks.graph_id`` in a subsequent write."""
    return conn.execute(select(s.chunks.c.graph_id).where(s.chunks.c.chunk_id == chunk_id)).scalar_one()


def latest_epoch(conn: Connection, chunk_id: str) -> int:
    """The newest lease, restart or ownership epoch, read in the writing transaction.

    A claim's reservation raises the fence; reading here prevents an overtaken
    read-then-write decision (``bzh:epoch-fencing``)."""
    lease_max = conn.execute(
        select(func.max(s.lease_facts.c.epoch)).where(s.lease_facts.c.chunk_id == chunk_id)
    ).scalar()
    restart_max = conn.execute(
        select(func.max(s.chunk_restarts.c.epoch)).where(s.chunk_restarts.c.chunk_id == chunk_id)
    ).scalar()
    owner_max = conn.execute(
        select(func.max(s.epoch_owners.c.epoch)).where(s.epoch_owners.c.chunk_id == chunk_id)
    ).scalar()
    return max(lease_max or 0, restart_max or 0, owner_max or 0)


def epoch_owner(conn: Connection, chunk_id: str, epoch: int) -> EpochOwner | None:
    """The owner recorded for one epoch of the chunk, or ``None`` while it is unowned."""
    row = conn.execute(
        select(s.epoch_owners.c.runner_id).where(
            (s.epoch_owners.c.chunk_id == chunk_id) & (s.epoch_owners.c.epoch == epoch)
        )
    ).first()
    if row is None:
        return None
    return EpochOwner.hub() if row.runner_id is None else EpochOwner.runner(row.runner_id)


def owning_lease_id(conn: Connection, chunk_id: str, epoch: int) -> str | None:
    """The lease that owns an epoch — the earliest admitted mint at it that named one."""
    return conn.execute(
        select(s.lease_facts.c.lease_id)
        .where(
            (s.lease_facts.c.chunk_id == chunk_id)
            & (s.lease_facts.c.epoch == epoch)
            & s.lease_facts.c.lease_id.is_not(None)
        )
        .order_by(s.lease_facts.c.id)
        .limit(1)
    ).scalar()


def record_epoch_owner(conn: Connection, chunk_id: str, epoch: int, owner: EpochOwner, *, at: datetime) -> None:
    """Record ``owner`` for the epoch unless it already has one — first owner wins. Call it
    after :func:`lock_chunk_row`, on the write's own connection, in the transaction of the
    fact that takes the epoch (``bzh:store-exclusive-write``); the unique constraint backs it."""
    if epoch_owner(conn, chunk_id, epoch) is not None:
        return
    conn.execute(
        s.epoch_owners.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=owner.runner_id, recorded_at=at)
    )


def record_hub_lease(conn: Connection, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
    """A hub mint: its ``lease_facts`` row and the epoch's hub ownership, together."""
    conn.execute(s.lease_facts.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at))
    record_epoch_owner(conn, chunk_id, epoch, EpochOwner.hub(), at=at)


def row_exists(conn, table, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
    return conn.execute(select(table.c.chunk_id).where(table.c.chunk_id == chunk_id).limit(1)).first() is not None


def fence(
    conn: Connection,
    chunk_id: str,
    *,
    epoch: int,
    admission: EpochAdmission,
    claimant: Claimant | None = None,
) -> FenceRefusal | None:
    """The in-transaction write fence (``bzh:epoch-fencing``).

    After :func:`lock_chunk_row` and the replay probe, refuse terminal chunks,
    then stale epochs, then displaced claimants. ``None`` admits the write."""
    newest = latest_epoch(conn, chunk_id)
    if _is_terminal(conn, chunk_id):
        return FenceRefusal.terminal(epoch)
    if not admission.admits(epoch, newest=newest):
        return FenceRefusal.stale(epoch, latest=newest)
    if claimant is not None and not claimant.owns(
        epoch_owner(conn, chunk_id, epoch), owning_lease_id=owning_lease_id(conn, chunk_id, epoch)
    ):
        return FenceRefusal.displaced(epoch, latest=newest)
    return None


def mint_admission(conn: Connection, chunk_id: str, *, epoch: int, runner_id: str) -> MintAdmission:
    """The chunk's state a runner's ``lease.minted`` at ``epoch`` is admitted against, read on
    the minting write's own connection after :func:`lock_chunk_row`."""
    newest = latest_epoch(conn, chunk_id)
    route = route_of_conn(conn, chunk_id)
    return MintAdmission(
        epoch=epoch,
        newest=newest,
        terminal=_is_terminal(conn, chunk_id),
        owner=epoch_owner(conn, chunk_id, epoch),
        owning_lease_id=owning_lease_id(conn, chunk_id, epoch),
        holds_route=route is not None and route.runner_id == runner_id,
    )


def _is_terminal(conn: Connection, chunk_id: str) -> bool:
    """Whether the chunk is stopped or done, read on the caller's connection so this sits inside the same
    transaction as the write it fences."""
    return bool(conn.execute(select(chunk_is_terminal(literal(chunk_id)))).scalar())


def insert_proposals(conn: Connection, proposals: list[StampedWorkItemProposal], *, at: datetime) -> None:
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
    """Enqueue open work refs on the landing/completion write's transaction.

    Ephemeral chunks and terminally closed refs enqueue nothing; the unique
    ``(chunk_id, source, ref)`` key prevents replay duplicates."""
    if is_ephemeral_id(conn, chunk_id):
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
                & s.work_item_closures.c.outcome.in_(sorted(outcome.value for outcome in TERMINAL_CLOSE_OUTCOMES))
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


def proposal_row(row) -> StampedWorkItemProposal:  # type: ignore[no-untyped-def]
    return StampedWorkItemProposal(
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
        default_harnesses=DEFAULT_HARNESSES.decode(row.default_harnesses),
        intended_migration=INTENDED_MIGRATION.decode(row.intended_migration),
    )
