"""A turn's task: build the visit, drive its action's pass on the :class:`ActionLoop`, write each effect it hands over,
and route its answer in the transaction that ends the turn.

Every store call runs through ``asyncio.to_thread``. The build is bounded on its own and the pass by its caller's
deadline; either ends the turn ``cut-off``, routing nothing. A turn whose end cannot be written is left to the driver's
sweep."""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from types import MappingProxyType
from typing import Protocol

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import annotate
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer, NoopPlatformTracer
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_ids import DerivedContext, SpanRole, StepKey
from blizzard.hub.delivery.action_pass import ActionPassDriver, PassRun
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.delivery.actions.base import IHubAction
from blizzard.hub.delivery.flow_changes import publish_flow_facts
from blizzard.hub.delivery.hub_node import HUB_LOG_PREFIX, BouncedAction, HubNodeRouter
from blizzard.hub.domain.actions.effects import (
    ActionEscalation,
    Effect,
    EffectResult,
    EffectStatus,
    LandingRecord,
    Marker,
    Pass,
    RecordGardenDelivery,
    RecordReviewFindings,
)
from blizzard.hub.domain.actions.requirements import ACTION_REQUIREMENTS, Requirement
from blizzard.hub.domain.actions.visit import LandingProject, Visit, VisitChunk, VisitNode, VisitRefusal, resolve_visit
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, LandedRepos, decide_close_enqueue
from blizzard.hub.domain.chunk.no_code import no_code_repos
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.events import IEventLogPublisher
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import (
    IReadRepositoryRecordRepository,
    RepositoryId,
    RepositoryResolution,
    RepositorySecretUnavailable,
    ResolvedRepository,
)
from blizzard.hub.domain.delivery.flows import (
    STUCK_AFTER,
    TURN_CUT_OFF,
    TURN_SUPERSEDED,
    FlowLanding,
    PollPolicy,
    Submission,
    Turn,
    TurnEvent,
    held_resources,
    is_stuck,
    may_pass,
    resources_moved,
    stamp_terms,
    withdrawal_for,
)
from blizzard.hub.domain.delivery.ports.flows import IFlowChangePublisher, IWriteFlows
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsOutcome
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.domain.graph.model import HUB_DEFAULT_FAILURE_CHOICE, Graph, IReadManyGraphs, Node
from blizzard.hub.domain.observability.tracing.platform import ACTION_CHOICE, ACTION_NAME, ACTION_PASS_SPAN
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.forge.budget import ForgePriority
from blizzard.hub.forge.landing import RepositoryForge
from blizzard.hub.forge.registry import ForgeRegistry, TurnCutOff
from blizzard.hub.tenancy import TenantClosed, UnknownTenant
from blizzard.hub.work_sources.source import IWorkSourceRegistry

#: How long a turn's visit build may run, on the turn's clock, before the turn is cut off.
BUILD_BOUND_SECONDS = 60.0

# Fires after marker and intents are durable in one transaction, before drain.
_CP_CLOSE_AFTER_ENQUEUE_BEFORE_DRAIN = crashpoint(
    "close.after-enqueue.before-drain", "a landing marker and its close intents are durable; no drain has run yet"
)
_CP_FLOW_AFTER_POLL_BEFORE_TURN_END = crashpoint(
    "flow.after-poll.before-turn-end",
    "a pending pass's poll row is written but uncommitted; the turn's end shares its transaction, so both roll back",
)
_EVENT_ACTION_OVERRAN: EventLogKind = "hub-action-overran"
_EVENT_TURN_INTERRUPTED: EventLogKind = "turn-interrupted"
_FLOW_LANDED: EventLogKind = "flow-landed"
_PENDING = "pending"
# Why a fenced write answers `fenced`; a landing record, written regardless, answers the reason alone.
_PAST_FENCE = "the chunk is terminal or has moved past this visit"
_FENCED_DETAIL = f"not recorded: {_PAST_FENCE}"

_log = get_logger("blizzard.hub.flow_turn")


class TurnEndRefused(Exception):
    """A turn's end found the turn already ended, or the turn let go; the writes beside it roll back."""


