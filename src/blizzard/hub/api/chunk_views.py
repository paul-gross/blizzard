"""Rendering one chunk as the rows and aggregates the board reads."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, TypedDict

from fastapi import status
from fastapi.responses import JSONResponse

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.ids import Id
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.decisions import to_decision_view
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.api.questions import question_view
from blizzard.hub.composition import TenantServices
from blizzard.hub.delivery.hub_node import PollPolicy
from blizzard.hub.domain.artifact.model import GitCommitArtifact, StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.dependencies import BlockedMarking
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    ChunkVerb,
    PauseFact,
    UsageTotal,
    WorkRef,
    holds_claim,
    verb_legal_from,
)
from blizzard.hub.domain.config.repositories import CommitOrigin, resolve_origin_record
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.operations.complete import is_completion_replay
from blizzard.hub.domain.operations.edit import is_graph_editable
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.forge.web import branch_url as forge_branch_url
from blizzard.hub.work_sources.source import IWorkSource
from blizzard.wire.chunk import (
    ArtifactView,
    BlockedView,
    BounceView,
    ChunkDetail,
    ChunkEscalationView,
    ChunkIngestConflict,
    ChunkNeighborhoodView,
    ChunkSummary,
    ChunkUsageTotalView,
    ChunkUsageView,
    IntendedMigrationView,
    LandedRepoView,
    MigrationView,
    PauseView,
    PendingView,
    PrView,
    RestartView,
    RouteView,
    TransitionView,
    WorkRefView,
)


class _RouteNotInjected(Enum):
    """The type of :data:`_ROUTE_NOT_INJECTED` — a single-member enum, not a plain class, so
    ``is``/``is not`` narrows the ``Route | None | _RouteNotInjected`` union for pyright."""

    TOKEN = 0


#: :meth:`ChunkView.of`'s default, distinct from an *injected* ``None``.
_ROUTE_NOT_INJECTED: Final = _RouteNotInjected.TOKEN


def pause_view(pause: PauseFact | None) -> PauseView | None:
    """The one place a :class:`PauseFact` becomes a :class:`PauseView` — every wire shape
    carrying pause state calls through here, so they stay in agreement by construction."""
    return PauseView(by=pause.set_by, set_at=iso_utc(pause.set_at)) if pause is not None else None


def usage_total_view(usage: UsageTotal) -> ChunkUsageTotalView:
    """The one place a :class:`UsageTotal` becomes a :class:`ChunkUsageTotalView` — every
    wire shape carrying totals calls through here, so they stay in agreement by construction."""
    return ChunkUsageTotalView(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_create_tokens=usage.cache_create_tokens,
        cost_usd=usage.cost_usd,
        cost_partial=usage.cost_partial,
        estimated_cost_usd=usage.estimated_cost_usd,
        billed_partial=usage.billed_partial,
    )


class _LiveHoldersNotInjected(Enum):
    """The type of :data:`_LIVE_HOLDERS_NOT_INJECTED` — the ``_RouteNotInjected`` pattern,
    reused so a ``dict[WorkRef, str] | None`` union stays narrowable for pyright."""

    TOKEN = 0


#: :meth:`ChunkView.of`'s default: no live-holder map was given, so it resolves lazily.
_LIVE_HOLDERS_NOT_INJECTED: Final = _LiveHoldersNotInjected.TOKEN


def blocked_view(unmet_prerequisite_chunk_ids: Sequence[str] | None) -> BlockedView | None:
    """A derived marking's wire wrapping — the one home every caller of
    :func:`~blizzard.hub.domain.chunk.dependencies.derive_blocked_prerequisites` reaches through,
    listing routes and ``ChunkView`` alike. Takes the dependent's whole unmet set, in
    declared order, and maps :meth:`BlockedMarking.of`'s marking onto the wire."""
    marking = BlockedMarking.of(unmet_prerequisite_chunk_ids)
    if marking is None:
        return None
    return BlockedView(prerequisite_chunk_id=marking.prerequisite_chunk_id, unmet_count=marking.unmet_count)


class ProjectLabelFields(TypedDict):
    """The project label fields every chunk-describing view carries."""

    project_id: str | None
    project_slug: str | None
    project_name: str | None


