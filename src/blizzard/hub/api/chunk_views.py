"""Rendering one chunk as the rows and aggregates the board reads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from blizzard.foundation.ids import Id
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.decisions import to_decision_view
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.api.questions import question_view
from blizzard.hub.composition import HubServices
from blizzard.hub.delivery.hub_node import PollPolicy
from blizzard.hub.domain.artifacts import ArtifactRow, GitCommitArtifact
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import Chunk, ChunkFacts, holds_claim
from blizzard.hub.work_sources.source import IWorkSource
from blizzard.wire.chunk import (
    ArtifactView,
    BlockedView,
    BounceView,
    ChunkDetail,
    ChunkEscalationView,
    ChunkSummary,
    ChunkUsageTotalView,
    ChunkUsageView,
    IntendedMigrationView,
    MigrationView,
    PauseView,
    PendingView,
    PrView,
    RestartView,
    RouteView,
    TransitionView,
    WorkRefView,
)
from blizzard.wire.decision import DecisionView


class _RouteNotInjected(Enum):
    """The type of :data:`_ROUTE_NOT_INJECTED` — a single-member enum, not a plain class, so
    ``is``/``is not`` narrows the ``Route | None | _RouteNotInjected`` union for pyright."""

    TOKEN = 0


#: :meth:`ChunkView.of`'s default, distinct from an *injected* ``None`` (issue #421).
_ROUTE_NOT_INJECTED: Final = _RouteNotInjected.TOKEN


def blocked_view(prerequisite_chunk_id: str | None) -> BlockedView | None:
    """A derived marking's wire wrapping (issue #457) — the one home every caller of
    :func:`~blizzard.hub.domain.dependencies.derive_blocked_markings` reaches through,
    listing routes and ``ChunkView`` alike (review round 1 F6), rather than each
    re-declaring the same ``str | None -> BlockedView | None`` wrap."""
    return BlockedView(prerequisite_chunk_id=prerequisite_chunk_id) if prerequisite_chunk_id is not None else None


@dataclass(frozen=True)
class ChunkView:
    """One chunk read — the row, the facts every derived value comes from
    (``bzh:facts-not-status``), and the graph resolver the read shares.

    A whole-fleet read hands :meth:`of` one :class:`GraphNames` for every chunk, so the
    graphs a fleet shares are resolved once across the list rather than once per row."""

    services: HubServices
    chunk: Chunk
    facts: ChunkFacts
    names: GraphNames
    route: Route | None | _RouteNotInjected = _ROUTE_NOT_INJECTED
    #: The chunk's blocked marking (issue #457) — the caller's own already-derived value;
    #: neither constructor derives it itself, so a caller that has no use for it (every verb
    #: response but the list and detail reads) pays nothing for it.
    blocked: BlockedView | None = None

    @classmethod
    def of(
        cls,
        services: HubServices,
        chunk: Chunk,
        names: GraphNames | None = None,
        blocked: BlockedView | None = None,
        facts: ChunkFacts | None = None,
    ) -> ChunkView:
        """``facts`` lets a caller that already loaded the chunk's own facts for another
        reason (the detail route's blocked-marking gate, issue #457 F5) hand it in rather
        than this reloading it a second time; omitted, this loads it as before."""
        return cls(
            services=services,
            chunk=chunk,
            facts=facts
            if facts is not None
            else (services.chunks.facts.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)),
            names=names or GraphNames(services.graphs.get),
            blocked=blocked,
        )

    @classmethod
    def injected(
        cls,
        services: HubServices,
        chunk: Chunk,
        facts: ChunkFacts,
        route: Route | None,
        names: GraphNames,
        blocked: BlockedView | None = None,
    ) -> ChunkView:
        """The bulk-read counterpart to :meth:`of` (issue #421): a fan-out list read injects
        already-fetched facts and route instead of calling ``load_facts``/``route_of`` per
        chunk. ``route=None`` means "no live route"; :meth:`of` leaves it uninjected."""
        return cls(services=services, chunk=chunk, facts=facts, names=names, route=route, blocked=blocked)

    def _resolved_route(self) -> Route | None:
        """The chunk's route: the injected value if one was given, otherwise fetched lazily
        via :meth:`~blizzard.hub.domain.chunks.route.IReadChunkRouteRepository.route_of`."""
        if self.route is not _ROUTE_NOT_INJECTED:
            return self.route
        return self.services.chunks.route.route_of(self.chunk.chunk_id)

    def summary(self) -> ChunkSummary:
        """The derived fleet-list row (issue #104) — rendered both by the list read and by
        every transition verb, from the same facts (``canon:one-owner``)."""
        node_id, node_name = self.current_node()
        status = self.facts.status()
        # A terminal chunk reads unrouted regardless of injection (issue #140); on the
        # lazy path the guard also spares it the `route_of` query.
        if not holds_claim(status):
            route = None
        elif self.route is not _ROUTE_NOT_INJECTED:
            route = self.route
        else:
            route = self.services.chunks.route.route_of(self.chunk.chunk_id)
        completed_at = self.facts.completed_at()
        return ChunkSummary(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            status=status,
            current_node_id=node_id,
            current_node_name=node_name,
            work_refs=self.pointer_views(),
            default_model=list(self.chunk.default_model),
            default_effort=self.chunk.default_effort,
            runner_id=route.runner_id if route is not None else None,
            environment_count=len(route.environment_ids) if route is not None else 0,
            cost=self.usage_total(),
            completed_at=iso_utc(completed_at) if completed_at is not None else None,
            blocked=self.blocked,
        )

    def current_node(self) -> tuple[str | None, str | None]:
        """The chunk's current node as ``(id, name)`` — the newest transition's target, or the
        pinned graph's entry node before the first transition (a nicer board value than ``None``).
        The name rides along so the board is legible without reassembly."""
        graph = self.names.graph(self.chunk.graph_id)
        node_id = self.facts.current_node_id() or (graph.entry_node_id if graph is not None else None)
        return node_id, self.names.node_name(self.chunk.graph_id, node_id)

    def pointer_views(self) -> list[WorkRefView]:
        """Each pointer with its board-legible label and browser URL — both null when no
        configured source names ``pointer.source``.

        Each pointer resolves to its own binding by name, so a chunk's pointers need not
        all share one source."""
        views: list[WorkRefView] = []
        for p in self.chunk.work_refs:
            source = self.services.work_sources.get(p.source)
            views.append(
                WorkRefView(
                    source=p.source,
                    ref=p.ref,
                    label=source.label(p) if source is not None else None,
                    web_url=source.web_url(p) if source is not None else None,
                )
            )
        return views

    def usage_total(self) -> ChunkUsageTotalView:
        usage = self.facts.usage_total()
        return ChunkUsageTotalView(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_create_tokens=usage.cache_create_tokens,
            cost_usd=usage.cost_usd,
            cost_partial=usage.cost_partial,
        )

    def detail(self) -> ChunkDetail:
        node_id, node_name = self.current_node()
        graph = self.names.graph(self.chunk.graph_id)
        artifacts = self.services.chunks.artifacts.load_artifacts(self.chunk.chunk_id)
        history = ChunkHistoryView(self.facts, self.names)
        return ChunkDetail(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            graph_name=self.names.graph_name(self.chunk.graph_id),
            graph_created_at=iso_utc(graph.created_at) if graph is not None else None,
            status=self.facts.status(),
            current_node_id=node_id,
            current_node_name=node_name,
            latest_epoch=self.facts.latest_epoch(),
            work_refs=self.pointer_views(),
            default_model=list(self.chunk.default_model),
            default_effort=self.chunk.default_effort,
            intended_migration=self.intended_migration(),
            route=self._route(),
            escalation=self._escalation(),
            pause=self._pause(),
            blocked=self.blocked,
            decision=self._decision(),
            history=history.transitions(),
            migrations=history.migrations(),
            restarts=history.restarts(),
            artifacts=self._artifacts(artifacts),
            questions=[question_view(q) for q in self.services.chunks.questions.load_questions(self.chunk.chunk_id)],
            awaiting_external_merge=self.facts.awaiting_external_merge(),
            open_prs=[PrView(repo=pr.repo, number=pr.number, url=pr.url) for pr in self.facts.pr_opened],
            cost=self.usage_total(),
            usage=self._usage_history(),
            pending=self._pending(),
            landed=self.facts.has_landed_repos(artifacts),
            bounces=self._bounces(),
        )

    def _route(self) -> RouteView | None:
        route = self._resolved_route()
        if route is None:
            return None
        return RouteView(
            runner_id=route.runner_id, workspace_id=route.workspace_id, environment_ids=route.environment_ids
        )

    def _escalation(self) -> ChunkEscalationView | None:
        escalation = self.facts.open_escalation()
        if escalation is None:
            return None
        return ChunkEscalationView(
            epoch=escalation.epoch,
            takeover_command=escalation.takeover_command,
            wrapped_takeover_command=escalation.wrapped_takeover_command,
        )

    def _pause(self) -> PauseView | None:
        pause = self.facts.open_pause()
        return PauseView(by=pause.set_by, set_at=iso_utc(pause.set_at)) if pause is not None else None

    def _decision(self) -> DecisionView | None:
        decision = self.services.chunks.decisions.decision_for_chunk(self.chunk.chunk_id)
        return to_decision_view(decision) if decision is not None else None

    def _pending(self) -> PendingView | None:
        pending = self.facts.hub_node_pending()
        if pending is None:
            return None
        graph = self.names.graph(self.chunk.graph_id)
        node = graph.node_by_id(pending.node_id) if graph is not None else None
        if node is None:
            return None
        return PendingView(node_name=node.name, next_poll_at=iso_utc(pending.polled_at + PollPolicy.of(node).interval))

    def intended_migration(self) -> IntendedMigrationView | None:
        """The chunk's standing migration intent as a view (issue #124), or ``None`` when no
        intent is set."""
        intent = self.chunk.intended_migration
        if intent is None:
            return None
        return IntendedMigrationView(
            mode=intent.mode,
            graph_id=intent.graph_id,
            graph_name=self.names.graph_name(intent.graph_id),
            node_name=intent.node_name,
        )

    def _usage_history(self) -> list[ChunkUsageView]:
        """The chunk's per-node-step usage facts, oldest first (issue #59)."""
        return [
            ChunkUsageView(
                node_id=u.node_id,
                epoch=u.epoch,
                kind=u.kind,
                model=u.model,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_tokens=u.cache_read_tokens,
                cache_create_tokens=u.cache_create_tokens,
                cost_usd=u.cost_usd,
            )
            for u in sorted(self.facts.usage, key=lambda u: u.recorded_at)
        ]

    def _bounces(self) -> list[BounceView]:
        return [
            BounceView(cause=b.cause, envelope=b.envelope, recorded_at=iso_utc(b.recorded_at))
            for b in sorted(self.facts.bounces, key=lambda b: b.recorded_at)
        ]

    def _branch_url_source(self) -> IWorkSource | None:
        """The binding a chunk's artifact branch links resolve through.

        One forge per chunk, *declared*: the chunk's first pointer whose ``source`` names a
        configured binding lends its ``branch_url``. ``None`` when no pointer's source is
        configured."""
        for p in self.chunk.work_refs:
            source = self.services.work_sources.get(p.source)
            if source is not None:
                return source
        return None

    def _artifacts(self, rows: list[ArtifactRow]) -> list[ArtifactView]:
        """The chunk's inline artifact store — every entry, with an asset's content and a
        git-commit's pinned reference surfaced; ordered by ``{node}.{name}.{epoch}``
        so a re-run's later-epoch entry follows its predecessors (append-only history)."""
        web_base = self._branch_url_source()
        views: list[ArtifactView] = []
        for row in sorted(rows, key=lambda r: (r.node_name, r.name, r.epoch)):
            artifact = row.artifact
            artifact_id = Id.parse(row.artifact_id)
            attached = artifact_id.minted_at if artifact_id is not None else None
            common = {
                "key": row.store_key,
                "kind": row.kind.value,
                "name": row.name,
                "node_id": row.node_id,
                "node_name": row.node_name,
                "epoch": row.epoch,
                "recorded_at": iso_utc(attached) if attached is not None else None,
            }
            if isinstance(artifact, GitCommitArtifact):
                branch_url = web_base.branch_url(artifact.repo, artifact.branch_name) if web_base is not None else None
                views.append(
                    ArtifactView(
                        **common,
                        repo=artifact.repo,
                        branch_name=artifact.branch_name,
                        commit_hash=artifact.commit_hash,
                        branch_url=branch_url,
                    )
                )
            else:
                views.append(ArtifactView(**common, content=artifact.content))
        return views


@dataclass(frozen=True)
class ChunkHistoryView:
    """A chunk's path through its graphs, rendered from facts and a resolver alone."""

    facts: ChunkFacts
    names: GraphNames

    def transitions(self) -> list[TransitionView]:
        """The chunk's transitions oldest-first.

        Each edge's node ids resolve against *the graph the transition happened in* (issue
        #90), keyed by ``TransitionFact.graph_id`` — not the chunk's current pin (pinned by
        ``tests/test_transition_graph_provenance.py``)."""
        return [
            TransitionView(
                from_node_id=t.from_node_id,
                from_node_name=self.names.node_name(t.graph_id, t.from_node_id),
                to_node_id=t.to_node_id,
                to_node_name=self.names.node_name(t.graph_id, t.to_node_id),
                choice_name=t.choice_name,
                epoch=t.epoch,
                recorded_at=iso_utc(t.recorded_at),
                graph_id=t.graph_id,
                graph_name=self.names.graph_name(t.graph_id),
            )
            for t in self.facts.transition_history()
        ]

    def restarts(self) -> list[RestartView]:
        """The chunk's operator restarts oldest-first (#370, #371).

        Each end resolves against its own graph the way :meth:`migrations` does, so a move that
        crossed one degrades neither side to a raw id; unset, ``from_graph`` is ``graph_id``."""
        return [
            RestartView(
                from_node_id=r.from_node_id,
                from_node_name=self.names.node_name(r.from_graph_id or r.graph_id, r.from_node_id),
                from_graph_id=r.from_graph_id,
                from_graph_name=self.names.graph_name(r.from_graph_id),
                to_node_id=r.to_node_id,
                to_node_name=self.names.node_name(r.graph_id, r.to_node_id),
                graph_id=r.graph_id,
                graph_name=self.names.graph_name(r.graph_id),
                epoch=r.epoch,
                restarted_by=r.restarted_by,
                decision_id=r.decision_id,
                recorded_at=iso_utc(r.recorded_at),
            )
            for r in sorted(self.facts.restarts, key=lambda r: (r.recorded_at, r.epoch))
        ]

    def migrations(self) -> list[MigrationView]:
        """The chunk's cross-graph migration steps oldest-first (issue #90).

        Each step names the graph it left and the graph it re-pinned to: ``from_node``
        resolves against the ``from_graph``, ``landed_node`` against the ``to_graph`` — each
        side's own graph, so neither degrades to a raw id when the two differ."""
        return [
            MigrationView(
                from_node_id=m.from_node_id,
                from_node_name=self.names.node_name(m.from_graph_id, m.from_node_id),
                from_graph_id=m.from_graph_id,
                from_graph_name=self.names.graph_name(m.from_graph_id),
                to_graph_id=m.to_graph_id,
                to_graph_name=self.names.graph_name(m.to_graph_id),
                landed_node_id=m.landed_node_id,
                landed_node_name=self.names.node_name(m.to_graph_id, m.landed_node_id),
                choice_name=m.choice_name,
                model=m.model,
                source=m.source.value if m.source is not None else None,
                recorded_at=iso_utc(m.recorded_at),
            )
            for m in sorted(self.facts.migrations, key=lambda m: (m.recorded_at, m.epoch))
        ]