class _TurnEnding:
    """The end of one turn, on whichever locked transaction routes its answer."""

    def __init__(self, flows: IWriteFlows, turn: Turn, submission: Submission, clock: IClock) -> None:
        self._flows = flows
        self._turn = turn
        self._submission = submission
        self._clock = clock
        self._abandoned = False
        #: The flow facts the newest end wrote. A routing that rolled back is retried or raises, so the facts a turn
        #: that returned normally holds here are the ones its committed end wrote.
        self.written: tuple[FlowFactWritten, ...] = ()

    def abandon(self) -> None:
        """Let the turn go: whatever would still end it writes nothing."""
        self._abandoned = True

    def end(self, handle: ILockedChunkRead, *, outcome: str, closure: str | None) -> None:
        self.end_with(handle, outcome=outcome, closure=closure, event=None)

    def end_with(self, handle: ILockedChunkRead, *, outcome: str, closure: str | None, event: TurnEvent | None) -> None:
        """End the turn on ``handle`` with ``event``; a ``None`` closure is what ``withdrawal_for`` answers under the
        lock (``bzh:store-exclusive-write``)."""
        if self._abandoned:
            raise TurnEndRefused(f"turn {self._turn.turn_id} was let go")
        if closure is None:
            closure = withdrawal_for(ChunkFacts.or_default(handle.facts(self._submission.chunk_id)), self._submission)
        written = self._flows.end_turn_locked(
            handle, self._turn.turn_id, outcome=outcome, closure=closure, event=event, at=self._clock.now()
        )
        if written is None:
            raise TurnEndRefused(f"turn {self._turn.turn_id} has already ended")
        self.written = written


@domain_model
@dataclass(frozen=True)
class _Visit:
    """Where a turn stands: the submission's chunk, its graph, and the node it is at."""

    chunk: Chunk
    graph: Graph
    node: Node
    epoch: int


@dataclass(frozen=True)
class _Ready:
    """A built visit, ready for its pass."""

    at: _Visit
    action: IHubAction
    visit: Visit
    forges: Mapping[RepositoryId, RepositoryForge]
    resolution: RepositoryResolution
    commits: list[dict[str, str]]


class ITurnPublisher(IEventLogPublisher, IFlowChangePublisher, Protocol):
    """What a turn announces once its writes commit: the event rows and the flow facts they wrote."""


