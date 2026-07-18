"""SQLAlchemy adapter for the chunk repository seam (package-private).

Implements :class:`~blizzard.hub.domain.work.IWriteChunkRepository` over the hub's
fact tables. All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``);
the domain sees only :class:`~blizzard.hub.domain.work.Chunk`,
:class:`~blizzard.hub.domain.work.ChunkFacts`, artifact rows, and routes.

Facts only (``bzh:facts-not-status``): every write appends a row that happened, and
status is **derived** by :func:`~blizzard.hub.domain.work.derive_chunk_status` over
:meth:`load_facts` — never read from a column. The transition-and-artifacts write is
one transaction (atomicity). Timestamps arrive already stamped from the
injected clock (``bzh:injected-clock``); the store never calls ``datetime.now``
except to source the ULID instant of a surrogate route id.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import Connection, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, HUB_EXEC_SLOT_PREFIX, mint
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.work import (
    AnswerOutcome,
    BounceFact,
    Chunk,
    ChunkFacts,
    ChunkStatus,
    DecisionChoice,
    DecisionFact,
    DecisionRow,
    EscalationFact,
    HubNodePollFact,
    IWriteChunkRepository,
    LeaseFact,
    PauseFact,
    PmPointer,
    PrOpenedFact,
    QuestionFact,
    QuestionRow,
    RequeueFact,
    RouteCreatedFact,
    RouteReleasedFact,
    RouteTokenMintedFact,
    TransitionFact,
    UsageFact,
    derive_chunk_status,
    newest_live_route,
)
from blizzard.hub.store import schema as s

_ROUTE_PREFIX = "route"
_TERMINAL = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})


class ChunkStore:
    """Read-write chunk-facts adapter over the hub store engine."""

    def __init__(self, engine: Engine, clock: IClock) -> None:
        self._engine = engine
        self._clock = clock

    # --- reads --------------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if row is None or chunk_id in self._grouped_ids(conn):
                return None  # a grouped-away chunk is ephemeral — gone from every read
            return self._chunk(conn, row)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        with self._engine.connect() as conn:
            chunk = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if chunk is None or chunk_id in self._grouped_ids(conn):
                return None
            transition_rows = conn.execute(select(s.transitions).where(s.transitions.c.chunk_id == chunk_id)).all()
            # Resolve each transition's executor against *its own* graph (issue #90):
            # after a cross-graph migration re-pins ``chunk.graph_id``, an old-graph
            # transition's ``to_node_id`` lives in a graph the chunk no longer points at,
            # so the executor map must span the set of graphs the chunk's transitions
            # touched, not only its current pin. Node ids are globally-unique ULIDs, so a
            # single node_id -> executor dict keyed across graphs resolves each transition
            # unambiguously (no silent ``RUNNER`` fallback for a known node).
            graph_ids = {chunk.graph_id} | {t.graph_id for t in transition_rows}
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
                EscalationFact(epoch=e.epoch, recorded_at=e.recorded_at, takeover_command=e.takeover_command or "")
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
            return ChunkFacts(
                minted=True,
                promoted=self._exists(conn, s.chunk_promoted, chunk_id),
                stopped=self._exists(conn, s.chunk_stopped, chunk_id),
                delivery_landed=self._exists(conn, s.delivery_landed, chunk_id),
                landed_repos=landed_repos,
                pr_closed=self._exists(conn, s.delivery_pr_closed, chunk_id),
                escalations=escalations,
                leases=leases,
                transitions=transitions,
                routes_created=routes_created,
                routes_released=routes_released,
                route_tokens_minted=route_tokens_minted,
                questions=questions,
                decisions=decisions,
                requeues=requeues,
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
                    artifact_id=a.artifact_id,
                    chunk_id=a.chunk_id,
                    node_id=a.node_id,
                    node_name=a.node_name,
                    epoch=a.epoch,
                )
                for a in conn.execute(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id)).all()
            ]

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route, or ``None`` if its newest release has caught up to it.

        Delegates the tie-break to :func:`~blizzard.hub.domain.work.newest_live_route`
        — the same function :func:`~blizzard.hub.domain.work._has_live_route` calls for
        chunk-status derivation — so route liveness has exactly one answer at a
        same-instant tie (issue #41) rather than two independently-maintained
        comparisons that can drift apart.
        """
        with self._engine.connect() as conn:
            # (created_at, seq) desc — must stay in lockstep with the key
            # newest_live_route orders by; that function, not this query, owns it.
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
            routes_released = (
                [RouteReleasedFact(released_at=released.released_at, seq=released.seq)] if released else []
            )
            routes_created = [RouteCreatedFact(created_at=created.created_at, seq=created.seq)]
            if newest_live_route(routes_created, routes_released) is None:
                return None
            env_ids = [
                e.environment_id
                for e in conn.execute(
                    select(s.route_environments.c.environment_id).where(
                        s.route_environments.c.route_id == created.route_id
                    )
                ).all()
            ]
            return Route(
                chunk_id=chunk_id,
                runner_id=created.runner_id,
                workspace_id=created.workspace_id,
                environment_ids=env_ids,
                created_at=created.created_at,
            )

    def list_all(self) -> list[Chunk]:
        with self._engine.connect() as conn:
            grouped = self._grouped_ids(conn)
            rows = conn.execute(select(s.chunks).order_by(s.chunks.c.minted_at.desc())).all()
            # A grouped-away chunk is ephemeral: removed from every listing.
            return [self._chunk(conn, row) for row in rows if row.chunk_id not in grouped]

    def list_ready(self) -> list[Chunk]:
        return [c for c in self.list_all() if self._status(c.chunk_id) is ChunkStatus.READY]

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

    def find_live_holder(self, pointer: PmPointer) -> str | None:
        with self._engine.connect() as conn:
            grouped = self._grouped_ids(conn)
            chunk_ids = [
                p.chunk_id
                for p in conn.execute(
                    select(s.chunk_pm_pointers.c.chunk_id).where(
                        (s.chunk_pm_pointers.c.source == pointer.source) & (s.chunk_pm_pointers.c.ref == pointer.ref)
                    )
                ).all()
            ]
        for chunk_id in chunk_ids:
            if chunk_id in grouped:
                continue  # the pointer moved to the survivor; the grouped chunk is gone
            if self._status(chunk_id) not in _TERMINAL:
                return chunk_id
        return None

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
            q = conn.execute(select(s.questions).where(s.questions.c.question_id == question_id)).one_or_none()
            if q is None:
                return None
            answer = conn.execute(
                select(s.question_answers).where(s.question_answers.c.question_id == question_id)
            ).one_or_none()
            return self._question_row(q, answer)

    def list_open_questions(self) -> list[QuestionRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.questions)
                .where(s.questions.c.question_id.not_in(select(s.question_answers.c.question_id)))
                .order_by(s.questions.c.asked_at)
            ).all()
            return [self._question_row(q, None) for q in rows]

    def load_questions(self, chunk_id: str) -> list[QuestionRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(s.questions).where(s.questions.c.chunk_id == chunk_id).order_by(s.questions.c.asked_at)
            ).all()
            out: list[QuestionRow] = []
            for q in rows:
                answer = conn.execute(
                    select(s.question_answers).where(s.question_answers.c.question_id == q.question_id)
                ).one_or_none()
                out.append(self._question_row(q, answer))
            return out

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

    def usage_since(self, since: datetime) -> list[UsageFact]:
        with self._engine.connect() as conn:
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
                for u in conn.execute(select(s.usage_facts).where(s.usage_facts.c.recorded_at >= since)).all()
            ]

    # --- writes -------------------------------------------------------------

    def mint(self, chunk: Chunk) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.chunks).values(
                    chunk_id=chunk.chunk_id,
                    graph_id=chunk.graph_id,
                    minted_at=chunk.minted_at,
                    model=chunk.model,
                )
            )
            for pointer in chunk.pm_pointers:
                conn.execute(
                    insert(s.chunk_pm_pointers).values(chunk_id=chunk.chunk_id, source=pointer.source, ref=pointer.ref)
                )

    def record_promote(self, chunk_id: str, *, at: datetime) -> None:
        # Idempotent by chunk_id: a chunk already promoted keeps its first row, so a
        # double promote (board click, CLI retry) is a harmless no-op.
        with self._engine.begin() as conn:
            if self._exists(conn, s.chunk_promoted, chunk_id):
                return
            conn.execute(insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=at))

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

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> None:
        """Record the route and mint its capability token's fact, one transaction (issue #84a).

        The token fact is a second row on the same shared per-chunk seq counter
        (:meth:`_next_route_seq`), allocated *after* the route's own seq is taken —
        its own call to the same allocator, not a fixed +1, so it stays correct if a
        future caller inserts anything else into this transaction between the two."""
        route_id = mint(_ROUTE_PREFIX, self._clock)
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

    def record_route_released(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=self._next_route_seq(conn, chunk_id)
                )
            )

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
                        produced_at=at,
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

        The hub lease, the ``delivery.landed`` fact, the terminal transition, and the
        route release are written in **one transaction**, so a ``kill -9`` mid-delivery
        can never leave a chunk landed-but-not-terminal (the ``merge-queue-single-state``
        invariant). Guarded by the ``delivery.landed`` existence check: a redelivery — a
        completion re-flushed after a mid-delivery hub crash — re-enters harmlessly and
        writes nothing a second time. Returns True when it wrote the terminal facts,
        False when the chunk was already landed.
        """
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
        escalation's existence at this epoch — a redelivery replay re-enters harmlessly
        and never double-escalates. No transition: the chunk's held route and stuck node
        are untouched. Returns True iff it wrote."""
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

    def record_escalation(self, chunk_id: str, *, epoch: int, takeover_command: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.escalations).values(
                    chunk_id=chunk_id, epoch=epoch, takeover_command=takeover_command, recorded_at=at
                )
            )

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
        # Append-only, no epoch fence, no second dedup key — see IWriteChunkRepository's
        # docstring: the caller's per-runner seq high-water mark already guarantees this
        # is called at most once per landed fact.
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
                        produced_at=at,
                    )
                )

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

    def record_requeue(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(s.requeues).values(chunk_id=chunk_id, requeued_at=at))

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append the moved chunk's new ready-queue position; order derives."""
        with self._engine.begin() as conn:
            conn.execute(insert(s.queue_positions).values(chunk_id=chunk_id, position=position, set_at=at))

    def add_pm_pointers(self, chunk_id: str, pointers: list[PmPointer], *, at: datetime) -> None:
        """Fold pointers into the survivor of a group, de-duped by (source, ref)."""
        with self._engine.begin() as conn:
            existing = {
                (p.source, p.ref)
                for p in conn.execute(
                    select(s.chunk_pm_pointers.c.source, s.chunk_pm_pointers.c.ref).where(
                        s.chunk_pm_pointers.c.chunk_id == chunk_id
                    )
                ).all()
            }
            for pointer in pointers:
                if (pointer.source, pointer.ref) in existing:
                    continue
                conn.execute(
                    insert(s.chunk_pm_pointers).values(chunk_id=chunk_id, source=pointer.source, ref=pointer.ref)
                )
                existing.add((pointer.source, pointer.ref))

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> None:
        """Record ``chunk.grouped`` — the merged-away chunk is ephemeral now."""
        with self._engine.begin() as conn:
            conn.execute(insert(s.chunk_grouped).values(chunk_id=chunk_id, grouped_into=grouped_into, grouped_at=at))

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> None:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46)."""
        with self._engine.begin() as conn:
            conn.execute(insert(s.chunk_pause_facts).values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by))

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready chunk to a different workflow graph (issue #27)."""
        with self._engine.begin() as conn:
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(graph_id=graph_id))

    def set_model(self, chunk_id: str, *, model: str) -> None:
        """Repin a not-ready chunk's model selection (issue #27)."""
        with self._engine.begin() as conn:
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(model=model))

    # --- The generic hub command node (#65) ---------------------------------

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution slot, **atomically** (crash-derivable
        fact, ``bzh:facts-not-status`` — never an in-process lock, so the invariant
        checker can assert at most one live slot and a ``kill -9`` mid-run leaves a
        stale, reclaimable row rather than a wedged fleet)."""
        with self._engine.begin() as conn:
            # Force sqlite's whole-database write lock BEFORE the read-then-insert
            # below — an identity update over the table (even zero rows) makes sqlite
            # acquire the RESERVED lock immediately, closing the race a bare SELECT
            # would leave open: two concurrent callers for two DIFFERENT chunks could
            # otherwise both read "no live rows" under sqlite's SHARED read lock before
            # either has inserted, and both mint a live slot (see
            # ``_next_route_seq``'s docstring, the same trick, same reason).
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
            slot_id = mint(HUB_EXEC_SLOT_PREFIX, self._clock)
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
                    artifact_id=mint(ARTIFACT_PREFIX, self._clock),
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name=node_name,
                    epoch=epoch,
                    name=name,
                    kind=ArtifactKind.ASSET.value,
                    data=content,
                    repo=None,
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
        """Record a generic hub command node's exit transition **atomically and
        idempotently** (#65) — the ``HubNodeExecutor`` counterpart to
        :meth:`finalize_delivery`, generalized to any authored target. Guarded by the
        transition's existence at ``(chunk_id, from_node_id, epoch)``: a redelivery
        replay re-enters harmlessly."""
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
    def _grouped_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
        return {r.chunk_id for r in conn.execute(select(s.chunk_grouped.c.chunk_id)).all()}

    @staticmethod
    def _next_route_seq(conn: Connection, chunk_id: str) -> int:
        """The next value of the per-chunk ``route_created``/``route_released``/
        ``route_token_minted`` seq counter (see ``work.newest_live_route`` and
        ``work.newest_live_route_token``) — one past the current max across all three
        tables for this chunk, so a create/release/token-mint triple is totally
        ordered by real write order even when their timestamps tie. ``route_token_minted``
        joined the counter in issue #84a (Phase 5): without it, a release recorded
        right after a token mint at the same instant could compute the same next value
        the mint just took (the released-max query alone can't see the token row), so
        the max must span all three tables, not just the original two.

        This is read-then-insert, not an atomic increment, so two concurrent callers
        for the same chunk must not both compute the same next value. A per-table
        ``UniqueConstraint`` on ``seq`` cannot close that: the counter is shared
        *across* ``route_created`` and ``route_released``, and a constraint scoped to
        one table cannot see a conflicting insert into the other.

        Instead this locks the chunk's own row in ``chunks`` — every route write for
        this chunk already holds a chunk row to lock, since a route can't exist
        without one — with a no-op ``UPDATE`` rather than ``SELECT ... FOR UPDATE``.
        ``FOR UPDATE`` is the more obvious primitive, but sqlite has no row-level
        locking and silently drops it, and a plain ``SELECT`` (locked or not) only
        takes sqlite's SHARED read lock, which does *not* block a second concurrent
        SHARED reader — so two callers can both read the same stale max before either
        has written, race and all, with no error from either side. An ``UPDATE``,
        even a no-op one, forces sqlite to acquire its whole-database write lock
        immediately rather than only when the eventual ``INSERT`` runs, closing that
        window; on postgres the same ``UPDATE`` takes the row-exclusive lock ``FOR
        UPDATE`` would have, so the second caller's lock acquisition blocks until the
        first's transaction commits its insert, then it re-reads the now-committed
        max. One portable statement serializes both dialects instead of two different
        primitives per dialect (``bzh:sql-portable``). Verified directly: racing this
        allocator from two threads against a real sqlite store never commits a
        duplicate seq (``tests/test_route_seq_concurrency.py``); postgres is checked
        by compiling the lock statement for the postgres dialect and asserting the
        expected row lock, since no live postgres server is available to this suite.

        Tradeoff: a route write now holds a write lock on ``chunks`` for the length of
        its transaction, so two route writes for the *same* chunk serialize instead of
        interleaving. Route writes are low-frequency and per-chunk, so this is judged
        cheap next to what it buys: the alternative considered (a per-table unique
        constraint) does not enforce the invariant at all, and a full restructure to
        one ``route_events`` table (kind discriminator + seq) is likely the sounder
        long-term shape but too large to fold into this fix — a candidate follow-up.
        """
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

    @staticmethod
    def _question_row(q, answer) -> QuestionRow:  # type: ignore[no-untyped-def]
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
            answered=answer is not None,
            answer=answer.answer if answer is not None else None,
            answered_by=answer.answered_by if answer is not None else None,
            answered_at=answer.answered_at if answer is not None else None,
        )

    def _chunk(self, conn, row) -> Chunk:  # type: ignore[no-untyped-def]
        pointers = [
            PmPointer(source=p.source, ref=p.ref)
            for p in conn.execute(
                select(s.chunk_pm_pointers).where(s.chunk_pm_pointers.c.chunk_id == row.chunk_id)
            ).all()
        ]
        return Chunk(
            chunk_id=row.chunk_id,
            graph_id=row.graph_id,
            pm_pointers=pointers,
            minted_at=row.minted_at,
            model=row.model,
        )

    def _status(self, chunk_id: str) -> ChunkStatus:
        facts = self.load_facts(chunk_id)
        return derive_chunk_status(facts) if facts is not None else ChunkStatus.READY

    @staticmethod
    def _resolved_ids(conn, decision_ids: list[str]) -> set[str]:  # type: ignore[no-untyped-def]
        if not decision_ids:
            return set()
        return {
            r.decision_id
            for r in conn.execute(
                select(s.decision_resolutions.c.decision_id).where(
                    s.decision_resolutions.c.decision_id.in_(decision_ids)
                )
            ).all()
        }

    def _decision_row(self, conn, row) -> DecisionRow:  # type: ignore[no-untyped-def]
        resolution = conn.execute(
            select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id == row.decision_id)
        ).one_or_none()
        transitioned = (
            conn.execute(
                select(s.transitions.c.transition_id).where(s.transitions.c.decision_id == row.decision_id).limit(1)
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