def project_label_fields(project_id: str | None, project: Project | None) -> ProjectLabelFields:
    """A chunk's project labels: its ``project_id``, with the current slug and name of ``project``,
    the record that id resolves to — ``None`` for each label the hub has no record for."""
    return {
        "project_id": project_id,
        "project_slug": project.slug if project is not None else None,
        "project_name": project.name if project is not None else None,
    }


def ingest_conflict_response(services: TenantServices, exc: IngestConflict) -> JSONResponse:
    """The ``409`` an ingest answers when a live chunk already holds one of its pointers: the pointer by its source's
    key, with the source's current slug and the pointer's label beside it."""
    pointer = exc.pointer
    conflict = ChunkIngestConflict(
        existing_chunk_id=exc.existing_chunk_id,
        source=pointer.source,
        slug=services.work_sources.source_label(pointer.source),
        label=services.work_sources.label(pointer),
        ref=pointer.ref,
    )
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())


class DeliveryWireFields(TypedDict):
    open_prs: list[PrView]
    closed_prs: list[PrView]
    awaiting_external_merge: bool
    landed_repos: list[LandedRepoView]
    landed: bool


@dataclass(frozen=True)
class ChunkView:
    """One chunk read — the row, the facts every derived value comes from
    (``bzh:facts-not-status``), and the graph resolver the read shares.

    A whole-fleet read hands :meth:`of` one :class:`GraphNames` for every chunk, so the
    graphs a fleet shares are resolved once across the list rather than once per row."""

    services: TenantServices
    chunk: Chunk
    facts: ChunkFacts
    names: GraphNames
    route: Route | None | _RouteNotInjected = _ROUTE_NOT_INJECTED
    #: Every pointer's live holder — the caller's own already-resolved map.
    live_holders: dict[WorkRef, str] | _LiveHoldersNotInjected = _LIVE_HOLDERS_NOT_INJECTED
    #: The chunk's blocked marking — the caller's own already-derived value;
    #: neither constructor derives it itself, so a caller that has no use for it (every verb
    #: response but the list and detail reads) pays nothing for it.
    blocked: BlockedView | None = None
    #: The chunk's standing-edge neighborhood — the caller's own
    #: already-derived value, the same shape ``blocked`` takes.
    neighborhood: ChunkNeighborhoodView | None = None
    delivery: DeliveryRead | None = None
    #: Runner names by id — the caller's own batched read; ``None`` makes each render read the names it shows.
    runner_names: Mapping[str, str] | None = None
    #: Projects by id — the caller's own batched read; ``None`` makes each render read the chunk's own project.
    projects: Mapping[ProjectId, Project] | None = None
    #: Request-scoped source resolution; absent on the single-chunk lazy path.
    sources: Mapping[str, IWorkSource | None] | None = None

    @classmethod
    def of(
        cls,
        services: TenantServices,
        chunk: Chunk,
        names: GraphNames | None = None,
        blocked: BlockedView | None = None,
        facts: ChunkFacts | None = None,
        neighborhood: ChunkNeighborhoodView | None = None,
    ) -> ChunkView:
        """``facts`` lets a caller that already loaded the chunk's own facts for another
        reason hand it in rather than this reloading it a second time; omitted, this
        loads it as before."""
        return cls(
            services=services,
            chunk=chunk,
            facts=facts
            if facts is not None
            else (services.chunks.facts.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)),
            names=names or GraphNames(services.graphs),
            blocked=blocked,
            neighborhood=neighborhood,
        )

    @classmethod
    def injected(
        cls,
        services: TenantServices,
        chunk: Chunk,
        facts: ChunkFacts,
        route: Route | None,
        names: GraphNames,
        live_holders: dict[WorkRef, str],
        blocked: BlockedView | None = None,
        delivery: DeliveryRead | None = None,
        runner_names: Mapping[str, str] | None = None,
        projects: Mapping[ProjectId, Project] | None = None,
        sources: Mapping[str, IWorkSource | None] | None = None,
    ) -> ChunkView:
        """The bulk-read counterpart to :meth:`of`: a fan-out list read injects
        already-fetched facts, route, pointer live-holders, runner names and projects, skipping
        a per-chunk ``load_facts``/``route_of``/``live_holders``/``names_for``/project read.
        ``route=None`` means "no live route"; :meth:`of` leaves both uninjected."""
        return cls(
            services=services,
            chunk=chunk,
            facts=facts,
            names=names,
            route=route,
            live_holders=live_holders,
            blocked=blocked,
            delivery=delivery,
            runner_names=runner_names,
            projects=projects,
            sources=sources,
        )

    def _project_fields(self) -> ProjectLabelFields:
        """The chunk's project labels: from the injected projects, else one read of its own project."""
        project_id = self.chunk.project_id
        if project_id is None:
            return project_label_fields(None, None)
        if self.projects is not None:
            return project_label_fields(project_id, self.projects.get(project_id))
        return project_label_fields(project_id, self.services.projects.get(project_id))

    def _delivery(self) -> DeliveryRead:
        if self.delivery is not None:
            return self.delivery
        sources = self.services.chunks.artifacts.delivery_sources_for([self.chunk.chunk_id])
        return DeliveryRead.of(self.facts, sources.get(self.chunk.chunk_id, DeliverySources()))

    @staticmethod
    def _delivery_fields(delivery: DeliveryRead) -> DeliveryWireFields:
        return {
            "open_prs": [PrView(repo=p.repo, number=p.number, url=p.url) for p in delivery.open_prs],
            "closed_prs": [PrView(repo=p.repo, number=p.number, url=p.url) for p in delivery.closed_prs],
            "awaiting_external_merge": delivery.awaiting_external_merge,
            "landed_repos": [
                LandedRepoView(repo=r.repo, commit_hash=r.commit_hash, url=r.url) for r in delivery.landed_repos
            ],
            "landed": delivery.landed,
        }

    def _resolved_route(self) -> Route | None:
        """The chunk's route: the injected value if one was given, otherwise fetched lazily
        via :meth:`~blizzard.hub.domain.chunk.ports.route.IReadChunkRouteRepository.route_of`."""
        if self.route is not _ROUTE_NOT_INJECTED:
            return self.route
        return self.services.chunks.route.route_of(self.chunk.chunk_id)

    def _runner_names(self, runner_ids: Iterable[str | None]) -> Mapping[str, str]:
        """The registered names of the runners this render shows: the injected map, else one
        ``names_for`` read over just ``runner_ids`` (none at all when every id is ``None``)."""
        if self.runner_names is not None:
            return self.runner_names
        return self.services.registry.names_for(runner_id for runner_id in runner_ids if runner_id is not None)

    def _resolved_live_holders(self) -> dict[WorkRef, str]:
        if self.live_holders is not _LIVE_HOLDERS_NOT_INJECTED:
            return self.live_holders
        return self.services.chunks.work_refs.live_holders(self.chunk.work_refs)

    def summary(self) -> ChunkSummary:
        """The derived fleet-list row — rendered both by the list read and by
        every transition verb, from the same facts (``canon:one-owner``)."""
        node_id, node_name = self.current_node()
        status = self.facts.status()
        # A terminal chunk reads unrouted regardless of injection; on the
        # lazy path the guard also spares it the `route_of` query.
        if not holds_claim(status):
            route = None
        elif self.route is not _ROUTE_NOT_INJECTED:
            route = self.route
        else:
            route = self.services.chunks.route.route_of(self.chunk.chunk_id)
        runner_id = route.runner_id if route is not None else None
        completed_at = self.facts.completed_at()
        return ChunkSummary(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            status=status,
            terminal=status in TERMINAL_STATUSES,
            current_node_id=node_id,
            current_node_name=node_name,
            work_refs=self.pointer_views(),
            default_model=list(self.chunk.default_model),
            default_effort=self.chunk.default_effort,
            default_harnesses=list(self.chunk.default_harnesses),
            runner_id=runner_id,
            runner_name=self._runner_names([runner_id]).get(runner_id) if runner_id is not None else None,
            environment_count=len(route.environment_ids) if route is not None else 0,
            cost=self.usage_total(),
            completed_at=iso_utc(completed_at) if completed_at is not None else None,
            blocked=self.blocked,
            **self._delivery_fields(self._delivery()),
            **self._project_fields(),
        )

    def current_node(self) -> tuple[str | None, str | None]:
        """The chunk's current node as ``(id, name)`` — the newest transition's target, or the
        pinned graph's entry node before the first transition (a nicer board value than ``None``).
        The name rides along so the board is legible without reassembly."""
        node_id = self.facts.current_node_id() or self.names.entry_node_id(self.chunk.graph_id)
        return node_id, self.names.node_name(self.chunk.graph_id, node_id)

    def pointer_views(self) -> list[WorkRefView]:
        """Each pointer with its board-legible label and browser URL. The label renders under the source's current
        slug, and falls back to ``{slug}#{ref}`` when no adapter is built for the source; the URL is null then. Each
        resolves to its own binding by key, so a chunk's pointers need not all share one source. Liveness resolves
        via :meth:`_resolved_live_holders`'s one bulk call."""
        holders = self._resolved_live_holders()
        views: list[WorkRefView] = []
        for p in self.chunk.work_refs:
            source = (
                self.sources.get(p.source) if self.sources is not None else self.services.work_sources.get(p.source)
            )
            views.append(
                WorkRefView(
                    source=p.source,
                    ref=p.ref,
                    label=(source.label(p) if source is not None else None) or self.services.work_sources.label(p),
                    web_url=source.web_url(p, live_holder=holders.get(p)) if source is not None else None,
                )
            )
        return views

    def usage_total(self) -> ChunkUsageTotalView:
        return usage_total_view(self.facts.usage_total())

    def detail(self) -> ChunkDetail:
        node_id, node_name = self.current_node()
        graph_created_at = self.names.created_at(self.chunk.graph_id)
        artifacts = self.services.chunks.artifacts.load_artifacts(self.chunk.chunk_id)
        history = ChunkHistoryView(self.facts, self.names)
        status = self.facts.status()
        route = self._resolved_route()
        decision = self.services.chunks.decisions.decision_for_chunk(self.chunk.chunk_id)
        questions = self.services.chunks.questions.load_questions(self.chunk.chunk_id)
        # One read names every runner the detail shows: the route's, the gate's, each asker's.
        runner_names = self._runner_names(
            [
                route.runner_id if route is not None else None,
                decision.imposed_by_runner_id if decision is not None else None,
                *(question.runner_id for question in questions),
            ]
        )
        return ChunkDetail(
            chunk_id=self.chunk.chunk_id,
            graph_id=self.chunk.graph_id,
            graph_name=self.names.graph_name(self.chunk.graph_id),
            graph_created_at=iso_utc(graph_created_at) if graph_created_at is not None else None,
            status=status,
            current_node_id=node_id,
            current_node_name=node_name,
            pausable=verb_legal_from(ChunkVerb.PAUSE, status),
            status_if_paused=self.facts.status_if_paused(),
            completable=not is_completion_replay(self.facts),
            deletable=verb_legal_from(ChunkVerb.DELETE, status),
            graph_editable=is_graph_editable(self.facts),
            terminal=status in TERMINAL_STATUSES,
            current_node_terminal=node_id == RESERVED_TERMINAL,
            latest_epoch=self.facts.latest_epoch(),
            work_refs=self.pointer_views(),
            default_model=list(self.chunk.default_model),
            default_effort=self.chunk.default_effort,
            default_harnesses=list(self.chunk.default_harnesses),
            intended_migration=self.intended_migration(),
            route=self._route(route, runner_names),
            escalation=self._escalation(),
            pause=self._pause(),
            blocked=self.blocked,
            neighborhood=self.neighborhood
            if self.neighborhood is not None
            else ChunkNeighborhoodView(prerequisites=[], dependents=[]),
            decision=to_decision_view(decision, runner_names) if decision is not None else None,
            history=history.transitions(),
            migrations=history.migrations(),
            restarts=history.restarts(),
            artifacts=self._artifacts(artifacts),
            questions=[question_view(question, runner_names) for question in questions],
            **self._delivery_fields(self._delivery()),
            cost=self.usage_total(),
            usage=self._usage_history(),
            pending=self._pending(),
            bounces=self._bounces(),
            **self._project_fields(),
        )

    @staticmethod
    def _route(route: Route | None, runner_names: Mapping[str, str]) -> RouteView | None:
        if route is None:
            return None
        return RouteView(
            runner_id=route.runner_id,
            runner_name=runner_names.get(route.runner_id),
            workspace_id=route.workspace_id,
            environment_ids=route.environment_ids,
        )

    def _escalation(self) -> ChunkEscalationView | None:
        escalation = self.facts.open_escalation()
        if escalation is None:
            return None
        return ChunkEscalationView(
            epoch=escalation.epoch,
            takeover_command=escalation.takeover_command,
            wrapped_takeover_command=escalation.wrapped_takeover_command,
            cause=escalation.cause,
            detail=escalation.detail,
        )

    def _pause(self) -> PauseView | None:
        return pause_view(self.facts.open_pause())

    def _pending(self) -> PendingView | None:
        """Reifies a full :class:`Graph` because the poll policy it needs lives only on a
        :class:`Node`, not the :class:`GraphSummary`/name projection :attr:`names`
        otherwise resolves through."""
        pending = self.facts.hub_node_pending()
        if pending is None:
            return None
        graph = self.services.graphs.get(self.chunk.graph_id)
        node = graph.node_by_id(pending.node_id) if graph is not None else None
        if node is None:
            return None
        return PendingView(node_name=node.name, next_poll_at=iso_utc(pending.polled_at + PollPolicy.of(node).interval))

    def intended_migration(self) -> IntendedMigrationView | None:
        """The chunk's standing migration intent as a view, or ``None`` when no
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
        """The chunk's per-node-step usage facts, oldest first."""
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
                harness_id=u.harness_id,
                harness_version=u.harness_version,
                estimated_cost_usd=u.estimated_cost_usd,
                cost_partial=u.cost_partial(),
            )
            for u in sorted(self.facts.usage, key=lambda u: u.recorded_at)
        ]

    def _bounces(self) -> list[BounceView]:
        return [
            BounceView(cause=b.cause, envelope=b.envelope, recorded_at=iso_utc(b.recorded_at))
            for b in sorted(self.facts.bounces, key=lambda b: b.recorded_at)
        ]

    def _branch_links(self, rows: list[StoredArtifact]) -> Callable[[StoredArtifact, GitCommitArtifact], str | None]:
        """Each git-commit artifact's branch link: the forge ``tree`` address on the repository
        record its repository resolves to by delivery's rules — among the records the chunk's
        project links, standing at the chunk's mint time. ``None`` for a repository resolving
        to no one record (unknown, ambiguous, or not linked); a chunk with no project links
        none. One read of the links and one of the records serve every commit of the view,
        and none at all when the view carries no commit."""
        if not any(isinstance(row.artifact, GitCommitArtifact) for row in rows):
            return lambda _row, _commit: None
        project_id = self.chunk.project_id
        linked = (
            frozenset(link.repository_id for link in self.services.project_links.repositories_of(project_id))
            if project_id is not None
            else frozenset[str]()
        )
        records = self.services.repository_records.list_all(include_retired=True) if linked else []
        minted_at = self.chunk.minted_at

        def link(row: StoredArtifact, commit: GitCommitArtifact) -> str | None:
            origin = CommitOrigin.of(commit.forge or None, commit.repo or row.name)
            record = resolve_origin_record(origin, records, minted_at=minted_at, linked=linked)
            return forge_branch_url(record.fields, commit.branch_name) if record is not None else None

        return link

    def _artifacts(self, rows: list[StoredArtifact]) -> list[ArtifactView]:
        """The chunk's inline artifact store — every entry, with an asset's content and a
        git-commit's pinned reference surfaced; ordered by ``{node}.{name}.{epoch}``
        so a re-run's later-epoch entry follows its predecessors (append-only history)."""
        branch_link = self._branch_links(rows)
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
                views.append(
                    ArtifactView(
                        **common,
                        repo=artifact.repo,
                        branch_name=artifact.branch_name,
                        commit_hash=artifact.commit_hash,
                        branch_url=branch_link(row, artifact),
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

        Each edge's node ids resolve against *the graph the transition happened in*,
        keyed by ``TransitionFact.graph_id`` — not the chunk's current pin (pinned by
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
            for r in self.facts.restart_history()
        ]

    def migrations(self) -> list[MigrationView]:
        """The chunk's cross-graph migration steps oldest-first.

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
                source=m.source,
                epoch=m.epoch,
                recorded_at=iso_utc(m.recorded_at),
            )
            for m in sorted(self.facts.migrations, key=lambda m: (m.recorded_at, m.epoch))
        ]
