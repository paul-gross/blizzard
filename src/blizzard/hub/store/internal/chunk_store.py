"""SQLAlchemy adapter for the chunk repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened and status is
derived. Timestamps arrive already stamped (``bzh:injected-clock``).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, HUB_EXEC_SLOT_PREFIX, MIGRATION_PREFIX, Id
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import (
    DEFAULT_EVENT_LIST_LIMIT,
    TERMINAL_STATUSES,
    ActivityRow,
    AnswerOutcome,
    BounceFact,
    Chunk,
    ChunkFacts,
    ChunkStatus,
    ClosableWorkRef,
    DecisionChoice,
    DecisionFact,
    DecisionRow,
    EscalationFact,
    EscalationOpen,
    EventRow,
    HubNodePollFact,
    IntendedMigration,
    IWriteChunkRepository,
    LeaseFact,
    MigrationFact,
    MigrationMode,
    MigrationSource,
    PauseFact,
    PrOpenedFact,
    QuestionFact,
    QuestionRow,
    RequeueFact,
    RestartFact,
    RouteCreatedFact,
    RouteHistory,
    RouteReleasedFact,
    RouteTokenMintedFact,
    TransitionFact,
    UsageFact,
    WorkItemCloseOutcome,
    WorkItemMaterializationOutcome,
    WorkRef,
)
from blizzard.hub.store import schema as s

_ROUTE_PREFIX = "route"

# The hub coordinator's own reserved ``transitions.runner_id`` (issue #213) — the only
# fact-table difference between a ``hub-advanced`` and a ``node-completed`` transition.
_HUB_RUNNER_ID = "hub"


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


def record_deleted_row(conn: Connection, chunk_id: str, *, by: str, at: datetime) -> int:
    """Insert one ``chunk_deleted`` row on a caller-supplied ``conn`` (issue #364) —
    mirrors :func:`insert_chunk_rows`'s shared-connection shape, so the withdrawal
    half of a composite delete write can fold this into its own transaction. Returns
    the freshly-inserted ``chunk_deleted.id``."""
    result = conn.execute(insert(s.chunk_deleted).values(chunk_id=chunk_id, deleted_at=at, deleted_by=by))
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


class ChunkStore:
    """Read-write chunk-facts adapter over the hub store engine."""

    def __init__(self, engine: Engine, clock: IClock) -> None:
        self._engine = engine
        self._clock = clock

    # --- reads --------------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if row is None or chunk_id in self._ephemeral_ids(conn):
                return None  # a grouped-away or deleted chunk is ephemeral — gone from every read
            return self._chunk(conn, row)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        with self._engine.connect() as conn:
            chunk = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if chunk is None or chunk_id in self._ephemeral_ids(conn):
                return None
            transition_rows = conn.execute(select(s.transitions).where(s.transitions.c.chunk_id == chunk_id)).all()
            migration_rows = conn.execute(
                select(s.chunk_migrations).where(s.chunk_migrations.c.chunk_id == chunk_id)
            ).all()
            restart_rows = conn.execute(select(s.chunk_restarts).where(s.chunk_restarts.c.chunk_id == chunk_id)).all()
            # The executor map must span every graph the chunk's movement facts touched, not
            # only its current pin (issues #90, #111, #370).
            graph_ids = (
                {chunk.graph_id}
                | {t.graph_id for t in transition_rows}
                | {m.to_graph_id for m in migration_rows}
                | {r.graph_id for r in restart_rows}
                | {r.from_graph_id for r in restart_rows if r.from_graph_id is not None}
            )
            executors = {
                r.node_id: Executor(r.executor)
                for r in conn.execute(
                    select(s.graph_nodes.c.node_id, s.graph_nodes.c.executor).where(
                        s.graph_nodes.c.graph_id.in_(graph_ids)
                    )
                ).all()
            }
            transitions = [
                TransitionFact(
                    to_node_id=t.to_node_id,
                    to_node_executor=executors.get(t.to_node_id, Executor.RUNNER),
                    epoch=t.epoch,
                    recorded_at=t.recorded_at,
                    from_node_id=t.from_node_id,
                    choice_name=t.choice_name,
                    graph_id=t.graph_id,
                )
                for t in transition_rows
            ]
            leases = [
                LeaseFact(epoch=lease.epoch, minted_at=lease.minted_at)
                for lease in conn.execute(select(s.lease_facts).where(s.lease_facts.c.chunk_id == chunk_id)).all()
            ]
            escalations = [
                EscalationFact(
                    epoch=e.epoch,
                    recorded_at=e.recorded_at,
                    takeover_command=e.takeover_command or "",
                    wrapped_takeover_command=e.wrapped_takeover_command or "",
                )
                for e in conn.execute(select(s.escalations).where(s.escalations.c.chunk_id == chunk_id)).all()
            ]
            routes_created = [
                RouteCreatedFact(created_at=r.created_at, seq=r.seq)
                for r in conn.execute(select(s.route_created).where(s.route_created.c.chunk_id == chunk_id)).all()
            ]
            routes_released = [
                RouteReleasedFact(released_at=r.released_at, seq=r.seq)
                for r in conn.execute(select(s.route_released).where(s.route_released.c.chunk_id == chunk_id)).all()
            ]
            route_tokens_minted = [
                RouteTokenMintedFact(token_hash=t.token_hash, minted_at=t.minted_at, seq=t.seq)
                for t in conn.execute(
                    select(s.route_token_minted).where(s.route_token_minted.c.chunk_id == chunk_id)
                ).all()
            ]
            answered = {
                a.question_id
                for a in conn.execute(
                    select(s.question_answers.c.question_id).join(
                        s.questions, s.questions.c.question_id == s.question_answers.c.question_id
                    )
                ).all()
            }
            questions = [
                QuestionFact(question_id=q.question_id, asked_at=q.asked_at, answered=q.question_id in answered)
                for q in conn.execute(select(s.questions).where(s.questions.c.chunk_id == chunk_id)).all()
            ]
            decision_rows = conn.execute(select(s.decisions).where(s.decisions.c.chunk_id == chunk_id)).all()
            resolved_ids = self._resolved_ids(conn, [d.decision_id for d in decision_rows])
            decisions = [
                DecisionFact(
                    decision_id=d.decision_id, submitted_at=d.submitted_at, resolved=d.decision_id in resolved_ids
                )
                for d in decision_rows
            ]
            requeues = [
                RequeueFact(requeued_at=r.requeued_at)
                for r in conn.execute(select(s.requeues).where(s.requeues.c.chunk_id == chunk_id)).all()
            ]
            restarts = [
                RestartFact(
                    to_node_id=r.to_node_id,
                    from_node_id=r.from_node_id,
                    graph_id=r.graph_id,
                    epoch=r.epoch,
                    recorded_at=r.recorded_at,
                    from_graph_id=r.from_graph_id,
                    to_node_executor=executors.get(r.to_node_id, Executor.RUNNER),
                    restarted_by=r.restarted_by,
                    decision_id=r.decision_id,
                )
                for r in restart_rows
            ]
            migrations = [
                MigrationFact(
                    from_node_id=m.from_node_id,
                    from_graph_id=m.from_graph_id,
                    to_graph_id=m.to_graph_id,
                    landed_node_id=m.landed_node_id,
                    choice_name=m.choice_name,
                    model=m.model_after,
                    epoch=m.epoch,
                    recorded_at=m.recorded_at,
                    landed_node_executor=executors.get(m.landed_node_id, Executor.RUNNER),
                    # Null for a row predating the discriminator — read as unrecorded, never
                    # guessed at (issue #164).
                    source=MigrationSource(m.source) if m.source else None,
                )
                for m in migration_rows
            ]
            pauses = [
                PauseFact(paused=p.paused, set_at=p.set_at, set_by=p.set_by)
                for p in conn.execute(
                    select(s.chunk_pause_facts)
                    .where(s.chunk_pause_facts.c.chunk_id == chunk_id)
                    .order_by(s.chunk_pause_facts.c.id)
                ).all()
            ]
            pr_opened = [
                PrOpenedFact(
                    repo=p.repo, number=p.pr_number, url=p.pr_url, commit_hash=p.commit_hash, opened_at=p.opened_at
                )
                for p in conn.execute(
                    select(s.delivery_pr_opened).where(s.delivery_pr_opened.c.chunk_id == chunk_id)
                ).all()
            ]
            usage = [
                UsageFact(
                    node_id=u.node_id,
                    epoch=u.epoch,
                    kind=u.kind,
                    model=u.model,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_create_tokens=u.cache_create_tokens,
                    cost_usd=u.cost_usd,
                    recorded_at=u.recorded_at,
                )
                for u in conn.execute(select(s.usage_facts).where(s.usage_facts.c.chunk_id == chunk_id)).all()
            ]
            landed_repos = frozenset(
                r.repo
                for r in conn.execute(
                    select(s.delivery_repo_landed.c.repo).where(s.delivery_repo_landed.c.chunk_id == chunk_id)
                ).all()
            )
            bounces = [
                BounceFact(epoch=b.epoch, cause=b.cause, envelope=b.envelope, recorded_at=b.recorded_at)
                for b in conn.execute(select(s.chunk_bounces).where(s.chunk_bounces.c.chunk_id == chunk_id)).all()
            ]
            hub_node_polls = [
                HubNodePollFact(node_id=p.node_id, epoch=p.epoch, polled_at=p.polled_at)
                for p in conn.execute(select(s.hub_node_poll).where(s.hub_node_poll.c.chunk_id == chunk_id)).all()
            ]
            stopped_rows = conn.execute(
                select(s.chunk_stopped.c.stopped_at).where(s.chunk_stopped.c.chunk_id == chunk_id)
            ).all()
            completed_rows = conn.execute(
                select(s.chunk_completed.c.completed_at).where(s.chunk_completed.c.chunk_id == chunk_id)
            ).all()
            pr_closed_rows = conn.execute(
                select(s.delivery_pr_closed.c.closed_at).where(s.delivery_pr_closed.c.chunk_id == chunk_id)
            ).all()
            return ChunkFacts(
                minted=True,
                promoted=self._exists(conn, s.chunk_promoted, chunk_id),
                stopped=bool(stopped_rows),
                stopped_at=max((r.stopped_at for r in stopped_rows), default=None),
                operator_completed=bool(completed_rows),
                operator_completed_at=max((r.completed_at for r in completed_rows), default=None),
                delivery_landed=self._exists(conn, s.delivery_landed, chunk_id),
                landed_repos=landed_repos,
                pr_closed=bool(pr_closed_rows),
                # Newest across every repo's row (issue #175/#173) — `delivery_pr_closed`
                # has no chunk_id-unique constraint, unlike `delivery_pr_opened`.
                pr_closed_at=max((r.closed_at for r in pr_closed_rows), default=None),
                escalations=escalations,
                leases=leases,
                transitions=transitions,
                routes_created=routes_created,
                routes_released=routes_released,
                route_tokens_minted=route_tokens_minted,
                questions=questions,
                decisions=decisions,
                requeues=requeues,
                migrations=migrations,
                restarts=restarts,
                pr_opened=pr_opened,
                pauses=pauses,
                usage=usage,
                bounces=bounces,
                hub_node_polls=hub_node_polls,
            )

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        with self._engine.connect() as conn:
            return [
                ArtifactRow(
                    kind=ArtifactKind(a.kind),
                    name=a.name,
                    data=a.data,
                    repo=a.repo,
                    forge=a.forge,
                    artifact_id=a.artifact_id,
                    chunk_id=a.chunk_id,
                    node_id=a.node_id,
                    node_name=a.node_name,
                    epoch=a.epoch,
                )
                for a in conn.execute(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id)).all()
            ]

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route, or ``None`` if its newest release has caught up to it."""
        with self._engine.connect() as conn:
            return self._route_of_conn(conn, chunk_id)

    @staticmethod
    def _route_of_conn(conn: Connection, chunk_id: str) -> Route | None:
        """:meth:`route_of`'s query body, taking an already-open ``conn`` so a write
        transaction can resolve the same question inside its own commit (issue #118).
        Delegates the tie-break to :attr:`~blizzard.hub.domain.work.RouteHistory.newest`,
        so route liveness has exactly one answer at a same-instant tie (issue #41)."""
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

    def list_all(self) -> list[Chunk]:
        with self._engine.connect() as conn:
            ephemeral = self._ephemeral_ids(conn)
            rows = conn.execute(select(s.chunks).order_by(s.chunks.c.minted_at.desc())).all()
            # A grouped-away or deleted chunk is ephemeral: removed from every listing.
            return [self._chunk(conn, row) for row in rows if row.chunk_id not in ephemeral]

    def list_ready(self) -> list[Chunk]:
        return [c for c in self.list_all() if self._status(c.chunk_id) is ChunkStatus.READY]

    def list_not_ready(self) -> list[Chunk]:
        return [c for c in self.list_all() if self._status(c.chunk_id) is ChunkStatus.NOT_READY]

    def queue_positions(self) -> dict[str, float]:
        """The newest explicit queue position per chunk — the ordering the peek honours."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.queue_positions.c.chunk_id, s.queue_positions.c.position, s.queue_positions.c.id).order_by(
                    s.queue_positions.c.id
                )
            ).all()
        # id is monotonic per insert, so the last row seen for a chunk is its newest fact.
        return {r.chunk_id: float(r.position) for r in rows}

    def promoted_ats(self) -> dict[str, datetime]:
        """Each promoted chunk's ``chunk_promoted.promoted_at`` (issue #137)."""
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.chunk_promoted.c.chunk_id, s.chunk_promoted.c.promoted_at)).all()
        return {r.chunk_id: r.promoted_at for r in rows}

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        with self._engine.connect() as conn:
            ephemeral = self._ephemeral_ids(conn)
            chunk_ids = [
                p.chunk_id
                for p in conn.execute(
                    select(s.chunk_work_refs.c.chunk_id).where(
                        (s.chunk_work_refs.c.source == pointer.source) & (s.chunk_work_refs.c.ref == pointer.ref)
                    )
                ).all()
            ]
        for chunk_id in chunk_ids:
            if chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            if self._status(chunk_id) not in TERMINAL_STATUSES:
                return chunk_id
        return None

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        with self._engine.connect() as conn:
            ephemeral = self._ephemeral_ids(conn)
            rows = conn.execute(
                select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref)
            ).all()
        result: dict[WorkRef, ChunkStatus] = {}
        for row in rows:
            if row.chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            status = self._status(row.chunk_id)
            if status in TERMINAL_STATUSES:
                continue
            result[WorkRef(source=row.source, ref=row.ref)] = status
        return result

    def closable_work_refs(self) -> list[ClosableWorkRef]:
        with self._engine.connect() as conn:
            ephemeral = self._ephemeral_ids(conn)
            rows = conn.execute(
                select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref)
            ).all()
            terminal = {
                (r.chunk_id, r.source, r.ref)
                for r in conn.execute(
                    select(
                        s.work_item_closures.c.chunk_id, s.work_item_closures.c.source, s.work_item_closures.c.ref
                    ).where(
                        s.work_item_closures.c.outcome.in_(
                            [WorkItemCloseOutcome.CLOSED.value, WorkItemCloseOutcome.GONE.value]
                        )
                    )
                ).all()
            }
        result: list[ClosableWorkRef] = []
        for row in rows:
            if row.chunk_id in ephemeral:
                continue  # grouped away or deleted; the chunk owes nothing
            if (row.chunk_id, row.source, row.ref) in terminal:
                continue
            facts = self.load_facts(row.chunk_id)
            if facts is None or not (
                facts.has_landed_repos(self.load_artifacts(row.chunk_id)) or facts.operator_completed
            ):
                continue  # landed, or hand-completed by an operator (issue #294) — never chunk status
            result.append(ClosableWorkRef(chunk_id=row.chunk_id, ref=WorkRef(source=row.source, ref=row.ref)))
        return result

    def unmaterialized_proposals(self) -> list[WorkItemProposalRow]:
        with self._engine.begin() as conn:
            ephemeral = self._ephemeral_ids(conn)
            delivered = {
                r.chunk_id
                for r in conn.execute(
                    select(s.transitions.c.chunk_id).where(s.transitions.c.to_node_id == RESERVED_TERMINAL).distinct()
                ).all()
            }
            judged = {r.proposal_id for r in conn.execute(select(s.work_item_materializations.c.proposal_id)).all()}
            rows = conn.execute(select(s.work_item_proposals)).all()
        return [
            WorkItemProposalRow(
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
            for row in rows
            if row.chunk_id in delivered and row.chunk_id not in ephemeral and row.proposal_id not in judged
        ]

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.transitions.c.to_node_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            return row.to_node_id if row is not None else None

    def landed_repos(self, chunk_id: str) -> set[str]:
        with self._engine.connect() as conn:
            return {
                r.repo
                for r in conn.execute(
                    select(s.delivery_repo_landed.c.repo).where(s.delivery_repo_landed.c.chunk_id == chunk_id)
                ).all()
            }

    def runner_high_water(self, runner_id: str) -> int:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.runner_high_water.c.seq).where(s.runner_high_water.c.runner_id == runner_id)
            ).one_or_none()
            return int(row.seq) if row is not None else 0

    def get_question(self, question_id: str) -> QuestionRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(QUESTIONS.select.where(s.questions.c.question_id == question_id)).one_or_none()
            return QUESTIONS.of(row) if row is not None else None

    def list_open_questions(self) -> list[QuestionRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                QUESTIONS.select.where(
                    s.questions.c.question_id.not_in(select(s.question_answers.c.question_id))
                ).order_by(s.questions.c.asked_at)
            ).all()
            return [QUESTIONS.of(row) for row in rows]

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                QUESTIONS.select.where(s.questions.c.chunk_id == chunk_id).order_by(s.questions.c.asked_at)
            ).all()
            return [QUESTIONS.of(row) for row in rows]

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.decisions).where(s.decisions.c.decision_id == decision_id)).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.decisions).where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.decisions)
                .where(s.decisions.c.chunk_id == chunk_id)
                .order_by(s.decisions.c.submitted_at.desc())
            ).all()
            for row in rows:  # newest-first; the newest not-yet-transitioned decision is live
                decision = self._decision_row(conn, row)
                if not decision.transitioned:
                    return decision
            return None

    def list_open_decisions(self) -> list[DecisionRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.decisions).order_by(s.decisions.c.submitted_at)).all()
            decisions = [self._decision_row(conn, row) for row in rows]
            return [d for d in decisions if not d.resolved]

    def usage_since(self, since: datetime, *, until: datetime | None = None) -> list[UsageFact]:
        with self._engine.connect() as conn:
            query = select(s.usage_facts).where(s.usage_facts.c.recorded_at >= since)
            if until is not None:
                query = query.where(s.usage_facts.c.recorded_at < until)
            return [
                UsageFact(
                    node_id=u.node_id,
                    epoch=u.epoch,
                    kind=u.kind,
                    model=u.model,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_create_tokens=u.cache_create_tokens,
                    cost_usd=u.cost_usd,
                    recorded_at=u.recorded_at,
                )
                for u in conn.execute(query).all()
            ]

    def list_events(
        self,
        *,
        severity: str | None = None,
        runner_id: str | None = None,
        chunk_id: str | None = None,
        since: datetime | None = None,
        limit: int = DEFAULT_EVENT_LIST_LIMIT,
    ) -> list[EventRow]:
        with self._engine.connect() as conn:
            stmt = select(s.event_log)
            if severity is not None:
                stmt = stmt.where(s.event_log.c.severity == severity)
            if runner_id is not None:
                stmt = stmt.where(s.event_log.c.runner_id == runner_id)
            if chunk_id is not None:
                stmt = stmt.where(s.event_log.c.chunk_id == chunk_id)
            if since is not None:
                stmt = stmt.where(s.event_log.c.recorded_at >= since)
            stmt = stmt.order_by(s.event_log.c.recorded_at.desc(), s.event_log.c.id.desc()).limit(limit)
            return [
                EventRow(
                    id=row.id,
                    recorded_at=row.recorded_at,
                    severity=row.severity,
                    kind=row.kind,
                    runner_id=row.runner_id,
                    chunk_id=row.chunk_id,
                    lease_id=row.lease_id,
                    node_name=row.node_name,
                    message=row.message,
                    detail=json.loads(row.detail) if row.detail is not None else None,
                )
                for row in conn.execute(stmt).all()
            ]

    def list_open_escalations(self) -> list[EscalationOpen]:
        """Every open escalation fleet-wide, each decided by ``ChunkFacts.open_escalation``
        (#293) — the rule's one implementation, never a second derivation from raw rows."""
        newest_by_chunk = self._newest_escalation_per_chunk()
        return [
            EscalationOpen(chunk_id=chunk_id, recorded_at=open_.recorded_at, takeover_command=open_.takeover_command)
            for chunk_id in self._escalation_candidates(newest_by_chunk)
            if (facts := self.load_facts(chunk_id)) is not None and (open_ := facts.open_escalation()) is not None
        ]

    def _newest_escalation_per_chunk(self):  # type: ignore[no-untyped-def]
        """The newest ``escalations`` row per chunk. Low-volume, so a full scan is fine."""
        with self._engine.connect() as conn:
            newest_by_chunk = {}
            for e in conn.execute(select(s.escalations)).all():
                current = newest_by_chunk.get(e.chunk_id)
                if current is None or e.recorded_at > current.recorded_at:
                    newest_by_chunk[e.chunk_id] = e
            return newest_by_chunk

    def _escalation_candidates(self, newest_by_chunk) -> list[str]:  # type: ignore[no-untyped-def]
        """Chunks whose newest escalation *might* still be open — a **drop-only** narrowing that
        keeps :meth:`load_facts` off the obviously-closed ones. Sound because every arm below is
        one the authoritative rule also has, so a chunk dropped here is one ``open_escalation``
        would drop too; arms it lacks (completion) only leave extra work for the fold, never a
        wrong answer."""
        if not newest_by_chunk:
            return []
        chunk_ids = list(newest_by_chunk)
        with self._engine.connect() as conn:
            lease_rows = conn.execute(select(s.lease_facts).where(s.lease_facts.c.chunk_id.in_(chunk_ids))).all()
            requeue_rows = conn.execute(select(s.requeues).where(s.requeues.c.chunk_id.in_(chunk_ids))).all()
        superseding: dict[str, list[datetime]] = {}
        for lease in lease_rows:
            superseding.setdefault(lease.chunk_id, []).append(lease.minted_at)
        for rq in requeue_rows:
            superseding.setdefault(rq.chunk_id, []).append(rq.requeued_at)
        return [
            chunk_id
            for chunk_id, newest in newest_by_chunk.items()
            if not any(at > newest.recorded_at for at in superseding.get(chunk_id, ()))
        ]

    def activity_facts_since(self, since: datetime, *, limit: int) -> list[ActivityRow]:
        """See :meth:`IReadChunkRepository.activity_facts_since` — one bounded read per
        mapped ``ChunkChangeCause`` fact table, concatenated, unsorted across sources.
        Every per-chunk source joins ``chunks`` for its current ``graph_id``, except
        ``transitions``/``chunk_migrations``, which carry their own column."""
        with self._engine.connect() as conn:
            rows: list[ActivityRow] = []
            # Resolved once (issue #364): every fact-source block below excludes a
            # deleted chunk by referencing this same subquery, rather than repeating it.
            deleted = select(s.chunk_deleted.c.chunk_id)
            rows += self._bounded(
                conn,
                select(s.chunks.c.chunk_id, s.chunks.c.graph_id, s.chunks.c.minted_at).where(
                    s.chunks.c.chunk_id.not_in(deleted)
                ),
                ts_col=s.chunks.c.minted_at,
                pk_col=s.chunks.c.chunk_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunks:{r.chunk_id}",
                    at=r.minted_at,
                    chunk_id=r.chunk_id,
                    cause="minted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_promoted.c.id,
                    s.chunk_promoted.c.chunk_id,
                    s.chunk_promoted.c.promoted_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_promoted.join(s.chunks, s.chunks.c.chunk_id == s.chunk_promoted.c.chunk_id))
                .where(s.chunk_promoted.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_promoted.c.promoted_at,
                pk_col=s.chunk_promoted.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_promoted:{r.id}",
                    at=r.promoted_at,
                    chunk_id=r.chunk_id,
                    cause="promoted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_grouped.c.id, s.chunk_grouped.c.chunk_id, s.chunk_grouped.c.grouped_at, s.chunks.c.graph_id
                ).select_from(s.chunk_grouped.join(s.chunks, s.chunks.c.chunk_id == s.chunk_grouped.c.chunk_id)),
                ts_col=s.chunk_grouped.c.grouped_at,
                pk_col=s.chunk_grouped.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_grouped:{r.id}",
                    at=r.grouped_at,
                    chunk_id=r.chunk_id,
                    cause="grouped",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.route_created.c.route_id,
                    s.route_created.c.chunk_id,
                    s.route_created.c.runner_id,
                    s.route_created.c.created_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.route_created.join(s.chunks, s.chunks.c.chunk_id == s.route_created.c.chunk_id))
                .where(s.route_created.c.chunk_id.not_in(deleted)),
                ts_col=s.route_created.c.created_at,
                pk_col=s.route_created.c.route_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"route_created:{r.route_id}",
                    at=r.created_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="claimed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.transitions.c.transition_id,
                    s.transitions.c.chunk_id,
                    s.transitions.c.runner_id,
                    s.transitions.c.graph_id,
                    s.transitions.c.recorded_at,
                ).where(s.transitions.c.chunk_id.not_in(deleted)),
                ts_col=s.transitions.c.recorded_at,
                pk_col=s.transitions.c.transition_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"transitions:{r.transition_id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="hub-advanced" if r.runner_id == _HUB_RUNNER_ID else "node-completed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_migrations.c.migration_id,
                    s.chunk_migrations.c.chunk_id,
                    s.chunk_migrations.c.to_graph_id,
                    s.chunk_migrations.c.recorded_at,
                ).where(s.chunk_migrations.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_migrations.c.recorded_at,
                pk_col=s.chunk_migrations.c.migration_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_migrations:{r.migration_id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="migrated",
                    graph_id=r.to_graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_restarts.c.id,
                    s.chunk_restarts.c.chunk_id,
                    s.chunk_restarts.c.recorded_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_restarts.join(s.chunks, s.chunks.c.chunk_id == s.chunk_restarts.c.chunk_id))
                .where(s.chunk_restarts.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_restarts.c.recorded_at,
                pk_col=s.chunk_restarts.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_restarts:{r.id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="restarted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.decisions.c.decision_id, s.decisions.c.chunk_id, s.decisions.c.submitted_at, s.chunks.c.graph_id
                )
                .select_from(s.decisions.join(s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id))
                .where(s.decisions.c.chunk_id.not_in(deleted)),
                ts_col=s.decisions.c.submitted_at,
                pk_col=s.decisions.c.decision_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"decisions:{r.decision_id}",
                    at=r.submitted_at,
                    chunk_id=r.chunk_id,
                    cause="decision-submitted",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.decision_resolutions.c.decision_id,
                    s.decisions.c.chunk_id,
                    s.decision_resolutions.c.resolved_at,
                    s.chunks.c.graph_id,
                )
                .select_from(
                    s.decision_resolutions.join(
                        s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id
                    ).join(s.chunks, s.chunks.c.chunk_id == s.decisions.c.chunk_id)
                )
                .where(s.decisions.c.chunk_id.not_in(deleted)),
                ts_col=s.decision_resolutions.c.resolved_at,
                pk_col=s.decision_resolutions.c.decision_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"decision_resolutions:{r.decision_id}",
                    at=r.resolved_at,
                    chunk_id=r.chunk_id,
                    cause="decision-resolved",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.questions.c.question_id,
                    s.questions.c.chunk_id,
                    s.questions.c.runner_id,
                    s.questions.c.asked_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.questions.join(s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id))
                .where(s.questions.c.chunk_id.not_in(deleted)),
                ts_col=s.questions.c.asked_at,
                pk_col=s.questions.c.question_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"questions:{r.question_id}",
                    at=r.asked_at,
                    chunk_id=r.chunk_id,
                    runner_id=r.runner_id,
                    cause="question-asked",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.question_answers.c.question_id,
                    s.questions.c.chunk_id,
                    s.question_answers.c.answered_at,
                    s.chunks.c.graph_id,
                )
                .select_from(
                    s.question_answers.join(
                        s.questions, s.questions.c.question_id == s.question_answers.c.question_id
                    ).join(s.chunks, s.chunks.c.chunk_id == s.questions.c.chunk_id)
                )
                .where(s.questions.c.chunk_id.not_in(deleted)),
                ts_col=s.question_answers.c.answered_at,
                pk_col=s.question_answers.c.question_id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"question_answers:{r.question_id}",
                    at=r.answered_at,
                    chunk_id=r.chunk_id,
                    cause="question-answered",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(s.escalations.c.id, s.escalations.c.chunk_id, s.escalations.c.recorded_at, s.chunks.c.graph_id)
                .select_from(s.escalations.join(s.chunks, s.chunks.c.chunk_id == s.escalations.c.chunk_id))
                .where(s.escalations.c.chunk_id.not_in(deleted)),
                ts_col=s.escalations.c.recorded_at,
                pk_col=s.escalations.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"escalations:{r.id}",
                    at=r.recorded_at,
                    chunk_id=r.chunk_id,
                    cause="escalated",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(s.requeues.c.id, s.requeues.c.chunk_id, s.requeues.c.requeued_at, s.chunks.c.graph_id)
                .select_from(s.requeues.join(s.chunks, s.chunks.c.chunk_id == s.requeues.c.chunk_id))
                .where(s.requeues.c.chunk_id.not_in(deleted)),
                ts_col=s.requeues.c.requeued_at,
                pk_col=s.requeues.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"requeues:{r.id}",
                    at=r.requeued_at,
                    chunk_id=r.chunk_id,
                    cause="requeued",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.route_released.c.id,
                    s.route_released.c.chunk_id,
                    s.route_released.c.released_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.route_released.join(s.chunks, s.chunks.c.chunk_id == s.route_released.c.chunk_id))
                .where(s.route_released.c.chunk_id.not_in(deleted)),
                ts_col=s.route_released.c.released_at,
                pk_col=s.route_released.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"route_released:{r.id}",
                    at=r.released_at,
                    chunk_id=r.chunk_id,
                    cause="detached",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_pause_facts.c.id,
                    s.chunk_pause_facts.c.chunk_id,
                    s.chunk_pause_facts.c.paused,
                    s.chunk_pause_facts.c.set_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_pause_facts.join(s.chunks, s.chunks.c.chunk_id == s.chunk_pause_facts.c.chunk_id))
                .where(s.chunk_pause_facts.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_pause_facts.c.set_at,
                pk_col=s.chunk_pause_facts.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_pause_facts:{r.id}",
                    at=r.set_at,
                    chunk_id=r.chunk_id,
                    cause="paused" if r.paused else "resumed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_stopped.c.id, s.chunk_stopped.c.chunk_id, s.chunk_stopped.c.stopped_at, s.chunks.c.graph_id
                )
                .select_from(s.chunk_stopped.join(s.chunks, s.chunks.c.chunk_id == s.chunk_stopped.c.chunk_id))
                .where(s.chunk_stopped.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_stopped.c.stopped_at,
                pk_col=s.chunk_stopped.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_stopped:{r.id}",
                    at=r.stopped_at,
                    chunk_id=r.chunk_id,
                    cause="stopped",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_completed.c.id,
                    s.chunk_completed.c.chunk_id,
                    s.chunk_completed.c.completed_at,
                    s.chunks.c.graph_id,
                )
                .select_from(s.chunk_completed.join(s.chunks, s.chunks.c.chunk_id == s.chunk_completed.c.chunk_id))
                .where(s.chunk_completed.c.chunk_id.not_in(deleted)),
                ts_col=s.chunk_completed.c.completed_at,
                pk_col=s.chunk_completed.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_completed:{r.id}",
                    at=r.completed_at,
                    chunk_id=r.chunk_id,
                    cause="completed",
                    graph_id=r.graph_id,
                ),
            )
            rows += self._bounded(
                conn,
                select(
                    s.chunk_deleted.c.id,
                    s.chunk_deleted.c.chunk_id,
                    s.chunk_deleted.c.deleted_at,
                    s.chunk_deleted.c.deleted_by,
                    s.chunks.c.graph_id,
                ).select_from(s.chunk_deleted.join(s.chunks, s.chunks.c.chunk_id == s.chunk_deleted.c.chunk_id)),
                ts_col=s.chunk_deleted.c.deleted_at,
                pk_col=s.chunk_deleted.c.id,
                since=since,
                limit=limit,
                builder=lambda r: ActivityRow(
                    type="chunk-changed",
                    key=f"chunk_deleted:{r.id}",
                    at=r.deleted_at,
                    chunk_id=r.chunk_id,
                    cause="deleted",
                    graph_id=r.graph_id,
                    by=r.deleted_by,
                ),
            )
            return rows

    @staticmethod
    def _bounded(conn: Connection, stmt, *, ts_col, pk_col, since: datetime, limit: int, builder):  # type: ignore[no-untyped-def]
        """Run one source's own ``ORDER BY <ts> DESC, <pk> DESC LIMIT :limit`` bounded
        read and reshape each row via ``builder`` — the one piece every
        :meth:`activity_facts_since` source shares (issue #213, AC4: never a full-table
        scan)."""
        bounded_stmt = stmt.where(ts_col >= since).order_by(ts_col.desc(), pk_col.desc()).limit(limit)
        return [builder(row) for row in conn.execute(bounded_stmt).all()]

    # --- writes -------------------------------------------------------------

    def mint(self, chunk: Chunk) -> None:
        with self._engine.begin() as conn:
            insert_chunk_rows(conn, chunk)

    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        # Idempotent by chunk_id: a chunk already promoted keeps its first row, so a
        # double promote (board click, CLI retry) is a harmless no-op.
        with self._engine.begin() as conn:
            if self._exists(conn, s.chunk_promoted, chunk_id):
                return None
            result = conn.execute(insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else None

    def record_promote_with_tail_position(self, chunk_id: str, *, position: float, at: datetime) -> int | None:
        # One transaction: a crash between the two writes would otherwise let a stale
        # backlog position outrank the tail stamp on restart.
        with self._engine.begin() as conn:
            if self._exists(conn, s.chunk_promoted, chunk_id):
                return None
            result = conn.execute(insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))
            conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else None

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )

    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(s.runner_high_water.c.runner_id).where(s.runner_high_water.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(insert(s.runner_high_water).values(runner_id=runner_id, seq=seq, updated_at=at))
            else:
                conn.execute(
                    s.runner_high_water.update()
                    .where(s.runner_high_water.c.runner_id == runner_id)
                    .values(seq=seq, updated_at=at)
                )

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> str:
        """Record the route and mint its capability token's fact, one transaction (issue #84a).

        The token fact is a second row on the same shared per-chunk seq counter
        (:meth:`_next_route_seq`), allocated by its own call to the allocator, never a
        fixed +1. Returns the freshly-minted ``route_created.route_id`` (issue #213)."""
        route_id = Id.mint(_ROUTE_PREFIX, self._clock).value
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.route_created).values(
                    route_id=route_id,
                    chunk_id=route.chunk_id,
                    runner_id=route.runner_id,
                    workspace_id=route.workspace_id,
                    created_at=at,
                    seq=self._next_route_seq(conn, route.chunk_id),
                )
            )
            for env_id in route.environment_ids:
                conn.execute(insert(s.route_environments).values(route_id=route_id, environment_id=env_id))
            conn.execute(
                insert(s.route_token_minted).values(
                    chunk_id=route.chunk_id,
                    token_hash=token_hash,
                    seq=self._next_route_seq(conn, route.chunk_id),
                    minted_at=at,
                )
            )
            return route_id

    def record_route_released(self, chunk_id: str, *, at: datetime) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh ``route_token_minted`` fact — the re-key path (issue #84b).
        Same allocator as :meth:`record_route`'s own token fact, its own call rather
        than a fixed +1, so it stays correctly ordered against a concurrent
        create/release/re-key on this chunk."""
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.route_token_minted).values(
                    chunk_id=chunk_id,
                    token_hash=token_hash,
                    seq=self._next_route_seq(conn, chunk_id),
                    minted_at=at,
                )
            )

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
        decision_id: str | None = None,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.transitions).values(
                    transition_id=transition_id,
                    chunk_id=chunk_id,
                    graph_id=self._graph_id_of(conn, chunk_id),
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
                    insert(s.artifacts).values(
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
            self._insert_proposals(conn, proposals, at=at)

    @staticmethod
    def _insert_proposals(conn: Connection, proposals: list[WorkItemProposalRow], *, at: datetime) -> None:
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

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.delivery_repo_landed).values(
                    chunk_id=chunk_id, repo=repo, commit_hash=commit_hash, landed_at=at
                )
            )

    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(s.delivery_landed).values(chunk_id=chunk_id, landed_at=at))

    def record_work_item_closure(
        self, chunk_id: str, *, pointer: WorkRef, outcome: WorkItemCloseOutcome, reason: str | None, at: datetime
    ) -> bool:
        """Idempotent per ``(chunk_id, source, ref, outcome)`` — mirrors
        :meth:`record_hub_artifact`'s own already-existed-row contract."""
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.work_item_closures.c.id).where(
                    (s.work_item_closures.c.chunk_id == chunk_id)
                    & (s.work_item_closures.c.source == pointer.source)
                    & (s.work_item_closures.c.ref == pointer.ref)
                    & (s.work_item_closures.c.outcome == outcome.value)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                insert(s.work_item_closures).values(
                    chunk_id=chunk_id,
                    source=pointer.source,
                    ref=pointer.ref,
                    outcome=outcome.value,
                    reason=reason,
                    recorded_at=at,
                )
            )
            return True

    def record_work_item_materialization(
        self,
        proposal_id: str,
        *,
        outcome: WorkItemMaterializationOutcome,
        pointer: WorkRef | None,
        reason: str | None,
        at: datetime,
    ) -> bool:
        with self._engine.begin() as conn:
            return insert_materialization_row(
                conn, proposal_id=proposal_id, outcome=outcome, pointer=pointer, reason=reason, at=at
            )

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
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.delivery_landed.c.id).where(s.delivery_landed.c.chunk_id == chunk_id)
            ).first()
            if already is not None:
                return False
            conn.execute(
                insert(s.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(insert(s.delivery_landed).values(chunk_id=chunk_id, landed_at=at))
            conn.execute(
                insert(s.transitions).values(
                    transition_id=transition_id,
                    chunk_id=chunk_id,
                    graph_id=self._graph_id_of(conn, chunk_id),
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
                insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                )
            )
            return True

    def record_bounce(self, chunk_id: str, *, epoch: int, cause: str, envelope: str, at: datetime) -> bool:
        """Record one delivery kick-back **idempotently by** ``(chunk_id, epoch)`` (#64).

        A pre-check within the same transaction (mirroring :meth:`record_hub_step_transition`)
        rather than a DB constraint: a redelivery replay at the coordinator's same
        ``hub_epoch`` re-enters harmlessly. Returns True iff it wrote."""
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.chunk_bounces.c.id).where(
                    (s.chunk_bounces.c.chunk_id == chunk_id) & (s.chunk_bounces.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                insert(s.chunk_bounces).values(
                    chunk_id=chunk_id, epoch=epoch, cause=cause, envelope=envelope, recorded_at=at
                )
            )
            return True

    def record_bounce_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, at: datetime
    ) -> bool:
        """Escalate a bounce-capped chunk **atomically and idempotently** (#64).

        The hub lease and the escalation fact land in one transaction, guarded by the
        escalation's existence at this epoch. No transition: the chunk's held route and
        stuck node are untouched. Returns True iff it wrote."""
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.escalations.c.id).where(
                    (s.escalations.c.chunk_id == chunk_id) & (s.escalations.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                insert(s.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(
                insert(s.escalations).values(
                    chunk_id=chunk_id, epoch=epoch, takeover_command=takeover_command, recorded_at=at
                )
            )
            return True

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
    ) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.escalations).values(
                    chunk_id=chunk_id,
                    epoch=epoch,
                    takeover_command=takeover_command,
                    wrapped_takeover_command=wrapped_takeover_command,
                    decision_id=decision_id,
                    recorded_at=at,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_usage(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        runner_id: str,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        at: datetime,
    ) -> None:
        # Append-only, no epoch fence, no second dedup key — the caller's per-runner seq
        # high-water mark already guarantees at most one call per landed fact.
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.usage_facts).values(
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    runner_id=runner_id,
                    kind=kind,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_create_tokens=cache_create_tokens,
                    cost_usd=cost_usd,
                    recorded_at=at,
                )
            )

    def record_event(
        self,
        *,
        severity: str,
        kind: str,
        runner_id: str,
        chunk_id: str | None,
        lease_id: str | None,
        node_name: str | None,
        message: str,
        detail: dict | None,
        at: datetime,
    ) -> int:
        # Append-only operational fact (issue #125), no epoch fence. `detail` serializes
        # to JSON text here; `chunk_id` is None for a runner-scoped event.
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.event_log).values(
                    severity=severity,
                    kind=kind,
                    runner_id=runner_id,
                    chunk_id=chunk_id,
                    lease_id=lease_id,
                    node_name=node_name,
                    message=message,
                    detail=json.dumps(detail) if detail is not None else None,
                    recorded_at=at,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_question(
        self,
        *,
        question_id: str,
        chunk_id: str,
        node_id: str | None,
        session_id: str | None,
        runner_id: str,
        epoch: int,
        question: str,
        options: list[str],
        asked_at: datetime,
    ) -> None:
        # Idempotent by question_id: a store-and-forward replay re-lands the same row.
        with self._engine.begin() as conn:
            exists = conn.execute(
                select(s.questions.c.question_id).where(s.questions.c.question_id == question_id)
            ).first()
            if exists is not None:
                return
            conn.execute(
                insert(s.questions).values(
                    question_id=question_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    session_id=session_id,
                    runner_id=runner_id,
                    epoch=epoch,
                    question=question,
                    options=json.dumps(options),
                    asked_at=asked_at,
                )
            )

    def answer_question(self, question_id: str, *, answer: str, answered_by: str, at: datetime) -> AnswerOutcome:
        # First-write-wins CAS: the answer row's PK is the question id, so a racing
        # second insert raises IntegrityError and the loser reads back the winner.
        try:
            with self._engine.begin() as conn:
                conn.execute(
                    insert(s.question_answers).values(
                        question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
                    )
                )
            return AnswerOutcome(
                won=True, question_id=question_id, answer=answer, answered_by=answered_by, answered_at=at
            )
        except IntegrityError:
            with self._engine.connect() as conn:
                winner = conn.execute(
                    select(s.question_answers).where(s.question_answers.c.question_id == question_id)
                ).one()
            return AnswerOutcome(
                won=False,
                question_id=question_id,
                answer=winner.answer,
                answered_by=winner.answered_by,
                answered_at=winner.answered_at,
            )

    def record_answer_delivered(self, *, question_id: str, chunk_id: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.answer_deliveries).values(question_id=question_id, chunk_id=chunk_id, delivered_at=at)
            )

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
    ) -> None:
        payload = json.dumps([{"name": c.name, "description": c.description} for c in choices])
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.decisions).values(
                    decision_id=decision_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name=node_name,
                    epoch=epoch,
                    choices=payload,
                    submitted_at=at,
                )
            )
            for row in artifacts:
                conn.execute(
                    insert(s.artifacts).values(
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
            self._insert_proposals(conn, proposals, at=at)

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        with self._engine.begin() as conn:
            existing = conn.execute(
                select(s.decision_resolutions.c.decision_id).where(s.decision_resolutions.c.decision_id == decision_id)
            ).one_or_none()
            if existing is not None:
                return False  # first-write-wins: the loser is told who won
            conn.execute(
                insert(s.decision_resolutions).values(
                    decision_id=decision_id, choice=choice, resolved_by=resolved_by, resolved_at=at
                )
            )
            return True

    def record_requeue(self, chunk_id: str, *, at: datetime) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(insert(s.requeues).values(chunk_id=chunk_id, requeued_at=at))
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_restart(
        self,
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
        """Record the forced move and everything it consumes in **one** transaction (#370, #371),
        so a ``kill -9`` cannot leave the chunk moved while an ask still parks it or a graph it no
        longer runs still pins it; each answer keeps first-write-wins. The fence epoch is derived
        HERE rather than handed down — one above every prior attempt is a read-then-write only this
        transaction holds together (``bzh:epoch-fencing``)."""
        with self._engine.begin() as conn:
            epoch = self._latest_epoch(conn, chunk_id) + 1
            for question_id in answered_question_ids:
                already = conn.execute(
                    select(s.question_answers.c.question_id).where(s.question_answers.c.question_id == question_id)
                ).first()
                if already is None:
                    conn.execute(
                        insert(s.question_answers).values(
                            question_id=question_id, answer=answer, answered_by=by, answered_at=at
                        )
                    )
            from_graph_id = self._graph_id_of(conn, chunk_id)
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
                insert(s.chunk_restarts).values(
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
            insert(s.chunk_migrations).values(
                migration_id=Id.mint(MIGRATION_PREFIX, self._clock).value,
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

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a migration is already recorded for ``(chunk_id, from_node_id, epoch)``
        — the idempotency probe a re-applied cross-graph completion short-circuits on (#90).

        A migration writes no ``transitions`` row, so the transition-replay probe cannot
        see it; this is its counterpart, on :meth:`record_migration`'s natural key."""
        with self._engine.connect() as conn:
            return self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch)

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
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | None:
        """Record a cross-graph migration **atomically and idempotently** (#90).

        One transaction: the fact, the ``chunks.graph_id`` re-pin, the route release
        (unless ``release_route``, #111), this step's artifacts and proposals, and the
        intent clear (``clear_intent``, #124). Keyed ``(chunk_id, from_node_id, epoch)``."""
        with self._engine.begin() as conn:
            if self._migration_exists(conn, chunk_id, from_node_id=from_node_id, epoch=epoch):
                return None
            resolved_migration_id = (
                migration_id if migration_id is not None else Id.mint(MIGRATION_PREFIX, self._clock).value
            )
            conn.execute(
                insert(s.chunk_migrations).values(
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
                # Written INLINE (issue #144): a second transactional write would split the
                # durable fact from the pin it implies (`hub:migration-pin-consistent`).
                values["default_model"] = DEFAULT_MODEL.encode([model])
            if clear_intent:
                values["intended_migration"] = None
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(**values))
            if release_route:
                conn.execute(
                    insert(s.route_released).values(
                        chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                    )
                )
            for row in artifacts:
                conn.execute(
                    insert(s.artifacts).values(
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
            self._insert_proposals(conn, proposals, at=at)
            return resolved_migration_id

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

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append the moved chunk's new ready-queue position; order derives."""
        with self._engine.begin() as conn:
            conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))

    def record_backlog_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        with self._engine.begin() as conn:
            if self._exists(conn, s.chunk_promoted, chunk_id):
                return  # promoted since the caller resolved backlog candidates — not this write's chunk anymore
            conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))

    def add_work_refs(self, chunk_id: str, pointers: list[WorkRef], *, at: datetime) -> None:
        """Fold pointers into the survivor of a group, de-duped by (source, ref)."""
        with self._engine.begin() as conn:
            existing = {
                (p.source, p.ref)
                for p in conn.execute(
                    select(s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref).where(
                        s.chunk_work_refs.c.chunk_id == chunk_id
                    )
                ).all()
            }
            for pointer in pointers:
                if (pointer.source, pointer.ref) in existing:
                    continue
                conn.execute(
                    insert(s.chunk_work_refs).values(chunk_id=chunk_id, source=pointer.source, ref=pointer.ref)
                )
                existing.add((pointer.source, pointer.ref))

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> int:
        """Record ``chunk.grouped`` — the merged-away chunk is ephemeral now."""
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.chunk_grouped).values(chunk_id=chunk_id, grouped_into=grouped_into, grouped_at=at)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46)."""
        with self._engine.begin() as conn:
            result = conn.execute(
                insert(s.chunk_pause_facts).values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_stop(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.stopped`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all in **one** transaction (issue #118), so a
        ``kill -9`` cannot leave the chunk durably ``stopped`` with its route still live.
        The route check runs against this same connection (:meth:`_route_of_conn`), so
        there is no read-then-write race. The slot release is unconditional."""
        with self._engine.begin() as conn:
            result = conn.execute(insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=at, stopped_by=by))
            if self._route_of_conn(conn, chunk_id) is not None:
                conn.execute(
                    insert(s.route_released).values(
                        chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                    )
                )
            conn.execute(
                update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_completion(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.completed`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all in **one** transaction (issue #294), mirroring
        :meth:`record_stop`, so a ``kill -9`` cannot leave the chunk durably ``done`` with
        its route still live. The caller has already checked the chunk is not already
        ``done`` — this always writes a fresh row."""
        with self._engine.begin() as conn:
            result = conn.execute(insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=at, completed_by=by))
            if self._route_of_conn(conn, chunk_id) is not None:
                conn.execute(
                    insert(s.route_released).values(
                        chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                    )
                )
            conn.execute(
                update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph (issue #27, #120)."""
        with self._engine.begin() as conn:
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(graph_id=graph_id))

    def set_defaults(self, chunk_id: str, *, default_model: list[str], default_effort: str | None) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort (issues #27,
        #120, #144) — both in one write; see :meth:`IWriteChunkRepository.set_defaults`."""
        with self._engine.begin() as conn:
            conn.execute(
                update(s.chunks)
                .where(s.chunks.c.chunk_id == chunk_id)
                .values(
                    default_model=DEFAULT_MODEL.encode(default_model),
                    default_effort=default_effort,
                )
            )

    def set_intended_migration(self, chunk_id: str, *, intended: IntendedMigration | None) -> None:
        """Set, overwrite, or clear a chunk's standing migration intent (issue #124).

        A plain column overwrite, editable at any non-terminal status. The column
        carries no timestamp, so this write takes no ``at``."""
        with self._engine.begin() as conn:
            conn.execute(
                update(s.chunks)
                .where(s.chunks.c.chunk_id == chunk_id)
                .values(intended_migration=INTENDED_MIGRATION.encode(intended))
            )

    # --- The generic hub command node (#65) ---------------------------------

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution slot, **atomically** (crash-derivable
        fact, ``bzh:facts-not-status`` — never an in-process lock, so the invariant
        checker can assert at most one live slot and a ``kill -9`` mid-run leaves a
        stale, reclaimable row rather than a wedged fleet)."""
        with self._engine.begin() as conn:
            # Force sqlite's whole-database write lock BEFORE the read-then-insert below,
            # closing the race a bare SELECT leaves open (see ``_next_route_seq``).
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
                insert(s.hub_exec_slot).values(
                    slot_id=slot_id, holder_chunk_id=chunk_id, node_id=node_id, acquired_at=at, released_at=None
                )
            )
            return slot_id

    def release_hub_exec_slot(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )

    def count_live_hub_exec_slots(self) -> int:
        with self._engine.connect() as conn:
            return int(
                conn.execute(
                    select(func.count()).select_from(s.hub_exec_slot).where(s.hub_exec_slot.c.released_at.is_(None))
                ).scalar()
                or 0
            )

    def has_hub_artifact(self, chunk_id: str, *, node_id: str, epoch: int, name: str) -> bool:
        with self._engine.connect() as conn:
            return (
                conn.execute(
                    select(s.artifacts.c.artifact_id).where(
                        (s.artifacts.c.chunk_id == chunk_id)
                        & (s.artifacts.c.node_id == node_id)
                        & (s.artifacts.c.epoch == epoch)
                        & (s.artifacts.c.name == name)
                    )
                ).first()
                is not None
            )

    def record_hub_artifact(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str, at: datetime
    ) -> bool:
        """Append one hub-node progress artifact **outside** a transition (#65),
        idempotent per ``(chunk, node, name, epoch)`` — the ``produces:`` re-run skip's
        durable side, and the mid-run marker callback's write."""
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.artifacts.c.artifact_id).where(
                    (s.artifacts.c.chunk_id == chunk_id)
                    & (s.artifacts.c.node_id == node_id)
                    & (s.artifacts.c.epoch == epoch)
                    & (s.artifacts.c.name == name)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                insert(s.artifacts).values(
                    artifact_id=Id.mint(ARTIFACT_PREFIX, self._clock).value,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name=node_name,
                    epoch=epoch,
                    name=name,
                    kind=ArtifactKind.ASSET.value,
                    data=content,
                    repo=None,
                    forge=None,
                    produced_at=at,
                )
            )
            return True

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
        (#65) — :meth:`finalize_delivery`'s counterpart, generalized to any authored target.
        Two guards, both returning False: the transition's existence at ``(chunk_id,
        from_node_id, epoch)`` absorbs a redelivery replay, and the chunk's CURRENT epoch
        absorbs a restart that re-aimed it while the ``run:`` list ran (``bzh:epoch-fencing``)."""
        with self._engine.begin() as conn:
            already = conn.execute(
                select(s.transitions.c.transition_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            if self._latest_epoch(conn, chunk_id) >= epoch:
                return False
            conn.execute(
                insert(s.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(
                insert(s.transitions).values(
                    transition_id=transition_id,
                    chunk_id=chunk_id,
                    graph_id=self._graph_id_of(conn, chunk_id),
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
                    insert(s.artifacts).values(
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
                    insert(s.route_released).values(
                        chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                    )
                )
            return True

    def record_hub_node_poll(self, chunk_id: str, *, node_id: str, epoch: int, at: datetime) -> None:
        """Append one pending-poll-attempt fact (#66) — never a transition, no
        idempotency guard (an at-least-once poll attempt is harmless recorded twice)."""
        with self._engine.begin() as conn:
            conn.execute(insert(s.hub_node_poll).values(chunk_id=chunk_id, node_id=node_id, epoch=epoch, polled_at=at))

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _graph_id_of(conn: Connection, chunk_id: str) -> str:
        """The chunk's then-current graph pin — the provenance a transition is stamped
        with (issue #90). Read inside the writing transaction so a transition always
        carries the graph it actually moved within, even as a later migration re-pins
        ``chunks.graph_id`` in a subsequent write."""
        return conn.execute(select(s.chunks.c.graph_id).where(s.chunks.c.chunk_id == chunk_id)).scalar_one()

    @staticmethod
    def _ephemeral_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
        """Every chunk id gone from every read (issue #364) — the union of grouped-away
        and deleted chunks; widened here so all six consumers inherit the exclusion."""
        grouped = {r.chunk_id for r in conn.execute(select(s.chunk_grouped.c.chunk_id)).all()}
        deleted = {r.chunk_id for r in conn.execute(select(s.chunk_deleted.c.chunk_id)).all()}
        return grouped | deleted

    @staticmethod
    def _latest_epoch(conn: Connection, chunk_id: str) -> int:
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

    @staticmethod
    def _next_route_seq(conn: Connection, chunk_id: str) -> int:
        """One past the current max ``seq`` across ``route_created``, ``route_released``
        and ``route_token_minted`` for this chunk, so the triple is totally ordered even
        when timestamps tie. Read-then-insert, so concurrent callers are serialized by a
        no-op ``UPDATE`` on the chunk's own row — one portable write-lock statement for
        both dialects (``bzh:sql-portable``; ``tests/test_route_seq_concurrency.py``)."""
        conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(chunk_id=chunk_id))
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

    def _chunk(self, conn, row) -> Chunk:  # type: ignore[no-untyped-def]
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

    def _status(self, chunk_id: str) -> ChunkStatus:
        facts = self.load_facts(chunk_id)
        return facts.status() if facts is not None else ChunkStatus.READY

    @staticmethod
    def _resolved_ids(conn, decision_ids: list[str]) -> set[str]:  # type: ignore[no-untyped-def]
        """The decisions among ``decision_ids`` that carry a resolution row, or that an
        operator restart superseded (#370) — the two ways one stops deriving open."""
        if not decision_ids:
            return set()
        resolved = {
            r.decision_id
            for r in conn.execute(
                select(s.decision_resolutions.c.decision_id).where(
                    s.decision_resolutions.c.decision_id.in_(decision_ids)
                )
            ).all()
        }
        return resolved | {
            r.decision_id
            for r in conn.execute(
                select(s.chunk_restarts.c.decision_id).where(s.chunk_restarts.c.decision_id.in_(decision_ids))
            ).all()
        }

    def _decision_row(self, conn, row) -> DecisionRow:  # type: ignore[no-untyped-def]
        resolution = conn.execute(
            select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id == row.decision_id)
        ).one_or_none()
        # Closed by whichever fact carries this decision_id: the resolving transition, the
        # migration (#90), the unresolvable-target escalation (#110), or the restart (#370).
        transitioned = (
            conn.execute(
                select(s.transitions.c.transition_id).where(s.transitions.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.chunk_migrations.c.migration_id)
                .where(s.chunk_migrations.c.decision_id == row.decision_id)
                .limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.escalations.c.id).where(s.escalations.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.chunk_restarts.c.id).where(s.chunk_restarts.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
        )
        choices = [DecisionChoice(name=c["name"], description=c["description"]) for c in json.loads(row.choices)]
        return DecisionRow(
            decision_id=row.decision_id,
            chunk_id=row.chunk_id,
            node_id=row.node_id,
            node_name=row.node_name,
            epoch=row.epoch,
            choices=choices,
            submitted_at=row.submitted_at,
            resolved_choice=resolution.choice if resolution is not None else None,
            resolved_by=resolution.resolved_by if resolution is not None else None,
            resolved_at=resolution.resolved_at if resolution is not None else None,
            transitioned=transitioned,
        )

    @staticmethod
    def _exists(conn, table, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
        return conn.execute(select(table.c.chunk_id).where(table.c.chunk_id == chunk_id).limit(1)).first() is not None


def _conforms_chunk_store(x: ChunkStore) -> IWriteChunkRepository:
    return x