class TurnRunner:
    """One tenant's turns: each :meth:`take` answers for one turn the driver started."""

    def __init__(
        self,
        *,
        flows: IWriteFlows,
        facts: IReadChunkFactsRepository,
        record: IReadChunkRecordRepository,
        graphs: IReadManyGraphs,
        artifacts: IWriteChunkArtifactsRepository,
        exclusive: IChunkExclusiveWrites,
        router: HubNodeRouter,
        events: EventLogService,
        publisher: ITurnPublisher,
        clock: IClock,
        repositories: IReadRepositoryRecordRepository,
        forge_records: IReadForgeRepository,
        links: IReadProjectLinks,
        projects: IReadProjectRepository,
        actions: HubActions,
        passes: ActionPassDriver,
        forges: ForgeRegistry,
        review_findings: ReviewFindingsRecorder,
        garden_delivery: GardenDeliveryRecorder,
        run_context: IReadRunContextRepository,
        requirements: Mapping[str, Requirement] = ACTION_REQUIREMENTS,
        public_url: str | None = None,
        work_sources: IWorkSourceRegistry,
        tracer: IPlatformTracer | None = None,
        build_bound_seconds: float = BUILD_BOUND_SECONDS,
    ) -> None:
        self._flows = flows
        self._facts = facts
        self._record = record
        self._graphs = graphs
        self._artifacts = artifacts
        self._exclusive = exclusive
        self._router = router
        self._events = events
        self._publisher = publisher
        self._clock = clock
        self._repositories = repositories
        self._forge_records = forge_records
        self._links = links
        self._projects = projects
        self._actions = actions
        self._passes = passes
        self._forges = forges
        self._review_findings = review_findings
        self._garden_delivery = garden_delivery
        self._run_context = run_context
        self._requirements = requirements
        self._public_url = public_url
        self._work_sources = work_sources
        self._tracer = tracer or NoopPlatformTracer()
        self._build_bound = build_bound_seconds

    async def take(self, turn: Turn, submission: Submission) -> None:
        """Answer for ``turn`` until it ends — or, cancelled at shutdown, let it go without writing its end. The
        ``turn-ended`` and ``withdrawn`` facts its end wrote are announced once that end's transaction committed."""
        ending = _TurnEnding(self._flows, turn, submission, self._clock)
        cut_off = TurnCutOff()
        try:
            await self._take(turn, submission, ending, cut_off)
            publish_flow_facts(self._publisher, ending.written)
        except asyncio.CancelledError:
            ending.abandon()
            cut_off.set()
            raise
        except TurnEndRefused as refused:
            _log.info("a turn's end was not written", turn_id=turn.turn_id, reason=str(refused))
        except (TenantClosed, UnknownTenant):
            _log.info("a turn's tenant closed; its turn is left to the teardown", turn_id=turn.turn_id)

    async def _take(self, turn: Turn, submission: Submission, ending: _TurnEnding, cut_off: TurnCutOff) -> None:
        building = asyncio.ensure_future(asyncio.to_thread(self._build_isolated, submission, ending, cut_off))
        building.add_done_callback(_settled)
        try:
            ready = await asyncio.wait_for(asyncio.shield(building), timeout=self._build_bound)
        except TimeoutError:
            ending.abandon()
            cut_off.set()
            await asyncio.to_thread(self._end_build_cut_off, turn, submission)
            return
        if ready is None:
            return
        run = await self._pass(turn, submission, ready, cut_off)
        if run.cut_off:
            await asyncio.to_thread(self._end_cut_off, turn, submission, ready)
            return
        await asyncio.to_thread(self._finish, ready, submission, run, ending)

    # --- the build -------------------------------------------------------------------------------------------------

    def _build_isolated(self, submission: Submission, ending: _TurnEnding, cut_off: TurnCutOff) -> _Ready | None:
        """The build, with any exception it raises routed ``failure`` carrying its traceback."""
        try:
            return self._build(submission, ending, cut_off)
        except (TurnEndRefused, TenantClosed, UnknownTenant):
            raise
        except Exception:
            self._fail_build(submission, ending, traceback.format_exc())
            return None

    def _build(self, submission: Submission, ending: _TurnEnding, cut_off: TurnCutOff) -> _Ready | None:
        """The visit, or ``None`` once the turn has ended without a pass: the visit no longer takes one, three
        cut-offs in a row, a poll timed out, a refusal, resources moved under the submission, or a capability
        missing."""
        at = self._visit_of(submission)
        facts = self._facts.load_facts(submission.chunk_id)
        if at is None or facts is None or not may_pass(facts, submission):
            self._end(submission, ending, outcome=TURN_SUPERSEDED)
            return None
        chunk, graph, node, epoch = at.chunk, at.graph, at.node, at.epoch
        outcomes = self._flows.turn_outcomes(chunk.chunk_id, node_id=node.node_id, epoch=epoch)
        if is_stuck(outcomes):
            self._router.escalate(chunk, self._stuck(node), epoch=epoch, ending=ending)
            return None
        pending_since = facts.hub_node_pending_since(node_id=node.node_id, epoch=epoch)
        if pending_since is not None and self._clock.now() - pending_since >= PollPolicy.of(node).timeout:
            self._router.route_pending_timeout(chunk, graph, node, epoch=epoch, ending=ending)
            return None
        action = self._actions.action(node.action) if node.action is not None else None
        return self._resolve(at, action, submission, ending, cut_off)

    def _resolve(
        self, at: _Visit, action: IHubAction | None, submission: Submission, ending: _TurnEnding, cut_off: TurnCutOff
    ) -> _Ready | None:
        chunk, graph, node, epoch = at.chunk, at.graph, at.node, at.epoch
        artifacts = self._artifacts.load_artifacts(chunk.chunk_id)
        project = LandingProject.read(chunk.project_id, projects=self._projects, links=self._links)
        resolved = resolve_visit(
            artifacts,
            action=node.action,
            spec=action.spec if action is not None else None,
            project=project,
            minted_at=chunk.minted_at,
            repositories=self._repositories.list_all(include_retired=True),
            forges=self._forge_records.list_all(include_retired=True),
        )
        if isinstance(resolved, VisitRefusal):
            self._router.refuse(
                chunk,
                graph,
                node,
                epoch=epoch,
                step=resolved.step,
                detail=resolved.detail,
                resolution=resolved.resolution,
                ending=ending,
            )
            return None
        assert action is not None  # resolved, so its spec was found
        spec, git_commits = resolved.spec, resolved.git_commits
        resolution = stamp_terms(resolved.resolution, self._flows.terms_of(submission.submission_id), project.positions)
        finished = LandedRepos.of(artifacts).names | no_code_repos(
            artifacts, git_commits.payload(resolution.qualified, resolution)
        )
        held = held_resources(resolution, finished, spec)
        if resources_moved(held, held_resources(resolution, frozenset(), spec), [f.resource for f in submission.flows]):
            self._end(submission, ending, outcome=TURN_SUPERSEDED, closure=TURN_SUPERSEDED)
            return None
        visit = Visit(
            chunk=VisitChunk(
                chunk_id=chunk.chunk_id,
                project_id=chunk.project_id,
                minted_at=chunk.minted_at,
                work_refs=tuple(chunk.work_refs),
            ),
            node=VisitNode(
                node_id=node.node_id,
                name=node.name,
                action=spec.name,
                action_args=MappingProxyType(dict(node.action_args)),
                expects_git_commits=graph.declares_git_commit,
            ),
            epoch=epoch,
            artifacts=tuple(artifacts),
            markers=tuple(row for row in artifacts if row.node_id == node.node_id and row.kind is ArtifactKind.ASSET),
            git_commits=git_commits,
            resolution=resolution,
            work_items=tuple(MappingProxyType(item) for item in self._resolve_work_items(chunk)),
            feature_title=self._resolve_feature_title(chunk),
            public_url=self._public_url,
            project=project,
            first_turn_at=self._first_turn_at(submission),
            now=self._clock.now(),
        )
        watch = visit.parked_watch() if spec.name == "land" else None
        if (missing := self._missing_capability(spec.name, resolution, only=watch)) is not None:
            target, capability = missing
            self._router.escalate_capability_missing(
                chunk,
                node,
                spec.name,
                target,
                capability,
                forge=resolution.forge_of(target),
                epoch=epoch,
                ending=ending,
            )
            return None
        forges: dict[RepositoryId, RepositoryForge] = {}
        if spec.lands:
            for target in resolution.targets:
                repository = RepositoryId(target.record)
                if watch is not None and repository != watch:
                    continue
                try:
                    forges[repository] = self._forges.repository_forge(
                        repository, cut_off=cut_off, priority=ForgePriority.LANDING
                    )
                except RepositorySecretUnavailable as exc:
                    detail = f"repository {target.slug}: {exc}"
                    step = "repository-secret-unavailable"
                    self._router.refuse(chunk, graph, node, epoch=epoch, step=step, detail=detail, ending=ending)
                    return None
        commits = [{k: v or "" for k, v in c.items()} for c in git_commits.payload(resolution.qualified, resolution)]
        return _Ready(at, action, visit, MappingProxyType(forges), resolution, commits)

    def _first_turn_at(self, submission: Submission) -> datetime:
        """When the visit's first turn started; the visit is built inside a turn, so one has."""
        started = self._flows.first_turn_at(submission.chunk_id, node_id=submission.node_id, epoch=submission.epoch)
        assert started is not None, "a visit is built inside one of its turns"
        return started

    def _visit_of(self, submission: Submission) -> _Visit | None:
        chunk = self._record.get(submission.chunk_id)
        graph = self._graphs.get_many([chunk.graph_id]).get(chunk.graph_id) if chunk is not None else None
        node = graph.node_by_id(submission.node_id) if graph is not None else None
        if chunk is None or graph is None or node is None:
            return None
        return _Visit(chunk, graph, node, submission.epoch)

    def _missing_capability(
        self, action_name: str, resolution: RepositoryResolution, *, only: RepositoryId | None = None
    ) -> tuple[ResolvedRepository, Capability] | None:
        """The first resolved repository whose forge lacks a capability ``action_name`` requires of it, and that
        capability; ``None`` when every forge offers what the action needs."""
        requirement = self._requirements.get(action_name)
        if requirement is None:
            return None
        for target in resolution.targets:
            if only is not None and target.record != only:
                continue
            offered = self._forges.offered(resolution.forge_of(target), RepositoryId(target.record))
            missing = sorted(requirement(target) - offered)
            if missing:
                return target, missing[0]
        return None

    def _stuck(self, node: Node) -> ActionEscalation:
        bound = self._passes.bound.deadline_seconds()
        return ActionEscalation(
            EscalationCause.HUB_ACTION_STUCK,
            f"hub action `{node.action}` at node `{node.name}` was cut off on each of its last {STUCK_AFTER} turns, "
            f"at its {bound:g}s pass deadline or its {self._build_bound:g}s build bound",
        )

    def _fail_build(self, submission: Submission, ending: _TurnEnding, log: str) -> None:
        """A build that raised routes ``failure``, its traceback the ``hub-log.<action>`` artifact."""
        at = self._visit_of(submission)
        if at is None:
            self._end(submission, ending, outcome=TURN_SUPERSEDED)
            return
        self._artifacts.record_hub_artifact(
            at.chunk.chunk_id,
            node_id=at.node.node_id,
            node_name=at.node.name,
            epoch=at.epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=f"{HUB_LOG_PREFIX}{at.node.action or 'build'}",
            content=log,
            at=self._clock.now(),
            close_decision=decide_close_enqueue(
                at.chunk.work_refs, lambda source: self._work_sources.closer(source) is not None
            ),
        )
        self._router.route(
            at.chunk, at.graph, at.node, epoch=at.epoch, choice=HUB_DEFAULT_FAILURE_CHOICE, ending=ending
        )

    # --- the pass --------------------------------------------------------------------------------------------------

    async def _pass(self, turn: Turn, submission: Submission, ready: _Ready, cut_off: TurnCutOff) -> PassRun:
        chunk, node, epoch = ready.at.chunk, ready.at.node, ready.at.epoch
        hub_exec = DerivedContext.of(StepKey.attempt(chunk.chunk_id, epoch + 1), SpanRole.HUB_EXEC, turn.turn_id)
        write = partial(self._write_effect, chunk, node, epoch, submission)
        with self._tracer.under(hub_exec), self._tracer.child(ACTION_PASS_SPAN, {ACTION_NAME: ready.action.spec.name}):
            run = await self._passes.drive(ready.action, ready.visit, ready.forges, write, cut_off=cut_off.set)
            annotate({ACTION_CHOICE: _choice_of(run, ready.action.spec, node) or ""})
        return run

    def _write_effect(
        self, chunk: Chunk, node: Node, epoch: int, submission: Submission, effect: Effect
    ) -> EffectResult:
        """Make one effect a pass recorded durable, answering how the hub took it."""
        match effect:
            case Marker(name=name, content=content):
                wrote = self._artifacts.record_hub_artifact(
                    chunk.chunk_id,
                    node_id=node.node_id,
                    node_name=node.name,
                    epoch=epoch,
                    admission=EpochAdmission.AT_OR_ABOVE,
                    name=name,
                    content=content,
                    at=self._clock.now(),
                    close_decision=decide_close_enqueue(
                        chunk.work_refs, lambda source: self._work_sources.closer(source) is not None
                    ),
                )
                if wrote or self._artifacts.has_hub_artifact(
                    chunk.chunk_id, node_id=node.node_id, epoch=epoch, name=name
                ):
                    return EffectResult(EffectStatus.RECORDED)
                return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
            case LandingRecord(repository=repository, repo=repo, revision=revision):
                refused = self._record_landing(chunk, node, epoch, submission, repository, repo, revision)
                _CP_CLOSE_AFTER_ENQUEUE_BEFORE_DRAIN.reached()
                if refused:
                    return EffectResult(EffectStatus.FENCED, _PAST_FENCE)
                return EffectResult(EffectStatus.RECORDED)
            case RecordReviewFindings():
                try:
                    findings = self._review_findings.record(chunk=chunk, node=node, epoch=epoch)
                except ReviewFindingsRejected as exc:
                    return EffectResult(EffectStatus.INVALID, str(exc))
                if findings is ReviewFindingsOutcome.FENCED:
                    return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
                return EffectResult(EffectStatus.RECORDED)
            case RecordGardenDelivery(delta=delta, proposals=proposals):
                run = self._run_context.for_chunk(chunk)
                if run is None:
                    return EffectResult(EffectStatus.INVALID, f"no run context for chunk {chunk.chunk_id}")
                try:
                    delivered = self._garden_delivery.record(
                        chunk=chunk, node=node, epoch=epoch, run=run, delta_names=delta, proposal_names=proposals
                    )
                except GardenDeliveryRejected as exc:
                    return EffectResult(EffectStatus.INVALID, str(exc), refused=exc.fault.value)
                if delivered is DeliveryOutcome.FENCED:
                    return EffectResult(EffectStatus.FENCED, _FENCED_DETAIL)
                return EffectResult(EffectStatus.RECORDED)
            case _:
                raise TypeError(f"no writer for the effect {effect!r}")

    def _record_landing(
        self,
        chunk: Chunk,
        node: Node,
        epoch: int,
        submission: Submission,
        repository: str,
        repo: str,
        revision: str,
    ) -> bool:
        """The merge on the flow of ``repository`` — its ``landed`` fact and ``flow-landed`` event with it, published
        once committed — or, for a repository the submission holds no flow of, on the chunk alone."""
        flow = next((f for f in submission.flows if f.repository_id == repository), None)
        at = self._clock.now()
        if flow is None:
            return self._artifacts.record_landing(
                chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
                admission=EpochAdmission.AT_OR_ABOVE,
                repo=repo,
                revision=revision,
                repository_id=repository,
                at=at,
                close_decision=decide_close_enqueue(
                    chunk.work_refs, lambda source: self._work_sources.closer(source) is not None
                ),
            )
        landing = FlowLanding.of(flow, submission, project_id=chunk.project_id, node_name=node.name, revision=revision)
        written = self._flows.record_landing(
            landing,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            repo=repo,
            revision=revision,
            at=at,
            close_decision=decide_close_enqueue(
                chunk.work_refs, lambda source: self._work_sources.closer(source) is not None
            ),
        )
        publish_flow_facts(self._publisher, written.written)
        if written.event_id is not None:
            self._publisher.publish_event_logged(
                severity=EVENT_LOG_SEVERITY[_FLOW_LANDED],
                kind=_FLOW_LANDED,
                chunk_id=chunk.chunk_id,
                runner_id=None,
                key=f"event_log:{written.event_id}",
                project_id=chunk.project_id,
            )
        return written.refused

    # --- the end ---------------------------------------------------------------------------------------------------

    def _finish(self, ready: _Ready, submission: Submission, run: PassRun, ending: _TurnEnding) -> None:
        """Route what the pass answered, ending the turn with it."""
        chunk, graph, node, epoch = ready.at.chunk, ready.at.graph, ready.at.node, ready.at.epoch
        spec = ready.action.spec
        answer = self._answer(chunk, node, spec, run, epoch=epoch)
        log_artifact = self._record_pass_log(chunk, node, spec.name, answer, epoch=epoch)
        if answer.escalation is not None:
            self._router.escalate(chunk, answer.escalation, epoch=epoch, ending=ending)
        elif answer.waits():
            self._router.record_wait(
                chunk,
                node,
                epoch=epoch,
                kind="awaiting-merge" if answer.choice == "awaiting-merge" else "pending",
                next_poll_at=answer.next_poll_at,
                ending=ending,
                reached=_CP_FLOW_AFTER_POLL_BEFORE_TURN_END.reached,
            )
        else:
            choice = answer.choice or HUB_DEFAULT_FAILURE_CHOICE
            bounced = BouncedAction(name=spec.name, choice=choice, log_artifact=log_artifact)
            self._router.route(
                chunk, graph, node, epoch=epoch, choice=choice, commits=ready.commits, action=bounced, ending=ending
            )

    def _end_cut_off(self, turn: Turn, submission: Submission, ready: _Ready) -> None:
        """A pass let go at its deadline ends its turn ``cut-off`` with its ``hub-action-overran`` event, routing
        nothing: the submission keeps its place."""
        node, spec = ready.at.node, ready.action.spec
        bound = self._passes.bound.deadline_seconds()
        event = TurnEvent(
            kind=_EVENT_ACTION_OVERRAN,
            chunk_id=submission.chunk_id,
            node_name=node.name,
            message=f"hub action `{spec.name}` was cut off at its {bound:g}s deadline at node `{node.name}`",
            detail={"action": spec.name, "epoch": submission.epoch, "bound_seconds": bound, "turn_id": turn.turn_id},
        )
        self._end_unrouted(turn, submission, event)

    def _end_build_cut_off(self, turn: Turn, submission: Submission) -> None:
        event = TurnEvent(
            kind=_EVENT_TURN_INTERRUPTED,
            chunk_id=submission.chunk_id,
            node_name=None,
            message=f"turn {turn.turn_id} was cut off: building its visit ran past {self._build_bound:g}s",
            detail={"turn_id": turn.turn_id, "submission_id": submission.submission_id, "bound": self._build_bound},
        )
        self._end_unrouted(turn, submission, event)

    def _end_unrouted(self, turn: Turn, submission: Submission, event: TurnEvent) -> None:
        ending = _TurnEnding(self._flows, turn, submission, self._clock)
        try:
            with self._exclusive.locked([submission.chunk_id]) as handle:
                ending.end_with(handle, outcome=TURN_CUT_OFF, closure=None, event=event)
        except TurnEndRefused:
            return
        publish_flow_facts(self._publisher, ending.written)

    def _end(self, submission: Submission, ending: _TurnEnding, *, outcome: str, closure: str | None = None) -> None:
        with self._exclusive.locked([submission.chunk_id]) as handle:
            ending.end(handle, outcome=outcome, closure=closure)

    def _answer(self, chunk: Chunk, node: Node, spec: ActionSpec, run: PassRun, *, epoch: int) -> Pass:
        """What a pass that ended as ``run`` answers: its own answer; an overrun waits where the action can and fails
        where it cannot; an escaping exception fails, its traceback the pass's log."""
        if run.answer is not None:
            return run.answer
        if run.traceback is not None:
            return Pass(choice=HUB_DEFAULT_FAILURE_CHOICE, log=(run.traceback,))
        self._events.record(
            kind=_EVENT_ACTION_OVERRAN,
            runner_id=None,
            chunk_id=chunk.chunk_id,
            project_id=chunk.project_id,
            lease_id=None,
            node_name=node.name,
            message=f"hub action `{spec.name}` overran its pass bound at node `{node.name}`",
            detail={"action": spec.name, "epoch": epoch},
            at=self._clock.now(),
        )
        if spec.can_wait_for(node.action_args):
            return Pass(choice=_PENDING)
        return Pass(choice=HUB_DEFAULT_FAILURE_CHOICE, log=(f"`{spec.name}` overran its pass bound",))

    def _record_pass_log(self, chunk: Chunk, node: Node, action_name: str, answer: Pass, *, epoch: int) -> str | None:
        """The pass's log lines as its ``hub-log.<action>`` artifact, when it has any; the artifact's name, or
        ``None`` when the pass logged nothing."""
        if not answer.log:
            return None
        name = f"{HUB_LOG_PREFIX}{action_name}"
        self._artifacts.record_hub_artifact(
            chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            name=name,
            content="\n".join(answer.log) + "\n",
            at=self._clock.now(),
            close_decision=decide_close_enqueue(
                chunk.work_refs, lambda source: self._work_sources.closer(source) is not None
            ),
        )
        return name

    def _resolve_feature_title(self, chunk: Chunk) -> str | None:
        """The chunk's prose feature title — the FIRST ``work_ref``'s work item title, best-effort. A read failure or
        a missing pointer or title degrades to ``None`` rather than breaking delivery."""
        pointer = chunk.originating_ref()
        if pointer is None:
            return None
        source = self._work_sources.get(pointer.source)
        if source is None:
            return None
        try:
            title = source.fetch(pointer).title
        except Exception:  # a forge read failing must never break delivery — degrade to no title
            return None
        return title or None

    def _resolve_work_items(self, chunk: Chunk) -> tuple[dict[str, str | None], ...]:
        """One ``{label, reference}`` per ``work_ref``, with no forge read; ``reference`` is ``None`` off-forge."""
        items: list[dict[str, str | None]] = []
        for pointer in chunk.work_refs:
            source = self._work_sources.get(pointer.source)
            label = self._work_sources.label(pointer)
            items.append(
                {
                    "label": label,
                    "reference": source.forge_reference(pointer) if source is not None else None,
                }
            )
        return tuple(items)


def _choice_of(run: PassRun, spec: ActionSpec, node: Node) -> str | None:
    """The choice ``run`` answers, without recording anything — what the pass's span names."""
    if run.answer is not None:
        return run.answer.choice
    if run.traceback is not None or not spec.can_wait_for(node.action_args):
        return HUB_DEFAULT_FAILURE_CHOICE
    return _PENDING


def _settled(done: asyncio.Future[_Ready | None]) -> None:
    """Retrieve a build's outcome, so one let go at its bound or at shutdown raises into nobody's log."""
    if not done.cancelled():
        done.exception()
