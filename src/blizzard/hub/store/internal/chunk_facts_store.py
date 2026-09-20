"""SQLAlchemy adapter for the chunk facts seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every read below folds already-recorded rows; nothing derives
a status column. Read-only (D2, ``blizzard-context/architecture/repository-access.md``):
``load_facts``/``load_all_facts``/``load_facts_for``/``load_all_statuses`` each project
the union of every other seam's own writes, so this adapter has no write half.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.work import (
    BounceFact,
    ChunkFacts,
    DecisionFact,
    EscalationFact,
    HubNodePollFact,
    LeaseFact,
    MigrationFact,
    MigrationSource,
    PauseFact,
    PrOpenedFact,
    QuestionFact,
    RequeueFact,
    RestartFact,
    RouteCreatedFact,
    RouteReleasedFact,
    RouteTokenMintedFact,
    TransitionFact,
    UsageFact,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.batching import id_batches
from blizzard.hub.store.internal.chunk_rows import graph_id_of_batch

#: Every fact family `ChunkFacts` carries — the default selection for `load_facts` and kin.
_ALL_FAMILIES: frozenset[str] = frozenset(
    {
        "promoted",
        "stopped",
        "operator_completed",
        "delivery_landed",
        "landed_repos",
        "pr_closed",
        "escalations",
        "leases",
        "transitions",
        "routes_created",
        "routes_released",
        "route_tokens_minted",
        "questions",
        "decisions",
        "requeues",
        "migrations",
        "restarts",
        "pr_opened",
        "pauses",
        "usage",
        "bounces",
        "hub_node_polls",
    }
)

#: Exactly the families `ChunkFacts.status()` reaches — pinned by a mechanical equivalence test.
_STATUS_FAMILIES: frozenset[str] = frozenset(
    {
        "promoted",
        "stopped",
        "operator_completed",
        "pr_closed",
        "escalations",
        "leases",
        "transitions",
        "routes_created",
        "routes_released",
        "questions",
        "decisions",
        "requeues",
        "migrations",
        "restarts",
        "pauses",
    }
)


#: The families a :class:`~blizzard.wire.chunk.ChunkStatusView` reaches — :attr:`_STATUS_FAMILIES`
#: plus ``usage`` (for :meth:`~blizzard.hub.domain.work.ChunkFacts.usage_total`, the one
#: reach ``status()`` itself doesn't make). :meth:`ChunkFactsStore.status_facts_for`'s narrowing.
_TICK_STATUS_FAMILIES: frozenset[str] = _STATUS_FAMILIES | frozenset({"usage"})


def _rows(conn, table, batch: Sequence[str] | None, *columns):  # type: ignore[no-untyped-def]
    stmt = select(*columns) if columns else select(table)
    if batch is not None:
        stmt = stmt.where(table.c.chunk_id.in_(batch))
    return conn.execute(stmt).all()


class ChunkFactsStore:
    """Read-only chunk-facts adapter — the fleet's fact-derivation projection."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        with self._store.read("load_facts") as conn:
            result = self._load(conn, [chunk_id])
        return result.get(chunk_id)

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        """See :meth:`~blizzard.hub.domain.chunks.facts.IReadChunkFactsRepository.load_all_facts`
        (issue #374) — one bounded query per fact table across the whole store, grouped by
        chunk id in Python, rather than :meth:`load_facts`'s per-chunk fan-out.
        ``activity_facts_since`` is this shape's precedent. Every family reproduces
        :meth:`load_facts`'s row construction verbatim; only ``chunk_pause_facts`` is read
        in an explicit order, since :meth:`ChunkFacts.open_pause` indexes its list's tail."""
        with self._store.read("load_all_facts") as conn:
            return self._load(conn, None)

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts`'s batched sibling — every id's
        :class:`ChunkFacts` (`bzh:bulk-reconstitution`). An id that doesn't exist or is
        ephemeral is silently dropped, the same as :meth:`load_facts` returning ``None``
        for it."""
        if not chunk_ids:
            return {}
        with self._store.read("load_facts_for") as conn:
            return self._load(conn, chunk_ids)

    def status_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts_for`'s status-only sibling — every id's :class:`ChunkFacts`, keyed
        by chunk id, reading only :attr:`_STATUS_FAMILIES` plus ``usage`` — the families
        behind :meth:`~blizzard.hub.domain.work.ChunkFacts.status`, ``open_pause``,
        ``latest_epoch``, ``restarts``, and ``usage_total`` — rather than every family
        :meth:`load_facts_for` loads. An id that doesn't exist or is ephemeral is silently
        dropped, the same as :meth:`load_facts_for`."""
        if not chunk_ids:
            return {}
        with self._store.read("status_facts_for") as conn:
            return self._load(conn, chunk_ids, families=_TICK_STATUS_FAMILIES)

    def load_all_statuses(self) -> dict[str, ChunkStatus]:
        """Every non-ephemeral chunk's derived :class:`ChunkStatus`, keyed by chunk id —
        :meth:`load_all_facts`'s status-only projection, reading only the fact families
        :meth:`ChunkFacts.status` actually reaches rather than every family
        :meth:`load_all_facts` loads. Status derivation itself stays in ``domain/work.py``;
        this only narrows which rows get read."""
        with self._store.read("load_all_statuses") as conn:
            facts_by_id = self._load(conn, None, families=_STATUS_FAMILIES)
        return {chunk_id: facts.status() for chunk_id, facts in facts_by_id.items()}

    def _load(
        self,
        conn,  # type: ignore[no-untyped-def]
        chunk_ids: Sequence[str] | None,
        *,
        families: frozenset[str] | None = None,
    ) -> dict[str, ChunkFacts]:
        selected = families if families is not None else _ALL_FAMILIES
        if chunk_ids is None:
            return self._load_batch(conn, None, selected)
        result: dict[str, ChunkFacts] = {}
        for batch in id_batches(chunk_ids):
            result.update(self._load_batch(conn, batch, selected))
        return result

    def _load_batch(self, conn, batch: Sequence[str] | None, families: frozenset[str]) -> dict[str, ChunkFacts]:  # type: ignore[no-untyped-def]
        graph_id_of = graph_id_of_batch(conn, batch)
        if not graph_id_of:
            return {}

        transition_rows = _rows(conn, s.transitions, batch) if "transitions" in families else []
        migration_rows = _rows(conn, s.chunk_migrations, batch) if "migrations" in families else []
        restart_rows = _rows(conn, s.chunk_restarts, batch) if "restarts" in families else []

        # The executor map spans every graph any fetched movement fact touched, keyed by
        # (graph_id, node_id) so a node id shared by two graphs never collides (issues
        # #90, #111, #370) — built only when a movement family was actually requested.
        executors: dict[tuple[str, str], Executor] = {}
        if {"transitions", "migrations", "restarts"} & families:
            graph_ids = (
                set(graph_id_of.values())
                | {t.graph_id for t in transition_rows}
                | {m.to_graph_id for m in migration_rows}
                | {r.graph_id for r in restart_rows}
                | {r.from_graph_id for r in restart_rows if r.from_graph_id is not None}
            )
            executors = {
                (r.graph_id, r.node_id): Executor(r.executor)
                for r in conn.execute(
                    select(s.graph_nodes.c.graph_id, s.graph_nodes.c.node_id, s.graph_nodes.c.executor).where(
                        s.graph_nodes.c.graph_id.in_(graph_ids)
                    )
                ).all()
            }

        transitions: dict[str, list[TransitionFact]] = defaultdict(list)
        for t in transition_rows:
            transitions[t.chunk_id].append(
                TransitionFact(
                    to_node_id=t.to_node_id,
                    to_node_executor=executors.get((t.graph_id, t.to_node_id), Executor.RUNNER),
                    epoch=t.epoch,
                    recorded_at=t.recorded_at,
                    from_node_id=t.from_node_id,
                    choice_name=t.choice_name,
                    graph_id=t.graph_id,
                )
            )

        leases: dict[str, list[LeaseFact]] = defaultdict(list)
        if "leases" in families:
            for lease in _rows(conn, s.lease_facts, batch):
                leases[lease.chunk_id].append(LeaseFact(epoch=lease.epoch, minted_at=lease.minted_at))

        escalations: dict[str, list[EscalationFact]] = defaultdict(list)
        if "escalations" in families:
            for e in _rows(conn, s.escalations, batch):
                escalations[e.chunk_id].append(
                    EscalationFact(
                        epoch=e.epoch,
                        recorded_at=e.recorded_at,
                        takeover_command=e.takeover_command or "",
                        wrapped_takeover_command=e.wrapped_takeover_command or "",
                    )
                )

        routes_created: dict[str, list[RouteCreatedFact]] = defaultdict(list)
        if "routes_created" in families:
            for r in _rows(conn, s.route_created, batch):
                routes_created[r.chunk_id].append(RouteCreatedFact(created_at=r.created_at, seq=r.seq))

        routes_released: dict[str, list[RouteReleasedFact]] = defaultdict(list)
        if "routes_released" in families:
            for r in _rows(conn, s.route_released, batch):
                routes_released[r.chunk_id].append(RouteReleasedFact(released_at=r.released_at, seq=r.seq))

        route_tokens_minted: dict[str, list[RouteTokenMintedFact]] = defaultdict(list)
        if "route_tokens_minted" in families:
            for t in _rows(conn, s.route_token_minted, batch):
                route_tokens_minted[t.chunk_id].append(
                    RouteTokenMintedFact(token_hash=t.token_hash, minted_at=t.minted_at, seq=t.seq)
                )

        questions: dict[str, list[QuestionFact]] = defaultdict(list)
        if "questions" in families:
            # Scoped to the requested batch's own questions (blizzard#421); the whole-fleet
            # call leaves this unfiltered, building the answered set for every chunk at once.
            answered_stmt = select(s.question_answers.c.question_id).join(
                s.questions, s.questions.c.question_id == s.question_answers.c.question_id
            )
            if batch is not None:
                answered_stmt = answered_stmt.where(s.questions.c.chunk_id.in_(batch))
            answered = {a.question_id for a in conn.execute(answered_stmt).all()}
            for q in _rows(conn, s.questions, batch):
                questions[q.chunk_id].append(
                    QuestionFact(question_id=q.question_id, asked_at=q.asked_at, answered=q.question_id in answered)
                )

        decisions: dict[str, list[DecisionFact]] = defaultdict(list)
        if "decisions" in families:
            decision_rows = _rows(conn, s.decisions, batch)
            # A decision can also resolve via an operator restart superseding it — only signaled
            # when `restarts` is itself among the requested families, true of every current caller.
            if batch is None:
                resolved_ids = {
                    r.decision_id for r in conn.execute(select(s.decision_resolutions.c.decision_id)).all()
                } | {r.decision_id for r in restart_rows if r.decision_id is not None}
            else:
                decision_ids = [d.decision_id for d in decision_rows]
                resolved_ids = self._resolved_ids(conn, decision_ids) | {
                    r.decision_id for r in restart_rows if r.decision_id is not None
                }
            for d in decision_rows:
                decisions[d.chunk_id].append(
                    DecisionFact(
                        decision_id=d.decision_id, submitted_at=d.submitted_at, resolved=d.decision_id in resolved_ids
                    )
                )

        requeues: dict[str, list[RequeueFact]] = defaultdict(list)
        if "requeues" in families:
            for r in _rows(conn, s.requeues, batch):
                requeues[r.chunk_id].append(RequeueFact(requeued_at=r.requeued_at))

        migrations: dict[str, list[MigrationFact]] = defaultdict(list)
        for m in migration_rows:
            migrations[m.chunk_id].append(
                MigrationFact(
                    from_node_id=m.from_node_id,
                    from_graph_id=m.from_graph_id,
                    to_graph_id=m.to_graph_id,
                    landed_node_id=m.landed_node_id,
                    choice_name=m.choice_name,
                    model=m.model_after,
                    epoch=m.epoch,
                    recorded_at=m.recorded_at,
                    landed_node_executor=executors.get((m.to_graph_id, m.landed_node_id), Executor.RUNNER),
                    # Null for a row predating the discriminator — read as unrecorded, never
                    # guessed at (issue #164).
                    source=MigrationSource(m.source) if m.source else None,
                )
            )

        restarts: dict[str, list[RestartFact]] = defaultdict(list)
        for r in restart_rows:
            restarts[r.chunk_id].append(
                RestartFact(
                    to_node_id=r.to_node_id,
                    from_node_id=r.from_node_id,
                    graph_id=r.graph_id,
                    epoch=r.epoch,
                    recorded_at=r.recorded_at,
                    from_graph_id=r.from_graph_id,
                    to_node_executor=executors.get((r.graph_id, r.to_node_id), Executor.RUNNER),
                    restarted_by=r.restarted_by,
                    decision_id=r.decision_id,
                )
            )

        pauses: dict[str, list[PauseFact]] = defaultdict(list)
        if "pauses" in families:
            stmt = select(s.chunk_pause_facts).order_by(s.chunk_pause_facts.c.id)
            if batch is not None:
                stmt = stmt.where(s.chunk_pause_facts.c.chunk_id.in_(batch))
            for p in conn.execute(stmt).all():
                pauses[p.chunk_id].append(PauseFact(paused=p.paused, set_at=p.set_at, set_by=p.set_by))

        pr_opened: dict[str, list[PrOpenedFact]] = defaultdict(list)
        if "pr_opened" in families:
            for p in _rows(conn, s.delivery_pr_opened, batch):
                pr_opened[p.chunk_id].append(
                    PrOpenedFact(
                        repo=p.repo, number=p.pr_number, url=p.pr_url, commit_hash=p.commit_hash, opened_at=p.opened_at
                    )
                )

        usage: dict[str, list[UsageFact]] = defaultdict(list)
        if "usage" in families:
            for u in _rows(conn, s.usage_facts, batch):
                usage[u.chunk_id].append(
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
                        harness_id=u.harness_id,
                        harness_version=u.harness_version,
                    )
                )

        landed_repos: dict[str, set[str]] = defaultdict(set)
        if "landed_repos" in families:
            for r in _rows(conn, s.delivery_repo_landed, batch):
                landed_repos[r.chunk_id].add(r.repo)

        bounces: dict[str, list[BounceFact]] = defaultdict(list)
        if "bounces" in families:
            for b in _rows(conn, s.chunk_bounces, batch):
                bounces[b.chunk_id].append(
                    BounceFact(epoch=b.epoch, cause=b.cause, envelope=b.envelope, recorded_at=b.recorded_at)
                )

        hub_node_polls: dict[str, list[HubNodePollFact]] = defaultdict(list)
        if "hub_node_polls" in families:
            for p in _rows(conn, s.hub_node_poll, batch):
                hub_node_polls[p.chunk_id].append(
                    HubNodePollFact(node_id=p.node_id, epoch=p.epoch, polled_at=p.polled_at)
                )

        stopped_ats: dict[str, list[datetime]] = defaultdict(list)
        if "stopped" in families:
            for r in _rows(conn, s.chunk_stopped, batch, s.chunk_stopped.c.chunk_id, s.chunk_stopped.c.stopped_at):
                stopped_ats[r.chunk_id].append(r.stopped_at)

        completed_ats: dict[str, list[datetime]] = defaultdict(list)
        if "operator_completed" in families:
            for r in _rows(
                conn, s.chunk_completed, batch, s.chunk_completed.c.chunk_id, s.chunk_completed.c.completed_at
            ):
                completed_ats[r.chunk_id].append(r.completed_at)

        pr_closed_ats: dict[str, list[datetime]] = defaultdict(list)
        if "pr_closed" in families:
            for r in _rows(
                conn, s.delivery_pr_closed, batch, s.delivery_pr_closed.c.chunk_id, s.delivery_pr_closed.c.closed_at
            ):
                pr_closed_ats[r.chunk_id].append(r.closed_at)

        promoted_ids: set[str] = set()
        if "promoted" in families:
            promoted_ids = {r.chunk_id for r in _rows(conn, s.chunk_promoted, batch, s.chunk_promoted.c.chunk_id)}

        delivery_landed_ids: set[str] = set()
        if "delivery_landed" in families:
            delivery_landed_ids = {
                r.chunk_id for r in _rows(conn, s.delivery_landed, batch, s.delivery_landed.c.chunk_id)
            }

        return {
            chunk_id: ChunkFacts(
                minted=True,
                promoted=chunk_id in promoted_ids,
                stopped=bool(stopped_ats[chunk_id]),
                stopped_at=max(stopped_ats[chunk_id], default=None),
                operator_completed=bool(completed_ats[chunk_id]),
                operator_completed_at=max(completed_ats[chunk_id], default=None),
                delivery_landed=chunk_id in delivery_landed_ids,
                landed_repos=frozenset(landed_repos[chunk_id]),
                pr_closed=bool(pr_closed_ats[chunk_id]),
                pr_closed_at=max(pr_closed_ats[chunk_id], default=None),
                escalations=escalations[chunk_id],
                leases=leases[chunk_id],
                transitions=transitions[chunk_id],
                routes_created=routes_created[chunk_id],
                routes_released=routes_released[chunk_id],
                route_tokens_minted=route_tokens_minted[chunk_id],
                questions=questions[chunk_id],
                decisions=decisions[chunk_id],
                requeues=requeues[chunk_id],
                migrations=migrations[chunk_id],
                restarts=restarts[chunk_id],
                pr_opened=pr_opened[chunk_id],
                pauses=pauses[chunk_id],
                usage=usage[chunk_id],
                bounces=bounces[chunk_id],
                hub_node_polls=hub_node_polls[chunk_id],
            )
            for chunk_id in graph_id_of
        }

    @staticmethod
    def _resolved_ids(conn, decision_ids: list[str]) -> set[str]:  # type: ignore[no-untyped-def]
        """The decisions among ``decision_ids`` that carry a resolution row, or that an
        operator restart superseded (#370) — the two ways one stops deriving open.
        ``decision_ids`` can outgrow one chunk-id batch's worth of decisions, so each
        ``IN`` runs through :func:`id_batches` rather than a single unbounded clause."""
        resolved: set[str] = set()
        for batch in id_batches(decision_ids):
            resolved |= {
                r.decision_id
                for r in conn.execute(
                    select(s.decision_resolutions.c.decision_id).where(s.decision_resolutions.c.decision_id.in_(batch))
                ).all()
            }
        for batch in id_batches(decision_ids):
            resolved |= {
                r.decision_id
                for r in conn.execute(
                    select(s.chunk_restarts.c.decision_id).where(s.chunk_restarts.c.decision_id.in_(batch))
                ).all()
            }
        return resolved


def _conforms_facts(x: ChunkFactsStore) -> IReadChunkFactsRepository:
    return x
