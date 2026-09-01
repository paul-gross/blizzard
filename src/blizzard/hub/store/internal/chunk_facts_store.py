"""SQLAlchemy adapter for the chunk facts seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every read below folds already-recorded rows; nothing derives
a status column. Read-only (D2, ``blizzard-context/architecture/repository-access.md``):
``load_facts``/``load_all_facts`` project the union of every other seam's own writes, so
this adapter has no write half.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

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
from blizzard.hub.store.internal.chunk_rows import ephemeral_ids


class ChunkFactsStore:
    """Read-only chunk-facts adapter — the fleet's fact-derivation projection."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        with self._store.read("load_facts") as conn:
            chunk = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if chunk is None or chunk_id in ephemeral_ids(conn):
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
            # Scoped to this chunk's own questions (blizzard#421); load_all_facts's fleet-wide
            # counterpart is deliberately unfiltered — it builds the set for every chunk at once.
            answered = {
                a.question_id
                for a in conn.execute(
                    select(s.question_answers.c.question_id)
                    .join(s.questions, s.questions.c.question_id == s.question_answers.c.question_id)
                    .where(s.questions.c.chunk_id == chunk_id)
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

    def load_all_facts(self) -> dict[str, ChunkFacts]:
        """See :meth:`~blizzard.hub.domain.chunks.facts.IReadChunkFactsRepository.load_all_facts`
        (issue #374) — one bounded query per fact table across the whole store, grouped by
        chunk id in Python, rather than :meth:`load_facts`'s per-chunk fan-out.
        ``activity_facts_since`` is this shape's precedent. Every family reproduces
        :meth:`load_facts`'s row construction verbatim; only ``chunk_pause_facts`` is read
        in an explicit order, since :meth:`ChunkFacts.open_pause` indexes its list's tail."""
        with self._store.read("load_all_facts") as conn:
            ephemeral = ephemeral_ids(conn)
            graph_id_of = {
                r.chunk_id: r.graph_id
                for r in conn.execute(select(s.chunks.c.chunk_id, s.chunks.c.graph_id)).all()
                if r.chunk_id not in ephemeral
            }
            if not graph_id_of:
                return {}

            transition_rows = conn.execute(select(s.transitions)).all()
            migration_rows = conn.execute(select(s.chunk_migrations)).all()
            restart_rows = conn.execute(select(s.chunk_restarts)).all()

            # The executor map spans every graph any chunk's movement facts touched, keyed
            # by (graph_id, node_id) so a node id shared by two graphs never collides
            # (issues #90, #111, #370) — the bulk counterpart of load_facts's per-chunk set.
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
            for lease in conn.execute(select(s.lease_facts)).all():
                leases[lease.chunk_id].append(LeaseFact(epoch=lease.epoch, minted_at=lease.minted_at))

            escalations: dict[str, list[EscalationFact]] = defaultdict(list)
            for e in conn.execute(select(s.escalations)).all():
                escalations[e.chunk_id].append(
                    EscalationFact(
                        epoch=e.epoch,
                        recorded_at=e.recorded_at,
                        takeover_command=e.takeover_command or "",
                        wrapped_takeover_command=e.wrapped_takeover_command or "",
                    )
                )

            routes_created: dict[str, list[RouteCreatedFact]] = defaultdict(list)
            for r in conn.execute(select(s.route_created)).all():
                routes_created[r.chunk_id].append(RouteCreatedFact(created_at=r.created_at, seq=r.seq))

            routes_released: dict[str, list[RouteReleasedFact]] = defaultdict(list)
            for r in conn.execute(select(s.route_released)).all():
                routes_released[r.chunk_id].append(RouteReleasedFact(released_at=r.released_at, seq=r.seq))

            route_tokens_minted: dict[str, list[RouteTokenMintedFact]] = defaultdict(list)
            for t in conn.execute(select(s.route_token_minted)).all():
                route_tokens_minted[t.chunk_id].append(
                    RouteTokenMintedFact(token_hash=t.token_hash, minted_at=t.minted_at, seq=t.seq)
                )

            answered = {
                a.question_id
                for a in conn.execute(
                    select(s.question_answers.c.question_id).join(
                        s.questions, s.questions.c.question_id == s.question_answers.c.question_id
                    )
                ).all()
            }
            questions: dict[str, list[QuestionFact]] = defaultdict(list)
            for q in conn.execute(select(s.questions)).all():
                questions[q.chunk_id].append(
                    QuestionFact(question_id=q.question_id, asked_at=q.asked_at, answered=q.question_id in answered)
                )

            decision_rows = conn.execute(select(s.decisions)).all()
            resolved_ids = {r.decision_id for r in conn.execute(select(s.decision_resolutions.c.decision_id)).all()} | {
                r.decision_id for r in restart_rows if r.decision_id is not None
            }
            decisions: dict[str, list[DecisionFact]] = defaultdict(list)
            for d in decision_rows:
                decisions[d.chunk_id].append(
                    DecisionFact(
                        decision_id=d.decision_id, submitted_at=d.submitted_at, resolved=d.decision_id in resolved_ids
                    )
                )

            requeues: dict[str, list[RequeueFact]] = defaultdict(list)
            for r in conn.execute(select(s.requeues)).all():
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
            for p in conn.execute(select(s.chunk_pause_facts).order_by(s.chunk_pause_facts.c.id)).all():
                pauses[p.chunk_id].append(PauseFact(paused=p.paused, set_at=p.set_at, set_by=p.set_by))

            pr_opened: dict[str, list[PrOpenedFact]] = defaultdict(list)
            for p in conn.execute(select(s.delivery_pr_opened)).all():
                pr_opened[p.chunk_id].append(
                    PrOpenedFact(
                        repo=p.repo, number=p.pr_number, url=p.pr_url, commit_hash=p.commit_hash, opened_at=p.opened_at
                    )
                )

            usage: dict[str, list[UsageFact]] = defaultdict(list)
            for u in conn.execute(select(s.usage_facts)).all():
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
                    )
                )

            landed_repos: dict[str, set[str]] = defaultdict(set)
            for r in conn.execute(select(s.delivery_repo_landed)).all():
                landed_repos[r.chunk_id].add(r.repo)

            bounces: dict[str, list[BounceFact]] = defaultdict(list)
            for b in conn.execute(select(s.chunk_bounces)).all():
                bounces[b.chunk_id].append(
                    BounceFact(epoch=b.epoch, cause=b.cause, envelope=b.envelope, recorded_at=b.recorded_at)
                )

            hub_node_polls: dict[str, list[HubNodePollFact]] = defaultdict(list)
            for p in conn.execute(select(s.hub_node_poll)).all():
                hub_node_polls[p.chunk_id].append(
                    HubNodePollFact(node_id=p.node_id, epoch=p.epoch, polled_at=p.polled_at)
                )

            stopped_ats: dict[str, list[datetime]] = defaultdict(list)
            for r in conn.execute(select(s.chunk_stopped.c.chunk_id, s.chunk_stopped.c.stopped_at)).all():
                stopped_ats[r.chunk_id].append(r.stopped_at)

            completed_ats: dict[str, list[datetime]] = defaultdict(list)
            for r in conn.execute(select(s.chunk_completed.c.chunk_id, s.chunk_completed.c.completed_at)).all():
                completed_ats[r.chunk_id].append(r.completed_at)

            pr_closed_ats: dict[str, list[datetime]] = defaultdict(list)
            for r in conn.execute(select(s.delivery_pr_closed.c.chunk_id, s.delivery_pr_closed.c.closed_at)).all():
                pr_closed_ats[r.chunk_id].append(r.closed_at)

            promoted_ids = {r.chunk_id for r in conn.execute(select(s.chunk_promoted.c.chunk_id)).all()}
            delivery_landed_ids = {r.chunk_id for r in conn.execute(select(s.delivery_landed.c.chunk_id)).all()}

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

    @staticmethod
    def _exists(conn, table, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
        return conn.execute(select(table.c.chunk_id).where(table.c.chunk_id == chunk_id).limit(1)).first() is not None


def _conforms_facts(x: ChunkFactsStore) -> IReadChunkFactsRepository:
    return x
